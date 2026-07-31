"""面向 WebUI 的 KT Codex Device OAuth 适配器。

KT 原生 ``oauth_login()`` 会一直等待登录完成，并把 device code 打到服务端
终端；Web 管理台无法读取那段终端输出。本适配器沿用 KT 的端点、Token 类型
和交换逻辑，只把“申请 device code”与“后台轮询”拆开，供浏览器展示验证码。
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from kohakuterrarium.llm.codex_auth import (
    CLIENT_ID,
    DEVICE_REDIRECT_URI,
    DEVICE_TOKEN_URL,
    DEVICE_USERCODE_URL,
    DEVICE_VERIFY_URL,
    CodexTokens,
    _exchange_code,
)


_PENDING_ERRORS = {
    "authorization_pending",
    "pending",
    "deviceauth_authorization_unknown",
}


@dataclass(slots=True)
class CodexDeviceLogin:
    login_id: str
    device_auth_id: str
    user_code: str
    verification_url: str
    interval: int
    created_at: float
    expires_at: float
    status: str = "pending"
    error: str = ""
    token_expires_at: float = 0.0
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def public_view(self) -> dict[str, Any]:
        return {
            "login_id": self.login_id,
            "user_code": self.user_code,
            "verification_url": self.verification_url,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "token_expires_at": self.token_expires_at or None,
            "poll_after_seconds": max(2, self.interval),
        }


class CodexDeviceLoginManager:
    def __init__(self) -> None:
        self._sessions: dict[str, CodexDeviceLogin] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> dict[str, Any]:
        async with self._lock:
            self._remove_stale()
            for session in self._sessions.values():
                if session.status == "pending" and time.time() < session.expires_at:
                    return session.public_view()

            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    DEVICE_USERCODE_URL,
                    json={"client_id": CLIENT_ID},
                )
                response.raise_for_status()
                data = response.json()

            expires_at = _device_expiry(data)
            session = CodexDeviceLogin(
                login_id=secrets.token_urlsafe(18),
                device_auth_id=str(data["device_auth_id"]),
                user_code=str(data["user_code"]),
                verification_url=DEVICE_VERIFY_URL,
                interval=max(2, int(data.get("interval", 5))),
                created_at=time.time(),
                expires_at=expires_at,
            )
            self._sessions[session.login_id] = session
            session.task = asyncio.create_task(
                self._poll(session),
                name=f"codex-device-login:{session.login_id}",
            )
            return session.public_view()

    async def get(self, login_id: str) -> dict[str, Any] | None:
        async with self._lock:
            self._remove_stale()
            session = self._sessions.get(str(login_id or ""))
            return session.public_view() if session is not None else None

    def _remove_stale(self) -> None:
        cutoff = time.time() - 3600
        stale = [
            login_id
            for login_id, session in self._sessions.items()
            if session.expires_at < cutoff
        ]
        for login_id in stale:
            self._sessions.pop(login_id, None)

    async def _poll(self, session: CodexDeviceLogin) -> None:
        interval = session.interval
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                while time.time() < session.expires_at:
                    await asyncio.sleep(interval)
                    response = await client.post(
                        DEVICE_TOKEN_URL,
                        json={
                            "device_auth_id": session.device_auth_id,
                            "user_code": session.user_code,
                        },
                    )
                    if response.status_code == 200:
                        tokens = await _tokens_from_device_response(response.json())
                        session.status = "authenticated"
                        session.token_expires_at = tokens.expires_at
                        return

                    error_code, error_message = _device_error(response)
                    if error_code in _PENDING_ERRORS:
                        continue
                    if error_code == "slow_down":
                        interval += 5
                        session.interval = interval
                        continue
                    if error_code in {"expired_token", "access_denied"}:
                        session.status = (
                            "denied" if error_code == "access_denied" else "expired"
                        )
                        session.error = error_message or error_code
                        return
                    session.status = "failed"
                    session.error = error_message or (
                        f"Device OAuth 返回 HTTP {response.status_code}"
                    )
                    return
            session.status = "expired"
            session.error = "Device code 已过期，请重新发起登录"
        except asyncio.CancelledError:
            session.status = "cancelled"
            raise
        except Exception as exc:
            session.status = "failed"
            session.error = str(exc)[:300]


class KtProviderCredentialStatusAdapter:
    """把 KT OAuth Token 状态投影为框架无关凭据状态。"""

    def resolve(self, driver_type: str) -> tuple[bool, str]:
        if str(driver_type or "") != "codex":
            return False, "none"
        status = codex_status()
        configured = bool(status.get("authenticated"))
        return configured, "kt_oauth" if configured else "none"


def _device_expiry(data: dict[str, Any]) -> float:
    value = data.get("expires_at")
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc).timestamp()
        except (TypeError, ValueError):
            pass
    return time.time() + max(60, int(data.get("expires_in", 900)))


def _device_error(response: httpx.Response) -> tuple[str, str]:
    try:
        raw_error = response.json().get("error", "")
    except Exception:
        return "", response.text[:300]
    if isinstance(raw_error, dict):
        return (
            str(raw_error.get("code") or raw_error.get("type") or ""),
            str(raw_error.get("message") or raw_error)[:300],
        )
    return str(raw_error), str(raw_error)[:300]


async def _tokens_from_device_response(data: dict[str, Any]) -> CodexTokens:
    auth_code = str(data.get("authorization_code") or "")
    code_verifier = str(data.get("code_verifier") or "")
    if auth_code and code_verifier:
        return await _exchange_code(
            auth_code,
            code_verifier,
            DEVICE_REDIRECT_URI,
        )
    access_token = str(data.get("access_token") or "")
    if not access_token:
        raise RuntimeError("Device OAuth 成功响应缺少 access_token")
    tokens = CodexTokens(
        access_token=access_token,
        refresh_token=str(data.get("refresh_token") or ""),
        expires_at=time.time() + int(data.get("expires_in", 3600)),
        id_token=str(data.get("id_token") or ""),
        account_id=str(data.get("account_id") or ""),
    )
    tokens.save()
    return tokens


def codex_status() -> dict[str, Any]:
    tokens = CodexTokens.load()
    if tokens is None:
        return {
            "authenticated": False,
            "expired": None,
            "expires_at": None,
            "account_configured": False,
        }
    return {
        "authenticated": True,
        "expired": tokens.is_expired(),
        "expires_at": tokens.expires_at or None,
        "account_configured": bool(tokens.account_id),
    }


codex_device_login_manager = CodexDeviceLoginManager()


__all__ = [
    "CodexDeviceLoginManager",
    "KtProviderCredentialStatusAdapter",
    "codex_device_login_manager",
    "codex_status",
]

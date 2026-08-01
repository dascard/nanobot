"""Codex OAuth 多账号凭据存储与会话粘性轮询。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from threading import RLock
from typing import Any

import httpx
from cryptography.fernet import Fernet, InvalidToken
from kohakuterrarium.llm.codex_auth import (
    CLIENT_ID,
    TOKEN_URL,
    CodexTokens,
)
from kohakuterrarium.llm.codex_provider import CodexOAuthProvider

from core.db import system_setting_repository
from core.settings_admin_service import (
    SystemSettingCommandService,
    SystemSettingWrite,
)

logger = logging.getLogger("nanobot.kt.codex_accounts")

ACCOUNT_SETTING_PREFIX = "model.codex_accounts."
ACCOUNT_ID_PATTERN = re.compile(r"^ca_[A-Za-z0-9_-]{12,40}$")
_CREDENTIAL_DOMAIN = b"nanobot/codex-account-credentials/v1"
_MAX_STICKY_SESSIONS = 10_000


class CodexAccountError(ValueError):
    """Codex 账号配置无效或不可用。"""


class CodexCredentialConfigurationError(CodexAccountError):
    """Codex 凭据加密密钥未安全配置。"""


@dataclass(frozen=True, slots=True)
class CodexAccount:
    id: str
    name: str
    enabled: bool
    weight: int
    created_at: float
    updated_at: float
    encrypted_tokens: str = ""

    @property
    def setting_key(self) -> str:
        return f"{ACCOUNT_SETTING_PREFIX}{self.id}"

    def storage_value(self) -> str:
        return json.dumps(
            {
                "version": 1,
                "id": self.id,
                "name": self.name,
                "enabled": self.enabled,
                "weight": self.weight,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "credential": self.encrypted_tokens,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def _account_id(value: object) -> str:
    account_id = str(value or "").strip()
    if not ACCOUNT_ID_PATTERN.fullmatch(account_id):
        raise CodexAccountError("Codex 账号 ID 无效")
    return account_id


def _account_name(value: object, *, default: str = "") -> str:
    name = str(value or "").strip() or default
    if not name:
        raise CodexAccountError("Codex 账号名称不能为空")
    if len(name) > 100:
        raise CodexAccountError("Codex 账号名称不能超过 100 个字符")
    return name


def _account_weight(value: object) -> int:
    try:
        weight = int(value)
    except (TypeError, ValueError) as exc:
        raise CodexAccountError("Codex 账号权重必须是整数") from exc
    if not 1 <= weight <= 100:
        raise CodexAccountError("Codex 账号权重必须在 1 到 100 之间")
    return weight


def _parse_account(key: str, value: object) -> CodexAccount | None:
    if not str(key).startswith(ACCOUNT_SETTING_PREFIX):
        return None
    try:
        data = json.loads(str(value or "{}"))
        if not isinstance(data, dict):
            raise TypeError("账号记录不是 JSON 对象")
        key_id = str(key).removeprefix(ACCOUNT_SETTING_PREFIX)
        account_id = _account_id(data.get("id") or key_id)
        if account_id != key_id:
            raise ValueError("账号记录 ID 与设置 key 不一致")
        return CodexAccount(
            id=account_id,
            name=_account_name(data.get("name"), default=account_id),
            enabled=bool(data.get("enabled", True)),
            weight=_account_weight(data.get("weight", 1)),
            created_at=float(data.get("created_at") or 0),
            updated_at=float(data.get("updated_at") or 0),
            encrypted_tokens=str(data.get("credential") or ""),
        )
    except (CodexAccountError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("忽略损坏的 Codex 账号记录 key=%s error=%s", key, exc)
        return None


def _credential_secret() -> bytes:
    raw = str(os.environ.get("NANOBOT_CODEX_CREDENTIAL_SECRET") or "").strip()
    if not raw:
        raw = str(os.environ.get("NANOBOT_ASSET_TOKEN_SECRET") or "").strip()
    if not raw:
        from core.settings_service import settings

        raw = str(settings.get("sandbox.asset_token_secret") or "").strip()
    secret = raw.encode("utf-8")
    if len(secret) < 32:
        raise CodexCredentialConfigurationError(
            "请配置至少 32 字节的 NANOBOT_CODEX_CREDENTIAL_SECRET，"
            "或复用 NANOBOT_ASSET_TOKEN_SECRET"
        )
    return secret


def _fernet() -> Fernet:
    derived = hmac.new(
        _credential_secret(),
        _CREDENTIAL_DOMAIN,
        hashlib.sha256,
    ).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def ensure_codex_credential_encryption_ready() -> None:
    """在发起 OAuth 前确认成功响应能够安全落库。"""

    _fernet()


def _serialize_tokens(tokens: CodexTokens) -> bytes:
    return json.dumps(
        {
            "access_token": str(tokens.access_token or ""),
            "refresh_token": str(tokens.refresh_token or ""),
            "expires_at": float(tokens.expires_at or 0),
            "id_token": str(tokens.id_token or ""),
            "account_id": str(tokens.account_id or ""),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _encrypt_tokens(tokens: CodexTokens) -> str:
    if not str(tokens.access_token or ""):
        raise CodexAccountError("Codex OAuth Token 缺少 access_token")
    return _fernet().encrypt(_serialize_tokens(tokens)).decode("ascii")


def _decrypt_tokens(value: str) -> CodexTokens:
    if not value:
        raise CodexAccountError("Codex 账号尚未登录")
    try:
        data = json.loads(_fernet().decrypt(value.encode("ascii")).decode("utf-8"))
        tokens = CodexTokens(
            access_token=str(data.get("access_token") or ""),
            refresh_token=str(data.get("refresh_token") or ""),
            expires_at=float(data.get("expires_at") or 0),
            id_token=str(data.get("id_token") or ""),
            account_id=str(data.get("account_id") or ""),
        )
    except (
        InvalidToken,
        UnicodeError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise CodexAccountError("Codex 账号凭据无法解密") from exc
    if not tokens.access_token:
        raise CodexAccountError("Codex 账号凭据缺少 access_token")
    return tokens


def _open_session(db: Any | None) -> tuple[Any, bool]:
    if db is not None:
        return db, False
    from core.database import SessionLocal

    return SessionLocal(), True


def _list_accounts(db: Any) -> list[CodexAccount]:
    accounts: list[CodexAccount] = []
    for row in system_setting_repository(db).list_all():
        account = _parse_account(row.key, row.value)
        if account is not None:
            accounts.append(account)
    accounts.sort(key=lambda item: (item.created_at, item.id))
    return accounts


def list_codex_accounts(db: Any | None = None) -> list[CodexAccount]:
    session, owned = _open_session(db)
    try:
        return _list_accounts(session)
    finally:
        if owned:
            session.close()


def get_codex_account(
    account_id: str,
    db: Any | None = None,
) -> CodexAccount | None:
    normalized = _account_id(account_id)
    session, owned = _open_session(db)
    try:
        row = system_setting_repository(session).get(
            f"{ACCOUNT_SETTING_PREFIX}{normalized}"
        )
        return _parse_account(row.key, row.value) if row is not None else None
    finally:
        if owned:
            session.close()


def _write_account(account: CodexAccount, db: Any) -> CodexAccount:
    SystemSettingCommandService(system_setting_repository(db)).upsert_many(
        (
            SystemSettingWrite(
                key=account.setting_key,
                value=account.storage_value(),
                description="Codex OAuth 账号（凭据已加密）",
            ),
        )
    )
    return account


def create_codex_account(
    name: str = "",
    *,
    enabled: bool = True,
    weight: int = 1,
    db: Any | None = None,
) -> CodexAccount:
    ensure_codex_credential_encryption_ready()
    session, owned = _open_session(db)
    try:
        existing = _list_accounts(session)
        account_id = f"ca_{secrets.token_urlsafe(12)}"
        while any(item.id == account_id for item in existing):
            account_id = f"ca_{secrets.token_urlsafe(12)}"
        now = time.time()
        account = CodexAccount(
            id=account_id,
            name=_account_name(name, default=f"Codex 账号 {len(existing) + 1}"),
            enabled=bool(enabled),
            weight=_account_weight(weight),
            created_at=now,
            updated_at=now,
        )
        return _write_account(account, session)
    finally:
        if owned:
            session.close()


def update_codex_account(
    account_id: str,
    *,
    name: str | None = None,
    enabled: bool | None = None,
    weight: int | None = None,
    db: Any | None = None,
) -> CodexAccount:
    session, owned = _open_session(db)
    try:
        current = get_codex_account(account_id, session)
        if current is None:
            raise CodexAccountError("Codex 账号不存在")
        updated = replace(
            current,
            name=(current.name if name is None else _account_name(name)),
            enabled=(current.enabled if enabled is None else bool(enabled)),
            weight=(current.weight if weight is None else _account_weight(weight)),
            updated_at=time.time(),
        )
        return _write_account(updated, session)
    finally:
        if owned:
            session.close()


def delete_codex_account(account_id: str, db: Any | None = None) -> bool:
    normalized = _account_id(account_id)
    session, owned = _open_session(db)
    try:
        deleted = SystemSettingCommandService(
            system_setting_repository(session)
        ).delete_many((f"{ACCOUNT_SETTING_PREFIX}{normalized}",))
        codex_account_pool.remove_account(normalized)
        return bool(deleted)
    finally:
        if owned:
            session.close()


def save_codex_account_tokens(
    account_id: str,
    tokens: CodexTokens,
    db: Any | None = None,
) -> CodexAccount:
    session, owned = _open_session(db)
    try:
        current = get_codex_account(account_id, session)
        if current is None:
            raise CodexAccountError("Codex 账号不存在，无法保存 OAuth 凭据")
        updated = replace(
            current,
            encrypted_tokens=_encrypt_tokens(tokens),
            updated_at=time.time(),
        )
        return _write_account(updated, session)
    finally:
        if owned:
            session.close()


def load_codex_account_tokens(
    account_id: str,
    *,
    require_enabled: bool = True,
    db: Any | None = None,
) -> CodexTokens:
    account = get_codex_account(account_id, db)
    if account is None:
        raise CodexAccountError("Codex 账号不存在")
    if require_enabled and not account.enabled:
        raise CodexAccountError("Codex 账号已停用")
    return _decrypt_tokens(account.encrypted_tokens)


def codex_account_public_view(account: CodexAccount) -> dict[str, Any]:
    tokens: CodexTokens | None = None
    credential_error = False
    if account.encrypted_tokens:
        try:
            tokens = _decrypt_tokens(account.encrypted_tokens)
        except CodexAccountError:
            credential_error = True
    if not account.enabled:
        status = "disabled"
    elif credential_error:
        status = "unavailable"
    elif tokens is None:
        status = "login_required"
    elif tokens.is_expired() and not tokens.refresh_token:
        status = "expired"
    elif tokens.is_expired():
        status = "refresh_required"
    else:
        status = "ready"
    return {
        "id": account.id,
        "name": account.name,
        "enabled": account.enabled,
        "weight": account.weight,
        "status": status,
        "credential_configured": bool(account.encrypted_tokens),
        "expired": tokens.is_expired() if tokens is not None else None,
        "expires_at": (tokens.expires_at or None) if tokens is not None else None,
        "account_configured": bool(tokens.account_id) if tokens is not None else False,
        "created_at": account.created_at,
        "updated_at": account.updated_at,
    }


def list_codex_account_views(db: Any | None = None) -> list[dict[str, Any]]:
    return [codex_account_public_view(item) for item in list_codex_accounts(db)]


def _runtime_accounts(db: Any | None = None) -> list[CodexAccount]:
    available: list[CodexAccount] = []
    for account in list_codex_accounts(db):
        if not account.enabled or not account.encrypted_tokens:
            continue
        try:
            tokens = _decrypt_tokens(account.encrypted_tokens)
        except CodexAccountError:
            continue
        if tokens.is_expired() and not tokens.refresh_token:
            continue
        available.append(account)
    return available


class CodexAccountPool:
    """按权重选择新会话首账号，并在会话内保持粘性。"""

    def __init__(self, *, max_sticky_sessions: int = _MAX_STICKY_SESSIONS) -> None:
        self._max_sticky_sessions = max(1, int(max_sticky_sessions))
        self._sticky: OrderedDict[str, str] = OrderedDict()
        self._cursor = 0
        self._lock = RLock()

    def ordered_account_ids(
        self,
        session_id: str,
        *,
        db: Any | None = None,
    ) -> tuple[str, ...]:
        accounts = _runtime_accounts(db)
        if not accounts:
            return ()
        ids = [item.id for item in accounts]
        weights = {item.id: item.weight for item in accounts}
        session_key = str(session_id or "").strip()

        with self._lock:
            selected = self._sticky.get(session_key) if session_key else None
            if selected not in ids:
                selected = None
            if selected is None:
                weighted_ring = [
                    account_id for account_id in ids for _ in range(weights[account_id])
                ]
                selected = weighted_ring[self._cursor % len(weighted_ring)]
                self._cursor = (self._cursor + 1) % len(weighted_ring)
                if session_key:
                    self._remember(session_key, selected)
            elif session_key:
                self._sticky.move_to_end(session_key)

        start = ids.index(selected)
        return tuple(ids[start:] + ids[:start])

    def mark_success(self, session_id: str, account_id: str) -> None:
        session_key = str(session_id or "").strip()
        if not session_key:
            return
        normalized = _account_id(account_id)
        with self._lock:
            self._remember(session_key, normalized)

    def remove_account(self, account_id: str) -> None:
        normalized = _account_id(account_id)
        with self._lock:
            stale = [
                session_id
                for session_id, selected in self._sticky.items()
                if selected == normalized
            ]
            for session_id in stale:
                self._sticky.pop(session_id, None)

    def _remember(self, session_id: str, account_id: str) -> None:
        self._sticky[session_id] = account_id
        self._sticky.move_to_end(session_id)
        while len(self._sticky) > self._max_sticky_sessions:
            self._sticky.popitem(last=False)


codex_account_pool = CodexAccountPool()


def codex_account_health_key(model: str, account_id: str) -> str:
    normalized_model = str(model or "").strip()
    if not normalized_model:
        raise CodexAccountError("Codex 模型 ID 不能为空")
    return f"{normalized_model}@codex:{_account_id(account_id)}"


_refresh_locks: dict[str, asyncio.Lock] = {}


async def refresh_codex_account_tokens(account_id: str) -> CodexTokens:
    normalized = _account_id(account_id)
    lock = _refresh_locks.setdefault(normalized, asyncio.Lock())
    async with lock:
        tokens = load_codex_account_tokens(normalized)
        if not tokens.is_expired():
            return tokens
        if not tokens.refresh_token:
            raise CodexAccountError("Codex 账号缺少 refresh_token，请重新登录")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                TOKEN_URL,
                data={
                    "client_id": CLIENT_ID,
                    "grant_type": "refresh_token",
                    "refresh_token": tokens.refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            data = response.json()
        refreshed = CodexTokens(
            access_token=str(data.get("access_token") or ""),
            refresh_token=str(data.get("refresh_token") or tokens.refresh_token),
            expires_at=time.time() + int(data.get("expires_in", 3600)),
            id_token=str(data.get("id_token") or tokens.id_token),
            account_id=tokens.account_id,
        )
        save_codex_account_tokens(normalized, refreshed)
        return refreshed


class AccountBoundCodexOAuthProvider(CodexOAuthProvider):
    """只使用指定账号凭据、绝不在请求链自动打开 OAuth 的 KT Provider。"""

    def __init__(self, *args: Any, account_id: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.codex_account_id = _account_id(account_id)

    async def ensure_authenticated(self) -> None:
        tokens = load_codex_account_tokens(self.codex_account_id)
        if tokens.is_expired():
            tokens = await refresh_codex_account_tokens(self.codex_account_id)
        self._tokens = tokens
        self._rebuild_client()

    async def _ensure_valid_token(self) -> None:
        if self._tokens is None:
            await self.ensure_authenticated()
            return
        if self._tokens.is_expired():
            self._tokens = await refresh_codex_account_tokens(self.codex_account_id)
            self._rebuild_client()

    def with_model(self, name: str) -> AccountBoundCodexOAuthProvider:
        if not name or name == self.model:
            return self
        clone = AccountBoundCodexOAuthProvider(
            model=name,
            account_id=self.codex_account_id,
            reasoning_effort=self.reasoning_effort,
            service_tier=self.service_tier,
            timeout=self.timeout,
            max_retries=self.max_retries,
            retry_policy=self._retry_policy,
        )
        clone._tokens = self._tokens
        clone._client = self._client
        clone._retry_policy = self._retry_policy
        clone._emergency_drop_callbacks = list(self._emergency_drop_callbacks)
        clone.prompt_cache_key = self.prompt_cache_key
        clone._profile_max_context = getattr(self, "_profile_max_context", None)
        for attribute in (
            "provider_name",
            "provider_native_tools",
            "_nanobot_profile_id",
            "_nanobot_provider_id",
            "_nanobot_config_fingerprint",
        ):
            if hasattr(self, attribute):
                setattr(clone, attribute, getattr(self, attribute))
        return clone


def import_legacy_codex_tokens(
    tokens: CodexTokens,
    *,
    name: str = "Codex 账号 1",
    db: Any | None = None,
) -> CodexAccount | None:
    """一次性把旧单文件 Token 导入账号池；已有账号时不做任何写入。"""

    ensure_codex_credential_encryption_ready()
    session, owned = _open_session(db)
    try:
        if _list_accounts(session):
            return None
        account = create_codex_account(name, db=session)
        return save_codex_account_tokens(account.id, tokens, db=session)
    finally:
        if owned:
            session.close()


__all__ = [
    "ACCOUNT_SETTING_PREFIX",
    "AccountBoundCodexOAuthProvider",
    "CodexAccount",
    "CodexAccountError",
    "CodexAccountPool",
    "CodexCredentialConfigurationError",
    "codex_account_health_key",
    "codex_account_pool",
    "codex_account_public_view",
    "create_codex_account",
    "delete_codex_account",
    "ensure_codex_credential_encryption_ready",
    "get_codex_account",
    "import_legacy_codex_tokens",
    "list_codex_account_views",
    "list_codex_accounts",
    "load_codex_account_tokens",
    "refresh_codex_account_tokens",
    "save_codex_account_tokens",
    "update_codex_account",
]

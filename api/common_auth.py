"""普通 API 共享鉴权依赖。"""

from __future__ import annotations

from dataclasses import dataclass
import sys
from hmac import compare_digest

from fastapi import Header, HTTPException

from config import NANOBOT_API_TOKEN as CONFIG_API_TOKEN


@dataclass(frozen=True, slots=True)
class AuthenticatedApiPrincipal:
    """Bearer 凭据验证后产生的受信服务主体与固定授权范围。"""

    subject: str
    kind: str
    scopes: frozenset[str]

    def __post_init__(self) -> None:
        subject = str(self.subject or "").strip()
        kind = str(self.kind or "").strip().lower()
        scopes = frozenset(
            str(scope or "").strip()
            for scope in self.scopes
            if str(scope or "").strip()
        )
        if not subject or len(subject) > 128:
            raise ValueError("API principal subject 无效")
        if kind not in {"gateway", "service"}:
            raise ValueError("API principal kind 无效")
        if not scopes:
            raise ValueError("API principal scopes 不能为空")
        object.__setattr__(self, "subject", subject)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "scopes", scopes)

    def has_scope(self, scope: str) -> bool:
        return str(scope or "").strip() in self.scopes


_API_GATEWAY_PRINCIPAL = AuthenticatedApiPrincipal(
    subject="nanobot-api-gateway",
    kind="gateway",
    scopes=frozenset({"api:access", "session_goal:control"}),
)


def _current_api_token() -> str:
    routes = sys.modules.get("api.routes")
    if routes is not None and hasattr(routes, "NANOBOT_API_TOKEN"):
        return str(getattr(routes, "NANOBOT_API_TOKEN") or "")
    return str(CONFIG_API_TOKEN or "")


def verify_token(
    authorization: str = Header(default=""),
) -> AuthenticatedApiPrincipal:
    """验证普通 API Bearer token，并返回不可由请求体覆盖的主体。"""

    token_config = _current_api_token()
    if not token_config:
        raise HTTPException(status_code=503, detail="API token not configured")
    scheme, separator, value = str(authorization or "").partition(" ")
    token = value.strip() if separator and scheme.lower() == "bearer" else ""
    if not token or not compare_digest(token, token_config):
        raise HTTPException(status_code=401, detail="Invalid or missing API token")
    return _API_GATEWAY_PRINCIPAL


__all__ = ["AuthenticatedApiPrincipal", "verify_token"]

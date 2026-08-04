"""MCP 请求级秘密引用的加密存储与短生命周期解析。"""

from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import hmac
import os
from types import MappingProxyType

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from core.db.models.mcp import McpSecretRow
from core.mcp.contracts import (
    McpControlPlaneError,
    McpSecretReference,
    McpSecretUnavailable,
)


_MCP_CREDENTIAL_DOMAIN = b"nanobot/mcp-credentials/v1"


def _secret_id(value: object) -> str:
    return McpSecretReference("auth.bearer", str(value or "")).secret_id


def _credential_secret() -> bytes:
    raw = str(os.environ.get("NANOBOT_MCP_CREDENTIAL_SECRET") or "").strip()
    if not raw:
        raw = str(os.environ.get("NANOBOT_ASSET_TOKEN_SECRET") or "").strip()
    if not raw:
        from core.settings_service import settings

        raw = str(settings.get("sandbox.asset_token_secret") or "").strip()
    secret = raw.encode("utf-8")
    if len(secret) < 32:
        raise McpSecretUnavailable(
            "请配置至少 32 字节的 NANOBOT_MCP_CREDENTIAL_SECRET"
        )
    return secret


def _fernet() -> Fernet:
    derived = hmac.new(
        _credential_secret(),
        _MCP_CREDENTIAL_DOMAIN,
        hashlib.sha256,
    ).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def ensure_mcp_secret_encryption_ready() -> None:
    _fernet()


class McpSecretService:
    """秘密值只在调用栈局部以 mapping 返回，不进入配置快照。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def configured_ids(self) -> frozenset[str]:
        return frozenset(
            str(row[0])
            for row in self.db.query(McpSecretRow.secret_id).all()
        )

    def replace(self, secret_id: str, value: str) -> None:
        normalized_id = _secret_id(secret_id)
        raw = str(value or "")
        if not raw or len(raw.encode("utf-8")) > 64 * 1024 or "\x00" in raw:
            raise McpControlPlaneError("MCP secret 值为空、过大或包含 NUL")
        encrypted = _fernet().encrypt(raw.encode("utf-8")).decode("ascii")
        row = self.db.get(McpSecretRow, normalized_id)
        now = datetime.now()
        if row is None:
            self.db.add(McpSecretRow(
                secret_id=normalized_id,
                encrypted_value=encrypted,
                created_at=now,
                updated_at=now,
            ))
        else:
            row.encrypted_value = encrypted
            row.updated_at = now
        self.db.flush()

    def clear(self, secret_id: str) -> bool:
        normalized_id = _secret_id(secret_id)
        row = self.db.get(McpSecretRow, normalized_id)
        if row is None:
            return False
        self.db.delete(row)
        self.db.flush()
        return True

    def resolve(
        self,
        refs: tuple[McpSecretReference, ...],
    ) -> MappingProxyType[str, str]:
        if not refs:
            return MappingProxyType({})
        ids = {item.secret_id for item in refs}
        rows = self.db.query(McpSecretRow).filter(
            McpSecretRow.secret_id.in_(ids)
        ).all()
        by_id = {str(row.secret_id): row for row in rows}
        missing = ids - set(by_id)
        if missing:
            raise McpSecretUnavailable("MCP 请求所需秘密引用尚未配置")
        resolved: dict[str, str] = {}
        cipher = _fernet()
        try:
            for ref in refs:
                value = cipher.decrypt(
                    str(by_id[ref.secret_id].encrypted_value).encode("ascii")
                ).decode("utf-8")
                if not value:
                    raise McpSecretUnavailable("MCP 请求所需秘密为空")
                resolved[ref.binding] = value
        except (InvalidToken, UnicodeError, ValueError, TypeError) as exc:
            raise McpSecretUnavailable("MCP 请求所需秘密无法解密") from exc
        return MappingProxyType(resolved)


__all__ = [
    "McpSecretService",
    "ensure_mcp_secret_encryption_ready",
]

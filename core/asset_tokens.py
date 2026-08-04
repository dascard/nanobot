"""绑定资产、收件人与过期时间的短期 HMAC Transport Token。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

from core.sandbox.contracts import SandboxServiceError
from core.sandbox.paths import validate_sha256


class AssetTokenError(ValueError):
    """Token 无效、过期或收件人不匹配；对外统一视为无权访问。"""


@dataclass(frozen=True)
class AssetTokenClaims:
    asset_sha256: str
    recipient_type: str
    recipient_id: str
    expires_at: int
    artifact_id: str = ""


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not value or len(value) > 4096:
        raise AssetTokenError("资产 Token 无效")
    padding = "=" * (-len(value) % 4)
    try:
        return base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, TypeError) as exc:
        raise AssetTokenError("资产 Token 无效") from exc


def _recipient(recipient_type: Any, recipient_id: Any) -> tuple[str, str]:
    normalized_type = str(recipient_type or "").strip().lower()
    normalized_id = str(recipient_id or "").strip()
    if (
        normalized_type != "session"
        or not normalized_id
        or len(normalized_id) > 512
        or "\x00" in normalized_id
    ):
        raise AssetTokenError("资产 Token 收件人无效")
    return normalized_type, normalized_id


class AssetTokenSigner:
    def __init__(self, secret: str | bytes, *, default_ttl_seconds: int = 300) -> None:
        self.secret = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        if len(self.secret) < 32:
            raise AssetTokenError("资产 Token HMAC 密钥未安全配置")
        self.default_ttl_seconds = int(default_ttl_seconds)
        if not 60 <= self.default_ttl_seconds <= 86400:
            raise AssetTokenError("资产 Token 有效期配置无效")

    def issue(
        self,
        asset_sha256: str,
        *,
        recipient_type: str,
        recipient_id: str,
        ttl_seconds: int | None = None,
        now: int | None = None,
        artifact_id: str = "",
    ) -> str:
        try:
            sha256 = validate_sha256(asset_sha256)
        except SandboxServiceError as exc:
            raise AssetTokenError("资产 Token 资源无效") from exc
        recipient_type, recipient_id = _recipient(recipient_type, recipient_id)
        ttl = int(
            self.default_ttl_seconds
            if ttl_seconds is None
            else ttl_seconds
        )
        if not 60 <= ttl <= 86400:
            raise AssetTokenError("资产 Token 有效期配置无效")
        normalized_artifact_id = str(artifact_id or "").strip()
        if normalized_artifact_id and not (
            normalized_artifact_id.startswith("art_")
            and 8 <= len(normalized_artifact_id) <= 64
            and normalized_artifact_id.replace("_", "").isalnum()
        ):
            raise AssetTokenError("Artifact 标识无效")
        payload = {
            "v": 2 if normalized_artifact_id else 1,
            "sha256": sha256,
            "recipient_type": recipient_type,
            "recipient_id": recipient_id,
            "exp": int(now if now is not None else time.time()) + ttl,
        }
        if normalized_artifact_id:
            payload["artifact_id"] = normalized_artifact_id
        encoded = _b64encode(json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"))
        signature = _b64encode(hmac.new(
            self.secret,
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest())
        return f"{encoded}.{signature}"

    def verify(
        self,
        token: str,
        *,
        recipient_type: str | None = None,
        recipient_id: str | None = None,
        now: int | None = None,
    ) -> AssetTokenClaims:
        raw = str(token or "")
        if len(raw) > 8192 or raw.count(".") != 1:
            raise AssetTokenError("资产 Token 无效")
        try:
            raw.encode("ascii", errors="strict")
        except UnicodeEncodeError as exc:
            raise AssetTokenError("资产 Token 无效") from exc
        encoded, signature = raw.split(".", 1)
        expected = _b64encode(hmac.new(
            self.secret,
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest())
        if not hmac.compare_digest(signature, expected):
            raise AssetTokenError("资产 Token 无效")
        try:
            payload = json.loads(_b64decode(encoded).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
            raise AssetTokenError("资产 Token 无效") from exc
        if not isinstance(payload, dict):
            raise AssetTokenError("资产 Token 无效")
        version = payload.get("v")
        expected_keys = {
            "v", "sha256", "recipient_type", "recipient_id", "exp",
        }
        if version == 2:
            expected_keys.add("artifact_id")
        if version not in {1, 2} or set(payload) != expected_keys:
            raise AssetTokenError("资产 Token 无效")
        try:
            try:
                sha256 = validate_sha256(str(payload.get("sha256") or ""))
            except SandboxServiceError as exc:
                raise AssetTokenError("资产 Token 无效") from exc
            claim_type, claim_id = _recipient(
                payload.get("recipient_type"),
                payload.get("recipient_id"),
            )
            expires_at = int(payload.get("exp"))
            artifact_id = str(payload.get("artifact_id") or "").strip()
            if version == 2 and not (
                artifact_id.startswith("art_")
                and 8 <= len(artifact_id) <= 64
                and artifact_id.replace("_", "").isalnum()
            ):
                raise AssetTokenError("资产 Token 无效")
        except (ValueError, TypeError) as exc:
            raise AssetTokenError("资产 Token 无效") from exc
        if expires_at <= int(now if now is not None else time.time()):
            raise AssetTokenError("资产 Token 已过期")
        if recipient_type is not None or recipient_id is not None:
            expected_type, expected_id = _recipient(recipient_type, recipient_id)
            if not (
                hmac.compare_digest(claim_type, expected_type)
                and hmac.compare_digest(claim_id, expected_id)
            ):
                raise AssetTokenError("资产 Token 收件人不匹配")
        return AssetTokenClaims(
            asset_sha256=sha256,
            recipient_type=claim_type,
            recipient_id=claim_id,
            expires_at=expires_at,
            artifact_id=artifact_id,
        )


def signer_from_settings(db=None) -> AssetTokenSigner:
    from core.config_registry import SETTING_DEFS
    from core.database import SystemSetting
    from core.settings_service import coerce_setting_value, settings

    values: dict[str, Any] = {}
    for key in ("sandbox.asset_token_secret", "sandbox.asset_token_ttl_seconds"):
        value = None
        if db is not None:
            try:
                row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
                if row is not None and row.value is not None:
                    value = coerce_setting_value(row.value, SETTING_DEFS[key])
            except Exception:
                value = None
        values[key] = settings.get(key) if value is None else value
    return AssetTokenSigner(
        str(values["sandbox.asset_token_secret"] or ""),
        default_ttl_seconds=int(values["sandbox.asset_token_ttl_seconds"]),
    )

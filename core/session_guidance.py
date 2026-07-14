"""会话专属指导的校验、摘要与只读解析。"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from core.chat_stream_identity import (
    ChatStreamIdentityError,
    parse_canonical_chat_stream_id,
    resolve_chat_stream_identity,
)


SESSION_GUIDANCE_MAX_CHARS = 4000

SessionGuidanceStatus = Literal[
    "not_requested",
    "missing",
    "empty",
    "configured",
]

_SESSION_GUIDANCE_STATUSES = {
    "not_requested",
    "missing",
    "empty",
    "configured",
}
_RESERVED_MARKERS = (
    "<session_guidance",
    "</session_guidance",
    "<runtime_context",
    "<identity_context",
    "<persona_reference",
    "<conversation_context",
    "<user_input",
    "[runtimetool]",
)


class SessionGuidanceValidationError(ValueError):
    """指导正文或摘要状态不符合公共契约。"""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class SessionGuidanceResolution:
    """一次会话指导解析的正文与安全摘要。"""

    chat_stream_id: str
    text: str = field(repr=False)
    configured: bool
    chars: int
    sha256: str
    updated_at: datetime | None
    status: SessionGuidanceStatus

    @property
    def debug(self) -> dict[str, object]:
        return {
            "session_guidance_chat_stream_id": self.chat_stream_id,
            "session_guidance_configured": self.configured,
            "session_guidance_chars": self.chars,
            "session_guidance_sha256": self.sha256,
            "session_guidance_resolution_status": self.status,
        }


def normalize_session_guidance(text: str) -> str:
    """规范化指导正文，并拒绝会破坏运行时结构的内容。"""
    if not isinstance(text, str):
        raise SessionGuidanceValidationError(
            "session_guidance_invalid_type",
            "指导正文必须是字符串",
        )

    line_normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if any(
        unicodedata.category(char) in {"Cc", "Cs"}
        and char not in {"\n", "\t"}
        for char in line_normalized
    ):
        raise SessionGuidanceValidationError(
            "session_guidance_invalid_character",
            "指导正文包含不允许的控制字符或代理码点",
        )

    normalized = line_normalized.strip()
    chars = len(normalized)
    if chars > SESSION_GUIDANCE_MAX_CHARS:
        raise SessionGuidanceValidationError(
            "session_guidance_too_long",
            f"指导正文字符数 {chars} 超过上限 {SESSION_GUIDANCE_MAX_CHARS}",
        )

    folded = normalized.casefold()
    if any(marker in folded for marker in _RESERVED_MARKERS):
        raise SessionGuidanceValidationError(
            "session_guidance_reserved_marker",
            "指导正文包含保留的运行时结构标记",
        )
    return normalized


def _build_resolution(
    *,
    chat_stream_id: str,
    normalized_text: str,
    updated_at: datetime | None,
    status: SessionGuidanceStatus,
) -> SessionGuidanceResolution:
    configured = bool(normalized_text)
    digest = (
        hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        if configured
        else ""
    )
    return SessionGuidanceResolution(
        chat_stream_id=chat_stream_id,
        text=normalized_text,
        configured=configured,
        chars=len(normalized_text),
        sha256=digest,
        updated_at=updated_at,
        status=status,
    )


def summarize_session_guidance(
    *,
    chat_stream_id: str,
    text: str,
    updated_at: datetime | None,
    status: SessionGuidanceStatus,
) -> SessionGuidanceResolution:
    """从正文生成不在 debug 中复制正文的确定性摘要。"""
    if not isinstance(status, str) or status not in _SESSION_GUIDANCE_STATUSES:
        raise SessionGuidanceValidationError(
            "session_guidance_invalid_status",
            "指导解析状态不合法",
        )

    if status == "not_requested":
        if chat_stream_id != "":
            raise SessionGuidanceValidationError(
                "session_guidance_inconsistent_identity",
                "not_requested 状态不能携带会话身份",
            )
    else:
        try:
            parse_canonical_chat_stream_id(chat_stream_id)
        except ChatStreamIdentityError as exc:
            raise SessionGuidanceValidationError(
                "session_guidance_inconsistent_identity",
                "指导解析状态要求合法的 canonical 会话身份",
            ) from exc

    normalized = normalize_session_guidance(text)
    if status == "configured" and not normalized:
        raise SessionGuidanceValidationError(
            "session_guidance_inconsistent_status",
            "configured 状态要求非空正文",
        )
    if status != "configured" and normalized:
        raise SessionGuidanceValidationError(
            "session_guidance_inconsistent_status",
            "非 configured 状态要求空正文",
        )
    return _build_resolution(
        chat_stream_id=chat_stream_id,
        normalized_text=normalized,
        updated_at=updated_at,
        status=status,
    )


def resolve_session_guidance(
    db: Any,
    *,
    platform: str,
    chat_type: str,
    session_id: str,
) -> SessionGuidanceResolution:
    """按 canonical 会话身份读取指导；任何解析或数据库异常均向上传播。"""
    from core.database import ChatStreamConfig

    identity = resolve_chat_stream_identity(
        platform=platform,
        chat_type=chat_type,
        session_id=session_id,
    )
    with db.no_autoflush:
        row = (
            db.query(ChatStreamConfig)
            .filter(ChatStreamConfig.chat_stream_id == identity.chat_stream_id)
            .one_or_none()
        )
    if row is None:
        return _build_resolution(
            chat_stream_id=identity.chat_stream_id,
            normalized_text="",
            updated_at=None,
            status="missing",
        )

    normalized = normalize_session_guidance(row.session_guidance)
    status: SessionGuidanceStatus = "configured" if normalized else "empty"
    return _build_resolution(
        chat_stream_id=identity.chat_stream_id,
        normalized_text=normalized,
        updated_at=row.session_guidance_updated_at,
        status=status,
    )

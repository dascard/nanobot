"""会话配置使用的 canonical chat stream identity。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import quote, unquote


_PLATFORM_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_CHAT_TYPES = {"group", "private"}
_ENCODE_SAFE = "-._~"
_INVALID_PERCENT_ESCAPE_RE = re.compile(r"%(?![0-9A-F]{2})")


class ChatStreamIdentityError(ValueError):
    """会话配置身份无法无歧义规范化。"""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ChatStreamIdentity:
    """已校验且可稳定序列化的会话配置身份。"""

    platform: str
    chat_type: str
    external_session_id: str
    encoded_external_session_id: str
    chat_stream_id: str


def _normalize_platform(value: str) -> str:
    platform = str(value or "").strip().lower()
    if not _PLATFORM_RE.fullmatch(platform):
        raise ChatStreamIdentityError(
            "invalid_platform",
            "platform 必须是合法的小写平台标识",
        )
    return platform


def _normalize_chat_type(value: str) -> str:
    chat_type = str(value or "").strip().lower()
    if chat_type not in _CHAT_TYPES:
        raise ChatStreamIdentityError(
            "invalid_chat_type",
            "chat_type 只允许 group 或 private",
        )
    return chat_type


def _validate_external_session_id(value: str, *, empty_code: str) -> str:
    if not value:
        raise ChatStreamIdentityError(empty_code, "session_id 不能为空")
    if any(unicodedata.category(char) in {"Cc", "Cs"} for char in value):
        raise ChatStreamIdentityError(
            "invalid_external_session_id",
            "session_id 不能包含控制字符或代理码点",
        )
    return value


def _encode_external_session_id(value: str) -> str:
    try:
        return quote(value, safe=_ENCODE_SAFE, encoding="utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ChatStreamIdentityError(
            "invalid_external_session_id",
            "session_id 不是合法 Unicode 文本",
        ) from exc


def _build_identity(
    *,
    platform: str,
    chat_type: str,
    external_session_id: str,
) -> ChatStreamIdentity:
    encoded = _encode_external_session_id(external_session_id)
    return ChatStreamIdentity(
        platform=platform,
        chat_type=chat_type,
        external_session_id=external_session_id,
        encoded_external_session_id=encoded,
        chat_stream_id=f"{platform}:{encoded}:{chat_type}",
    )


def parse_canonical_chat_stream_id(chat_stream_id: str) -> ChatStreamIdentity:
    """严格解析 canonical key，并拒绝任何非规范序列化。"""
    value = str(chat_stream_id or "")
    parts = value.split(":")
    if len(parts) != 3:
        raise ChatStreamIdentityError(
            "invalid_canonical_id",
            "canonical chat_stream_id 必须包含三个字段",
        )

    platform, encoded_external_session_id, chat_type = parts
    if not _PLATFORM_RE.fullmatch(platform):
        raise ChatStreamIdentityError(
            "invalid_platform",
            "canonical platform 不是合法的小写平台标识",
        )
    if chat_type not in _CHAT_TYPES:
        raise ChatStreamIdentityError(
            "invalid_chat_type",
            "canonical chat_type 只允许 group 或 private",
        )
    if not encoded_external_session_id:
        raise ChatStreamIdentityError(
            "invalid_external_session_id",
            "canonical external_session_id 不能为空",
        )
    if _INVALID_PERCENT_ESCAPE_RE.search(encoded_external_session_id):
        raise ChatStreamIdentityError(
            "invalid_percent_encoding",
            "canonical external_session_id 包含非法百分号编码",
        )

    try:
        external_session_id = unquote(
            encoded_external_session_id,
            encoding="utf-8",
            errors="strict",
        )
    except (UnicodeDecodeError, UnicodeEncodeError) as exc:
        raise ChatStreamIdentityError(
            "invalid_percent_encoding",
            "canonical external_session_id 不是合法 UTF-8",
        ) from exc

    _validate_external_session_id(
        external_session_id,
        empty_code="invalid_external_session_id",
    )
    identity = _build_identity(
        platform=platform,
        chat_type=chat_type,
        external_session_id=external_session_id,
    )
    if identity.chat_stream_id != value:
        raise ChatStreamIdentityError(
            "invalid_percent_encoding",
            "canonical chat_stream_id 不是唯一规范序列化",
        )
    return identity


def _looks_like_canonical_chat_stream_id(value: str) -> bool:
    parts = value.split(":")
    if len(parts) != 3:
        return False
    return parts[2] in _CHAT_TYPES


def resolve_chat_stream_identity(
    *,
    platform: str,
    chat_type: str,
    session_id: str,
) -> ChatStreamIdentity:
    """使用显式平台和会话类型解析运行时 session 身份。"""
    normalized_platform = _normalize_platform(platform)
    normalized_chat_type = _normalize_chat_type(chat_type)
    raw_session_value = str(session_id or "")
    _validate_external_session_id(
        raw_session_value,
        empty_code="empty_session_id",
    )
    raw_session_id = raw_session_value.strip()
    _validate_external_session_id(raw_session_id, empty_code="empty_session_id")

    if _looks_like_canonical_chat_stream_id(raw_session_id):
        identity = parse_canonical_chat_stream_id(raw_session_id)
        if identity.platform != normalized_platform:
            raise ChatStreamIdentityError(
                "mismatched_platform",
                "canonical platform 与显式 platform 不一致",
            )
        if identity.chat_type != normalized_chat_type:
            raise ChatStreamIdentityError(
                "mismatched_chat_type",
                "canonical chat_type 与显式 chat_type 不一致",
            )
        return identity

    prefixes = {"group": "group_", "private": "private_"}
    opposite_chat_type = "private" if normalized_chat_type == "group" else "group"
    opposite_prefix = prefixes[opposite_chat_type]
    if raw_session_id.startswith(opposite_prefix):
        raise ChatStreamIdentityError(
            "mismatched_chat_type",
            "session_id 前缀与显式 chat_type 不一致",
        )

    expected_prefix = prefixes[normalized_chat_type]
    external_session_id = raw_session_id.removeprefix(expected_prefix)
    _validate_external_session_id(
        external_session_id,
        empty_code="invalid_external_session_id",
    )
    return _build_identity(
        platform=normalized_platform,
        chat_type=normalized_chat_type,
        external_session_id=external_session_id,
    )


def canonicalize_legacy_chat_stream_id(value: str) -> str | None:
    """只转换 group_/private_ 明确别名；裸 ID 返回 None。"""
    raw_value = str(value or "")
    try:
        _validate_external_session_id(raw_value, empty_code="empty_session_id")
    except ChatStreamIdentityError:
        return None
    raw = raw_value.strip()
    for chat_type, prefix in (("group", "group_"), ("private", "private_")):
        if not raw.startswith(prefix):
            continue
        try:
            return resolve_chat_stream_identity(
                platform="qq",
                chat_type=chat_type,
                session_id=raw,
            ).chat_stream_id
        except ChatStreamIdentityError:
            return None
    return None

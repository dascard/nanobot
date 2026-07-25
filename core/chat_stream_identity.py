"""兼容 façade；canonical 身份实现位于 ``foundation.identity``。"""

from foundation.identity import (
    ChatStreamIdentity,
    ChatStreamIdentityError,
    canonicalize_legacy_chat_stream_id as _canonicalize_legacy_chat_stream_id,
    identity_storage_aliases,
    legacy_runtime_session_id,
    parse_compatibility_chat_stream_identity as _parse_compatibility_chat_stream_identity,
    parse_canonical_chat_stream_id,
    resolve_chat_stream_identity,
)

from core.lifecycle import record_compatibility_usage


def _record_legacy_identity_prefix(value: str) -> None:
    normalized = str(value or "").strip()
    if normalized.startswith("group_"):
        record_compatibility_usage("identity.group_prefix")
    elif normalized.startswith("private_"):
        record_compatibility_usage("identity.private_prefix")


def canonicalize_legacy_chat_stream_id(value: str) -> str | None:
    """转换显式旧身份并记录无原始身份值的兼容用量。"""

    canonical = _canonicalize_legacy_chat_stream_id(value)
    if canonical is not None:
        _record_legacy_identity_prefix(value)
    return canonical


def parse_compatibility_chat_stream_identity(
    value: str,
    *,
    legacy_platform: str = "qq",
) -> ChatStreamIdentity | None:
    """解析 canonical／旧身份；只对成功解析的旧前缀计数。"""

    identity = _parse_compatibility_chat_stream_identity(
        value,
        legacy_platform=legacy_platform,
    )
    if identity is not None:
        _record_legacy_identity_prefix(value)
    return identity


__all__ = [
    "ChatStreamIdentity",
    "ChatStreamIdentityError",
    "canonicalize_legacy_chat_stream_id",
    "identity_storage_aliases",
    "legacy_runtime_session_id",
    "parse_compatibility_chat_stream_identity",
    "parse_canonical_chat_stream_id",
    "resolve_chat_stream_identity",
]

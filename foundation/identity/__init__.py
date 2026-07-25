"""Nanobot 跨子域的 canonical 身份合同。"""

from foundation.identity.contracts import (
    ActorIdentity,
    ChatStreamIdentity,
    ChatStreamIdentityError,
    ExternalSessionId,
    PlatformId,
    Principal,
    RecipientIdentity,
)
from foundation.identity.normalization import (
    canonicalize_legacy_chat_stream_id,
    identity_storage_aliases,
    legacy_runtime_session_id,
    parse_compatibility_chat_stream_identity,
    parse_canonical_chat_stream_id,
    resolve_chat_stream_identity,
)


__all__ = [
    "ActorIdentity",
    "ChatStreamIdentity",
    "ChatStreamIdentityError",
    "ExternalSessionId",
    "PlatformId",
    "Principal",
    "RecipientIdentity",
    "canonicalize_legacy_chat_stream_id",
    "identity_storage_aliases",
    "legacy_runtime_session_id",
    "parse_compatibility_chat_stream_identity",
    "parse_canonical_chat_stream_id",
    "resolve_chat_stream_identity",
]

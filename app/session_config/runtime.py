"""在线请求使用的会话配置查询。"""

from __future__ import annotations

from typing import Any

from core.chat_stream_identity import resolve_chat_stream_identity
from core.database import ChatStreamConfig


def is_database_only_enabled(
    db: Any,
    *,
    platform: str,
    chat_type: str,
    session_id: str,
) -> bool:
    """返回当前会话是否只落库且禁止调用模型。"""

    identity = resolve_chat_stream_identity(
        platform=platform,
        chat_type=chat_type,
        session_id=session_id,
    )
    value = (
        db.query(ChatStreamConfig.database_only)
        .filter(
            ChatStreamConfig.chat_stream_id == identity.chat_stream_id,
        )
        .scalar()
    )
    return True if value is None else bool(value)


def resolve_session_agent_id(
    db: Any,
    *,
    platform: str,
    chat_type: str,
    session_id: str,
) -> str:
    """返回会话绑定的注册 Agent；未覆写时使用 Nanobot。"""

    identity = resolve_chat_stream_identity(
        platform=platform,
        chat_type=chat_type,
        session_id=session_id,
    )
    value = (
        db.query(ChatStreamConfig.agent_id)
        .filter(
            ChatStreamConfig.chat_stream_id == identity.chat_stream_id,
        )
        .scalar()
    )
    normalized = str(value or "nanobot").strip()
    from core.registry.validation import validate_identifier

    return validate_identifier(normalized, field_name="session.agent_id")


__all__ = ["is_database_only_enabled", "resolve_session_agent_id"]

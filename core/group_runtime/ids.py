"""统一 ID 层——group_id / session_id / stream_id 互转。

约定：
  ChatLog.session_id       -> group_123456
  ConversationTurn.session -> group_123456
  GroupMemory.group_id     -> group_123456
  ChatStreamConfig.id      -> qq:123456:group (stream_id)
  ExpressionMemory.stream  -> qq:123456:group (stream_id)
  JargonMemory.stream      -> qq:123456:group (stream_id)
"""

from core.chat_stream_identity import resolve_chat_stream_identity


def normalize_group_session_id(group_id: str) -> str:
    """将任意群号格式统一为 group_<raw_id>。"""
    raw = str(group_id or "").strip()
    if not raw:
        return ""
    if raw.startswith("group_"):
        return raw
    if raw.startswith("qq:") and raw.endswith(":group"):
        raw = raw.removeprefix("qq:").removesuffix(":group")
        return f"group_{raw}"
    return f"group_{raw}"


def normalize_group_stream_id(group_id: str) -> str:
    """将任意群号格式统一为 qq:<raw_id>:group。"""
    raw = str(group_id or "").strip()
    if not raw:
        return ""
    return resolve_chat_stream_identity(
        platform="qq",
        chat_type="group",
        session_id=raw,
    ).chat_stream_id


def raw_group_id(group_id: str) -> str:
    """从任意群号格式提取纯数字 ID。"""
    return normalize_group_session_id(group_id).removeprefix("group_")

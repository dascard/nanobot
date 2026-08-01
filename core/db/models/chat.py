"""聊天与入站身份模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from core.db.base import Base


class User(Base):
    """用户/群聊统一实体。

    id 支持 QQ 用户 ID 与 ``group_<group_id>``；不再创建 ``private_*`` ID。
    name 由消息入口根据 sender/session 名称刷新。
    """

    __tablename__ = "users"
    id = Column(String, primary_key=True, index=True)
    name = Column(String, default="")
    history_clear_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class ChatLog(Base):
    """完整原始消息档案，供审计、画像和离线分析使用。"""

    __tablename__ = "chat_logs"
    __table_args__ = (
        Index("idx_cl_session_id", "session_id", "id"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, index=True)
    session_id = Column(String, index=True)
    sender_name = Column(String, nullable=True)
    session_name = Column(String, nullable=True)
    role = Column(String)
    content = Column(Text)
    processed = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    message_id = Column(String, nullable=True)
    source_message_ids_json = Column(Text, default="[]")
    meta_json = Column(Text, default="{}")


class ConversationTurn(Base):
    """精简会话工作记忆，只存 user/assistant 对话。"""

    __tablename__ = "conversation_turns"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, index=True)
    session_id = Column(String, index=True)
    role = Column(String)
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    source_message_ids_json = Column(Text, default="[]")
    meta_json = Column(Text, default="{}")


class SensitiveData(Base):
    """Guardrail 拒绝的原始消息，独立于 ChatLog 存档。"""

    __tablename__ = "sensitive_data"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, index=True)
    session_id = Column(String)
    content = Column(Text)
    guardrail_status = Column(String, default="silent")
    sender_name = Column(String, default="")
    session_name = Column(String, default="")
    created_at = Column(DateTime, default=datetime.now)


__all__ = ["ChatLog", "ConversationTurn", "SensitiveData", "User"]

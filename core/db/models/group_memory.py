"""群体记忆、表达、黑话、表情和会话配置模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)

from core.db.base import Base


class GroupMemory(Base):
    """群聊中长期稳定的群体认知。"""

    __tablename__ = "group_memories"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    chat_stream_id = Column(String, index=True, nullable=True)
    group_id = Column(String, index=True, nullable=False)
    memory_type = Column(String, index=True, nullable=False)
    content = Column(Text, nullable=False)
    content_hash = Column(String, default="")
    cluster_key = Column(String, nullable=True)
    evidence_log_ids_json = Column(Text, default="[]")
    confidence = Column(Float, default=0.5)
    evidence_count = Column(Integer, default=1)
    first_seen = Column(DateTime, default=datetime.now)
    last_seen = Column(DateTime, default=datetime.now)
    updated_at = Column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
    )
    decay_score = Column(Float, default=1.0)
    status = Column(String, default="active")
    inject_policy = Column(String, default="auto")
    disabled_reason = Column(Text, default="")
    rejected_reason = Column(Text, default="")
    merged_into_id = Column(Integer, nullable=True)
    last_injected_at = Column(DateTime, nullable=True)
    injected_count = Column(Integer, default=0)
    source = Column(String, default="group_analysis")
    meta_json = Column(Text, default="{}")
    approval_source = Column(String(16), nullable=True)
    governance_mode = Column(String(24), nullable=True)
    approved_content_hash = Column(String(64), nullable=True)
    model_review_run_id = Column(String(64), nullable=True)
    model_contract_version = Column(String(64), nullable=True)
    human_reviewer_id = Column(String(128), nullable=True)
    human_reviewed_at = Column(DateTime, nullable=True)
    human_action = Column(String(32), nullable=True)
    conflict_group_id = Column(String(64), nullable=True)
    version = Column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint(
            "group_id",
            "memory_type",
            "content_hash",
            name="uq_group_memory_hash",
        ),
        UniqueConstraint(
            "chat_stream_id",
            "memory_type",
            "content_hash",
            name="uq_group_memory_canonical_hash",
        ),
    )


class ExpressionMemory(Base):
    """群聊表达学习候选。"""

    __tablename__ = "expression_memories"
    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_stream_id = Column(String, index=True, nullable=False)
    expression = Column(Text, nullable=False)
    expression_type = Column(String, default="phrase")
    scene = Column(String, default="")
    example_json = Column(Text, default="[]")
    source_count = Column(Integer, default=1)
    confidence = Column(Float, default=0.5)
    checked = Column(Integer, default=0)
    status = Column(String, default="candidate")
    weight = Column(Float, default=0.5)
    first_seen = Column(DateTime, default=datetime.now)
    last_seen = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint(
            "chat_stream_id",
            "expression",
            name="uq_expr_stream_expr",
        ),
    )


class JargonMemory(Base):
    """群聊黑话和术语学习候选。"""

    __tablename__ = "jargon_memories"
    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_stream_id = Column(String, index=True, nullable=False)
    term = Column(String, index=True, nullable=False)
    meaning = Column(Text, default="")
    examples_json = Column(Text, default="[]")
    confidence = Column(Float, default=0.5)
    checked = Column(Integer, default=0)
    status = Column(String, default="candidate")
    first_seen = Column(DateTime, default=datetime.now)
    last_seen = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint(
            "chat_stream_id",
            "term",
            name="uq_jargon_stream_term",
        ),
    )


class StickerMemory(Base):
    """按群或全局作用域保存的可发送图片引用。"""

    __tablename__ = "sticker_memories"
    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_stream_id = Column(String, index=True, nullable=False)
    sticker_hash = Column(String, index=True, nullable=False)
    file_ref = Column(Text, nullable=False)
    send_code = Column(Text, default="")
    name = Column(String, default="")
    description = Column(Text, default="")
    tags_json = Column(Text, default="[]")
    emotions_json = Column(Text, default="[]")
    source_type = Column(String, default="manual")
    source_count = Column(Integer, default=1)
    status = Column(String, default="active")
    usage_count = Column(Integer, default=0)
    first_seen = Column(DateTime, default=datetime.now)
    last_seen = Column(DateTime, default=datetime.now)
    last_used = Column(DateTime, nullable=True)
    meta_json = Column(Text, default="{}")
    local_path = Column(Text, default="")
    preview_status = Column(String, default="pending")
    content_hash = Column(String, index=True, default="")
    byte_size = Column(Integer, default=0)
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    phash = Column(String(32), default="")
    dhash = Column(String(32), default="")
    ahash = Column(String(32), default="")
    duplicate_of_id = Column(Integer, nullable=True, index=True)
    dedupe_status = Column(String, default="unique")
    describe_status = Column(String, default="pending")
    describe_attempts = Column(Integer, default=0)
    describe_last_error = Column(Text, default="")
    described_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint(
            "chat_stream_id",
            "sticker_hash",
            name="uq_sticker_stream_hash",
        ),
    )


class StickerDuplicateCandidate(Base):
    """感知哈希近邻重复候选。"""

    __tablename__ = "sticker_duplicate_candidates"
    id = Column(Integer, primary_key=True, autoincrement=True)
    sticker_a_id = Column(Integer, index=True, nullable=False)
    sticker_b_id = Column(Integer, index=True, nullable=False)
    content_hash = Column(String(64), default="")
    phash_dist = Column(Integer, default=0)
    dhash_dist = Column(Integer, default=0)
    ahash_dist = Column(Integer, default=0)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.now)
    __table_args__ = (
        UniqueConstraint("sticker_a_id", "sticker_b_id"),
    )


class ChatStreamConfig(Base):
    """群聊或私聊流的类型化配置。"""

    __tablename__ = "chat_stream_configs"
    chat_stream_id = Column(String, primary_key=True)
    talk_value = Column(Float, default=0.5)
    mentioned_bot_reply = Column(Integer, default=1)
    use_expression = Column(Integer, default=1)
    enable_expression_learning = Column(Integer, default=1)
    enable_jargon_learning = Column(Integer, default=1)
    group_profile_mode = Column(String, default="off")
    planner_smooth = Column(Integer, default=3)
    session_guidance = Column(
        Text,
        default="",
        server_default=text("''"),
        nullable=False,
    )
    session_guidance_updated_at = Column(DateTime, nullable=True)
    meta_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now)


__all__ = [
    "ChatStreamConfig",
    "ExpressionMemory",
    "GroupMemory",
    "JargonMemory",
    "StickerDuplicateCandidate",
    "StickerMemory",
]

"""会话滚动摘要与长期记忆摘要模型。"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text, UniqueConstraint, text

from core.db.base import Base


class RollingSessionSummary(Base):
    """当前 session 的滚动上下文摘要。

    私聊覆盖被 recent raw ConversationTurn window 挤出的旧上下文；群聊
    覆盖被保护 raw ChatLog tail 挤出的旧现场。来源游标必须与 source_type
    配套读取，不能把 ChatLog.id 写入 legacy turn 字段。
    """

    __tablename__ = "rolling_session_summaries"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    session_id = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, default="")
    chat_type = Column(String, index=True, default="private")

    status = Column(String, index=True, default="active")
    summary_kind = Column(String, index=True, default="deterministic_fallback")
    summary_text = Column(Text, default="")
    summary_json = Column(Text, default="{}")

    covered_from_turn_id = Column(Integer, default=0)
    covered_until_turn_id = Column(Integer, index=True, default=0)
    source_turn_ids_json = Column(Text, default="[]")
    source_type = Column(String, index=True, default="conversation_turn")
    covered_from_source_id = Column(Integer, default=0)
    covered_until_source_id = Column(Integer, index=True, default=0)
    source_ids_json = Column(Text, default="[]")
    source_turn_count = Column(Integer, default=0)
    source_token_estimate = Column(Integer, default=0)
    source_char_count = Column(Integer, default=0)

    raw_window_start_turn_id = Column(Integer, default=0)
    raw_window_start_source_id = Column(Integer, default=0)
    quality_score = Column(Float, default=0.0)
    issues_json = Column(Text, default="[]")

    model = Column(String, default="")
    prompt_sha256 = Column(String, default="")
    llm_status = Column(String, index=True, default="")
    llm_model = Column(String, default="")
    llm_request_log_id = Column(Integer, nullable=True)
    llm_error = Column(Text, default="")
    retry_count = Column(Integer, default=0)
    next_retry_at = Column(DateTime, nullable=True)
    supersedes_summary_id = Column(Integer, nullable=True)
    stable_hash = Column(String, index=True, default="")
    # 系统 A 滚动摘要归属的块;群聊/旧行为为 NULL。P2 起消费。
    block_id = Column(Integer, index=True, nullable=True)
    meta_json = Column(Text, default="{}")

    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ConversationBlock(Base):
    """私聊会话按连续时间段切分的块。

    相邻消息间隔小于 ``BLOCK_GAP_SECONDS`` 的连续 ConversationTurn 归为一个块，
    块是长期记忆(系统 B episode)与跨块召回的单元。每个 session 至多一个
    ``status='open'`` 的块，靠 ``open_key`` 唯一约束保证(SQLite 多 NULL 互异)。
    """

    __tablename__ = "conversation_blocks"
    __table_args__ = (
        UniqueConstraint("open_key", name="uq_conversation_block_open_key"),
        Index("idx_conv_block_session_status", "session_id", "status", "id"),
        Index("idx_conv_block_session_seq", "session_id", "block_seq"),
        Index("idx_conv_block_status_last_turn", "status", "last_turn_at"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    session_id = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, default="")
    chat_type = Column(String, index=True, default="private")

    block_seq = Column(Integer, default=1)
    status = Column(String, index=True, default="open")
    # open 时 = session_id，closed/cleared 时 = NULL；唯一约束下每 session 至多一个 open 块。
    open_key = Column(String, nullable=True)

    first_turn_id = Column(Integer, index=True, default=0)
    last_turn_id = Column(Integer, index=True, default=0)
    started_at = Column(DateTime, nullable=True)
    # 块内最新 turn 的墙钟；gap 判定、召回时距与 idle sweep 的唯一基准。
    last_turn_at = Column(DateTime, index=True, nullable=True)
    # 封口墙钟;仅生命周期/sweep 用，不用于时距判定。
    closed_at = Column(DateTime, nullable=True)

    turn_count = Column(Integer, default=0)
    token_estimate = Column(Integer, default=0)
    closed_reason = Column(String, default="")

    rolling_summary_id = Column(Integer, nullable=True)  # 系统 A 当前块摘要指针(P2)
    episode_id = Column(Integer, nullable=True)  # 系统 B episode 外键(P3)
    meta_json = Column(Text, default="{}")

    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class ConversationBlockEpisode(Base):
    """已封口块的长期记忆 episode(系统 B)。

    一块至多一条 active episode,是块式会话记忆里唯一进入语义索引的历史长期
    单元。封口时由块的最佳滚动摘要固化(llm_episode)或降级生成
    (deterministic_fallback)。见块式会话记忆 spec §3.2 与机制 3。
    """

    __tablename__ = "conversation_block_episodes"
    __table_args__ = (
        UniqueConstraint("block_id", name="uq_block_episode_block_id"),
        Index("idx_block_episode_session_status", "session_id", "status"),
        Index("idx_block_episode_user_session", "user_id", "session_id"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    block_id = Column(Integer, index=True, nullable=False)
    block_seq = Column(Integer, index=True, default=0)
    session_id = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, default="")
    chat_type = Column(String, default="private")

    status = Column(String, index=True, default="active")  # active | archived
    # deterministic_fallback | llm_episode
    summary_kind = Column(String, default="deterministic_fallback")
    # "" | pending | running | done | failed —— episode 是否已升级为 LLM 版本
    llm_status = Column(String, index=True, default="")

    summary_text = Column(Text, default="")
    summary_json = Column(Text, default="{}")
    covered_first_turn_id = Column(Integer, default=0)
    covered_last_turn_id = Column(Integer, default=0)
    source_turn_ids_json = Column(Text, default="[]")
    source_turn_count = Column(Integer, default=0)
    seed_summary_id = Column(Integer, nullable=True)  # 初稿来源 rolling_session_summaries.id

    quality_score = Column(Float, default=0.0)
    issues_json = Column(Text, default="[]")
    model = Column(String, default="")
    prompt_sha256 = Column(String, default="")
    stable_hash = Column(String, index=True, default="")
    source_revision = Column(String, default="")

    created_at = Column(DateTime, default=datetime.now, index=True)
    sealed_at = Column(DateTime, nullable=True)
    refined_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    meta_json = Column(Text, default="{}")


class SessionSummaryJob(Base):
    """异步 LLM session summary 生成任务。"""

    __tablename__ = "session_summary_jobs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    session_id = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, default="")
    chat_type = Column(String, index=True, default="private")

    covered_from_turn_id = Column(Integer, index=True, default=0)
    covered_until_turn_id = Column(Integer, index=True, default=0)
    source_turn_ids_json = Column(Text, default="[]")
    source_type = Column(String, index=True, default="conversation_turn")
    covered_from_source_id = Column(Integer, index=True, default=0)
    covered_until_source_id = Column(Integer, index=True, default=0)
    source_ids_json = Column(Text, default="[]")

    previous_summary_id = Column(Integer, nullable=True)
    fallback_summary_id = Column(Integer, nullable=True)
    result_summary_id = Column(Integer, nullable=True)

    status = Column(String, index=True, default="pending")
    retry_count = Column(Integer, default=0)
    max_retry = Column(Integer, default=3)
    next_retry_at = Column(DateTime, nullable=True)
    locked_by = Column(String, default="")
    locked_at = Column(DateTime, nullable=True)
    lease_token = Column(String(64), default="")
    lease_expires_at = Column(DateTime, nullable=True)
    generation = Column(Integer, default=0)
    attempt_count = Column(Integer, default=0)
    error = Column(Text, default="")
    finished_at = Column(DateTime, nullable=True)
    stable_hash = Column(String, index=True, default="")
    meta_json = Column(Text, default="{}")

    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class MemoryDigest(Base):
    __tablename__ = "memory_digests"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, index=True)
    session_id = Column(String, index=True)
    digest_date = Column(String, index=True)
    level = Column(Integer, default=0, index=True)
    parent_id = Column(Integer, nullable=True)
    content = Column(Text)
    meta_json = Column(Text, default="{}")
    source_start_log_id = Column(Integer, nullable=True)
    source_end_log_id = Column(Integer, nullable=True)
    generation_job_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.now)


class MemoryDigestJob(Base):
    """单个 session/date 的 MemoryDigest 生成与重试账本。"""

    __tablename__ = "memory_digest_jobs"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "digest_date",
            name="uq_memory_digest_job_source",
        ),
        Index(
            "idx_memory_digest_job_claim",
            "status",
            "lease_expires_at",
            "id",
        ),
        Index(
            "idx_memory_digest_job_retry",
            "status",
            "next_retry_at",
            "id",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, nullable=False)
    digest_date = Column(String, nullable=False)
    user_id = Column(String, nullable=False, default="", server_default=text("''"))

    source_start_log_id = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    source_end_log_id = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    source_log_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    source_revision = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )

    status = Column(
        String(24),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    locked_by = Column(
        String(128),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    lease_token = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    lease_expires_at = Column(DateTime, nullable=True)

    attempt_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    retry_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    max_retry = Column(
        Integer,
        nullable=False,
        default=3,
        server_default=text("3"),
    )
    next_retry_at = Column(DateTime, nullable=True)

    result_digest_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    result_source_id = Column(
        String(128),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    result_root_digest_id = Column(Integer, nullable=True)
    result_semantic_job_id = Column(Integer, nullable=True)
    error_type = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    error_summary = Column(
        Text,
        nullable=False,
        default="",
        server_default=text("''"),
    )
    meta_json = Column(
        Text,
        nullable=False,
        default="{}",
        server_default=text("'{}'"),
    )
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


__all__ = [
    "ConversationBlock",
    "ConversationBlockEpisode",
    "MemoryDigest",
    "MemoryDigestJob",
    "RollingSessionSummary",
    "SessionSummaryJob",
]

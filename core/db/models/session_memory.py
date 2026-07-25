"""会话滚动摘要与长期记忆摘要模型。"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text, UniqueConstraint, text

from core.db.base import Base


class RollingSessionSummary(Base):
    """当前 session 的滚动上下文摘要。

    只覆盖已被 recent raw ConversationTurn window 挤出的旧上下文，不承载
    daily digest、persona 或 group memory 语义。
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
    source_turn_count = Column(Integer, default=0)
    source_token_estimate = Column(Integer, default=0)
    source_char_count = Column(Integer, default=0)

    raw_window_start_turn_id = Column(Integer, default=0)
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
    meta_json = Column(Text, default="{}")

    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


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
    "MemoryDigest",
    "MemoryDigestJob",
    "RollingSessionSummary",
    "SessionSummaryJob",
]

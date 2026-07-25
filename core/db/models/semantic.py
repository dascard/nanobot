"""语义索引、异步索引任务和 RAG 调试模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)

from core.db.base import Base


class SemanticIndexItem(Base):
    __tablename__ = "semantic_index_items"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    source_type = Column(String, index=True, nullable=False, default="")
    source_id = Column(String, index=True, nullable=False, default="")
    source_sub_id = Column(String, index=True, nullable=False, default="")
    document_id = Column(String, index=True, default="")
    chunk_id = Column(String, index=True, default="")
    user_id = Column(String, index=True, default="")
    session_id = Column(String, index=True, default="")
    group_id = Column(String, index=True, default="")
    chat_stream_id = Column(String, index=True, default="")
    visibility = Column(String, index=True, default="recall")
    status = Column(String, index=True, default="active")
    title = Column(Text, default="")
    text = Column(Text, default="")
    lexical_text = Column(Text, default="")
    embedding_text = Column(Text, default="")
    text_hash = Column(String, index=True, default="")
    source_hash = Column(String, index=True, default="")
    source_updated_at = Column(DateTime, nullable=True)
    embedding = Column(LargeBinary, nullable=True)
    embedding_dim = Column(Integer, default=0)
    embedding_model = Column(String, default="")
    embedding_status = Column(String, index=True, default="pending")
    index_version = Column(String, index=True, default="")
    source_revision = Column(
        String,
        nullable=False,
        default="",
        server_default="",
    )
    quality_score = Column(Float, default=0.0)
    trust_level = Column(String, index=True, default="medium")
    source_prior = Column(Float, default=0.5)
    meta_json = Column(Text, default="{}")
    indexed_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
    )
    deleted_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "source_id",
            "source_sub_id",
            "index_version",
            name="uq_semantic_source_sub_version",
        ),
        Index(
            "idx_semantic_item_source_revision_v2",
            "source_type",
            "source_id",
            "source_revision",
            "status",
        ),
    )


class SemanticIndexJob(Base):
    __tablename__ = "semantic_index_jobs"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    source_type = Column(String, index=True, nullable=False, default="")
    source_id = Column(String, index=True, nullable=False, default="")
    source_sub_id = Column(String, index=True, default="")
    job_type = Column(String, index=True, default="upsert")
    index_version = Column(String, index=True, default="")
    source_revision = Column(
        String,
        nullable=False,
        default="",
        server_default="",
    )
    status = Column(String, index=True, default="pending")
    retry_count = Column(Integer, default=0)
    max_retry = Column(Integer, default=3)
    attempt_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    manual_retry_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    next_retry_at = Column(DateTime, nullable=True)
    locked_by = Column(String, default="")
    locked_at = Column(DateTime, nullable=True)
    lease_token = Column(
        String(64),
        nullable=False,
        default="",
        server_default="",
    )
    lease_expires_at = Column(DateTime, nullable=True)
    error = Column(Text, default="")
    meta_json = Column(
        Text,
        nullable=False,
        default="{}",
        server_default="{}",
    )
    created_at = Column(DateTime, default=datetime.now, index=True)
    updated_at = Column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
    )
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index(
            "idx_semantic_job_claim_v2",
            "status",
            "next_retry_at",
            "id",
        ),
        Index(
            "idx_semantic_job_lease_v2",
            "status",
            "lease_expires_at",
            "id",
        ),
        Index(
            "idx_semantic_job_source_revision_v2",
            "source_type",
            "source_id",
            "index_version",
            "source_revision",
            "status",
        ),
    )


class RagDebugRun(Base):
    __tablename__ = "rag_debug_runs"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    trace_id = Column(String, index=True, default="")
    source_type = Column(String, index=True, default="")
    query = Column(Text, default="")
    request_json = Column(Text, default="{}")
    response_json = Column(Text, default="{}")
    degraded = Column(Integer, index=True, default=0)
    fallback_reason = Column(Text, default="")
    latency_ms = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now, index=True)


__all__ = ["RagDebugRun", "SemanticIndexItem", "SemanticIndexJob"]

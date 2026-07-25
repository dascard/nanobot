"""外部知识来源、文档和分块模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from core.db.base import Base


class KnowledgeSource(Base):
    __tablename__ = "knowledge_sources"
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_key = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, default="")
    source_type = Column(String, default="manual")
    domain = Column(String, index=True, default="")
    base_url = Column(Text, default="")
    status = Column(String, index=True, default="active")
    trust_level = Column(String, index=True, default="medium")
    meta_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
    )


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(Integer, index=True, nullable=True)
    document_kind = Column(String, index=True, default="manual_file")
    title = Column(Text, default="")
    url = Column(Text, default="")
    domain = Column(String, index=True, default="")
    author = Column(String, default="")
    published_at = Column(String, index=True, default="")
    summary = Column(Text, default="")
    status = Column(String, index=True, default="active")
    trust_level = Column(String, index=True, default="medium")
    created_by = Column(String, default="")
    updated_by = Column(String, default="")
    disabled_reason = Column(Text, default="")
    disabled_by = Column(String, default="")
    disabled_at = Column(DateTime, nullable=True)
    latest_seen = Column(DateTime, nullable=True)
    meta_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, index=True, nullable=False)
    chunk_id = Column(String, index=True, nullable=False)
    order_index = Column(Integer, default=0)
    title = Column(Text, default="")
    text = Column(Text, default="")
    citation_json = Column(Text, default="{}")
    status = Column(String, index=True, default="active")
    trust_level = Column(String, index=True, default="medium")
    meta_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
    )

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_id",
            name="uq_knowledge_doc_chunk",
        ),
    )


__all__ = ["KnowledgeChunk", "KnowledgeDocument", "KnowledgeSource"]

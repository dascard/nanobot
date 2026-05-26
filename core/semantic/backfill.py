"""从现有业务表回填统一语义索引。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from core.database import (
    GroupMemory,
    KnowledgeChunk,
    KnowledgeDocument,
    MemoryDigest,
    RollingSessionSummary,
    SemanticIndexItem,
    StickerMemory,
)
from core.semantic.adapters import (
    SemanticChunk,
    chunk_from_group_memory,
    chunk_from_knowledge_chunk,
    chunk_from_sticker,
    chunks_from_memory_digest,
    chunks_from_session_summary,
)
from core.semantic.indexer import upsert_semantic_chunks
from core.semantic.schema import ensure_semantic_schema


DEFAULT_BACKFILL_INDEX_VERSION = ""
_SOURCE_ALIASES = {
    "memory": ["memory_digest", "session_summary"],
    "all": ["memory_digest", "session_summary", "group_memory", "sticker", "knowledge"],
}
_SOURCE_TYPES = {"memory_digest", "session_summary", "group_memory", "sticker", "knowledge"}


def source_types_for_backfill(source_type: str) -> list[str]:
    source = str(source_type or "all").strip() or "all"
    if source in _SOURCE_ALIASES:
        return list(_SOURCE_ALIASES[source])
    if source in _SOURCE_TYPES:
        return [source]
    return [source]


def _load_memory_digest_chunks(db: Session, limit: int) -> tuple[int, list[SemanticChunk]]:
    rows = db.query(MemoryDigest).order_by(MemoryDigest.id.asc()).limit(limit).all()
    chunks: list[SemanticChunk] = []
    for row in rows:
        chunks.extend(chunk for chunk in chunks_from_memory_digest(row) if chunk.text or chunk.lexical_text)
    return len(rows), chunks


def _load_session_summary_chunks(db: Session, limit: int) -> tuple[int, list[SemanticChunk]]:
    rows = (
        db.query(RollingSessionSummary)
        .filter(RollingSessionSummary.status == "active")
        .order_by(RollingSessionSummary.id.asc())
        .limit(limit)
        .all()
    )
    chunks: list[SemanticChunk] = []
    for row in rows:
        chunks.extend(chunk for chunk in chunks_from_session_summary(row) if chunk.text or chunk.lexical_text)
    return len(rows), chunks


def _load_group_memory_chunks(db: Session, limit: int) -> tuple[int, list[SemanticChunk]]:
    rows = (
        db.query(GroupMemory)
        .filter(GroupMemory.status == "active")
        .order_by(GroupMemory.id.asc())
        .limit(limit)
        .all()
    )
    chunks = [chunk_from_group_memory(row) for row in rows if row.content]
    return len(rows), chunks


def _load_sticker_chunks(db: Session, limit: int) -> tuple[int, list[SemanticChunk]]:
    rows = (
        db.query(StickerMemory)
        .filter(StickerMemory.status == "active")
        .order_by(StickerMemory.id.asc())
        .limit(limit)
        .all()
    )
    chunks = []
    for row in rows:
        chunk = chunk_from_sticker(row)
        if chunk is not None:
            chunks.append(chunk)
    return len(rows), chunks


def _load_knowledge_chunks(db: Session, limit: int) -> tuple[int, list[SemanticChunk]]:
    rows = (
        db.query(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .filter(KnowledgeDocument.status == "active")
        .filter(KnowledgeChunk.status == "active")
        .order_by(KnowledgeDocument.id.asc(), KnowledgeChunk.order_index.asc(), KnowledgeChunk.id.asc())
        .limit(limit)
        .all()
    )
    chunks = [
        chunk_from_knowledge_chunk(chunk, document=document)
        for chunk, document in rows
        if chunk.text
    ]
    return len(rows), chunks


def _load_source_chunks(db: Session, source_type: str, limit: int) -> tuple[int, list[SemanticChunk]]:
    if source_type == "memory_digest":
        return _load_memory_digest_chunks(db, limit)
    if source_type == "session_summary":
        return _load_session_summary_chunks(db, limit)
    if source_type == "group_memory":
        return _load_group_memory_chunks(db, limit)
    if source_type == "sticker":
        return _load_sticker_chunks(db, limit)
    if source_type == "knowledge":
        return _load_knowledge_chunks(db, limit)
    return 0, []


def _active_index_count(db: Session, source_types: list[str]) -> int:
    if not source_types:
        return 0
    return (
        db.query(SemanticIndexItem)
        .filter(SemanticIndexItem.source_type.in_(source_types))
        .filter(SemanticIndexItem.status == "active")
        .count()
    )


def preview_semantic_index_backfill(
    db: Session,
    *,
    source_type: str = "all",
    limit_per_source: int = 500,
) -> dict[str, Any]:
    ensure_semantic_schema(db.bind)
    source_types = source_types_for_backfill(source_type)
    sources: dict[str, dict[str, Any]] = {}
    total_rows = 0
    total_chunks = 0
    for item_source_type in source_types:
        source_rows, chunks = _load_source_chunks(db, item_source_type, int(limit_per_source))
        indexed_items = _active_index_count(db, [item_source_type])
        sources[item_source_type] = {
            "source_rows": source_rows,
            "buildable_chunks": len(chunks),
            "indexed_items": indexed_items,
            "needs_build": indexed_items == 0 and len(chunks) > 0,
        }
        total_rows += source_rows
        total_chunks += len(chunks)
    indexed_items = _active_index_count(db, source_types)
    return {
        "source_type": str(source_type or "all"),
        "source_types": source_types,
        "source_rows": total_rows,
        "buildable_chunks": total_chunks,
        "indexed_items": indexed_items,
        "needs_build": indexed_items == 0 and total_chunks > 0,
        "limit_per_source": int(limit_per_source),
        "sources": sources,
    }


def build_semantic_index_from_existing_data(
    db: Session,
    *,
    source_type: str = "all",
    limit_per_source: int = 500,
    index_version: str = DEFAULT_BACKFILL_INDEX_VERSION,
) -> dict[str, Any]:
    ensure_semantic_schema(db.bind)
    source_types = source_types_for_backfill(source_type)
    sources: dict[str, dict[str, Any]] = {}
    total_rows = 0
    total_chunks = 0
    for item_source_type in source_types:
        source_rows, chunks = _load_source_chunks(db, item_source_type, int(limit_per_source))
        rows = []
        if chunks:
            rows = upsert_semantic_chunks(
                db,
                chunks,
                index_version=index_version,
                embedding_enabled=False,
            )
        sources[item_source_type] = {
            "source_rows": source_rows,
            "buildable_chunks": len(chunks),
            "indexed_chunks": len(rows),
            "index_version": index_version,
        }
        total_rows += source_rows
        total_chunks += len(rows)
    return {
        "source_type": str(source_type or "all"),
        "source_types": source_types,
        "source_rows": total_rows,
        "indexed_chunks": total_chunks,
        "index_version": index_version,
        "sources": sources,
    }

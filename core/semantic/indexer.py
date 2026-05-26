"""语义索引写入工具。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import SemanticIndexItem
from core.semantic.adapters import SemanticChunk
from core.semantic.schema import ensure_semantic_schema


def _normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_value(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple, set)):
        normalized = [_normalize_value(item) for item in value]
        cleaned = [
            item.strip() if isinstance(item, str) else item
            for item in normalized
            if item is not None and (not isinstance(item, str) or item.strip())
        ]
        if all(isinstance(item, (str, int, float, bool)) for item in cleaned):
            return sorted(cleaned, key=lambda item: str(item))
        return cleaned
    if isinstance(value, str):
        return value.strip()
    return value


def stable_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        _normalize_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_index_version(
    embedding_model: str,
    embedding_template_version: str,
    chunk_strategy_version: str,
) -> str:
    return f"{embedding_model}:{embedding_template_version}:{chunk_strategy_version}"


def source_hash_for_chunk(chunk: SemanticChunk) -> str:
    return stable_hash({
        "source_type": chunk.source_type,
        "source_id": chunk.source_id,
        "source_sub_id": chunk.source_sub_id,
        "title": chunk.title,
        "text": chunk.text,
        "lexical_text": chunk.lexical_text,
        "embedding_text": chunk.embedding_text,
        "metadata": chunk.metadata,
    })


def upsert_semantic_chunks(
    db: Session,
    chunks: list[SemanticChunk],
    *,
    index_version: str,
    embedding_model: str = "",
    embeddings: dict[str, bytes] | None = None,
    embedding_enabled: bool | None = None,
) -> list[SemanticIndexItem]:
    ensure_semantic_schema(db.bind)
    rows: list[SemanticIndexItem] = []
    embedding_enabled = bool(embedding_model) if embedding_enabled is None else bool(embedding_enabled)
    embeddings = embeddings or {}
    for chunk in chunks:
        row = (
            db.query(SemanticIndexItem)
            .filter(
                SemanticIndexItem.source_type == chunk.source_type,
                SemanticIndexItem.source_id == str(chunk.source_id),
                SemanticIndexItem.source_sub_id == chunk.source_sub_id,
                SemanticIndexItem.index_version == index_version,
            )
            .first()
        )
        if row is None:
            row = SemanticIndexItem(
                source_type=chunk.source_type,
                source_id=str(chunk.source_id),
                source_sub_id=chunk.source_sub_id,
                index_version=index_version,
            )
            db.add(row)

        row.document_id = str(chunk.metadata.get("document_id", "") or "")
        row.chunk_id = str(chunk.metadata.get("chunk_id", chunk.source_sub_id) or "")
        row.user_id = str(chunk.metadata.get("user_id", "") or "")
        row.session_id = str(chunk.metadata.get("session_id", "") or "")
        row.group_id = str(chunk.metadata.get("group_id", "") or "")
        row.chat_stream_id = str(chunk.metadata.get("chat_stream_id", "") or "")
        row.visibility = chunk.visibility
        row.status = "active"
        row.title = chunk.title
        row.text = chunk.text
        row.lexical_text = chunk.lexical_text
        row.embedding_text = chunk.embedding_text
        row.text_hash = stable_hash({"text": chunk.text})
        row.source_hash = source_hash_for_chunk(chunk)
        row.embedding = embeddings.get(chunk.source_sub_id)
        row.embedding_model = embedding_model
        if row.embedding:
            row.embedding_status = "ok"
        else:
            row.embedding_status = "pending" if embedding_enabled else "disabled"
        row.quality_score = float(chunk.quality_score or 0.0)
        row.trust_level = chunk.trust_level
        row.source_prior = float(chunk.source_prior or 0.0)
        row.meta_json = json.dumps(chunk.metadata or {}, ensure_ascii=False, sort_keys=True)
        db.flush()

        db.execute(
            text(
                "DELETE FROM semantic_index_fts WHERE rowid = :rowid"
            ),
            {"rowid": row.id},
        )
        db.execute(
            text(
                "INSERT INTO semantic_index_fts(rowid, title, text, lexical_text, source_type, source_id, source_sub_id) "
                "VALUES (:rowid, :title, :text, :lexical_text, :source_type, :source_id, :source_sub_id)"
            ),
            {
                "rowid": row.id,
                "title": row.title,
                "text": row.text,
                "lexical_text": row.lexical_text,
                "source_type": row.source_type,
                "source_id": row.source_id,
                "source_sub_id": row.source_sub_id,
            },
        )
        rows.append(row)

    db.commit()
    return rows

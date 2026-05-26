"""语义索引 worker 单任务处理。"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import SemanticIndexItem, SemanticIndexJob
from core.semantic.adapters import SemanticChunk
from core.semantic.indexer import upsert_semantic_chunks
from core.semantic.jobs import finish_job
from core.semantic.schema import ensure_semantic_schema


ChunkLoader = Callable[[SemanticIndexJob], list[SemanticChunk]]


def _mark_source_deleted(db: Session, job: SemanticIndexJob) -> None:
    rows = (
        db.query(SemanticIndexItem)
        .filter(SemanticIndexItem.source_type == job.source_type)
        .filter(SemanticIndexItem.source_id == str(job.source_id))
        .filter(SemanticIndexItem.index_version == job.index_version)
        .all()
    )
    now = datetime.now()
    for row in rows:
        row.status = "deleted"
        row.deleted_at = now
        db.execute(text("DELETE FROM semantic_index_fts WHERE rowid = :rowid"), {"rowid": row.id})
    db.commit()


def _embedding_bytes_by_sub_id(
    chunks: list[SemanticChunk],
    embedding_provider,
) -> tuple[dict[str, bytes], str]:
    if embedding_provider is None or not chunks:
        return {}, ""
    try:
        vectors = embedding_provider.embed([chunk.embedding_text for chunk in chunks])
    except Exception as exc:
        return {}, str(exc)

    embeddings: dict[str, bytes] = {}
    for chunk, vector in zip(chunks, vectors):
        if isinstance(vector, bytes):
            embeddings[chunk.source_sub_id] = vector
        else:
            embeddings[chunk.source_sub_id] = repr(list(vector)).encode("utf-8")
    return embeddings, ""


def process_semantic_index_job(
    db: Session,
    job: SemanticIndexJob,
    *,
    chunk_loader: ChunkLoader,
    embedding_provider=None,
) -> SemanticIndexJob:
    ensure_semantic_schema(db.bind)
    try:
        if job.job_type == "delete":
            _mark_source_deleted(db, job)
            return finish_job(db, job, status="done")

        chunks = chunk_loader(job)
        embeddings, embedding_error = _embedding_bytes_by_sub_id(chunks, embedding_provider)
        try:
            rows = upsert_semantic_chunks(
                db,
                chunks,
                index_version=job.index_version,
                embeddings=embeddings,
            )
        except Exception as exc:
            db.rollback()
            return finish_job(db, job, status="failed", error=str(exc))

        if embedding_error:
            for row in rows:
                row.embedding_status = "failed"
            db.commit()
            return finish_job(db, job, status="done_with_warning", error=embedding_error)

        return finish_job(db, job, status="done")
    except Exception as exc:
        db.rollback()
        return finish_job(db, job, status="failed", error=str(exc))

"""语义索引 worker。"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from typing import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import SemanticIndexItem, SemanticIndexJob, SessionLocal
from core.semantic.adapters import (
    SemanticChunk,
    chunk_from_group_memory,
    chunk_from_knowledge_chunk,
    chunk_from_sticker,
    chunks_from_memory_digest,
    chunks_from_session_summary,
)
from core.semantic.indexer import upsert_semantic_chunks
from core.semantic.jobs import claim_next_job, finish_job, recover_timed_out_jobs
from core.semantic.provider_factory import get_embedding_provider
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
            embeddings[chunk.source_sub_id] = json.dumps(
                [float(item) for item in vector],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
    return embeddings, ""


def _default_chunk_loader(db: Session) -> ChunkLoader:
    def load(job: SemanticIndexJob) -> list[SemanticChunk]:
        source_type = str(job.source_type or "")
        source_id = str(job.source_id or "")
        if source_type == "memory_digest":
            from core.database import MemoryDigest

            row = db.query(MemoryDigest).filter(MemoryDigest.id == int(source_id)).first()
            return chunks_from_memory_digest(row) if row is not None else []
        if source_type == "session_summary":
            from core.database import RollingSessionSummary

            row = db.query(RollingSessionSummary).filter(RollingSessionSummary.id == int(source_id)).first()
            return chunks_from_session_summary(row) if row is not None else []
        if source_type == "group_memory":
            from core.database import GroupMemory

            row = db.query(GroupMemory).filter(GroupMemory.id == int(source_id)).first()
            return [chunk_from_group_memory(row)] if row is not None else []
        if source_type == "sticker":
            from core.database import StickerMemory

            row = db.query(StickerMemory).filter(StickerMemory.id == int(source_id)).first()
            chunk = chunk_from_sticker(row) if row is not None else None
            return [chunk] if chunk is not None else []
        if source_type == "knowledge":
            from core.database import KnowledgeChunk, KnowledgeDocument

            document = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == int(source_id)).first()
            query = db.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == int(source_id))
            if job.source_sub_id:
                query = query.filter(KnowledgeChunk.chunk_id == job.source_sub_id)
            rows = query.order_by(KnowledgeChunk.order_index.asc()).all()
            return [chunk_from_knowledge_chunk(row, document=document) for row in rows]
        return []

    return load


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
                embedding_enabled=embedding_provider is not None,
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


def run_once(
    *,
    db: Session | None = None,
    worker_id: str = "semantic-index-worker",
    chunk_loader: ChunkLoader | None = None,
    embedding_provider=None,
    recover_timeout_seconds: int = 900,
) -> bool:
    close_db = db is None
    if db is None:
        db = SessionLocal()
    try:
        recover_timed_out_jobs(db, timeout_seconds=recover_timeout_seconds)
        job = claim_next_job(db, worker_id=worker_id)
        if job is None:
            return False
        process_semantic_index_job(
            db,
            job,
            chunk_loader=chunk_loader or _default_chunk_loader(db),
            embedding_provider=embedding_provider if embedding_provider is not None else get_embedding_provider(),
        )
        return True
    finally:
        if close_db:
            db.close()


def run_forever(
    *,
    worker_id: str = "semantic-index-worker",
    interval_seconds: float = 10.0,
    embedding_provider=None,
) -> None:
    while True:
        processed = run_once(
            worker_id=worker_id,
            embedding_provider=embedding_provider,
        )
        if not processed:
            time.sleep(max(0.1, float(interval_seconds)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Semantic index worker")
    parser.add_argument("--loop", action="store_true", help="持续消费 semantic_index_jobs")
    parser.add_argument("--interval", type=float, default=10.0, help="空轮询间隔秒数")
    parser.add_argument("--owner", default="semantic-index-worker", help="worker owner id")
    args = parser.parse_args(argv)
    if args.loop:
        run_forever(worker_id=args.owner, interval_seconds=args.interval)
    else:
        run_once(worker_id=args.owner)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

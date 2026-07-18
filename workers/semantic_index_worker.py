"""语义索引 worker。"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections.abc import Callable
from dataclasses import replace

from sqlalchemy.orm import Session

from core.database import SemanticIndexJob, SessionLocal
from core.semantic.adapters import (
    SemanticChunk,
    chunk_from_group_memory,
    chunk_from_knowledge_chunk,
    chunk_from_sticker,
    chunks_from_memory_digest,
    chunks_from_session_summary,
    is_recallable_knowledge_chunk,
    is_recallable_knowledge_document,
)
from core.semantic.indexer import reconcile_semantic_source
from core.semantic.jobs import (
    DEFAULT_LEASE_SECONDS,
    SemanticJobLeaseLost,
    claim_next_job,
    fail_job,
    heartbeat_job,
    recover_timed_out_jobs,
    semantic_job_lease,
)
from core.semantic.provider_factory import get_embedding_provider, get_rag_runtime_config
from core.semantic.schema import ensure_semantic_schema


ChunkLoader = Callable[[SemanticIndexJob], list[SemanticChunk]]


def _job_meta(job: SemanticIndexJob) -> dict:
    try:
        value = json.loads(job.meta_json or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _legacy_physical_source_chunks(
    chunks: list[SemanticChunk],
    *,
    job: SemanticIndexJob,
) -> list[SemanticChunk]:
    """只为部署前遗留物理 ID job 保持可消费；新 job 必须使用 v2 meta。"""

    return [
        replace(
            chunk,
            source_id=str(job.source_id or ""),
            metadata={**chunk.metadata, "legacy_physical_source_id": True},
        )
        for chunk in chunks
    ]


def _embedding_bytes_by_sub_id(
    chunks: list[SemanticChunk],
    embedding_provider,
) -> tuple[dict[str, bytes], str]:
    if embedding_provider is None or not chunks:
        return {}, ""
    try:
        raw_vectors = embedding_provider.embed(
            [chunk.embedding_text for chunk in chunks]
        )
        vectors = list(raw_vectors)
    except Exception as exc:
        return {}, f"embedding_provider_error:{type(exc).__name__}"
    if len(vectors) != len(chunks):
        return {}, "embedding_vector_count_mismatch"

    encoded_vectors: list[bytes] = []
    numeric_dimensions: set[int] = set()
    for vector in vectors:
        if isinstance(vector, bytes):
            if not vector:
                return {}, "embedding_vector_empty"
            try:
                raw_values = json.loads(vector)
            except Exception:
                return {}, "embedding_vector_invalid"
            if not isinstance(raw_values, list):
                return {}, "embedding_vector_invalid"
        else:
            raw_values = vector
        try:
            values = [float(item) for item in raw_values]
        except Exception as exc:
            return {}, f"embedding_provider_error:{type(exc).__name__}"
        if not values:
            return {}, "embedding_vector_empty"
        if not all(math.isfinite(value) for value in values):
            return {}, "embedding_vector_non_finite"
        if not any(value != 0.0 for value in values):
            return {}, "embedding_vector_zero_norm"
        numeric_dimensions.add(len(values))
        encoded_vectors.append(json.dumps(
            values,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8"))
    if len(numeric_dimensions) > 1:
        return {}, "embedding_vector_dimension_mismatch"

    embeddings: dict[str, bytes] = {}
    for chunk, encoded_vector in zip(chunks, encoded_vectors, strict=True):
        embeddings[chunk.source_sub_id] = encoded_vector
    return embeddings, ""


def _safe_worker_error(exc: Exception, *, prefix: str) -> str:
    return f"{prefix}:{type(exc).__name__}"


def _default_chunk_loader(db: Session) -> ChunkLoader:
    def load(job: SemanticIndexJob) -> list[SemanticChunk]:
        source_type = str(job.source_type or "")
        source_id = str(job.source_id or "")
        meta = _job_meta(job)
        if source_type == "memory_digest":
            from core.database import MemoryDigest

            document_ids = meta.get("document_ids")
            if isinstance(document_ids, list) and document_ids:
                normalized_ids = [int(item) for item in document_ids if int(item or 0) > 0]
                rows = (
                    db.query(MemoryDigest)
                    .filter(MemoryDigest.id.in_(normalized_ids))
                    .order_by(MemoryDigest.level.asc(), MemoryDigest.id.asc())
                    .all()
                )
                return chunks_from_memory_digest(rows)
            if not source_id.isdigit():
                return []
            row = db.query(MemoryDigest).filter(MemoryDigest.id == int(source_id)).first()
            chunks = chunks_from_memory_digest(row) if row is not None else []
            return _legacy_physical_source_chunks(chunks, job=job)
        if source_type == "session_summary":
            from core.database import RollingSessionSummary

            document_id = int(meta.get("document_id") or 0)
            if document_id > 0:
                row = db.get(RollingSessionSummary, document_id)
                return chunks_from_session_summary(row) if row is not None else []
            if not source_id.isdigit():
                return []
            row = db.get(RollingSessionSummary, int(source_id))
            chunks = chunks_from_session_summary(row) if row is not None else []
            return _legacy_physical_source_chunks(chunks, job=job)
        if source_type == "group_memory":
            from core.database import GroupMemory

            row = db.query(GroupMemory).filter(
                GroupMemory.id == int(source_id),
                GroupMemory.status == "active",
            ).first()
            return [chunk_from_group_memory(row)] if row is not None else []
        if source_type == "sticker":
            from core.database import StickerMemory

            row = db.query(StickerMemory).filter(StickerMemory.id == int(source_id)).first()
            chunk = chunk_from_sticker(row) if row is not None else None
            return [chunk] if chunk is not None else []
        if source_type == "knowledge":
            from core.database import KnowledgeChunk, KnowledgeDocument

            document = db.query(KnowledgeDocument).filter(
                KnowledgeDocument.id == int(source_id),
                KnowledgeDocument.status == "active",
            ).first()
            if document is None or not is_recallable_knowledge_document(document):
                return []
            query = db.query(KnowledgeChunk).filter(
                KnowledgeChunk.document_id == int(source_id),
                KnowledgeChunk.status == "active",
            )
            if job.source_sub_id:
                query = query.filter(KnowledgeChunk.chunk_id == job.source_sub_id)
            rows = query.order_by(
                KnowledgeChunk.order_index.asc(),
                KnowledgeChunk.id.asc(),
            ).all()
            return [
                chunk_from_knowledge_chunk(row, document=document)
                for row in rows
                if is_recallable_knowledge_chunk(row)
            ]
        return []

    return load


def process_semantic_index_job(
    db: Session,
    job: SemanticIndexJob,
    *,
    chunk_loader: ChunkLoader,
    embedding_provider=None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> SemanticIndexJob | None:
    ensure_semantic_schema(db.bind)
    lease = semantic_job_lease(job)
    try:
        # delete 只做索引清除，不读取业务正文，也不调用 embedding provider。
        chunks = [] if str(job.job_type or "") == "delete" else chunk_loader(job)
        embeddings, embedding_error = _embedding_bytes_by_sub_id(chunks, embedding_provider)
        renewed = heartbeat_job(
            db,
            job_id=lease.job_id,
            lease_token=lease.lease_token,
            lease_seconds=lease_seconds,
        )
        if renewed is None:
            return None
        job_meta = _job_meta(job)
        delete_source_ids = job_meta.get("delete_source_ids")
        if not isinstance(delete_source_ids, list):
            delete_source_ids = []
        delete_item_ids = job_meta.get("delete_item_ids")
        if not isinstance(delete_item_ids, list):
            delete_item_ids = []
        if (
            not delete_item_ids
            and str(job.job_type or "") == "delete"
            and str(job_meta.get("backfill_category") or "") == "orphan"
        ):
            legacy_document_ids = job_meta.get("document_ids")
            if isinstance(legacy_document_ids, list):
                delete_item_ids = legacy_document_ids
        terminal_status = "done_with_warning" if embedding_error else "done"
        rows = reconcile_semantic_source(
            db,
            source_type=str(job.source_type or ""),
            source_id=str(job.source_id or ""),
            source_revision=renewed.source_revision,
            index_version=job.index_version,
            expected_chunks=[] if job.job_type == "delete" else chunks,
            delete_source_ids=delete_source_ids,
            lease=renewed,
            delete_item_ids=delete_item_ids,
            embeddings=embeddings,
            embedding_enabled=embedding_provider is not None,
            status=terminal_status,
            error=embedding_error,
            ensure_schema=False,
        )

        if embedding_error:
            for row in rows:
                row.embedding_status = "failed"
        db.commit()
        db.expire_all()
        return db.get(SemanticIndexJob, renewed.job_id)
    except SemanticJobLeaseLost:
        db.rollback()
        return None
    except ValueError as exc:
        db.rollback()
        return fail_job(
            db,
            job_id=lease.job_id,
            lease_token=lease.lease_token,
            error=_safe_worker_error(
                exc,
                prefix="semantic_index_permanent_error",
            ),
            retryable=False,
        )
    except Exception as exc:
        db.rollback()
        return fail_job(
            db,
            job_id=lease.job_id,
            lease_token=lease.lease_token,
            error=_safe_worker_error(
                exc,
                prefix="semantic_index_worker_error",
            ),
            retryable=True,
        )


def run_once(
    *,
    db: Session | None = None,
    worker_id: str = "semantic-index-worker",
    chunk_loader: ChunkLoader | None = None,
    embedding_provider=None,
    recover_timeout_seconds: int = 900,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> bool:
    if not get_rag_runtime_config().semantic_index_enabled:
        return False
    close_db = db is None
    if db is None:
        db = SessionLocal()
    try:
        recover_timed_out_jobs(db, timeout_seconds=recover_timeout_seconds)
        job = claim_next_job(
            db,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )
        if job is None:
            return False
        process_semantic_index_job(
            db,
            job,
            chunk_loader=chunk_loader or _default_chunk_loader(db),
            embedding_provider=embedding_provider if embedding_provider is not None else get_embedding_provider(),
            lease_seconds=lease_seconds,
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

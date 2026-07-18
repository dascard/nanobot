"""语义索引写入工具。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import SemanticIndexItem, SemanticIndexJob
from core.semantic.adapters import SemanticChunk
from core.semantic.jobs import (
    SemanticJobLease,
    assert_semantic_job_lease,
    semantic_job_meta,
    semantic_job_origin,
    settle_semantic_job,
)
from core.semantic.schema import ensure_semantic_schema
from core.time_utils import db_now_naive


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
        "visibility": chunk.visibility,
        "quality_score": float(chunk.quality_score or 0.0),
        "trust_level": chunk.trust_level,
        "source_prior": float(chunk.source_prior or 0.0),
    })


def source_revision_for_chunks(
    chunks: Sequence[SemanticChunk],
    *,
    document_ids: Sequence[int],
) -> str:
    """按完整逻辑源内容生成稳定 revision。"""

    return stable_hash({
        "v": 2,
        "document_ids": sorted({int(item) for item in document_ids}),
        "chunks": sorted(source_hash_for_chunk(chunk) for chunk in chunks),
    })


def upsert_semantic_chunks(
    db: Session,
    chunks: list[SemanticChunk],
    *,
    index_version: str,
    embedding_model: str = "",
    embeddings: dict[str, bytes] | None = None,
    embedding_enabled: bool | None = None,
    source_revision: str = "",
    commit: bool = True,
    ensure_schema: bool = True,
) -> list[SemanticIndexItem]:
    if ensure_schema:
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
        row.source_revision = str(
            source_revision
            or chunk.metadata.get("source_revision")
            or source_hash_for_chunk(chunk)
        )
        row.quality_score = float(chunk.quality_score or 0.0)
        row.trust_level = chunk.trust_level
        row.source_prior = float(chunk.source_prior or 0.0)
        row.meta_json = json.dumps(chunk.metadata or {}, ensure_ascii=False, sort_keys=True)
        row.deleted_at = None
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

    if commit:
        db.commit()
    else:
        db.flush()
    return rows


def soft_delete_existing_source_rows(
    db: Session,
    *,
    source_type: str,
    source_ids: Sequence[str],
    index_version: str | None,
    now: datetime | None = None,
) -> int:
    normalized_ids = sorted({str(item or "").strip() for item in source_ids if str(item or "").strip()})
    if not normalized_ids:
        return 0
    query = (
        db.query(SemanticIndexItem)
        .filter(SemanticIndexItem.source_type == str(source_type or ""))
        .filter(SemanticIndexItem.source_id.in_(normalized_ids))
    )
    if index_version is not None:
        query = query.filter(SemanticIndexItem.index_version == str(index_version or ""))
    rows = query.all()
    deleted_at = now or db_now_naive()
    for row in rows:
        row.status = "deleted"
        row.deleted_at = deleted_at
        row.updated_at = deleted_at
        db.execute(
            text("DELETE FROM semantic_index_fts WHERE rowid = :rowid"),
            {"rowid": int(row.id or 0)},
        )
    db.flush()
    return len(rows)


def soft_delete_exact_source_items(
    db: Session,
    *,
    source_type: str,
    source_id: str,
    item_ids: Sequence[int],
    now: datetime | None = None,
) -> int:
    """按扫描快照中的精确条目 ID 清理孤儿索引。"""

    normalized_ids: set[int] = set()
    for item_id in item_ids:
        if isinstance(item_id, bool) or not isinstance(item_id, (int, str)):
            raise ValueError("semantic_delete_item_id_invalid")
        normalized_text = str(item_id).strip()
        if not normalized_text.isdigit():
            raise ValueError("semantic_delete_item_id_invalid")
        normalized_id = int(normalized_text)
        if normalized_id <= 0:
            raise ValueError("semantic_delete_item_id_invalid")
        normalized_ids.add(normalized_id)
    if not normalized_ids:
        return 0

    rows = (
        db.query(SemanticIndexItem)
        .filter(
            SemanticIndexItem.id.in_(sorted(normalized_ids)),
            SemanticIndexItem.source_type == str(source_type or ""),
            SemanticIndexItem.source_id == str(source_id or ""),
            SemanticIndexItem.status == "active",
        )
        .all()
    )
    deleted_at = now or db_now_naive()
    for row in rows:
        row.status = "deleted"
        row.deleted_at = deleted_at
        row.updated_at = deleted_at
        db.execute(
            text("DELETE FROM semantic_index_fts WHERE rowid = :rowid"),
            {"rowid": int(row.id or 0)},
        )
    db.flush()
    return len(rows)


def reconcile_semantic_source(
    db: Session,
    *,
    source_type: str,
    source_id: str,
    source_revision: str,
    index_version: str,
    expected_chunks: list[SemanticChunk],
    delete_source_ids: Sequence[str],
    lease: SemanticJobLease,
    delete_item_ids: Sequence[int] = (),
    embeddings: dict[str, bytes] | None = None,
    embedding_model: str = "",
    embedding_enabled: bool | None = None,
    status: str = "done",
    error: str = "",
    ensure_schema: bool = True,
) -> list[SemanticIndexItem]:
    """在同一事务中替换逻辑源、更新 FTS 并结算任务。"""

    if ensure_schema:
        ensure_semantic_schema(db.bind)
    job = assert_semantic_job_lease(db, lease)
    normalized_source_type = str(source_type or "").strip()
    normalized_source_id = str(source_id or "").strip()
    normalized_revision = str(source_revision or "").strip()
    job_meta = semantic_job_meta(job)
    job_origin = semantic_job_origin(job)
    exact_delete_item_ids = tuple(delete_item_ids)
    orphan_exact_delete = (
        str(job.job_type or "") == "delete"
        and job_origin == "backfill"
        and str(job_meta.get("backfill_category") or "") == "orphan"
        and bool(exact_delete_item_ids)
        and not expected_chunks
    )
    empty_source_orphan_delete = not normalized_source_id and orphan_exact_delete
    if (
        not normalized_source_type
        or not normalized_revision
        or (not normalized_source_id and not empty_source_orphan_delete)
        or (exact_delete_item_ids and not orphan_exact_delete)
    ):
        raise ValueError("semantic_reconcile_identity_incomplete")
    if (
        str(job.source_type or "") != normalized_source_type
        or str(job.source_id or "") != normalized_source_id
        or str(job.source_revision or "") != normalized_revision
        or str(job.index_version or "") != str(index_version or "")
    ):
        raise ValueError("semantic_reconcile_job_identity_mismatch")
    sibling_jobs = (
        db.query(SemanticIndexJob)
        .filter(
            SemanticIndexJob.source_type == normalized_source_type,
            SemanticIndexJob.source_id == normalized_source_id,
            SemanticIndexJob.id != int(job.id or 0),
        )
        .order_by(SemanticIndexJob.id.desc())
        .all()
    )
    latest_business_job = next(
        (item for item in sibling_jobs if semantic_job_origin(item) == "business"),
        None,
    )
    superseded = False
    if job_origin == "backfill":
        observed_head = semantic_job_meta(job).get("observed_business_head")
        if isinstance(observed_head, dict):
            try:
                observed_identity = (
                    int(observed_head.get("job_id") or 0),
                    str(observed_head.get("source_revision") or ""),
                    str(observed_head.get("job_type") or ""),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("semantic_backfill_business_head_invalid") from exc
            if observed_identity[0] < 0:
                raise ValueError("semantic_backfill_business_head_invalid")
            latest_identity = (
                int(getattr(latest_business_job, "id", 0) or 0),
                str(getattr(latest_business_job, "source_revision", "") or ""),
                str(getattr(latest_business_job, "job_type", "") or ""),
            )
            superseded = latest_identity != observed_identity
        else:
            # 部署前遗留 backfill job 没有扫描时 business head，只能保守拦截。
            superseded = bool(
                latest_business_job is not None
                and str(latest_business_job.source_revision or "") != normalized_revision
            )
    else:
        superseded = any(
            int(item.id or 0) > int(job.id or 0)
            and semantic_job_origin(item) == "business"
            and str(item.source_revision or "") != normalized_revision
            for item in sibling_jobs
        )
    if superseded:
        settle_semantic_job(
            db,
            lease,
            status="superseded",
            commit=False,
        )
        return []
    seen_sub_ids: set[str] = set()
    for chunk in expected_chunks:
        if (
            chunk.source_type != normalized_source_type
            or str(chunk.source_id) != normalized_source_id
        ):
            raise ValueError("semantic_reconcile_chunk_identity_mismatch")
        chunk_revision = str(chunk.metadata.get("source_revision") or "").strip()
        if chunk_revision and chunk_revision != normalized_revision:
            raise ValueError("semantic_reconcile_chunk_revision_mismatch")
        if chunk.source_sub_id in seen_sub_ids:
            raise ValueError("semantic_reconcile_duplicate_source_sub_id")
        seen_sub_ids.add(chunk.source_sub_id)

    soft_delete_existing_source_rows(
        db,
        source_type=normalized_source_type,
        source_ids=(normalized_source_id, *tuple(delete_source_ids)),
        index_version=None,
    )
    if exact_delete_item_ids:
        soft_delete_exact_source_items(
            db,
            source_type=normalized_source_type,
            source_id=normalized_source_id,
            item_ids=exact_delete_item_ids,
        )
    rows = upsert_semantic_chunks(
        db,
        expected_chunks,
        index_version=str(index_version or ""),
        embedding_model=embedding_model,
        embeddings=embeddings,
        embedding_enabled=embedding_enabled,
        source_revision=normalized_revision,
        commit=False,
        ensure_schema=False,
    )
    settle_semantic_job(
        db,
        lease,
        status=status,
        error=error,
        commit=False,
    )
    return rows

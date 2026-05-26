"""语义索引任务状态机。"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from core.database import SemanticIndexJob
from core.semantic.schema import ensure_semantic_schema


def enqueue_index_job(
    db: Session,
    *,
    source_type: str,
    source_id: str,
    source_sub_id: str = "",
    job_type: str = "upsert",
    index_version: str = "",
    max_retry: int = 3,
) -> SemanticIndexJob:
    ensure_semantic_schema(db.bind)
    job = SemanticIndexJob(
        source_type=source_type,
        source_id=str(source_id),
        source_sub_id=source_sub_id,
        job_type=job_type,
        index_version=index_version,
        status="pending",
        max_retry=max_retry,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def claim_next_job(db: Session, *, worker_id: str) -> SemanticIndexJob | None:
    ensure_semantic_schema(db.bind)
    now = datetime.now()
    candidate = (
        db.query(SemanticIndexJob)
        .with_entities(SemanticIndexJob.id)
        .filter(SemanticIndexJob.status == "pending")
        .filter(
            (SemanticIndexJob.next_retry_at.is_(None))
            | (SemanticIndexJob.next_retry_at <= now)
        )
        .order_by(SemanticIndexJob.id.asc())
        .first()
    )
    if candidate is None:
        return None
    job_id = int(candidate[0])
    result = db.execute(
        update(SemanticIndexJob)
        .where(SemanticIndexJob.id == job_id)
        .where(SemanticIndexJob.status == "pending")
        .values(
            status="running",
            locked_by=worker_id,
            locked_at=now,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    job = db.query(SemanticIndexJob).filter(SemanticIndexJob.id == job_id).one()
    db.refresh(job)
    return job


def recover_timed_out_jobs(db: Session, *, timeout_seconds: int) -> int:
    ensure_semantic_schema(db.bind)
    cutoff = datetime.now() - timedelta(seconds=int(timeout_seconds))
    rows = (
        db.query(SemanticIndexJob)
        .filter(SemanticIndexJob.status == "running")
        .filter(SemanticIndexJob.locked_at.is_not(None))
        .filter(SemanticIndexJob.locked_at < cutoff)
        .all()
    )
    for row in rows:
        row.status = "pending"
        row.locked_by = ""
        row.locked_at = None
        row.updated_at = datetime.now()
    db.commit()
    return len(rows)


def finish_job(
    db: Session,
    job: SemanticIndexJob,
    *,
    status: str,
    error: str = "",
) -> SemanticIndexJob:
    job.status = status
    job.error = error or ""
    job.locked_by = ""
    job.locked_at = None
    job.updated_at = datetime.now()
    job.finished_at = datetime.now()
    db.commit()
    db.refresh(job)
    return job


def fail_job(
    db: Session,
    job: SemanticIndexJob,
    *,
    error: str,
    retry: bool = True,
) -> SemanticIndexJob:
    job.retry_count = int(job.retry_count or 0) + 1
    job.error = error
    job.locked_by = ""
    job.locked_at = None
    job.updated_at = datetime.now()
    if retry and job.retry_count < int(job.max_retry or 0):
        job.status = "failed"
        job.next_retry_at = datetime.now() + timedelta(seconds=min(300, 2 ** job.retry_count))
    else:
        job.status = "failed"
        job.finished_at = datetime.now()
    db.commit()
    db.refresh(job)
    return job

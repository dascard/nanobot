"""语义索引任务状态机。"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, update
from sqlalchemy.orm import Session

from core.database import SemanticIndexJob
from core.fencing import new_fencing_token
from core.semantic.schema import ensure_semantic_schema
from core.time_utils import db_now_naive, to_db_naive


DEFAULT_LEASE_SECONDS = 900
TERMINAL_SUCCESS_STATUSES = frozenset({"done", "done_with_warning", "superseded"})
SEMANTIC_JOB_ORIGINS = frozenset({"business", "backfill"})


class SemanticJobLeaseLost(RuntimeError):
    """任务已被其他 worker 重领，当前事务不得提交任何派生写入。"""


class SemanticIndexJobNotFound(ValueError):
    """管理动作指定的语义索引任务不存在。"""


class SemanticIndexJobRetryConflict(ValueError):
    """任务状态、版本或租约不允许管理端重试。"""


@dataclass(frozen=True, slots=True)
class SemanticJobLease:
    """跨事务传递的不可变语义索引任务租约。"""

    job_id: int
    worker_id: str
    lease_token: str = field(repr=False)
    lease_expires_at: datetime
    source_revision: str
    attempt_count: int


def semantic_job_lease(job: SemanticIndexJob) -> SemanticJobLease:
    token = str(job.lease_token or "")
    expires_at = job.lease_expires_at
    worker_id = str(job.locked_by or "")
    if job.status != "running" or not token or expires_at is None or not worker_id:
        raise ValueError("semantic index job has no active lease")
    return SemanticJobLease(
        job_id=int(job.id or 0),
        worker_id=worker_id,
        lease_token=token,
        lease_expires_at=expires_at,
        source_revision=str(job.source_revision or ""),
        attempt_count=int(job.attempt_count or 0),
    )


def semantic_job_meta(job: SemanticIndexJob) -> dict[str, Any]:
    """解析不可信任务元数据；失败时返回空字典。"""

    try:
        value = json.loads(job.meta_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def semantic_job_origin(job: SemanticIndexJob) -> str:
    """返回任务来源；旧 backfill_category 兼容识别为回填。"""

    meta = semantic_job_meta(job)
    if str(meta.get("backfill_category") or "").strip():
        return "backfill"
    origin = str(meta.get("job_origin") or "").strip().lower()
    if origin in SEMANTIC_JOB_ORIGINS:
        return origin
    return "business"


def _legacy_source_revision(
    *,
    source_type: str,
    source_id: str,
    source_sub_id: str,
    job_type: str,
) -> str:
    payload = "|".join((source_type, source_id, source_sub_id, job_type))
    return "legacy_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def enqueue_index_job(
    db: Session,
    *,
    source_type: str,
    source_id: str,
    source_sub_id: str = "",
    job_type: str = "upsert",
    index_version: str = "",
    source_revision: str = "",
    meta: dict[str, Any] | None = None,
    max_retry: int = 3,
    commit: bool = True,
) -> SemanticIndexJob:
    # 事务型 producer 已处于业务 unit-of-work，不能在其中通过 Engine
    # 另开 schema 事务；StaticPool/SQLite 会提交同一底层连接并破坏 savepoint。
    if commit:
        ensure_semantic_schema(db.bind)
    now = db_now_naive()
    job = SemanticIndexJob(
        source_type=source_type,
        source_id=str(source_id),
        source_sub_id=source_sub_id,
        job_type=job_type,
        index_version=index_version,
        source_revision=(
            str(source_revision or "").strip()
            or _legacy_source_revision(
                source_type=str(source_type or ""),
                source_id=str(source_id or ""),
                source_sub_id=str(source_sub_id or ""),
                job_type=str(job_type or ""),
            )
        ),
        status="pending",
        max_retry=max_retry,
        meta_json=json.dumps(meta or {}, ensure_ascii=False, sort_keys=True),
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    if commit:
        db.commit()
        db.refresh(job)
    else:
        db.flush()
    return job


def _lease_seconds(value: int | None) -> int:
    return max(1, int(value or DEFAULT_LEASE_SECONDS))


def _automatic_retry_available(job: SemanticIndexJob) -> bool:
    """人工重试只授权一次 claim；自动预算只由自动重排消耗。"""

    return (
        int(job.manual_retry_count or 0) == 0
        and int(job.retry_count or 0) < max(0, int(job.max_retry or 0))
    )


def _active_lease_predicate(
    *,
    lease: SemanticJobLease,
    now: datetime,
):
    return and_(
        SemanticIndexJob.id == int(lease.job_id),
        SemanticIndexJob.status == "running",
        SemanticIndexJob.locked_by == lease.worker_id,
        SemanticIndexJob.lease_token == lease.lease_token,
        SemanticIndexJob.source_revision == lease.source_revision,
        SemanticIndexJob.attempt_count == lease.attempt_count,
        SemanticIndexJob.lease_expires_at.is_not(None),
        SemanticIndexJob.lease_expires_at > now,
    )


def _finish_cas(
    db: Session,
    *,
    job_id: int,
    affected: int,
    commit: bool,
) -> SemanticIndexJob | None:
    if affected != 1:
        if commit:
            db.rollback()
        return None
    if commit:
        db.commit()
    else:
        db.flush()
    db.expire_all()
    return db.get(SemanticIndexJob, int(job_id))


def claim_next_job(
    db: Session,
    *,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> SemanticIndexJob | None:
    ensure_semantic_schema(db.bind)
    claimed_at = to_db_naive(now) or db_now_naive()
    normalized_worker = str(worker_id or "").strip()
    if not normalized_worker or len(normalized_worker) > 128:
        raise ValueError("worker_id 必须是 1-128 字符")
    candidate = (
        db.query(SemanticIndexJob)
        .with_entities(
            SemanticIndexJob.id,
            SemanticIndexJob.source_type,
            SemanticIndexJob.source_id,
            SemanticIndexJob.source_sub_id,
            SemanticIndexJob.job_type,
            SemanticIndexJob.source_revision,
        )
        .filter(SemanticIndexJob.status == "pending")
        .filter(
            or_(
                SemanticIndexJob.next_retry_at.is_(None),
                SemanticIndexJob.next_retry_at <= claimed_at,
            )
        )
        .order_by(SemanticIndexJob.id.asc())
        .first()
    )
    if candidate is None:
        return None
    job_id = int(candidate[0])
    lease_token = new_fencing_token()
    claimed_revision = str(candidate[5] or "").strip() or _legacy_source_revision(
        source_type=str(candidate[1] or ""),
        source_id=str(candidate[2] or ""),
        source_sub_id=str(candidate[3] or ""),
        job_type=str(candidate[4] or ""),
    )
    result = db.execute(
        update(SemanticIndexJob)
        .where(SemanticIndexJob.id == job_id)
        .where(SemanticIndexJob.status == "pending")
        .where(
            or_(
                SemanticIndexJob.next_retry_at.is_(None),
                SemanticIndexJob.next_retry_at <= claimed_at,
            )
        )
        .values(
            status="running",
            locked_by=normalized_worker,
            locked_at=claimed_at,
            lease_token=lease_token,
            source_revision=claimed_revision,
            lease_expires_at=(
                claimed_at + timedelta(seconds=_lease_seconds(lease_seconds))
            ),
            attempt_count=func.coalesce(SemanticIndexJob.attempt_count, 0) + 1,
            next_retry_at=None,
            finished_at=None,
            updated_at=claimed_at,
        )
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    db.expire_all()
    return db.get(SemanticIndexJob, job_id)


def heartbeat_job(
    db: Session,
    *,
    lease: SemanticJobLease,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
    commit: bool = True,
) -> SemanticJobLease | None:
    heartbeat_at = to_db_naive(now) or db_now_naive()
    expires_at = heartbeat_at + timedelta(seconds=_lease_seconds(lease_seconds))
    result = db.execute(
        update(SemanticIndexJob)
        .where(_active_lease_predicate(
            lease=lease,
            now=heartbeat_at,
        ))
        .values(
            locked_at=heartbeat_at,
            lease_expires_at=expires_at,
            updated_at=heartbeat_at,
        )
    )
    row = _finish_cas(
        db,
        job_id=lease.job_id,
        affected=int(result.rowcount or 0),
        commit=commit,
    )
    return semantic_job_lease(row) if row is not None else None


def assert_semantic_job_lease(
    db: Session,
    lease: SemanticJobLease,
    *,
    now: datetime | None = None,
) -> SemanticIndexJob:
    checked_at = to_db_naive(now) or db_now_naive()
    row = (
        db.query(SemanticIndexJob)
        .filter(_active_lease_predicate(
            lease=lease,
            now=checked_at,
        ))
        .one_or_none()
    )
    if row is None:
        raise SemanticJobLeaseLost("semantic_index_job_lease_lost")
    return row


def recover_timed_out_jobs(
    db: Session,
    *,
    timeout_seconds: int,
    now: datetime | None = None,
    limit: int | None = None,
) -> int:
    ensure_semantic_schema(db.bind)
    recovered_at = to_db_naive(now) or db_now_naive()
    cutoff = recovered_at - timedelta(seconds=max(1, int(timeout_seconds)))
    expired = or_(
        SemanticIndexJob.lease_expires_at <= recovered_at,
        and_(
            SemanticIndexJob.lease_expires_at.is_(None),
            or_(
                SemanticIndexJob.locked_at.is_(None),
                SemanticIndexJob.locked_at <= cutoff,
            ),
        ),
    )
    query = (
        db.query(SemanticIndexJob)
        .filter(SemanticIndexJob.status == "running")
        .filter(expired)
        .order_by(SemanticIndexJob.id.asc())
    )
    if limit is not None:
        query = query.limit(max(1, int(limit)))
    candidates = query.all()
    recovered = 0
    for candidate in candidates:
        retryable = _automatic_retry_available(candidate)
        next_retry_count = (
            int(candidate.retry_count or 0) + 1
            if retryable
            else int(candidate.retry_count or 0)
        )
        result = db.execute(
            update(SemanticIndexJob)
            .where(SemanticIndexJob.id == int(candidate.id or 0))
            .where(SemanticIndexJob.status == "running")
            .where(SemanticIndexJob.lease_token == str(candidate.lease_token or ""))
            .where(SemanticIndexJob.locked_by == str(candidate.locked_by or ""))
            .where(
                SemanticIndexJob.attempt_count
                == int(candidate.attempt_count or 0)
            )
            .where(
                SemanticIndexJob.source_revision
                == str(candidate.source_revision or "")
            )
            .where(expired)
            .values(
                status="pending" if retryable else "failed",
                retry_count=next_retry_count,
                next_retry_at=recovered_at if retryable else None,
                locked_by="",
                locked_at=None,
                lease_token="",
                lease_expires_at=None,
                error=(
                    "lease_expired_recovered"
                    if retryable
                    else "lease_expired_exhausted"
                ),
                finished_at=None if retryable else recovered_at,
                updated_at=recovered_at,
            )
        )
        recovered += int(result.rowcount or 0)
    db.commit()
    return recovered


def finish_job(
    db: Session,
    *,
    lease: SemanticJobLease,
    status: str,
    error: str = "",
    now: datetime | None = None,
    commit: bool = True,
) -> SemanticIndexJob | None:
    if status not in TERMINAL_SUCCESS_STATUSES:
        raise ValueError(f"unsupported semantic success status: {status}")
    finished_at = to_db_naive(now) or db_now_naive()
    result = db.execute(
        update(SemanticIndexJob)
        .where(_active_lease_predicate(
            lease=lease,
            now=finished_at,
        ))
        .values(
            status=status,
            error=str(error or "")[:4000],
            next_retry_at=None,
            locked_by="",
            locked_at=None,
            lease_token="",
            lease_expires_at=None,
            updated_at=finished_at,
            finished_at=finished_at,
        )
    )
    return _finish_cas(
        db,
        job_id=lease.job_id,
        affected=int(result.rowcount or 0),
        commit=commit,
    )


def settle_semantic_job(
    db: Session,
    lease: SemanticJobLease,
    *,
    status: str = "done",
    error: str = "",
    now: datetime | None = None,
    commit: bool = False,
) -> SemanticIndexJob:
    row = finish_job(
        db,
        lease=lease,
        status=status,
        error=error,
        now=now,
        commit=commit,
    )
    if row is None:
        raise SemanticJobLeaseLost("semantic_index_job_lease_lost")
    return row


def fail_job(
    db: Session,
    *,
    lease: SemanticJobLease,
    error: str,
    retryable: bool = True,
    now: datetime | None = None,
    commit: bool = True,
) -> SemanticIndexJob | None:
    failed_at = to_db_naive(now) or db_now_naive()
    row = (
        db.query(SemanticIndexJob)
        .filter(_active_lease_predicate(
            lease=lease,
            now=failed_at,
        ))
        .one_or_none()
    )
    if row is None:
        if commit:
            db.rollback()
        return None
    will_retry = bool(retryable and _automatic_retry_available(row))
    next_retry_count = (
        int(row.retry_count or 0) + 1
        if will_retry
        else int(row.retry_count or 0)
    )
    retry_delay = min(300, 2 ** min(next_retry_count, 8))
    result = db.execute(
        update(SemanticIndexJob)
        .where(_active_lease_predicate(
            lease=lease,
            now=failed_at,
        ))
        .values(
            status="pending" if will_retry else "failed",
            retry_count=next_retry_count,
            next_retry_at=(
                failed_at + timedelta(seconds=retry_delay)
                if will_retry
                else None
            ),
            locked_by="",
            locked_at=None,
            lease_token="",
            lease_expires_at=None,
            error=str(error or "semantic_index_job_failed")[:4000],
            updated_at=failed_at,
            finished_at=None if will_retry else failed_at,
        )
    )
    return _finish_cas(
        db,
        job_id=lease.job_id,
        affected=int(result.rowcount or 0),
        commit=commit,
    )


def retry_semantic_index_job(
    db: Session,
    *,
    job_id: int,
    expected_status: str,
    expected_updated_at: datetime,
    reason: str,
    now: datetime | None = None,
    commit: bool = False,
) -> SemanticIndexJob:
    normalized_status = str(expected_status or "").strip()
    normalized_reason = str(reason or "").strip()
    if normalized_status not in {"failed", "running"}:
        raise ValueError("expected_status must be failed or running")
    if not normalized_reason or len(normalized_reason) > 500:
        raise ValueError("reason must be 1-500 characters")
    expected_at = to_db_naive(expected_updated_at)
    if expected_at is None:
        raise ValueError("expected_updated_at is required")
    retried_at = to_db_naive(now) or db_now_naive()
    row = db.get(SemanticIndexJob, int(job_id))
    if row is None:
        raise SemanticIndexJobNotFound(f"semantic index job not found: {job_id}")
    if row.status != normalized_status or row.updated_at != expected_at:
        raise SemanticIndexJobRetryConflict("semantic index job state changed")
    if normalized_status == "running" and (
        row.lease_expires_at is not None
        and row.lease_expires_at > retried_at
    ):
        raise SemanticIndexJobRetryConflict("semantic index job lease is still active")

    result = db.execute(
        update(SemanticIndexJob)
        .where(SemanticIndexJob.id == int(job_id))
        .where(SemanticIndexJob.status == normalized_status)
        .where(SemanticIndexJob.updated_at == expected_at)
        .where(
            or_(
                normalized_status == "failed",
                SemanticIndexJob.lease_expires_at.is_(None),
                SemanticIndexJob.lease_expires_at <= retried_at,
            )
        )
        .values(
            status="pending",
            manual_retry_count=(
                func.coalesce(SemanticIndexJob.manual_retry_count, 0) + 1
            ),
            next_retry_at=retried_at,
            locked_by="",
            locked_at=None,
            lease_token="",
            lease_expires_at=None,
            error="",
            finished_at=None,
            updated_at=retried_at,
        )
    )
    updated = _finish_cas(
        db,
        job_id=job_id,
        affected=int(result.rowcount or 0),
        commit=commit,
    )
    if updated is None:
        raise SemanticIndexJobRetryConflict("semantic index job state changed")
    return updated

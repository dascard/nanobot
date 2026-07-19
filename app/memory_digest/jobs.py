"""MemoryDigest 生成作业的幂等 claim、租约和结算。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Sequence
from uuid import uuid4

from sqlalchemy import and_, func, or_, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core.database import ChatLog, MemoryDigestJob
from core.time_utils import db_now_naive, to_db_naive


DEFAULT_LEASE_SECONDS = 1800
ClaimDecision = Literal["claimed", "in_progress", "done", "skipped", "failed"]


class MemoryDigestJobLeaseLost(RuntimeError):
    """作业租约已过期或被其他执行者接管。"""


@dataclass(frozen=True, slots=True)
class MemoryDigestSourceSnapshot:
    user_id: str
    session_id: str
    digest_date: str
    source_start_log_id: int
    source_end_log_id: int
    source_log_count: int
    source_revision: str


@dataclass(frozen=True, slots=True)
class MemoryDigestJobClaim:
    decision: ClaimDecision
    job_id: int
    lease_token: str = ""
    source_revision: str = ""
    retryable: bool = False
    error_type: str = ""
    checkpoint: dict = field(default_factory=dict)


def _safe_job_meta(raw: object) -> dict:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _stable_datetime(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    return str(value or "")


def memory_digest_source_snapshot(
    *,
    session_id: str,
    digest_date: str,
    logs: Sequence[ChatLog],
) -> MemoryDigestSourceSnapshot:
    """对完整来源生成不含正文的稳定 revision。"""

    rows = list(logs)
    revision_rows = []
    for row in rows:
        content = str(getattr(row, "content", "") or "")
        meta_json = str(getattr(row, "meta_json", "") or "")
        revision_rows.append({
            "id": int(getattr(row, "id", 0) or 0),
            "user_id": str(getattr(row, "user_id", "") or ""),
            "session_id": str(getattr(row, "session_id", "") or ""),
            "sender_name": str(getattr(row, "sender_name", "") or ""),
            "role": str(getattr(row, "role", "") or ""),
            "created_at": _stable_datetime(getattr(row, "created_at", None)),
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "meta_sha256": hashlib.sha256(meta_json.encode("utf-8")).hexdigest(),
        })
    revision = hashlib.sha256(
        json.dumps(
            {
                "session_id": str(session_id or ""),
                "digest_date": str(digest_date or ""),
                "rows": revision_rows,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    ids = [int(getattr(row, "id", 0) or 0) for row in rows]
    user_id = str(getattr(rows[0], "user_id", "") or "") if rows else ""
    return MemoryDigestSourceSnapshot(
        user_id=user_id,
        session_id=str(session_id or ""),
        digest_date=str(digest_date or ""),
        source_start_log_id=ids[0] if ids else 0,
        source_end_log_id=ids[-1] if ids else 0,
        source_log_count=len(rows),
        source_revision=revision,
    )


def _job_has_retry_budget(job: MemoryDigestJob) -> bool:
    return int(job.retry_count or 0) < max(0, int(job.max_retry or 0))


def memory_digest_job_retryable(job: MemoryDigestJob) -> bool:
    """失败类型允许重试且仍有预算时才向调用方暴露 retryable。"""

    return job.next_retry_at is not None and _job_has_retry_budget(job)


def recover_expired_memory_digest_jobs(
    db: Session,
    *,
    now: datetime | None = None,
) -> int:
    """回收所有已过租约的 running 作业，并保留批次检查点供显式重试。"""

    recovered_at = to_db_naive(now) or db_now_naive()
    candidates = (
        db.query(MemoryDigestJob)
        .filter(
            MemoryDigestJob.status == "running",
            or_(
                MemoryDigestJob.lease_expires_at.is_(None),
                MemoryDigestJob.lease_expires_at <= recovered_at,
            ),
        )
        .all()
    )
    recovered = 0
    try:
        for job in candidates:
            next_retry_at = recovered_at if _job_has_retry_budget(job) else None
            result = db.execute(
                update(MemoryDigestJob)
                .where(
                    MemoryDigestJob.id == int(job.id),
                    MemoryDigestJob.status == "running",
                    or_(
                        MemoryDigestJob.lease_expires_at.is_(None),
                        MemoryDigestJob.lease_expires_at <= recovered_at,
                    ),
                )
                .values(
                    status="failed",
                    locked_by="",
                    lease_token="",
                    lease_expires_at=None,
                    next_retry_at=next_retry_at,
                    error_type="lease_expired_recovered",
                    error_summary="memory_digest_failed:lease_expired_recovered",
                    finished_at=recovered_at,
                    updated_at=recovered_at,
                )
            )
            recovered += int(result.rowcount or 0)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return recovered


def _insert_job_if_missing(
    db: Session,
    snapshot: MemoryDigestSourceSnapshot,
    *,
    now: datetime,
) -> None:
    values = {
        "session_id": snapshot.session_id,
        "digest_date": snapshot.digest_date,
        "user_id": snapshot.user_id,
        "source_start_log_id": snapshot.source_start_log_id,
        "source_end_log_id": snapshot.source_end_log_id,
        "source_log_count": snapshot.source_log_count,
        "source_revision": snapshot.source_revision,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
    }
    dialect = getattr(getattr(db, "bind", None), "dialect", None)
    dialect_name = str(getattr(dialect, "name", ""))
    if dialect_name == "sqlite":
        db.execute(
            sqlite_insert(MemoryDigestJob)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["session_id", "digest_date"],
            )
        )
        return
    existing = (
        db.query(MemoryDigestJob.id)
        .filter(
            MemoryDigestJob.session_id == snapshot.session_id,
            MemoryDigestJob.digest_date == snapshot.digest_date,
        )
        .first()
    )
    if existing is None:
        db.add(MemoryDigestJob(**values))
        db.flush()


def claim_memory_digest_job(
    db: Session,
    snapshot: MemoryDigestSourceSnapshot,
    *,
    force: bool = False,
    retry_failed: bool = False,
    worker_id: str = "memory-digest-inline",
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> MemoryDigestJobClaim:
    """原子 claim 同一 `(session,date)` 作业。"""

    claimed_at = to_db_naive(now) or db_now_naive()
    job = (
        db.query(MemoryDigestJob)
        .filter(
            MemoryDigestJob.session_id == snapshot.session_id,
            MemoryDigestJob.digest_date == snapshot.digest_date,
        )
        .one_or_none()
    )
    if retry_failed and job is None:
        db.rollback()
        return MemoryDigestJobClaim(
            decision="failed",
            job_id=0,
            retryable=False,
            error_type="retry_job_not_found",
        )
    if job is None:
        _insert_job_if_missing(db, snapshot, now=claimed_at)
        job = (
            db.query(MemoryDigestJob)
            .filter(
                MemoryDigestJob.session_id == snapshot.session_id,
                MemoryDigestJob.digest_date == snapshot.digest_date,
            )
            .one()
        )
    job_id = int(job.id)
    previous_status = str(job.status or "pending")
    previous_source_revision = str(job.source_revision or "")
    previous_meta = _safe_job_meta(job.meta_json)
    resume_checkpoint = (
        previous_meta.get("batch_checkpoint")
        if previous_source_revision == snapshot.source_revision
        and previous_status in {"failed", "running"}
        and isinstance(previous_meta.get("batch_checkpoint"), dict)
        else {}
    )
    retry_budget_available = _job_has_retry_budget(job)
    failed_retryable = memory_digest_job_retryable(job)
    active_lease = (
        previous_status == "running"
        and job.lease_expires_at is not None
        and job.lease_expires_at > claimed_at
    )
    if active_lease:
        db.rollback()
        return MemoryDigestJobClaim(
            decision="in_progress",
            job_id=job_id,
            source_revision=str(job.source_revision or ""),
        )
    if previous_status == "running" and not retry_budget_available and not force:
        result = db.execute(
            update(MemoryDigestJob)
            .where(
                MemoryDigestJob.id == job_id,
                MemoryDigestJob.status == "running",
                or_(
                    MemoryDigestJob.lease_expires_at.is_(None),
                    MemoryDigestJob.lease_expires_at <= claimed_at,
                ),
            )
            .values(
                status="failed",
                locked_by="",
                lease_token="",
                lease_expires_at=None,
                next_retry_at=None,
                result_digest_count=0,
                result_source_id="",
                result_root_digest_id=None,
                result_semantic_job_id=None,
                error_type="lease_expired_exhausted",
                error_summary="memory_digest_failed:lease_expired_exhausted",
                meta_json="{}",
                finished_at=claimed_at,
                updated_at=claimed_at,
            )
        )
        if int(result.rowcount or 0) != 1:
            db.rollback()
            return MemoryDigestJobClaim(
                decision="in_progress",
                job_id=job_id,
                source_revision=str(job.source_revision or ""),
            )
        db.commit()
        return MemoryDigestJobClaim(
            decision="failed",
            job_id=job_id,
            retryable=False,
            error_type="lease_expired_exhausted",
        )
    if retry_failed and previous_status not in {"failed", "running"}:
        db.rollback()
        return MemoryDigestJobClaim(
            decision="failed",
            job_id=job_id,
            retryable=False,
            error_type="retry_status_conflict",
        )
    if previous_status == "done" and not force:
        db.rollback()
        return MemoryDigestJobClaim(decision="done", job_id=job_id)
    if (
        previous_status == "skipped"
        and str(job.source_revision or "") == snapshot.source_revision
        and not force
    ):
        db.rollback()
        return MemoryDigestJobClaim(decision="skipped", job_id=job_id)
    if previous_status == "failed":
        explicit_retry = bool(force or retry_failed)
        failed_retry_allowed = bool(force or failed_retryable)
        if not explicit_retry or not failed_retry_allowed:
            db.rollback()
            return MemoryDigestJobClaim(
                decision="failed",
                job_id=job_id,
                retryable=failed_retryable,
                error_type=str(job.error_type or ""),
            )

    lease_token = uuid4().hex
    lease_expires_at = claimed_at + timedelta(
        seconds=max(30, int(lease_seconds or DEFAULT_LEASE_SECONDS))
    )
    predicate = [
        MemoryDigestJob.id == job_id,
        MemoryDigestJob.status == previous_status,
    ]
    if previous_status == "running":
        predicate.append(or_(
            MemoryDigestJob.lease_expires_at.is_(None),
            MemoryDigestJob.lease_expires_at <= claimed_at,
        ))
    result = db.execute(
        update(MemoryDigestJob)
        .where(and_(*predicate))
        .values(
            user_id=snapshot.user_id,
            source_start_log_id=snapshot.source_start_log_id,
            source_end_log_id=snapshot.source_end_log_id,
            source_log_count=snapshot.source_log_count,
            source_revision=snapshot.source_revision,
            status="running",
            locked_by=str(worker_id or "memory-digest-inline")[:128],
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
            attempt_count=func.coalesce(MemoryDigestJob.attempt_count, 0) + 1,
            retry_count=(
                func.coalesce(MemoryDigestJob.retry_count, 0) + 1
                if previous_status in {"failed", "running"}
                else func.coalesce(MemoryDigestJob.retry_count, 0)
            ),
            next_retry_at=None,
            result_digest_count=0,
            result_source_id="",
            result_root_digest_id=None,
            result_semantic_job_id=None,
            error_type="",
            error_summary="",
            meta_json=json.dumps(
                {"batch_checkpoint": resume_checkpoint}
                if resume_checkpoint
                else {},
                ensure_ascii=False,
                sort_keys=True,
            ),
            finished_at=None,
            updated_at=claimed_at,
        )
    )
    if int(result.rowcount or 0) != 1:
        db.rollback()
        return MemoryDigestJobClaim(decision="in_progress", job_id=job_id)
    db.commit()
    return MemoryDigestJobClaim(
        decision="claimed",
        job_id=job_id,
        lease_token=lease_token,
        source_revision=snapshot.source_revision,
        checkpoint=dict(resume_checkpoint),
    )


def _active_lease_predicate(
    *,
    job_id: int,
    lease_token: str,
    now: datetime,
):
    return and_(
        MemoryDigestJob.id == int(job_id),
        MemoryDigestJob.status == "running",
        MemoryDigestJob.lease_token == str(lease_token or ""),
        MemoryDigestJob.lease_expires_at.is_not(None),
        MemoryDigestJob.lease_expires_at > now,
    )


def heartbeat_memory_digest_job(
    db: Session,
    claim: MemoryDigestJobClaim,
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> datetime:
    """使用当前 fencing token 续租；旧 token 或过期租约均不得复活。"""

    heartbeat_at = to_db_naive(now) or db_now_naive()
    lease_expires_at = heartbeat_at + timedelta(
        seconds=max(30, int(lease_seconds or DEFAULT_LEASE_SECONDS))
    )
    try:
        result = db.execute(
            update(MemoryDigestJob)
            .where(_active_lease_predicate(
                job_id=claim.job_id,
                lease_token=claim.lease_token,
                now=heartbeat_at,
            ))
            .where(MemoryDigestJob.source_revision == claim.source_revision)
            .values(
                lease_expires_at=lease_expires_at,
                updated_at=heartbeat_at,
            )
        )
        if int(result.rowcount or 0) != 1:
            raise MemoryDigestJobLeaseLost("memory_digest_job_lease_lost")
        db.commit()
    except MemoryDigestJobLeaseLost:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise MemoryDigestJobLeaseLost(
            "memory_digest_job_heartbeat_failed"
        ) from exc
    return lease_expires_at


def save_memory_digest_batch_checkpoint(
    db: Session,
    claim: MemoryDigestJobClaim,
    checkpoint: dict,
    *,
    now: datetime | None = None,
) -> None:
    """在当前 fencing token 下持久化已审计完成的批次结果。"""

    saved_at = to_db_naive(now) or db_now_naive()
    row = (
        db.query(MemoryDigestJob)
        .filter(_active_lease_predicate(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            now=saved_at,
        ))
        .filter(MemoryDigestJob.source_revision == claim.source_revision)
        .one_or_none()
    )
    if row is None:
        db.rollback()
        raise MemoryDigestJobLeaseLost("memory_digest_job_lease_lost")
    meta = _safe_job_meta(row.meta_json)
    meta["batch_checkpoint"] = dict(checkpoint or {})
    try:
        result = db.execute(
            update(MemoryDigestJob)
            .where(_active_lease_predicate(
                job_id=claim.job_id,
                lease_token=claim.lease_token,
                now=saved_at,
            ))
            .where(MemoryDigestJob.source_revision == claim.source_revision)
            .values(
                meta_json=json.dumps(
                    meta,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                updated_at=saved_at,
            )
        )
        if int(result.rowcount or 0) != 1:
            raise MemoryDigestJobLeaseLost("memory_digest_job_lease_lost")
        db.commit()
    except MemoryDigestJobLeaseLost:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise MemoryDigestJobLeaseLost(
            "memory_digest_job_checkpoint_failed"
        ) from exc


def finish_memory_digest_job(
    db: Session,
    claim: MemoryDigestJobClaim,
    *,
    result_digest_count: int,
    result_source_id: str,
    result_root_digest_id: int,
    result_semantic_job_id: int,
    meta: dict | None = None,
    now: datetime | None = None,
) -> None:
    """在摘要与索引写事务内 CAS 完成作业，不自行提交。"""

    finished_at = to_db_naive(now) or db_now_naive()
    result = db.execute(
        update(MemoryDigestJob)
        .where(_active_lease_predicate(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            now=finished_at,
        ))
        .where(MemoryDigestJob.source_revision == claim.source_revision)
        .values(
            status="done",
            locked_by="",
            lease_token="",
            lease_expires_at=None,
            next_retry_at=None,
            result_digest_count=max(0, int(result_digest_count)),
            result_source_id=str(result_source_id or "")[:128],
            result_root_digest_id=int(result_root_digest_id),
            result_semantic_job_id=int(result_semantic_job_id),
            error_type="",
            error_summary="",
            meta_json=json.dumps(meta or {}, ensure_ascii=False, sort_keys=True),
            finished_at=finished_at,
            updated_at=finished_at,
        )
    )
    if int(result.rowcount or 0) != 1:
        raise MemoryDigestJobLeaseLost("memory_digest_job_lease_lost")


def settle_memory_digest_job_without_rows(
    db: Session,
    claim: MemoryDigestJobClaim,
    *,
    status: Literal["skipped", "failed"],
    error_type: str = "",
    error_summary: str = "",
    retryable: bool = False,
    meta: dict | None = None,
    now: datetime | None = None,
) -> MemoryDigestJob | None:
    """结算未写摘要的 skipped/failed 作业。"""

    finished_at = to_db_naive(now) or db_now_naive()
    current = (
        db.query(MemoryDigestJob)
        .filter(_active_lease_predicate(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            now=finished_at,
        ))
        .filter(MemoryDigestJob.source_revision == claim.source_revision)
        .one_or_none()
    )
    if current is None:
        db.rollback()
        return None
    settled_meta = {
        **_safe_job_meta(current.meta_json),
        **dict(meta or {}),
    }
    result = db.execute(
        update(MemoryDigestJob)
        .where(_active_lease_predicate(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            now=finished_at,
        ))
        .where(MemoryDigestJob.source_revision == claim.source_revision)
        .values(
            status=status,
            locked_by="",
            lease_token="",
            lease_expires_at=None,
            next_retry_at=finished_at if status == "failed" and retryable else None,
            result_digest_count=0,
            result_source_id="",
            result_root_digest_id=None,
            result_semantic_job_id=None,
            error_type=str(error_type or "")[:64],
            error_summary=str(error_summary or "")[:500],
            meta_json=json.dumps(
                settled_meta,
                ensure_ascii=False,
                sort_keys=True,
            ),
            finished_at=finished_at,
            updated_at=finished_at,
        )
    )
    if int(result.rowcount or 0) != 1:
        db.rollback()
        return None
    db.commit()
    db.expire_all()
    return db.get(MemoryDigestJob, int(claim.job_id))

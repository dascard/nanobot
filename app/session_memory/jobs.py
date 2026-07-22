"""Session summary LLM job 队列服务。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from collections.abc import Sequence
from typing import Literal

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.session_memory import config
from core.db.models.chat import ConversationTurn
from core.db.models.session_memory import RollingSessionSummary, SessionSummaryJob
from core.time_utils import db_now_naive, to_db_naive


ACTIVE_JOB_STATUSES = frozenset({"pending", "running", "done"})


class SessionSummaryJobRetryConflict(ValueError):
    """只有 failed job 允许由管理端显式重试。"""


@dataclass(frozen=True, slots=True)
class FinalizePermit:
    decision: Literal["promote", "obsolete", "lost_lease"]
    blocking_summary_id: int | None
    blocking_coverage: int
    proposed_coverage: int
    reason: str


def _turn_ids(turns: Sequence[ConversationTurn]) -> list[int]:
    return [int(turn.id) for turn in turns]


def _stable_job_hash(
    *,
    session_id: str,
    covered_from_turn_id: int,
    covered_until_turn_id: int,
    source_turn_ids: list[int],
) -> str:
    return hashlib.sha256(
        json.dumps({
            "session_id": session_id,
            "covered_from_turn_id": covered_from_turn_id,
            "covered_until_turn_id": covered_until_turn_id,
            "source_turn_ids": source_turn_ids,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def enqueue_session_summary_job(
    db: Session,
    *,
    session_id: str,
    user_id: str,
    chat_type: str,
    pending_turns: Sequence[ConversationTurn],
    previous_summary: RollingSessionSummary | None,
    fallback_summary: RollingSessionSummary,
    max_retry: int | None = None,
    force: bool = False,
    recent_raw_turn_ids: Sequence[int] | None = None,
    current_user_input: str = "",
) -> tuple[SessionSummaryJob, bool]:
    """为 fallback summary 覆盖范围创建 LLM 摘要任务。

    同一 session + turn 覆盖范围只保留一个 pending/running/done job。
    failed job 通过 retry 接口恢复，不在 enqueue 时隐式覆盖。
    """
    if not pending_turns:
        raise ValueError("pending_turns is required")
    source_turn_ids = _turn_ids(pending_turns)
    covered_from = source_turn_ids[0]
    covered_until = source_turn_ids[-1]
    stable_hash = _stable_job_hash(
        session_id=session_id,
        covered_from_turn_id=covered_from,
        covered_until_turn_id=covered_until,
        source_turn_ids=source_turn_ids,
    )

    dedupe_statuses = ("pending", "running") if force else tuple(ACTIVE_JOB_STATUSES)
    existing = (
        db.query(SessionSummaryJob)
        .filter(
            SessionSummaryJob.session_id == session_id,
            SessionSummaryJob.covered_from_turn_id == covered_from,
            SessionSummaryJob.covered_until_turn_id == covered_until,
            SessionSummaryJob.status.in_(dedupe_statuses),
        )
        .order_by(SessionSummaryJob.id.desc())
        .first()
    )
    if existing is not None:
        return existing, False

    now = db_now_naive()
    job = SessionSummaryJob(
        session_id=session_id,
        user_id=user_id or "",
        chat_type=chat_type or "private",
        covered_from_turn_id=covered_from,
        covered_until_turn_id=covered_until,
        source_turn_ids_json=json.dumps(source_turn_ids, ensure_ascii=False),
        previous_summary_id=int(previous_summary.id) if previous_summary and previous_summary.id else None,
        fallback_summary_id=int(fallback_summary.id) if fallback_summary and fallback_summary.id else None,
        status="pending",
        retry_count=0,
        max_retry=int(max_retry if max_retry is not None else config.SESSION_SUMMARY_MAX_RETRY),
        stable_hash=stable_hash,
        meta_json=json.dumps({
            "schema_version": 1,
            "created_by": "rolling_summary_fallback",
            "fallback_summary_kind": getattr(fallback_summary, "summary_kind", "") or "deterministic_fallback",
            "recent_raw_turn_ids": list(dict.fromkeys(
                int(item) for item in (recent_raw_turn_ids or [])
            )),
            "current_user_input": str(current_user_input or "").replace("\x00", "")[:2000],
        }, ensure_ascii=False),
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.flush()
    return job, True


def retry_session_summary_job(db: Session, job_id: int) -> SessionSummaryJob:
    job = db.get(SessionSummaryJob, int(job_id))
    if job is None:
        raise ValueError(f"session summary job not found: {job_id}")
    if job.status != "failed":
        raise SessionSummaryJobRetryConflict(
            f"session summary job is not retryable: {job.status}"
        )
    job.status = "pending"
    job.error = ""
    job.next_retry_at = None
    job.locked_by = ""
    job.locked_at = None
    job.updated_at = db_now_naive()
    db.flush()
    return job


def fetch_pending_summary_jobs(
    db: Session,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> list[SessionSummaryJob]:
    now = to_db_naive(now) or db_now_naive()
    return (
        db.query(SessionSummaryJob)
        .filter(
            SessionSummaryJob.status == "pending",
            or_(
                SessionSummaryJob.next_retry_at.is_(None),
                SessionSummaryJob.next_retry_at <= now,
            ),
        )
        .order_by(SessionSummaryJob.id.asc())
        .limit(max(1, int(limit or config.SESSION_SUMMARY_JOB_BATCH_SIZE)))
        .all()
    )


def mark_summary_job_running(
    db: Session,
    job: SessionSummaryJob,
    *,
    owner: str,
) -> SessionSummaryJob:
    job.status = "running"
    job.locked_by = owner or "session-summary-worker"
    now = db_now_naive()
    job.locked_at = now
    job.error = ""
    job.updated_at = now
    db.flush()
    return job


def claim_summary_job(
    db: Session,
    job_id: int,
    *,
    owner: str,
    now: datetime | None = None,
) -> SessionSummaryJob | None:
    """原子抢占 pending job。

    多 worker 并发时，只有第一个满足 `status='pending'` 的 UPDATE 会成功。
    """
    now = to_db_naive(now) or db_now_naive()
    affected = (
        db.query(SessionSummaryJob)
        .filter(
            SessionSummaryJob.id == int(job_id),
            SessionSummaryJob.status == "pending",
            or_(
                SessionSummaryJob.next_retry_at.is_(None),
                SessionSummaryJob.next_retry_at <= now,
            ),
        )
        .update({
            "status": "running",
            "locked_by": owner or "session-summary-worker",
            "locked_at": now,
            "error": "",
            "updated_at": now,
        }, synchronize_session=False)
    )
    db.flush()
    if not affected:
        return None
    db.expire_all()
    return db.get(SessionSummaryJob, int(job_id))


def renew_summary_job_lease(
    db: Session,
    job_id: int,
    *,
    owner: str,
    now: datetime | None = None,
) -> bool:
    """仅允许当前 running owner 刷新 locked_at。"""

    normalized_owner = str(owner or "").strip()
    if not normalized_owner or len(normalized_owner) > 128:
        raise ValueError("owner 必须是 1-128 字符")
    renewed_at = to_db_naive(now) or db_now_naive()
    affected = (
        db.query(SessionSummaryJob)
        .filter(
            SessionSummaryJob.id == int(job_id),
            SessionSummaryJob.status == "running",
            SessionSummaryJob.locked_by == normalized_owner,
        )
        .update(
            {
                "locked_at": renewed_at,
                "updated_at": renewed_at,
            },
            synchronize_session=False,
        )
    )
    db.flush()
    return affected == 1


def acquire_summary_finalize_permit(
    db: Session,
    job_id: int,
    *,
    owner: str,
    now: datetime | None = None,
) -> FinalizePermit:
    """续租当前 owner 后，按 active coverage 决定是否允许晋升。"""

    proposed_job = db.get(SessionSummaryJob, int(job_id))
    proposed_coverage = int(
        getattr(proposed_job, "covered_until_turn_id", 0) or 0
    )
    if proposed_job is None or not renew_summary_job_lease(
        db,
        int(job_id),
        owner=owner,
        now=now,
    ):
        return FinalizePermit(
            decision="lost_lease",
            blocking_summary_id=None,
            blocking_coverage=0,
            proposed_coverage=proposed_coverage,
            reason="running_owner_lost",
        )

    db.expire_all()
    job = db.get(SessionSummaryJob, int(job_id))
    if job is None:
        return FinalizePermit(
            decision="lost_lease",
            blocking_summary_id=None,
            blocking_coverage=0,
            proposed_coverage=proposed_coverage,
            reason="running_owner_lost",
        )
    proposed_coverage = int(job.covered_until_turn_id or 0)
    fallback_id = int(job.fallback_summary_id or 0)
    active_rows = (
        db.query(RollingSessionSummary)
        .filter(
            RollingSessionSummary.session_id == job.session_id,
            RollingSessionSummary.status == "active",
        )
        .order_by(
            RollingSessionSummary.covered_until_turn_id.desc(),
            RollingSessionSummary.id.desc(),
        )
        .all()
    )
    blocking = next(
        (
            row
            for row in active_rows
            if int(row.covered_until_turn_id or 0) > proposed_coverage
            or (
                int(row.covered_until_turn_id or 0) == proposed_coverage
                and int(row.id or 0) != fallback_id
            )
        ),
        None,
    )
    if blocking is not None:
        blocking_coverage = int(blocking.covered_until_turn_id or 0)
        return FinalizePermit(
            decision="obsolete",
            blocking_summary_id=int(blocking.id or 0),
            blocking_coverage=blocking_coverage,
            proposed_coverage=proposed_coverage,
            reason=(
                "higher_active_coverage"
                if blocking_coverage > proposed_coverage
                else "equal_active_coverage"
            ),
        )
    return FinalizePermit(
        decision="promote",
        blocking_summary_id=None,
        blocking_coverage=0,
        proposed_coverage=proposed_coverage,
        reason=(
            "replace_job_fallback"
            if any(int(row.id or 0) == fallback_id for row in active_rows)
            else "newer_coverage"
        ),
    )


def mark_summary_job_obsolete(
    db: Session,
    job: SessionSummaryJob,
    *,
    permit: FinalizePermit,
) -> SessionSummaryJob:
    try:
        meta = json.loads(job.meta_json or "{}")
        if not isinstance(meta, dict):
            meta = {}
    except (TypeError, json.JSONDecodeError):
        meta = {}
    meta["obsolete"] = {
        "blocking_summary_id": permit.blocking_summary_id,
        "blocking_coverage": permit.blocking_coverage,
        "proposed_coverage": permit.proposed_coverage,
        "reason": permit.reason,
    }
    job.status = "obsolete"
    job.result_summary_id = None
    job.error = ""
    job.next_retry_at = None
    job.locked_by = ""
    job.locked_at = None
    job.meta_json = json.dumps(meta, ensure_ascii=False)
    job.updated_at = db_now_naive()
    db.flush()
    return job


def obsolete_summary_jobs_for_scope(
    db: Session,
    *,
    session_id: str = "",
    user_id: str = "",
    reason: str,
) -> int:
    """终止归档或历史清除范围内仍可能执行的摘要任务。"""

    normalized_session_id = str(session_id or "").strip()
    normalized_user_id = str(user_id or "").strip()
    if bool(normalized_session_id) == bool(normalized_user_id):
        raise ValueError("session_id 与 user_id 必须且只能提供一个")
    normalized_reason = str(reason or "").strip()
    if not normalized_reason or len(normalized_reason) > 128:
        raise ValueError("reason 必须是 1-128 字符")

    query = db.query(SessionSummaryJob).filter(
        SessionSummaryJob.status.in_(("pending", "running", "failed")),
    )
    if normalized_session_id:
        query = query.filter(SessionSummaryJob.session_id == normalized_session_id)
    else:
        query = query.filter(SessionSummaryJob.user_id == normalized_user_id)

    rows = query.order_by(SessionSummaryJob.id.asc()).all()
    for job in rows:
        proposed_coverage = int(job.covered_until_turn_id or 0)
        mark_summary_job_obsolete(
            db,
            job,
            permit=FinalizePermit(
                decision="obsolete",
                blocking_summary_id=None,
                blocking_coverage=0,
                proposed_coverage=proposed_coverage,
                reason=normalized_reason,
            ),
        )
    return len(rows)


def recover_stale_running_jobs(
    db: Session,
    *,
    now: datetime | None = None,
    timeout_sec: int | None = None,
    limit: int | None = None,
) -> int:
    """回收长时间卡在 running 的 job。

    worker 崩溃或进程被杀时，running job 不会被 `fetch_pending_summary_jobs`
    再次取到。这里把超时 job 重新放回 pending；超过重试次数则标 failed。
    """
    now = to_db_naive(now) or db_now_naive()
    timeout = int(timeout_sec if timeout_sec is not None else config.SESSION_SUMMARY_RUNNING_TIMEOUT_SEC)
    cutoff = now - timedelta(seconds=max(1, timeout))
    query = (
        db.query(SessionSummaryJob)
        .filter(
            SessionSummaryJob.status == "running",
            or_(
                SessionSummaryJob.locked_at.is_(None),
                SessionSummaryJob.locked_at <= cutoff,
            ),
        )
        .order_by(SessionSummaryJob.id.asc())
    )
    if limit is not None:
        query = query.limit(max(1, int(limit)))
    rows = query.all()
    for job in rows:
        job.retry_count = int(job.retry_count or 0) + 1
        max_retry = max(0, int(job.max_retry or config.SESSION_SUMMARY_MAX_RETRY))
        job.locked_by = ""
        job.locked_at = None
        job.updated_at = now
        if job.retry_count >= max_retry:
            job.status = "failed"
            job.error = "running_timeout"
            job.next_retry_at = None
        else:
            job.status = "pending"
            job.error = "running_timeout_recovered"
            job.next_retry_at = now
    db.flush()
    return len(rows)


def mark_summary_job_done(
    db: Session,
    job: SessionSummaryJob,
    *,
    result_summary_id: int,
) -> SessionSummaryJob:
    job.status = "done"
    job.result_summary_id = int(result_summary_id or 0)
    job.error = ""
    job.next_retry_at = None
    job.locked_by = ""
    job.locked_at = None
    job.updated_at = db_now_naive()
    db.flush()
    return job


def mark_summary_job_failed(
    db: Session,
    job: SessionSummaryJob,
    *,
    error: str,
    retry_delay_sec: int | None = None,
    retryable: bool = True,
) -> SessionSummaryJob:
    job.retry_count = int(job.retry_count or 0) + 1
    job.error = str(error or "summary_job_failed")[:4000]
    job.locked_by = ""
    job.locked_at = None
    now = db_now_naive()
    job.updated_at = now
    max_retry = max(0, int(job.max_retry or config.SESSION_SUMMARY_MAX_RETRY))
    if retryable and job.retry_count < max_retry:
        job.status = "pending"
        delay = int(retry_delay_sec if retry_delay_sec is not None else config.SESSION_SUMMARY_RETRY_DELAY_SEC)
        job.next_retry_at = now + timedelta(seconds=max(1, delay))
    else:
        job.status = "failed"
        job.next_retry_at = None
    db.flush()
    return job

"""Session summary LLM job 队列服务。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Sequence

from sqlalchemy.orm import Session

from app.session_memory import config
from core.database import ConversationTurn, RollingSessionSummary, SessionSummaryJob


ACTIVE_JOB_STATUSES = frozenset({"pending", "running", "done"})


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

    existing = (
        db.query(SessionSummaryJob)
        .filter(
            SessionSummaryJob.session_id == session_id,
            SessionSummaryJob.covered_from_turn_id == covered_from,
            SessionSummaryJob.covered_until_turn_id == covered_until,
            SessionSummaryJob.status.in_(tuple(ACTIVE_JOB_STATUSES)),
        )
        .order_by(SessionSummaryJob.id.desc())
        .first()
    )
    if existing is not None:
        return existing, False

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
        }, ensure_ascii=False),
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db.add(job)
    db.flush()
    return job, True


def retry_session_summary_job(db: Session, job_id: int) -> SessionSummaryJob:
    job = db.get(SessionSummaryJob, int(job_id))
    if job is None:
        raise ValueError(f"session summary job not found: {job_id}")
    if job.status != "failed":
        return job
    job.status = "pending"
    job.error = ""
    job.next_retry_at = None
    job.locked_by = ""
    job.locked_at = None
    job.updated_at = datetime.now()
    db.flush()
    return job

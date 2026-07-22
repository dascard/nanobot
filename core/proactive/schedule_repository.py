"""主动外呼排期、评估错误与研究候选写入适配器。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from core.db.models.proactive import ProactiveOutreachLog
from core.model_provider.response_normalization import strip_think_blocks
from core.proactive.identity_policy import post_clear_pending_key as _post_clear_pending_key
from core.proactive.lease import fence_evaluation_write as _fence_evaluation_write
from core.proactive.repository import (
    cancelled_row_is_reusable as _cancelled_row_is_reusable,
    current_pending_schedule as _current_pending_schedule,
    history_clear_at as _history_clear_at,
)
from core.proactive.serialization import grounding_json as _grounding_json


def upsert_pending_schedule(
    session: Session,
    *,
    user_id: str,
    idempotency_key: str,
    grounding: dict[str, Any],
    judge_reason: str,
    next_check_at: datetime | None,
    next_intent: str,
    created_at: datetime | None = None,
    evaluation_owner_token: str | None = None,
) -> ProactiveOutreachLog | None:
    if not _fence_evaluation_write(
        session,
        user_id=user_id,
        owner_token=evaluation_owner_token,
    ):
        session.rollback()
        return None
    generation_at = created_at or datetime.now()
    row = _current_pending_schedule(session, user_id)
    conflicting = (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.idempotency_key == idempotency_key)
        .first()
    )
    clear_at = _history_clear_at(session, user_id)
    if (
        conflicting is not None
        and conflicting.user_id == user_id
        and conflicting.status == "cancelled"
        and conflicting.outbound_run_id is not None
        and clear_at is not None
        and generation_at > clear_at
    ):
        idempotency_key = _post_clear_pending_key(
            user_id=user_id,
            idempotency_key=idempotency_key,
            clear_at=clear_at,
        )
        conflicting = (
            session.query(ProactiveOutreachLog)
            .filter(ProactiveOutreachLog.idempotency_key == idempotency_key)
            .first()
        )
    reset_created_at = False
    replacement_created_at: datetime | None = None
    if conflicting is not None and (
        row is None or int(conflicting.id) != int(row.id)
    ):
        reusable_cancelled = _cancelled_row_is_reusable(
            session,
            row=conflicting,
            user_id=user_id,
            generation_at=generation_at,
        )
        if reusable_cancelled:
            if row is not None:
                replacement_created_at = row.created_at
                row.status = "cancelled"
            row = conflicting
            reset_created_at = True
        elif conflicting is not None:
            conflicting_id = int(conflicting.id)
            session.rollback()
            return session.get(ProactiveOutreachLog, conflicting_id)
    elif row is None:
        row = ProactiveOutreachLog(user_id=user_id, status="pending")
        session.add(row)
        reset_created_at = True
    row.idempotency_key = idempotency_key
    row.grounding_json = _grounding_json(grounding)
    row.judge_should = False
    row.judge_reason = judge_reason
    row.next_check_at = next_check_at
    row.next_intent = next_intent
    row.message = ""
    row.forced = False
    row.status = "pending"
    if reset_created_at or row.created_at is None:
        row.created_at = replacement_created_at or created_at or datetime.now()
    session.commit()
    session.refresh(row)
    return row


def record_evaluation_error(
    session: Session,
    *,
    user_id: str,
    idempotency_key: str,
    phase: str,
    grounding: dict[str, Any],
    error_type: str,
    reason: str,
    next_check_at: datetime | None,
    next_intent: str,
    created_at: datetime,
    forced: bool,
    evaluation_owner_token: str | None,
) -> ProactiveOutreachLog | None:
    """记录失败评估，既保留审计证据，也建立最长沉默起算点。"""

    if not _fence_evaluation_write(
        session,
        user_id=user_id,
        owner_token=evaluation_owner_token,
    ):
        session.rollback()
        return None
    audit_key = f"{idempotency_key}:evaluation:{phase}"
    row = (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.idempotency_key == audit_key)
        .first()
    )
    if row is not None and row.status != "evaluation_error":
        session.rollback()
        return row
    if row is None:
        row = ProactiveOutreachLog(
            user_id=user_id,
            idempotency_key=audit_key,
            created_at=created_at,
        )
        session.add(row)
    audit_grounding = dict(grounding)
    audit_grounding["evaluation_error"] = {
        "phase": phase,
        "error_type": error_type,
        "reason": reason[:500],
    }
    row.grounding_json = _grounding_json(audit_grounding)
    row.judge_should = None
    row.judge_reason = f"{phase}:{error_type}: {reason}"[:1000]
    row.next_check_at = next_check_at
    row.next_intent = next_intent[:500]
    row.message = ""
    row.forced = forced
    row.status = "evaluation_error"
    if row.created_at is None:
        row.created_at = created_at
    session.commit()
    session.refresh(row)
    return row


def pending_schedule_conflict_result(
    session: Session,
    *,
    row: ProactiveOutreachLog,
    user_id: str,
) -> dict[str, Any]:
    status = str(row.status or "")
    if status in {
        "sending",
        "queued",
        "delivering",
        "retry_wait",
        "sent",
        "sent_after_ambiguous_replay",
        "failed",
        "blocked",
        "ambiguous",
        "legacy_ambiguous_hold",
    }:
        return {
            "status": "skipped_duplicate",
            "log_id": row.id,
            "forced": bool(row.forced),
        }
    if status == "cancelled":
        clear_at = _history_clear_at(session, user_id)
        return {
            "status": (
                "cancelled_history_clear"
                if clear_at is not None
                and (row.created_at is None or row.created_at <= clear_at)
                else "stale_schedule"
            ),
            "log_id": row.id,
            "forced": bool(row.forced),
        }
    return {
        "status": "state_error",
        "error_type": "unknown_delivery_state",
        "delivery_state": status,
        "log_id": row.id,
        "forced": bool(row.forced),
    }


def upsert_research_candidate(
    session: Session,
    *,
    user_id: str,
    idempotency_key: str,
    grounding: dict[str, Any],
    judge_reason: str,
    next_check_at: datetime | None,
    next_intent: str,
    message: str,
    created_at: datetime,
    evaluation_owner_token: str | None = None,
) -> ProactiveOutreachLog | None:
    if not _fence_evaluation_write(
        session,
        user_id=user_id,
        owner_token=evaluation_owner_token,
    ):
        session.rollback()
        return None
    existing = (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.idempotency_key == idempotency_key)
        .first()
    )
    reusable_cancelled = _cancelled_row_is_reusable(
        session,
        row=existing,
        user_id=user_id,
        generation_at=created_at,
    )
    if (
        existing is not None
        and existing.status not in {"pending", "candidate"}
        and not reusable_cancelled
    ):
        session.rollback()
        return existing
    row = existing or _current_pending_schedule(session, user_id)
    if row is None:
        row = (
            session.query(ProactiveOutreachLog)
            .filter(ProactiveOutreachLog.idempotency_key == idempotency_key)
            .filter(ProactiveOutreachLog.status == "candidate")
            .first()
        )
    if row is None:
        row = ProactiveOutreachLog(user_id=user_id)
        session.add(row)
    row.idempotency_key = idempotency_key
    row.grounding_json = _grounding_json(grounding)
    row.judge_should = True
    row.judge_reason = judge_reason
    row.next_check_at = next_check_at
    row.next_intent = next_intent
    row.message = strip_think_blocks(str(message or "")).strip()
    row.forced = False
    row.status = "candidate"
    row.created_at = created_at
    session.commit()
    session.refresh(row)
    return row



__all__ = [
    "pending_schedule_conflict_result",
    "record_evaluation_error",
    "upsert_pending_schedule",
    "upsert_research_candidate",
]

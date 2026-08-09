"""过期 Agent Run 租约的保守终结协调器。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from core.db.models.durable_task import RunTaskControl
from core.db.models.observability import AgentRun
from core.db.models.run_recovery import (
    RunRecoveryOperation,
    RunSideEffectReceipt,
)
from core.durable_tasks.contracts import RunTaskStatus
from core.run_ledger.adapters import run_terminated_event
from core.run_ledger.persistence import SqlAlchemyRunEventLedger


def _utc_naive(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is not None:
        current = current.astimezone(timezone.utc).replace(tzinfo=None)
    return current


def _expired_status(
    db: Session,
    row: RunTaskControl,
    *,
    now: datetime,
) -> tuple[RunTaskStatus, str]:
    unsafe_receipt = (
        db.query(RunSideEffectReceipt.receipt_id)
        .filter(
            RunSideEffectReceipt.run_id == str(row.run_id),
            RunSideEffectReceipt.state.in_(("prepared", "ambiguous")),
        )
        .first()
    )
    if unsafe_receipt is not None:
        return RunTaskStatus.AMBIGUOUS, "lease_expired_with_unknown_effect"
    if row.cancel_requested_at is not None:
        return RunTaskStatus.CANCELLED, "cancel_requested"
    if row.timeout_at is not None and row.timeout_at <= now:
        return RunTaskStatus.TIMED_OUT, "execution_timeout"
    return RunTaskStatus.FAILED, "lease_expired"


def reconcile_expired_run_tasks(
    db: Session,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> int:
    """CAS 终结过期 owner；不在同一 Run 上猜测重放模型或副作用。"""

    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit 必须是 1-1000")
    current = _utc_naive(now)
    candidates = (
        db.query(RunTaskControl)
        .filter(
            (
                (
                    (RunTaskControl.status == RunTaskStatus.RUNNING.value)
                    & (
                        (RunTaskControl.lease_expires_at <= current)
                        | (
                            RunTaskControl.timeout_at.is_not(None)
                            & (RunTaskControl.timeout_at <= current)
                        )
                    )
                )
                | (
                    (RunTaskControl.status == RunTaskStatus.ACCEPTED.value)
                    & (
                        RunTaskControl.cancel_requested_at.is_not(None)
                        | (
                            RunTaskControl.timeout_at.is_not(None)
                            & (RunTaskControl.timeout_at <= current)
                        )
                    )
                )
            ),
        )
        .order_by(RunTaskControl.updated_at.asc())
        .limit(limit)
        .all()
    )
    reconciled = 0
    for candidate in candidates:
        status, reason = _expired_status(db, candidate, now=current)
        owner = str(candidate.lease_owner)
        token = str(candidate.lease_token)
        generation = int(candidate.lease_generation)
        query = db.query(RunTaskControl).filter(
            RunTaskControl.run_id == str(candidate.run_id),
            RunTaskControl.status == str(candidate.status),
        )
        if str(candidate.status) == RunTaskStatus.RUNNING.value:
            query = query.filter(
                RunTaskControl.lease_owner == owner,
                RunTaskControl.lease_token == token,
                RunTaskControl.lease_generation == generation,
                (
                    (RunTaskControl.lease_expires_at <= current)
                    | (
                        RunTaskControl.timeout_at.is_not(None)
                        & (RunTaskControl.timeout_at <= current)
                    )
                ),
            )
        else:
            query = query.filter(
                (
                    RunTaskControl.cancel_requested_at.is_not(None)
                    | (
                        RunTaskControl.timeout_at.is_not(None)
                        & (RunTaskControl.timeout_at <= current)
                    )
                )
            )
        changed = (
            query
            .update(
                {
                    RunTaskControl.status: status.value,
                    RunTaskControl.lease_owner: "",
                    RunTaskControl.lease_token: "",
                    RunTaskControl.lease_expires_at: None,
                    RunTaskControl.terminal_reason: reason,
                    RunTaskControl.updated_at: current,
                    RunTaskControl.finished_at: current,
                },
                synchronize_session=False,
            )
        )
        if changed != 1:
            db.rollback()
            continue
        run = db.get(AgentRun, str(candidate.run_id))
        ledger = SqlAlchemyRunEventLedger(db)
        head = ledger.head(str(candidate.run_id))
        if head is not None and head.terminal_sequence is None and run is not None:
            ledger.append(run_terminated_event(
                run_id=str(candidate.run_id),
                trace_id=str(run.trace_id or ""),
                session_id=str(run.session_id or ""),
                status=status.value,
                output_value="",
                error_value=reason,
                latency_ms=int(run.latency_ms or 0),
                model=str(run.model or ""),
                occurred_at=current.replace(tzinfo=timezone.utc),
            ))
        if run is not None:
            run.status = status.value
            run.error = reason
            run.finished_at = current
        recovery = (
            db.query(RunRecoveryOperation)
            .filter(RunRecoveryOperation.run_id == str(candidate.run_id))
            .one_or_none()
        )
        if recovery is not None and str(recovery.status) == "running":
            recovery.status = status.value
            recovery.error_code = reason
            recovery.updated_at = current
            recovery.finished_at = current
        db.commit()
        reconciled += 1
    return reconciled


__all__ = ["reconcile_expired_run_tasks"]

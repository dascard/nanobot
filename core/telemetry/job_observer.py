"""已提交 ORM Job 状态到统一 Telemetry 的显式 Adapter。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session as OrmSession

from core.db.models.outbound import OutboundDeliveryOutbox
from core.db.models.sandbox import SandboxAdminOperation
from core.db.models.semantic import SemanticIndexJob
from core.db.models.session_memory import MemoryDigestJob, SessionSummaryJob
from core.jobs import JobCorrelation
from core.runtime.events import RuntimeEventEmitter
from core.telemetry.jobs import JobTelemetryEmitter


_SESSION_PENDING_KEY = "nanobot_pending_job_telemetry"
_TERMINAL_STATUSES = frozenset({
    "ambiguous",
    "blocked",
    "cancelled",
    "delivered",
    "done",
    "done_with_warning",
    "failed",
    "skipped",
    "succeeded",
    "superseded",
})
_RUNNING_STATUSES = frozenset({"claimed", "running"})


@dataclass(frozen=True, slots=True)
class _JobModelAdapter:
    job_type: str
    model_type: type
    id_attr: str
    attempt_attr: str
    generation_attr: str = ""
    worker_attr: str = ""
    lease_token_attr: str = ""
    lease_expiry_attr: str = ""
    retry_at_attr: str = ""
    failure_code_attr: str = ""
    request_id_attr: str = ""
    session_id_attr: str = ""
    turn_id_attr: str = ""
    run_id_attr: str = ""
    delivery_id_from_job: bool = False


_JOB_MODEL_ADAPTERS = (
    _JobModelAdapter(
        job_type="session_summary",
        model_type=SessionSummaryJob,
        id_attr="id",
        attempt_attr="attempt_count",
        generation_attr="generation",
        worker_attr="locked_by",
        lease_token_attr="lease_token",
        lease_expiry_attr="lease_expires_at",
        retry_at_attr="next_retry_at",
        session_id_attr="session_id",
        turn_id_attr="covered_until_turn_id",
    ),
    _JobModelAdapter(
        job_type="memory_digest",
        model_type=MemoryDigestJob,
        id_attr="id",
        attempt_attr="attempt_count",
        worker_attr="locked_by",
        lease_token_attr="lease_token",
        lease_expiry_attr="lease_expires_at",
        retry_at_attr="next_retry_at",
        session_id_attr="session_id",
    ),
    _JobModelAdapter(
        job_type="semantic_index",
        model_type=SemanticIndexJob,
        id_attr="id",
        attempt_attr="attempt_count",
        worker_attr="locked_by",
        lease_token_attr="lease_token",
        lease_expiry_attr="lease_expires_at",
        retry_at_attr="next_retry_at",
    ),
    _JobModelAdapter(
        job_type="sandbox_admin_operation",
        model_type=SandboxAdminOperation,
        id_attr="operation_id",
        attempt_attr="attempt_count",
        worker_attr="locked_by",
        lease_token_attr="lease_token",
        lease_expiry_attr="lease_expires_at",
        retry_at_attr="next_attempt_at",
        failure_code_attr="error_code",
        request_id_attr="request_id",
        session_id_attr="chat_stream_id",
    ),
    _JobModelAdapter(
        job_type="outbound_delivery",
        model_type=OutboundDeliveryOutbox,
        id_attr="id",
        attempt_attr="allocated_attempt_count",
        worker_attr="lease_owner",
        lease_token_attr="lease_token",
        lease_expiry_attr="lease_expires_at",
        retry_at_attr="next_attempt_at",
        failure_code_attr="last_error_type",
        run_id_attr="run_id",
        delivery_id_from_job=True,
    ),
)


@dataclass(frozen=True, slots=True)
class _PendingJobTransition:
    job_type: str
    job_id: str
    transition: str
    status: str
    generation: int
    attempt_no: int
    lease_active: bool
    retry_scheduled: bool
    failure_code: str
    correlation: JobCorrelation


def _text(value: object, *, max_chars: int = 160) -> str:
    text = str(value or "").strip()
    if (
        len(text) > max_chars
        or any(ord(character) < 32 for character in text)
    ):
        return ""
    return text


def _integer(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _adapter_for(value: object) -> _JobModelAdapter | None:
    for adapter in _JOB_MODEL_ADAPTERS:
        if isinstance(value, adapter.model_type):
            return adapter
    return None


def _attribute_changed(state: Any, name: str) -> bool:
    if not name:
        return False
    return bool(getattr(state.attrs, name).history.has_changes())


def _old_status(state: Any) -> str:
    history = state.attrs.status.history
    if history.deleted:
        return _text(history.deleted[0], max_chars=32)
    return ""


def _transition(
    *,
    is_new: bool,
    old_status: str,
    status: str,
    lease_changed: bool,
    retry_scheduled: bool,
) -> str:
    if is_new:
        return "enqueued"
    if status in _RUNNING_STATUSES and old_status not in _RUNNING_STATUSES:
        return "lease_claimed"
    if retry_scheduled:
        return "retry_scheduled"
    if status in _TERMINAL_STATUSES and old_status != status:
        return "settled"
    if lease_changed and status in _RUNNING_STATUSES:
        return "lease_renewed"
    return "state_changed"


def _failure_code(
    value: object,
    adapter: _JobModelAdapter,
    *,
    transition: str,
    status: str,
) -> str:
    configured = (
        _text(
            getattr(value, adapter.failure_code_attr, ""),
            max_chars=64,
        )
        if adapter.failure_code_attr
        else ""
    )
    if configured:
        return configured
    if transition == "retry_scheduled":
        return "job.retry_scheduled"
    if status in {"failed", "ambiguous", "blocked"}:
        return "job.failed"
    return ""


def _pending_transition(
    value: object,
    adapter: _JobModelAdapter,
    *,
    is_new: bool,
) -> _PendingJobTransition | None:
    state = inspect(value)
    status_changed = _attribute_changed(state, "status")
    lease_changed = (
        _attribute_changed(state, adapter.lease_token_attr)
        or _attribute_changed(state, adapter.lease_expiry_attr)
    )
    retry_changed = _attribute_changed(state, adapter.retry_at_attr)
    if not is_new and not (status_changed or lease_changed or retry_changed):
        return None

    job_id = _text(getattr(value, adapter.id_attr, ""))
    if not job_id:
        return None
    status = _text(getattr(value, "status", ""), max_chars=32)
    retry_at = (
        getattr(value, adapter.retry_at_attr, None)
        if adapter.retry_at_attr
        else None
    )
    retry_scheduled = (
        retry_at is not None
        and status in {"pending", "retry_wait"}
    )
    transition = _transition(
        is_new=is_new,
        old_status=_old_status(state),
        status=status,
        lease_changed=lease_changed,
        retry_scheduled=retry_scheduled,
    )
    attempt_no = _integer(getattr(value, adapter.attempt_attr, 0))
    generation = (
        _integer(getattr(value, adapter.generation_attr, 0))
        if adapter.generation_attr
        else attempt_no
    )
    lease_active = bool(
        adapter.lease_token_attr
        and adapter.lease_expiry_attr
        and _text(getattr(value, adapter.lease_token_attr, ""))
        and isinstance(
            getattr(value, adapter.lease_expiry_attr, None),
            datetime,
        )
    )
    session_id = (
        _text(getattr(value, adapter.session_id_attr, ""))
        if adapter.session_id_attr
        else ""
    )
    turn_id = (
        _text(getattr(value, adapter.turn_id_attr, ""))
        if adapter.turn_id_attr
        else ""
    )
    run_id = (
        _text(getattr(value, adapter.run_id_attr, ""))
        if adapter.run_id_attr
        else ""
    )
    return _PendingJobTransition(
        job_type=adapter.job_type,
        job_id=job_id,
        transition=transition,
        status=status or "unknown",
        generation=generation,
        attempt_no=attempt_no,
        lease_active=lease_active,
        retry_scheduled=retry_scheduled,
        failure_code=_failure_code(
            value,
            adapter,
            transition=transition,
            status=status,
        ),
        correlation=JobCorrelation(
            request_id=(
                _text(getattr(value, adapter.request_id_attr, ""))
                if adapter.request_id_attr
                else ""
            ),
            session_id=session_id,
            turn_id=turn_id,
            run_id=(
                f"outbound:{run_id}"
                if adapter.job_type == "outbound_delivery" and run_id
                else run_id
            ),
            task_id=adapter.job_type,
            delivery_id=(
                job_id if adapter.delivery_id_from_job else ""
            ),
        ),
    )


class JobTelemetryObserverHandle:
    def __init__(
        self,
        event_emitter: RuntimeEventEmitter,
    ) -> None:
        self._emitter = JobTelemetryEmitter(event_emitter)
        self._installed = True

        def after_flush(db: OrmSession, _flush_context: object) -> None:
            pending = db.info.setdefault(_SESSION_PENDING_KEY, {})
            for value in tuple(db.new) + tuple(db.dirty):
                adapter = _adapter_for(value)
                if adapter is None:
                    continue
                transition = _pending_transition(
                    value,
                    adapter,
                    is_new=value in db.new,
                )
                if transition is not None:
                    pending[(transition.job_type, transition.job_id)] = (
                        transition
                    )

        def after_commit(db: OrmSession) -> None:
            pending = tuple(
                db.info.pop(_SESSION_PENDING_KEY, {}).values()
            )
            for transition in pending:
                try:
                    self._emitter.emit_transition(
                        job_type=transition.job_type,
                        transition=transition.transition,
                        status=transition.status,
                        correlation=transition.correlation,
                        job_id=transition.job_id,
                        generation=transition.generation,
                        attempt_no=transition.attempt_no,
                        failure_code=transition.failure_code,
                        retry_scheduled=transition.retry_scheduled,
                        lease_active=transition.lease_active,
                    )
                except Exception:
                    continue

        def after_rollback(db: OrmSession) -> None:
            db.info.pop(_SESSION_PENDING_KEY, None)

        self._after_flush = after_flush
        self._after_commit = after_commit
        self._after_rollback = after_rollback
        event.listen(OrmSession, "after_flush", self._after_flush)
        event.listen(OrmSession, "after_commit", self._after_commit)
        event.listen(OrmSession, "after_rollback", self._after_rollback)
        event.listen(
            OrmSession,
            "after_soft_rollback",
            self._after_soft_rollback,
        )

    @property
    def installed(self) -> bool:
        return self._installed

    @staticmethod
    def _after_soft_rollback(
        db: OrmSession,
        _previous_transaction: object,
    ) -> None:
        db.info.pop(_SESSION_PENDING_KEY, None)

    def uninstall(self) -> None:
        if not self._installed:
            return
        event.remove(OrmSession, "after_flush", self._after_flush)
        event.remove(OrmSession, "after_commit", self._after_commit)
        event.remove(OrmSession, "after_rollback", self._after_rollback)
        event.remove(
            OrmSession,
            "after_soft_rollback",
            self._after_soft_rollback,
        )
        self._installed = False


def install_job_telemetry_observer(
    event_emitter: RuntimeEventEmitter,
) -> JobTelemetryObserverHandle:
    return JobTelemetryObserverHandle(event_emitter)


__all__ = [
    "JobTelemetryObserverHandle",
    "install_job_telemetry_observer",
]

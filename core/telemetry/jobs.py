"""Durable Job 合同到 RuntimeEvent 的无正文投影。"""

from __future__ import annotations

from core.jobs import JobCorrelation, JobLease
from core.runtime.events import (
    RuntimeEvent,
    RuntimeEventContext,
    RuntimeEventEmitter,
)


def _job_context(
    correlation: JobCorrelation,
    *,
    job_id: str,
) -> RuntimeEventContext:
    return RuntimeEventContext(
        request_id=correlation.request_id,
        session_id=correlation.session_id,
        turn_id=correlation.turn_id,
        trace_id=correlation.trace_id,
        run_id=correlation.run_id,
        task_id=correlation.task_id,
        job_id=job_id,
        tool_call_id=correlation.tool_call_id,
        delivery_id=correlation.delivery_id,
        parent_job_id=correlation.parent_job_id,
    )


class JobTelemetryEmitter:
    def __init__(self, event_emitter: RuntimeEventEmitter) -> None:
        if not isinstance(event_emitter, RuntimeEventEmitter):
            raise TypeError("event_emitter 必须是 RuntimeEventEmitter")
        self._event_emitter = event_emitter

    def emit_transition(
        self,
        *,
        job_type: str,
        transition: str,
        status: str,
        correlation: JobCorrelation,
        lease: JobLease | None = None,
        job_id: str = "",
        generation: int = 0,
        attempt_no: int = 0,
        failure_code: str = "",
        retry_scheduled: bool = False,
        lease_active: bool | None = None,
    ) -> RuntimeEvent:
        if not isinstance(correlation, JobCorrelation):
            raise TypeError("correlation 必须是 JobCorrelation")
        resolved_job_id = (
            lease.job_id if lease is not None else str(job_id or "").strip()
        )
        if not resolved_job_id:
            raise ValueError("Job Telemetry 必须声明 job_id")
        resolved_generation = (
            lease.generation if lease is not None else int(generation)
        )
        resolved_attempt = (
            lease.attempt_no if lease is not None else int(attempt_no)
        )
        attributes: dict[str, object] = {
            "job_type": str(job_type or "").strip(),
            "transition": str(transition or "").strip(),
            "status": str(status or "").strip(),
            "generation": max(0, resolved_generation),
            "attempt_no": max(0, resolved_attempt),
            "lease_active": (
                lease is not None
                if lease_active is None
                else bool(lease_active)
            ),
            "retry_scheduled": bool(retry_scheduled),
        }
        if failure_code:
            attributes["failure_code"] = str(failure_code)
        return self._event_emitter.emit(
            "job.lifecycle",
            "state_changed",
            context=_job_context(
                correlation,
                job_id=resolved_job_id,
            ),
            attributes=attributes,
        )


__all__ = ["JobTelemetryEmitter"]

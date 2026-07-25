"""Durable Job 的显式 claim → handle → fenced settle 编排。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import logging
from typing import TYPE_CHECKING

from core.jobs.contracts import (
    JobCorrelation,
    JobExecutionContext,
    JobHandler,
    JobLifecycle,
    JobLifecycleError,
    JobLease,
    JobRecord,
    JobRepositoryMode,
    JobRepositoryPort,
    JobResult,
    JobStatus,
)
from core.jobs.policies import (
    require_job_retry_policy,
    require_job_schedule_policy,
)
from core.jobs.registry import JOB_DESCRIPTOR_REGISTRY

if TYPE_CHECKING:
    from core.runtime.events import RuntimeEventEmitter
    from core.telemetry.jobs import JobTelemetryEmitter


logger = logging.getLogger(__name__)


class DurableJobKernel:
    """不发现 Handler；Composition Root 必须显式注入固定映射。"""

    def __init__(
        self,
        repository: JobRepositoryPort,
        *,
        handlers: Mapping[str, JobHandler],
        event_emitter: RuntimeEventEmitter | None = None,
    ) -> None:
        if not isinstance(repository, JobRepositoryPort):
            raise TypeError("repository 未实现 JobRepositoryPort")
        normalized_handlers = dict(handlers)
        for handler_id, handler in normalized_handlers.items():
            if not str(handler_id or "").strip():
                raise ValueError("Job handler_id 不能为空")
            if not isinstance(handler, JobHandler):
                raise TypeError(f"Job Handler 无效：{handler_id}")
        self._repository = repository
        self._handlers = normalized_handlers
        self._job_telemetry: JobTelemetryEmitter | None = None
        if event_emitter is not None:
            from core.runtime.events import RuntimeEventEmitter
            from core.telemetry.jobs import JobTelemetryEmitter

            if not isinstance(event_emitter, RuntimeEventEmitter):
                raise TypeError("event_emitter 必须是 RuntimeEventEmitter")
            self._job_telemetry = JobTelemetryEmitter(event_emitter)

    def _emit_job_transition(
        self,
        *,
        job_type: str,
        transition: str,
        status: str,
        correlation: JobCorrelation,
        lease: JobLease,
        failure_code: str = "",
        retry_scheduled: bool = False,
        lease_active: bool = True,
    ) -> None:
        telemetry = self._job_telemetry
        if telemetry is None:
            return
        try:
            telemetry.emit_transition(
                job_type=job_type,
                transition=transition,
                status=status,
                correlation=correlation,
                lease=lease,
                failure_code=failure_code,
                retry_scheduled=retry_scheduled,
                lease_active=lease_active,
            )
        except Exception:
            logger.exception(
                "Durable Job Telemetry 投递失败",
                extra={
                    "job_type": job_type,
                    "transition": transition,
                },
            )

    def run_once(
        self,
        descriptor_id: str,
        *,
        worker_id: str,
        now: datetime,
    ) -> JobRecord | None:
        descriptor = JOB_DESCRIPTOR_REGISTRY.require(descriptor_id)
        if (
            descriptor.lifecycle is not JobLifecycle.ACTIVE
            or descriptor.repository_mode is not JobRepositoryMode.KERNEL
        ):
            raise JobLifecycleError(
                f"Job 不能由 DurableJobKernel 执行：{descriptor.job_type}"
            )
        try:
            handler = self._handlers[descriptor.handler_id]
        except KeyError as exc:
            raise JobLifecycleError(
                f"Job Handler 未绑定：{descriptor.handler_id}"
            ) from exc
        schedule_policy = require_job_schedule_policy(
            descriptor.schedule_policy_id
        )
        retry_policy = require_job_retry_policy(
            descriptor.retry_policy_id
        )
        claim = self._repository.claim(
            descriptor_id=descriptor.job_type,
            worker_id=worker_id,
            schedule_policy=schedule_policy,
            now=now,
        )
        if claim is None:
            return None
        self._emit_job_transition(
            job_type=descriptor.job_type,
            transition="lease_claimed",
            status=claim.record.status.value,
            correlation=claim.record.correlation,
            lease=claim.lease,
        )
        context = JobExecutionContext(
            lease=claim.lease,
            side_effect_idempotency_key=(
                claim.record.idempotency_key
            ),
            correlation=claim.record.correlation,
        )
        result = handler.handle(claim.record, context)
        if not isinstance(result, JobResult):
            raise TypeError("Job Handler 必须返回 JobResult")
        settled = self._repository.settle(
            claim.lease,
            result,
            retry_policy=retry_policy,
            now=now,
        )
        retry_scheduled = settled.status is JobStatus.RETRY_WAIT
        self._emit_job_transition(
            job_type=descriptor.job_type,
            transition=(
                "retry_scheduled"
                if retry_scheduled
                else "settled"
            ),
            status=settled.status.value,
            correlation=settled.correlation,
            lease=claim.lease,
            failure_code=(
                settled.failure.code
                if settled.failure is not None
                else ""
            ),
            retry_scheduled=retry_scheduled,
            lease_active=False,
        )
        return settled


__all__ = ["DurableJobKernel"]

"""Durable Job Kernel 的框架无关状态、租约和 Port 合同。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from core.resilience import FailureCategory


_FAILURE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _safe_text(
    value: object,
    *,
    max_chars: int,
    required: bool = False,
) -> str:
    text = str(value or "").strip()
    text = "".join(
        character if ord(character) >= 32 else " "
        for character in text
    )[:max_chars]
    if required and not text:
        raise ValueError("字段不能为空")
    return text


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobLifecycle(StrEnum):
    ACTIVE = "active"
    RESERVED = "reserved"


class JobRepositoryMode(StrEnum):
    KERNEL = "kernel"
    PORT_ADAPTER = "port_adapter"


class JobLeaseLost(RuntimeError):
    """租约 owner、token、generation、状态或有效期不再匹配。"""


class JobLifecycleError(RuntimeError):
    """保留或 Adapter-only Job 被错误地交给通用 Kernel 执行。"""


@dataclass(frozen=True, slots=True)
class JobCorrelation:
    request_id: str = ""
    session_id: str = ""
    turn_id: str = ""
    trace_id: str = ""
    run_id: str = ""
    task_id: str = ""
    tool_call_id: str = ""
    delivery_id: str = ""
    parent_job_id: str = ""

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            object.__setattr__(
                self,
                field_name,
                _safe_text(
                    getattr(self, field_name),
                    max_chars=160,
                ),
            )


@dataclass(frozen=True, slots=True)
class JobFailure:
    code: str
    category: FailureCategory
    retryable: bool
    safe_summary: str
    cause_type: str = ""
    trace_ref: str = ""

    def __post_init__(self) -> None:
        code = _safe_text(self.code, max_chars=64, required=True)
        if _FAILURE_CODE_PATTERN.fullmatch(code) is None:
            raise ValueError("JobFailure.code 格式无效")
        summary = _safe_text(
            self.safe_summary,
            max_chars=240,
            required=True,
        )
        object.__setattr__(self, "code", code)
        object.__setattr__(
            self,
            "category",
            FailureCategory(self.category),
        )
        object.__setattr__(self, "retryable", bool(self.retryable))
        object.__setattr__(self, "safe_summary", summary)
        object.__setattr__(
            self,
            "cause_type",
            _safe_text(self.cause_type, max_chars=128),
        )
        object.__setattr__(
            self,
            "trace_ref",
            _safe_text(self.trace_ref, max_chars=128),
        )


@dataclass(frozen=True, slots=True)
class JobLease:
    job_id: str
    worker_id: str
    owner_token: str = field(repr=False)
    generation: int
    attempt_no: int
    expires_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "job_id",
            _safe_text(self.job_id, max_chars=128, required=True),
        )
        object.__setattr__(
            self,
            "worker_id",
            _safe_text(self.worker_id, max_chars=128, required=True),
        )
        object.__setattr__(
            self,
            "owner_token",
            _safe_text(
                self.owner_token,
                max_chars=128,
                required=True,
            ),
        )
        if (
            isinstance(self.generation, bool)
            or not isinstance(self.generation, int)
            or self.generation <= 0
        ):
            raise ValueError("JobLease.generation 必须为正整数")
        if (
            isinstance(self.attempt_no, bool)
            or not isinstance(self.attempt_no, int)
            or self.attempt_no <= 0
        ):
            raise ValueError("JobLease.attempt_no 必须为正整数")
        if not isinstance(self.expires_at, datetime):
            raise TypeError("JobLease.expires_at 必须是 datetime")


@dataclass(frozen=True, slots=True)
class JobRecord:
    job_id: str
    descriptor_id: str
    status: JobStatus
    generation: int
    attempt_count: int
    idempotency_key: str
    payload_ref: str
    payload_sha256: str
    correlation: JobCorrelation
    created_at: datetime
    updated_at: datetime
    next_attempt_at: datetime | None = None
    lease: JobLease | None = None
    failure: JobFailure | None = None
    result_ref: str = ""
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "job_id",
            _safe_text(self.job_id, max_chars=128, required=True),
        )
        object.__setattr__(
            self,
            "descriptor_id",
            _safe_text(
                self.descriptor_id,
                max_chars=128,
                required=True,
            ),
        )
        object.__setattr__(self, "status", JobStatus(self.status))
        if (
            isinstance(self.generation, bool)
            or not isinstance(self.generation, int)
            or self.generation < 0
        ):
            raise ValueError("JobRecord.generation 必须是非负整数")
        if (
            isinstance(self.attempt_count, bool)
            or not isinstance(self.attempt_count, int)
            or self.attempt_count < 0
        ):
            raise ValueError("JobRecord.attempt_count 必须是非负整数")
        object.__setattr__(
            self,
            "idempotency_key",
            _safe_text(
                self.idempotency_key,
                max_chars=256,
                required=True,
            ),
        )
        object.__setattr__(
            self,
            "payload_ref",
            _safe_text(self.payload_ref, max_chars=512, required=True),
        )
        payload_sha256 = _safe_text(
            self.payload_sha256,
            max_chars=64,
            required=True,
        )
        if _SHA256_PATTERN.fullmatch(payload_sha256) is None:
            raise ValueError("JobRecord.payload_sha256 必须是 SHA-256")
        object.__setattr__(self, "payload_sha256", payload_sha256)
        if not isinstance(self.correlation, JobCorrelation):
            raise TypeError("JobRecord.correlation 必须是 JobCorrelation")
        if not isinstance(self.created_at, datetime) or not isinstance(
            self.updated_at,
            datetime,
        ):
            raise TypeError("JobRecord 时间字段必须是 datetime")
        if self.status is JobStatus.RUNNING and self.lease is None:
            raise ValueError("running JobRecord 必须携带 lease")
        if self.status is not JobStatus.RUNNING and self.lease is not None:
            raise ValueError("非 running JobRecord 不能携带 lease")
        if (
            self.status is JobStatus.RETRY_WAIT
            and self.next_attempt_at is None
        ):
            raise ValueError("retry_wait JobRecord 必须有 next_attempt_at")
        if (
            self.status is not JobStatus.RETRY_WAIT
            and self.next_attempt_at is not None
        ):
            raise ValueError("只有 retry_wait JobRecord 可有 next_attempt_at")
        object.__setattr__(
            self,
            "result_ref",
            _safe_text(self.result_ref, max_chars=512),
        )

    def changed(self, **values: object) -> "JobRecord":
        return replace(self, **values)


@dataclass(frozen=True, slots=True)
class JobClaim:
    record: JobRecord
    lease: JobLease

    def __post_init__(self) -> None:
        if self.record.status is not JobStatus.RUNNING:
            raise ValueError("JobClaim.record 必须是 running")
        if self.record.lease != self.lease:
            raise ValueError("JobClaim lease 与 record 不一致")


@dataclass(frozen=True, slots=True)
class JobResult:
    outcome: JobOutcome
    result_ref: str = ""
    failure: JobFailure | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", JobOutcome(self.outcome))
        object.__setattr__(
            self,
            "result_ref",
            _safe_text(self.result_ref, max_chars=512),
        )
        if self.outcome is JobOutcome.FAILED and self.failure is None:
            raise ValueError("failed JobResult 必须包含 failure")
        if self.outcome is not JobOutcome.FAILED and self.failure is not None:
            raise ValueError("非 failed JobResult 不能包含 failure")

    @classmethod
    def succeeded(cls, *, result_ref: str = "") -> "JobResult":
        return cls(JobOutcome.SUCCEEDED, result_ref=result_ref)

    @classmethod
    def failed(cls, failure: JobFailure) -> "JobResult":
        return cls(JobOutcome.FAILED, failure=failure)

    @classmethod
    def cancelled(cls) -> "JobResult":
        return cls(JobOutcome.CANCELLED)


@dataclass(frozen=True, slots=True)
class JobExecutionContext:
    lease: JobLease
    side_effect_idempotency_key: str
    correlation: JobCorrelation

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "side_effect_idempotency_key",
            _safe_text(
                self.side_effect_idempotency_key,
                max_chars=256,
                required=True,
            ),
        )


@runtime_checkable
class JobHandler(Protocol):
    def handle(
        self,
        record: JobRecord,
        context: JobExecutionContext,
    ) -> JobResult: ...


@runtime_checkable
class JobRepositoryPort(Protocol):
    def enqueue(
        self,
        *,
        descriptor_id: str,
        idempotency_key: str,
        payload_ref: str,
        payload_sha256: str,
        correlation: JobCorrelation = JobCorrelation(),
        now: datetime,
    ) -> JobRecord: ...

    def claim(
        self,
        *,
        descriptor_id: str,
        worker_id: str,
        schedule_policy: object,
        now: datetime,
    ) -> JobClaim | None: ...

    def heartbeat(
        self,
        lease: JobLease,
        *,
        schedule_policy: object,
        now: datetime,
    ) -> JobLease: ...

    def settle(
        self,
        lease: JobLease,
        result: JobResult,
        *,
        retry_policy: object,
        now: datetime,
    ) -> JobRecord: ...

    def recover_expired(
        self,
        *,
        now: datetime,
        retry_policy_resolver: object,
    ) -> int: ...

    def get(self, job_id: str) -> JobRecord | None: ...


__all__ = [
    "JobClaim",
    "JobCorrelation",
    "JobExecutionContext",
    "JobFailure",
    "JobHandler",
    "JobLease",
    "JobLeaseLost",
    "JobLifecycle",
    "JobLifecycleError",
    "JobOutcome",
    "JobRecord",
    "JobRepositoryMode",
    "JobRepositoryPort",
    "JobResult",
    "JobStatus",
]

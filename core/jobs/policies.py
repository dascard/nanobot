"""Durable Job 的冻结调度与重试策略。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from core.registry import RegistryBuilder, RegistrySnapshot
from core.resilience import FailureCategory
from core.jobs.contracts import JobFailure


@dataclass(frozen=True, slots=True)
class JobSchedulePolicy:
    policy_id: str
    version: str
    lease_seconds: float
    execution_timeout_seconds: float
    poll_interval_seconds: float
    max_claim_batch: int

    def __post_init__(self) -> None:
        for field_name in ("policy_id", "version"):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"JobSchedulePolicy.{field_name} 不能为空")
        lease = float(self.lease_seconds)
        timeout = float(self.execution_timeout_seconds)
        poll = float(self.poll_interval_seconds)
        if (
            not math.isfinite(lease)
            or not math.isfinite(timeout)
            or timeout <= 0
            or lease <= timeout
        ):
            raise ValueError("Job lease 必须严格大于有限正执行超时")
        if not math.isfinite(poll) or poll <= 0:
            raise ValueError("Job poll interval 必须为有限正数")
        if (
            isinstance(self.max_claim_batch, bool)
            or not isinstance(self.max_claim_batch, int)
            or self.max_claim_batch <= 0
        ):
            raise ValueError("Job max_claim_batch 必须为正整数")
        object.__setattr__(self, "lease_seconds", lease)
        object.__setattr__(self, "execution_timeout_seconds", timeout)
        object.__setattr__(self, "poll_interval_seconds", poll)

    @property
    def registry_namespace(self) -> str:
        return "job_schedule_policy"

    @property
    def registry_id(self) -> str:
        return self.policy_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> dict[str, object]:
        return {
            "version": self.version,
            "lease_seconds": self.lease_seconds,
            "execution_timeout_seconds": self.execution_timeout_seconds,
            "poll_interval_seconds": self.poll_interval_seconds,
            "max_claim_batch": self.max_claim_batch,
        }


@dataclass(frozen=True, slots=True)
class JobRetryPolicy:
    policy_id: str
    version: str
    max_attempts: int
    retryable_categories: frozenset[FailureCategory]
    retryable_codes: frozenset[str]
    initial_backoff_seconds: float
    backoff_multiplier: float
    max_backoff_seconds: float

    def __post_init__(self) -> None:
        if not str(self.policy_id or "").strip():
            raise ValueError("JobRetryPolicy.policy_id 不能为空")
        if not str(self.version or "").strip():
            raise ValueError("JobRetryPolicy.version 不能为空")
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or self.max_attempts <= 0
        ):
            raise ValueError("JobRetryPolicy.max_attempts 必须为正整数")
        initial = float(self.initial_backoff_seconds)
        multiplier = float(self.backoff_multiplier)
        maximum = float(self.max_backoff_seconds)
        if (
            not math.isfinite(initial)
            or initial < 0
            or not math.isfinite(maximum)
            or maximum < initial
        ):
            raise ValueError("JobRetryPolicy backoff 范围无效")
        if not math.isfinite(multiplier) or multiplier < 1:
            raise ValueError("JobRetryPolicy multiplier 不能小于 1")
        categories = frozenset(
            FailureCategory(item)
            for item in self.retryable_categories
        )
        codes = frozenset(str(item or "").strip() for item in self.retryable_codes)
        if self.max_attempts > 1 and not categories and not codes:
            raise ValueError("多 attempt JobRetryPolicy 必须声明可重试类型")
        object.__setattr__(self, "retryable_categories", categories)
        object.__setattr__(self, "retryable_codes", codes)
        object.__setattr__(self, "initial_backoff_seconds", initial)
        object.__setattr__(self, "backoff_multiplier", multiplier)
        object.__setattr__(self, "max_backoff_seconds", maximum)

    @property
    def registry_namespace(self) -> str:
        return "job_retry_policy"

    @property
    def registry_id(self) -> str:
        return self.policy_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> dict[str, object]:
        return {
            "version": self.version,
            "max_attempts": self.max_attempts,
            "retryable_categories": sorted(
                item.value for item in self.retryable_categories
            ),
            "retryable_codes": sorted(self.retryable_codes),
            "initial_backoff_seconds": self.initial_backoff_seconds,
            "backoff_multiplier": self.backoff_multiplier,
            "max_backoff_seconds": self.max_backoff_seconds,
        }

    def allows_retry(
        self,
        failure: JobFailure,
        *,
        attempt_count: int,
    ) -> bool:
        return bool(
            failure.retryable
            and attempt_count < self.max_attempts
            and (
                failure.category in self.retryable_categories
                or failure.code in self.retryable_codes
            )
        )

    def delay_seconds(self, *, attempt_count: int) -> float:
        if attempt_count <= 0:
            raise ValueError("attempt_count 必须为正数")
        return min(
            self.max_backoff_seconds,
            self.initial_backoff_seconds
            * (self.backoff_multiplier ** (attempt_count - 1)),
        )


class _PolicyRegistry:
    def __init__(self, namespace: str, descriptors: tuple[object, ...]) -> None:
        builder = RegistryBuilder(namespace)
        for descriptor in descriptors:
            builder.register(descriptor)
        self.snapshot: RegistrySnapshot = builder.freeze()

    def require(self, policy_id: str):
        try:
            return self.snapshot.require(str(policy_id or "").strip())
        except KeyError as exc:
            raise ValueError(f"未登记的 Job Policy：{policy_id}") from exc


_SCHEDULE_REGISTRY = _PolicyRegistry(
    "job_schedule_policy",
    (
        JobSchedulePolicy(
            "background.long.v1",
            "1.0.0",
            lease_seconds=1800,
            execution_timeout_seconds=1200,
            poll_interval_seconds=1,
            max_claim_batch=1,
        ),
        JobSchedulePolicy(
            "background.standard.v1",
            "1.0.0",
            lease_seconds=180,
            execution_timeout_seconds=120,
            poll_interval_seconds=1,
            max_claim_batch=1,
        ),
        JobSchedulePolicy(
            "outbound.adapter.v1",
            "1.0.0",
            lease_seconds=180,
            execution_timeout_seconds=120,
            poll_interval_seconds=0.5,
            max_claim_batch=1,
        ),
    ),
)

_TRANSIENT = frozenset({
    FailureCategory.UNAVAILABLE,
    FailureCategory.TIMEOUT,
    FailureCategory.RATE_LIMITED,
    FailureCategory.TRANSIENT_TRANSPORT,
})


def _retry(
    policy_id: str,
    max_attempts: int,
    *,
    categories: frozenset[FailureCategory] = _TRANSIENT,
) -> JobRetryPolicy:
    return JobRetryPolicy(
        policy_id,
        "1.0.0",
        max_attempts=max_attempts,
        retryable_categories=categories,
        retryable_codes=frozenset(),
        initial_backoff_seconds=1,
        backoff_multiplier=2,
        max_backoff_seconds=300,
    )


_RETRY_REGISTRY = _PolicyRegistry(
    "job_retry_policy",
    (
        _retry("group_memory_learning.v1", 5),
        _retry("memory_digest.v1", 3),
        _retry(
            "outbound_delivery.v1",
            1,
            categories=frozenset(),
        ),
        _retry("sandbox_admin_operation.v1", 5),
        _retry("semantic_index.v1", 3),
        _retry("session_summary.v1", 3),
    ),
)


def require_job_schedule_policy(policy_id: str) -> JobSchedulePolicy:
    return _SCHEDULE_REGISTRY.require(policy_id)


def require_job_retry_policy(policy_id: str) -> JobRetryPolicy:
    return _RETRY_REGISTRY.require(policy_id)


def job_schedule_policy_snapshot() -> RegistrySnapshot:
    return _SCHEDULE_REGISTRY.snapshot


def job_retry_policy_snapshot() -> RegistrySnapshot:
    return _RETRY_REGISTRY.snapshot


__all__ = [
    "JobRetryPolicy",
    "JobSchedulePolicy",
    "job_retry_policy_snapshot",
    "job_schedule_policy_snapshot",
    "require_job_retry_policy",
    "require_job_schedule_policy",
]

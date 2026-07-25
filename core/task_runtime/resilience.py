"""Task 失败到重试、熔断引用和类型化终态的冻结策略。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from core.registry import RegistryBuilder, RegistrySnapshot
from core.resilience import FailureCategory
from core.task_runtime.contracts import (
    TaskFailureCode,
    TaskTerminalAction,
    failure_category_for_code,
)


_VERSION_PATTERN = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?$"
)


def _required_text(value: object, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(
            f"ResiliencePolicyDescriptor.{field_name} 不能为空"
        )
    if any(ord(character) < 32 for character in normalized):
        raise ValueError(
            f"ResiliencePolicyDescriptor.{field_name} 不能包含控制字符"
        )
    return normalized


@dataclass(frozen=True, slots=True)
class ResiliencePolicyDescriptor:
    """一次 Task run 的完整同步韧性预算。"""

    policy_id: str
    version: str
    owner_module: str
    max_attempts: int
    total_timeout_seconds: float
    per_attempt_timeout_seconds: float
    backoff_initial_seconds: float
    backoff_multiplier: float
    backoff_max_seconds: float
    jitter_ratio: float
    retryable_failure_categories: frozenset[FailureCategory]
    retryable_failure_codes: frozenset[TaskFailureCode]
    fallback_route: str | None
    circuit_breaker_policy_id: str
    terminal_action: TaskTerminalAction
    slo_descriptor_id: str

    def __post_init__(self) -> None:
        policy_id = _required_text(
            self.policy_id,
            field_name="policy_id",
        )
        version = _required_text(self.version, field_name="version")
        owner = _required_text(
            self.owner_module,
            field_name="owner_module",
        )
        circuit_breaker = _required_text(
            self.circuit_breaker_policy_id,
            field_name="circuit_breaker_policy_id",
        )
        slo_descriptor_id = _required_text(
            self.slo_descriptor_id,
            field_name="slo_descriptor_id",
        )
        if _VERSION_PATTERN.fullmatch(version) is None:
            raise ValueError(
                "ResiliencePolicyDescriptor.version 必须是语义版本"
            )
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or self.max_attempts <= 0
        ):
            raise ValueError(
                "ResiliencePolicyDescriptor.max_attempts 必须为正整数"
            )
        total_timeout = float(self.total_timeout_seconds)
        attempt_timeout = float(self.per_attempt_timeout_seconds)
        if not math.isfinite(total_timeout) or total_timeout <= 0:
            raise ValueError("ResiliencePolicy 总 timeout 必须为正数")
        if not math.isfinite(attempt_timeout) or attempt_timeout <= 0:
            raise ValueError("ResiliencePolicy 单次 timeout 必须为正数")
        if attempt_timeout > total_timeout:
            raise ValueError(
                "ResiliencePolicy 单次 timeout 不能超过总 timeout"
            )
        backoff_initial = float(self.backoff_initial_seconds)
        backoff_multiplier = float(self.backoff_multiplier)
        backoff_max = float(self.backoff_max_seconds)
        jitter_ratio = float(self.jitter_ratio)
        if (
            not math.isfinite(backoff_initial)
            or backoff_initial < 0
            or not math.isfinite(backoff_max)
            or backoff_max < backoff_initial
        ):
            raise ValueError("ResiliencePolicy backoff 范围不合法")
        if (
            not math.isfinite(backoff_multiplier)
            or backoff_multiplier < 1
        ):
            raise ValueError(
                "ResiliencePolicy backoff multiplier 不能小于 1"
            )
        if not math.isfinite(jitter_ratio) or not 0 <= jitter_ratio <= 1:
            raise ValueError(
                "ResiliencePolicy jitter ratio 必须位于 [0, 1]"
            )
        categories = frozenset(
            FailureCategory(item)
            for item in self.retryable_failure_categories
        )
        codes = frozenset(
            TaskFailureCode(item)
            for item in self.retryable_failure_codes
        )
        if (
            self.max_attempts > 1
            and not categories
            and not codes
        ):
            raise ValueError(
                "可重试 ResiliencePolicy 必须声明 failure category 或 code"
            )
        fallback_route = (
            _required_text(
                self.fallback_route,
                field_name="fallback_route",
            )
            if self.fallback_route is not None
            else None
        )
        object.__setattr__(self, "policy_id", policy_id)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "owner_module", owner)
        object.__setattr__(
            self,
            "total_timeout_seconds",
            total_timeout,
        )
        object.__setattr__(
            self,
            "per_attempt_timeout_seconds",
            attempt_timeout,
        )
        object.__setattr__(
            self,
            "backoff_initial_seconds",
            backoff_initial,
        )
        object.__setattr__(
            self,
            "backoff_multiplier",
            backoff_multiplier,
        )
        object.__setattr__(
            self,
            "backoff_max_seconds",
            backoff_max,
        )
        object.__setattr__(self, "jitter_ratio", jitter_ratio)
        object.__setattr__(
            self,
            "retryable_failure_categories",
            categories,
        )
        object.__setattr__(
            self,
            "retryable_failure_codes",
            codes,
        )
        object.__setattr__(self, "fallback_route", fallback_route)
        object.__setattr__(
            self,
            "circuit_breaker_policy_id",
            circuit_breaker,
        )
        object.__setattr__(
            self,
            "terminal_action",
            TaskTerminalAction(self.terminal_action),
        )
        object.__setattr__(
            self,
            "slo_descriptor_id",
            slo_descriptor_id,
        )

    @property
    def registry_namespace(self) -> str:
        return "resilience_policy"

    @property
    def registry_id(self) -> str:
        return self.policy_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> dict[str, object]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "owner_module": self.owner_module,
            "max_attempts": self.max_attempts,
            "total_timeout_seconds": self.total_timeout_seconds,
            "per_attempt_timeout_seconds": (
                self.per_attempt_timeout_seconds
            ),
            "backoff_initial_seconds": self.backoff_initial_seconds,
            "backoff_multiplier": self.backoff_multiplier,
            "backoff_max_seconds": self.backoff_max_seconds,
            "jitter_ratio": self.jitter_ratio,
            "retryable_failure_categories": sorted(
                category.value
                for category in self.retryable_failure_categories
            ),
            "retryable_failure_codes": sorted(
                code.value
                for code in self.retryable_failure_codes
            ),
            "fallback_route": self.fallback_route,
            "circuit_breaker_policy_id": (
                self.circuit_breaker_policy_id
            ),
            "terminal_action": self.terminal_action.value,
            "slo_descriptor_id": self.slo_descriptor_id,
        }

    def allows_retry(
        self,
        code: TaskFailureCode | str,
        *,
        failure_retryable: bool,
    ) -> bool:
        """只读取类型化字段；异常正文不属于本接口。"""

        typed_code = TaskFailureCode(code)
        return bool(
            failure_retryable
            and (
                typed_code in self.retryable_failure_codes
                or failure_category_for_code(typed_code)
                in self.retryable_failure_categories
            )
        )

    def backoff_seconds(
        self,
        attempt_no: int,
        *,
        jitter_sample: float,
    ) -> float:
        """返回当前失败 attempt 到下一 attempt 的等待时间。"""

        if (
            isinstance(attempt_no, bool)
            or not isinstance(attempt_no, int)
            or attempt_no <= 0
        ):
            raise ValueError("attempt_no 必须为正整数")
        sample = float(jitter_sample)
        if not math.isfinite(sample) or not 0 <= sample <= 1:
            raise ValueError("jitter_sample 必须位于 [0, 1]")
        base = min(
            self.backoff_max_seconds,
            self.backoff_initial_seconds
            * (self.backoff_multiplier ** (attempt_no - 1)),
        )
        jitter_factor = 1 + ((sample * 2) - 1) * self.jitter_ratio
        return max(0.0, base * jitter_factor)


class ResiliencePolicyRegistry:
    """代码所有、构造即冻结的 Task 韧性策略注册表。"""

    def __init__(
        self,
        descriptors: tuple[ResiliencePolicyDescriptor, ...],
    ) -> None:
        builder = RegistryBuilder[ResiliencePolicyDescriptor](
            "resilience_policy"
        )
        for descriptor in descriptors:
            builder.register(descriptor)
        self._snapshot = builder.freeze()

    @property
    def registry_snapshot(
        self,
    ) -> RegistrySnapshot[ResiliencePolicyDescriptor]:
        return self._snapshot

    def require(self, policy_id: str) -> ResiliencePolicyDescriptor:
        normalized = str(policy_id or "").strip()
        try:
            return self._snapshot.require(normalized)
        except KeyError as exc:
            raise ValueError(
                "未登记的 Task ResiliencePolicy："
                f"{normalized or '<empty>'}"
            ) from exc

    def descriptors(self) -> tuple[ResiliencePolicyDescriptor, ...]:
        return tuple(self._snapshot)


_TRANSIENT_CATEGORIES = frozenset({
    FailureCategory.UNAVAILABLE,
    FailureCategory.TIMEOUT,
    FailureCategory.RATE_LIMITED,
    FailureCategory.TRANSIENT_TRANSPORT,
})
_OUTPUT_CODES = frozenset({
    TaskFailureCode.EMPTY_OUTPUT,
    TaskFailureCode.INVALID_JSON,
    TaskFailureCode.SCHEMA_INVALID,
    TaskFailureCode.FIELD_OUT_OF_RANGE,
    TaskFailureCode.BUSINESS_VALIDATION_FAILED,
})


def _policy(
    policy_id: str,
    *,
    max_attempts: int,
    total_timeout_seconds: float,
    per_attempt_timeout_seconds: float,
    terminal_action: TaskTerminalAction,
    retryable_failure_categories: frozenset[
        FailureCategory
    ] = frozenset(),
    retryable_failure_codes: frozenset[
        TaskFailureCode
    ] = frozenset(),
    backoff_initial_seconds: float = 0.0,
    backoff_multiplier: float = 1.0,
    backoff_max_seconds: float = 0.0,
    jitter_ratio: float = 0.0,
    fallback_route: str | None = None,
    slo_descriptor_id: str = "model_route_slo.baseline.v1",
) -> ResiliencePolicyDescriptor:
    return ResiliencePolicyDescriptor(
        policy_id=policy_id,
        version="1.0.0",
        owner_module="core.task_runtime",
        max_attempts=max_attempts,
        total_timeout_seconds=total_timeout_seconds,
        per_attempt_timeout_seconds=per_attempt_timeout_seconds,
        backoff_initial_seconds=backoff_initial_seconds,
        backoff_multiplier=backoff_multiplier,
        backoff_max_seconds=backoff_max_seconds,
        jitter_ratio=jitter_ratio,
        retryable_failure_categories=retryable_failure_categories,
        retryable_failure_codes=retryable_failure_codes,
        fallback_route=fallback_route,
        circuit_breaker_policy_id="model_failure_tracker.default",
        terminal_action=terminal_action,
        slo_descriptor_id=slo_descriptor_id,
    )


_POLICY_DESCRIPTORS = (
    _policy(
        "fail_closed",
        max_attempts=1,
        total_timeout_seconds=30,
        per_attempt_timeout_seconds=30,
        terminal_action=TaskTerminalAction.BLOCK,
    ),
    _policy(
        "single_attempt_normal_agent",
        max_attempts=1,
        total_timeout_seconds=15,
        per_attempt_timeout_seconds=15,
        terminal_action=TaskTerminalAction.NORMAL_AGENT,
        slo_descriptor_id="task_slo.by_invocation.v1",
    ),
    _policy(
        "single_attempt_deterministic_fallback",
        max_attempts=1,
        total_timeout_seconds=180,
        per_attempt_timeout_seconds=180,
        terminal_action=TaskTerminalAction.DETERMINISTIC_FALLBACK,
    ),
    _policy(
        "retry_route_deterministic_fallback",
        max_attempts=2,
        total_timeout_seconds=60,
        per_attempt_timeout_seconds=30,
        retryable_failure_categories=_TRANSIENT_CATEGORIES,
        backoff_initial_seconds=0.25,
        backoff_multiplier=2,
        backoff_max_seconds=1,
        jitter_ratio=0.2,
        terminal_action=TaskTerminalAction.DETERMINISTIC_FALLBACK,
        slo_descriptor_id="task_slo.by_invocation.v1",
    ),
    _policy(
        "retry_twice_branch_failed",
        max_attempts=3,
        total_timeout_seconds=180,
        per_attempt_timeout_seconds=60,
        retryable_failure_categories=_TRANSIENT_CATEGORIES,
        retryable_failure_codes=_OUTPUT_CODES,
        backoff_initial_seconds=0.25,
        backoff_multiplier=2,
        backoff_max_seconds=1,
        jitter_ratio=0.2,
        terminal_action=TaskTerminalAction.BRANCH_FAILED,
        slo_descriptor_id="task_slo.by_invocation.v1",
    ),
    _policy(
        "retry_once_then_no_reply",
        max_attempts=2,
        total_timeout_seconds=30,
        per_attempt_timeout_seconds=15,
        retryable_failure_categories=_TRANSIENT_CATEGORIES,
        retryable_failure_codes=_OUTPUT_CODES,
        backoff_initial_seconds=0.1,
        backoff_multiplier=2,
        backoff_max_seconds=0.5,
        jitter_ratio=0.2,
        terminal_action=TaskTerminalAction.NO_REPLY,
        slo_descriptor_id="task_slo.by_invocation.v1",
    ),
    _policy(
        "retry_once_keep_unprocessed",
        max_attempts=2,
        total_timeout_seconds=180,
        per_attempt_timeout_seconds=90,
        retryable_failure_categories=_TRANSIENT_CATEGORIES,
        retryable_failure_codes=_OUTPUT_CODES,
        backoff_initial_seconds=0.25,
        backoff_multiplier=2,
        backoff_max_seconds=1,
        jitter_ratio=0.2,
        terminal_action=TaskTerminalAction.KEEP_UNPROCESSED,
    ),
    _policy(
        "drop_invalid_keep_catalog",
        max_attempts=1,
        total_timeout_seconds=120,
        per_attempt_timeout_seconds=120,
        terminal_action=TaskTerminalAction.KEEP_CATALOG,
    ),
    _policy(
        "block",
        max_attempts=1,
        total_timeout_seconds=30,
        per_attempt_timeout_seconds=30,
        terminal_action=TaskTerminalAction.BLOCK,
    ),
    _policy(
        "retry_then_preserve_pending",
        max_attempts=2,
        total_timeout_seconds=120,
        per_attempt_timeout_seconds=60,
        retryable_failure_categories=_TRANSIENT_CATEGORIES,
        retryable_failure_codes=_OUTPUT_CODES,
        backoff_initial_seconds=0.25,
        backoff_multiplier=2,
        backoff_max_seconds=1,
        jitter_ratio=0.2,
        terminal_action=TaskTerminalAction.PRESERVE_PENDING,
    ),
    _policy(
        "single_attempt_preserve_pending",
        max_attempts=1,
        total_timeout_seconds=90,
        per_attempt_timeout_seconds=90,
        terminal_action=TaskTerminalAction.PRESERVE_PENDING,
        slo_descriptor_id="task_slo.by_invocation.v1",
    ),
    _policy(
        "single_attempt_conservative_downrank",
        max_attempts=1,
        total_timeout_seconds=30,
        per_attempt_timeout_seconds=30,
        terminal_action=TaskTerminalAction.CONSERVATIVE_DOWNRANK,
        slo_descriptor_id="task_slo.by_invocation.v1",
    ),
)


RESILIENCE_POLICY_REGISTRY = ResiliencePolicyRegistry(
    _POLICY_DESCRIPTORS
)


def require_resilience_policy(
    policy_id: str,
) -> ResiliencePolicyDescriptor:
    return RESILIENCE_POLICY_REGISTRY.require(policy_id)


def list_resilience_policies() -> tuple[ResiliencePolicyDescriptor, ...]:
    return RESILIENCE_POLICY_REGISTRY.descriptors()


__all__ = [
    "RESILIENCE_POLICY_REGISTRY",
    "ResiliencePolicyDescriptor",
    "ResiliencePolicyRegistry",
    "list_resilience_policies",
    "require_resilience_policy",
]

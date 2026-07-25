"""逐语义 Task 的版本化 SLO、成本预算与激活门禁。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from core.registry import RegistryBuilder, RegistrySnapshot
from core.task_runtime.contracts import TaskTerminalAction


_VERSION_PATTERN = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?$"
)
_BASELINE_ARTIFACT = (
    "docs/architecture/semantic-task-performance-baseline.json"
)
_APPROVAL_REF = (
    "docs/superpowers/specs/"
    "2026-07-23-hardcoded-routing-and-modularization-master-plan.md"
    "#47-语义-task-slo-与成本预算"
)


class TaskSloStatus(StrEnum):
    BASELINE_ONLY = "baseline_only"
    FROZEN = "frozen"


class TaskBillingClass(StrEnum):
    LOCAL_FREE = "local_free"
    PROVIDER_GATEWAY = "provider_gateway"


class TaskSloActivationError(RuntimeError):
    """Task 尚未具备从观察切换为生效行为的 SLO。"""


def _required_text(
    value: object,
    *,
    field_name: str,
    max_chars: int = 256,
) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > max_chars
        or any(ord(character) < 32 for character in normalized)
    ):
        raise ValueError(f"TaskSloDescriptor.{field_name} 无效")
    return normalized


def _positive_integer(value: object, *, field_name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ValueError(
            f"TaskSloDescriptor.{field_name} 必须为正整数"
        )
    return value


def _optional_positive_number(
    value: float | int | None,
    *,
    field_name: str,
) -> float | None:
    if value is None:
        return None
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(
            f"TaskSloDescriptor.{field_name} 必须为正有限数"
        )
    return normalized


def _optional_rate(
    value: float | None,
    *,
    field_name: str,
) -> float | None:
    if value is None:
        return None
    normalized = float(value)
    if not math.isfinite(normalized) or not 0 <= normalized <= 1:
        raise ValueError(
            f"TaskSloDescriptor.{field_name} 必须位于 [0, 1]"
        )
    return normalized


@dataclass(frozen=True, slots=True)
class TaskSloDescriptor:
    slo_id: str
    task_id: str
    route_key: str
    owner_module: str
    version: str
    status: TaskSloStatus
    baseline_artifact: str
    baseline_task_id: str
    observation_window_days: int
    min_sample_count: int
    max_task_runs_per_request: int
    max_provider_attempts_per_run: int
    max_input_chars: int
    max_input_tokens: int
    max_output_tokens: int
    daily_call_limit: int
    max_concurrency: int
    billing_class: TaskBillingClass
    circuit_breaker_scope: str
    terminal_action: TaskTerminalAction
    p50_latency_ms: int | None = None
    p95_latency_ms: int | None = None
    p99_latency_ms: int | None = None
    daily_cost_limit: float | None = None
    cost_per_1000_calls_limit: float | None = None
    cpu_time_ms_limit: int | None = None
    gpu_time_ms_limit: int | None = None
    max_total_failure_rate: float | None = None
    max_timeout_rate: float | None = None
    max_unavailable_rate: float | None = None
    max_contract_violation_rate: float | None = None
    max_fallback_rate: float | None = None
    approved_by: str = ""
    approval_ref: str = ""
    observation_only_reason: str = ""

    def __post_init__(self) -> None:
        for field_name in (
            "slo_id",
            "task_id",
            "route_key",
            "owner_module",
            "baseline_artifact",
            "circuit_breaker_scope",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(
                    getattr(self, field_name),
                    field_name=field_name,
                ),
            )
        version = _required_text(
            self.version,
            field_name="version",
            max_chars=64,
        )
        if _VERSION_PATTERN.fullmatch(version) is None:
            raise ValueError(
                "TaskSloDescriptor.version 必须是语义版本"
            )
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "status", TaskSloStatus(self.status))
        object.__setattr__(
            self,
            "billing_class",
            TaskBillingClass(self.billing_class),
        )
        object.__setattr__(
            self,
            "terminal_action",
            TaskTerminalAction(self.terminal_action),
        )
        for field_name in (
            "observation_window_days",
            "min_sample_count",
            "max_task_runs_per_request",
            "max_provider_attempts_per_run",
            "max_input_chars",
            "max_input_tokens",
            "max_output_tokens",
            "daily_call_limit",
            "max_concurrency",
        ):
            _positive_integer(
                getattr(self, field_name),
                field_name=field_name,
            )

        latency = (
            self.p50_latency_ms,
            self.p95_latency_ms,
            self.p99_latency_ms,
        )
        for field_name, value in zip(
            ("p50_latency_ms", "p95_latency_ms", "p99_latency_ms"),
            latency,
            strict=True,
        ):
            if value is not None:
                _positive_integer(value, field_name=field_name)
        rates = {
            field_name: _optional_rate(
                getattr(self, field_name),
                field_name=field_name,
            )
            for field_name in (
                "max_total_failure_rate",
                "max_timeout_rate",
                "max_unavailable_rate",
                "max_contract_violation_rate",
                "max_fallback_rate",
            )
        }
        for field_name, value in rates.items():
            object.__setattr__(self, field_name, value)
        for field_name in (
            "daily_cost_limit",
            "cost_per_1000_calls_limit",
            "cpu_time_ms_limit",
            "gpu_time_ms_limit",
        ):
            object.__setattr__(
                self,
                field_name,
                _optional_positive_number(
                    getattr(self, field_name),
                    field_name=field_name,
                ),
            )

        if self.status is TaskSloStatus.FROZEN:
            if any(value is None for value in latency):
                raise ValueError("冻结 Task SLO 必须声明 P50/P95/P99")
            p50, p95, p99 = (int(value) for value in latency)
            if not p50 <= p95 <= p99:
                raise ValueError(
                    "Task SLO 延迟预算必须满足 P50 <= P95 <= P99"
                )
            if any(value is None for value in rates.values()):
                raise ValueError("冻结 Task SLO 必须声明全部失败率预算")
            if not str(self.baseline_task_id or "").strip():
                raise ValueError(
                    "冻结 Task SLO 必须声明 baseline_task_id"
                )
            object.__setattr__(
                self,
                "approved_by",
                _required_text(
                    self.approved_by,
                    field_name="approved_by",
                    max_chars=128,
                ),
            )
            object.__setattr__(
                self,
                "approval_ref",
                _required_text(
                    self.approval_ref,
                    field_name="approval_ref",
                    max_chars=512,
                ),
            )
            if self.billing_class is TaskBillingClass.PROVIDER_GATEWAY:
                if (
                    self.daily_cost_limit is None
                    or self.cost_per_1000_calls_limit is None
                ):
                    raise ValueError(
                        "冻结 Provider Task SLO 必须声明成本预算"
                    )
            elif (
                self.cpu_time_ms_limit is None
                and self.gpu_time_ms_limit is None
            ):
                raise ValueError(
                    "冻结本�� Task SLO 必须声明 CPU 或 GPU 时间预算"
                )
        else:
            if any(value is not None for value in latency):
                raise ValueError(
                    "baseline_only Task SLO 不能声明延迟预算"
                )
            if any(value is not None for value in rates.values()):
                raise ValueError(
                    "baseline_only Task SLO 不能声明失败率预算"
                )
            if self.daily_cost_limit is not None or (
                self.cost_per_1000_calls_limit is not None
            ):
                raise ValueError(
                    "baseline_only Task SLO 不能伪造成本预算"
                )
            object.__setattr__(
                self,
                "observation_only_reason",
                _required_text(
                    self.observation_only_reason,
                    field_name="observation_only_reason",
                    max_chars=512,
                ),
            )

    @property
    def registry_namespace(self) -> str:
        return "task_slo"

    @property
    def registry_id(self) -> str:
        return self.slo_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> Mapping[str, object]:
        return self.metadata()

    def metadata(self) -> dict[str, object]:
        return {
            field_name: (
                value.value
                if isinstance(
                    value,
                    (TaskSloStatus, TaskBillingClass, TaskTerminalAction),
                )
                else value
            )
            for field_name, value in (
                (field_name, getattr(self, field_name))
                for field_name in self.__dataclass_fields__
            )
        }


class TaskSloRegistry:
    """构造即冻结，并与 Route、Task Contract、Resilience 对账。"""

    def __init__(
        self,
        descriptors: tuple[TaskSloDescriptor, ...],
    ) -> None:
        builder = RegistryBuilder[TaskSloDescriptor]("task_slo")
        for descriptor in descriptors:
            builder.register(descriptor)
        self._snapshot = builder.freeze()
        self._by_task = {
            descriptor.task_id: descriptor
            for descriptor in self._snapshot
        }
        if len(self._by_task) != len(tuple(self._snapshot)):
            raise ValueError("Task SLO 包含重复 task_id")
        self._validate_bindings()

    def _validate_bindings(self) -> None:
        from core.model_provider.route_registry import (
            require_model_route_descriptor,
        )
        from core.prompt_v2.task_contracts import get_task_contract
        from core.task_runtime.resilience import (
            require_resilience_policy,
        )

        for descriptor in self._snapshot:
            route = require_model_route_descriptor(
                descriptor.route_key
            )
            contract = get_task_contract(
                f"tasks/{descriptor.task_id}"
            )
            if contract is None:
                raise ValueError(
                    f"Task SLO 缺少 Task Contract：{descriptor.task_id}"
                )
            policy = require_resilience_policy(
                contract.output_failure_policy
            )
            if route.runtime_task_key != f"tasks/{descriptor.task_id}":
                raise ValueError(
                    f"Task SLO 与 Route runtime task 不一致："
                    f"{descriptor.task_id}"
                )
            if route.slo.task_slo_descriptor_id != descriptor.slo_id:
                raise ValueError(
                    f"Task SLO 与 Model Route 引用不一致："
                    f"{descriptor.task_id}"
                )
            if route.owner != descriptor.owner_module:
                raise ValueError(
                    f"Task SLO owner 与 Model Route 不一致："
                    f"{descriptor.task_id}"
                )
            if (
                route.default_max_tokens
                > descriptor.max_output_tokens
            ):
                raise ValueError(
                    f"Task SLO 输出上限低于 Route 默认值："
                    f"{descriptor.task_id}"
                )
            if (
                policy.max_attempts
                > descriptor.max_provider_attempts_per_run
            ):
                raise ValueError(
                    f"Task SLO attempt 上限低于 ResiliencePolicy："
                    f"{descriptor.task_id}"
                )
            if policy.slo_descriptor_id != "task_slo.by_invocation.v1":
                raise ValueError(
                    f"ResiliencePolicy 未按 invocation 解析 Task SLO："
                    f"{descriptor.task_id}"
                )
            if policy.terminal_action is not descriptor.terminal_action:
                raise ValueError(
                    f"Task SLO 终态与 ResiliencePolicy 不一致："
                    f"{descriptor.task_id}"
                )

    @property
    def registry_snapshot(
        self,
    ) -> RegistrySnapshot[TaskSloDescriptor]:
        return self._snapshot

    def get(self, task_id: str) -> TaskSloDescriptor | None:
        return self._by_task.get(str(task_id or "").strip())

    def require(self, task_id: str) -> TaskSloDescriptor:
        descriptor = self.get(task_id)
        if descriptor is None:
            raise ValueError(
                f"未登记的 Task SLO：{task_id or '<empty>'}"
            )
        return descriptor

    def descriptors(self) -> tuple[TaskSloDescriptor, ...]:
        return tuple(self._snapshot)


def _frozen_local_slo(
    task_id: str,
    *,
    baseline_task_id: str,
    min_sample_count: int,
    p50_latency_ms: int,
    p95_latency_ms: int,
    p99_latency_ms: int,
    max_attempts: int,
    max_input_chars: int,
    max_input_tokens: int,
    max_output_tokens: int,
    max_total_failure_rate: float,
    terminal_action: TaskTerminalAction,
) -> TaskSloDescriptor:
    return TaskSloDescriptor(
        slo_id=f"task_slo.{task_id}.v1",
        task_id=task_id,
        route_key=task_id,
        owner_module="core.private_timing",
        version="1.0.0",
        status=TaskSloStatus.FROZEN,
        baseline_artifact=_BASELINE_ARTIFACT,
        baseline_task_id=baseline_task_id,
        observation_window_days=30,
        min_sample_count=min_sample_count,
        p50_latency_ms=p50_latency_ms,
        p95_latency_ms=p95_latency_ms,
        p99_latency_ms=p99_latency_ms,
        max_task_runs_per_request=1,
        max_provider_attempts_per_run=max_attempts,
        max_input_chars=max_input_chars,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        daily_call_limit=2000,
        max_concurrency=8,
        billing_class=TaskBillingClass.LOCAL_FREE,
        cpu_time_ms_limit=p99_latency_ms,
        gpu_time_ms_limit=p99_latency_ms,
        max_total_failure_rate=max_total_failure_rate,
        max_timeout_rate=0.05,
        max_unavailable_rate=0.10,
        max_contract_violation_rate=0.05,
        max_fallback_rate=max_total_failure_rate,
        circuit_breaker_scope=f"route:{task_id}",
        terminal_action=terminal_action,
        approved_by="project_owner_plan",
        approval_ref=_APPROVAL_REF,
    )


def _baseline_provider_slo(
    task_id: str,
    *,
    owner_module: str,
    baseline_task_id: str,
    min_sample_count: int,
    max_attempts: int,
    max_input_chars: int,
    max_input_tokens: int,
    max_output_tokens: int,
    daily_call_limit: int,
    max_concurrency: int,
    terminal_action: TaskTerminalAction,
    observation_only_reason: str,
) -> TaskSloDescriptor:
    return TaskSloDescriptor(
        slo_id=f"task_slo.{task_id}.v1",
        task_id=task_id,
        route_key=task_id,
        owner_module=owner_module,
        version="1.0.0",
        status=TaskSloStatus.BASELINE_ONLY,
        baseline_artifact=_BASELINE_ARTIFACT,
        baseline_task_id=baseline_task_id,
        observation_window_days=30,
        min_sample_count=min_sample_count,
        max_task_runs_per_request=1,
        max_provider_attempts_per_run=max_attempts,
        max_input_chars=max_input_chars,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        daily_call_limit=daily_call_limit,
        max_concurrency=max_concurrency,
        billing_class=TaskBillingClass.PROVIDER_GATEWAY,
        circuit_breaker_scope=f"route:{task_id}",
        terminal_action=terminal_action,
        observation_only_reason=observation_only_reason,
    )


def _group_analysis_slo(
    task_id: str,
    *,
    max_output_tokens: int,
) -> TaskSloDescriptor:
    return _baseline_provider_slo(
        task_id,
        owner_module="app.group_analysis",
        baseline_task_id="group_analysis",
        min_sample_count=100,
        max_attempts=3,
        max_input_chars=80000,
        max_input_tokens=40000,
        max_output_tokens=max_output_tokens,
        daily_call_limit=400,
        max_concurrency=2,
        terminal_action=TaskTerminalAction.BRANCH_FAILED,
        observation_only_reason=(
            "旧基线只记录 group_analysis 聚合调用，无法归因到单个方面；"
            "缺少逐分支 Token、成本和类型化失败率。"
        ),
    )


_TASK_SLO_DESCRIPTORS = (
    _frozen_local_slo(
        "timing_gate",
        baseline_task_id="private_timing_gate",
        min_sample_count=1000,
        p50_latency_ms=8500,
        p95_latency_ms=15500,
        p99_latency_ms=16000,
        max_attempts=2,
        max_input_chars=8192,
        max_input_tokens=4096,
        max_output_tokens=30,
        max_total_failure_rate=0.15,
        terminal_action=TaskTerminalAction.NO_REPLY,
    ),
    _frozen_local_slo(
        "private_decision",
        baseline_task_id="private_decision",
        min_sample_count=50,
        p50_latency_ms=10000,
        p95_latency_ms=15500,
        p99_latency_ms=16000,
        max_attempts=1,
        max_input_chars=16384,
        max_input_tokens=8192,
        max_output_tokens=120,
        max_total_failure_rate=0.10,
        terminal_action=TaskTerminalAction.NORMAL_AGENT,
    ),
    _baseline_provider_slo(
        "news_daily_quality",
        owner_module="creatures.nanobot.news_daily",
        baseline_task_id="news_quality",
        min_sample_count=200,
        max_attempts=2,
        max_input_chars=24000,
        max_input_tokens=12000,
        max_output_tokens=3200,
        daily_call_limit=200,
        max_concurrency=2,
        terminal_action=TaskTerminalAction.DETERMINISTIC_FALLBACK,
        observation_only_reason=(
            "当前只有 57 次聚合样本，且缺少币种化成本、Token 覆盖和"
            "类型化失败码；只能观察，不能启用新的新闻语义决策。"
        ),
    ),
    _baseline_provider_slo(
        "news_relevance_review",
        owner_module="core.news",
        baseline_task_id="",
        min_sample_count=200,
        max_attempts=1,
        max_input_chars=24000,
        max_input_tokens=12000,
        max_output_tokens=2000,
        daily_call_limit=200,
        max_concurrency=2,
        terminal_action=TaskTerminalAction.CONSERVATIVE_DOWNRANK,
        observation_only_reason=(
            "新新闻相关性审核 Task 尚无生产样本；必须先观察，补齐批大小、"
            "P95/P99、Token、成本和类型化失败率后再冻结预算。"
        ),
    ),
    _group_analysis_slo(
        "group_analysis_topics",
        max_output_tokens=2048,
    ),
    _group_analysis_slo(
        "group_analysis_titles",
        max_output_tokens=2048,
    ),
    _group_analysis_slo(
        "group_analysis_quotes",
        max_output_tokens=1536,
    ),
    _group_analysis_slo(
        "group_analysis_quality",
        max_output_tokens=2048,
    ),
    _baseline_provider_slo(
        "group_memory_learning",
        owner_module="app.group_memory",
        baseline_task_id="",
        min_sample_count=100,
        max_attempts=1,
        max_input_chars=100000,
        max_input_tokens=50000,
        max_output_tokens=4096,
        daily_call_limit=200,
        max_concurrency=1,
        terminal_action=TaskTerminalAction.PRESERVE_PENDING,
        observation_only_reason=(
            "新群记忆审核 Task 尚无生产样本；必须先 candidate-only 观察，"
            "补齐延迟、Token、成本和类型化失败率后再冻结预算。"
        ),
    ),
)


TASK_SLO_REGISTRY = TaskSloRegistry(_TASK_SLO_DESCRIPTORS)


def get_task_slo_descriptor(
    task_id: str,
) -> TaskSloDescriptor | None:
    return TASK_SLO_REGISTRY.get(task_id)


def require_task_slo_descriptor(task_id: str) -> TaskSloDescriptor:
    return TASK_SLO_REGISTRY.require(task_id)


def require_task_slo_activation(task_id: str) -> TaskSloDescriptor:
    descriptor = require_task_slo_descriptor(task_id)
    if descriptor.status is not TaskSloStatus.FROZEN:
        raise TaskSloActivationError(
            f"Task {descriptor.task_id} 的 SLO 只能观察，尚未冻结预算"
        )
    return descriptor


__all__ = [
    "TASK_SLO_REGISTRY",
    "TaskBillingClass",
    "TaskSloActivationError",
    "TaskSloDescriptor",
    "TaskSloRegistry",
    "TaskSloStatus",
    "get_task_slo_descriptor",
    "require_task_slo_activation",
    "require_task_slo_descriptor",
]

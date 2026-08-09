"""Agent Harness 分层评测 Registry。

三条评测通道具有不同证据权限：只有离线确定性 gate 能阻断提交；真实模型
benchmark 只提供成本和质量证据；线上采样必须只读、禁止模型调用和业务写入。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
import re

from core.registry import RegistryBuilder, RegistryGeneration, RegistrySnapshot


class EvaluationLane(str, Enum):
    OFFLINE_DETERMINISTIC = "offline_deterministic"
    REAL_MODEL_BENCHMARK = "real_model_benchmark"
    ONLINE_READONLY_SAMPLING = "online_readonly_sampling"


class EvidenceAuthority(str, Enum):
    BLOCKING_GATE = "blocking_gate"
    BENCHMARK_ONLY = "benchmark_only"
    READONLY_SIGNAL = "readonly_signal"


_SAFE_ID_RE = re.compile(r"[a-z][a-z0-9_]{1,95}")


def _safe_id(value: object, name: str) -> str:
    normalized = str(value or "").strip()
    if _SAFE_ID_RE.fullmatch(normalized) is None:
        raise ValueError(f"{name} 必须是安全 snake_case 标识符")
    return normalized


def _required_text(value: object, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} 不能为空")
    if len(normalized) > 256:
        raise ValueError(f"{name} 不能超过 256 个字符")
    return normalized


def _test_selector(value: object) -> str:
    selector = _required_text(value, "test selector")
    path_text = selector.split("::", 1)[0]
    path = PurePosixPath(path_text)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path_text.startswith("tests/test_")
        or path.suffix != ".py"
        or "\\" in selector
    ):
        raise ValueError(f"不安全的 pytest selector: {selector!r}")
    return selector


@dataclass(frozen=True, slots=True)
class HarnessCheckDescriptor:
    check_id: str
    title: str
    metric_id: str
    selectors: tuple[str, ...]
    timeout_seconds: int = 600

    def __post_init__(self) -> None:
        object.__setattr__(self, "check_id", _safe_id(self.check_id, "check_id"))
        object.__setattr__(self, "title", _required_text(self.title, "title"))
        object.__setattr__(self, "metric_id", _safe_id(self.metric_id, "metric_id"))
        selectors = tuple(_test_selector(item) for item in self.selectors)
        if not selectors:
            raise ValueError("离线 check 必须声明 pytest selectors")
        if len(selectors) != len(set(selectors)):
            raise ValueError("pytest selectors 不能重复")
        object.__setattr__(self, "selectors", selectors)
        if type(self.timeout_seconds) is not int or not (
            1 <= self.timeout_seconds <= 1_800
        ):
            raise ValueError("timeout_seconds 必须位于 1..1800")

    def to_dict(self) -> dict[str, object]:
        return {
            "check_id": self.check_id,
            "title": self.title,
            "metric_id": self.metric_id,
            "selectors": list(self.selectors),
            "timeout_seconds": self.timeout_seconds,
            "thresholds": {
                "min_pass_rate": 1.0,
                "max_failures": 0,
                "max_errors": 0,
                "max_skipped": 0,
            },
        }


@dataclass(frozen=True, slots=True)
class EvidenceMetricContract:
    metric_id: str
    value_kind: str
    minimum: float | None = None
    maximum: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric_id", _safe_id(self.metric_id, "metric_id"))
        if self.value_kind not in {"integer", "number", "rate"}:
            raise ValueError("value_kind 无效")
        if self.minimum is not None and not isinstance(self.minimum, (int, float)):
            raise ValueError("minimum 必须是数值或空")
        if self.maximum is not None and not isinstance(self.maximum, (int, float)):
            raise ValueError("maximum 必须是数值或空")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("metric minimum 不能大于 maximum")
        if self.value_kind == "rate" and (
            (self.minimum is not None and self.minimum < 0)
            or (self.maximum is not None and self.maximum > 1)
        ):
            raise ValueError("rate 阈值必须位于 0..1")

    def to_dict(self) -> dict[str, object]:
        return {
            "metric_id": self.metric_id,
            "value_kind": self.value_kind,
            "minimum": self.minimum,
            "maximum": self.maximum,
        }


@dataclass(frozen=True, slots=True)
class HarnessSuiteDescriptor:
    registry_id: str
    title: str
    lane: EvaluationLane
    authority: EvidenceAuthority
    domains: tuple[str, ...]
    checks: tuple[HarnessCheckDescriptor, ...] = ()
    evidence_metrics: tuple[EvidenceMetricContract, ...] = ()
    blocking: bool = False
    allows_network: bool = False
    allows_model_calls: bool = False
    production_data_mode: str = "forbidden"
    explicit_opt_in_required: bool = False
    cost_budget_required: bool = False
    lifecycle: str = "active"
    registry_namespace: str = field(default="eval_harness", init=False)
    registry_dependencies: tuple[str, ...] = field(default=(), init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "registry_id",
            _safe_id(self.registry_id, "registry_id"),
        )
        object.__setattr__(self, "title", _required_text(self.title, "title"))
        try:
            lane = EvaluationLane(self.lane)
            authority = EvidenceAuthority(self.authority)
        except ValueError as exc:
            raise ValueError("评测 lane 或 authority 无效") from exc
        object.__setattr__(self, "lane", lane)
        object.__setattr__(self, "authority", authority)
        domains = tuple(_safe_id(item, "domain") for item in self.domains)
        if not domains or len(domains) != len(set(domains)):
            raise ValueError("domains 必须非空且不能重复")
        object.__setattr__(self, "domains", domains)
        checks = tuple(self.checks)
        metrics = tuple(self.evidence_metrics)
        if len({item.check_id for item in checks}) != len(checks):
            raise ValueError("check_id 不能重复")
        if len({item.metric_id for item in checks}) != len(checks):
            raise ValueError("离线 check metric_id 不能重复")
        if len({item.metric_id for item in metrics}) != len(metrics):
            raise ValueError("evidence metric_id 不能重复")
        object.__setattr__(self, "checks", checks)
        object.__setattr__(self, "evidence_metrics", metrics)
        if self.production_data_mode not in {"forbidden", "readonly"}:
            raise ValueError("production_data_mode 无效")
        if lane is EvaluationLane.OFFLINE_DETERMINISTIC:
            if (
                authority is not EvidenceAuthority.BLOCKING_GATE
                or not self.blocking
                or not checks
                or metrics
                or self.allows_network
                or self.allows_model_calls
                or self.production_data_mode != "forbidden"
                or self.explicit_opt_in_required
                or self.cost_budget_required
            ):
                raise ValueError("离线确定性 gate 的权限边界无效")
        elif lane is EvaluationLane.REAL_MODEL_BENCHMARK:
            if (
                authority is not EvidenceAuthority.BENCHMARK_ONLY
                or self.blocking
                or checks
                or not metrics
                or not self.allows_network
                or not self.allows_model_calls
                or self.production_data_mode != "forbidden"
                or not self.explicit_opt_in_required
                or not self.cost_budget_required
            ):
                raise ValueError("真实模型 benchmark 的权限边界无效")
        elif (
            authority is not EvidenceAuthority.READONLY_SIGNAL
            or self.blocking
            or checks
            or not metrics
            or self.allows_network
            or self.allows_model_calls
            or self.production_data_mode != "readonly"
            or self.cost_budget_required
        ):
            raise ValueError("线上只读采样的权限边界无效")

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "title": self.title,
            "lane": self.lane.value,
            "authority": self.authority.value,
            "domains": self.domains,
            "checks": tuple(item.to_dict() for item in self.checks),
            "evidence_metrics": tuple(
                item.to_dict() for item in self.evidence_metrics
            ),
            "blocking": self.blocking,
            "allows_network": self.allows_network,
            "allows_model_calls": self.allows_model_calls,
            "production_data_mode": self.production_data_mode,
            "explicit_opt_in_required": self.explicit_opt_in_required,
            "cost_budget_required": self.cost_budget_required,
            "lifecycle": self.lifecycle,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "suite_id": self.registry_id,
            **dict(self.registry_payload()),
        }


def _check(
    check_id: str,
    title: str,
    metric_id: str,
    *selectors: str,
) -> HarnessCheckDescriptor:
    return HarnessCheckDescriptor(
        check_id=check_id,
        title=title,
        metric_id=metric_id,
        selectors=tuple(selectors),
    )


_SUITES = (
    HarnessSuiteDescriptor(
        registry_id="offline_runtime_equivalence",
        title="Runtime 合同与 Native/KT 等价门禁",
        lane=EvaluationLane.OFFLINE_DETERMINISTIC,
        authority=EvidenceAuthority.BLOCKING_GATE,
        domains=("runtime_contract", "native_runtime", "kt_adapter"),
        checks=(
            _check(
                "runtime_contract_equivalence",
                "Runtime 最小端口和 Native/KT 等价",
                "runtime_equivalence_pass_rate",
                "tests/test_agent_runtime_port.py",
                "tests/test_native_without_kt.py",
            ),
            _check(
                "native_runtime_behavior",
                "Native 模型工具循环、流式与恢复行为",
                "native_runtime_pass_rate",
                "tests/test_native_agent_runtime.py",
            ),
            _check(
                "kt_adapter_behavior",
                "KT 公开 Adapter 与模型 Provider 兼容行为",
                "kt_adapter_pass_rate",
                "tests/test_kt_framework.py",
                "tests/test_kt_model_provider_adapter.py",
            ),
        ),
        blocking=True,
    ),
    HarnessSuiteDescriptor(
        registry_id="offline_prompt_context",
        title="Prompt 稳定、缓存与 Context 压缩门禁",
        lane=EvaluationLane.OFFLINE_DETERMINISTIC,
        authority=EvidenceAuthority.BLOCKING_GATE,
        domains=("prompt_stability", "prefix_cache", "context_compaction"),
        checks=(
            _check(
                "prompt_prefix_stability",
                "稳定前缀、工具顺序和缓存 Manifest",
                "prompt_cache_pass_rate",
                "tests/test_prompt_prefix_cache.py",
                "tests/test_prompt_runtime_request_contract.py",
            ),
            _check(
                "context_compaction",
                "上下文水位、工具批次与安全压缩",
                "context_compaction_pass_rate",
                "tests/test_context_compaction.py",
            ),
            _check(
                "context_manifest",
                "Context Manifest 预算与来源证据",
                "context_manifest_pass_rate",
                "tests/test_context_engine.py",
            ),
        ),
        blocking=True,
    ),
    HarnessSuiteDescriptor(
        registry_id="offline_memory_skill_mcp",
        title="记忆注入、Skill 选择与 MCP 门禁",
        lane=EvaluationLane.OFFLINE_DETERMINISTIC,
        authority=EvidenceAuthority.BLOCKING_GATE,
        domains=("memory_injection", "skill_selection", "mcp"),
        checks=(
            _check(
                "memory_injection",
                "记忆来源、治理、相关性与注入预算",
                "memory_injection_pass_rate",
                "tests/test_group_memory_injection.py",
                "tests/test_memory_provider_registry.py",
            ),
            _check(
                "skill_selection",
                "Skill 适用性、依赖闭包和版本治理",
                "skill_selection_pass_rate",
                "tests/test_skill_governance.py",
                "tests/test_agent_skills.py",
                "tests/test_skill_candidates.py",
            ),
            _check(
                "mcp_contract",
                "MCP 命名空间、Schema、传输和模糊恢复",
                "mcp_pass_rate",
                "tests/test_mcp_control_plane.py",
                "tests/test_agent_runtime_extension_ports.py",
            ),
        ),
        blocking=True,
    ),
    HarnessSuiteDescriptor(
        registry_id="offline_governance_recovery",
        title="权限、恢复、成本与长任务门禁",
        lane=EvaluationLane.OFFLINE_DETERMINISTIC,
        authority=EvidenceAuthority.BLOCKING_GATE,
        domains=("permission", "recovery", "cost", "long_task"),
        checks=(
            _check(
                "permission_and_cost",
                "权限、预算、token 和成本上限",
                "permission_cost_pass_rate",
                "tests/test_runtime_governance.py",
                "tests/test_provider_runtime_evidence.py",
            ),
            _check(
                "checkpoint_recovery",
                "Checkpoint、回执、回放和恢复幂等",
                "recovery_pass_rate",
                "tests/test_run_recovery.py",
                "tests/test_deterministic_replay.py",
            ),
            _check(
                "durable_long_task",
                "长期任务 lease、deadline、取消和 SLO",
                "long_task_pass_rate",
                "tests/test_durable_tasks.py",
                "tests/test_task_slo.py",
            ),
        ),
        blocking=True,
    ),
    HarnessSuiteDescriptor(
        registry_id="offline_multi_agent",
        title="多 Agent 完成率、协作成本与失败传播门禁",
        lane=EvaluationLane.OFFLINE_DETERMINISTIC,
        authority=EvidenceAuthority.BLOCKING_GATE,
        domains=("completion_rate", "collaboration_cost", "failure_propagation"),
        checks=(
            _check(
                "multi_agent_completion",
                "DAG 完成、预算和取消终态",
                "multi_agent_completion_rate",
                "tests/test_agent_orchestration.py",
            ),
            _check(
                "collaboration_cost",
                "Runtime 子任务用量与父预算归集",
                "collaboration_cost_gate_pass_rate",
                "tests/test_agent_orchestration_runtime.py",
            ),
            _check(
                "failure_propagation",
                "失败屏障、重试治理和计划修复",
                "failure_propagation_pass_rate",
                "tests/test_agent_orchestration_governance.py",
            ),
        ),
        blocking=True,
    ),
    HarnessSuiteDescriptor(
        registry_id="real_model_end_to_end",
        title="真实模型端到端 Benchmark 证据",
        lane=EvaluationLane.REAL_MODEL_BENCHMARK,
        authority=EvidenceAuthority.BENCHMARK_ONLY,
        domains=("quality", "completion_rate", "token_cost", "latency"),
        evidence_metrics=(
            EvidenceMetricContract("case_count", "integer", minimum=1),
            EvidenceMetricContract("completion_rate", "rate", minimum=0),
            EvidenceMetricContract("quality_score", "rate", minimum=0),
            EvidenceMetricContract("input_tokens", "integer", minimum=0),
            EvidenceMetricContract("output_tokens", "integer", minimum=0),
            EvidenceMetricContract("p95_latency_ms", "number", minimum=0),
        ),
        allows_network=True,
        allows_model_calls=True,
        explicit_opt_in_required=True,
        cost_budget_required=True,
    ),
    HarnessSuiteDescriptor(
        registry_id="online_readonly_runtime_sample",
        title="线上只读 Runtime 采样证据",
        lane=EvaluationLane.ONLINE_READONLY_SAMPLING,
        authority=EvidenceAuthority.READONLY_SIGNAL,
        domains=("runtime_health", "recovery", "cache", "cost"),
        evidence_metrics=(
            EvidenceMetricContract("sample_count", "integer", minimum=1),
            EvidenceMetricContract("success_rate", "rate", minimum=0),
            EvidenceMetricContract("recovery_rate", "rate", minimum=0),
            EvidenceMetricContract("cache_hit_rate", "rate", minimum=0),
            EvidenceMetricContract(
                "average_cost_microunits",
                "number",
                minimum=0,
            ),
        ),
        production_data_mode="readonly",
    ),
)


def _build_registry() -> RegistrySnapshot[HarnessSuiteDescriptor]:
    generation = RegistryGeneration[HarnessSuiteDescriptor]("eval_harness")

    def configure(builder: RegistryBuilder[HarnessSuiteDescriptor]) -> None:
        for descriptor in _SUITES:
            builder.register(descriptor)

    return generation.rebuild(configure)


EVAL_HARNESS_REGISTRY = _build_registry()


def harness_catalog_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "registry": {
            "namespace": EVAL_HARNESS_REGISTRY.namespace,
            "generation": EVAL_HARNESS_REGISTRY.generation,
            "sha256": EVAL_HARNESS_REGISTRY.sha256,
        },
        "lane_authority": {
            EvaluationLane.OFFLINE_DETERMINISTIC.value: (
                EvidenceAuthority.BLOCKING_GATE.value
            ),
            EvaluationLane.REAL_MODEL_BENCHMARK.value: (
                EvidenceAuthority.BENCHMARK_ONLY.value
            ),
            EvaluationLane.ONLINE_READONLY_SAMPLING.value: (
                EvidenceAuthority.READONLY_SIGNAL.value
            ),
        },
        "suites": [
            descriptor.to_dict()
            for descriptor in EVAL_HARNESS_REGISTRY
        ],
    }


__all__ = [
    "EVAL_HARNESS_REGISTRY",
    "EvaluationLane",
    "EvidenceAuthority",
    "EvidenceMetricContract",
    "HarnessCheckDescriptor",
    "HarnessSuiteDescriptor",
    "harness_catalog_payload",
]

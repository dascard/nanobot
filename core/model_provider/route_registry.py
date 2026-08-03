"""模型业务路由的代码所有、冻结 Descriptor Registry。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from core.lifecycle import (
    COMPATIBILITY_REGISTRY,
    CompatibilityKind,
    CompatibilityTombstoneBehavior,
    resolve_compatibility_alias,
)
from core.model_provider.contracts import ProviderCapability
from core.registry import RegistryBuilder, RegistrySnapshot


class ModelRouteError(RuntimeError):
    """模型路由 Registry 的稳定错误基类。"""


class ModelRouteNotFoundError(ModelRouteError, LookupError):
    """请求了未登记的模型业务路由。"""


class ModelRouteLifecycle(StrEnum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class ModelRouteSloStatus(StrEnum):
    BASELINE_ONLY = "baseline_only"
    FROZEN = "frozen"


class ModelRouteExecutionMode(StrEnum):
    CHAT_COMPLETION = "chat_completion"
    ROUTE_COMPLETION = "route_completion"


@dataclass(frozen=True, slots=True)
class ModelRouteSlo:
    """路由预算声明；未冻结预算只能观察，不能作为启用新行为的依据。"""

    status: ModelRouteSloStatus
    baseline_artifact: str
    task_slo_descriptor_id: str = "model_route_slo.baseline.v1"
    p50_latency_ms: int | None = None
    p95_latency_ms: int | None = None
    p99_latency_ms: int | None = None
    max_calls_per_request: int = 1
    daily_call_limit: int | None = None
    input_token_limit: int | None = None
    output_token_limit: int | None = None
    daily_cost_limit: float | None = None

    def __post_init__(self) -> None:
        if not self.baseline_artifact.strip():
            raise ValueError("ModelRouteSlo 必须声明 baseline_artifact")
        if not str(self.task_slo_descriptor_id or "").strip():
            raise ValueError(
                "ModelRouteSlo 必须声明 task_slo_descriptor_id"
            )
        if self.max_calls_per_request <= 0:
            raise ValueError("max_calls_per_request 必须大于 0")
        latency = (
            self.p50_latency_ms,
            self.p95_latency_ms,
            self.p99_latency_ms,
        )
        if self.status is ModelRouteSloStatus.FROZEN:
            if any(value is None for value in latency):
                raise ValueError("冻结 SLO 必须声明 P50/P95/P99")
            p50, p95, p99 = latency
            if not (0 < int(p50) <= int(p95) <= int(p99)):
                raise ValueError("SLO 延迟预算必须满足 0 < P50 <= P95 <= P99")
        elif any(value is not None for value in latency):
            raise ValueError("baseline_only SLO 不能伪装成已冻结延迟预算")

    def metadata(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "baseline_artifact": self.baseline_artifact,
            "task_slo_descriptor_id": self.task_slo_descriptor_id,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "p99_latency_ms": self.p99_latency_ms,
            "max_calls_per_request": self.max_calls_per_request,
            "daily_call_limit": self.daily_call_limit,
            "input_token_limit": self.input_token_limit,
            "output_token_limit": self.output_token_limit,
            "daily_cost_limit": self.daily_cost_limit,
        }


_DEFAULT_SLO_BASELINE = (
    "docs/architecture/semantic-task-performance-baseline.json"
)


def _baseline_slo(
    *,
    output_token_limit: int | None,
    task_slo_descriptor_id: str,
) -> ModelRouteSlo:
    return ModelRouteSlo(
        status=ModelRouteSloStatus.BASELINE_ONLY,
        baseline_artifact=_DEFAULT_SLO_BASELINE,
        task_slo_descriptor_id=task_slo_descriptor_id,
        output_token_limit=output_token_limit,
    )


@dataclass(frozen=True, slots=True)
class ModelRouteDescriptor:
    route_key: str
    label: str
    route_type: str
    domain: str
    owner: str
    required_provider_capabilities: frozenset[ProviderCapability]
    default_provider_id: str
    candidate_policy_id: str
    setting_prefix: str
    model_setting_key: str
    model_fallback_setting_key: str | None
    inherits_from: str | None
    inherit_thinking_when_unset: bool
    fallback_route: str | None
    fallback_scope: str
    default_timeout_seconds: float
    default_temperature: float
    default_max_tokens: int
    default_enable_thinking: str
    circuit_breaker_policy_id: str
    task_contract_keys: tuple[str, ...]
    runtime_task_key: str | None
    output_contract_id: str
    trace_policy_id: str
    trace_source: str
    lifecycle: ModelRouteLifecycle
    execution_mode: ModelRouteExecutionMode
    slo: ModelRouteSlo
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        required_text = {
            "route_key": self.route_key,
            "label": self.label,
            "route_type": self.route_type,
            "domain": self.domain,
            "owner": self.owner,
            "candidate_policy_id": self.candidate_policy_id,
            "setting_prefix": self.setting_prefix,
            "model_setting_key": self.model_setting_key,
            "fallback_scope": self.fallback_scope,
            "circuit_breaker_policy_id": self.circuit_breaker_policy_id,
            "output_contract_id": self.output_contract_id,
            "trace_policy_id": self.trace_policy_id,
            "trace_source": self.trace_source,
        }
        for field_name, value in required_text.items():
            if not str(value or "").strip():
                raise ValueError(
                    f"ModelRouteDescriptor.{field_name} 不能为空"
                )
        if self.route_type not in {
            "controller",
            "classifier",
            "task",
            "vision",
        }:
            raise ValueError(f"非法 route_type: {self.route_type}")
        if not self.required_provider_capabilities:
            raise ValueError("模型路由必须声明 Provider capability")
        if self.default_timeout_seconds <= 0:
            raise ValueError("模型路由 timeout 必须大于 0")
        if not 0 <= self.default_temperature <= 2:
            raise ValueError("模型路由 temperature 必须位于 [0, 2]")
        if self.default_max_tokens < 0:
            raise ValueError("模型路由 max_tokens 不能小于 0")
        if self.default_enable_thinking not in {"auto", "true", "false"}:
            raise ValueError("模型路由 enable_thinking 默认值无效")
        if self.runtime_task_key is not None:
            if self.runtime_task_key not in self.task_contract_keys:
                raise ValueError(
                    "runtime_task_key 必须包含在 task_contract_keys 中"
                )
        if self.inherit_thinking_when_unset and self.inherits_from is None:
            raise ValueError(
                "inherit_thinking_when_unset 需要 inherits_from"
            )
        if self.fallback_route is None and self.fallback_scope != "none":
            raise ValueError("没有 fallback_route 时 fallback_scope 必须为 none")
        if (
            self.fallback_route is not None
            and self.fallback_scope not in {"model_only", "full_route"}
        ):
            raise ValueError("fallback_scope 只允许 model_only/full_route")
        if len(set(self.aliases)) != len(self.aliases):
            raise ValueError(f"模型路由 {self.route_key} 包含重复 alias")

    @property
    def registry_namespace(self) -> str:
        return "model_route"

    @property
    def registry_id(self) -> str:
        return self.route_key

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        dependencies = {
            value
            for value in (self.inherits_from, self.fallback_route)
            if value and value != self.route_key
        }
        return tuple(sorted(dependencies))

    def registry_payload(self) -> Mapping[str, object]:
        return self.metadata()

    def metadata(self) -> dict[str, object]:
        return {
            "route_key": self.route_key,
            "label": self.label,
            "route_type": self.route_type,
            "domain": self.domain,
            "owner": self.owner,
            "required_provider_capabilities": sorted(
                capability.value
                for capability in self.required_provider_capabilities
            ),
            "default_provider_id": self.default_provider_id,
            "candidate_policy_id": self.candidate_policy_id,
            "setting_prefix": self.setting_prefix,
            "model_setting_key": self.model_setting_key,
            "model_fallback_setting_key": (
                self.model_fallback_setting_key
            ),
            "inherits_from": self.inherits_from,
            "inherit_thinking_when_unset": (
                self.inherit_thinking_when_unset
            ),
            "fallback_route": self.fallback_route,
            "fallback_scope": self.fallback_scope,
            "defaults": {
                "timeout_seconds": self.default_timeout_seconds,
                "temperature": self.default_temperature,
                "max_tokens": self.default_max_tokens,
                "enable_thinking": self.default_enable_thinking,
            },
            "circuit_breaker_policy_id": self.circuit_breaker_policy_id,
            "task_contract_keys": list(self.task_contract_keys),
            "runtime_task_key": self.runtime_task_key,
            "output_contract_id": self.output_contract_id,
            "trace_policy_id": self.trace_policy_id,
            "trace_source": self.trace_source,
            "lifecycle": self.lifecycle.value,
            "execution_mode": self.execution_mode.value,
            "slo": self.slo.metadata(),
            "aliases": list(self.aliases),
        }


class ModelRouteDescriptorRegistry:
    """构造完成即冻结；route alias 由 Compatibility Registry 提供。"""

    def __init__(
        self,
        descriptors: tuple[ModelRouteDescriptor, ...],
    ) -> None:
        builder = RegistryBuilder[ModelRouteDescriptor]("model_route")
        route_keys = {descriptor.route_key for descriptor in descriptors}
        for descriptor in descriptors:
            builder.register(descriptor)
            if descriptor.aliases:
                raise ValueError(
                    f"模型路由 {descriptor.route_key} 不能私有维护 alias，"
                    "请登记到 Compatibility Registry"
                )
        self._snapshot = builder.freeze()
        aliases: dict[str, str] = {}
        for compatibility in COMPATIBILITY_REGISTRY.descriptors(
            CompatibilityKind.ROUTE
        ):
            if (
                compatibility.tombstone_behavior
                is not CompatibilityTombstoneBehavior.FORWARD
            ):
                continue
            alias = compatibility.alias_value
            canonical = compatibility.canonical_replacement
            if alias in route_keys or canonical not in route_keys:
                raise ValueError(
                    f"模型路由兼容 alias 无法安全转发: {alias!r}"
                )
            if alias in aliases:
                raise ValueError(f"模型路由 alias 冲突: {alias!r}")
            aliases[alias] = canonical
        self._aliases = MappingProxyType(aliases)

    @property
    def registry_snapshot(
        self,
    ) -> RegistrySnapshot[ModelRouteDescriptor]:
        return self._snapshot

    def resolve_key(self, route_key: str) -> str:
        normalized = str(route_key or "").strip()
        resolution = resolve_compatibility_alias(
            CompatibilityKind.ROUTE,
            normalized,
        )
        if normalized in self._snapshot.items:
            canonical = normalized
        elif resolution is not None and (
            resolution.descriptor.tombstone_behavior
            is CompatibilityTombstoneBehavior.FORWARD
        ):
            canonical = resolution.canonical_replacement
        else:
            canonical = normalized
        if self._snapshot.get(canonical) is None:
            raise ModelRouteNotFoundError(
                f"未登记的模型路由: {normalized or '<empty>'}"
            )
        return canonical

    def get(self, route_key: str) -> ModelRouteDescriptor | None:
        try:
            canonical = self.resolve_key(route_key)
        except ModelRouteNotFoundError:
            return None
        return self._snapshot.get(canonical)

    def require(self, route_key: str) -> ModelRouteDescriptor:
        return self._snapshot.require(self.resolve_key(route_key))

    def descriptors(self) -> tuple[ModelRouteDescriptor, ...]:
        return tuple(self._snapshot)

    def aliases(self) -> Mapping[str, str]:
        return self._aliases


_CHAT_CAPABILITY = frozenset({ProviderCapability.CHAT_COMPLETION})
_VISION_CAPABILITY = frozenset(
    {
        ProviderCapability.CHAT_COMPLETION,
        ProviderCapability.VISION,
    }
)


def _descriptor(
    route_key: str,
    *,
    label: str,
    route_type: str,
    domain: str,
    owner: str,
    default_provider_id: str,
    model_setting_key: str,
    timeout: float,
    temperature: float,
    max_tokens: int,
    enable_thinking: str,
    output_contract_id: str,
    trace_source: str,
    inherits_from: str | None = None,
    inherit_thinking_when_unset: bool = False,
    fallback_route: str | None = None,
    fallback_scope: str = "none",
    model_fallback_setting_key: str | None = None,
    task_contract_keys: tuple[str, ...] = (),
    runtime_task_key: str | None = None,
    lifecycle: ModelRouteLifecycle = ModelRouteLifecycle.ACTIVE,
    execution_mode: ModelRouteExecutionMode = (
        ModelRouteExecutionMode.ROUTE_COMPLETION
    ),
    capabilities: frozenset[ProviderCapability] = _CHAT_CAPABILITY,
    aliases: tuple[str, ...] = (),
    task_slo_descriptor_id: str = "model_route_slo.baseline.v1",
) -> ModelRouteDescriptor:
    return ModelRouteDescriptor(
        route_key=route_key,
        label=label,
        route_type=route_type,
        domain=domain,
        owner=owner,
        required_provider_capabilities=capabilities,
        default_provider_id=default_provider_id,
        candidate_policy_id="configured_provider_then_model_catalog",
        setting_prefix=f"model.route.{route_key}",
        model_setting_key=model_setting_key,
        model_fallback_setting_key=model_fallback_setting_key,
        inherits_from=inherits_from,
        inherit_thinking_when_unset=inherit_thinking_when_unset,
        fallback_route=fallback_route,
        fallback_scope=fallback_scope,
        default_timeout_seconds=timeout,
        default_temperature=temperature,
        default_max_tokens=max_tokens,
        default_enable_thinking=enable_thinking,
        circuit_breaker_policy_id="model_failure_tracker.default",
        task_contract_keys=task_contract_keys,
        runtime_task_key=runtime_task_key,
        output_contract_id=output_contract_id,
        trace_policy_id="model_route.metadata_only",
        trace_source=trace_source,
        lifecycle=lifecycle,
        execution_mode=execution_mode,
        slo=_baseline_slo(
            output_token_limit=max_tokens if max_tokens > 0 else None,
            task_slo_descriptor_id=task_slo_descriptor_id,
        ),
        aliases=aliases,
    )


_MODEL_ROUTE_DESCRIPTORS = (
    _descriptor(
        "reply",
        label="主回复模型",
        route_type="controller",
        domain="chat",
        owner="nanobot_kt.model_runtime",
        default_provider_id="newapi",
        model_setting_key="model.reply",
        timeout=120,
        temperature=0.7,
        max_tokens=0,
        enable_thinking="true",
        output_contract_id="agent_reply_v1",
        trace_source="chat.reply",
        execution_mode=ModelRouteExecutionMode.CHAT_COMPLETION,
    ),
    _descriptor(
        "fast",
        label="快速模型（预留）",
        route_type="controller",
        domain="model_routing",
        owner="core.model_provider",
        default_provider_id="newapi",
        model_setting_key="model.fast",
        timeout=120,
        temperature=0.7,
        max_tokens=0,
        enable_thinking="auto",
        output_contract_id="text_v1",
        trace_source="model_route.fast",
        execution_mode=ModelRouteExecutionMode.CHAT_COMPLETION,
    ),
    _descriptor(
        "smart",
        label="智能模型（预留）",
        route_type="controller",
        domain="model_routing",
        owner="core.model_provider",
        default_provider_id="newapi",
        model_setting_key="model.smart",
        timeout=120,
        temperature=0.7,
        max_tokens=0,
        enable_thinking="auto",
        output_contract_id="text_v1",
        trace_source="model_route.smart",
        execution_mode=ModelRouteExecutionMode.CHAT_COMPLETION,
    ),
    _descriptor(
        "timing_gate",
        label="TimingGate 分类器",
        route_type="classifier",
        domain="reply_timing",
        owner="core.private_timing",
        default_provider_id="local_llama",
        model_setting_key="model.route.timing_gate.model",
        timeout=15,
        temperature=0,
        max_tokens=30,
        enable_thinking="auto",
        task_contract_keys=("tasks/timing_gate",),
        runtime_task_key="tasks/timing_gate",
        output_contract_id="timing_gate_v1",
        trace_source="classifier.timing_gate",
        task_slo_descriptor_id="task_slo.timing_gate.v1",
    ),
    _descriptor(
        "timing_proactive",
        label="主动发言裁判",
        route_type="classifier",
        domain="reply_timing",
        owner="core.private_timing",
        default_provider_id="",
        model_setting_key="model.route.timing_proactive.model",
        timeout=30,
        temperature=0,
        max_tokens=65536,
        enable_thinking="true",
        inherits_from="reply",
        task_contract_keys=("tasks/timing_proactive",),
        runtime_task_key="tasks/timing_proactive",
        output_contract_id="timing_proactive_v1",
        trace_source="classifier.timing_proactive",
    ),
    _descriptor(
        "outreach_extract",
        label="主动外呼话题提炼",
        route_type="classifier",
        domain="proactive_outreach",
        owner="core.proactive",
        default_provider_id="",
        model_setting_key="model.route.outreach_extract.model",
        timeout=30,
        temperature=0,
        max_tokens=65536,
        enable_thinking="true",
        inherits_from="reply",
        task_contract_keys=("tasks/outreach_extract",),
        runtime_task_key="tasks/outreach_extract",
        output_contract_id="outreach_threads_v2",
        trace_source="classifier.outreach_extract",
    ),
    _descriptor(
        "outreach_judge",
        label="主动外呼决策",
        route_type="classifier",
        domain="proactive_outreach",
        owner="core.proactive",
        default_provider_id="",
        model_setting_key="model.route.outreach_judge.model",
        timeout=45,
        temperature=0,
        max_tokens=65536,
        enable_thinking="true",
        inherits_from="reply",
        task_contract_keys=("tasks/outreach_judge",),
        runtime_task_key="tasks/outreach_judge",
        output_contract_id="outreach_judge_v2",
        trace_source="classifier.outreach_judge",
    ),
    _descriptor(
        "outreach_generate",
        label="主动外呼生成",
        route_type="classifier",
        domain="proactive_outreach",
        owner="core.proactive",
        default_provider_id="",
        model_setting_key="model.route.outreach_generate.model",
        timeout=60,
        temperature=0.7,
        max_tokens=65536,
        enable_thinking="true",
        inherits_from="reply",
        task_contract_keys=("tasks/outreach_generate",),
        runtime_task_key="tasks/outreach_generate",
        output_contract_id="outreach_message_v1",
        trace_source="classifier.outreach_generate",
    ),
    _descriptor(
        "outreach_quality",
        label="主动外呼质量复核",
        route_type="classifier",
        domain="proactive_outreach",
        owner="core.proactive",
        default_provider_id="",
        model_setting_key="model.route.outreach_quality.model",
        timeout=30,
        temperature=0,
        max_tokens=4096,
        enable_thinking="true",
        inherits_from="reply",
        task_contract_keys=("tasks/outreach_quality",),
        runtime_task_key="tasks/outreach_quality",
        output_contract_id="outreach_quality_v1",
        trace_source="classifier.outreach_quality",
    ),
    _descriptor(
        "news_daily_quality",
        label="AI 日报质量摘要",
        route_type="task",
        domain="news_daily",
        owner="creatures.nanobot.news_daily",
        default_provider_id="",
        model_setting_key="model.route.news_daily_quality.model",
        timeout=20,
        temperature=0.1,
        max_tokens=3200,
        enable_thinking="false",
        inherits_from="reply",
        task_contract_keys=("tasks/news_daily_quality",),
        runtime_task_key="tasks/news_daily_quality",
        output_contract_id="news_quality_summary_v1",
        trace_source="news_daily.summarize_quality",
        task_slo_descriptor_id="task_slo.news_daily_quality.v1",
    ),
    _descriptor(
        "news_relevance_review",
        label="新闻相关性批量审核",
        route_type="task",
        domain="news_relevance",
        owner="core.news",
        default_provider_id="",
        model_setting_key="model.route.news_relevance_review.model",
        timeout=30,
        temperature=0.0,
        max_tokens=2000,
        enable_thinking="false",
        inherits_from="reply",
        task_contract_keys=("tasks/news_relevance_review",),
        runtime_task_key="tasks/news_relevance_review",
        output_contract_id="news_relevance_review_v1",
        trace_source="news.relevance_review",
        task_slo_descriptor_id="task_slo.news_relevance_review.v1",
    ),
    _descriptor(
        "group_analysis_topics",
        label="群分析话题提取",
        route_type="task",
        domain="group_analysis",
        owner="app.group_analysis",
        default_provider_id="",
        model_setting_key="model.route.group_analysis_topics.model",
        timeout=60,
        temperature=0.3,
        max_tokens=2048,
        enable_thinking="false",
        inherits_from="reply",
        task_contract_keys=("tasks/group_analysis_topics",),
        runtime_task_key="tasks/group_analysis_topics",
        output_contract_id="group_analysis_topics_v1",
        trace_source="group_analysis.topics",
        task_slo_descriptor_id="task_slo.group_analysis_topics.v1",
    ),
    _descriptor(
        "group_analysis_titles",
        label="群分析用户称号",
        route_type="task",
        domain="group_analysis",
        owner="app.group_analysis",
        default_provider_id="",
        model_setting_key="model.route.group_analysis_titles.model",
        timeout=60,
        temperature=0.3,
        max_tokens=2048,
        enable_thinking="false",
        inherits_from="reply",
        task_contract_keys=("tasks/group_analysis_titles",),
        runtime_task_key="tasks/group_analysis_titles",
        output_contract_id="group_analysis_titles_v1",
        trace_source="group_analysis.titles",
        task_slo_descriptor_id="task_slo.group_analysis_titles.v1",
    ),
    _descriptor(
        "group_analysis_quotes",
        label="群分析金句提取",
        route_type="task",
        domain="group_analysis",
        owner="app.group_analysis",
        default_provider_id="",
        model_setting_key="model.route.group_analysis_quotes.model",
        timeout=60,
        temperature=0.2,
        max_tokens=1536,
        enable_thinking="false",
        inherits_from="reply",
        task_contract_keys=("tasks/group_analysis_quotes",),
        runtime_task_key="tasks/group_analysis_quotes",
        output_contract_id="group_analysis_quotes_v1",
        trace_source="group_analysis.quotes",
        task_slo_descriptor_id="task_slo.group_analysis_quotes.v1",
    ),
    _descriptor(
        "group_analysis_quality",
        label="群分析质量锐评",
        route_type="task",
        domain="group_analysis",
        owner="app.group_analysis",
        default_provider_id="",
        model_setting_key="model.route.group_analysis_quality.model",
        timeout=60,
        temperature=0.3,
        max_tokens=2048,
        enable_thinking="false",
        inherits_from="reply",
        task_contract_keys=("tasks/group_analysis_quality",),
        runtime_task_key="tasks/group_analysis_quality",
        output_contract_id="group_analysis_quality_v1",
        trace_source="group_analysis.quality",
        task_slo_descriptor_id="task_slo.group_analysis_quality.v1",
    ),
    _descriptor(
        "group_memory_learning",
        label="群记忆候选审核",
        route_type="task",
        domain="group_memory_learning",
        owner="app.group_memory",
        default_provider_id="",
        model_setting_key="model.route.group_memory_learning.model",
        timeout=90,
        temperature=0.1,
        max_tokens=4096,
        enable_thinking="false",
        inherits_from="reply",
        task_contract_keys=("tasks/group_memory_learning",),
        runtime_task_key="tasks/group_memory_learning",
        output_contract_id="group_memory_learning_v1",
        trace_source="group_memory.learning",
        task_slo_descriptor_id="task_slo.group_memory_learning.v1",
    ),
    _descriptor(
        "private_decision",
        label="私聊决策分类器",
        route_type="classifier",
        domain="reply_timing",
        owner="core.private_timing",
        default_provider_id="",
        model_setting_key="model.route.private_decision.model",
        timeout=15,
        temperature=0,
        max_tokens=120,
        enable_thinking="auto",
        inherits_from="timing_gate",
        inherit_thinking_when_unset=True,
        task_contract_keys=("tasks/private_decision",),
        runtime_task_key="tasks/private_decision",
        output_contract_id="private_decision_v2",
        trace_source="classifier.private_decision",
        task_slo_descriptor_id="task_slo.private_decision.v1",
    ),
    _descriptor(
        "classifier_legacy",
        label="旧分类器",
        route_type="classifier",
        domain="compatibility",
        owner="clients.classifier_client",
        default_provider_id="",
        model_setting_key="model.route.classifier_legacy.model",
        timeout=15,
        temperature=0,
        max_tokens=30,
        enable_thinking="auto",
        inherits_from="timing_gate",
        inherit_thinking_when_unset=True,
        task_contract_keys=("tasks/classifier_legacy",),
        runtime_task_key="tasks/classifier_legacy",
        output_contract_id="legacy_reply_v1",
        trace_source="classifier.classifier_legacy",
        lifecycle=ModelRouteLifecycle.DEPRECATED,
    ),
    _descriptor(
        "sticker_describe",
        label="表情包打标",
        route_type="vision",
        domain="sticker",
        owner="creatures.nanobot.image_summary",
        default_provider_id="local_llama",
        model_setting_key="model.route.sticker_describe.model",
        timeout=15,
        temperature=0,
        max_tokens=256,
        enable_thinking="false",
        output_contract_id="sticker_description_v1",
        trace_source="classifier.sticker_describe",
        capabilities=_VISION_CAPABILITY,
    ),
    _descriptor(
        "session_summary",
        label="近期摘要",
        route_type="controller",
        domain="session_memory",
        owner="app.session_memory",
        default_provider_id="newapi",
        model_setting_key="model.session_summary",
        model_fallback_setting_key="model.fast",
        timeout=120,
        temperature=0.1,
        max_tokens=4096,
        enable_thinking="false",
        fallback_route="fast",
        fallback_scope="model_only",
        task_contract_keys=(
            "tasks/session_summary_system",
            "tasks/session_summary_output",
        ),
        output_contract_id="session_summary_v1",
        trace_source="session_summary",
        execution_mode=ModelRouteExecutionMode.CHAT_COMPLETION,
    ),
    _descriptor(
        "memory_digest",
        label="长期摘要",
        route_type="controller",
        domain="memory_digest",
        owner="app.memory_digest",
        default_provider_id="newapi",
        model_setting_key="model.memory_digest",
        model_fallback_setting_key="model.smart",
        timeout=180,
        temperature=0.1,
        max_tokens=8192,
        enable_thinking="false",
        fallback_route="smart",
        fallback_scope="model_only",
        task_contract_keys=(
            "tasks/memory_digest_system",
            "tasks/memory_digest_user",
        ),
        output_contract_id="memory_digest_v2",
        trace_source="memory_digest",
        execution_mode=ModelRouteExecutionMode.CHAT_COMPLETION,
    ),
)


MODEL_ROUTE_REGISTRY = ModelRouteDescriptorRegistry(
    _MODEL_ROUTE_DESCRIPTORS
)


def model_route_registry_snapshot(
) -> RegistrySnapshot[ModelRouteDescriptor]:
    return MODEL_ROUTE_REGISTRY.registry_snapshot


def list_model_route_descriptors() -> tuple[ModelRouteDescriptor, ...]:
    return MODEL_ROUTE_REGISTRY.descriptors()


def get_model_route_descriptor(
    route_key: str,
) -> ModelRouteDescriptor | None:
    return MODEL_ROUTE_REGISTRY.get(route_key)


def require_model_route_descriptor(
    route_key: str,
) -> ModelRouteDescriptor:
    return MODEL_ROUTE_REGISTRY.require(route_key)


def resolve_model_route_key(route_key: str) -> str:
    return MODEL_ROUTE_REGISTRY.resolve_key(route_key)


def model_route_registry_metadata() -> dict[str, Any]:
    snapshot = model_route_registry_snapshot()
    return {
        "generation": snapshot.generation,
        "sha256": snapshot.sha256,
        "routes": [
            descriptor.metadata()
            for descriptor in list_model_route_descriptors()
        ],
        "aliases": dict(MODEL_ROUTE_REGISTRY.aliases()),
    }


def validate_model_route_task_contracts() -> None:
    """启动期验证 Route 声明的 Task／Output Contract 没有漂移。"""

    from core.prompt_v2.task_contracts import get_task_contract

    for descriptor in list_model_route_descriptors():
        for task_key in descriptor.task_contract_keys:
            contract = get_task_contract(task_key)
            if contract is None:
                raise ValueError(
                    f"模型路由 {descriptor.route_key} 引用了未登记 Task: "
                    f"{task_key}"
                )
            if contract.output_contract_id != descriptor.output_contract_id:
                raise ValueError(
                    f"模型路由 {descriptor.route_key} 的 Output Contract 漂移: "
                    f"{task_key}={contract.output_contract_id}, "
                    f"route={descriptor.output_contract_id}"
                )


__all__ = [
    "MODEL_ROUTE_REGISTRY",
    "ModelRouteDescriptor",
    "ModelRouteDescriptorRegistry",
    "ModelRouteError",
    "ModelRouteExecutionMode",
    "ModelRouteLifecycle",
    "ModelRouteNotFoundError",
    "ModelRouteSlo",
    "ModelRouteSloStatus",
    "get_model_route_descriptor",
    "list_model_route_descriptors",
    "model_route_registry_metadata",
    "model_route_registry_snapshot",
    "require_model_route_descriptor",
    "resolve_model_route_key",
    "validate_model_route_task_contracts",
]

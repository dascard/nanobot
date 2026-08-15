"""代码所有、冻结的自检 Probe 清单。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from core.registry import RegistryBuilder, RegistrySnapshot
from core.registry.validation import validate_identifier


ProbeLevel = Literal["structural", "functional", "operational", "quality"]
ProbeSeverity = Literal["critical", "high", "medium", "low"]


@dataclass(frozen=True, slots=True)
class SelfcheckProbeDescriptor:
    check_id: str
    category: str
    label: str
    level: ProbeLevel
    severity: ProbeSeverity
    executor_key: str
    timeout_seconds: float
    environments: tuple[str, ...] = ("ci", "staging", "production")
    capability_kinds: tuple[str, ...] = ()
    capability_source_ids: tuple[str, ...] = ()
    destructive: bool = False
    requires_model: bool = False

    def __post_init__(self) -> None:
        validate_identifier(self.check_id, field_name="probe.check_id")
        validate_identifier(self.category, field_name="probe.category")
        validate_identifier(self.executor_key, field_name="probe.executor_key")
        if not self.label.strip() or len(self.label) > 256:
            raise ValueError("probe.label 非法")
        if self.level not in {"structural", "functional", "operational", "quality"}:
            raise ValueError("probe.level 非法")
        if self.severity not in {"critical", "high", "medium", "low"}:
            raise ValueError("probe.severity 非法")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 300:
            raise ValueError("probe.timeout_seconds 非法")
        if not self.environments or len(self.environments) != len(set(self.environments)):
            raise ValueError("probe.environments 非法")
        if self.destructive:
            raise ValueError("默认自检 Probe 禁止破坏性操作")

    @property
    def registry_namespace(self) -> str:
        return "selfcheck_probe"

    @property
    def registry_id(self) -> str:
        return self.check_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "category": self.category,
            "label": self.label,
            "level": self.level,
            "severity": self.severity,
            "executor_key": self.executor_key,
            "timeout_seconds": self.timeout_seconds,
            "environments": list(self.environments),
            "capability_kinds": list(self.capability_kinds),
            "capability_source_ids": list(self.capability_source_ids),
            "destructive": self.destructive,
            "requires_model": self.requires_model,
        }


def _probe(
    check_id: str,
    category: str,
    label: str,
    *,
    level: ProbeLevel = "operational",
    severity: ProbeSeverity = "high",
    executor_key: str | None = None,
    capability_kinds: tuple[str, ...] = (),
    capability_source_ids: tuple[str, ...] = (),
    timeout_seconds: float = 10.0,
    requires_model: bool = False,
) -> SelfcheckProbeDescriptor:
    return SelfcheckProbeDescriptor(
        check_id=check_id,
        category=category,
        label=label,
        level=level,
        severity=severity,
        executor_key=executor_key or check_id,
        timeout_seconds=timeout_seconds,
        capability_kinds=capability_kinds,
        capability_source_ids=capability_source_ids,
        requires_model=requires_model,
    )


_PROBES = (
    _probe("registry.capability_integrity", "registry", "能力清单完整性", level="structural", severity="critical"),
    _probe("api.openapi_contracts", "api", "OpenAPI 操作与合同完整性", level="structural", severity="critical", capability_kinds=("api",)),
    _probe("webui.route_manifest", "webui", "WebUI 路由清单漂移", level="structural", capability_kinds=("webui",)),
    _probe(
        "webui.critical-operation-bindings",
        "webui",
        "关键 WebUI 页面与后端 Operation 绑定",
        level="functional",
        severity="critical",
        capability_kinds=("webui",),
        capability_source_ids=("/rag-debug", "/self-check"),
    ),
    _probe("agent.runtime_registry", "agent", "Agent Runtime 注册表", level="structural", severity="critical", capability_kinds=("agent",)),
    _probe(
        "agent.routing.functional",
        "agent",
        "Agent 默认路由与入口门禁",
        level="functional",
        severity="critical",
        executor_key="scenario.functional",
        capability_kinds=("agent",),
    ),
    _probe(
        "agent.a2a-routing.functional",
        "agent",
        "Agent Link 与 A2A 路由",
        level="functional",
        severity="high",
        executor_key="scenario.functional",
        capability_kinds=("agent",),
    ),
    _probe("tool.registration_registry", "tool", "工具注册表与执行绑定", level="structural", capability_kinds=("tool",)),
    _probe(
        "tool.runtime-bindings.functional",
        "tool",
        "工具 Schema 与 Native／KT 运行绑定",
        level="functional",
        severity="critical",
        executor_key="scenario.functional",
        capability_kinds=("tool",),
    ),
    _probe("model.route_registry", "model", "模型业务路由注册表", level="structural", severity="critical", capability_kinds=("model_route",)),
    _probe(
        "model.route-configuration.functional",
        "model",
        "模型 Runtime 与有效 Route 配置",
        level="functional",
        severity="critical",
        executor_key="scenario.functional",
        capability_kinds=("model_route",),
    ),
    _probe(
        "model.reply-canary.functional",
        "model",
        "reply Route 主动调用与语义 Canary",
        level="functional",
        severity="critical",
        timeout_seconds=30.0,
        requires_model=True,
        capability_kinds=("model_route",),
    ),
    _probe("database.connectivity", "database", "数据库连接与读事务", severity="critical", capability_kinds=("storage",), capability_source_ids=("database",)),
    _probe("database.required_schema", "database", "关键数据库表与列", level="structural", severity="critical", capability_kinds=("storage",), capability_source_ids=("database",)),
    _probe("database.integrity", "database", "数据库快速完整性与外键", severity="critical", capability_kinds=("storage",), capability_source_ids=("database",)),
    _probe("session.database_only_default", "session", "仅入库默认值", level="structural", severity="critical", capability_kinds=("integration",), capability_source_ids=("session_policy",)),
    _probe(
        "session.default-gate.functional",
        "session",
        "未配置会话仅入库运行门禁",
        level="functional",
        severity="critical",
        executor_key="scenario.functional",
        capability_kinds=("integration",),
        capability_source_ids=("session_policy",),
    ),
    _probe("prompt.runtime_templates", "prompt", "Prompt Runtime 模板完整性", level="structural", severity="critical", capability_kinds=("integration",), capability_source_ids=("prompt_runtime",)),
    *tuple(
        _probe(
            f"rag.{source.replace('_', '-')}.runtime",
            "rag",
            f"RAG {source} 运行配置",
            level="structural",
            severity="critical" if source in {"all", "group_memory", "group_analysis"} else "high",
            executor_key="rag.source_runtime",
            capability_kinds=("rag_source",),
            capability_source_ids=(source,),
        )
        for source in (
            "memory",
            "memory_digest",
            "session_summary",
            "group_memory",
            "sticker",
            "knowledge",
            "group_analysis",
            "all",
        )
    ),
    *tuple(
        _probe(
            f"rag.{source.replace('_', '-')}.smoke",
            "rag",
            f"RAG {source} 只读管线冒烟",
            level="functional",
            severity="critical"
            if source in {"all", "group_memory", "group_analysis"}
            else "high",
            executor_key="scenario.functional",
            capability_kinds=("rag_source",),
            capability_source_ids=(source,),
        )
        for source in (
            "memory",
            "memory_digest",
            "session_summary",
            "group_memory",
            "sticker",
            "knowledge",
            "group_analysis",
            "all",
        )
    ),
    _probe("rag.index_queue", "rag", "语义索引任务队列", severity="critical"),
    _probe("rag.index_quality", "rag", "语义索引条目质量", level="quality", severity="high"),
    _probe("rag.debug_history", "rag", "RAG Debug 近期结果", level="functional", severity="critical"),
    *tuple(
        _probe(
            f"worker.{worker_id}.liveness",
            "worker",
            f"{worker_id} 活性",
            severity="critical" if worker_id in {
                "session-summary-worker",
                "outbound-delivery-worker",
                "semantic-index-worker",
                "daily-digest-scheduler",
                "scheduled-task-runner",
                "proactive-outreach-scheduler",
                "selfcheck-watchdog",
            } else "high",
            executor_key="worker.liveness",
            capability_kinds=("worker",),
            capability_source_ids=(worker_id,),
        )
        for worker_id in (
            "session-summary-worker",
            "outbound-delivery-worker",
            "semantic-index-worker",
            "daily-digest-scheduler",
            "scheduled-task-runner",
            "chat-delivery-worker",
            "eval-sampling-scheduler",
            "proactive-outreach-scheduler",
            "group-learning-scheduler",
            "selfcheck-watchdog",
        )
    ),
    _probe("queue.session_summary", "queue", "会话摘要队列", severity="critical"),
    _probe("queue.outbound_delivery", "queue", "通用主动投递队列", severity="critical"),
    _probe("queue.chat_delivery", "queue", "聊天断连投递队列", severity="high"),
    _probe("schedule.daily_digest", "schedule", "记忆摘要日报生成与 fallback 比例", level="quality", severity="critical"),
    _probe(
        "schedule.task-definitions.functional",
        "schedule",
        "定时任务 Trigger、Owner 与 Program 合同",
        level="functional",
        severity="critical",
    ),
    _probe(
        "schedule.scheduled-delivery-quality",
        "schedule",
        "定时推送生成与投递质量",
        level="quality",
        severity="critical",
    ),
    _probe("schedule.proactive_outreach", "schedule", "主动外呼评估、生成与投递", level="quality", severity="critical"),
    _probe("schedule.scheduled_tasks", "schedule", "定时任务到期与执行", severity="critical"),
    _probe("observability.model_calls", "observability", "模型 API 成功率与失败分类", level="quality", severity="critical", capability_kinds=("integration",), capability_source_ids=("model_observability",)),
    _probe("observability.agent_runs", "observability", "Agent Run 终态与卡死运行", severity="high", capability_kinds=("integration",), capability_source_ids=("agent_observability",)),
    _probe("memory.summary_quality", "memory", "滚动摘要 fallback 比例", level="quality", severity="critical", capability_kinds=("integration",), capability_source_ids=("memory_quality",)),
    _probe("storage.workspace_assets", "storage", "Workspace 与不可变资产引用", severity="high", capability_kinds=("storage",), capability_source_ids=("workspace_assets",)),
    _probe("collaboration.event_integrity", "collaboration", "多 Agent 协作事件一致性", severity="high", capability_kinds=("integration",), capability_source_ids=("agent_collaboration",)),
    _probe("run_ledger.stream_integrity", "observability", "Run Ledger 流头一致性", severity="high", capability_kinds=("integration",), capability_source_ids=("run_ledger",)),
)


def _build_registry() -> RegistrySnapshot[SelfcheckProbeDescriptor]:
    builder = RegistryBuilder[SelfcheckProbeDescriptor]("selfcheck_probe")
    for descriptor in _PROBES:
        builder.register(descriptor)
    return builder.freeze()


SELFCHECK_PROBE_REGISTRY = _build_registry()


def probe_ids_for_capability(kind: str, source_id: str) -> tuple[str, ...]:
    return tuple(
        probe.check_id
        for probe in SELFCHECK_PROBE_REGISTRY
        if kind in probe.capability_kinds
        and not (
            kind in {"api", "webui", "agent", "tool", "model_route"}
            and probe.level == "structural"
        )
        and (
            not probe.capability_source_ids
            or source_id in probe.capability_source_ids
        )
    )


__all__ = [
    "SELFCHECK_PROBE_REGISTRY",
    "SelfcheckProbeDescriptor",
    "probe_ids_for_capability",
]

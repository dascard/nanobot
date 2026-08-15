"""默认无模型、无网络、无副作用的确定性自检引擎。"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import time
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session

from core.db.models import (
    AgentRun,
    ChatDeliveryOutbox,
    LLMApiRequestLog,
    MemoryDigest,
    MemoryDigestJob,
    OutboundDeliveryAttempt,
    OutboundDeliveryCircuit,
    OutboundDeliveryOutbox,
    OutboundGenerationAttempt,
    OutboundRun,
    ProactiveOutreachLog,
    RagDebugRun,
    RollingSessionSummary,
    ScheduledTask,
    ScheduledTaskExecution,
    SemanticIndexItem,
    SemanticIndexJob,
    SessionSummaryJob,
)
from core.db.models.selfcheck import (
    SelfcheckResultRow,
    SelfcheckRunRow,
    WorkerHeartbeat,
)
from core.model_provider.route_registry import list_model_route_descriptors
from core.selfcheck.capabilities import (
    CapabilityDescriptor,
    build_capability_registry,
)
from core.selfcheck.probes import (
    SELFCHECK_PROBE_REGISTRY,
    SelfcheckProbeDescriptor,
)
from core.selfcheck.runtime_diagnostics import (
    SelfcheckRuntimeDiagnosticsPort,
)
from core.settings_service import settings
from core.tool_registration import TOOL_REGISTRATION_REGISTRY


CheckStatus = Literal[
    "passed",
    "degraded",
    "failed",
    "inconclusive",
    "skipped",
]
RunStatus = Literal["passed", "degraded", "failed", "inconclusive"]
ModelCanaryRunner = Callable[[str, float], str]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SAFE_JSON_TYPES = (str, int, float, bool, type(None))
_RESULT_STATUSES = (
    "passed",
    "degraded",
    "failed",
    "inconclusive",
    "skipped",
)


def _safe_json_mapping(values: Mapping[str, object]) -> dict[str, object]:
    """只保留结构化计数、状态和时间，不让业务正文进入自检账本。"""

    safe: dict[str, object] = {}
    for raw_key, value in values.items():
        key = str(raw_key or "").strip()
        if not key or len(key) > 128:
            continue
        if isinstance(value, _SAFE_JSON_TYPES):
            safe[key] = value[:256] if isinstance(value, str) else value
        elif isinstance(value, datetime):
            safe[key] = value.isoformat()
        elif isinstance(value, (tuple, list)):
            safe[key] = [
                item[:128] if isinstance(item, str) else item
                for item in value
                if isinstance(item, _SAFE_JSON_TYPES)
            ][:100]
        elif isinstance(value, Mapping):
            safe[key] = _safe_json_mapping(value)
    return safe


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    status: CheckStatus
    detail_code: str
    message: str
    metrics: Mapping[str, object] = field(default_factory=dict)
    evidence: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in _RESULT_STATUSES:
            raise ValueError("自检结果 status 非法")
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", self.detail_code):
            raise ValueError("自检结果 detail_code 非法")
        if len(self.message) > 512:
            raise ValueError("自检结果 message 过长")


@dataclass(frozen=True, slots=True)
class SelfcheckCheckResult:
    check_id: str
    category: str
    status: CheckStatus
    severity: str
    level: str
    duration_ms: int
    detail_code: str
    message: str
    capability_ids: tuple[str, ...]
    metrics: Mapping[str, object]
    evidence: Mapping[str, object]
    started_at: datetime
    completed_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "check_id": self.check_id,
            "category": self.category,
            "status": self.status,
            "severity": self.severity,
            "level": self.level,
            "duration_ms": self.duration_ms,
            "detail_code": self.detail_code,
            "message": self.message,
            "capability_ids": list(self.capability_ids),
            "metrics": dict(self.metrics),
            "evidence": dict(self.evidence),
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SelfcheckReport:
    run_id: str
    trigger: str
    environment: str
    status: RunStatus
    capability_registry_sha256: str
    probe_registry_sha256: str
    summary: Mapping[str, int]
    results: tuple[SelfcheckCheckResult, ...]
    started_at: datetime
    completed_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "trigger": self.trigger,
            "environment": self.environment,
            "status": self.status,
            "capability_registry_sha256": self.capability_registry_sha256,
            "probe_registry_sha256": self.probe_registry_sha256,
            "summary": dict(self.summary),
            "results": [item.to_dict() for item in self.results],
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
        }


@dataclass(slots=True)
class _CheckContext:
    app: FastAPI
    db: Session
    now: datetime
    testing: bool
    environment: str
    agent_descriptors: tuple[object, ...]
    agent_registry: object | None
    runtime_diagnostics: SelfcheckRuntimeDiagnosticsPort | None
    allow_model_checks: bool
    model_canary_runner: ModelCanaryRunner
    capability_snapshot: object

    @property
    def tables(self) -> set[str]:
        return set(inspect(self.db.bind).get_table_names())


def _outcome(
    status: CheckStatus,
    detail_code: str,
    message: str,
    *,
    metrics: Mapping[str, object] | None = None,
    evidence: Mapping[str, object] | None = None,
) -> ProbeOutcome:
    return ProbeOutcome(
        status=status,
        detail_code=detail_code,
        message=message,
        metrics=_safe_json_mapping(metrics or {}),
        evidence=_safe_json_mapping(evidence or {}),
    )


def _table_unavailable(table_name: str) -> ProbeOutcome:
    return _outcome(
        "inconclusive",
        "table_unavailable",
        f"数据表 {table_name} 不可用",
        evidence={"table": table_name},
    )


def _check_capability_integrity(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    snapshot = context.capability_snapshot
    count = len(snapshot)
    if count <= 0 or len(snapshot.sha256) != 64:
        return _outcome("failed", "capability_registry_invalid", "能力清单为空或摘要非法")
    return _outcome(
        "passed",
        "capability_registry_valid",
        "能力清单已冻结且摘要有效",
        metrics={"capability_count": count},
        evidence={"registry_sha256": snapshot.sha256},
    )


def _check_openapi(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    schema = context.app.openapi()
    paths = schema.get("paths")
    if not isinstance(paths, dict):
        return _outcome("failed", "openapi_paths_missing", "OpenAPI paths 缺失")
    operation_ids: list[str] = []
    invalid = 0
    typed = 0
    compatibility = 0
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for method in ("get", "put", "post", "delete", "patch"):
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            operation_id = str(operation.get("operationId") or "")
            operation_ids.append(operation_id)
            responses = operation.get("responses")
            lifecycle = str(operation.get("x-nanobot-contract-lifecycle") or "")
            typed += int(lifecycle == "typed")
            compatibility += int(lifecycle == "compatibility")
            if (
                not operation_id
                or not isinstance(responses, dict)
                or "default" not in responses
                or lifecycle not in {"typed", "compatibility"}
            ):
                invalid += 1
    duplicate_count = len(operation_ids) - len(set(operation_ids))
    if invalid or duplicate_count:
        return _outcome(
            "failed",
            "openapi_contract_invalid",
            "OpenAPI 存在缺失或重复合同",
            metrics={"invalid": invalid, "duplicates": duplicate_count},
        )
    return _outcome(
        "passed",
        "openapi_contracts_valid",
        "OpenAPI 操作 ID、错误响应与生命周期完整",
        metrics={
            "operation_count": len(operation_ids),
            "typed_count": typed,
            "compatibility_count": compatibility,
        },
    )


def _check_webui_manifest(
    _context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    manifest_path = _PROJECT_ROOT / "config" / "webui-capabilities.v1.json"
    app_path = _PROJECT_ROOT / "webui" / "src" / "App.jsx"
    feature_path = _PROJECT_ROOT / "webui" / "src" / "features" / "manifest.jsx"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        app_source = app_path.read_text(encoding="utf-8")
        feature_source = feature_path.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        return _outcome("failed", "webui_manifest_unreadable", "WebUI 路由清单不可读取")
    declared = {
        str(item.get("route") or "")
        for item in manifest.get("capabilities", [])
        if isinstance(item, dict)
    }
    actual = set(re.findall(r'<Route\s+path="([^"]+)"', app_source))
    actual.update(re.findall(r"\broute:\s*'([^']+)'", feature_source))
    actual.discard("*")
    missing = sorted(actual - declared)
    stale = sorted(declared - actual)
    if missing or stale:
        return _outcome(
            "failed",
            "webui_route_manifest_drift",
            "WebUI 路由与机器清单发生漂移",
            metrics={"missing_count": len(missing), "stale_count": len(stale)},
        )
    return _outcome(
        "passed",
        "webui_route_manifest_valid",
        "WebUI 路由与机器清单一致",
        metrics={"route_count": len(actual)},
    )


def _check_webui_operation_bindings(
    context: _CheckContext,
    probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    manifest_path = _PROJECT_ROOT / "config" / "webui-capabilities.v1.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _outcome(
            "failed",
            "webui_operation_manifest_unreadable",
            "WebUI 后端 Operation 清单不可读取",
        )
    items = manifest.get("capabilities") if isinstance(manifest, dict) else None
    if not isinstance(items, list):
        return _outcome(
            "failed",
            "webui_operation_manifest_invalid",
            "WebUI 后端 Operation 清单结构非法",
        )
    by_route = {
        str(item.get("route") or ""): item
        for item in items
        if isinstance(item, dict)
    }
    schema = context.app.openapi()
    operation_ids = {
        str(operation.get("operationId") or "")
        for path_item in (schema.get("paths") or {}).values()
        if isinstance(path_item, dict)
        for method in ("get", "put", "post", "delete", "patch")
        for operation in (path_item.get(method),)
        if isinstance(operation, dict)
    }
    missing_routes: list[str] = []
    empty_routes: list[str] = []
    missing_operations: list[str] = []
    binding_count = 0
    for route in probe.capability_source_ids:
        item = by_route.get(route)
        if item is None:
            missing_routes.append(route)
            continue
        bindings = item.get("backend_operation_ids")
        if not isinstance(bindings, list) or not bindings:
            empty_routes.append(route)
            continue
        binding_count += len(bindings)
        missing_operations.extend(
            f"{route}:{operation_id}"
            for operation_id in bindings
            if not isinstance(operation_id, str)
            or operation_id not in operation_ids
        )
    if missing_routes or empty_routes or missing_operations:
        return _outcome(
            "failed",
            "webui_operation_binding_drift",
            "关键 WebUI 页面缺少后端 Operation 或引用已漂移",
            metrics={
                "route_count": len(probe.capability_source_ids),
                "binding_count": binding_count,
                "missing_route_count": len(missing_routes),
                "empty_route_count": len(empty_routes),
                "missing_operation_count": len(missing_operations),
            },
            evidence={
                "missing_routes": missing_routes,
                "empty_routes": empty_routes,
                "missing_operations": missing_operations,
            },
        )
    return _outcome(
        "passed",
        "webui_operation_bindings_valid",
        "RAG Debug 与系统自检页面的后端 Operation 绑定有效",
        metrics={
            "route_count": len(probe.capability_source_ids),
            "binding_count": binding_count,
        },
    )


def _check_agent_registry(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    agents = context.agent_descriptors
    if not agents:
        return _outcome(
            "skipped" if context.testing else "failed",
            "agent_registry_unavailable",
            "测试环境未绑定 Agent Registry" if context.testing else "Agent Registry 不可用",
        )
    defaults = [agent for agent in agents if bool(getattr(agent, "default", False))]
    chat_agents = [
        agent
        for agent in agents
        if "chat" in tuple(getattr(agent, "allowed_entrypoints", ()))
    ]
    if len(defaults) != 1 or not chat_agents:
        return _outcome(
            "failed",
            "agent_registry_invalid",
            "Agent Registry 默认项或 chat 入口非法",
            metrics={"agent_count": len(agents), "default_count": len(defaults)},
        )
    return _outcome(
        "passed",
        "agent_registry_valid",
        "Agent Registry 默认项和入口有效",
        metrics={"agent_count": len(agents), "chat_agent_count": len(chat_agents)},
    )


def _check_tool_registry(
    _context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    registrations = tuple(TOOL_REGISTRATION_REGISTRY.registry_snapshot)
    active = [item for item in registrations if item.lifecycle == "active"]
    missing_binding = [
        item
        for item in active
        if item.execution_binding is None or not item.schema_provider_id
    ]
    if missing_binding:
        return _outcome(
            "failed",
            "tool_registration_binding_missing",
            "活动工具缺少执行或 Schema 绑定",
            metrics={"missing_binding_count": len(missing_binding)},
        )
    return _outcome(
        "passed",
        "tool_registration_registry_valid",
        "工具注册表和活动绑定完整",
        metrics={"tool_count": len(registrations), "active_count": len(active)},
    )


def _check_model_routes(
    _context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    routes = list_model_route_descriptors()
    invalid = [
        route
        for route in routes
        if not route.default_provider_id and not route.inherits_from
    ]
    if not routes or invalid:
        return _outcome(
            "failed",
            "model_route_registry_invalid",
            "模型路由为空或缺少默认 Provider",
            metrics={"route_count": len(routes), "invalid_count": len(invalid)},
        )
    return _outcome(
        "passed",
        "model_route_registry_valid",
        "模型路由注册表已冻结且 Provider 声明完整",
        metrics={"route_count": len(routes)},
    )


def _default_model_canary_runner(nonce: str, timeout_seconds: float) -> str:
    from core.async_bridge import run_awaitable_sync
    from core.model_provider.chat_runtime import RuntimeChatCompletionClient

    async def invoke() -> dict[str, Any]:
        return await asyncio.wait_for(
            RuntimeChatCompletionClient().chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你正在执行受控系统自检。只输出一个 JSON 对象，"
                            "不得输出 Markdown、解释或工具调用。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "计算 17+25，并严格返回 "
                            f'{{"status":"ok","answer":42,"nonce":"{nonce}"}}'
                        ),
                    },
                ],
                temperature=0.0,
                model_tier="fast",
                max_tokens=80,
                llm_source="selfcheck_model_canary",
                enable_thinking=False,
            ),
            timeout=timeout_seconds,
        )

    response = run_awaitable_sync(invoke())
    choices = response.get("choices") if isinstance(response, dict) else None
    if not isinstance(choices, list) or not choices:
        raise ValueError("模型 Canary 响应缺少 choices")
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ValueError("模型 Canary 响应缺少 content")
    return content


def _check_model_canary(
    context: _CheckContext,
    probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    from core.model_provider.response_normalization import strip_think_blocks

    nonce = uuid4().hex
    try:
        raw = context.model_canary_runner(nonce, probe.timeout_seconds)
    except TimeoutError:
        return _outcome(
            "failed",
            "model_canary_timeout",
            "reply Route 主动 Canary 超时",
        )
    except Exception as exc:
        return _outcome(
            "failed",
            "model_canary_call_failed",
            "reply Route 主动 Canary 调用失败",
            evidence={"error_type": type(exc).__name__},
        )
    content = strip_think_blocks(str(raw or "")).strip()
    if content.startswith("```") and content.endswith("```"):
        lines = content.splitlines()
        content = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        payload = None
    matched = (
        isinstance(payload, dict)
        and payload.get("status") == "ok"
        and str(payload.get("answer")) == "42"
        and payload.get("nonce") == nonce
    )
    metrics = {"response_chars": len(content), "semantic_match": matched}
    if not matched:
        return _outcome(
            "failed",
            "model_canary_semantic_mismatch",
            "reply Route 可返回内容，但未满足结构化语义 Canary",
            metrics=metrics,
        )
    return _outcome(
        "passed",
        "model_canary_passed",
        "reply Route 主动调用和结构化语义 Canary 通过",
        metrics=metrics,
        evidence={"model_tier": "fast"},
    )


def _check_database_connectivity(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    value = int(context.db.execute(text("SELECT 1")).scalar_one())
    return _outcome(
        "passed" if value == 1 else "failed",
        "database_connectivity_ok" if value == 1 else "database_connectivity_invalid",
        "数据库读事务正常" if value == 1 else "数据库读事务返回异常",
    )


def _check_functional_scenario(
    context: _CheckContext,
    probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    from core.selfcheck.scenarios import run_functional_scenario

    scenario = run_functional_scenario(
        probe.check_id,
        db=context.db,
        testing=context.testing,
        agent_registry=context.agent_registry,
        runtime_diagnostics=context.runtime_diagnostics,
        source_id=(
            probe.capability_source_ids[0]
            if probe.capability_source_ids
            else ""
        ),
    )
    return _outcome(
        scenario.status,
        scenario.detail_code,
        scenario.message,
        metrics=scenario.metrics,
        evidence=scenario.evidence,
    )


def _check_required_schema(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    required = {
        "agent_runs",
        "chat_delivery_outbox",
        "chat_logs",
        "chat_stream_configs",
        "conversation_turns",
        "llm_api_request_logs",
        "memory_digest_jobs",
        "outbound_delivery_outbox",
        "proactive_outreach_log",
        "rag_debug_runs",
        "scheduled_task_executions",
        "scheduled_tasks",
        "semantic_index_items",
        "semantic_index_jobs",
        "selfcheck_results",
        "selfcheck_runs",
        "session_summary_jobs",
        "worker_heartbeats",
    }
    if not context.testing:
        required.add("semantic_index_fts")
    missing = sorted(required - context.tables)
    if missing:
        return _outcome(
            "failed",
            "database_required_schema_missing",
            "数据库缺少关键运行表",
            metrics={"missing_count": len(missing)},
            evidence={"missing_tables": missing},
        )
    return _outcome(
        "passed",
        "database_required_schema_valid",
        "关键运行表完整",
        metrics={"required_table_count": len(required)},
    )


def _check_database_integrity(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    dialect = str(context.db.bind.dialect.name)
    if dialect != "sqlite":
        return _outcome(
            "inconclusive",
            "database_integrity_probe_unsupported",
            "当前数据库方言未实现在线完整性探针",
            evidence={"dialect": dialect},
        )
    quick = str(context.db.execute(text("PRAGMA quick_check")).scalar_one())
    foreign_keys = context.db.execute(text("PRAGMA foreign_key_check")).fetchall()
    if quick.lower() != "ok" or foreign_keys:
        return _outcome(
            "failed",
            "database_integrity_failed",
            "SQLite 快速检查或外键检查失败",
            metrics={"foreign_key_violation_count": len(foreign_keys)},
            evidence={"quick_check": quick[:128]},
        )
    return _outcome(
        "passed",
        "database_integrity_ok",
        "SQLite 快速检查和外键检查通过",
    )


def _check_database_only_default(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    columns = {
        column["name"]: column
        for column in inspect(context.db.bind).get_columns("chat_stream_configs")
    }
    column = columns.get("database_only")
    default = str(column.get("default") if column else "").strip("()'\" ")
    nullable = bool(column.get("nullable")) if column else True
    if column is None or default != "1" or nullable:
        return _outcome(
            "failed",
            "database_only_default_invalid",
            "database_only 必须非空且数据库默认值为 1",
            evidence={"column_present": column is not None, "default": default, "nullable": nullable},
        )
    return _outcome(
        "passed",
        "database_only_default_enabled",
        "仅入库模式数据库默认值为开启",
    )


def _check_prompt_runtime(
    _context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    from core.prompt_v2.template_registry import (
        list_template_keys,
        runtime_template_dir,
    )

    runtime_dir = runtime_template_dir()
    keys = list_template_keys()
    flow_path = runtime_dir / "chat" / "flow.json"
    if not runtime_dir.is_dir() or not flow_path.is_file() or not keys:
        return _outcome(
            "failed",
            "prompt_runtime_incomplete",
            "Prompt Runtime 目录、flow 或模板不完整",
            metrics={"template_count": len(keys)},
            evidence={"runtime_dir_exists": runtime_dir.is_dir(), "flow_exists": flow_path.is_file()},
        )
    return _outcome(
        "passed",
        "prompt_runtime_valid",
        "Prompt Runtime 目录、flow 与模板可读取",
        metrics={"template_count": len(keys)},
    )


def _check_rag_source_runtime(
    _context: _CheckContext,
    probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    from core.semantic.provider_factory import get_rag_runtime_config

    source = probe.capability_source_ids[0]
    config = get_rag_runtime_config(source)
    if not config.enabled:
        return _outcome(
            "skipped",
            "rag_source_disabled",
            f"RAG {source} 已显式关闭",
            evidence={"source": source},
        )
    if not config.reranker_enabled:
        return _outcome(
            "degraded",
            "rag_reranker_disabled",
            f"RAG {source} 已启用但 reranker 关闭",
            evidence={"source": source},
        )
    return _outcome(
        "passed",
        "rag_source_runtime_enabled",
        f"RAG {source} 与 reranker 配置已启用",
        evidence={"source": source, "allow_degraded": config.allow_degraded},
    )


def _check_rag_index_queue(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    if "semantic_index_jobs" not in context.tables:
        return _table_unavailable("semantic_index_jobs")
    cutoff = context.now - timedelta(hours=24)
    query = context.db.query(SemanticIndexJob).filter(
        SemanticIndexJob.updated_at >= cutoff
    )
    total = query.count()
    failed = query.filter(SemanticIndexJob.status == "failed").count()
    pending = query.filter(
        SemanticIndexJob.status.in_(("pending", "retry_wait"))
    ).count()
    stale = context.db.query(SemanticIndexJob).filter(
        SemanticIndexJob.status == "running",
        SemanticIndexJob.lease_expires_at.is_not(None),
        SemanticIndexJob.lease_expires_at < context.now,
    ).count()
    metrics = {"recent_total": total, "failed": failed, "pending": pending, "stale_running": stale}
    if stale or (total >= 3 and failed == total):
        return _outcome("failed", "rag_index_queue_failed", "语义索引队列存在卡死或全量失败", metrics=metrics)
    if failed or pending > 1000:
        return _outcome("degraded", "rag_index_queue_degraded", "语义索引队列存在失败或大量积压", metrics=metrics)
    return _outcome("passed", "rag_index_queue_healthy", "语义索引队列无异常积压", metrics=metrics)


def _check_rag_index_quality(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    if "semantic_index_items" not in context.tables:
        return _table_unavailable("semantic_index_items")
    active = context.db.query(SemanticIndexItem).filter(
        SemanticIndexItem.status == "active"
    )
    total = active.count()
    if total == 0:
        return _outcome("inconclusive", "rag_index_empty", "语义索引暂无活动条目", metrics={"active": 0})
    blank = active.filter(
        func.length(func.trim(SemanticIndexItem.text)) == 0,
        func.length(func.trim(SemanticIndexItem.lexical_text)) == 0,
    ).count()
    embedding_failed = active.filter(
        SemanticIndexItem.embedding_status == "failed"
    ).count()
    metrics = {"active": total, "blank": blank, "embedding_failed": embedding_failed}
    if blank:
        return _outcome("failed", "rag_index_blank_items", "语义索引含空正文条目", metrics=metrics)
    if embedding_failed / total > 0.2:
        return _outcome("degraded", "rag_embedding_failure_rate_high", "语义索引 embedding 失败率偏高", metrics=metrics)
    return _outcome("passed", "rag_index_quality_ok", "语义索引活动条目质量正常", metrics=metrics)


def _check_rag_debug_history(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    if "rag_debug_runs" not in context.tables:
        return _table_unavailable("rag_debug_runs")
    cutoff = context.now - timedelta(hours=24)
    rows = context.db.query(RagDebugRun).filter(RagDebugRun.created_at >= cutoff).all()
    if not rows:
        return _outcome("inconclusive", "rag_debug_no_recent_evidence", "24 小时内没有 RAG Debug 运行证据")
    degraded = sum(bool(row.degraded) for row in rows)
    by_source = Counter(str(row.source_type or "unknown") for row in rows)
    metrics = {
        "recent_total": len(rows),
        "degraded": degraded,
        "degraded_rate": round(degraded / len(rows), 4),
        "source_counts": dict(by_source),
    }
    if degraded == len(rows):
        return _outcome("failed", "rag_debug_all_degraded", "近期 RAG Debug 全部降级", metrics=metrics)
    if degraded:
        return _outcome("degraded", "rag_debug_partially_degraded", "近期部分 RAG Debug 降级", metrics=metrics)
    return _outcome("passed", "rag_debug_history_healthy", "近期 RAG Debug 无降级记录", metrics=metrics)


def _scheduler_handle(context: _CheckContext, worker_id: str) -> object | None:
    handles = getattr(context.app.state, "scheduler_handles", None)
    attribute_by_worker = {
        "daily-digest-scheduler": "digest",
        "scheduled-task-runner": "scheduled_tasks",
        "session-summary-worker": "session_summary",
        "chat-delivery-worker": "chat_delivery",
        "eval-sampling-scheduler": "eval_sampling",
        "proactive-outreach-scheduler": "proactive_outreach",
        "group-learning-scheduler": "group_learning",
        "selfcheck-watchdog": "selfcheck",
    }
    attribute = attribute_by_worker.get(worker_id)
    return getattr(handles, attribute, None) if handles is not None and attribute else None


def _check_worker_liveness(
    context: _CheckContext,
    probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    worker_id = probe.capability_source_ids[0]
    if context.testing:
        return _outcome("skipped", "worker_liveness_skipped_testing", f"测试环境不启动 {worker_id}")
    handle = _scheduler_handle(context, worker_id)
    if handle is not None:
        thread = getattr(handle, "thread", None)
        alive = bool(thread is not None and thread.is_alive())
        return _outcome(
            "passed" if alive else "failed",
            "worker_thread_alive" if alive else "worker_thread_stopped",
            f"{worker_id} 线程存活" if alive else f"{worker_id} 线程已停止",
            evidence={"mode": "embedded"},
        )
    if "worker_heartbeats" not in context.tables:
        return _table_unavailable("worker_heartbeats")
    row = context.db.get(WorkerHeartbeat, worker_id)
    if row is None:
        return _outcome("failed", "worker_heartbeat_missing", f"{worker_id} 从未上报心跳", evidence={"mode": "external"})
    age_seconds = max(0.0, (context.now - row.last_seen_at).total_seconds())
    metrics = {
        "age_seconds": round(age_seconds, 3),
        "cycle_count": int(row.cycle_count or 0),
        "failure_count": int(row.failure_count or 0),
    }
    if row.state != "running" or age_seconds > 180:
        return _outcome("failed", "worker_heartbeat_stale", f"{worker_id} 心跳过期或已停止", metrics=metrics, evidence={"state": row.state, "mode": row.mode})
    recent_error = row.last_error_at is not None and (
        row.last_success_at is None or row.last_error_at > row.last_success_at
    )
    if recent_error:
        return _outcome("degraded", "worker_last_cycle_failed", f"{worker_id} 最近循环失败", metrics=metrics, evidence={"error_code": row.last_error_code})
    return _outcome("passed", "worker_heartbeat_fresh", f"{worker_id} 心跳新鲜", metrics=metrics, evidence={"mode": row.mode})


def _check_session_summary_queue(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    if "session_summary_jobs" not in context.tables:
        return _table_unavailable("session_summary_jobs")
    recent = context.db.query(SessionSummaryJob).filter(
        SessionSummaryJob.updated_at >= context.now - timedelta(hours=24)
    )
    total = recent.count()
    failed = recent.filter(SessionSummaryJob.status == "failed").count()
    pending = recent.filter(SessionSummaryJob.status == "pending").count()
    stale = context.db.query(SessionSummaryJob).filter(
        SessionSummaryJob.status == "running",
        SessionSummaryJob.lease_expires_at.is_not(None),
        SessionSummaryJob.lease_expires_at < context.now,
    ).count()
    metrics = {"recent_total": total, "failed": failed, "pending": pending, "stale_running": stale}
    if stale or (total >= 3 and failed == total):
        return _outcome("failed", "session_summary_queue_failed", "会话摘要队列卡死或全量失败", metrics=metrics)
    if failed or pending > 500:
        return _outcome("degraded", "session_summary_queue_degraded", "会话摘要队列存在失败或积压", metrics=metrics)
    return _outcome("passed", "session_summary_queue_healthy", "会话摘要队列无异常积压", metrics=metrics)


def _check_outbound_queue(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    if "outbound_delivery_outbox" not in context.tables:
        return _table_unavailable("outbound_delivery_outbox")
    active = context.db.query(OutboundDeliveryOutbox).filter(
        OutboundDeliveryOutbox.status.in_(("pending", "leased", "retry_wait"))
    )
    pending = active.count()
    stale_leases = active.filter(
        OutboundDeliveryOutbox.status == "leased",
        OutboundDeliveryOutbox.lease_expires_at.is_not(None),
        OutboundDeliveryOutbox.lease_expires_at < context.now,
    ).count()
    open_circuits = context.db.query(OutboundDeliveryCircuit).filter(
        OutboundDeliveryCircuit.status == "open"
    ).count()
    metrics = {"active": pending, "stale_leases": stale_leases, "open_circuits": open_circuits}
    if stale_leases or open_circuits:
        return _outcome("failed", "outbound_delivery_queue_blocked", "主动投递队列存在过期租约或打开的熔断器", metrics=metrics)
    if pending > 500:
        return _outcome("degraded", "outbound_delivery_backlog", "主动投递队列积压偏高", metrics=metrics)
    return _outcome("passed", "outbound_delivery_queue_healthy", "主动投递队列无阻断", metrics=metrics)


def _check_chat_delivery_queue(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    if "chat_delivery_outbox" not in context.tables:
        return _table_unavailable("chat_delivery_outbox")
    pending = context.db.query(ChatDeliveryOutbox).filter(
        ChatDeliveryOutbox.status.in_(("pending", "sending", "ambiguous"))
    ).count()
    stale = context.db.query(ChatDeliveryOutbox).filter(
        ChatDeliveryOutbox.status == "sending",
        ChatDeliveryOutbox.lease_expires_at.is_not(None),
        ChatDeliveryOutbox.lease_expires_at < context.now,
    ).count()
    metrics = {"active": pending, "stale_sending": stale}
    if stale:
        return _outcome("failed", "chat_delivery_queue_stale", "聊天投递队列存在过期 sending", metrics=metrics)
    if pending > 500:
        return _outcome("degraded", "chat_delivery_backlog", "聊天投递队列积压偏高", metrics=metrics)
    return _outcome("passed", "chat_delivery_queue_healthy", "聊天投递队列无阻断", metrics=metrics)


def _check_daily_digest(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    if not settings.get_bool("memory_digest.scheduler_enabled", True):
        return _outcome("skipped", "daily_digest_disabled", "记忆摘要日报调度已显式关闭")
    cutoff_date = (context.now - timedelta(days=3)).date().isoformat()
    jobs = context.db.query(MemoryDigestJob).filter(
        MemoryDigestJob.digest_date >= cutoff_date
    )
    total = jobs.count()
    if total == 0:
        return _outcome("inconclusive", "daily_digest_no_recent_jobs", "近三天没有记忆摘要日报任务证据")
    done = jobs.filter(MemoryDigestJob.status == "done").count()
    failed = jobs.filter(MemoryDigestJob.status == "failed").count()
    digest_rows = context.db.query(MemoryDigest).filter(
        MemoryDigest.digest_date >= cutoff_date
    )
    digest_count = digest_rows.count()
    fallback_count = digest_rows.filter(
        MemoryDigest.meta_json.like("%deterministic_fallback%")
    ).count()
    fallback_rate = round(fallback_count / digest_count, 4) if digest_count else 0.0
    metrics = {
        "job_total": total,
        "done": done,
        "failed": failed,
        "digest_count": digest_count,
        "fallback_count": fallback_count,
        "fallback_rate": fallback_rate,
    }
    if done == 0 and failed:
        return _outcome("failed", "daily_digest_all_failed", "近期记忆摘要日报任务没有成功项", metrics=metrics)
    if digest_count and fallback_count == digest_count:
        return _outcome("failed", "daily_digest_all_fallback", "近期记忆摘要日报全部为 deterministic fallback", metrics=metrics)
    if failed or fallback_rate > 0.2:
        return _outcome("degraded", "daily_digest_quality_degraded", "近期记忆摘要日报存在失败或 fallback", metrics=metrics)
    return _outcome("passed", "daily_digest_healthy", "近期记忆摘要日报任务和生成质量正常", metrics=metrics)


def _check_scheduled_task_definitions(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    from core.schedule_spec import spec_from_fields
    from core.scheduled_task_outbound import snapshot_scheduled_task

    rows = context.db.query(ScheduledTask).filter(ScheduledTask.enabled == 1).all()
    invalid_schedule = 0
    invalid_snapshot = 0
    error_types: Counter[str] = Counter()
    for row in rows:
        if spec_from_fields(
            row.schedule_kind,
            row.schedule_spec,
            row.cron_expr,
        ) is None:
            invalid_schedule += 1
        try:
            snapshot_scheduled_task(row)
        except Exception as exc:
            invalid_snapshot += 1
            error_types[type(exc).__name__] += 1
    metrics = {
        "enabled": len(rows),
        "invalid_schedule": invalid_schedule,
        "invalid_snapshot": invalid_snapshot,
    }
    if invalid_schedule or invalid_snapshot:
        return _outcome(
            "failed",
            "scheduled_task_definition_invalid",
            "启用中的定时任务含不可解析触发器、owner 或 program",
            metrics=metrics,
            evidence={"error_types": dict(error_types)},
        )
    return _outcome(
        "passed",
        "scheduled_task_definitions_valid",
        "启用中的定时任务均可构建规范执行快照",
        metrics=metrics,
    )


def _check_scheduled_delivery_quality(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    required = {
        "outbound_runs",
        "outbound_generation_attempts",
        "outbound_delivery_outbox",
        "outbound_delivery_attempts",
    }
    if not required <= context.tables:
        return _outcome(
            "inconclusive",
            "scheduled_delivery_tables_unavailable",
            "定时推送质量账本不完整",
            evidence={"missing_tables": sorted(required - context.tables)},
        )
    cutoff = context.now - timedelta(hours=72)
    runs = context.db.query(OutboundRun).filter(
        OutboundRun.source_type == "scheduled_task",
        OutboundRun.created_at >= cutoff,
    ).all()
    if not runs:
        return _outcome(
            "inconclusive",
            "scheduled_delivery_no_recent_runs",
            "72 小时内没有定时推送运行证据",
        )
    run_ids = [int(row.id) for row in runs]
    generation_attempts = context.db.query(OutboundGenerationAttempt).filter(
        OutboundGenerationAttempt.run_id.in_(run_ids)
    ).all()
    outboxes = context.db.query(OutboundDeliveryOutbox).filter(
        OutboundDeliveryOutbox.run_id.in_(run_ids)
    ).all()
    outbox_ids = [int(row.id) for row in outboxes]
    delivery_attempts = (
        context.db.query(OutboundDeliveryAttempt).filter(
            OutboundDeliveryAttempt.outbox_id.in_(outbox_ids)
        ).all()
        if outbox_ids
        else []
    )
    generated = sum(row.status == "succeeded" for row in generation_attempts)
    generation_failed = sum(row.status == "failed" for row in generation_attempts)
    delivered = sum(row.status == "delivered" for row in outboxes)
    delivery_failed = sum(
        row.status in {"failed", "blocked", "ambiguous"}
        for row in outboxes
    )
    terminal_attempt_failures = sum(
        row.status in {"failed", "ambiguous"}
        for row in delivery_attempts
    )
    stale_runs = sum(
        row.status in {"claimed", "queued", "delivering"}
        and row.updated_at < context.now - timedelta(minutes=30)
        for row in runs
    )
    metrics = {
        "run_total": len(runs),
        "generated": generated,
        "generation_failed": generation_failed,
        "outbox_total": len(outboxes),
        "delivered": delivered,
        "delivery_failed": delivery_failed,
        "delivery_attempt_failures": terminal_attempt_failures,
        "stale_runs": stale_runs,
    }
    terminal_generation = generated + generation_failed
    if stale_runs:
        return _outcome(
            "failed",
            "scheduled_delivery_runs_stale",
            "定时推送存在超过 30 分钟未终结的运行",
            metrics=metrics,
        )
    if terminal_generation and generated == 0:
        return _outcome(
            "failed",
            "scheduled_delivery_generation_all_failed",
            "近期定时推送正文生成全部失败",
            metrics=metrics,
        )
    terminal_deliveries = delivered + delivery_failed
    if terminal_deliveries and delivered == 0:
        return _outcome(
            "failed",
            "scheduled_delivery_all_failed",
            "近期定时推送投递全部失败",
            metrics=metrics,
        )
    if generation_failed or delivery_failed:
        return _outcome(
            "degraded",
            "scheduled_delivery_partially_failed",
            "近期部分定时推送生成或投递失败",
            metrics=metrics,
        )
    if not outboxes:
        return _outcome(
            "inconclusive",
            "scheduled_delivery_generation_pending",
            "近期定时推送尚未形成可投递 Outbox",
            metrics=metrics,
        )
    return _outcome(
        "passed",
        "scheduled_delivery_quality_healthy",
        "近期定时推送生成与投递质量正常",
        metrics=metrics,
    )


def _check_proactive_outreach(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    if context.testing:
        return _outcome(
            "skipped",
            "proactive_outreach_skipped_testing",
            "测试环境不执行主动外呼运行状态判断",
        )
    if not settings.get_bool("proactive_outreach.enabled", False):
        return _outcome("skipped", "proactive_outreach_disabled", "主动外呼已显式关闭")
    if "proactive_outreach_log" not in context.tables:
        return _table_unavailable("proactive_outreach_log")
    cutoff = context.now - timedelta(hours=48)
    rows = context.db.query(ProactiveOutreachLog).filter(
        ProactiveOutreachLog.created_at >= cutoff
    ).all()
    if not rows:
        return _outcome("failed", "proactive_outreach_no_recent_evaluation", "主动外呼已启用但 48 小时无评估记录")
    counts = Counter(str(row.status or "unknown") for row in rows)
    failure_count = sum(counts[key] for key in ("failed", "evaluation_error"))
    generated_rows = [
        row
        for row in rows
        if row.outbound_run_id is not None or bool(str(row.message or "").strip())
    ]
    forced_fallback = 0
    generation_error = 0
    for row in generated_rows:
        try:
            grounding = json.loads(row.grounding_json or "{}")
        except (TypeError, json.JSONDecodeError):
            grounding = {}
        if not isinstance(grounding, dict):
            grounding = {}
        forced_fallback += int(bool(grounding.get("forced_fallback")))
        generation_error += int(bool(grounding.get("generation_error")))
    fallback_rate = (
        round(forced_fallback / len(generated_rows), 4)
        if generated_rows
        else 0.0
    )
    metrics = {
        "recent_total": len(rows),
        "failed": failure_count,
        "generated": len(generated_rows),
        "forced_fallback": forced_fallback,
        "fallback_rate": fallback_rate,
        "generation_error": generation_error,
        "status_counts": dict(counts),
    }
    if generated_rows and forced_fallback == len(generated_rows):
        return _outcome(
            "failed",
            "proactive_outreach_all_forced_fallback",
            "近期主动外呼正文全部为强制 fallback",
            metrics=metrics,
        )
    if failure_count == len(rows):
        return _outcome("failed", "proactive_outreach_all_failed", "近期主动外呼评估全部失败", metrics=metrics)
    if failure_count or forced_fallback or generation_error:
        return _outcome("degraded", "proactive_outreach_partially_failed", "近期主动外呼存在失败评估或 fallback 正文", metrics=metrics)
    return _outcome("passed", "proactive_outreach_active", "近期主动外呼存在有效调度证据", metrics=metrics)


def _check_scheduled_tasks(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    # scheduled_tasks.next_fire_at 与 scheduled_task_executions.lease_expires_at
    # 都按 UTC naive 持久化；context.now 则是服务器本地墙钟。比较前必须统一
    # 到 UTC naive，否则 Asia/Shanghai 部署会把尚未到期的任务误报为逾期。
    scheduled_now = context.now.astimezone(timezone.utc).replace(tzinfo=None)
    enabled = context.db.query(ScheduledTask).filter(ScheduledTask.enabled == 1)
    enabled_count = enabled.count()
    overdue = enabled.filter(
        ScheduledTask.next_fire_at.is_not(None),
        ScheduledTask.next_fire_at < scheduled_now - timedelta(minutes=5),
    ).count()
    stale = context.db.query(ScheduledTaskExecution).filter(
        ScheduledTaskExecution.status == "running",
        ScheduledTaskExecution.lease_expires_at.is_not(None),
        ScheduledTaskExecution.lease_expires_at < scheduled_now,
    ).count()
    metrics = {"enabled": enabled_count, "overdue": overdue, "stale_running": stale}
    if overdue or stale:
        return _outcome("failed", "scheduled_tasks_stalled", "定时任务存在逾期触发或过期运行租约", metrics=metrics)
    return _outcome("passed", "scheduled_tasks_healthy", "定时任务无逾期或卡死执行", metrics=metrics)


def _check_model_observability(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    cutoff = context.now - timedelta(hours=24)
    rows = context.db.query(LLMApiRequestLog).filter(
        LLMApiRequestLog.created_at >= cutoff,
        LLMApiRequestLog.status != "created",
    ).all()
    if not rows:
        return _outcome("inconclusive", "model_calls_no_recent_evidence", "24 小时内没有模型调用终态证据")
    succeeded = sum(200 <= int(row.response_status or 0) < 300 and not row.error for row in rows)
    failed = len(rows) - succeeded
    success_rate = round(succeeded / len(rows), 4)
    categories = Counter(str(row.error_category or "none") for row in rows if row.error_category != "none")
    metrics = {
        "recent_total": len(rows),
        "succeeded": succeeded,
        "failed": failed,
        "success_rate": success_rate,
        "error_categories": dict(categories),
    }
    if succeeded == 0:
        return _outcome("failed", "model_calls_all_failed", "近期模型 API 调用全部失败", metrics=metrics)
    if success_rate < 0.8:
        return _outcome("degraded", "model_call_success_rate_low", "近期模型 API 成功率低于 80%", metrics=metrics)
    return _outcome("passed", "model_calls_healthy", "近期模型 API 成功率正常", metrics=metrics)


def _check_agent_runs(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    cutoff = context.now - timedelta(hours=24)
    rows = context.db.query(AgentRun).filter(AgentRun.started_at >= cutoff).all()
    if not rows:
        return _outcome("inconclusive", "agent_runs_no_recent_evidence", "24 小时内没有 Agent Run 证据")
    stale = sum(
        row.status == "running"
        and row.started_at is not None
        and row.started_at < context.now - timedelta(minutes=30)
        for row in rows
    )
    failed = sum(row.status in {"failed", "error"} for row in rows)
    metrics = {"recent_total": len(rows), "failed": failed, "stale_running": stale}
    if stale:
        return _outcome("failed", "agent_runs_stale", "存在超过 30 分钟未终结的 Agent Run", metrics=metrics)
    if failed / len(rows) > 0.2:
        return _outcome("degraded", "agent_run_failure_rate_high", "近期 Agent Run 失败率偏高", metrics=metrics)
    return _outcome("passed", "agent_runs_healthy", "近期 Agent Run 终态正常", metrics=metrics)


def _check_memory_summary_quality(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    cutoff = context.now - timedelta(hours=24)
    rows = context.db.query(RollingSessionSummary).filter(
        RollingSessionSummary.created_at >= cutoff
    ).all()
    if not rows:
        return _outcome("inconclusive", "summary_quality_no_recent_evidence", "24 小时内没有滚动摘要证据")
    fallback = sum(row.summary_kind == "deterministic_fallback" for row in rows)
    failed = sum(row.llm_status == "failed" for row in rows)
    fallback_rate = round(fallback / len(rows), 4)
    metrics = {"recent_total": len(rows), "fallback": fallback, "failed": failed, "fallback_rate": fallback_rate}
    if fallback == len(rows) and len(rows) >= 3:
        return _outcome("failed", "summary_quality_all_fallback", "近期滚动摘要全部为 fallback", metrics=metrics)
    if fallback_rate > 0.2 or failed:
        return _outcome("degraded", "summary_quality_degraded", "近期滚动摘要 fallback 或失败率偏高", metrics=metrics)
    return _outcome("passed", "summary_quality_healthy", "近期滚动摘要质量正常", metrics=metrics)


def _check_workspace_assets(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    required = {"workspaces", "assets", "workspace_assets"}
    if not required <= context.tables:
        return _outcome("inconclusive", "workspace_asset_tables_unavailable", "Workspace 或 Asset 表不可用")
    orphan_count = int(context.db.execute(text(
        "SELECT COUNT(*) FROM workspace_assets wa "
        "LEFT JOIN workspaces w ON w.id = wa.workspace_id "
        "LEFT JOIN assets a ON a.sha256 = wa.asset_sha256 "
        "WHERE w.id IS NULL OR a.sha256 IS NULL"
    )).scalar_one())
    if orphan_count:
        return _outcome("failed", "workspace_asset_orphans", "Workspace Asset 存在悬空引用", metrics={"orphan_count": orphan_count})
    return _outcome("passed", "workspace_assets_consistent", "Workspace Asset 引用一致", metrics={"orphan_count": 0})


def _check_collaboration_events(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    required = {"agent_collaboration_boards", "agent_collaboration_events"}
    if not required <= context.tables:
        return _outcome("inconclusive", "collaboration_tables_unavailable", "协作任务板表不可用")
    orphan_count = int(context.db.execute(text(
        "SELECT COUNT(*) FROM agent_collaboration_events e "
        "LEFT JOIN agent_collaboration_boards b ON b.board_id = e.board_id "
        "WHERE b.board_id IS NULL"
    )).scalar_one())
    gap_count = int(context.db.execute(text(
        "SELECT COUNT(*) FROM ("
        "SELECT board_id FROM agent_collaboration_events GROUP BY board_id "
        "HAVING MIN(sequence) <> 1 OR MAX(sequence) <> COUNT(*)"
        ") gaps"
    )).scalar_one())
    metrics = {"orphan_events": orphan_count, "sequence_gaps": gap_count}
    if orphan_count or gap_count:
        return _outcome("failed", "collaboration_event_integrity_failed", "协作事件存在悬空或序列缺口", metrics=metrics)
    return _outcome("passed", "collaboration_event_integrity_ok", "协作事件引用和序列一致", metrics=metrics)


def _check_run_ledger(
    context: _CheckContext,
    _probe: SelfcheckProbeDescriptor,
) -> ProbeOutcome:
    required = {"run_ledger_events", "run_ledger_stream_heads"}
    if not required <= context.tables:
        return _outcome("inconclusive", "run_ledger_tables_unavailable", "Run Ledger 表不可用")
    mismatch = int(context.db.execute(text(
        "SELECT COUNT(*) FROM run_ledger_stream_heads h "
        "LEFT JOIN (SELECT run_id, MAX(sequence) AS max_sequence "
        "FROM run_ledger_events GROUP BY run_id) e ON e.run_id = h.run_id "
        "WHERE h.last_sequence <> COALESCE(e.max_sequence, 0)"
    )).scalar_one())
    if mismatch:
        return _outcome("failed", "run_ledger_head_mismatch", "Run Ledger 流头与事件序列不一致", metrics={"mismatch_count": mismatch})
    return _outcome("passed", "run_ledger_stream_integrity_ok", "Run Ledger 流头与事件序列一致", metrics={"mismatch_count": 0})


_EXECUTORS: dict[
    str,
    Callable[[_CheckContext, SelfcheckProbeDescriptor], ProbeOutcome],
] = {
    "registry.capability_integrity": _check_capability_integrity,
    "api.openapi_contracts": _check_openapi,
    "webui.route_manifest": _check_webui_manifest,
    "webui.critical-operation-bindings": _check_webui_operation_bindings,
    "agent.runtime_registry": _check_agent_registry,
    "tool.registration_registry": _check_tool_registry,
    "model.route_registry": _check_model_routes,
    "model.reply-canary.functional": _check_model_canary,
    "database.connectivity": _check_database_connectivity,
    "scenario.functional": _check_functional_scenario,
    "database.required_schema": _check_required_schema,
    "database.integrity": _check_database_integrity,
    "session.database_only_default": _check_database_only_default,
    "prompt.runtime_templates": _check_prompt_runtime,
    "rag.source_runtime": _check_rag_source_runtime,
    "rag.index_queue": _check_rag_index_queue,
    "rag.index_quality": _check_rag_index_quality,
    "rag.debug_history": _check_rag_debug_history,
    "worker.liveness": _check_worker_liveness,
    "queue.session_summary": _check_session_summary_queue,
    "queue.outbound_delivery": _check_outbound_queue,
    "queue.chat_delivery": _check_chat_delivery_queue,
    "schedule.daily_digest": _check_daily_digest,
    "schedule.task-definitions.functional": _check_scheduled_task_definitions,
    "schedule.scheduled-delivery-quality": _check_scheduled_delivery_quality,
    "schedule.proactive_outreach": _check_proactive_outreach,
    "schedule.scheduled_tasks": _check_scheduled_tasks,
    "observability.model_calls": _check_model_observability,
    "observability.agent_runs": _check_agent_runs,
    "memory.summary_quality": _check_memory_summary_quality,
    "storage.workspace_assets": _check_workspace_assets,
    "collaboration.event_integrity": _check_collaboration_events,
    "run_ledger.stream_integrity": _check_run_ledger,
}


def _capability_ids_for_probe(
    snapshot: Iterable[CapabilityDescriptor],
    check_id: str,
) -> tuple[str, ...]:
    return tuple(
        descriptor.capability_id
        for descriptor in snapshot
        if check_id in descriptor.probe_ids
    )


def _run_status(results: tuple[SelfcheckCheckResult, ...]) -> RunStatus:
    statuses = {item.status for item in results}
    if "failed" in statuses:
        return "failed"
    if "degraded" in statuses:
        return "degraded"
    if "inconclusive" in statuses:
        return "inconclusive"
    return "passed"


class SelfcheckEngine:
    """同步执行有界只读 Probe，并把每项终态持久化。"""

    def __init__(
        self,
        *,
        app: FastAPI,
        db: Session,
        testing: bool,
        agent_descriptors: Iterable[object] = (),
        agent_registry: object | None = None,
        runtime_diagnostics: SelfcheckRuntimeDiagnosticsPort | None = None,
        allow_model_checks: bool = False,
        model_canary_runner: ModelCanaryRunner | None = None,
        endpoint_contracts: Iterable[object] = (),
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(app, FastAPI):
            raise TypeError("SelfcheckEngine.app 必须是 FastAPI")
        self._app = app
        self._db = db
        self._testing = bool(testing)
        self._agents = tuple(agent_descriptors)
        self._agent_registry = agent_registry
        if runtime_diagnostics is None:
            from core.selfcheck.runtime_diagnostics import (
                SelfcheckRuntimeDiagnosticsUnavailableError,
                get_selfcheck_runtime_diagnostics_port,
            )

            try:
                runtime_diagnostics = get_selfcheck_runtime_diagnostics_port()
            except SelfcheckRuntimeDiagnosticsUnavailableError:
                runtime_diagnostics = None
        self._runtime_diagnostics = runtime_diagnostics
        self._allow_model_checks = bool(allow_model_checks)
        self._model_canary_runner = (
            model_canary_runner or _default_model_canary_runner
        )
        self._endpoint_contracts = tuple(endpoint_contracts)
        self._now = now_provider or datetime.now

    def run(
        self,
        *,
        trigger: str,
        requested_by: str,
        check_ids: Iterable[str] | None = None,
    ) -> SelfcheckReport:
        trigger = str(trigger or "").strip()
        requested_by = str(requested_by or "").strip()[:128]
        if trigger not in {"manual", "scheduled", "watchdog", "predeploy"}:
            raise ValueError("selfcheck trigger 非法")
        environment = "ci" if self._testing else "production"
        capability_snapshot = build_capability_registry(
            self._app,
            agent_descriptors=self._agents,
            endpoint_contracts=self._endpoint_contracts,
        )
        selected = tuple(check_ids or SELFCHECK_PROBE_REGISTRY.ordered_ids)
        if len(selected) != len(set(selected)):
            raise ValueError("selfcheck check_ids 不能重复")
        unknown = sorted(set(selected) - set(SELFCHECK_PROBE_REGISTRY.ordered_ids))
        if unknown:
            raise ValueError(f"未知 selfcheck check_id：{unknown}")
        probes = tuple(SELFCHECK_PROBE_REGISTRY.require(check_id) for check_id in selected)
        run_id = f"sc_{uuid4().hex}"
        started_at = self._now()
        run_row = SelfcheckRunRow(
            run_id=run_id,
            trigger=trigger,
            environment=environment,
            status="running",
            requested_by=requested_by,
            capability_registry_sha256=capability_snapshot.sha256,
            probe_registry_sha256=SELFCHECK_PROBE_REGISTRY.sha256,
            selected_check_ids_json=json.dumps(selected, ensure_ascii=False),
            summary_json="{}",
            started_at=started_at,
        )
        self._db.add(run_row)
        self._db.commit()

        context = _CheckContext(
            app=self._app,
            db=self._db,
            now=started_at,
            testing=self._testing,
            environment=environment,
            agent_descriptors=self._agents,
            agent_registry=self._agent_registry,
            runtime_diagnostics=self._runtime_diagnostics,
            allow_model_checks=self._allow_model_checks,
            model_canary_runner=self._model_canary_runner,
            capability_snapshot=capability_snapshot,
        )
        results: list[SelfcheckCheckResult] = []
        for probe in probes:
            check_started = self._now()
            monotonic_started = time.perf_counter()
            try:
                if probe.requires_model and not context.allow_model_checks:
                    outcome = _outcome(
                        "skipped",
                        "model_check_not_authorized",
                        "本次运行未显式启用模型自检",
                    )
                else:
                    executor = _EXECUTORS[probe.executor_key]
                    outcome = executor(context, probe)
            except Exception as exc:
                self._db.rollback()
                outcome = _outcome(
                    "failed",
                    "probe_execution_error",
                    "Probe 执行异常，已记录稳定错误类型",
                    evidence={"error_type": type(exc).__name__},
                )
            duration_ms = max(
                0,
                int(round((time.perf_counter() - monotonic_started) * 1000)),
            )
            check_completed = self._now()
            results.append(SelfcheckCheckResult(
                check_id=probe.check_id,
                category=probe.category,
                status=outcome.status,
                severity=probe.severity,
                level=probe.level,
                duration_ms=duration_ms,
                detail_code=outcome.detail_code,
                message=outcome.message,
                capability_ids=_capability_ids_for_probe(
                    capability_snapshot,
                    probe.check_id,
                ),
                metrics=outcome.metrics,
                evidence=outcome.evidence,
                started_at=check_started,
                completed_at=check_completed,
            ))

        frozen_results = tuple(results)
        status = _run_status(frozen_results)
        counts = Counter(item.status for item in frozen_results)
        summary = {
            "total": len(frozen_results),
            **{item_status: counts[item_status] for item_status in _RESULT_STATUSES},
        }
        completed_at = self._now()
        run_row = self._db.get(SelfcheckRunRow, run_id)
        if run_row is None:
            raise RuntimeError("selfcheck run row 丢失")
        for result in frozen_results:
            self._db.add(SelfcheckResultRow(
                run_id=run_id,
                check_id=result.check_id,
                category=result.category,
                status=result.status,
                severity=result.severity,
                duration_ms=result.duration_ms,
                detail_code=result.detail_code,
                message=result.message,
                capability_ids_json=json.dumps(result.capability_ids, ensure_ascii=False),
                metrics_json=json.dumps(dict(result.metrics), ensure_ascii=False, sort_keys=True),
                evidence_json=json.dumps(dict(result.evidence), ensure_ascii=False, sort_keys=True),
                started_at=result.started_at,
                completed_at=result.completed_at,
            ))
        run_row.status = status
        run_row.summary_json = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        run_row.completed_at = completed_at
        self._db.commit()
        return SelfcheckReport(
            run_id=run_id,
            trigger=trigger,
            environment=environment,
            status=status,
            capability_registry_sha256=capability_snapshot.sha256,
            probe_registry_sha256=SELFCHECK_PROBE_REGISTRY.sha256,
            summary=summary,
            results=frozen_results,
            started_at=started_at,
            completed_at=completed_at,
        )


__all__ = [
    "ProbeOutcome",
    "SelfcheckCheckResult",
    "SelfcheckEngine",
    "SelfcheckReport",
]

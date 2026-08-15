"""跨层、只读且不调用外部网络的功能自检场景。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from core.selfcheck.runtime_diagnostics import (
    SelfcheckRuntimeDiagnosticsPort,
)


ScenarioStatus = Literal[
    "passed",
    "degraded",
    "failed",
    "inconclusive",
    "skipped",
]


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    status: ScenarioStatus
    detail_code: str
    message: str
    metrics: Mapping[str, object] = field(default_factory=dict)
    evidence: Mapping[str, object] = field(default_factory=dict)


def _result(
    status: ScenarioStatus,
    detail_code: str,
    message: str,
    *,
    metrics: Mapping[str, object] | None = None,
    evidence: Mapping[str, object] | None = None,
) -> ScenarioResult:
    return ScenarioResult(
        status=status,
        detail_code=detail_code,
        message=message,
        metrics=metrics or {},
        evidence=evidence or {},
    )


def _session_default_gate(db: Session) -> ScenarioResult:
    from app.session_config.runtime import (
        is_database_only_enabled,
        resolve_session_agent_id,
    )

    # 使用不存在且不落库的会话，实测运行时查询函数的默认行为。
    session_id = f"selfcheck-default-{uuid4().hex}"
    database_only = is_database_only_enabled(
        db,
        platform="qq",
        chat_type="private",
        session_id=session_id,
    )
    agent_id = resolve_session_agent_id(
        db,
        platform="qq",
        chat_type="private",
        session_id=session_id,
    )
    if database_only is not True or agent_id != "nanobot":
        return _result(
            "failed",
            "session_default_gate_invalid",
            "未配置会话没有保持仅入库和默认 Agent 约束",
            evidence={
                "database_only": database_only,
                "default_agent_id": agent_id,
            },
        )
    return _result(
        "passed",
        "session_default_gate_valid",
        "未配置会话运行时默认进入仅入库模式",
        evidence={
            "database_only": True,
            "default_agent_id": "nanobot",
        },
    )


def _agent_routing(
    *,
    agent_registry: object | None,
    testing: bool,
    entrypoints: frozenset[str],
) -> ScenarioResult:
    if agent_registry is None:
        return _result(
            "skipped" if testing else "failed",
            "agent_registry_unavailable",
            "测试环境未绑定 Agent Registry"
            if testing
            else "Agent Registry 不可用，无法验证路由",
        )
    descriptors = tuple(agent_registry.descriptors())
    checked = 0
    for descriptor in descriptors:
        declared = set(getattr(descriptor, "allowed_entrypoints", ()))
        for entrypoint in sorted(declared & entrypoints):
            registration = agent_registry.require_registration(
                getattr(descriptor, "agent_id", ""),
                entrypoint=entrypoint,
            )
            if registration.descriptor is not descriptor:
                return _result(
                    "failed",
                    "agent_registration_identity_mismatch",
                    "Agent 路由返回了错误的注册项",
                )
            checked += 1
    if "chat" in entrypoints:
        default_registration = agent_registry.require_registration(
            "",
            entrypoint="chat",
        )
        if not bool(default_registration.descriptor.default):
            return _result(
                "failed",
                "agent_default_route_invalid",
                "空 Agent ID 没有解析到默认 chat Agent",
            )
    if checked == 0:
        return _result(
            "inconclusive",
            "agent_entrypoint_not_declared",
            "注册集中没有声明待检查的 Agent 入口",
            evidence={"entrypoints": sorted(entrypoints)},
        )
    return _result(
        "passed",
        "agent_routing_valid",
        "Agent 注册项可按声明入口确定性路由",
        metrics={"agent_count": len(descriptors), "route_count": checked},
        evidence={"entrypoints": sorted(entrypoints)},
    )


def _tool_runtime_bindings(
    *,
    runtime_diagnostics: SelfcheckRuntimeDiagnosticsPort | None,
    testing: bool,
) -> ScenarioResult:
    from core.tool_registration import list_active_tool_registrations
    from core.tool_schema_preview import validate_registered_tool_schemas

    validate_registered_tool_schemas()
    expected_port_ids = tuple(sorted(
        registration.execution_binding.port_id
        for registration in list_active_tool_registrations()
        if registration.execution_binding is not None
    ))
    if runtime_diagnostics is None:
        return _result(
            "inconclusive" if testing else "failed",
            "tool_runtime_diagnostics_unavailable",
            "测试环境未绑定 Runtime 诊断 Port"
            if testing
            else "Runtime 诊断 Port 不可用，无法验证实际工具绑定",
            metrics={"active_tool_count": len(expected_port_ids)},
        )
    snapshot = runtime_diagnostics.inspect_tool_bindings()
    if tuple(snapshot.expected_binding_ids) != expected_port_ids:
        return _result(
            "failed",
            "tool_diagnostics_registration_drift",
            "Runtime 诊断快照与工具注册表发生漂移",
        )
    by_runtime = {
        str(item.runtime_id): item
        for item in snapshot.runtimes
    }
    missing_required = sorted(
        set(snapshot.required_runtime_ids) - set(by_runtime)
    )
    unavailable_required = sorted(
        set(snapshot.required_runtime_ids)
        & set(snapshot.unavailable_runtime_ids)
    )
    mismatched = sorted(
        runtime_id
        for runtime_id, item in by_runtime.items()
        if tuple(item.binding_ids) != expected_port_ids
    )
    import_failures = {
        runtime_id: int(item.import_failure_count)
        for runtime_id, item in by_runtime.items()
        if int(item.import_failure_count) > 0
    }
    required_failures = sorted(
        set(missing_required)
        | set(unavailable_required)
        | (set(import_failures) & set(snapshot.required_runtime_ids))
        | (set(mismatched) & set(snapshot.required_runtime_ids))
    )
    metrics = {
        "active_tool_count": len(expected_port_ids),
        "runtime_count": len(by_runtime),
        "kt_binding_count": len(
            tuple(getattr(by_runtime.get("kt"), "binding_ids", ()))
        ),
        "import_failure_count": sum(import_failures.values()),
    }
    if required_failures:
        return _result(
            "failed",
            "required_tool_runtime_invalid",
            "已启用的 Agent Runtime 工具绑定不可用或发生漂移",
            metrics=metrics,
            evidence={"runtime_ids": required_failures},
        )
    optional_failures = sorted(
        (set(import_failures) | set(mismatched))
        - set(snapshot.required_runtime_ids)
    )
    if optional_failures:
        return _result(
            "degraded",
            "optional_tool_runtime_not_ready",
            "未启用的可选 Agent Runtime 尚未满足工具绑定条件",
            metrics=metrics,
            evidence={"runtime_ids": optional_failures},
        )
    if "kt" in snapshot.unavailable_runtime_ids:
        return _result(
            "passed",
            "native_tool_bindings_valid_kt_not_required",
            "Native 工具绑定有效；KT 未启用且运行依赖不可用",
            metrics=metrics,
            evidence={"unavailable_optional_runtimes": ["kt"]},
        )
    return _result(
        "passed",
        "tool_runtime_bindings_valid",
        "工具 Schema 与已安装 Runtime 执行绑定均可构建",
        metrics=metrics,
    )


def _model_route_configuration(
    *,
    testing: bool,
    runtime_diagnostics: SelfcheckRuntimeDiagnosticsPort | None,
) -> ScenarioResult:
    from core.model_provider.chat_runtime import chat_completion_runtime_status
    from core.model_provider.decision_runtime import decision_model_runtime_status
    from core.model_provider.route_runtime import route_model_runtime_status

    runtime_states = {
        "chat": str(chat_completion_runtime_status().get("state") or ""),
        "decision": str(decision_model_runtime_status().get("state") or ""),
        "route": str(route_model_runtime_status().get("state") or ""),
    }
    stopped = sorted(
        runtime_id
        for runtime_id, state in runtime_states.items()
        if state != "running"
    )
    if stopped and not testing:
        return _result(
            "failed",
            "model_runtime_not_running",
            "模型运行时 Port 尚未启动或已经停止",
            metrics={"stopped_count": len(stopped)},
            evidence={"stopped_runtimes": stopped},
        )

    if runtime_diagnostics is None:
        return _result(
            "inconclusive" if testing else "failed",
            "model_runtime_diagnostics_unavailable",
            "测试环境未绑定 Runtime 诊断 Port"
            if testing
            else "Runtime 诊断 Port 不可用，无法验证有效模型路由",
            evidence={"runtime_states": runtime_states},
        )

    try:
        snapshot = runtime_diagnostics.inspect_model_routes()
    except Exception as exc:
        return _result(
            "inconclusive" if testing else "failed",
            "reply_route_unresolvable",
            "测试环境未提供完整 reply Route"
            if testing
            else "reply Route 没有可用候选",
            evidence={"error_type": type(exc).__name__},
        )

    invalid: list[str] = []
    credential_warnings: list[str] = []
    for route in snapshot.routes:
        route_key = str(route.route_key or "")
        driver_type = str(route.driver_type or "").lower()
        if not route.provider_id:
            invalid.append(f"{route_key}:provider_missing")
        if not route.provider_enabled:
            invalid.append(f"{route_key}:provider_disabled")
        if not route.route_completion_supported:
            invalid.append(f"{route_key}:unsupported_driver")
        if str(route.model or "").strip() in {"", "未指定"}:
            invalid.append(f"{route_key}:model_missing")
        if not route.endpoint_configured:
            invalid.append(f"{route_key}:base_url_missing")
        if (
            driver_type not in {"codex", "llama.cpp", "llamacpp", "ollama", "local"}
            and not route.credential_configured
        ):
            credential_warnings.append(route_key)

    invalid_reply = [
        candidate
        for candidate in snapshot.reply_candidates
        if not str(candidate.provider_id or "").strip()
        or not str(candidate.model or "").strip()
        or not candidate.endpoint_configured
    ]
    if invalid or invalid_reply:
        return _result(
            "inconclusive" if testing else "failed",
            "model_route_configuration_invalid",
            "测试环境没有提供完整模型业务路由"
            if testing
            else "模型业务路由存在禁用、缺失或不受支持的有效配置",
            metrics={
                "route_count": len(snapshot.routes),
                "invalid_count": len(invalid),
                "invalid_reply_count": len(invalid_reply),
            },
            evidence={"invalid_routes": invalid},
        )
    if credential_warnings:
        return _result(
            "degraded",
            "model_route_credentials_missing",
            "部分远端模型路由没有可确认的凭据",
            metrics={
                "route_count": len(snapshot.routes),
                "credential_warning_count": len(credential_warnings),
                "reply_candidate_count": len(snapshot.reply_candidates),
            },
            evidence={"routes": credential_warnings},
        )
    return _result(
        "passed",
        "model_route_configuration_valid",
        "模型运行时与有效路由配置可解析",
        metrics={
            "route_count": len(snapshot.routes),
            "reply_candidate_count": len(snapshot.reply_candidates),
        },
        evidence={"runtime_states": runtime_states},
    )


def _semantic_tables_available(db: Session) -> bool:
    tables = set(inspect(db.bind).get_table_names())
    return {"semantic_index_items", "semantic_index_fts"} <= tables


def _mapping_contract(
    value: object,
    *,
    keys: frozenset[str],
) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not keys <= set(value):
        return None
    return value


def _rag_semantic_smoke(db: Session, source: str, *, testing: bool) -> ScenarioResult:
    if not _semantic_tables_available(db):
        return _result(
            "inconclusive" if testing else "failed",
            "rag_semantic_schema_unavailable",
            "语义索引或 FTS 表不可用，无法执行 RAG 冒烟",
            evidence={"source": source},
        )
    query = "自检 检索 管线"
    if source in {"memory", "memory_digest", "session_summary"}:
        from core.memory_rag import MemoryRagService

        memory_source = {
            "memory": "all",
            "memory_digest": "digest",
            "session_summary": "session_summary",
        }[source]
        value = MemoryRagService(
            db,
            embedding_provider=None,
            reranker_provider=None,
            allow_degraded=True,
            readonly=True,
        ).query(
            query,
            source=memory_source,
            limit=2,
            include_debug=True,
        )
    elif source == "sticker":
        from core.sticker_rag import StickerRagService

        value = StickerRagService(
            db,
            embedding_provider=None,
            reranker_provider=None,
            readonly=True,
        ).query(query, limit=2, include_debug=True)
    elif source == "knowledge":
        from core.knowledge_rag import KnowledgeRagService

        value = KnowledgeRagService(
            db,
            embedding_provider=None,
            reranker_provider=None,
            readonly=True,
        ).query(query, limit=2, include_debug=True)
    else:
        raise ValueError(f"不支持的语义 RAG 来源：{source}")
    result = _mapping_contract(
        value,
        keys=frozenset({"items", "stats", "debug_trace", "degraded"}),
    )
    if result is None:
        return _result(
            "failed",
            "rag_result_contract_invalid",
            f"RAG {source} 返回合同不完整",
            evidence={"source": source},
        )
    trace = result.get("debug_trace")
    if not isinstance(trace, dict) or "final_candidates" not in trace:
        return _result(
            "failed",
            "rag_debug_trace_invalid",
            f"RAG {source} 没有生成完整 Debug Trace",
            evidence={"source": source},
        )
    items = result.get("items")
    stats = result.get("stats")
    return _result(
        "passed",
        "rag_pipeline_smoke_passed",
        f"RAG {source} 已执行只读检索管线并返回有效合同",
        metrics={
            "item_count": len(items) if isinstance(items, list) else 0,
            "final_items": int(stats.get("final_items") or 0)
            if isinstance(stats, dict)
            else 0,
        },
        evidence={
            "source": source,
            "deterministic_degraded": bool(result.get("degraded")),
        },
    )


def _rag_group_memory_smoke(db: Session) -> ScenarioResult:
    from app.group_memory.retrieval_service import GroupMemoryRetrievalService

    tables = set(inspect(db.bind).get_table_names())
    if "group_memories" not in tables:
        return _result(
            "failed",
            "group_memory_schema_unavailable",
            "群记忆数据表不可用",
        )
    selection = GroupMemoryRetrievalService(
        db,
        reranker_provider=None,
    ).select(
        group_id="selfcheck-contract-group",
        platform="qq",
        current_user_input="自检 群体记忆 检索",
        recent_messages=[{"content": "检查群体记忆管线"}],
        max_items=2,
        max_chars=256,
    )
    if (
        not isinstance(selection.selected, list)
        or not isinstance(selection.skipped, list)
        or not isinstance(selection.score_components, dict)
    ):
        return _result(
            "failed",
            "group_memory_result_contract_invalid",
            "群记忆检索返回合同不完整",
        )
    return _result(
        "passed",
        "group_memory_pipeline_smoke_passed",
        "group_memory 已执行真实只读选择管线",
        metrics={
            "selected_count": len(selection.selected),
            "skipped_count": len(selection.skipped),
        },
    )


def _rag_group_analysis_smoke() -> ScenarioResult:
    from app.group_analysis.local_rag import select_group_analysis_context

    messages = [
        {
            "log_id": index,
            "user_id": f"selfcheck-user-{index % 2}",
            "content": f"群分析自检消息 {index}，讨论检索和日报质量",
        }
        for index in range(12)
    ]
    result = select_group_analysis_context(
        messages,
        query="群分析 检索 日报",
        bundle_size=4,
        lexical_top_k=10,
        reranker_top_k=4,
        neighbor_radius=1,
        budget_chars=1200,
        embedding_provider=None,
        reranker_provider=None,
    )
    value = _mapping_contract(
        result,
        keys=frozenset({"messages", "stats_logs", "prompt_logs"}),
    )
    if value is None:
        return _result(
            "failed",
            "group_analysis_result_contract_invalid",
            "group_analysis 本地 RAG 返回合同不完整",
        )
    stats = value.get("stats_logs")
    selected = value.get("messages")
    selected_count = len(selected) if isinstance(selected, list) else 0
    if not isinstance(stats, dict) or selected_count <= 0:
        return _result(
            "failed",
            "group_analysis_no_selected_messages",
            "group_analysis 本地 RAG 未能从确定性样本选出消息",
            metrics={"selected_count": selected_count},
        )
    return _result(
        "passed",
        "group_analysis_pipeline_smoke_passed",
        "group_analysis 已执行真实临时检索与预算选择管线",
        metrics={
            "bundle_count": int(stats.get("bundle_count") or 0),
            "selected_count": selected_count,
        },
    )


def _rag_source_smoke(
    db: Session,
    source: str,
    *,
    testing: bool,
) -> ScenarioResult:
    if source == "all":
        sources = (
            "memory",
            "memory_digest",
            "session_summary",
            "group_memory",
            "sticker",
            "knowledge",
            "group_analysis",
        )
        results = {
            item: _rag_source_smoke(db, item, testing=testing)
            for item in sources
        }
        counts = Counter(result.status for result in results.values())
        failed = [source_id for source_id, result in results.items() if result.status == "failed"]
        if failed:
            return _result(
                "failed",
                "rag_all_source_smoke_failed",
                "RAG all 聚合覆盖的来源存在执行失败",
                metrics=dict(counts),
                evidence={"failed_sources": failed},
            )
        if counts["inconclusive"]:
            return _result(
                "inconclusive",
                "rag_all_source_smoke_inconclusive",
                "RAG all 部分来源因测试 Schema 不完整而无法实跑",
                metrics=dict(counts),
            )
        return _result(
            "passed",
            "rag_all_source_smoke_passed",
            "RAG all 覆盖的所有来源均完成真实管线冒烟",
            metrics=dict(counts),
        )
    if source == "group_memory":
        return _rag_group_memory_smoke(db)
    if source == "group_analysis":
        return _rag_group_analysis_smoke()
    return _rag_semantic_smoke(db, source, testing=testing)


def run_functional_scenario(
    check_id: str,
    *,
    db: Session,
    testing: bool,
    agent_registry: object | None = None,
    runtime_diagnostics: SelfcheckRuntimeDiagnosticsPort | None = None,
    source_id: str = "",
) -> ScenarioResult:
    """按冻结 check_id 执行场景；未知 ID 直接拒绝。"""

    if check_id == "session.default-gate.functional":
        return _session_default_gate(db)
    if check_id == "agent.routing.functional":
        return _agent_routing(
            agent_registry=agent_registry,
            testing=testing,
            entrypoints=frozenset({"chat", "research", "scheduled"}),
        )
    if check_id == "agent.a2a-routing.functional":
        return _agent_routing(
            agent_registry=agent_registry,
            testing=testing,
            entrypoints=frozenset({"agent_link", "a2a"}),
        )
    if check_id == "tool.runtime-bindings.functional":
        return _tool_runtime_bindings(
            runtime_diagnostics=runtime_diagnostics,
            testing=testing,
        )
    if check_id == "model.route-configuration.functional":
        return _model_route_configuration(
            testing=testing,
            runtime_diagnostics=runtime_diagnostics,
        )
    if check_id.startswith("rag.") and check_id.endswith(".smoke"):
        if not source_id:
            raise ValueError("RAG smoke 缺少 source_id")
        return _rag_source_smoke(db, source_id, testing=testing)
    raise KeyError(f"未知功能自检场景：{check_id}")


__all__ = ["ScenarioResult", "run_functional_scenario"]

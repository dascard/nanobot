"""Bridge 的 Native 构造、过渡工具宿主与 Runtime 选择适配。"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clients.new_api_client import NewAPIClient
from clients.reply_route_chat_completion_adapter import (
    ReplyRouteChatCompletionAdapter,
)
from core.agent_runtime import (
    AgentRuntimeKind,
    AgentRuntimePort,
    AgentRuntimeSelection,
    AgentRuntimeSelectionPolicy,
    NativeAgentRuntime,
    NativeAgentRuntimeConfig,
    RequestRuntimeContext,
    RuntimeModelRoute,
    RuntimeOwnerType,
    RuntimePlanKind,
    RuntimePlanRef,
    RuntimePrincipal,
    runtime_model_route_sha256,
)
from core.runtime.plugin_lifecycle import (
    RuntimePluginManager,
    build_runtime_plugin_manager,
)


logger = logging.getLogger("nanobot.kt.bridge")

if TYPE_CHECKING:
    from core.tracing_context import RuntimeCorrelationTokens


def bridge_agent_id(bridge: Any) -> str:
    """从 creature profile 派生稳定身份，不读取 Runtime 实现名称。"""

    explicit = str(getattr(bridge, "_agent_id", "") or "").strip()
    return explicit or Path(
        str(getattr(bridge, "creature_path", "") or "")
    ).name or "agent"


def bridge_memory_registry_snapshot(bridge: Any):
    """读取已启动 Runtime 的快照，并兼容不启动生命周期的合同夹具。"""

    memory_runtime = getattr(bridge, "_memory_runtime", None)
    if memory_runtime is not None:
        return memory_runtime.registry_snapshot
    from nanobot_kt.memory_runtime import memory_provider_registry_snapshot

    return memory_provider_registry_snapshot()


def bind_bridge_runtime_correlation(
    meta: Mapping[str, Any],
    session_id: str,
    trace_id: str,
    run_id: str,
) -> RuntimeCorrelationTokens:
    """绑定单轮 Bridge 的请求、追踪与 Ledger 关联上下文。"""

    from core.tracing_context import set_runtime_correlation

    message_id = str(meta.get("message_id") or "")
    return set_runtime_correlation(
        request_id=message_id or run_id,
        session_id=session_id,
        turn_id=message_id or str(meta.get("request_id") or run_id),
        trace_id=trace_id,
        run_id=run_id,
    )


def build_bridge_llm_cache_context(
    meta: Mapping[str, Any],
    session_id: str,
    *,
    drop_none: bool = False,
) -> dict[str, Any]:
    """优先读取已审计的 Prompt 前缀上下文，并兼容旧入口。"""

    cached = meta.get("_prompt_cache_context")
    if isinstance(cached, Mapping):
        return dict(cached)
    from core.llm_trace_context import build_llm_cache_context

    return build_llm_cache_context(
        session_id,
        meta.get("context_debug"),
        drop_none=drop_none,
    )


def build_attempt_cache_context(
    cache_context: Mapping[str, Any],
    route_plan: Any,
) -> dict[str, Any]:
    """为单次模型路由附加仅供计量使用的冻结定价。"""

    return {
        **dict(cache_context),
        "cost_input_1m": getattr(route_plan, "cost_input_1m", None),
        "cost_output_1m": getattr(route_plan, "cost_output_1m", None),
    }


def build_native_tool_registry_runtime_info(
    loaded_names: Iterable[str],
) -> dict[str, object]:
    """生成不依赖 KT 配置类型的 Native 工具注册审计快照。"""

    from core.tool_registration import (
        TOOL_REGISTRATION_REGISTRY,
        list_active_tool_registrations,
    )

    loaded = {
        str(name or "").strip()
        for name in loaded_names
        if str(name or "").strip()
    }
    active = {
        registration.name for registration in list_active_tool_registrations()
    }
    missing = sorted(active - loaded)
    unknown = sorted(loaded - active)
    if missing or unknown:
        raise RuntimeError(
            "Native loaded tool 与冻结注册快照不一致："
            f"缺失={missing}，未登记={unknown}"
        )
    snapshot = TOOL_REGISTRATION_REGISTRY.registry_snapshot
    return {
        "runtime_kind": AgentRuntimeKind.NATIVE.value,
        "runtime_loaded": sorted(loaded),
        # 管理端迁移期间保留旧字段；调用方优先读取 runtime_loaded。
        "kt_loaded": sorted(loaded),
        "missing_meta": [],
        "missing_kt": [],
        "declared_yaml": [],
        "projected": sorted(active),
        "removed_declared": [],
        "added_projected": [],
        "generation": snapshot.generation,
        "sha256": snapshot.sha256,
    }


def build_child_bridge(
    bridge_type: Callable[..., object],
    creature_path: str,
    runtime_kind: AgentRuntimeKind,
) -> object:
    if runtime_kind is AgentRuntimeKind.KT:
        return bridge_type(creature_path)
    return bridge_type(creature_path, runtime_kind=runtime_kind)


def build_bridge_agent_runtime(
    bridge: Any,
    *,
    initially_started: bool,
) -> AgentRuntimePort:
    """在兼容 Bridge 外组合 Native／KT 与受管 Plugin Manager。"""

    from core.runtime.event_bus import emit_agent_lifecycle_event

    manager_factory = (
        getattr(bridge, "_runtime_plugin_manager_factory", None)
        or build_runtime_plugin_manager
    )
    runtime_kind = getattr(bridge, "runtime_kind", AgentRuntimeKind.KT)
    if runtime_kind is AgentRuntimeKind.NATIVE:
        runtime_id = f"native:{bridge._runtime_name}"
        runtime, completion_port = build_native_bridge_runtime(
            name=bridge._runtime_name,
            completion_port=bridge._native_completion_port,
            plugin_manager=manager_factory(runtime_id),
        )
        bridge._native_completion_port = completion_port
        return runtime

    agent = getattr(bridge, "_agent", None)
    if agent is None:
        raise RuntimeError("KT Agent 尚未创建")
    config = getattr(agent, "config", None)
    name = str(getattr(config, "name", "") or "agent")
    runtime_id = f"kt:{name}"
    from nanobot_kt.runtime_adapter import build_kt_runtime
    from core import database
    from core.agent_runtime import RuntimeBudgetManager
    from core.permissions import default_session_permission_port
    from core.run_ledger.sinks import SqlAlchemyRuntimeBudgetDecisionSink

    return build_kt_runtime(
        agent,
        runtime_id=runtime_id,
        route_applier=bridge._apply_runtime_model_route,
        event_sinks=(emit_agent_lifecycle_event,),
        initially_started=initially_started,
        output_sink=bridge._output,
        plugin_manager=manager_factory(runtime_id),
        budget_manager=RuntimeBudgetManager(
            sink=SqlAlchemyRuntimeBudgetDecisionSink(
                lambda: database.SessionLocal()
            )
        ),
        permission_port=default_session_permission_port(),
    )


def build_native_bridge_runtime(
    *,
    name: str,
    completion_port: ReplyRouteChatCompletionAdapter | None = None,
    plugin_manager: RuntimePluginManager | None = None,
) -> tuple[AgentRuntimePort, ReplyRouteChatCompletionAdapter]:
    """组合 Native 主循环与框架无关的注册工具执行 Port。"""

    from bootstrap.native_tool_runtime import (
        build_native_tool_execution_port,
    )
    from core.runtime.event_bus import emit_agent_lifecycle_event
    from core.run_recovery import default_runtime_recovery_port
    from core.agent_runtime import RuntimeBudgetManager
    from core.run_ledger.sinks import SqlAlchemyRuntimeBudgetDecisionSink
    from core import database
    from core.permissions import default_session_permission_port
    from core.context_compaction import (
        context_compaction_policy_from_settings,
    )
    from core.tool_result_artifacts import (
        SqlAlchemyToolResultArtifactPublisher,
    )
    from core.tool_registration import list_active_tool_registrations

    resolved_completion = completion_port or ReplyRouteChatCompletionAdapter(
        session_provider=NewAPIClient.get_shared_session,
    )
    tool_names = tuple(
        sorted(
            registration.name
            for registration in list_active_tool_registrations()
        )
    )
    runtime = NativeAgentRuntime(
        resolved_completion,
        build_native_tool_execution_port(),
        runtime_id=f"native:{name}",
        config=NativeAgentRuntimeConfig(
            context_policy=context_compaction_policy_from_settings(),
        ),
        available_tool_names=tool_names,
        event_sinks=(emit_agent_lifecycle_event,),
        recovery_port=default_runtime_recovery_port(),
        tool_result_artifact_publisher=(
            SqlAlchemyToolResultArtifactPublisher()
        ),
        plugin_manager=plugin_manager,
        budget_manager=RuntimeBudgetManager(
            sink=SqlAlchemyRuntimeBudgetDecisionSink(
                lambda: database.SessionLocal()
            )
        ),
        permission_port=default_session_permission_port(),
    )
    return runtime, resolved_completion


def set_bridge_runtime_model_route(
    bridge: Any,
    target_model: str,
    route_plan: Any,
    *,
    unavailable_error: Callable[[str], Exception],
) -> RuntimeModelRoute:
    """冻结候选模型传输参数并同步到当前 Agent Runtime。"""

    temperature_raw = getattr(route_plan, "temperature", None)
    try:
        temperature = (
            float(temperature_raw) if temperature_raw is not None else None
        )
    except (TypeError, ValueError):
        temperature = None
    max_tokens_raw = getattr(route_plan, "max_tokens", None)
    try:
        max_tokens = int(max_tokens_raw) if max_tokens_raw is not None else None
    except (TypeError, ValueError):
        max_tokens = None
    if max_tokens is not None and max_tokens <= 0:
        max_tokens = None
    thinking = getattr(route_plan, "enable_thinking", None)
    if not isinstance(thinking, (str, bool, type(None))):
        thinking = None
    provider_id = str(
        getattr(route_plan, "provider_id", "")
        or getattr(route_plan, "registry_provider", "")
        or "unknown"
    )
    if (
        getattr(bridge, "runtime_kind", AgentRuntimeKind.KT)
        is AgentRuntimeKind.NATIVE
    ):
        completion_port = bridge._native_completion_port
        if completion_port is None:
            raise unavailable_error("Native Chat Completion Adapter 尚未初始化")
        completion_port.bind_route(route_plan)
    bridge._active_route_plan = route_plan
    runtime_route = RuntimeModelRoute(
        route_id="reply/current",
        model_id=target_model,
        provider_id=provider_id,
        profile_id=str(getattr(route_plan, "profile_id", "") or ""),
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=float(getattr(route_plan, "timeout", 120.0) or 120.0),
        enable_thinking=thinking,
    )
    bridge._require_runtime().set_model_route(runtime_route)
    return runtime_route


def bind_native_recovery_model_plan(
    runtime_kind: AgentRuntimeKind,
    context: RequestRuntimeContext,
    route: RuntimeModelRoute,
) -> RequestRuntimeContext:
    """把候选模型预算绑定到单次尝试；Native 另附恢复证明。"""

    governance = context.governance.bind_model(route.model_id)
    if runtime_kind is not AgentRuntimeKind.NATIVE:
        return replace(context, governance=governance)
    from core.run_recovery.proofs import replace_recovery_plan

    return replace(
        context,
        plans=replace_recovery_plan(
            context.plans,
            RuntimePlanRef(
                RuntimePlanKind.MODEL,
                f"model-route:{route.route_id}",
                runtime_model_route_sha256(route),
            ),
        ),
        governance=governance,
    )


def build_request_runtime_plans(
    *,
    agent_id: str,
    runtime_kind: AgentRuntimeKind,
    platform: str,
    user_id: str,
    group_id: str,
    session_id: str,
    is_group: bool,
    runtime_id: str,
    prompt_key: str,
    prompt_sha256: str,
    tool_plan: Any,
    memory_registry_generation: int,
    memory_registry_sha256: str,
) -> tuple[RuntimePlanRef, ...]:
    """生成共用身份作用域；Native 额外固定 Checkpoint Manifest。"""

    from core import database
    from core.run_recovery.proofs import (
        build_live_recovery_plans,
        build_runtime_scope_plans,
    )

    recovery_db = database.SessionLocal()
    try:
        common = {
            "agent_id": agent_id,
            "principal": RuntimePrincipal(
                platform=platform,
                owner_type=(
                    RuntimeOwnerType.GROUP
                    if is_group
                    else RuntimeOwnerType.USER
                ),
                owner_id=(group_id if is_group else user_id) or session_id,
            ),
            "session_id": session_id,
            "chat_type": "group" if is_group else "private",
            "tool_plan": tool_plan,
            "memory_registry_generation": memory_registry_generation,
            "memory_registry_sha256": memory_registry_sha256,
        }
        if runtime_kind is AgentRuntimeKind.NATIVE:
            return build_live_recovery_plans(
                recovery_db,
                runtime_id=runtime_id,
                prompt_key=prompt_key,
                prompt_sha256=prompt_sha256,
                **common,
            )
        return build_runtime_scope_plans(recovery_db, **common)
    finally:
        recovery_db.close()


def compatibility_runtime_selection_policy() -> AgentRuntimeSelectionPolicy:
    """保留直接构造 BridgePool 的 KT 兼容默认值。"""

    return AgentRuntimeSelectionPolicy(
        default_kind=AgentRuntimeKind.KT,
        kt_enabled=True,
    )


def select_runtime_for_bridge_key(
    policy: AgentRuntimeSelectionPolicy,
    key: str,
    *,
    user_id: str = "",
    session_id: str = "",
) -> AgentRuntimeSelection:
    if session_id or user_id:
        return policy.select(session_id=session_id, user_id=user_id)
    return policy.select(session_id=key)


def emit_runtime_selection(
    selection: AgentRuntimeSelection,
    *,
    previous: AgentRuntimeKind | None,
    emitter: Callable[..., object] | None = None,
) -> None:
    if emitter is None:
        from core.runtime.event_bus import emit_runtime_event

        emitter = emit_runtime_event
    emitter(
        "agent.runtime_selection",
        "state_changed",
        attributes={
            "selected_runtime": selection.kind.value,
            "previous_runtime": previous.value if previous is not None else "",
            "selection_reason": selection.reason,
            "scope_sha256": selection.scope_sha256,
            "policy_sha256": selection.policy_sha256,
            "bucket": selection.bucket,
            "changed": previous is not None and previous is not selection.kind,
        },
    )


async def reconcile_selected_bridge(
    pool: Any,
    key: str,
    selection: AgentRuntimeSelection,
    *,
    unavailable_error: Callable[[str], Exception],
) -> object:
    """在无在途请求时原子停止旧 Runtime，并启动唯一选中的 child。"""

    bridge = pool._bridges.get(key)
    current_kind = pool._bridge_runtime_kinds.get(key)
    if bridge is not None and current_kind is not selection.kind:
        if pool._bridge_inflight.get(key, 0) > 0:
            raise unavailable_error("会话仍有在途请求，拒绝切换 Agent Runtime")
        await bridge.stop()
        pool._bridges.pop(key, None)
        pool._bridge_runtime_kinds.pop(key, None)
        pool._bridge_last_used.pop(key, None)
        pool._bridge_inflight.pop(key, None)
        bridge = None
    if bridge is None:
        previous = pool._last_runtime_kinds.get(key)
        bridge = pool._bridge_factory(selection.kind)
        await bridge.start()
        pool._bridges[key] = bridge
        pool._bridge_runtime_kinds[key] = selection.kind
        pool._last_runtime_kinds[key] = selection.kind
        emit_runtime_selection(selection, previous=previous)
        logger.info(
            "[NanobotBridgePool] created child bridge session=%s runtime=%s reason=%s",
            key,
            selection.kind.value,
            selection.reason,
        )
    return bridge


__all__ = [
    "bind_bridge_runtime_correlation",
    "bind_native_recovery_model_plan",
    "bridge_agent_id",
    "bridge_memory_registry_snapshot",
    "build_child_bridge",
    "build_native_bridge_runtime",
    "build_request_runtime_plans",
    "build_native_tool_registry_runtime_info",
    "compatibility_runtime_selection_policy",
    "emit_runtime_selection",
    "reconcile_selected_bridge",
    "select_runtime_for_bridge_key",
    "set_bridge_runtime_model_route",
]

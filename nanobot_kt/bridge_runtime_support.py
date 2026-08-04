"""Bridge 的 Native 构造、过渡工具宿主与 Runtime 选择适配。"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
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
    RequestRuntimeContext,
    RuntimeModelRoute,
    RuntimeOwnerType,
    RuntimePlanKind,
    RuntimePlanRef,
    RuntimePrincipal,
    runtime_model_route_sha256,
)


logger = logging.getLogger("nanobot.kt.bridge")

if TYPE_CHECKING:
    from core.tracing_context import RuntimeCorrelationTokens


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


def build_native_bridge_runtime(
    *,
    name: str,
    completion_port: ReplyRouteChatCompletionAdapter | None = None,
) -> tuple[AgentRuntimePort, ReplyRouteChatCompletionAdapter]:
    """组合 Native 主循环与框架无关的注册工具执行 Port。"""

    from bootstrap.native_tool_runtime import (
        build_native_tool_execution_port,
    )
    from core.runtime.event_bus import emit_agent_lifecycle_event
    from core.run_recovery import default_runtime_recovery_port
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
        available_tool_names=tool_names,
        event_sinks=(emit_agent_lifecycle_event,),
        recovery_port=default_runtime_recovery_port(),
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
    """把候选模型的精确冻结点绑定到 Native 单次尝试。"""

    if runtime_kind is not AgentRuntimeKind.NATIVE:
        return context
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
    )


def build_native_request_recovery_plans(
    *,
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
) -> tuple[RuntimePlanRef, ...]:
    """从实时权威配置生成 Native Checkpoint 的版本证明。"""

    if runtime_kind is not AgentRuntimeKind.NATIVE:
        return ()
    from core import database
    from core.run_recovery.proofs import build_live_recovery_plans

    recovery_db = database.SessionLocal()
    try:
        return build_live_recovery_plans(
            recovery_db,
            principal=RuntimePrincipal(
                platform=platform,
                owner_type=(
                    RuntimeOwnerType.GROUP
                    if is_group
                    else RuntimeOwnerType.USER
                ),
                owner_id=(group_id if is_group else user_id) or session_id,
            ),
            session_id=session_id,
            chat_type="group" if is_group else "private",
            runtime_id=runtime_id,
            prompt_key=prompt_key,
            prompt_sha256=prompt_sha256,
            tool_plan=tool_plan,
        )
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
    "build_child_bridge",
    "build_native_bridge_runtime",
    "build_native_request_recovery_plans",
    "build_native_tool_registry_runtime_info",
    "compatibility_runtime_selection_policy",
    "emit_runtime_selection",
    "reconcile_selected_bridge",
    "select_runtime_for_bridge_key",
    "set_bridge_runtime_model_route",
]

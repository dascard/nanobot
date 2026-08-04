"""Bridge 的 Native 构造、过渡工具宿主与 Runtime 选择适配。"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
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
    )
    return runtime, resolved_completion


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
    "build_child_bridge",
    "build_native_bridge_runtime",
    "build_native_tool_registry_runtime_info",
    "compatibility_runtime_selection_policy",
    "emit_runtime_selection",
    "reconcile_selected_bridge",
    "select_runtime_for_bridge_key",
]

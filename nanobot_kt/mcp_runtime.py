"""MCP 控制面到请求级 ToolPlan、Native 和 KT 的薄适配。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import json
from types import MappingProxyType
from typing import Any, Awaitable

from core.mcp import (
    McpClientFailure,
    McpConfigurationService,
    McpRequestRuntime,
    McpRuntimeService,
    get_current_mcp_runtime,
    reset_current_mcp_runtime,
    set_current_mcp_runtime,
)
from core.tool_plan import ToolPlan, extend_tool_plan
from nanobot_kt.optional_tool_api import BaseTool, ExecutionMode, ToolResult


CleanupRegistrar = Callable[[Callable[[], Awaitable[None]]], None]


@dataclass(frozen=True, slots=True)
class McpBridgeBinding:
    tool_plan: ToolPlan
    runtime: McpRequestRuntime | None
    persistence_pending: bool = False
    run_meta_update: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


class McpProxyTool(BaseTool):
    """KT 只负责调用当前请求绑定；transport 与秘密解析仍由 Core 控制面持有。"""

    def __init__(self, wire_name: str, description: str) -> None:
        super().__init__()
        self._wire_name = str(wire_name)
        self._description = str(description or "")

    @property
    def tool_name(self) -> str:
        return self._wire_name

    @property
    def description(self) -> str:
        return self._description

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(
        self,
        args: dict[str, Any],
        **_kwargs: Any,
    ) -> ToolResult:
        runtime = get_current_mcp_runtime()
        if runtime is None or runtime.descriptor(self.tool_name) is None:
            payload = {
                "status": "error",
                "error": {
                    "code": "mcp_request_binding_missing",
                    "retryable": False,
                },
            }
            return ToolResult(
                output=_json(payload),
                error=_json(payload),
                metadata={"mcp": True, "code": "mcp_request_binding_missing"},
            )
        try:
            result = await runtime.call(self.tool_name, args)
        except McpClientFailure as failure:
            payload = {
                "status": "error",
                "error": {
                    "code": failure.code,
                    "retryable": failure.retryable,
                    "ambiguous": failure.ambiguous,
                },
            }
            return ToolResult(
                output=_json(payload),
                error=_json(payload),
                metadata={
                    "mcp": True,
                    "code": failure.code,
                    "retryable": failure.retryable,
                    "ambiguous": failure.ambiguous,
                },
            )
        output = _json(dict(result.payload))
        return ToolResult(
            output=output,
            error=output if result.is_error else None,
            exit_code=1 if result.is_error else 0,
            metadata={"mcp": True, "code": "mcp_tool_error" if result.is_error else ""},
        )


def _external_registry_names(agent: Any) -> set[str]:
    registry = getattr(agent, "registry", None)
    list_tools = getattr(registry, "list_tools", None)
    if not callable(list_tools):
        return set()
    owned = set(getattr(agent, "_nanobot_mcp_dynamic_tool_names", set()))
    return {str(name) for name in list_tools()} - owned


def _install_kt_tools(agent: Any, runtime: McpRequestRuntime) -> None:
    if agent is None:
        return
    registry = getattr(agent, "registry", None)
    executor = getattr(agent, "executor", None)
    if registry is None or executor is None:
        return
    owned = set(getattr(agent, "_nanobot_mcp_dynamic_tool_names", set()))
    current = set(runtime.descriptors)
    unregister = getattr(registry, "unregister_tool", None)
    if callable(unregister):
        for stale in sorted(owned - current):
            unregister(stale)
    for descriptor in runtime.snapshot.tools:
        existing = registry.get_tool(descriptor.wire_name)
        if existing is not None and descriptor.wire_name not in owned:
            raise ValueError("MCP 工具与 KT 现有 registry 冲突")
        tool = McpProxyTool(descriptor.wire_name, descriptor.description)
        registry.register_tool(tool)
        executor.register_tool(tool)
    agent._nanobot_mcp_dynamic_tool_names = current


def _activate_runtime(
    runtime: McpRequestRuntime,
    *,
    agent: Any,
    cleanup_registrar: CleanupRegistrar | None,
) -> None:
    _install_kt_tools(agent, runtime)
    if cleanup_registrar is None:
        return
    token = set_current_mcp_runtime(runtime)

    async def reset_runtime() -> None:
        reset_current_mcp_runtime(token)

    cleanup_registrar(reset_runtime)


async def build_mcp_bridge_binding(
    *,
    db: Any,
    tool_plan: ToolPlan,
    runtime_chat_type: str,
    platform: str,
    session_id: str,
    session_goal_mode: str = "",
    agent: Any = None,
    cleanup_registrar: CleanupRegistrar | None = None,
    client: Any = None,
    session_factory: Callable[[], Any] | None = None,
) -> McpBridgeBinding:
    """发现健康 server 并把其冻结 schema 直接并入生产 ToolPlan。"""

    if str(session_goal_mode or "").strip().lower() == "plan":
        return McpBridgeBinding(
            tool_plan=tool_plan,
            runtime=None,
            run_meta_update=MappingProxyType({
                "mcp_tool_count": 0,
                "mcp_disabled_reason": "plan_mode",
            }),
        )
    if db is None:
        return McpBridgeBinding(
            tool_plan=tool_plan,
            runtime=None,
            run_meta_update=MappingProxyType({
                "mcp_tool_count": 0,
                "mcp_disabled_reason": "control_plane_unavailable",
            }),
        )
    configuration = McpConfigurationService(db).snapshot()
    enabled_count = sum(1 for item in configuration.servers if item.enabled)
    if enabled_count == 0:
        return McpBridgeBinding(
            tool_plan=tool_plan,
            runtime=None,
            run_meta_update=MappingProxyType({
                "mcp_configuration_revision": configuration.revision,
                "mcp_configuration_sha256": configuration.sha256,
                "mcp_configured_server_count": 0,
                "mcp_healthy_server_count": 0,
                "mcp_failed_server_count": 0,
                "mcp_cached_server_count": 0,
                "mcp_tool_count": 0,
            }),
        )
    if client is None:
        try:
            from clients.mcp import McpSdkClient
        except ModuleNotFoundError:
            return McpBridgeBinding(
                tool_plan=tool_plan,
                runtime=None,
                run_meta_update=MappingProxyType({
                    "mcp_configuration_revision": configuration.revision,
                    "mcp_configuration_sha256": configuration.sha256,
                    "mcp_configured_server_count": enabled_count,
                    "mcp_healthy_server_count": 0,
                    "mcp_failed_server_count": enabled_count,
                    "mcp_cached_server_count": 0,
                    "mcp_tool_count": 0,
                    "mcp_disabled_reason": "transport_sdk_unavailable",
                }),
            )

        client = McpSdkClient()
    if session_factory is None:
        from core.database import session_factory_from_session

        session_factory = session_factory_from_session(db)
    existing_names = set(tool_plan.enabled)
    existing_names.update(_external_registry_names(agent))
    result = await McpRuntimeService(
        db,
        client=client,
        session_factory=session_factory,
    ).build_request(existing_tool_names=existing_names)
    if result.runtime is None:
        return McpBridgeBinding(
            tool_plan=tool_plan,
            runtime=None,
            persistence_pending=result.persistence_pending,
            run_meta_update=MappingProxyType({
                "mcp_configuration_revision": result.configuration.revision,
                "mcp_configuration_sha256": result.configuration.sha256,
                "mcp_configured_server_count": result.configured_server_count,
                "mcp_healthy_server_count": 0,
                "mcp_failed_server_count": result.failed_server_count,
                "mcp_cached_server_count": result.cached_server_count,
                "mcp_tool_count": 0,
            }),
        )
    plan = extend_tool_plan(
        tool_plan,
        result.tool_schemas,
        chat_type=runtime_chat_type,
        platform=platform,
        session_id=session_id,
        db=db,
    )
    _activate_runtime(
        result.runtime,
        agent=agent,
        cleanup_registrar=cleanup_registrar,
    )
    return McpBridgeBinding(
        tool_plan=plan,
        runtime=result.runtime,
        persistence_pending=result.persistence_pending,
        run_meta_update=MappingProxyType({
            "mcp_configuration_revision": result.configuration.revision,
            "mcp_configuration_sha256": result.configuration.sha256,
            "mcp_snapshot_sha256": result.snapshot.snapshot_sha256,
            "mcp_configured_server_count": result.configured_server_count,
            "mcp_healthy_server_count": result.healthy_server_count,
            "mcp_failed_server_count": result.failed_server_count,
            "mcp_cached_server_count": result.cached_server_count,
            "mcp_tool_count": len(result.snapshot.tools),
        }),
    )


__all__ = [
    "McpBridgeBinding",
    "McpProxyTool",
    "build_mcp_bridge_binding",
]

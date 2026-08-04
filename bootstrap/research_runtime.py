"""主动研究 Runtime 的 Native／可选 KT 组合适配。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.agent_runtime import AgentRuntimeKind
from core.agent_runtime.gateway_contracts import ResearchAgentRuntimePort


class NativeResearchRuntime:
    """在 Native Runtime 上安装同一个框架无关研究预算守卫。"""

    def __init__(self, bridge: Any) -> None:
        self._bridge = bridge

    async def start(self) -> None:
        await self._bridge.start()

    async def stop(self) -> None:
        await self._bridge.stop()

    async def handle_message(self, query: str, **kwargs: Any) -> str:
        return await self._bridge.handle_message(query, **kwargs)

    def research_tool_guards_ready(self) -> bool:
        runtime = getattr(self._bridge, "_runtime", None)
        if runtime is None:
            return False
        status = runtime.install_tool_policy()
        return bool(status.guard_installed and status.schema_filter_installed)

    def install_research_budget_guard(self, guard: object) -> bool:
        runtime = getattr(self._bridge, "_runtime", None)
        install = getattr(runtime, "install_tool_guard", None)
        return bool(callable(install) and install(guard))


def build_research_runtime_factory(
    isolated_bridge_factory: Callable[[], Any],
) -> Callable[[], ResearchAgentRuntimePort]:
    """按冻结的默认 Runtime 构造研究实例，不在 Native 路径导入 KT。"""

    def create() -> ResearchAgentRuntimePort:
        bridge = isolated_bridge_factory()
        runtime_kind = getattr(bridge, "runtime_kind", AgentRuntimeKind.KT)
        if runtime_kind is AgentRuntimeKind.NATIVE:
            return NativeResearchRuntime(bridge)
        from nanobot_kt.research_runtime import KtResearchRuntime

        return KtResearchRuntime(bridge=bridge)

    return create


__all__ = ["NativeResearchRuntime", "build_research_runtime_factory"]

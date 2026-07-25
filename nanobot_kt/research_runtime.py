"""KT 主动研究运行时 Adapter。

KT 私有 Agent 字段、Plugin 基类和 ``PluginBlockError`` 只停留在本文件；
``core.proactive_research`` 只看到框架无关的预算守卫和公开方法。
"""

from __future__ import annotations

from typing import Any

from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
)

from core.proactive_research import (
    ResearchBudgetPlugin,
    ResearchToolBlockError,
)
from nanobot_kt.bridge import NanobotBridge
from nanobot_kt.tool_runtime import tool_plan_runtime_status


class _KtResearchBudgetPlugin(BasePlugin):
    name = "nanobot_research_budget"
    priority = 1

    def __init__(self, guard: ResearchBudgetPlugin) -> None:
        super().__init__()
        self._guard = guard

    async def pre_tool_execute(
        self,
        args: dict,
        **kwargs: Any,
    ) -> dict | None:
        try:
            return await self._guard.pre_tool_execute(args, **kwargs)
        except ResearchToolBlockError as exc:
            raise PluginBlockError(str(exc)) from exc

    async def pre_subagent_run(
        self,
        task: str,
        **kwargs: Any,
    ) -> str | None:
        try:
            return await self._guard.pre_subagent_run(task, **kwargs)
        except ResearchToolBlockError as exc:
            raise PluginBlockError(str(exc)) from exc


class KtResearchRuntime:
    """把单次 ``NanobotBridge`` 暴露为研究任务所需的窄接口。"""

    def __init__(self) -> None:
        self._bridge = NanobotBridge()

    async def start(self) -> None:
        await self._bridge.start()

    async def stop(self) -> None:
        await self._bridge.stop()

    async def handle_message(
        self,
        query: str,
        **kwargs: Any,
    ) -> str:
        return await self._bridge.handle_message(query, **kwargs)

    def research_tool_guards_ready(self) -> bool:
        agent = self._bridge._agent
        return bool(tool_plan_runtime_status(agent).get("ready"))

    def install_research_budget_guard(
        self,
        guard: ResearchBudgetPlugin,
    ) -> bool:
        agent = self._bridge._agent
        manager = getattr(agent, "plugins", None)
        if manager is None or not hasattr(manager, "register"):
            return False
        manager.register(_KtResearchBudgetPlugin(guard))
        return True


def create_research_runtime() -> KtResearchRuntime:
    return KtResearchRuntime()


__all__ = [
    "KtResearchRuntime",
    "create_research_runtime",
]

"""KT 工具运行时适配。

这里只放请求级 ToolPlan 与 KT 插件/执行链的适配逻辑，不负责解析业务配置。
"""

from __future__ import annotations

import logging
from typing import Any

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginBlockError

from core.tool_plan import ToolPlanExecutionError, get_current_tool_plan

logger = logging.getLogger("nanobot.kt.tool_runtime")


class ToolPlanGuardPlugin(BasePlugin):
    """按当前请求 ToolPlan 拦截禁用工具调用。"""

    name = "nanobot_tool_plan_guard"
    priority = 0

    def _ensure_allowed(self, tool_name: str) -> None:
        plan = get_current_tool_plan()
        if plan is None:
            return
        try:
            plan.ensure_executable(tool_name)
        except ToolPlanExecutionError as exc:
            logger.warning("Tool blocked by ToolPlan", extra={"tool_name": tool_name})
            raise PluginBlockError(str(exc)) from exc

    async def pre_tool_dispatch(self, call: Any, context: Any = None) -> Any | None:
        self._ensure_allowed(str(getattr(call, "name", "") or ""))
        return None

    async def pre_tool_execute(self, args: dict, **kwargs: Any) -> dict | None:
        self._ensure_allowed(str(kwargs.get("tool_name", "") or ""))
        return None


def install_tool_plan_guard(agent: Any) -> bool:
    """把 ToolPlan 守卫注册到 KT PluginManager。

    返回 True 表示本次新注册；已存在或无法注册时返回 False。
    """
    manager = getattr(agent, "plugins", None)
    if manager is None or not hasattr(manager, "register"):
        return False
    plugins = list(getattr(manager, "_plugins", []) or [])
    if any(getattr(plugin, "name", "") == ToolPlanGuardPlugin.name for plugin in plugins):
        return False
    manager.register(ToolPlanGuardPlugin())
    return True

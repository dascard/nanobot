"""KT 工具运行时适配。

这里只放请求级 ToolPlan 与 KT 插件/执行链的适配逻辑，不负责解析业务配置。
"""

from __future__ import annotations

import logging
from typing import Any

from kohakuterrarium.llm.base import ToolSchema
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


def _tool_schema_name(schema: dict[str, Any]) -> str:
    function = schema.get("function")
    if isinstance(function, dict) and function.get("name"):
        return str(function.get("name") or "")
    if schema.get("name"):
        return str(schema.get("name") or "")
    return ""


def _tool_schema_description(schema: dict[str, Any]) -> str:
    function = schema.get("function")
    if isinstance(function, dict):
        return str(function.get("description") or "")
    return str(schema.get("description") or "")


def _tool_schema_parameters(schema: dict[str, Any]) -> dict[str, Any]:
    function = schema.get("function")
    if isinstance(function, dict) and isinstance(function.get("parameters"), dict):
        return dict(function["parameters"])
    if isinstance(schema.get("parameters"), dict):
        return dict(schema["parameters"])
    return {"type": "object", "properties": {}}


def _tool_plan_native_schemas(plan: Any) -> list[ToolSchema]:
    schemas: list[ToolSchema] = []
    for item in list(getattr(plan, "sent_tool_schemas", []) or []):
        if not isinstance(item, dict):
            continue
        name = _tool_schema_name(item).strip()
        if not name:
            continue
        schemas.append(
            ToolSchema(
                name=name,
                description=_tool_schema_description(item),
                parameters=_tool_schema_parameters(item),
            )
        )
    return schemas


def install_tool_plan_native_schema_filter(agent: Any) -> bool:
    """让 KT native tools schema 使用当前请求的 ToolPlan。

    KT 默认从 registry 生成 schema；同名内置工具会覆盖 package 工具自己的参数定义。
    这里在 controller 层做一次轻量适配，使发给模型的 API tools 与 ToolPlan 保持一致。
    """

    controller = getattr(agent, "controller", None)
    if controller is None or not hasattr(controller, "_get_native_tool_schemas"):
        return False
    if getattr(controller, "_nanobot_tool_plan_schema_filter_installed", False):
        return False

    original_get_native_tool_schemas = controller._get_native_tool_schemas

    def _get_native_tool_schemas_with_tool_plan() -> list[ToolSchema]:
        plan = get_current_tool_plan()
        if plan is not None:
            return _tool_plan_native_schemas(plan)
        return original_get_native_tool_schemas()

    controller._nanobot_original_get_native_tool_schemas = original_get_native_tool_schemas
    controller._get_native_tool_schemas = _get_native_tool_schemas_with_tool_plan
    controller._nanobot_tool_plan_schema_filter_installed = True
    return True

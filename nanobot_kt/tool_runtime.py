"""KT 工具运行时适配。

这里只放请求级 ToolPlan 与 KT 插件/执行链的适配逻辑，不负责解析业务配置。
"""

from __future__ import annotations

import copy
import json
import logging
from typing import Any

from kohakuterrarium.llm.base import ToolSchema
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginBlockError

from core.tool_plan import ToolPlanExecutionError, get_current_tool_plan
from core.tool_execution_policy import (
    FINAL_ACTION_TOOLS,
    get_current_tool_execution_state,
)

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

    async def pre_subagent_run(self, task: str, **kwargs: Any) -> str | None:
        """SubAgentCallEvent 也必须服从同一请求级 ToolPlan。"""

        self._ensure_allowed(str(kwargs.get("name", "") or ""))
        return None


class ToolLoopControlPlugin(BasePlugin):
    """抑制不可重试的同参调用和授权失败后的同族调用。"""

    name = "nanobot_tool_loop_control"
    priority = 1

    @staticmethod
    def _blocked_result(code: str, summary: str) -> str:
        return json.dumps(
            {
                "status": "error",
                "summary": summary,
                "next_actions": [],
                "artifacts": [],
                "error": {
                    "code": code,
                    "retryable": False,
                    "hint": "请调整参数或改用其他操作，不要重复相同调用",
                    "stop": True,
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    async def pre_tool_execute(self, args: dict, **kwargs: Any) -> dict | None:
        state = get_current_tool_execution_state()
        if state is None:
            return None
        tool_name = str(kwargs.get("tool_name", "") or "")
        if state.final_action_only and tool_name in FINAL_ACTION_TOOLS:
            return None
        if state.final_action_only:
            raise PluginBlockError(
                self._blocked_result(
                    "final_action_tool_forbidden",
                    "最终回复阶段禁止重新进入普通工具循环",
                )
            )
        family_failure = state.family_failure(tool_name)
        if family_failure is not None:
            state.family_suppressed += 1
            raise PluginBlockError(
                self._blocked_result(
                    "tool_family_blocked",
                    (
                        f"已停止调用 {tool_name} 所属工具族："
                        f"{family_failure.summary}"
                    ),
                )
            )
        previous = state.duplicate_failure(tool_name, args)
        if previous is None:
            return None
        state.duplicate_suppressed += 1
        raise PluginBlockError(
            self._blocked_result(
                "duplicate_non_retryable_call",
                f"已阻止重复执行不可重试的 {tool_name} 调用：{previous.summary}",
            )
        )

    async def post_tool_execute(self, result: Any, **kwargs: Any) -> Any | None:
        state = get_current_tool_execution_state()
        if state is None:
            return None
        state.record_result(
            str(kwargs.get("tool_name", "") or ""),
            kwargs.get("args", {}),
            result,
        )
        return None


def tool_plan_runtime_status(agent: Any) -> dict[str, Any]:
    """检查 ToolPlan 执行守卫与原生 schema 过滤器的最终安装状态。"""

    manager = getattr(agent, "plugins", None)
    plugins = list(getattr(manager, "_plugins", []) or [])
    guard_marker = bool(
        getattr(agent, "__dict__", {}).get(
            "_nanobot_tool_plan_guard_installed",
            False,
        )
    )
    guard_installed = guard_marker or any(
        str(getattr(plugin, "name", "") or "") == ToolPlanGuardPlugin.name
        for plugin in plugins
    )
    controller = getattr(agent, "controller", None)
    schema_filter_installed = bool(
        getattr(controller, "__dict__", {}).get(
            "_nanobot_tool_plan_schema_filter_installed",
            False,
        )
    )
    missing = []
    if not guard_installed:
        missing.append("guard")
    if not schema_filter_installed:
        missing.append("native_schema_filter")
    return {
        "ready": not missing,
        "guard_installed": guard_installed,
        "schema_filter_installed": schema_filter_installed,
        "missing": missing,
    }


def ensure_tool_plan_runtime(agent: Any) -> None:
    """ToolPlan 运行时组件缺失时失败关闭。"""

    status = tool_plan_runtime_status(agent)
    if not status["ready"]:
        raise RuntimeError(
            "ToolPlan runtime missing: " + ", ".join(status["missing"])
        )


def install_tool_plan_guard(agent: Any) -> bool:
    """把 ToolPlan 守卫注册到 KT PluginManager。

    返回 True 表示本次新注册；已存在或无法注册时返回 False。
    """
    manager = getattr(agent, "plugins", None)
    if manager is None or not hasattr(manager, "register"):
        return False
    plugins = list(getattr(manager, "_plugins", []) or [])
    if any(getattr(plugin, "name", "") == ToolPlanGuardPlugin.name for plugin in plugins):
        agent._nanobot_tool_plan_guard_installed = True
        return False
    manager.register(ToolPlanGuardPlugin())
    agent._nanobot_tool_plan_guard_installed = True
    return True


def install_tool_loop_control(agent: Any) -> bool:
    """安装请求级工具循环控制插件。"""

    manager = getattr(agent, "plugins", None)
    if manager is None or not hasattr(manager, "register"):
        return False
    plugins = list(getattr(manager, "_plugins", []) or [])
    if any(
        getattr(plugin, "name", "") == ToolLoopControlPlugin.name
        for plugin in plugins
    ):
        agent._nanobot_tool_loop_control_installed = True
        return False
    manager.register(ToolLoopControlPlugin())
    agent._nanobot_tool_loop_control_installed = True
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
        return copy.deepcopy(function["parameters"])
    if isinstance(schema.get("parameters"), dict):
        return copy.deepcopy(schema["parameters"])
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
    if getattr(controller, "__dict__", {}).get(
        "_nanobot_tool_plan_schema_filter_installed",
        False,
    ):
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

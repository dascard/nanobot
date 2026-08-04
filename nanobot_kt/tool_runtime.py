"""KT 工具运行时适配。

这里只放请求级 ToolPlan 与 KT 插件/执行链的适配逻辑，不负责解析业务配置。
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import AsyncIterator
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.llm.base import ToolSchema
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginBlockError

from core.agent_runtime import (
    AgentTurnRequest,
    PermissionPort,
    RuntimeBudgetAccount,
    RuntimeBudgetReservation,
)
from core.tool_execution_policy import (
    FINAL_ACTION_TOOLS,
    get_current_tool_execution_state,
)
from core.tool_plan import ToolPlanExecutionError, get_current_tool_plan

logger = logging.getLogger("nanobot.kt.tool_runtime")


class ToolPlanProviderAdapter:
    """在 KT 的公开 Provider 边界应用请求级 ToolPlan schema。

    KT Controller 会从 Registry 生成 native schema，但 Nanobot 的最终权限
    事实源是请求级 ToolPlan。Adapter 只改写公开 ``chat(..., tools=...)``
    参数，不替换 Controller 方法，也不读取 Registry 私有状态。
    """

    def __init__(self, provider: Any) -> None:
        self.provider = provider

    def __getattr__(self, name: str) -> Any:
        return getattr(self.provider, name)

    @property
    def model(self) -> str:
        value = getattr(self.provider, "model", None)
        if value is not None:
            return str(value)
        return str(getattr(getattr(self.provider, "config", None), "model", ""))

    @model.setter
    def model(self, value: str) -> None:
        if hasattr(self.provider, "model"):
            self.provider.model = value
        config = getattr(self.provider, "config", None)
        if config is not None and hasattr(config, "model"):
            config.model = value

    @property
    def prompt_cache_key(self) -> str | None:
        return getattr(self.provider, "prompt_cache_key", None)

    @prompt_cache_key.setter
    def prompt_cache_key(self, value: str | None) -> None:
        if hasattr(self.provider, "prompt_cache_key"):
            self.provider.prompt_cache_key = value

    async def chat(
        self,
        messages: list[Any],
        *,
        stream: bool = True,
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        plan = get_current_tool_plan()
        effective_tools = (
            _tool_plan_native_schemas(plan) if plan is not None else tools
        )
        async for chunk in self.provider.chat(
            messages,
            stream=stream,
            tools=effective_tools,
            **kwargs,
        ):
            yield chunk

    async def chat_complete(self, messages: list[Any], **kwargs: Any) -> Any:
        return await self.provider.chat_complete(messages, **kwargs)

    def on_emergency_drop(self, callback: Any) -> None:
        self.provider.on_emergency_drop(callback)

    def translate_provider_native_tool(self, tool: Any) -> dict | None:
        return self.provider.translate_provider_native_tool(tool)

    def with_model(self, name: str) -> "ToolPlanProviderAdapter":
        replacement = self.provider.with_model(name)
        if replacement is self.provider:
            return self
        return ToolPlanProviderAdapter(replacement)

    async def close(self) -> None:
        close = getattr(self.provider, "close", None)
        if not callable(close):
            return
        result = close()
        if hasattr(result, "__await__"):
            await result


def wrap_tool_plan_provider(provider: Any) -> ToolPlanProviderAdapter:
    if isinstance(provider, ToolPlanProviderAdapter):
        return provider
    return ToolPlanProviderAdapter(provider)


def _get_registered_plugin(manager: Any, name: str) -> Any | None:
    """只通过 KT 1.4 PluginManager 的公开查询入口读取插件。"""

    getter = getattr(manager, "get_plugin", None)
    if not callable(getter):
        return None
    return getter(name)


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


@dataclass(slots=True)
class _PermissionTurnState:
    failure: BaseException | None = None


class PermissionGuardPlugin(BasePlugin):
    """把 KT 工具执行映射到同一个持久 PermissionPort。"""

    name = "nanobot_permission_guard"
    # ToolPlan(0) 与重复调用控制(1) 先收窄请求，再做动态权限决定。
    priority = 5

    def __init__(
        self,
        permission_port: PermissionPort,
        request_provider: Any,
    ) -> None:
        super().__init__()
        if not isinstance(permission_port, PermissionPort):
            raise TypeError("permission_port 必须实现 PermissionPort")
        if not callable(request_provider):
            raise TypeError("request_provider 必须可调用")
        self._permission_port = permission_port
        self._request_provider = request_provider
        self._turn_state: ContextVar[_PermissionTurnState | None] = ContextVar(
            f"nanobot_kt_permission_failure_{id(self)}",
            default=None,
        )

    def begin_turn(self) -> Token[_PermissionTurnState | None]:
        return self._turn_state.set(_PermissionTurnState())

    def end_turn(self, token: Token[_PermissionTurnState | None]) -> None:
        self._turn_state.reset(token)

    def raise_deferred_failure(self) -> None:
        state = self._turn_state.get()
        if state is not None and state.failure is not None:
            raise state.failure

    def _defer(self, exc: BaseException) -> None:
        state = self._turn_state.get()
        if state is None:
            state = _PermissionTurnState()
            self._turn_state.set(state)
        if state.failure is None:
            state.failure = exc

    async def pre_tool_execute(
        self,
        args: dict,
        **kwargs: Any,
    ) -> dict | None:
        del args
        request = self._request_provider()
        if not isinstance(request, AgentTurnRequest):
            raise PluginBlockError("runtime_permission_context_missing")
        from core.permissions import authorize_tool_execution

        try:
            await authorize_tool_execution(
                self._permission_port,
                context=request.context,
                tool_name=str(kwargs.get("tool_name", "") or ""),
                tool_call_id=str(kwargs.get("job_id", "") or "kt-tool-call"),
            )
        except Exception as exc:
            self._defer(exc)
            raise PluginBlockError("runtime_permission_denied") from exc
        return None


@dataclass(slots=True)
class _BudgetTurnState:
    failure: BaseException | None = None
    reservations: dict[str, RuntimeBudgetReservation] = field(
        default_factory=dict
    )


class RuntimeBudgetGuardPlugin(BasePlugin):
    """在 KT 公开 Plugin 边界执行模型、工具和 Subagent 硬预算。"""

    name = "nanobot_runtime_budget_guard"
    # 放在受管 Hook 之后，确保预算批准是原始调用前的最后一道门禁。
    priority = 20_000

    def __init__(self, budget_provider: Any, request_provider: Any) -> None:
        super().__init__()
        if not callable(budget_provider):
            raise TypeError("budget_provider 必须可调用")
        if not callable(request_provider):
            raise TypeError("request_provider 必须可调用")
        self._budget_provider = budget_provider
        self._request_provider = request_provider
        self._turn_state: ContextVar[_BudgetTurnState | None] = ContextVar(
            f"nanobot_kt_budget_failure_{id(self)}",
            default=None,
        )

    def begin_turn(self) -> Token[_BudgetTurnState | None]:
        return self._turn_state.set(_BudgetTurnState())

    def end_turn(self, token: Token[_BudgetTurnState | None]) -> None:
        state = self._turn_state.get()
        if state is not None:
            budget = self._budget_provider()
            if isinstance(budget, RuntimeBudgetAccount):
                for reservation in tuple(state.reservations.values()):
                    try:
                        budget.release(reservation)
                    except Exception:
                        pass
            state.reservations.clear()
        self._turn_state.reset(token)

    def raise_deferred_failure(self) -> None:
        state = self._turn_state.get()
        if state is not None and state.failure is not None:
            raise state.failure

    def _state(self) -> _BudgetTurnState:
        state = self._turn_state.get()
        if state is None:
            raise PluginBlockError("runtime_budget_turn_state_missing")
        return state

    def _defer(self, exc: BaseException) -> None:
        state = self._turn_state.get()
        if state is None:
            state = _BudgetTurnState()
            self._turn_state.set(state)
        if state.failure is None:
            state.failure = exc

    def _budget(self) -> RuntimeBudgetAccount:
        budget = self._budget_provider()
        if not isinstance(budget, RuntimeBudgetAccount):
            raise PluginBlockError("runtime_budget_context_missing")
        return budget

    def _model_id(self, raw_model: object) -> str:
        model_id = str(raw_model or "").strip()
        if model_id:
            return model_id
        request = self._request_provider()
        if isinstance(request, AgentTurnRequest):
            allowed = request.context.governance.budgets.turn.allowed_model_ids
            if len(allowed) == 1:
                return allowed[0]
        return "host-bound"

    @staticmethod
    def _usage(raw: object) -> Any:
        if not isinstance(raw, dict):
            return None
        from core.agent_runtime import RuntimeUsage

        def nonnegative(*names: str) -> int:
            for name in names:
                try:
                    value = int(raw.get(name, 0) or 0)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    return value
            return 0

        input_tokens = nonnegative("prompt_tokens", "input_tokens")
        output_tokens = nonnegative("completion_tokens", "output_tokens")
        if input_tokens == 0 and output_tokens == 0:
            input_tokens = nonnegative("total_tokens")
        usage = RuntimeUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=nonnegative(
                "cached_tokens",
                "cached_input_tokens",
            ),
            reasoning_tokens=nonnegative("reasoning_tokens"),
            cost_microunits=nonnegative("cost_microunits"),
        )
        if not usage.total_tokens and not usage.cost_microunits:
            return None
        return usage

    async def pre_llm_call(
        self,
        messages: list[dict],
        **kwargs: Any,
    ) -> list[dict] | None:
        del messages
        try:
            self._budget().reserve_model(self._model_id(kwargs.get("model")))
        except Exception as exc:
            self._defer(exc)
            raise PluginBlockError("runtime_model_budget_denied") from exc
        return None

    async def post_llm_call(
        self,
        messages: list[dict],
        response: str,
        usage: dict,
        **kwargs: Any,
    ) -> str | None:
        del messages, response, kwargs
        normalized = self._usage(usage)
        if normalized is not None:
            try:
                self._budget().record_usage(normalized)
            except Exception as exc:
                # KT 会隔离 post hook 异常；Adapter 在 inject_event 返回后
                # 重新抛出这个失败，避免预算超限退化成观测日志。
                self._defer(exc)
        return None

    async def pre_tool_execute(
        self,
        args: dict,
        **kwargs: Any,
    ) -> dict | None:
        del args
        try:
            state = self._state()
            tool_name = str(kwargs.get("tool_name", "") or "")
            job_id = str(kwargs.get("job_id", "") or "").strip()
            reservation_key = job_id or f"tool:{tool_name}:{id(state)}"
            if reservation_key in state.reservations:
                raise RuntimeError("KT 工具 budget reservation 重复")
            reservation = self._budget().reserve_tool(
                tool_name
            )
            state.reservations[reservation_key] = reservation
        except Exception as exc:
            self._defer(exc)
            raise PluginBlockError("runtime_tool_budget_denied") from exc
        return None

    async def post_tool_execute(
        self,
        result: Any,
        **kwargs: Any,
    ) -> Any | None:
        del result
        state = self._state()
        tool_name = str(kwargs.get("tool_name", "") or "")
        job_id = str(kwargs.get("job_id", "") or "").strip()
        reservation_key = job_id or f"tool:{tool_name}:{id(state)}"
        reservation = state.reservations.pop(reservation_key, None)
        if reservation is not None:
            try:
                self._budget().release(reservation)
            except Exception as exc:
                self._defer(exc)
        return None

    async def pre_subagent_run(self, task: str, **kwargs: Any) -> str | None:
        del task
        try:
            self._budget().reserve_subagent(
                str(kwargs.get("name", "") or "subagent")
            )
        except Exception as exc:
            self._defer(exc)
            raise PluginBlockError("runtime_subagent_budget_denied") from exc
        return None


def tool_plan_runtime_status(agent: Any) -> dict[str, Any]:
    """检查 ToolPlan 执行守卫与原生 schema 过滤器的最终安装状态。"""

    manager = getattr(agent, "plugins", None)
    guard_marker = bool(
        getattr(agent, "__dict__", {}).get(
            "_nanobot_tool_plan_guard_installed",
            False,
        )
    )
    guard_installed = guard_marker or _get_registered_plugin(
        manager,
        ToolPlanGuardPlugin.name,
    ) is not None
    schema_filter_installed = bool(
        getattr(agent, "__dict__", {}).get(
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
    if _get_registered_plugin(manager, ToolPlanGuardPlugin.name) is not None:
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
    if _get_registered_plugin(manager, ToolLoopControlPlugin.name) is not None:
        agent._nanobot_tool_loop_control_installed = True
        return False
    manager.register(ToolLoopControlPlugin())
    agent._nanobot_tool_loop_control_installed = True
    return True


def install_permission_guard(
    agent: Any,
    permission_port: PermissionPort,
    request_provider: Any,
) -> bool:
    """安装统一权限守卫；同一 KT Agent 只允许一个权威实例。"""

    manager = getattr(agent, "plugins", None)
    if manager is None or not hasattr(manager, "register"):
        return False
    if _get_registered_plugin(manager, PermissionGuardPlugin.name) is not None:
        agent._nanobot_permission_guard_installed = True
        return False
    manager.register(PermissionGuardPlugin(permission_port, request_provider))
    agent._nanobot_permission_guard_installed = True
    return True


def install_runtime_budget_guard(
    agent: Any,
    budget_provider: Any,
    request_provider: Any,
) -> bool:
    """安装 KT 统一预算门禁；同一 Agent 只注册一次。"""

    manager = getattr(agent, "plugins", None)
    if manager is None or not hasattr(manager, "register"):
        return False
    if _get_registered_plugin(manager, RuntimeBudgetGuardPlugin.name) is not None:
        agent._nanobot_runtime_budget_guard_installed = True
        return False
    manager.register(RuntimeBudgetGuardPlugin(budget_provider, request_provider))
    agent._nanobot_runtime_budget_guard_installed = True
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

    KT 默认从 Registry 生成 schema；同名内置工具会覆盖 package 工具自己的
    参数定义。这里包装公开 Provider.chat 边界，使最终请求与 ToolPlan 一致。
    """

    controller = getattr(agent, "controller", None)
    provider = getattr(controller, "llm", None)
    if controller is None or provider is None:
        return False
    if isinstance(provider, ToolPlanProviderAdapter):
        agent._nanobot_tool_plan_schema_filter_installed = True
        return False

    wrapped = wrap_tool_plan_provider(provider)
    controller.llm = wrapped
    if getattr(agent, "llm", None) is provider:
        agent.llm = wrapped
    subagents = getattr(agent, "subagent_manager", None)
    if getattr(subagents, "llm", None) is provider:
        subagents.llm = wrapped
    agent._nanobot_tool_plan_schema_filter_installed = True
    return True

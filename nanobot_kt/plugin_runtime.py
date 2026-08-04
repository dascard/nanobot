"""受管 Nanobot Plugin 生命周期到 KT 1.4 公开 Plugin API 的适配。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextvars import ContextVar, Token
from typing import Any

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginBlockError

from core.agent_runtime.contracts import AgentTurnRequest
from core.agent_runtime.plugin_hooks import runtime_hook_invariants
from core.runtime.plugin_lifecycle import (
    RuntimeHookPoint,
    RuntimePluginManager,
    thaw_runtime_hook_value,
)


_DeferredFailure = BaseException | None


class ManagedKtRuntimePlugin(BasePlugin):
    """让 KT 模型／工具调用服从同一受管 Hook Manager。"""

    name = "nanobot_managed_runtime_plugins"
    # ToolPlan(0) 与循环守卫(1) 必须先运行；受管 Hook 不能改写工具名称。
    priority = 10_000

    def __init__(
        self,
        manager: RuntimePluginManager,
        request_provider: Callable[[], AgentTurnRequest | None],
    ) -> None:
        super().__init__()
        self._manager = manager
        self._request_provider = request_provider
        self._deferred_failure: ContextVar[_DeferredFailure] = ContextVar(
            f"nanobot_kt_plugin_failure_{id(self)}",
            default=None,
        )
        self._model_step: ContextVar[int] = ContextVar(
            f"nanobot_kt_plugin_model_step_{id(self)}",
            default=0,
        )

    def begin_turn(self) -> tuple[Token[_DeferredFailure], Token[int]]:
        return self._deferred_failure.set(None), self._model_step.set(0)

    def end_turn(
        self,
        tokens: tuple[Token[_DeferredFailure], Token[int]],
    ) -> None:
        failure_token, model_step_token = tokens
        self._deferred_failure.reset(failure_token)
        self._model_step.reset(model_step_token)

    def _defer(self, exc: BaseException) -> None:
        if self._deferred_failure.get() is None:
            self._deferred_failure.set(exc)

    def raise_deferred_failure(self) -> None:
        failure = self._deferred_failure.get()
        if failure is not None:
            raise failure

    def _invariants(self) -> Mapping[str, object]:
        from core.tool_plan import get_current_tool_plan

        plan = get_current_tool_plan()
        return runtime_hook_invariants(
            self._manager.runtime_id,
            self._request_provider(),
            tool_plan_sha256=(str(plan.sha256) if plan is not None else ""),
        )

    async def _dispatch_or_block(
        self,
        point: RuntimeHookPoint,
        fields: Mapping[str, object],
        *,
        validate_fields: Callable[[Mapping[str, object]], None] | None = None,
    ):
        try:
            return await self._manager.dispatch(
                point,
                fields,
                protected_invariants=self._invariants(),
                validate_fields=validate_fields,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._defer(exc)
            raise PluginBlockError(
                f"managed_plugin_blocked:{point.value}:{type(exc).__name__}"
            ) from exc

    async def _dispatch_or_defer(
        self,
        point: RuntimeHookPoint,
        fields: Mapping[str, object],
    ):
        try:
            return await self._manager.dispatch(
                point,
                fields,
                protected_invariants=self._invariants(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # KT 的 post hook 会隔离异常；保存原异常并在 inject_event 返回后
            # 由 KtRuntimeAdapter 重新抛出，避免 fail-closed 被框架吞掉。
            self._defer(exc)
            return None

    async def pre_llm_call(
        self,
        messages: list[dict],
        **kwargs: Any,
    ) -> list[dict] | None:
        model_step = self._model_step.get() + 1
        self._model_step.set(model_step)
        request = self._request_provider()
        await self._dispatch_or_block(
            RuntimeHookPoint.PRE_MODEL,
            {
                "messages": tuple(messages),
                "model": str(kwargs.get("model", "") or ""),
                "model_step": model_step,
                "runtime_id": self._manager.runtime_id,
                "stream": bool(request.stream) if request is not None else False,
                "tools": tuple(kwargs.get("tools") or ()),
            },
        )
        return None

    async def post_llm_call(
        self,
        messages: list[dict],
        response: str,
        usage: dict,
        **kwargs: Any,
    ) -> str | None:
        del messages
        request = self._request_provider()
        await self._dispatch_or_defer(
            RuntimeHookPoint.POST_MODEL,
            {
                "model": str(kwargs.get("model", "") or ""),
                "model_step": self._model_step.get(),
                "response": str(response or ""),
                "runtime_id": self._manager.runtime_id,
                "stream": bool(request.stream) if request is not None else False,
                "tool_calls": (),
                "usage": dict(usage or {}),
            },
        )
        return None

    async def pre_tool_execute(
        self,
        args: dict,
        **kwargs: Any,
    ) -> dict | None:
        from core.tool_plan import get_current_tool_plan

        tool_name = str(kwargs.get("tool_name", "") or "")
        job_id = str(kwargs.get("job_id", "") or "")
        plan = get_current_tool_plan()

        def validate_hook_fields(
            candidate: Mapping[str, object],
        ) -> None:
            updated = thaw_runtime_hook_value(candidate["arguments"])
            if not isinstance(updated, Mapping):
                raise TypeError("Pre Tool Hook 返回了无效参数合同")
            if plan is not None:
                plan.ensure_executable(tool_name)
                plan.validate_arguments(tool_name, updated)

        result = await self._dispatch_or_block(
            RuntimeHookPoint.PRE_TOOL,
            {
                "arguments": dict(args),
                "model_step": self._model_step.get(),
                "runtime_id": self._manager.runtime_id,
                "tool_call_id": job_id,
                "tool_name": tool_name,
                "tool_round": 0,
            },
            validate_fields=validate_hook_fields,
        )
        updated = thaw_runtime_hook_value(result.fields["arguments"])
        if not isinstance(updated, dict):
            error = TypeError("Pre Tool Hook 返回了无效参数合同")
            self._defer(error)
            raise PluginBlockError("managed_plugin_invalid_arguments") from error
        if plan is not None:
            try:
                plan.ensure_executable(tool_name)
                plan.validate_arguments(tool_name, updated)
            except Exception as exc:
                self._defer(exc)
                raise PluginBlockError(
                    "managed_plugin_arguments_rejected"
                ) from exc
        return updated if updated != args else None

    async def post_tool_execute(
        self,
        result: Any,
        **kwargs: Any,
    ) -> Any | None:
        dispatch = await self._dispatch_or_defer(
            RuntimeHookPoint.POST_TOOL,
            {
                "arguments": dict(kwargs.get("args") or {}),
                "error_code": "",
                "output": result,
                "runtime_id": self._manager.runtime_id,
                "status": "completed",
                "tool_call_id": str(kwargs.get("job_id", "") or ""),
                "tool_name": str(kwargs.get("tool_name", "") or ""),
            },
        )
        if dispatch is None:
            return None
        updated = thaw_runtime_hook_value(dispatch.fields["output"])
        return updated if updated != result else None


def install_managed_runtime_plugin(
    agent: object,
    plugin: ManagedKtRuntimePlugin,
) -> None:
    """只通过 KT 1.4 PluginManager 的公开入口安装一次 Adapter。"""

    manager = getattr(agent, "plugins", None)
    getter = getattr(manager, "get_plugin", None)
    register = getattr(manager, "register", None)
    if not callable(getter) or not callable(register):
        raise RuntimeError("KT PluginManager 缺少公开 get_plugin/register")
    existing = getter(plugin.name)
    if existing is plugin:
        return
    if existing is not None:
        raise RuntimeError("KT 已存在同名 Nanobot 受管 Plugin Adapter")
    register(plugin)


__all__ = [
    "ManagedKtRuntimePlugin",
    "install_managed_runtime_plugin",
]

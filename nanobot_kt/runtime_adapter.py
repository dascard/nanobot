"""KT 1.4 Agent 到 Nanobot ``AgentRuntimePort`` 的公开 API 适配器。

核心运行时合同不反向依赖本模块；KT 对象只在这里转换为 Nanobot 的类型化
Turn、事件、Conversation 与模型路由合同。
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import nullcontext, suppress
from dataclasses import replace
from typing import Protocol

from core.agent_runtime import (
    AgentRuntimeAdapterError,
    AgentRuntimeCapabilityError,
    AgentRuntimeExecutionError,
    AgentRuntimePort,
    AgentTurnRequest,
    AgentTurnResult,
    RuntimeLifecycleEvent,
    RuntimeLifecycleEventSink,
    RuntimeLifecycleState,
    PermissionPort,
    RuntimeBudgetAccount,
    RuntimeBudgetManager,
    RuntimeCapabilities,
    RuntimeCapability,
    RuntimeMessage,
    RuntimeModelRoute,
    RuntimePendingStateReset,
    RuntimeRunError,
    RuntimeRunEvent,
    RuntimeRunEventHandler,
    RuntimeRunStatus,
    RuntimeToolCall,
    RuntimeToolCallStatus,
    RuntimeToolPolicyStatus,
    RuntimeTurnKind,
    RuntimeUsage,
)
from core.agent_runtime.event_stream import (
    RuntimeRunEventEmitter,
    relay_runtime_run_events,
)
from core.agent_runtime.lifecycle import RuntimeLifecycleMachine
from core.agent_runtime.plugin_hooks import (
    dispatch_runtime_completion,
    dispatch_runtime_input_event,
    dispatch_runtime_interrupt_nowait,
    ledger_first_runtime_event_handler,
)
from core.runtime.plugin_lifecycle import (
    RuntimeHookPoint,
    RuntimePluginContractError,
    RuntimePluginExecutionError,
    RuntimePluginManager,
    RuntimePluginState,
    build_runtime_plugin_manager,
)


_OUTPUT_SIGNAL_END = object()


class KtModelRouteApplier(Protocol):
    """由 composition root 注入的 KT Provider 路由应用器。"""

    def __call__(self, agent: object, route: RuntimeModelRoute) -> None: ...


def _member(value: object, name: str, default: object = None) -> object:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _required_callable(
    value: object, name: str, *, runtime_id: str
) -> Callable[..., object]:
    method = getattr(value, name, None)
    if not callable(method):
        raise AgentRuntimeCapabilityError(
            f"KT Agent 缺少必需能力：{name}",
            runtime_id=runtime_id,
        )
    return method


async def _await_call(
    target: object,
    name: str,
    *,
    runtime_id: str,
) -> object:
    method = _required_callable(target, name, runtime_id=runtime_id)
    result = method()
    if inspect.isawaitable(result):
        return await result
    return result


def _tool_call_from_raw(raw: object) -> RuntimeToolCall | None:
    call_id = str(_member(raw, "id", "") or "").strip()
    function = _member(raw, "function", None)
    name = str(_member(function, "name", "") or "").strip()
    if not call_id or not name:
        return None
    return RuntimeToolCall(
        call_id=call_id,
        name=name,
        arguments=_member(function, "arguments", ""),
    )


def _raw_tool_calls(message: object) -> tuple[RuntimeToolCall, ...]:
    raw_calls = _member(message, "tool_calls", ())
    if not isinstance(raw_calls, Iterable) or isinstance(raw_calls, (str, bytes, dict)):
        return ()
    calls: list[RuntimeToolCall] = []
    for raw_call in raw_calls:
        call = _tool_call_from_raw(raw_call)
        if call is not None:
            calls.append(call)
    return tuple(calls)


def _runtime_message(message: object) -> RuntimeMessage:
    return RuntimeMessage(
        role=str(_member(message, "role", "unknown") or "unknown"),
        content=_member(message, "content", ""),
        name=str(_member(message, "name", "") or ""),
        tool_call_id=str(_member(message, "tool_call_id", "") or ""),
        tool_calls=_raw_tool_calls(message),
    )


def _tool_call_wire(call: RuntimeToolCall) -> dict[str, object]:
    return {
        "id": call.call_id,
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": call.arguments,
        },
    }


def _default_route_applier(agent: object, route: RuntimeModelRoute) -> None:
    """只更新 KT 公开配置字段；传输连接切换必须由专用 Adapter 注入。"""

    if route.timeout_seconds is not None or route.enable_thinking is not None:
        raise AgentRuntimeCapabilityError(
            "默认 KT 路由器不能安全重建 Provider 连接或设置 thinking，"
            "请从 composition root 注入 KtModelRouteApplier",
        )
    controller = getattr(agent, "controller", None)
    llm = getattr(controller, "llm", None)
    config = getattr(llm, "config", None)
    if config is None or not hasattr(config, "model"):
        raise AgentRuntimeCapabilityError("KT LLM 缺少可写 config.model")
    if route.temperature is not None and not hasattr(config, "temperature"):
        raise AgentRuntimeCapabilityError("KT LLM 不支持 temperature")
    if route.max_tokens is not None and not hasattr(config, "max_tokens"):
        raise AgentRuntimeCapabilityError("KT LLM 不支持 max_tokens")

    config.model = route.model_id
    if route.temperature is not None:
        config.temperature = route.temperature
    if route.max_tokens is not None:
        config.max_tokens = route.max_tokens
    if hasattr(llm, "provider_name"):
        llm.provider_name = route.provider_id


class KtRuntimeAdapter:
    """把 KT Agent 包装成稳定端口，不拥有 Nanobot 业务策略。"""

    def __init__(
        self,
        agent: object,
        *,
        runtime_id: str,
        route_applier: KtModelRouteApplier | None = None,
        event_sinks: tuple[RuntimeLifecycleEventSink, ...] = (),
        initially_started: bool | None = None,
        output_sink: object | None = None,
        plugin_manager: RuntimePluginManager | None = None,
        budget_manager: RuntimeBudgetManager | None = None,
        permission_port: PermissionPort | None = None,
    ) -> None:
        self._agent = agent
        self._output_sink = output_sink
        if initially_started is None:
            running_state = getattr(agent, "is_running", None)
            if running_state is None:
                raise AgentRuntimeCapabilityError(
                    "KT Agent 缺少公开 is_running 状态",
                    runtime_id=runtime_id,
                )
            running = bool(
                running_state() if callable(running_state) else running_state
            )
        else:
            running = bool(initially_started)
        self._lifecycle = RuntimeLifecycleMachine(
            runtime_id,
            initial_state=(
                RuntimeLifecycleState.RUNNING if running else RuntimeLifecycleState.NEW
            ),
            event_sinks=event_sinks,
        )
        self._route_applier = route_applier or _default_route_applier
        if plugin_manager is not None and not isinstance(
            plugin_manager,
            RuntimePluginManager,
        ):
            raise TypeError("plugin_manager 必须是 RuntimePluginManager")
        self._plugin_manager = plugin_manager or build_runtime_plugin_manager(
            runtime_id
        )
        if self._plugin_manager.runtime_id != runtime_id:
            raise ValueError("Plugin Manager runtime_id 与 KT Runtime 不一致")
        if budget_manager is not None and not isinstance(
            budget_manager,
            RuntimeBudgetManager,
        ):
            raise TypeError("budget_manager 必须是 RuntimeBudgetManager")
        self._budget_manager = budget_manager or RuntimeBudgetManager()
        if permission_port is not None and not isinstance(
            permission_port,
            PermissionPort,
        ):
            raise TypeError("permission_port 必须实现 PermissionPort")
        self._permission_port = permission_port
        self._plugin_start_lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()
        self._managed_kt_plugin: object | None = None
        self._permission_guard_plugin: object | None = None
        self._budget_guard_plugin: object | None = None
        self._active_request: AgentTurnRequest | None = None
        self._active_budget: RuntimeBudgetAccount | None = None

    @property
    def runtime_id(self) -> str:
        return self._lifecycle.runtime_id

    @property
    def state(self) -> RuntimeLifecycleState:
        return self._lifecycle.state

    @property
    def lifecycle_events(self) -> tuple[RuntimeLifecycleEvent, ...]:
        return self._lifecycle.events

    def _install_managed_kt_plugin(self) -> None:
        if self._managed_kt_plugin is not None:
            return
        if not any(
            self._plugin_manager.has_hooks(point)
            for point in (
                RuntimeHookPoint.PRE_MODEL,
                RuntimeHookPoint.POST_MODEL,
                RuntimeHookPoint.PRE_TOOL,
                RuntimeHookPoint.POST_TOOL,
            )
        ):
            return
        from nanobot_kt.plugin_runtime import (
            ManagedKtRuntimePlugin,
            install_managed_runtime_plugin,
        )

        managed_plugin = ManagedKtRuntimePlugin(
            self._plugin_manager,
            lambda: self._active_request,
        )
        install_managed_runtime_plugin(self._agent, managed_plugin)
        self._managed_kt_plugin = managed_plugin

    async def _ensure_plugin_runtime_started(self) -> None:
        async with self._plugin_start_lock:
            self._install_managed_kt_plugin()
            state = self._plugin_manager.state
            if state is RuntimePluginState.NEW:
                await self._plugin_manager.start()
                return
            if state is not RuntimePluginState.RUNNING:
                raise RuntimePluginContractError(
                    f"KT Plugin Manager 状态不可执行：{state.value}"
                )

    @property
    def runtime_capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            runtime_id=self.runtime_id,
            supported=frozenset(
                capability
                for capability in RuntimeCapability
                if capability is not RuntimeCapability.CHECKPOINT_RECOVERY
            ),
        )

    async def start(self) -> None:
        self._lifecycle.ensure(RuntimeLifecycleState.NEW)
        self._lifecycle.transition(RuntimeLifecycleState.STARTING)
        try:
            await self._ensure_plugin_runtime_started()
            self._install_executor_tracing()
            await _await_call(self._agent, "start", runtime_id=self.runtime_id)
            self._disable_framework_compaction()
        except Exception as exc:
            with suppress(Exception):
                await self._plugin_manager.stop()
            self._lifecycle.fail(type(exc).__name__)
            raise AgentRuntimeAdapterError(
                "KT Agent 启动失败",
                runtime_id=self.runtime_id,
            ) from exc
        self._lifecycle.transition(RuntimeLifecycleState.RUNNING)

    def _disable_framework_compaction(self) -> None:
        """Nanobot Prompt Runtime 独占上下文预算与压缩。"""

        manager = getattr(self._agent, "compact_manager", None)
        config = getattr(manager, "config", None)
        if config is not None and hasattr(config, "enabled"):
            config.enabled = False

    def _install_executor_tracing(self) -> None:
        """给公开 Executor 实例安装 Nanobot 追踪。"""

        executor = getattr(self._agent, "executor", None)
        if executor is None:
            return
        try:
            from core.tool_tracing import install_executor_tracing

            install_executor_tracing(executor)
        except Exception:
            # 追踪是 fail-open 辅助能力，不能阻断 Agent 启动。
            pass

    def _request_context(self, request: AgentTurnRequest) -> dict[str, object]:
        """把受信合同转换为请求级不可变上下文。"""

        attributes = {
            attribute.key: attribute.value for attribute in request.event_attributes
        }
        context = request.context
        principal = context.principal
        actor = context.actor
        is_group = context.chat_type.value == "group"
        actor_user_id = str(attributes.get("actor_user_id", "") or "")
        group_id = principal.owner_id if is_group else str(
            attributes.get("group_id", "") or ""
        )
        user_id = actor_user_id or (
            principal.owner_id if not is_group else ""
        )
        runtime_context: dict[str, object] = {
            "agent_id": context.agent_id,
            "chat_type": context.chat_type.value,
            "runtime_chat_type": str(
                attributes.get("runtime_chat_type", context.chat_type.value) or ""
            ),
            "is_group": is_group,
            "is_super_user": context.feature_enabled("super_user"),
            "session_id": context.session_id,
            "group_id": group_id,
            "user_id": user_id,
            "platform": principal.platform,
            "sender_name": str(attributes.get("sender_name", "") or ""),
            "trace_id": context.trace_id,
            "run_id": context.run_id,
            "turn_id": context.turn_id,
            "correlation_id": context.correlation_id,
            "actor_type": actor.actor_type.value if actor is not None else "",
            "actor_id": actor.actor_id if actor is not None else "",
            "actor_parent_id": (
                actor.parent_actor_id if actor is not None else ""
            ),
            "owner_type": principal.owner_type.value,
            "owner_id": principal.owner_id,
            "message_id": context.message_id,
        }
        session_goal_id = str(attributes.get("session_goal_id", "") or "")
        if session_goal_id:
            runtime_context.update(
                {
                    "session_goal_id": session_goal_id,
                    "session_goal_status": str(
                        attributes.get("session_goal_status", "") or ""
                    ),
                    "session_goal_mode": str(
                        attributes.get("session_goal_mode", "") or ""
                    ),
                    "session_goal_version": attributes.get(
                        "session_goal_version",
                        0,
                    ),
                    "session_plan_revision": attributes.get(
                        "session_plan_revision",
                        0,
                    ),
                    "session_plan_sha256": str(
                        attributes.get("session_plan_sha256", "") or ""
                    ),
                }
            )
        if str(attributes.get("skill_lock_json", "") or ""):
            runtime_context.update(
                {
                    key: attributes.get(key, "")
                    for key in (
                        "skill_lock_json",
                        "skill_lock_sha256",
                        "skill_scope_targets_json",
                        "skill_agent_id",
                        "skill_project_id",
                    )
                }
            )
        return runtime_context

    async def stop(self) -> None:
        if self.state is RuntimeLifecycleState.STOPPED:
            return
        if self.state is RuntimeLifecycleState.NEW:
            await self._plugin_manager.stop()
            self._lifecycle.transition(RuntimeLifecycleState.STOPPED)
            return
        self._lifecycle.ensure(
            RuntimeLifecycleState.RUNNING,
            RuntimeLifecycleState.FAILED,
        )
        self._lifecycle.transition(RuntimeLifecycleState.STOPPING)
        first_error: BaseException | None = None
        try:
            await _await_call(self._agent, "stop", runtime_id=self.runtime_id)
        except BaseException as exc:
            first_error = exc
        try:
            await self._plugin_manager.stop()
        except BaseException as exc:
            if first_error is None:
                first_error = exc
        if first_error is not None:
            self._lifecycle.fail(type(first_error).__name__)
            raise AgentRuntimeAdapterError(
                "KT Agent 停止失败",
                runtime_id=self.runtime_id,
            ) from first_error
        self._lifecycle.transition(RuntimeLifecycleState.STOPPED)

    def _conversation(self) -> object:
        controller = getattr(self._agent, "controller", None)
        conversation = getattr(controller, "conversation", None)
        if conversation is None:
            raise AgentRuntimeCapabilityError(
                "KT Agent 缺少 controller.conversation",
                runtime_id=self.runtime_id,
            )
        return conversation

    def _raw_messages(self) -> tuple[object, ...]:
        conversation = self._conversation()
        get_messages = _required_callable(
            conversation,
            "get_messages",
            runtime_id=self.runtime_id,
        )
        messages = get_messages()
        if not isinstance(messages, Iterable) or isinstance(
            messages, (str, bytes, dict)
        ):
            raise AgentRuntimeCapabilityError(
                "KT conversation 未返回消息序列",
                runtime_id=self.runtime_id,
            )
        return tuple(messages)

    async def run(self, request: AgentTurnRequest) -> AgentTurnResult:
        self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
        await self._ensure_plugin_runtime_started()
        from core.agent_runtime.request_scope import runtime_context_scope
        from kohakuterrarium.core.events import create_user_input_event

        runtime_context = self._request_context(request)
        event_context = {
            attribute.key: attribute.value for attribute in request.event_attributes
        }
        event_context["stream"] = request.stream
        if request.kind is RuntimeTurnKind.CONTINUE:
            from kohakuterrarium.core.events import TriggerEvent

            event = TriggerEvent(type="user_input", content=request.content)
        else:
            event = create_user_input_event(request.content, **event_context)
        async with self._run_lock:
            managed_plugin = self._managed_kt_plugin
            permission_plugin = self._permission_guard_plugin
            budget_plugin = self._budget_guard_plugin
            begin_turn = getattr(managed_plugin, "begin_turn", None)
            end_turn = getattr(managed_plugin, "end_turn", None)
            budget_begin = getattr(budget_plugin, "begin_turn", None)
            budget_end = getattr(budget_plugin, "end_turn", None)
            permission_begin = getattr(permission_plugin, "begin_turn", None)
            permission_end = getattr(permission_plugin, "end_turn", None)
            turn_tokens = begin_turn() if callable(begin_turn) else None
            budget_token = budget_begin() if callable(budget_begin) else None
            permission_token = (
                permission_begin() if callable(permission_begin) else None
            )
            self._active_request = request
            try:
                self._active_budget = self._budget_manager.bind(
                    request.context.execution_identity(),
                    request.context.governance,
                )
                with runtime_context_scope(runtime_context):
                    async with asyncio.timeout(
                        self._active_budget.remaining_time_seconds()
                    ):
                        await dispatch_runtime_input_event(
                            self._plugin_manager,
                            request,
                        )
                        inject_event = _required_callable(
                            self._agent,
                            "inject_event",
                            runtime_id=self.runtime_id,
                        )
                        raw_result = inject_event(event)
                        if inspect.isawaitable(raw_result):
                            raw_result = await raw_result
                        for plugin in (
                            managed_plugin,
                            permission_plugin,
                            budget_plugin,
                        ):
                            raise_deferred = getattr(
                                plugin,
                                "raise_deferred_failure",
                                None,
                            )
                            if callable(raise_deferred):
                                raise_deferred()
                        result = AgentTurnResult(
                            raw_result=raw_result,
                            messages=self.read_conversation(),
                            tool_calls=self.inspect_tool_calls(),
                        )
                        await dispatch_runtime_completion(
                            self._plugin_manager,
                            request,
                            result,
                        )
                        return result
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                raise
            except RuntimePluginExecutionError:
                raise
            except Exception as exc:
                from core.run_ledger.contracts import (
                    find_run_ledger_authority_error,
                )

                authority_failure = find_run_ledger_authority_error(exc)
                if authority_failure is not None:
                    raise authority_failure
                raise AgentRuntimeExecutionError(
                    "KT Agent 单轮执行失败",
                    runtime_id=self.runtime_id,
                ) from exc
            finally:
                if permission_token is not None and callable(permission_end):
                    permission_end(permission_token)
                if budget_token is not None and callable(budget_end):
                    budget_end(budget_token)
                if turn_tokens is not None and callable(end_turn):
                    end_turn(turn_tokens)
                if self._active_request is request:
                    self._active_request = None
                    self._active_budget = None

    def run_stream(
        self,
        request: AgentTurnRequest,
    ) -> AsyncIterator[RuntimeRunEvent]:
        stream_request = replace(request, stream=True)
        return relay_runtime_run_events(
            lambda handler: self.run_event(stream_request, handler)
        )

    async def run_event(
        self,
        request: AgentTurnRequest,
        handler: RuntimeRunEventHandler,
    ) -> AgentTurnResult:
        self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
        await self._ensure_plugin_runtime_started()
        managed_handler = ledger_first_runtime_event_handler(
            self._plugin_manager,
            request,
            handler,
        )
        emitter = RuntimeRunEventEmitter(
            request.context.execution_identity(),
            managed_handler,
        )
        await emitter.status_changed(RuntimeRunStatus.ACCEPTED)
        await emitter.status_changed(RuntimeRunStatus.RUNNING)

        signal_queue: asyncio.Queue[
            tuple[str, dict[str, object]] | object
        ] = asyncio.Queue()

        def collect_signal(kind: str, payload: dict[str, object]) -> None:
            signal_queue.put_nowait((kind, payload))

        async def forward_signals() -> None:
            while True:
                signal = await signal_queue.get()
                if signal is _OUTPUT_SIGNAL_END:
                    return
                kind, payload = signal
                if kind == "text_delta":
                    text = str(payload.get("text", "") or "")
                    if text:
                        await emitter.text_delta(text)
                elif kind == "error":
                    await emitter.error(
                        RuntimeRunError(
                            code=str(
                                payload.get("code", "kt_runtime_error")
                                or "kt_runtime_error"
                            ),
                            message=str(
                                payload.get("message", "KT Runtime 输出错误")
                                or "KT Runtime 输出错误"
                            ),
                            retryable=bool(payload.get("retryable", False)),
                        )
                    )

        capture = getattr(self._output_sink, "capture_runtime_signals", None)
        signal_scope = (
            capture(collect_signal) if callable(capture) else nullcontext()
        )
        forward_task = asyncio.create_task(forward_signals())
        result: AgentTurnResult | None = None
        execution_failure: BaseException | None = None
        forward_failure: BaseException | None = None
        try:
            with signal_scope:
                result = await self.run(request)
        except BaseException as exc:
            execution_failure = exc
        finally:
            signal_queue.put_nowait(_OUTPUT_SIGNAL_END)
            try:
                await forward_task
            except BaseException as exc:
                forward_failure = exc

        if execution_failure is not None:
            if isinstance(execution_failure, asyncio.CancelledError):
                await emitter.end(RuntimeRunStatus.CANCELLED)
            elif isinstance(execution_failure, TimeoutError):
                await emitter.error(
                    RuntimeRunError(
                        code="runtime_timeout",
                        message=str(execution_failure) or "KT Runtime 执行超时",
                        retryable=True,
                    )
                )
                await emitter.end(RuntimeRunStatus.TIMED_OUT)
            else:
                await emitter.error(
                    RuntimeRunError(
                        code="runtime_execution_error",
                        message=str(execution_failure)
                        or type(execution_failure).__name__,
                    )
                )
                await emitter.end(RuntimeRunStatus.FAILED)
            raise execution_failure
        if forward_failure is not None:
            raise forward_failure
        if result is None:
            raise AgentRuntimeExecutionError(
                "KT Agent 单轮执行未返回结果",
                runtime_id=self.runtime_id,
            )

        for tool_call in result.tool_calls:
            await emitter.tool_activity(tool_call)
        usage = self._read_runtime_usage()
        if usage is not None:
            await emitter.usage(usage)
        await emitter.end(RuntimeRunStatus.SUCCEEDED)
        return result

    def _read_runtime_usage(self) -> RuntimeUsage | None:
        controller = getattr(self._agent, "controller", None)
        llm = getattr(controller, "llm", None)
        raw = getattr(llm, "last_usage", None)
        if raw is None:
            return None

        def nonnegative_int(name: str) -> int:
            try:
                return max(0, int(_member(raw, name, 0) or 0))
            except (TypeError, ValueError):
                return 0

        usage = RuntimeUsage(
            input_tokens=nonnegative_int("prompt_tokens")
            or nonnegative_int("input_tokens"),
            output_tokens=nonnegative_int("completion_tokens")
            or nonnegative_int("output_tokens"),
            cached_input_tokens=nonnegative_int("cached_tokens")
            or nonnegative_int("cached_input_tokens"),
            reasoning_tokens=nonnegative_int("reasoning_tokens"),
        )
        if not usage.total_tokens and not usage.reasoning_tokens:
            return None
        return usage

    async def execute_turn(self, request: AgentTurnRequest) -> AgentTurnResult:
        """旧版兼容 façade；保留给尚未迁移的 Bridge 调用。"""

        return await self.run(request)

    def replace_conversation(self, messages: tuple[RuntimeMessage, ...]) -> int:
        self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
        conversation = self._conversation()
        clear = _required_callable(
            conversation,
            "clear",
            runtime_id=self.runtime_id,
        )
        append = _required_callable(
            conversation,
            "append",
            runtime_id=self.runtime_id,
        )
        clear(keep_system=False)
        for message in messages:
            kwargs: dict[str, object] = {}
            if message.name:
                kwargs["name"] = message.name
            if message.tool_call_id:
                kwargs["tool_call_id"] = message.tool_call_id
            if message.tool_calls:
                kwargs["tool_calls"] = [
                    _tool_call_wire(tool_call) for tool_call in message.tool_calls
                ]
            append(message.role, message.content, **kwargs)
        return len(messages)

    def read_conversation(self) -> tuple[RuntimeMessage, ...]:
        return tuple(_runtime_message(message) for message in self._raw_messages())

    def clear_pending_events(self) -> RuntimePendingStateReset:
        """公开注入路径不使用 KT Controller 的内部等待队列。"""

        self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
        return RuntimePendingStateReset()

    def install_tool_policy(self) -> RuntimeToolPolicyStatus:
        # 权限组件必须能在 Agent 启动前安装；RUNNING 仅用于兼容显式重验。
        self._lifecycle.ensure(
            RuntimeLifecycleState.NEW,
            RuntimeLifecycleState.RUNNING,
        )
        from nanobot_kt.tool_runtime import (
            PermissionGuardPlugin,
            RuntimeBudgetGuardPlugin,
            install_tool_loop_control,
            install_permission_guard,
            install_runtime_budget_guard,
            install_tool_plan_guard,
            install_tool_plan_native_schema_filter,
            tool_plan_runtime_status,
        )

        install_tool_plan_guard(self._agent)
        install_tool_loop_control(self._agent)
        if self._permission_port is not None:
            install_permission_guard(
                self._agent,
                self._permission_port,
                lambda: self._active_request,
            )
        install_runtime_budget_guard(
            self._agent,
            lambda: self._active_budget,
            lambda: self._active_request,
        )
        manager = getattr(self._agent, "plugins", None)
        getter = getattr(manager, "get_plugin", None)
        if callable(getter):
            permission_candidate = getter(PermissionGuardPlugin.name)
            if isinstance(permission_candidate, PermissionGuardPlugin):
                self._permission_guard_plugin = permission_candidate
            candidate = getter(RuntimeBudgetGuardPlugin.name)
            if isinstance(candidate, RuntimeBudgetGuardPlugin):
                self._budget_guard_plugin = candidate
        install_tool_plan_native_schema_filter(self._agent)
        raw_status = tool_plan_runtime_status(self._agent)
        status = RuntimeToolPolicyStatus(
            ready=bool(raw_status.get("ready", False)),
            guard_installed=bool(raw_status.get("guard_installed", False)),
            schema_filter_installed=bool(
                raw_status.get("schema_filter_installed", False)
            ),
            missing=tuple(str(item) for item in raw_status.get("missing", ())),
        )
        if not status.ready:
            raise AgentRuntimeCapabilityError(
                "KT ToolPlan 运行时组件缺失：" + ", ".join(status.missing),
                runtime_id=self.runtime_id,
            )
        return status

    def set_model_route(self, route: RuntimeModelRoute) -> None:
        self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
        try:
            self._route_applier(self._agent, route)
        except AgentRuntimeCapabilityError as exc:
            if exc.runtime_id:
                raise
            raise AgentRuntimeCapabilityError(
                str(exc),
                runtime_id=self.runtime_id,
            ) from exc
        except Exception as exc:
            raise AgentRuntimeAdapterError(
                "KT 模型路由应用失败",
                runtime_id=self.runtime_id,
            ) from exc

    def inspect_tool_calls(self) -> tuple[RuntimeToolCall, ...]:
        calls: list[RuntimeToolCall] = []
        indexes: dict[str, int] = {}
        for message in self._raw_messages():
            for call in _raw_tool_calls(message):
                indexes[call.call_id] = len(calls)
                calls.append(call)
            if str(_member(message, "role", "") or "") != "tool":
                continue
            call_id = str(_member(message, "tool_call_id", "") or "").strip()
            index = indexes.get(call_id)
            if index is None:
                continue
            requested = calls[index]
            calls[index] = RuntimeToolCall(
                call_id=requested.call_id,
                name=requested.name,
                arguments=requested.arguments,
                status=RuntimeToolCallStatus.COMPLETED,
                result=_member(message, "content", ""),
            )
        return tuple(calls)

    def list_tool_names(self) -> tuple[str, ...]:
        registry = getattr(self._agent, "registry", None)
        if registry is None:
            return ()
        list_tools = _required_callable(
            registry,
            "list_tools",
            runtime_id=self.runtime_id,
        )
        raw = list_tools() or ()
        if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
            return ()
        names: set[str] = set()
        for item in raw:
            if isinstance(item, str):
                name = item
            elif isinstance(item, dict):
                name = str(item.get("name", "") or "")
            else:
                name = str(getattr(item, "name", "") or "")
            if name.strip():
                names.add(name.strip())
        return tuple(sorted(names))

    def interrupt(self, *, reason: str = "") -> bool:
        self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
        interrupt = _required_callable(
            self._agent,
            "interrupt",
            runtime_id=self.runtime_id,
        )
        interrupt()
        # 原因只进入受管只读 Hook，不注入 KT 控制流。
        dispatch_runtime_interrupt_nowait(
            self._plugin_manager,
            reason=reason,
            request=self._active_request,
        )
        return True


def build_kt_runtime(
    agent: object,
    *,
    runtime_id: str | None = None,
    route_applier: KtModelRouteApplier | None = None,
    event_sinks: tuple[RuntimeLifecycleEventSink, ...] = (),
    initially_started: bool | None = None,
    output_sink: object | None = None,
    plugin_manager: RuntimePluginManager | None = None,
    budget_manager: RuntimeBudgetManager | None = None,
    permission_port: PermissionPort | None = None,
) -> AgentRuntimePort:
    """显式 composition factory；不读取或写入模块级单例。"""

    resolved_id = str(runtime_id or "").strip()
    if not resolved_id:
        config = getattr(agent, "config", None)
        name = str(getattr(config, "name", "") or "").strip()
        resolved_id = f"kt:{name or 'agent'}"
    return KtRuntimeAdapter(
        agent,
        runtime_id=resolved_id,
        route_applier=route_applier,
        event_sinks=event_sinks,
        initially_started=initially_started,
        output_sink=output_sink,
        plugin_manager=plugin_manager,
        budget_manager=budget_manager,
        permission_port=permission_port,
    )


__all__ = [
    "KtRuntimeAdapter",
    "KtModelRouteApplier",
    "build_kt_runtime",
]

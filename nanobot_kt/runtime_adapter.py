"""KT 1.3 Agent 到 Nanobot ``AgentRuntimePort`` 的兼容适配器。

这里是允许了解 KT 对象结构、私有兼容字段和 monkey patch 安装方式的唯一
边界。核心运行时合同不反向依赖本模块。
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Iterable
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
    RuntimeMessage,
    RuntimeModelRoute,
    RuntimePendingStateReset,
    RuntimeToolCall,
    RuntimeToolCallStatus,
    RuntimeToolPolicyStatus,
    RuntimeTurnKind,
)
from core.agent_runtime.lifecycle import RuntimeLifecycleMachine


class KtModelRouteApplier(Protocol):
    """由 composition root 注入的 KT Provider 路由应用器。"""

    def __call__(self, agent: object, route: RuntimeModelRoute) -> None: ...


class KtOpenAITransportConfig(Protocol):
    base_url: str
    api_key: str
    timeout: float
    enable_thinking: object
    provider_id: str
    registry_provider: str


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


def apply_kt_openai_model_route(
    agent: object,
    route: RuntimeModelRoute,
    transport: KtOpenAITransportConfig,
    *,
    client_factory: Callable[..., object] | None = None,
    tracer_installer: Callable[..., object] | None = None,
) -> None:
    """在 KT Adapter 边界内原子应用模型与 OpenAI-compatible 传输配置。"""

    controller = getattr(agent, "controller", None)
    llm = getattr(controller, "llm", None)
    config = getattr(llm, "config", None)
    if llm is None or config is None or not hasattr(config, "model"):
        raise AgentRuntimeCapabilityError("KT LLM 缺少可配置模型连接")

    config.model = route.model_id
    if route.temperature is not None:
        if not hasattr(config, "temperature"):
            raise AgentRuntimeCapabilityError("KT LLM 不支持 temperature")
        config.temperature = route.temperature
    if route.max_tokens is not None:
        if not hasattr(config, "max_tokens"):
            raise AgentRuntimeCapabilityError("KT LLM 不支持 max_tokens")
        config.max_tokens = route.max_tokens

    if hasattr(llm, "extra_body"):
        from core.model_route_options import apply_enable_thinking_to_payload

        if getattr(llm, "extra_body", None) is None:
            llm.extra_body = {}
        apply_enable_thinking_to_payload(
            llm.extra_body,
            route.model_id,
            transport.enable_thinking,
        )

    target_base_url = str(transport.base_url or "").rstrip("/")
    target_api_key = str(transport.api_key or "")
    target_timeout = float(transport.timeout)
    current_base_url = str(getattr(llm, "base_url", "") or "").rstrip("/")
    current_api_key = str(getattr(llm, "_api_key", "") or "")
    current_timeout = float(getattr(llm, "_timeout", 120.0) or 120.0)
    if (
        (target_base_url and current_base_url != target_base_url)
        or current_api_key != target_api_key
        or current_timeout != target_timeout
    ):
        if client_factory is None:
            from openai import AsyncOpenAI

            client_factory = AsyncOpenAI

        llm.base_url = target_base_url
        llm._api_key = target_api_key
        llm._timeout = target_timeout
        llm._client = client_factory(
            api_key=target_api_key,
            base_url=target_base_url,
            timeout=target_timeout,
            max_retries=getattr(llm, "_max_retries", 3),
            default_headers=getattr(llm, "_extra_headers", {}),
        )
    llm.provider_name = route.provider_id

    if tracer_installer is None:
        from core.llm_sdk_tracing import install_openai_chat_completion_tracer

        tracer_installer = install_openai_chat_completion_tracer

    tracer_installer(
        llm,
        provider=route.provider_id,
        base_url=target_base_url,
    )


class Kt13RuntimeAdapter:
    """把现有 KT Agent 包装成稳定端口，不拥有 Nanobot 业务策略。"""

    def __init__(
        self,
        agent: object,
        *,
        runtime_id: str,
        route_applier: KtModelRouteApplier | None = None,
        event_sinks: tuple[RuntimeLifecycleEventSink, ...] = (),
        initially_started: bool | None = None,
    ) -> None:
        self._agent = agent
        running = (
            bool(getattr(agent, "_running", False))
            if initially_started is None
            else bool(initially_started)
        )
        self._lifecycle = RuntimeLifecycleMachine(
            runtime_id,
            initial_state=(
                RuntimeLifecycleState.RUNNING if running else RuntimeLifecycleState.NEW
            ),
            event_sinks=event_sinks,
        )
        self._route_applier = route_applier or _default_route_applier

    @property
    def runtime_id(self) -> str:
        return self._lifecycle.runtime_id

    @property
    def state(self) -> RuntimeLifecycleState:
        return self._lifecycle.state

    @property
    def lifecycle_events(self) -> tuple[RuntimeLifecycleEvent, ...]:
        return self._lifecycle.events

    async def start(self) -> None:
        self._lifecycle.ensure(RuntimeLifecycleState.NEW)
        self._lifecycle.transition(RuntimeLifecycleState.STARTING)
        try:
            self._configure_executor_boundary()
            await _await_call(self._agent, "start", runtime_id=self.runtime_id)
        except Exception as exc:
            self._lifecycle.fail(type(exc).__name__)
            raise AgentRuntimeAdapterError(
                "KT Agent 启动失败",
                runtime_id=self.runtime_id,
            ) from exc
        self._lifecycle.transition(RuntimeLifecycleState.RUNNING)

    def _configure_executor_boundary(self) -> None:
        """集中安装 KT 1.3 executor 的安全与追踪兼容项。"""

        executor = getattr(self._agent, "executor", None)
        path_guard = getattr(executor, "_path_guard", None)
        if path_guard is not None and hasattr(path_guard, "mode"):
            path_guard.mode = "block"
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
        is_group = context.chat_type.value == "group"
        actor_user_id = str(attributes.get("actor_user_id", "") or "")
        group_id = principal.owner_id if is_group else str(
            attributes.get("group_id", "") or ""
        )
        user_id = actor_user_id or (
            principal.owner_id if not is_group else ""
        )
        return {
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
            "message_id": context.message_id,
        }

    async def stop(self) -> None:
        if self.state is RuntimeLifecycleState.STOPPED:
            return
        if self.state is RuntimeLifecycleState.NEW:
            self._lifecycle.transition(RuntimeLifecycleState.STOPPED)
            return
        self._lifecycle.ensure(
            RuntimeLifecycleState.RUNNING,
            RuntimeLifecycleState.FAILED,
        )
        self._lifecycle.transition(RuntimeLifecycleState.STOPPING)
        try:
            await _await_call(self._agent, "stop", runtime_id=self.runtime_id)
        except Exception as exc:
            self._lifecycle.fail(type(exc).__name__)
            raise AgentRuntimeAdapterError(
                "KT Agent 停止失败",
                runtime_id=self.runtime_id,
            ) from exc
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
        get_messages = getattr(conversation, "get_messages", None)
        if callable(get_messages):
            messages = get_messages()
        else:
            to_messages = getattr(conversation, "to_messages", None)
            if callable(to_messages):
                messages = to_messages()
            else:
                # KT 1.3 兼容 fallback；私有访问被限制在本 Adapter 内。
                messages = getattr(conversation, "_messages", ())
        if not isinstance(messages, Iterable) or isinstance(
            messages, (str, bytes, dict)
        ):
            raise AgentRuntimeCapabilityError(
                "KT conversation 未返回消息序列",
                runtime_id=self.runtime_id,
            )
        return tuple(messages)

    async def execute_turn(self, request: AgentTurnRequest) -> AgentTurnResult:
        self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
        from nanobot_kt.kt_adapter import create_user_event, process_event
        from core.agent_runtime.request_scope import runtime_context_scope

        runtime_context = self._request_context(request)
        event_context = {
            attribute.key: attribute.value for attribute in request.event_attributes
        }
        event_context["stream"] = request.stream
        if request.kind is RuntimeTurnKind.CONTINUE:
            from kohakuterrarium.core.events import TriggerEvent

            event = TriggerEvent(type="user_input", content=request.content)
        else:
            event = create_user_event(request.content, **event_context)
        try:
            with runtime_context_scope(runtime_context):
                raw_result = await process_event(self._agent, event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise AgentRuntimeExecutionError(
                "KT Agent 单轮执行失败",
                runtime_id=self.runtime_id,
            ) from exc
        return AgentTurnResult(
            raw_result=raw_result,
            messages=self.read_conversation(),
            tool_calls=self.inspect_tool_calls(),
        )

    def replace_conversation(self, messages: tuple[RuntimeMessage, ...]) -> int:
        self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
        from nanobot_kt.kt_adapter import install_conversation_order_guard

        install_conversation_order_guard(self._agent)
        conversation = self._conversation()
        clear = getattr(conversation, "clear", None)
        append = getattr(conversation, "append", None)
        raw_messages = getattr(conversation, "_messages", None)
        if callable(clear):
            clear(keep_system=False)
        elif isinstance(raw_messages, list):
            # KT 1.3 兼容 fallback；私有访问只允许留在本 Adapter。
            raw_messages.clear()
        else:
            raise AgentRuntimeCapabilityError(
                "KT conversation 缺少 clear 能力",
                runtime_id=self.runtime_id,
            )
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
            if callable(append):
                append(message.role, message.content, **kwargs)
            elif isinstance(raw_messages, list):
                raw_messages.append(
                    {
                        "role": message.role,
                        "content": message.content,
                        **kwargs,
                    }
                )
            else:
                raise AgentRuntimeCapabilityError(
                    "KT conversation 缺少 append 能力",
                    runtime_id=self.runtime_id,
                )
        return len(messages)

    def read_conversation(self) -> tuple[RuntimeMessage, ...]:
        return tuple(_runtime_message(message) for message in self._raw_messages())

    def clear_pending_events(self) -> RuntimePendingStateReset:
        self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
        controller = getattr(self._agent, "controller", None)
        if controller is None:
            raise AgentRuntimeCapabilityError(
                "KT Agent 缺少 controller",
                runtime_id=self.runtime_id,
            )

        pending = getattr(controller, "_pending_events", None)
        pending_count = len(pending) if isinstance(pending, list) else 0
        if isinstance(pending, list):
            pending.clear()

        queued_count = 0
        queue = getattr(controller, "_event_queue", None)
        if isinstance(queue, asyncio.Queue):
            while True:
                try:
                    queue.get_nowait()
                    queued_count += 1
                except asyncio.QueueEmpty:
                    break

        injections = getattr(controller, "_pending_injections", None)
        injection_count = len(injections) if isinstance(injections, list) else 0
        if isinstance(injections, list):
            injections.clear()

        return RuntimePendingStateReset(
            pending_events=pending_count,
            queued_events=queued_count,
            pending_injections=injection_count,
        )

    def install_tool_policy(self) -> RuntimeToolPolicyStatus:
        # 权限组件必须能在 Agent 启动前安装；RUNNING 仅用于兼容显式重验。
        self._lifecycle.ensure(
            RuntimeLifecycleState.NEW,
            RuntimeLifecycleState.RUNNING,
        )
        from nanobot_kt.tool_runtime import (
            install_tool_loop_control,
            install_tool_plan_guard,
            install_tool_plan_native_schema_filter,
            tool_plan_runtime_status,
        )

        install_tool_plan_guard(self._agent)
        install_tool_loop_control(self._agent)
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
        raw: object = ()
        list_tools = getattr(registry, "list_tools", None)
        if callable(list_tools):
            try:
                raw = list_tools() or ()
            except Exception:
                raw = ()
        if not raw:
            # KT 1.3 兼容 fallback；私有访问只允许留在本 Adapter。
            raw = getattr(registry, "_tools", {})
            if isinstance(raw, dict):
                raw = tuple(raw)
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
        del reason  # 原因由调用侧事件记录，不能注入 KT 控制流。
        self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
        interrupt = getattr(self._agent, "interrupt", None)
        if callable(interrupt):
            interrupt()
            return True
        if hasattr(self._agent, "_interrupt_requested"):
            # KT 旧版 fallback；私有访问被限制在本 Adapter 内。
            self._agent._interrupt_requested = True
            return True
        return False


def build_kt13_runtime(
    agent: object,
    *,
    runtime_id: str | None = None,
    route_applier: KtModelRouteApplier | None = None,
    event_sinks: tuple[RuntimeLifecycleEventSink, ...] = (),
    initially_started: bool | None = None,
) -> AgentRuntimePort:
    """显式 composition factory；不读取或写入模块级单例。"""

    resolved_id = str(runtime_id or "").strip()
    if not resolved_id:
        config = getattr(agent, "config", None)
        name = str(getattr(config, "name", "") or "").strip()
        resolved_id = f"kt13:{name or 'agent'}"
    return Kt13RuntimeAdapter(
        agent,
        runtime_id=resolved_id,
        route_applier=route_applier,
        event_sinks=event_sinks,
        initially_started=initially_started,
    )


__all__ = [
    "Kt13RuntimeAdapter",
    "KtModelRouteApplier",
    "KtOpenAITransportConfig",
    "apply_kt_openai_model_route",
    "build_kt13_runtime",
]

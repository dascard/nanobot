"""不依赖外部 Agent 框架的最小原生 Runtime。"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Protocol

from core.agent_runtime.contracts import (
    AgentTurnRequest,
    AgentTurnResult,
    RuntimeCapabilities,
    RuntimeCapability,
    RuntimeLifecycleEvent,
    RuntimeLifecycleEventSink,
    RuntimeLifecycleState,
    RuntimeMessage,
    RuntimeModelRoute,
    RuntimePendingStateReset,
    RuntimePlanKind,
    RuntimeRunError,
    RuntimeRunEvent,
    RuntimeRunEventHandler,
    RuntimeRunStatus,
    RuntimeToolCall,
    RuntimeToolCallStatus,
    RuntimeToolExecutionRequest,
    RuntimeToolExecutionResult,
    RuntimeToolPolicyStatus,
    RuntimeTurnKind,
    RuntimeUsage,
    ToolExecutionPort,
)
from core.agent_runtime.errors import (
    AgentRuntimeCapabilityError,
    AgentRuntimeExecutionError,
)
from core.agent_runtime.event_stream import (
    RuntimeRunEventEmitter,
    relay_runtime_run_events,
)
from core.agent_runtime.lifecycle import RuntimeLifecycleMachine
from core.model_provider.chat_runtime import (
    ChatCompletionPort,
    ChatCompletionRequest,
)


class NativeToolPlan(Protocol):
    """Native Runtime 实际需要的 ToolPlan 最小只读能力。"""

    @property
    def sha256(self) -> str: ...

    @property
    def sent_tool_schemas(self) -> tuple[dict[str, Any], ...]: ...

    @property
    def executable_tool_names(self) -> frozenset[str]: ...

    def ensure_executable(self, tool_name: str) -> None: ...


NativeToolPlanResolver = Callable[[], NativeToolPlan | None]
NativeToolBindingResolver = Callable[[str], str]
NativeTextDeltaHandler = Callable[[str], Awaitable[None]]
NativeToolActivityHandler = Callable[[RuntimeToolCall], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class NativeAgentRuntimeConfig:
    """Native Runtime 的集中式硬上限。"""

    max_model_steps: int = 8
    max_model_attempts: int = 2
    max_tool_rounds: int = 6
    request_timeout_seconds: float = 120.0
    tool_timeout_seconds: float = 60.0
    terminal_tool_names: frozenset[str] = frozenset(
        {"reply", "no_reply", "ai_daily", "group_analysis"}
    )

    def __post_init__(self) -> None:
        for name in (
            "max_model_steps",
            "max_model_attempts",
            "max_tool_rounds",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} 必须是正整数")
        for name in ("request_timeout_seconds", "tool_timeout_seconds"):
            value = getattr(self, name)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or float(value) <= 0
            ):
                raise ValueError(f"{name} 必须是有限正数")
        object.__setattr__(
            self,
            "request_timeout_seconds",
            float(self.request_timeout_seconds),
        )
        object.__setattr__(
            self,
            "tool_timeout_seconds",
            float(self.tool_timeout_seconds),
        )
        object.__setattr__(
            self,
            "terminal_tool_names",
            frozenset(
                str(name).strip()
                for name in self.terminal_tool_names
                if str(name).strip()
            ),
        )


@dataclass(frozen=True, slots=True)
class _CompletionTurn:
    content: str
    tool_calls: tuple[RuntimeToolCall, ...]
    usage: RuntimeUsage | None
    raw_response: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _NativeRunOutcome:
    result: AgentTurnResult
    usage: RuntimeUsage | None


class _NativeModelResponseError(AgentRuntimeExecutionError):
    pass


class _NativeAmbiguousExecutionError(AgentRuntimeExecutionError):
    pass


def _default_tool_plan_resolver() -> NativeToolPlan | None:
    from core.tool_plan import get_current_tool_plan

    return get_current_tool_plan()


def _default_tool_binding_resolver(tool_name: str) -> str:
    from core.tool_registration import get_tool_registration

    registration = get_tool_registration(tool_name)
    binding = registration.execution_binding if registration is not None else None
    return str(binding.port_id if binding is not None else "").strip()


def _default_available_tool_names() -> tuple[str, ...]:
    from core.tool_registration import list_active_tool_registrations

    return tuple(
        sorted(registration.name for registration in list_active_tool_registrations())
    )


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _usage_from_mapping(payload: Any) -> RuntimeUsage | None:
    usage = _mapping(payload)
    if usage is None:
        return None
    prompt_details = _mapping(usage.get("prompt_tokens_details")) or {}
    completion_details = _mapping(usage.get("completion_tokens_details")) or {}
    normalized = RuntimeUsage(
        input_tokens=_nonnegative_int(
            usage.get("prompt_tokens", usage.get("input_tokens"))
        ),
        output_tokens=_nonnegative_int(
            usage.get("completion_tokens", usage.get("output_tokens"))
        ),
        cached_input_tokens=_nonnegative_int(
            prompt_details.get(
                "cached_tokens",
                usage.get("cached_tokens", usage.get("cached_input_tokens")),
            )
        ),
        reasoning_tokens=_nonnegative_int(
            completion_details.get(
                "reasoning_tokens",
                usage.get("reasoning_tokens"),
            )
        ),
        cost_microunits=_nonnegative_int(usage.get("cost_microunits")),
    )
    if (
        not normalized.total_tokens
        and not normalized.reasoning_tokens
        and not normalized.cost_microunits
    ):
        return None
    return normalized


def _usage_to_mapping(usage: RuntimeUsage | None) -> dict[str, int]:
    if usage is None:
        return {}
    return {
        "prompt_tokens": usage.input_tokens,
        "completion_tokens": usage.output_tokens,
        "cached_tokens": usage.cached_input_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "cost_microunits": usage.cost_microunits,
    }


def _merge_usage(
    current: RuntimeUsage | None,
    incoming: RuntimeUsage | None,
) -> RuntimeUsage | None:
    if incoming is None:
        return current
    if current is None:
        return incoming
    return RuntimeUsage(
        input_tokens=current.input_tokens + incoming.input_tokens,
        output_tokens=current.output_tokens + incoming.output_tokens,
        cached_input_tokens=(
            current.cached_input_tokens + incoming.cached_input_tokens
        ),
        reasoning_tokens=current.reasoning_tokens + incoming.reasoning_tokens,
        cost_microunits=current.cost_microunits + incoming.cost_microunits,
    )


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    parts: list[str] = []
    for item in _sequence(value):
        part = _mapping(item)
        if part is None:
            continue
        if part.get("type") in {None, "text", "output_text"}:
            text = part.get("text", part.get("content", ""))
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _normalize_wire_part(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normalize_wire_part(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_wire_part(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _normalize_wire_part(to_dict())
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _tool_call_wire(call: RuntimeToolCall) -> dict[str, Any]:
    arguments = call.arguments
    if isinstance(arguments, str):
        encoded_arguments = arguments
    else:
        encoded_arguments = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    return {
        "id": call.call_id,
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": encoded_arguments,
        },
    }


def _message_wire(message: RuntimeMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "role": message.role,
        "content": _normalize_wire_part(message.content),
    }
    if message.name:
        payload["name"] = message.name
    if message.tool_call_id:
        payload["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        payload["tool_calls"] = [_tool_call_wire(call) for call in message.tool_calls]
    return payload


def _parse_tool_arguments(value: Any, *, tool_name: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"工具 {tool_name} 的 arguments 不是合法 JSON") from exc
        if isinstance(parsed, Mapping):
            return dict(parsed)
    raise ValueError(f"工具 {tool_name} 的 arguments 必须是 JSON 对象")


def _parse_tool_calls(
    raw_calls: Any, *, model_step: int
) -> tuple[RuntimeToolCall, ...]:
    calls: list[RuntimeToolCall] = []
    seen_ids: set[str] = set()
    for index, raw_call in enumerate(_sequence(raw_calls), start=1):
        call = _mapping(raw_call)
        if call is None:
            continue
        function = _mapping(call.get("function"))
        if function is None:
            continue
        name = str(function.get("name") or "").strip()
        if not name:
            raise ValueError("模型返回了缺少名称的工具调用")
        call_id = str(call.get("id") or f"call_{model_step}_{index}").strip()
        if call_id in seen_ids:
            raise ValueError(f"模型返回了重复 tool_call_id：{call_id}")
        seen_ids.add(call_id)
        calls.append(
            RuntimeToolCall(
                call_id=call_id,
                name=name,
                arguments=_parse_tool_arguments(
                    function.get("arguments", "{}"),
                    tool_name=name,
                ),
            )
        )
    return tuple(calls)


def _model_error_message(payload: Mapping[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, Mapping):
        value = error.get("message") or error.get("code") or "模型调用失败"
    else:
        value = error or payload.get("detail") or "模型调用失败"
    return str(value).strip()[:500] or "模型调用失败"


def _is_retryable_model_error(message: str) -> bool:
    lowered = message.lower()
    deterministic = (
        "missing",
        "disabled",
        "lacks required",
        "no candidates",
        "invalid",
        "unauthorized",
        "forbidden",
    )
    return not any(marker in lowered for marker in deterministic)


def _completion_from_response(
    response: Mapping[str, Any],
    *,
    model_step: int,
) -> _CompletionTurn:
    if response.get("error"):
        raise _NativeModelResponseError(_model_error_message(response))
    choices = _sequence(response.get("choices"))
    first = _mapping(choices[0]) if choices else None
    message = _mapping(first.get("message")) if first is not None else None
    if message is None:
        raise _NativeModelResponseError("模型响应缺少 choices[0].message")
    content = _content_text(message.get("content"))
    calls = _parse_tool_calls(message.get("tool_calls"), model_step=model_step)
    if not content and not calls:
        raise _NativeModelResponseError("模型响应没有文本或工具调用")
    return _CompletionTurn(
        content=content,
        tool_calls=calls,
        usage=_usage_from_mapping(response.get("usage")),
        raw_response=dict(response),
    )


def _accumulate_stream_tool_calls(
    accumulator: dict[int, dict[str, Any]],
    raw_calls: Any,
) -> bool:
    changed = False
    for fallback_index, raw_call in enumerate(_sequence(raw_calls)):
        call = _mapping(raw_call)
        if call is None:
            continue
        index = call.get("index")
        if not isinstance(index, int):
            index = fallback_index
        entry = accumulator.setdefault(
            index,
            {
                "id": "",
                "type": "function",
                "function": {"name": "", "arguments": ""},
            },
        )
        call_id = call.get("id")
        if isinstance(call_id, str) and call_id:
            entry["id"] = call_id
            changed = True
        call_type = call.get("type")
        if isinstance(call_type, str) and call_type:
            entry["type"] = call_type
            changed = True
        function = _mapping(call.get("function"))
        if function is None:
            continue
        target = entry["function"]
        name = function.get("name")
        if isinstance(name, str) and name:
            current = str(target.get("name") or "")
            if not current:
                target["name"] = name
            elif name.startswith(current):
                target["name"] = name
            elif not current.endswith(name):
                target["name"] = current + name
            changed = True
        arguments = function.get("arguments")
        if isinstance(arguments, str) and arguments:
            target["arguments"] = str(target.get("arguments") or "") + arguments
            changed = True
    return changed


def _tool_output_text(result: RuntimeToolExecutionResult) -> str:
    output = result.output
    if output is not None:
        if isinstance(output, str):
            return output
        return json.dumps(
            output,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    if result.success:
        return '{"status":"success"}'
    error = result.error
    return json.dumps(
        {
            "status": "error",
            "error": {
                "code": error.code if error is not None else "tool_execution_failed",
                "message": error.message if error is not None else "工具执行失败",
                "retryable": error.retryable if error is not None else False,
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _runtime_context_payload(request: AgentTurnRequest) -> dict[str, object]:
    context = request.context
    actor = context.actor
    attributes = {
        attribute.key: attribute.value for attribute in request.event_attributes
    }
    is_group = context.chat_type.value == "group"
    owner_id = context.principal.owner_id
    actor_id = actor.actor_id if actor is not None else owner_id
    return {
        "chat_type": context.chat_type.value,
        "runtime_chat_type": str(
            attributes.get("runtime_chat_type", context.chat_type.value) or ""
        ),
        "is_group": is_group,
        "is_super_user": context.feature_enabled("super_user"),
        "session_id": context.session_id,
        "group_id": owner_id if is_group else str(attributes.get("group_id", "")),
        "user_id": str(attributes.get("actor_user_id", actor_id) or ""),
        "platform": context.principal.platform,
        "sender_name": str(attributes.get("sender_name", "") or ""),
        "trace_id": context.trace_id,
        "run_id": context.run_id,
        "turn_id": context.turn_id,
        "correlation_id": context.correlation_id,
        "actor_type": actor.actor_type.value if actor is not None else "",
        "actor_id": actor_id,
        "actor_parent_id": actor.parent_actor_id if actor is not None else "",
        "owner_type": context.principal.owner_type.value,
        "owner_id": owner_id,
        "message_id": context.message_id,
    }


class NativeAgentRuntime:
    """直接组合模型 Port、ToolPlan 与确定性工具 Port 的 Agent Runtime。"""

    def __init__(
        self,
        completion_port: ChatCompletionPort,
        tool_execution_port: ToolExecutionPort,
        *,
        runtime_id: str = "native:default",
        config: NativeAgentRuntimeConfig | None = None,
        tool_plan_resolver: NativeToolPlanResolver | None = None,
        tool_binding_resolver: NativeToolBindingResolver | None = None,
        available_tool_names: tuple[str, ...] | None = None,
        event_sinks: tuple[RuntimeLifecycleEventSink, ...] = (),
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(completion_port, ChatCompletionPort):
            raise TypeError("completion_port 未实现 ChatCompletionPort")
        if not isinstance(tool_execution_port, ToolExecutionPort):
            raise TypeError("tool_execution_port 未实现 ToolExecutionPort")
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lifecycle = RuntimeLifecycleMachine(
            runtime_id,
            event_sinks=event_sinks,
            now=self._now,
        )
        self._completion_port = completion_port
        self._tool_execution_port = tool_execution_port
        self._config = config or NativeAgentRuntimeConfig()
        self._tool_plan_resolver = tool_plan_resolver or _default_tool_plan_resolver
        self._tool_binding_resolver = (
            tool_binding_resolver or _default_tool_binding_resolver
        )
        self._available_tool_names = tuple(
            sorted(
                set(
                    available_tool_names
                    if available_tool_names is not None
                    else _default_available_tool_names()
                )
            )
        )
        self._messages: tuple[RuntimeMessage, ...] = ()
        self._tool_calls: tuple[RuntimeToolCall, ...] = ()
        self._route: RuntimeModelRoute | None = None
        self._tool_guards: list[object] = []
        self._run_lock = asyncio.Lock()
        self._active_task: asyncio.Task[Any] | None = None

    @property
    def runtime_id(self) -> str:
        return self._lifecycle.runtime_id

    @property
    def state(self) -> RuntimeLifecycleState:
        return self._lifecycle.state

    @property
    def lifecycle_events(self) -> tuple[RuntimeLifecycleEvent, ...]:
        return self._lifecycle.events

    @property
    def runtime_capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            runtime_id=self.runtime_id,
            supported=frozenset(RuntimeCapability),
        )

    async def start(self) -> None:
        self._lifecycle.ensure(RuntimeLifecycleState.NEW)
        self._lifecycle.transition(RuntimeLifecycleState.STARTING)
        self._lifecycle.transition(RuntimeLifecycleState.RUNNING)

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
        task = self._active_task
        if task is not None and not task.done():
            task.cancel()
            if task is not asyncio.current_task():
                with suppress(asyncio.CancelledError):
                    await task
        self._lifecycle.transition(RuntimeLifecycleState.STOPPED)

    def _resolve_tool_plan(self, request: AgentTurnRequest) -> NativeToolPlan | None:
        reference = request.context.plan(RuntimePlanKind.TOOL)
        plan = self._tool_plan_resolver()
        if reference is None:
            return None
        if plan is None:
            raise AgentRuntimeCapabilityError(
                "请求声明了 ToolPlan，但当前上下文没有冻结计划",
                runtime_id=self.runtime_id,
            )
        if str(plan.sha256).lower() != reference.sha256:
            raise AgentRuntimeCapabilityError(
                "请求 ToolPlan 摘要与当前冻结计划不一致",
                runtime_id=self.runtime_id,
            )
        return plan

    def _completion_request(
        self,
        request: AgentTurnRequest,
        plan: NativeToolPlan | None,
    ) -> ChatCompletionRequest:
        route = self._route
        return ChatCompletionRequest(
            messages=tuple(_message_wire(message) for message in self._messages),
            tools=(
                tuple(plan.sent_tool_schemas)
                if plan is not None and plan.sent_tool_schemas
                else None
            ),
            temperature=(
                route.temperature
                if route is not None and route.temperature is not None
                else 0.7
            ),
            manual_model=route.model_id if route is not None else "",
            max_tokens=route.max_tokens if route is not None else None,
            trace_id=request.context.trace_id,
            run_id=request.context.run_id,
            trace_source="native_agent",
            enable_thinking=(
                route.enable_thinking
                if route is not None and route.enable_thinking is not None
                else "auto"
            ),
        )

    async def _complete_non_streaming(
        self,
        completion_request: ChatCompletionRequest,
        *,
        model_step: int,
    ) -> _CompletionTurn:
        last_error: BaseException | None = None
        for attempt in range(self._config.max_model_attempts):
            try:
                response = await self._completion_port.complete_chat(completion_request)
                return _completion_from_response(response, model_step=model_step)
            except asyncio.CancelledError:
                raise
            except TimeoutError as exc:
                last_error = exc
            except _NativeModelResponseError as exc:
                last_error = exc
                if not _is_retryable_model_error(str(exc)):
                    break
            except Exception as exc:
                last_error = exc
            if attempt + 1 >= self._config.max_model_attempts:
                break
        if isinstance(last_error, TimeoutError):
            raise last_error
        if isinstance(last_error, _NativeModelResponseError):
            raise AgentRuntimeExecutionError(
                f"Native 模型响应无效：{last_error}",
                runtime_id=self.runtime_id,
            ) from last_error
        raise AgentRuntimeExecutionError(
            "Native 模型调用失败",
            runtime_id=self.runtime_id,
        ) from last_error

    async def _complete_streaming(
        self,
        completion_request: ChatCompletionRequest,
        *,
        model_step: int,
        on_text_delta: NativeTextDeltaHandler | None,
    ) -> _CompletionTurn:
        last_error: BaseException | None = None
        for attempt in range(self._config.max_model_attempts):
            content_parts: list[str] = []
            raw_tool_calls: dict[int, dict[str, Any]] = {}
            usage: RuntimeUsage | None = None
            saw_irreversible_payload = False
            try:
                async for chunk in self._completion_port.stream_chat(
                    completion_request
                ):
                    if chunk.get("error"):
                        raise _NativeModelResponseError(_model_error_message(chunk))
                    chunk_usage = _usage_from_mapping(chunk.get("usage"))
                    if chunk_usage is not None:
                        usage = chunk_usage
                    choices = _sequence(chunk.get("choices"))
                    first = _mapping(choices[0]) if choices else None
                    if first is None:
                        continue
                    delta = _mapping(first.get("delta")) or _mapping(
                        first.get("message")
                    )
                    if delta is None:
                        continue
                    content = _content_text(delta.get("content"))
                    if content:
                        content_parts.append(content)
                        saw_irreversible_payload = True
                        if on_text_delta is not None:
                            await on_text_delta(content)
                    if _accumulate_stream_tool_calls(
                        raw_tool_calls,
                        delta.get("tool_calls"),
                    ):
                        saw_irreversible_payload = True
                calls = _parse_tool_calls(
                    [raw_tool_calls[index] for index in sorted(raw_tool_calls)],
                    model_step=model_step,
                )
                content = "".join(content_parts)
                if not content and not calls:
                    raise _NativeModelResponseError("模型流没有文本或工具调用")
                message: dict[str, Any] = {
                    "role": "assistant",
                    "content": content,
                }
                if calls:
                    message["tool_calls"] = [_tool_call_wire(call) for call in calls]
                raw_response: dict[str, Any] = {
                    "choices": [{"message": message}],
                    "_native_stream": True,
                }
                usage_payload = _usage_to_mapping(usage)
                if usage_payload:
                    raw_response["usage"] = usage_payload
                return _CompletionTurn(
                    content=content,
                    tool_calls=calls,
                    usage=usage,
                    raw_response=raw_response,
                )
            except asyncio.CancelledError:
                raise
            except TimeoutError as exc:
                last_error = exc
            except Exception as exc:
                last_error = exc
            if saw_irreversible_payload:
                raise _NativeAmbiguousExecutionError(
                    "Native 模型流在部分输出后中断",
                    runtime_id=self.runtime_id,
                ) from last_error
            if isinstance(
                last_error, _NativeModelResponseError
            ) and not _is_retryable_model_error(str(last_error)):
                break
            if attempt + 1 >= self._config.max_model_attempts:
                break
        if isinstance(last_error, TimeoutError):
            raise last_error
        if isinstance(last_error, _NativeModelResponseError):
            raise AgentRuntimeExecutionError(
                f"Native 模型流响应无效：{last_error}",
                runtime_id=self.runtime_id,
            ) from last_error
        raise AgentRuntimeExecutionError(
            "Native 模型流调用失败",
            runtime_id=self.runtime_id,
        ) from last_error

    async def _invoke_model(
        self,
        request: AgentTurnRequest,
        plan: NativeToolPlan | None,
        *,
        model_step: int,
        on_text_delta: NativeTextDeltaHandler | None,
    ) -> _CompletionTurn:
        completion_request = self._completion_request(request, plan)
        if request.stream:
            return await self._complete_streaming(
                completion_request,
                model_step=model_step,
                on_text_delta=on_text_delta,
            )
        return await self._complete_non_streaming(
            completion_request,
            model_step=model_step,
        )

    async def _execute_tool(
        self,
        request: AgentTurnRequest,
        call: RuntimeToolCall,
    ) -> RuntimeToolExecutionResult:
        arguments = dict(call.arguments) if isinstance(call.arguments, Mapping) else {}
        for guard in self._tool_guards:
            pre_tool_execute = getattr(guard, "pre_tool_execute", None)
            if not callable(pre_tool_execute):
                raise AgentRuntimeCapabilityError(
                    "Native 工具守卫缺少 pre_tool_execute",
                    runtime_id=self.runtime_id,
                )
            guarded = pre_tool_execute(
                arguments,
                tool_name=call.name,
                job_id=call.call_id,
            )
            if inspect.isawaitable(guarded):
                guarded = await guarded
            if guarded is not None:
                if not isinstance(guarded, Mapping):
                    raise AgentRuntimeCapabilityError(
                        "Native 工具守卫返回了无效参数",
                        runtime_id=self.runtime_id,
                    )
                arguments = dict(guarded)
        if arguments != call.arguments:
            call = RuntimeToolCall(
                call_id=call.call_id,
                name=call.name,
                arguments=arguments,
            )
        binding_id = self._tool_binding_resolver(call.name)
        if not binding_id:
            raise AgentRuntimeCapabilityError(
                f"工具缺少 Native execution binding：{call.name}",
                runtime_id=self.runtime_id,
            )
        execution_request = RuntimeToolExecutionRequest(
            context=request.context,
            tool_call=call,
            execution_port_id=binding_id,
            idempotency_key=(f"{request.context.request_id}:{call.call_id}"),
            timeout_seconds=self._config.tool_timeout_seconds,
            attributes=request.event_attributes,
        )
        try:
            result = await self._tool_execution_port.execute(execution_request)
        except asyncio.CancelledError:
            raise
        except AgentRuntimeCapabilityError:
            raise
        except TimeoutError:
            return RuntimeToolExecutionResult(
                tool_call=RuntimeToolCall(
                    call_id=call.call_id,
                    name=call.name,
                    arguments=call.arguments,
                    status=RuntimeToolCallStatus.TIMED_OUT,
                ),
                error=RuntimeRunError(
                    code="tool_timeout",
                    message=f"工具执行超时：{call.name}",
                    retryable=True,
                ),
            )
        except Exception as exc:
            return RuntimeToolExecutionResult(
                tool_call=RuntimeToolCall(
                    call_id=call.call_id,
                    name=call.name,
                    arguments=call.arguments,
                    status=RuntimeToolCallStatus.FAILED,
                ),
                error=RuntimeRunError(
                    code="tool_execution_failed",
                    message=f"工具执行失败：{call.name} ({type(exc).__name__})",
                    retryable=False,
                ),
            )
        if not isinstance(result, RuntimeToolExecutionResult):
            raise AgentRuntimeExecutionError(
                f"工具执行 Port 返回了无效结果：{call.name}",
                runtime_id=self.runtime_id,
            )
        if (
            result.tool_call.call_id != call.call_id
            or result.tool_call.name != call.name
        ):
            raise AgentRuntimeExecutionError(
                f"工具执行结果与请求不匹配：{call.name}",
                runtime_id=self.runtime_id,
            )
        return result

    async def _run_loop(
        self,
        request: AgentTurnRequest,
        *,
        on_text_delta: NativeTextDeltaHandler | None,
        on_tool_activity: NativeToolActivityHandler | None,
    ) -> _NativeRunOutcome:
        plan = self._resolve_tool_plan(request)
        messages = list(self._messages)
        if request.kind is RuntimeTurnKind.USER_INPUT:
            messages.append(RuntimeMessage("user", request.content))
            self._messages = tuple(messages)

        turn_tool_calls: list[RuntimeToolCall] = []
        turn_usage: RuntimeUsage | None = None
        tool_rounds = 0
        for model_step in range(1, self._config.max_model_steps + 1):
            completion = await self._invoke_model(
                request,
                plan,
                model_step=model_step,
                on_text_delta=on_text_delta,
            )
            turn_usage = _merge_usage(turn_usage, completion.usage)
            assistant = RuntimeMessage(
                "assistant",
                completion.content,
                tool_calls=completion.tool_calls,
            )
            messages.append(assistant)
            self._messages = tuple(messages)

            if not completion.tool_calls:
                self._tool_calls = tuple(turn_tool_calls)
                return _NativeRunOutcome(
                    result=AgentTurnResult(
                        raw_result=dict(completion.raw_response),
                        messages=self._messages,
                        tool_calls=self._tool_calls,
                    ),
                    usage=turn_usage,
                )

            tool_rounds += 1
            if tool_rounds > self._config.max_tool_rounds:
                raise AgentRuntimeExecutionError(
                    "Native 工具循环超过最大轮数",
                    runtime_id=self.runtime_id,
                )

            final_calls = [
                call
                for call in completion.tool_calls
                if call.name in self._config.terminal_tool_names
            ]
            if final_calls and len(completion.tool_calls) != 1:
                raise AgentRuntimeExecutionError(
                    "最终动作工具必须单独调用",
                    runtime_id=self.runtime_id,
                )

            terminal = False
            for call in completion.tool_calls:
                if plan is None:
                    raise AgentRuntimeCapabilityError(
                        f"模型在无 ToolPlan 时请求工具：{call.name}",
                        runtime_id=self.runtime_id,
                    )
                try:
                    plan.ensure_executable(call.name)
                except Exception as exc:
                    raise AgentRuntimeCapabilityError(
                        f"模型请求了 ToolPlan 未授权工具：{call.name}",
                        runtime_id=self.runtime_id,
                    ) from exc
                result = await self._execute_tool(request, call)
                turn_tool_calls.append(result.tool_call)
                messages.append(
                    RuntimeMessage(
                        "tool",
                        _tool_output_text(result),
                        name=call.name,
                        tool_call_id=call.call_id,
                    )
                )
                self._messages = tuple(messages)
                if on_tool_activity is not None:
                    await on_tool_activity(result.tool_call)
                if call.name in self._config.terminal_tool_names and not result.success:
                    self._tool_calls = tuple(turn_tool_calls)
                    raise AgentRuntimeExecutionError(
                        f"最终动作工具执行失败：{call.name}",
                        runtime_id=self.runtime_id,
                    )
                terminal = terminal or (
                    result.success
                    and (
                        call.name in self._config.terminal_tool_names
                        or bool(result.metadata.get("stop", False))
                    )
                )

            self._tool_calls = tuple(turn_tool_calls)
            if terminal:
                return _NativeRunOutcome(
                    result=AgentTurnResult(
                        raw_result=dict(completion.raw_response),
                        messages=self._messages,
                        tool_calls=self._tool_calls,
                    ),
                    usage=turn_usage,
                )

        raise AgentRuntimeExecutionError(
            "Native 模型—工具循环超过最大步数",
            runtime_id=self.runtime_id,
        )

    def _request_timeout(self, request: AgentTurnRequest) -> float:
        timeout = self._config.request_timeout_seconds
        route = self._route
        if route is not None and route.timeout_seconds is not None:
            timeout = min(timeout, route.timeout_seconds)
        deadline = request.context.deadline_at
        if deadline is None:
            return timeout
        remaining = (deadline - self._now()).total_seconds()
        if remaining <= 0:
            raise TimeoutError("Native 请求 deadline 已过期")
        return min(timeout, remaining)

    async def _execute(
        self,
        request: AgentTurnRequest,
        *,
        on_text_delta: NativeTextDeltaHandler | None = None,
        on_tool_activity: NativeToolActivityHandler | None = None,
    ) -> _NativeRunOutcome:
        self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
        from core.agent_runtime.request_scope import runtime_context_scope

        async with self._run_lock:
            self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
            task = asyncio.current_task()
            self._active_task = task
            try:
                timeout = self._request_timeout(request)
                with runtime_context_scope(_runtime_context_payload(request)):
                    async with asyncio.timeout(timeout):
                        return await self._run_loop(
                            request,
                            on_text_delta=on_text_delta,
                            on_tool_activity=on_tool_activity,
                        )
            finally:
                if self._active_task is task:
                    self._active_task = None

    async def run(self, request: AgentTurnRequest) -> AgentTurnResult:
        return (await self._execute(request)).result

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
        emitter = RuntimeRunEventEmitter(
            request.context.execution_identity(),
            handler,
        )
        await emitter.status_changed(RuntimeRunStatus.ACCEPTED)
        await emitter.status_changed(RuntimeRunStatus.RUNNING)
        try:
            outcome = await self._execute(
                request,
                on_text_delta=emitter.text_delta,
                on_tool_activity=emitter.tool_activity,
            )
        except asyncio.CancelledError:
            await emitter.end(RuntimeRunStatus.CANCELLED)
            raise
        except TimeoutError as exc:
            await emitter.error(
                RuntimeRunError(
                    code="runtime_timeout",
                    message=str(exc) or "Native Runtime 执行超时",
                    retryable=True,
                )
            )
            await emitter.end(RuntimeRunStatus.TIMED_OUT)
            raise
        except _NativeAmbiguousExecutionError as exc:
            await emitter.error(
                RuntimeRunError(
                    code="native_stream_ambiguous",
                    message=str(exc),
                    retryable=False,
                )
            )
            await emitter.end(RuntimeRunStatus.AMBIGUOUS)
            raise
        except Exception as exc:
            await emitter.error(
                RuntimeRunError(
                    code=getattr(exc, "code", "runtime_execution_error"),
                    message=str(exc) or type(exc).__name__,
                    retryable=False,
                )
            )
            await emitter.end(RuntimeRunStatus.FAILED)
            raise
        if outcome.usage is not None:
            await emitter.usage(outcome.usage)
        await emitter.end(RuntimeRunStatus.SUCCEEDED)
        return outcome.result

    async def execute_turn(self, request: AgentTurnRequest) -> AgentTurnResult:
        """旧版兼容 façade；新调用方使用 run/run_event。"""

        return await self.run(request)

    def replace_conversation(self, messages: tuple[RuntimeMessage, ...]) -> int:
        self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
        self._messages = tuple(messages)
        self._tool_calls = self._inspect_messages(self._messages)
        return len(self._messages)

    def read_conversation(self) -> tuple[RuntimeMessage, ...]:
        return self._messages

    @staticmethod
    def _inspect_messages(
        messages: tuple[RuntimeMessage, ...],
    ) -> tuple[RuntimeToolCall, ...]:
        calls: list[RuntimeToolCall] = []
        indexes: dict[str, int] = {}
        for message in messages:
            for call in message.tool_calls:
                indexes[call.call_id] = len(calls)
                calls.append(call)
            if message.role != "tool" or not message.tool_call_id:
                continue
            index = indexes.get(message.tool_call_id)
            if index is None:
                continue
            requested = calls[index]
            calls[index] = RuntimeToolCall(
                call_id=requested.call_id,
                name=requested.name,
                arguments=requested.arguments,
                status=RuntimeToolCallStatus.COMPLETED,
                result=message.content,
            )
        return tuple(calls)

    def clear_pending_events(self) -> RuntimePendingStateReset:
        self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
        return RuntimePendingStateReset()

    def install_tool_policy(self) -> RuntimeToolPolicyStatus:
        self._lifecycle.ensure(
            RuntimeLifecycleState.NEW,
            RuntimeLifecycleState.RUNNING,
        )
        return RuntimeToolPolicyStatus(
            ready=True,
            guard_installed=True,
            schema_filter_installed=True,
        )

    def install_tool_guard(self, guard: object) -> bool:
        """安装 Native 专用请求守卫；不接受无执行钩子的占位对象。"""

        if not callable(getattr(guard, "pre_tool_execute", None)):
            return False
        if guard in self._tool_guards:
            return True
        self._tool_guards.append(guard)
        return True

    def set_model_route(self, route: RuntimeModelRoute) -> None:
        self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
        if not isinstance(route, RuntimeModelRoute):
            raise TypeError("route 必须是 RuntimeModelRoute")
        self._route = route

    def inspect_tool_calls(self) -> tuple[RuntimeToolCall, ...]:
        return self._tool_calls

    def list_tool_names(self) -> tuple[str, ...]:
        return self._available_tool_names

    def interrupt(self, *, reason: str = "") -> bool:
        del reason
        self._lifecycle.ensure(RuntimeLifecycleState.RUNNING)
        task = self._active_task
        if task is None or task.done():
            return False
        task.cancel()
        return True


__all__ = [
    "NativeAgentRuntime",
    "NativeAgentRuntimeConfig",
    "NativeToolBindingResolver",
    "NativeToolPlan",
    "NativeToolPlanResolver",
]

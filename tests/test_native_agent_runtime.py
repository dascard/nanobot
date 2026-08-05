from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator, Mapping
from datetime import datetime
from typing import Any

import pytest

from core.agent_runtime import (
    AgentRuntimeCapabilityError,
    AgentRuntimeBudgetExceededError,
    AgentRuntimeExecutionError,
    AgentRuntimePermissionError,
    AgentRuntimePort,
    AgentTurnRequest,
    InMemoryRunEventSink,
    NativeAgentRuntime,
    NativeAgentRuntimeConfig,
    RegisteredToolExecutionPort,
    RequestRuntimeContext,
    RuntimeActor,
    RuntimeActorType,
    RuntimeAccessEnvelope,
    RuntimeAccessGrant,
    RuntimeAccessKind,
    RuntimeBudgetEnvelope,
    RuntimeBudgetLimit,
    RuntimeBudgetManager,
    RuntimeBudgetScope,
    RuntimeArtifactRef,
    RuntimeChatType,
    RuntimeLifecycleState,
    RuntimeMessage,
    RuntimeModelRoute,
    RuntimeGovernanceEnvelope,
    RuntimeOwnerType,
    RuntimePlanKind,
    RuntimePlanRef,
    RuntimePrincipal,
    RuntimeRunEventKind,
    RuntimeRunStatus,
    RuntimeToolCall,
    RuntimeToolCallStatus,
    RuntimeToolExecutionRequest,
    RuntimeToolExecutionResult,
    RuntimeTurnKind,
    StaticPermissionPort,
)
from core.context_compaction import (
    ContextCompactionPolicy,
    TOOL_RESULT_ENVELOPE_KEY,
    unwrap_tool_result_content,
)
from core.model_provider.chat_runtime import ChatCompletionRequest
from core.tool_plan import ToolPlan


def _tool_schema(name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"执行 {name}",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    }


def _tool_plan(*names: str) -> ToolPlan:
    return ToolPlan.from_effective_tools(
        enabled={name: True for name in names},
        chat_type="private",
        tool_schemas=[_tool_schema(name) for name in names],
    )


def _context(
    plan: ToolPlan | None = None,
    *,
    plan_sha256: str = "",
    deadline_at: datetime | None = None,
    governance: RuntimeGovernanceEnvelope | None = None,
) -> RequestRuntimeContext:
    digest = plan_sha256 or (plan.sha256 if plan is not None else "")
    plans = (
        (
            RuntimePlanRef(
                RuntimePlanKind.TOOL,
                "tool-plan:native-test",
                digest,
            ),
        )
        if digest
        else ()
    )
    return RequestRuntimeContext(
        request_id="request-native-1",
        agent_id="test.agent",
        principal=RuntimePrincipal(
            platform="qq",
            owner_type=RuntimeOwnerType.USER,
            owner_id="10001",
        ),
        session_id="private_10001",
        chat_type=RuntimeChatType.PRIVATE,
        trace_id="trace-native-1",
        run_id="run-native-1",
        turn_id="turn-native-1",
        correlation_id="correlation-native-1",
        actor=RuntimeActor(RuntimeActorType.USER, "10001"),
        plans=plans,
        deadline_at=deadline_at,
        governance=governance or RuntimeGovernanceEnvelope(),
    )


def _assistant_response(
    content: str,
    *,
    usage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "choices": [{"message": {"role": "assistant", "content": content}}],
    }
    if usage is not None:
        response["usage"] = dict(usage)
    return response


def _tool_call_response(
    name: str,
    arguments: Mapping[str, Any],
    *,
    call_id: str = "call-native-1",
    usage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(
                                    dict(arguments),
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    ],
                }
            }
        ]
    }
    if usage is not None:
        response["usage"] = dict(usage)
    return response


class _ScriptedCompletionPort:
    def __init__(
        self,
        *,
        responses: tuple[Mapping[str, Any] | BaseException, ...] = (),
        streams: tuple[tuple[Mapping[str, Any] | BaseException, ...], ...] = (),
    ) -> None:
        self._responses = deque(responses)
        self._streams = deque(streams)
        self.complete_requests: list[ChatCompletionRequest] = []
        self.stream_requests: list[ChatCompletionRequest] = []

    @property
    def adapter_id(self) -> str:
        return "completion:scripted"

    async def complete_chat(
        self,
        request: ChatCompletionRequest,
    ) -> Mapping[str, Any]:
        self.complete_requests.append(request)
        if not self._responses:
            raise AssertionError("没有预设非流式模型响应")
        response = self._responses.popleft()
        if isinstance(response, BaseException):
            raise response
        return response

    async def stream_chat(
        self,
        request: ChatCompletionRequest,
    ) -> AsyncIterator[Mapping[str, Any]]:
        self.stream_requests.append(request)
        if not self._streams:
            raise AssertionError("没有预设流式模型响应")
        stream = self._streams.popleft()
        for item in stream:
            if isinstance(item, BaseException):
                raise item
            yield item


class _RouteScriptedCompletionPort(_ScriptedCompletionPort):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.bound_routes: list[object] = []

    def bind_route(self, route: object) -> None:
        self.bound_routes.append(route)


class _BlockingCompletionPort:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    @property
    def adapter_id(self) -> str:
        return "completion:blocking"

    async def complete_chat(
        self,
        request: ChatCompletionRequest,
    ) -> Mapping[str, Any]:
        del request
        self.entered.set()
        await self.release.wait()
        return _assistant_response("不应到达")

    async def stream_chat(
        self,
        request: ChatCompletionRequest,
    ) -> AsyncIterator[Mapping[str, Any]]:
        del request
        self.entered.set()
        await self.release.wait()
        yield {"choices": [{"delta": {"content": "不应到达"}}]}


def _completed_tool_result(
    request: RuntimeToolExecutionRequest,
    output: object,
    *,
    stop: bool = False,
) -> RuntimeToolExecutionResult:
    return RuntimeToolExecutionResult(
        tool_call=RuntimeToolCall(
            call_id=request.tool_call.call_id,
            name=request.tool_call.name,
            arguments=request.arguments,
            status=RuntimeToolCallStatus.COMPLETED,
            result=output,
        ),
        metadata={"stop": stop},
    )


def _runtime(
    completion_port: _ScriptedCompletionPort | _BlockingCompletionPort,
    *,
    plan: ToolPlan | None = None,
    handlers: Mapping[str, Any] | None = None,
    config: NativeAgentRuntimeConfig | None = None,
    artifact_publisher: Any = None,
    plugin_manager: Any = None,
    budget_manager: RuntimeBudgetManager | None = None,
    permission_port: Any = None,
) -> NativeAgentRuntime:
    bindings = dict(handlers or {})
    tool_port = RegisteredToolExecutionPort(bindings)
    return NativeAgentRuntime(
        completion_port,
        tool_port,
        runtime_id="native:test",
        config=config,
        tool_plan_resolver=lambda: plan,
        tool_binding_resolver=lambda name: f"tool.{name}.execute",
        tool_result_artifact_publisher=artifact_publisher,
        available_tool_names=tuple(sorted(plan.executable_tool_names)) if plan else (),
        plugin_manager=plugin_manager,
        budget_manager=budget_manager,
        permission_port=permission_port,
    )


def _governance_with_limits(
    *,
    model_calls: int,
    tokens: int = 100,
    tool_steps: int = 4,
    tool_name: str = "",
) -> RuntimeGovernanceEnvelope:
    access = (
        RuntimeAccessEnvelope((
            RuntimeAccessGrant(
                RuntimeAccessKind.TOOL,
                f"tool:{tool_name}",
                ("execute",),
                "tool_plan",
            ),
        ))
        if tool_name
        else RuntimeAccessEnvelope(())
    )
    return RuntimeGovernanceEnvelope(
        budgets=RuntimeBudgetEnvelope(
            run=RuntimeBudgetLimit(
                RuntimeBudgetScope.RUN,
                model_calls,
                tokens,
                1_000_000,
                model_calls + tool_steps,
                30_000,
                1,
            ),
            turn=RuntimeBudgetLimit(
                RuntimeBudgetScope.TURN,
                model_calls,
                tokens,
                1_000_000,
                model_calls + tool_steps,
                30_000,
                1,
            ),
            tool=RuntimeBudgetLimit(
                RuntimeBudgetScope.TOOL,
                0,
                0,
                0,
                tool_steps,
                10_000,
                1,
            ),
            subagent=RuntimeBudgetLimit(
                RuntimeBudgetScope.SUBAGENT,
                0,
                0,
                0,
                0,
                0,
                0,
            ),
        ),
        access=access,
    )


class _ToolResultArtifactPublisher:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []

    async def publish_tool_result(self, **kwargs: Any) -> RuntimeArtifactRef:
        payload = bytes(kwargs["payload"])
        self.payloads.append(payload)
        return RuntimeArtifactRef(
            artifact_id="art_native_tool_1",
            uri="artifact://art_native_tool_1",
            sha256="b" * 64,
            media_type=str(kwargs["media_type"]),
            size_bytes=len(payload),
            source_run_id="run-native-1",
    )


@pytest.mark.asyncio
async def test_native_runtime_counts_every_physical_model_attempt_against_budget():
    completion = _ScriptedCompletionPort(responses=(
        RuntimeError("首个物理请求失败"),
        _assistant_response("不应执行第二个物理请求"),
    ))
    runtime = _runtime(completion)
    await runtime.start()

    with pytest.raises(AgentRuntimeBudgetExceededError, match="model_call_limit"):
        await runtime.run(AgentTurnRequest(
            _context(governance=_governance_with_limits(model_calls=1)),
            "执行",
        ))

    assert len(completion.complete_requests) == 1
    await runtime.stop()


@pytest.mark.asyncio
async def test_native_runtime_denies_tool_before_real_handler_when_permission_denies():
    plan = _tool_plan("memory_query")
    completion = _ScriptedCompletionPort(responses=(
        _tool_call_response("memory_query", {"query": "秘密"}),
    ))
    handler_called = False

    async def handler(request):
        nonlocal handler_called
        handler_called = True
        return _completed_tool_result(request, {"items": []})

    runtime = _runtime(
        completion,
        plan=plan,
        handlers={"tool.memory_query.execute": handler},
        permission_port=StaticPermissionPort(),
    )
    await runtime.start()

    with pytest.raises(AgentRuntimePermissionError, match="被拒绝"):
        await runtime.run(AgentTurnRequest(
            _context(
                plan,
                governance=_governance_with_limits(
                    model_calls=1,
                    tool_name="memory_query",
                ),
            ),
            "查询",
        ))

    assert handler_called is False
    await runtime.stop()


@pytest.mark.asyncio
async def test_native_runtime_executes_managed_hooks_in_live_model_tool_event_path():
    from core.runtime.extensions import RuntimeFailurePolicy
    from core.runtime.plugin_lifecycle import (
        RuntimeHookPatch,
        RuntimeHookPoint,
        RuntimePluginBinding,
        RuntimePluginDescriptor,
        RuntimePluginHookDescriptor,
        RuntimePluginManager,
    )

    trace: list[str] = []
    diagnostics: list[object] = []
    hook_projections: list[tuple[str, object]] = []

    class ManagedPlugin:
        async def on_load(self, context):
            trace.append(f"load:{context['runtime_id']}")

        async def on_unload(self):
            trace.append("unload")

        async def invoke(self, invocation):
            direction = str(invocation.fields.get("direction", ""))
            trace.append(
                f"hook:{invocation.point.value}:{direction or invocation.hook_id}"
            )
            if invocation.point is RuntimeHookPoint.EVENT:
                hook_projections.append((direction, invocation.fields["event"]))
            if invocation.point is RuntimeHookPoint.COMPLETION:
                hook_projections.append(("completion", invocation.fields["result"]))
            if invocation.point is RuntimeHookPoint.PRE_TOOL:
                return RuntimeHookPatch({"arguments": {"value": 2}})
            if invocation.point is RuntimeHookPoint.POST_TOOL:
                return RuntimeHookPatch({"output": "Hook 已封装"})
            return None

    def hook(
        hook_id: str,
        point: RuntimeHookPoint,
        fields: tuple[str, ...],
        *,
        mutable: tuple[str, ...] = (),
        order: int = 0,
    ) -> RuntimePluginHookDescriptor:
        return RuntimePluginHookDescriptor(
            hook_id=hook_id,
            point=point,
            order=order,
            timeout_seconds=1,
            failure_policy=RuntimeFailurePolicy.FAIL_OPEN,
            readable_fields=fields,
            mutable_fields=mutable,
            trusted_builtin=bool(mutable),
        )

    manager = RuntimePluginManager(
        "native:test",
        (
            RuntimePluginBinding(
                RuntimePluginDescriptor(
                    plugin_id="builtin.integration",
                    version="1.0.0",
                    order=0,
                    required=True,
                    lifecycle_timeout_seconds=1,
                    hooks=(
                        hook(
                            "pre.model",
                            RuntimeHookPoint.PRE_MODEL,
                            ("model", "model_step"),
                        ),
                        hook(
                            "post.model",
                            RuntimeHookPoint.POST_MODEL,
                            ("response", "tool_calls"),
                        ),
                        hook(
                            "pre.tool",
                            RuntimeHookPoint.PRE_TOOL,
                            ("tool_name", "arguments"),
                            mutable=("arguments",),
                        ),
                        hook(
                            "post.tool",
                            RuntimeHookPoint.POST_TOOL,
                            ("tool_name", "output"),
                            mutable=("output",),
                        ),
                        hook(
                            "event",
                            RuntimeHookPoint.EVENT,
                            ("direction", "event"),
                        ),
                        hook(
                            "complete",
                            RuntimeHookPoint.COMPLETION,
                            ("result", "tool_call_count"),
                        ),
                    ),
                ),
                ManagedPlugin(),
            ),
        ),
        diagnostic_emitter=diagnostics.append,
    )
    plan = ToolPlan.from_effective_tools(
        enabled={"echo": True},
        chat_type="private",
        tool_schemas=[{
            "type": "function",
            "function": {
                "name": "echo",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            },
        }],
    )
    completion = _ScriptedCompletionPort(
        responses=(
            _tool_call_response("echo", {"value": 1}),
            _assistant_response("完成"),
        )
    )
    seen_arguments: list[dict[str, object]] = []

    async def execute_echo(request: RuntimeToolExecutionRequest):
        seen_arguments.append(dict(request.arguments))
        return _completed_tool_result(request, "原始结果")

    runtime = _runtime(
        completion,
        plan=plan,
        handlers={"tool.echo.execute": execute_echo},
        plugin_manager=manager,
    )
    await runtime.start()

    async def ledger_handler(event):
        trace.append(f"ledger:{event.kind.value}")

    result = await runtime.run_event(
        AgentTurnRequest(_context(plan), "执行 echo"),
        ledger_handler,
    )
    await runtime.stop()

    assert seen_arguments == [{"value": 2}]
    assert result.tool_calls[0].result == "Hook 已封装"
    assert diagnostics == []
    assert "hook:pre_model:pre.model" in trace
    assert "hook:post_model:post.model" in trace
    assert "hook:pre_tool:pre.tool" in trace
    assert "hook:post_tool:post.tool" in trace
    assert "hook:event:input" in trace
    assert "hook:completion:complete" in trace
    first_ledger = trace.index("ledger:status")
    first_output_hook = trace.index("hook:event:output")
    assert first_ledger < first_output_hook
    input_projection = next(
        projection
        for direction, projection in hook_projections
        if direction == "input"
    )
    assert set(input_projection) == {
        "attribute_keys",
        "content_type",
        "kind",
        "stream",
    }
    assert "context" not in input_projection
    output_projection = next(
        projection
        for direction, projection in hook_projections
        if direction == "output"
    )
    assert "identity" not in output_projection
    assert "text_delta" not in output_projection
    completion_projection = next(
        projection
        for direction, projection in hook_projections
        if direction == "completion"
    )
    assert set(completion_projection) == {
        "message_roles",
        "raw_result_type",
        "tool_calls",
    }
    assert "messages" not in completion_projection
    assert "raw_result" not in completion_projection
    assert trace[-1] == "unload"


@pytest.mark.asyncio
async def test_native_runtime_satisfies_port_and_projects_prompt_route_and_usage():
    completion = _ScriptedCompletionPort(
        responses=(
            _assistant_response(
                "原生完成",
                usage={
                    "prompt_tokens": 12,
                    "completion_tokens": 3,
                    "prompt_tokens_details": {"cached_tokens": 7},
                    "completion_tokens_details": {"reasoning_tokens": 2},
                },
            ),
        )
    )
    runtime = _runtime(completion)
    assert isinstance(runtime, AgentRuntimePort)

    await runtime.start()
    assert runtime.state is RuntimeLifecycleState.RUNNING
    assert runtime.replace_conversation((RuntimeMessage("system", "系统规则"),)) == 1
    runtime.set_model_route(
        RuntimeModelRoute(
            route_id="reply/default",
            model_id="model-native",
            provider_id="new-api",
            temperature=0.2,
            max_tokens=256,
            enable_thinking=False,
        )
    )
    sink = InMemoryRunEventSink()

    result = await runtime.run_event(
        AgentTurnRequest(_context(), "当前消息"),
        sink.append,
    )

    assert [(message.role, message.content) for message in result.messages] == [
        ("system", "系统规则"),
        ("user", "当前消息"),
        ("assistant", "原生完成"),
    ]
    request = completion.complete_requests[0]
    assert [message["role"] for message in request.messages] == [
        "system",
        "user",
    ]
    assert request.manual_model == "model-native"
    assert request.temperature == 0.2
    assert request.max_tokens == 256
    assert request.enable_thinking == "false"
    assert request.trace_id == "trace-native-1"
    assert request.run_id == "run-native-1"
    assert [event.kind for event in sink.events] == [
        RuntimeRunEventKind.STATUS,
        RuntimeRunEventKind.STATUS,
        RuntimeRunEventKind.USAGE,
        RuntimeRunEventKind.END,
    ]
    assert sink.events[2].usage.input_tokens == 12
    assert sink.events[2].usage.cached_input_tokens == 7
    assert sink.events[2].usage.reasoning_tokens == 2
    assert sink.events[-1].status is RuntimeRunStatus.SUCCEEDED
    assert runtime.read_conversation() == result.messages
    assert runtime.clear_pending_events().total == 0
    assert runtime.install_tool_policy().ready is True

    await runtime.stop()

    assert runtime.state is RuntimeLifecycleState.STOPPED


@pytest.mark.asyncio
async def test_native_runtime_runs_bounded_model_tool_loop_through_frozen_binding():
    plan = _tool_plan("memory_query")
    completion = _ScriptedCompletionPort(
        responses=(
            _tool_call_response(
                "memory_query",
                {"query": "原生 Runtime"},
                usage={"prompt_tokens": 10, "completion_tokens": 2},
            ),
            _assistant_response(
                "根据记忆，答案如下",
                usage={"prompt_tokens": 20, "completion_tokens": 4},
            ),
        )
    )
    seen: list[RuntimeToolExecutionRequest] = []

    async def execute_memory(
        request: RuntimeToolExecutionRequest,
    ) -> RuntimeToolExecutionResult:
        seen.append(request)
        return _completed_tool_result(request, {"items": ["命中"]})

    runtime = _runtime(
        completion,
        plan=plan,
        handlers={"tool.memory_query.execute": execute_memory},
    )
    await runtime.start()
    sink = InMemoryRunEventSink()

    result = await runtime.run_event(
        AgentTurnRequest(_context(plan), "查询记忆"),
        sink.append,
    )

    assert len(completion.complete_requests) == 2
    assert completion.complete_requests[0].tools == plan.sent_tool_schemas
    second_messages = completion.complete_requests[1].messages
    assert [message["role"] for message in second_messages] == [
        "user",
        "assistant",
        "tool",
    ]
    assert second_messages[1]["tool_calls"][0]["function"]["name"] == "memory_query"
    assert second_messages[2]["tool_call_id"] == "call-native-1"
    envelope = json.loads(second_messages[2]["content"])
    assert envelope[TOOL_RESULT_ENVELOPE_KEY]["trust"] == "untrusted_data"
    assert unwrap_tool_result_content(
        second_messages[2]["content"]
    ) == '{"items":["命中"]}'
    assert seen[0].execution_port_id == "tool.memory_query.execute"
    assert seen[0].idempotency_key == "request-native-1:call-native-1"
    assert dict(seen[0].arguments) == {"query": "原生 Runtime"}
    assert result.messages[-1].content == "根据记忆，答案如下"
    assert result.tool_calls[0].status is RuntimeToolCallStatus.COMPLETED
    assert runtime.inspect_tool_calls() == result.tool_calls
    assert [event.kind for event in sink.events][-3:] == [
        RuntimeRunEventKind.TOOL_ACTIVITY,
        RuntimeRunEventKind.USAGE,
        RuntimeRunEventKind.END,
    ]
    usage = next(event.usage for event in sink.events if event.usage is not None)
    assert usage.input_tokens == 30
    assert usage.output_tokens == 6


@pytest.mark.asyncio
async def test_native_runtime_stops_after_terminal_reply_tool():
    plan = _tool_plan("reply")
    completion = _ScriptedCompletionPort(
        responses=(_tool_call_response("reply", {"content": "直接回复"}),)
    )

    def execute_reply(
        request: RuntimeToolExecutionRequest,
    ) -> RuntimeToolExecutionResult:
        return _completed_tool_result(request, "直接回复", stop=True)

    runtime = _runtime(
        completion,
        plan=plan,
        handlers={"tool.reply.execute": execute_reply},
    )
    await runtime.start()

    result = await runtime.run(
        AgentTurnRequest(_context(plan), "请回复"),
    )

    assert len(completion.complete_requests) == 1
    assert [message.role for message in result.messages] == [
        "user",
        "assistant",
        "tool",
    ]
    assert result.messages[-1].content == "直接回复"
    assert result.tool_calls[0].name == "reply"
    assert result.tool_calls[0].result == "直接回复"


@pytest.mark.asyncio
async def test_native_runtime_fails_when_terminal_reply_tool_does_not_complete():
    plan = _tool_plan("reply")
    completion = _ScriptedCompletionPort(
        responses=(_tool_call_response("reply", {"content": "无法送达"}),)
    )

    async def timeout_reply(
        request: RuntimeToolExecutionRequest,
    ) -> RuntimeToolExecutionResult:
        del request
        raise TimeoutError("回复超时")

    runtime = _runtime(
        completion,
        plan=plan,
        handlers={"tool.reply.execute": timeout_reply},
    )
    await runtime.start()
    events = []

    with pytest.raises(AgentRuntimeExecutionError, match="最终动作工具执行失败"):
        await runtime.run_event(
            AgentTurnRequest(_context(plan), "请回复"),
            events.append,
        )

    assert [event.kind for event in events][-3:] == [
        RuntimeRunEventKind.TOOL_ACTIVITY,
        RuntimeRunEventKind.ERROR,
        RuntimeRunEventKind.END,
    ]
    assert events[-3].tool_call.status is RuntimeToolCallStatus.TIMED_OUT
    assert events[-1].status is RuntimeRunStatus.FAILED


@pytest.mark.asyncio
async def test_native_runtime_streams_typed_deltas_usage_and_end_event():
    completion = _ScriptedCompletionPort(
        streams=(
            (
                {"choices": [{"delta": {"content": "你"}}]},
                {"choices": [{"delta": {"content": "好"}}]},
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 8,
                        "completion_tokens": 2,
                    },
                },
            ),
        )
    )
    runtime = _runtime(completion)
    await runtime.start()

    events = [
        event
        async for event in runtime.run_stream(
            AgentTurnRequest(_context(), "流式回复"),
        )
    ]

    assert [event.kind for event in events] == [
        RuntimeRunEventKind.STATUS,
        RuntimeRunEventKind.STATUS,
        RuntimeRunEventKind.TEXT_DELTA,
        RuntimeRunEventKind.TEXT_DELTA,
        RuntimeRunEventKind.USAGE,
        RuntimeRunEventKind.END,
    ]
    assert [event.text_delta for event in events if event.text_delta] == ["你", "好"]
    assert events[4].usage.total_tokens == 10
    assert events[-1].status is RuntimeRunStatus.SUCCEEDED
    assert completion.stream_requests[0].messages[-1]["content"] == "流式回复"
    assert runtime.read_conversation()[-1].content == "你好"


@pytest.mark.asyncio
async def test_native_runtime_retries_only_before_irreversible_model_output():
    completion = _ScriptedCompletionPort(
        responses=(
            RuntimeError("临时连接失败"),
            _assistant_response("重试成功"),
        )
    )
    runtime = _runtime(completion)
    await runtime.start()

    result = await runtime.run(AgentTurnRequest(_context(), "请重试"))

    assert result.messages[-1].content == "重试成功"
    assert len(completion.complete_requests) == 2


@pytest.mark.asyncio
async def test_native_runtime_never_retries_run_ledger_authority_failure():
    from core.run_ledger.contracts import RunLedgerAuthorityError

    completion = _ScriptedCompletionPort(
        responses=(
            RunLedgerAuthorityError(
                "model event ledger failed",
                run_id="run-native-1",
                event_type="model.request.started",
            ),
            _assistant_response("不应重试"),
        )
    )
    runtime = _runtime(completion)
    await runtime.start()

    with pytest.raises(
        RunLedgerAuthorityError,
        match="model event ledger failed",
    ):
        await runtime.run(AgentTurnRequest(_context(), "不要重试"))

    assert len(completion.complete_requests) == 1


@pytest.mark.asyncio
async def test_native_runtime_does_not_normalize_tool_ledger_failure():
    from core.run_ledger.contracts import RunLedgerAuthorityError

    plan = _tool_plan("memory_query")
    completion = _ScriptedCompletionPort(
        responses=(
            _tool_call_response("memory_query", {"query": "ledger"}),
        )
    )

    async def fail_authority(_request):
        raise RunLedgerAuthorityError(
            "tool event ledger failed",
            run_id="run-native-1",
            event_type="tool.execute.started",
        )

    runtime = _runtime(
        completion,
        plan=plan,
        handlers={"tool.memory_query.execute": fail_authority},
    )
    await runtime.start()
    events = []

    with pytest.raises(
        RunLedgerAuthorityError,
        match="tool event ledger failed",
    ):
        await runtime.run_event(
            AgentTurnRequest(_context(plan), "查询"),
            events.append,
        )

    assert events[-1].kind is RuntimeRunEventKind.END
    assert events[-1].status is RuntimeRunStatus.FAILED


@pytest.mark.asyncio
async def test_native_runtime_rejects_tool_plan_digest_mismatch_before_model_call():
    plan = _tool_plan("memory_query")
    completion = _ScriptedCompletionPort(responses=(_assistant_response("不应调用"),))
    runtime = _runtime(completion, plan=plan)
    await runtime.start()

    with pytest.raises(AgentRuntimeCapabilityError, match="摘要.*不一致"):
        await runtime.run(
            AgentTurnRequest(
                _context(plan_sha256="f" * 64),
                "摘要不匹配",
            )
        )

    assert completion.complete_requests == []
    assert runtime.read_conversation() == ()


@pytest.mark.asyncio
async def test_native_runtime_rejects_unplanned_tool_call_and_mixed_terminal_calls():
    plan = _tool_plan("memory_query")
    completion = _ScriptedCompletionPort(
        responses=(_tool_call_response("unknown_tool", {}),)
    )
    runtime = _runtime(completion, plan=plan)
    await runtime.start()

    with pytest.raises(AgentRuntimeCapabilityError, match="未授权工具"):
        await runtime.run(AgentTurnRequest(_context(plan), "越权工具"))

    reply_plan = _tool_plan("reply", "memory_query")
    mixed_response = _tool_call_response("reply", {"content": "回复"})
    mixed_response["choices"][0]["message"]["tool_calls"].append(
        _tool_call_response(
            "memory_query",
            {"query": "x"},
            call_id="call-native-2",
        )["choices"][0]["message"]["tool_calls"][0]
    )
    mixed_runtime = _runtime(
        _ScriptedCompletionPort(responses=(mixed_response,)),
        plan=reply_plan,
    )
    await mixed_runtime.start()

    with pytest.raises(AgentRuntimeExecutionError, match="必须单独调用"):
        await mixed_runtime.run(
            AgentTurnRequest(_context(reply_plan), "混合最终动作"),
        )


@pytest.mark.asyncio
async def test_native_runtime_enforces_model_tool_loop_limit():
    plan = _tool_plan("memory_query")
    completion = _ScriptedCompletionPort(
        responses=(
            _tool_call_response(
                "memory_query",
                {"query": "1"},
                call_id="call-loop-1",
            ),
            _tool_call_response(
                "memory_query",
                {"query": "2"},
                call_id="call-loop-2",
            ),
        )
    )

    def execute_memory(
        request: RuntimeToolExecutionRequest,
    ) -> RuntimeToolExecutionResult:
        return _completed_tool_result(request, {"items": []})

    runtime = _runtime(
        completion,
        plan=plan,
        handlers={"tool.memory_query.execute": execute_memory},
        config=NativeAgentRuntimeConfig(
            max_model_steps=2,
            max_tool_rounds=2,
        ),
    )
    await runtime.start()

    with pytest.raises(AgentRuntimeExecutionError, match="最大步数"):
        await runtime.run(AgentTurnRequest(_context(plan), "循环"))

    assert len(completion.complete_requests) == 2
    assert len(runtime.inspect_tool_calls()) == 2


@pytest.mark.asyncio
async def test_native_runtime_timeout_is_normalized_to_typed_terminal_events():
    completion = _BlockingCompletionPort()
    runtime = _runtime(
        completion,
        config=NativeAgentRuntimeConfig(request_timeout_seconds=10),
    )
    await runtime.start()
    runtime.set_model_route(
        RuntimeModelRoute(
            route_id="reply/timeout",
            model_id="model-native",
            provider_id="new-api",
            timeout_seconds=0.02,
        )
    )
    events = []

    with pytest.raises(TimeoutError):
        await runtime.run_event(
            AgentTurnRequest(_context(), "等待超时"),
            events.append,
        )

    assert [event.kind for event in events] == [
        RuntimeRunEventKind.STATUS,
        RuntimeRunEventKind.STATUS,
        RuntimeRunEventKind.ERROR,
        RuntimeRunEventKind.END,
    ]
    assert events[2].error.code == "runtime_timeout"
    assert events[2].error.retryable is True
    assert events[-1].status is RuntimeRunStatus.TIMED_OUT


@pytest.mark.asyncio
async def test_native_runtime_interrupt_cancels_active_turn_and_emits_cancelled_end():
    completion = _BlockingCompletionPort()
    runtime = _runtime(completion)
    await runtime.start()
    events = []

    task = asyncio.create_task(
        runtime.run_event(
            AgentTurnRequest(_context(), "等待取消", stream=True),
            events.append,
        )
    )
    await asyncio.wait_for(completion.entered.wait(), timeout=1)

    assert runtime.interrupt(reason="用户取消") is True
    with pytest.raises(asyncio.CancelledError):
        await task

    assert events[-1].kind is RuntimeRunEventKind.END
    assert events[-1].status is RuntimeRunStatus.CANCELLED
    assert runtime.interrupt(reason="重复取消") is False


@pytest.mark.asyncio
async def test_native_runtime_stop_waits_for_active_turn_cancellation():
    completion = _BlockingCompletionPort()
    runtime = _runtime(completion)
    await runtime.start()

    task = asyncio.create_task(runtime.run(AgentTurnRequest(_context(), "停止运行")))
    await asyncio.wait_for(completion.entered.wait(), timeout=1)

    await runtime.stop()

    assert runtime.state is RuntimeLifecycleState.STOPPED
    assert task.cancelled() is True


@pytest.mark.asyncio
async def test_native_runtime_publishes_large_tool_result_before_context_injection():
    plan = _tool_plan("memory_query")
    completion = _ScriptedCompletionPort(
        responses=(
            _tool_call_response("memory_query", {"query": "大结果"}),
            _assistant_response("已基于 Artifact 摘录处理"),
        )
    )
    publisher = _ToolResultArtifactPublisher()
    raw_output = "检索结果" + "甲" * 400

    async def execute_memory(
        request: RuntimeToolExecutionRequest,
    ) -> RuntimeToolExecutionResult:
        return _completed_tool_result(request, raw_output)

    runtime = _runtime(
        completion,
        plan=plan,
        handlers={"tool.memory_query.execute": execute_memory},
        config=NativeAgentRuntimeConfig(
            context_policy=ContextCompactionPolicy(
                tool_inline_max_bytes=120,
                tool_inline_max_chars=120,
                tool_snippet_head_chars=60,
                tool_snippet_tail_chars=20,
            )
        ),
        artifact_publisher=publisher,
    )
    await runtime.start()
    sink = InMemoryRunEventSink()

    await runtime.run_event(
        AgentTurnRequest(_context(plan), "查询大结果"),
        sink.append,
    )

    assert publisher.payloads == [raw_output.encode("utf-8")]
    tool_message = completion.complete_requests[1].messages[-1]
    envelope = json.loads(tool_message["content"])
    assert envelope[TOOL_RESULT_ENVELOPE_KEY]["truncated"] is True
    assert envelope[TOOL_RESULT_ENVELOPE_KEY]["artifact"]["uri"] == (
        "artifact://art_native_tool_1"
    )
    kinds = [event.kind for event in sink.events]
    assert kinds.index(RuntimeRunEventKind.ARTIFACT) < kinds.index(
        RuntimeRunEventKind.TOOL_ACTIVITY
    )
    await runtime.stop()


@pytest.mark.asyncio
async def test_native_runtime_emits_context_decision_before_compacted_model_request():
    completion = _ScriptedCompletionPort(
        responses=(_assistant_response("压缩后完成"),)
    )
    policy = ContextCompactionPolicy(
        notice_tokens=300,
        snip_tokens=400,
        summary_tokens=500,
        hard_limit_tokens=2_000,
        target_tokens=350,
        recent_units_to_keep=2,
        summary_chars=300,
        snip_message_chars=120,
    )
    runtime = _runtime(
        completion,
        config=NativeAgentRuntimeConfig(context_policy=policy),
    )
    await runtime.start()
    history: list[RuntimeMessage] = []
    for index in range(6):
        history.extend((
            RuntimeMessage("user", f"旧问题{index}" + "甲" * 100),
            RuntimeMessage("assistant", f"旧回答{index}" + "乙" * 100),
        ))
    runtime.replace_conversation(tuple(history))
    sink = InMemoryRunEventSink()

    await runtime.run_event(
        AgentTurnRequest(_context(), "当前请求"),
        sink.append,
    )

    decisions = [
        event.context_decision
        for event in sink.events
        if event.kind is RuntimeRunEventKind.CONTEXT_DECISION
    ]
    assert len(decisions) == 1
    assert decisions[0] is not None
    assert decisions[0].action in {"summary", "hard_limit"}
    assert decisions[0].current_request_retained is True
    assert completion.complete_requests[0].messages[-1]["content"] == "当前请求"
    assert len(completion.complete_requests[0].messages) < len(history) + 1
    await runtime.stop()


@pytest.mark.asyncio
async def test_native_runtime_marks_partial_stream_failure_as_ambiguous_without_retry():
    completion = _ScriptedCompletionPort(
        streams=(
            (
                {"choices": [{"delta": {"content": "部分"}}]},
                RuntimeError("连接中断"),
            ),
            ({"choices": [{"delta": {"content": "不应重试"}}]},),
        )
    )
    runtime = _runtime(completion)
    await runtime.start()
    events = []

    with pytest.raises(AgentRuntimeExecutionError, match="部分输出后中断"):
        await runtime.run_event(
            AgentTurnRequest(_context(), "流式歧义", stream=True),
            events.append,
        )

    assert [event.kind for event in events] == [
        RuntimeRunEventKind.STATUS,
        RuntimeRunEventKind.STATUS,
        RuntimeRunEventKind.TEXT_DELTA,
        RuntimeRunEventKind.ERROR,
        RuntimeRunEventKind.END,
    ]
    assert events[2].text_delta == "部分"
    assert events[3].error.code == "native_stream_ambiguous"
    assert events[-1].status is RuntimeRunStatus.AMBIGUOUS
    assert len(completion.stream_requests) == 1


@pytest.mark.asyncio
async def test_native_runtime_continue_turn_does_not_append_duplicate_user_message():
    completion = _ScriptedCompletionPort(responses=(_assistant_response("继续完成"),))
    runtime = _runtime(completion)
    await runtime.start()
    runtime.replace_conversation(
        (
            RuntimeMessage("system", "规则"),
            RuntimeMessage("user", "原问题"),
            RuntimeMessage("assistant", "处理中"),
        )
    )

    result = await runtime.run(
        AgentTurnRequest(
            _context(),
            "该字段不应写入 conversation",
            kind=RuntimeTurnKind.CONTINUE,
        )
    )

    assert [
        message["content"] for message in completion.complete_requests[0].messages
    ] == [
        "规则",
        "原问题",
        "处理中",
    ]
    assert [message.content for message in result.messages] == [
        "规则",
        "原问题",
        "处理中",
        "继续完成",
    ]


@pytest.mark.asyncio
async def test_native_runtime_completes_bridge_reply_contract_main_path():
    from nanobot_kt.tools.reply import REPLY_MARKER
    from core.agent_runtime import AgentRuntimeKind
    from core.model_provider import ReplyRoutePlan
    from nanobot_kt.bridge import NanobotBridge

    plan = _tool_plan("reply")
    completion = _RouteScriptedCompletionPort(
        responses=(
            _tool_call_response(
                "reply",
                {"content": "Native 主链路回复"},
            ),
        )
    )

    def reply_handler(request: RuntimeToolExecutionRequest):
        return _completed_tool_result(
            request,
            json.dumps(
                {REPLY_MARKER: {"content": "Native 主链路回复"}},
                ensure_ascii=False,
            ),
            stop=True,
        )

    runtime = _runtime(
        completion,
        plan=plan,
        handlers={"tool.reply.execute": reply_handler},
    )
    await runtime.start()
    bridge = NanobotBridge(runtime_kind=AgentRuntimeKind.NATIVE)
    bridge._agent = object()
    bridge._runtime = runtime
    bridge._run_event_sink = InMemoryRunEventSink()
    bridge._native_completion_port = completion
    bridge._record_reply_contract_check = lambda **_kwargs: None
    bridge._log_agent_result = lambda *_args, **_kwargs: None
    route = ReplyRoutePlan(
        provider_id="newapi",
        registry_provider="new-api",
        base_url="http://model.test/v1",
        api_key="secret",
        timeout=30,
        model="model-native",
        capabilities={
            "supports_stream": True,
            "supports_tools": True,
            "supports_image": True,
        },
    )
    try:
        model_loop = await bridge._run_model_loop(
            candidate_models=[{
                "id": "model-native",
                "_route_plan": route,
            }],
            route_plan=route,
            event_content="当前消息",
            query="当前消息",
            session_id="private_native",
            meta={"stream": False},
            tracker=None,
            trace_id="trace-native-bridge",
            run_id="run-native-bridge",
            reply_llm_source="replyer.private_chat",
            runtime_context=_context(plan),
        )
        resolution = await bridge._check_reply_contract(
            session_id="private_native",
            response=model_loop.response,
            result=model_loop.result,
            terminal_output=model_loop.terminal_output,
            target_model=model_loop.target_model,
            query="当前消息",
            meta={
                "stream": False,
                "enable_reply_contract_retry": False,
            },
            event_content="当前消息",
            trace_id="trace-native-bridge",
            run_id="run-native-bridge",
            reply_llm_source="replyer.private_chat",
            runtime_context=_context(plan),
        )
    finally:
        await runtime.stop()

    assert completion.bound_routes == [route]
    assert len(completion.complete_requests) == 1
    assert model_loop.target_model == "model-native"
    assert model_loop.health_status == "success"
    assert resolution.response == "Native 主链路回复"
    assert resolution.finish_status == "success"

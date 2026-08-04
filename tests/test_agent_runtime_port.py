from __future__ import annotations

import ast
import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent_runtime import (
    AgentRuntimeCapabilityError,
    AgentRuntimePort,
    AgentTurnRequest,
    AgentTurnResult,
    ConversationPort,
    FakeAgentRuntime,
    InMemoryRunEventSink,
    RequestRuntimeContext,
    RegisteredToolExecutionPort,
    RunEventSink,
    RuntimeActor,
    RuntimeActorType,
    RuntimeArtifactRef,
    RuntimeAttribute,
    RuntimeChatType,
    RuntimeCapabilities,
    RuntimeCapability,
    RuntimeFeature,
    RuntimeLifecycleState,
    RuntimeMessage,
    RuntimeModelRoute,
    RuntimeOwnerType,
    RuntimePlanKind,
    RuntimePlanRef,
    RuntimePrincipal,
    RuntimeRunError,
    RuntimeRunEvent,
    RuntimeRunEventKind,
    RuntimeRunStatus,
    RuntimeToolCall,
    RuntimeToolCallStatus,
    RuntimeToolExecutionRequest,
    RuntimeToolExecutionResult,
    RuntimeUsage,
    ToolExecutionPort,
    validate_run_status_transition,
)
from tests.async_helpers import run_async


def _request_context() -> RequestRuntimeContext:
    return RequestRuntimeContext(
        request_id="req-1",
        principal=RuntimePrincipal(
            platform="QQ",
            owner_type=RuntimeOwnerType.USER,
            owner_id="10001",
        ),
        session_id="private_10001",
        chat_type=RuntimeChatType.PRIVATE,
        trace_id="trace-1",
        run_id="run-1",
        turn_id="turn-1",
        correlation_id="correlation-1",
        actor=RuntimeActor(RuntimeActorType.USER, "10001"),
        capabilities=frozenset({"tools", "memory"}),
        features=(RuntimeFeature("sandbox", False, "default"),),
        plans=(
            RuntimePlanRef(
                RuntimePlanKind.TOOL,
                "tool-plan:default",
                "a" * 64,
            ),
        ),
        deadline_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )


def test_request_runtime_context_is_immutable_and_has_typed_snapshots():
    context = _request_context()

    assert context.principal.canonical_id == "qq:user:10001"
    assert context.feature_enabled("sandbox") is False
    assert context.feature_enabled("unknown", default=True) is True
    assert context.plan(RuntimePlanKind.TOOL).sha256 == "a" * 64
    identity = context.execution_identity()
    assert identity.run_id == "run-1"
    assert identity.turn_id == "turn-1"
    assert identity.correlation_id == "correlation-1"
    assert identity.actor.actor_id == "10001"
    assert identity.owner is context.principal
    with pytest.raises(FrozenInstanceError):
        context.session_id = "other"  # type: ignore[misc]


def test_request_runtime_context_rejects_duplicate_policy_sources():
    principal = RuntimePrincipal("qq", RuntimeOwnerType.USER, "1")
    with pytest.raises(ValueError, match="features"):
        RequestRuntimeContext(
            request_id="req",
            principal=principal,
            session_id="s",
            chat_type=RuntimeChatType.PRIVATE,
            features=(
                RuntimeFeature("same", True),
                RuntimeFeature("same", False),
            ),
        )
    with pytest.raises(ValueError, match="plans"):
        RequestRuntimeContext(
            request_id="req",
            principal=principal,
            session_id="s",
            chat_type=RuntimeChatType.PRIVATE,
            plans=(
                RuntimePlanRef(RuntimePlanKind.TOOL, "one", "a" * 64),
                RuntimePlanRef(RuntimePlanKind.TOOL, "two", "b" * 64),
            ),
        )


def test_runtime_capabilities_are_explicit_and_report_missing_features():
    descriptor = RuntimeCapabilities(
        runtime_id="runtime:minimal",
        supported=frozenset(
            {
                RuntimeCapability.RUN,
                RuntimeCapability.CONVERSATION,
            }
        ),
    )

    assert descriptor.supports(
        RuntimeCapability.RUN,
        RuntimeCapability.CONVERSATION,
    )
    assert descriptor.supports(RuntimeCapability.RUN_STREAM) is False
    assert descriptor.missing(
        frozenset(
            {
                RuntimeCapability.RUN,
                RuntimeCapability.RUN_STREAM,
                RuntimeCapability.INTERRUPT,
            }
        )
    ) == (
        RuntimeCapability.INTERRUPT,
        RuntimeCapability.RUN_STREAM,
    )


def test_runtime_run_status_transitions_are_explicit_and_terminal():
    assert (
        validate_run_status_transition(
            RuntimeRunStatus.ACCEPTED,
            RuntimeRunStatus.RUNNING,
        )
        is RuntimeRunStatus.RUNNING
    )
    assert (
        validate_run_status_transition(
            RuntimeRunStatus.RUNNING,
            RuntimeRunStatus.WAITING_APPROVAL,
        )
        is RuntimeRunStatus.WAITING_APPROVAL
    )
    assert (
        validate_run_status_transition(
            RuntimeRunStatus.WAITING_APPROVAL,
            RuntimeRunStatus.RUNNING,
        )
        is RuntimeRunStatus.RUNNING
    )
    for terminal in (
        RuntimeRunStatus.CANCELLED,
        RuntimeRunStatus.TIMED_OUT,
        RuntimeRunStatus.SUCCEEDED,
        RuntimeRunStatus.FAILED,
        RuntimeRunStatus.AMBIGUOUS,
    ):
        assert terminal.is_terminal is True
        assert (
            validate_run_status_transition(
                RuntimeRunStatus.RUNNING,
                terminal,
            )
            is terminal
        )
        with pytest.raises(ValueError, match="不允许"):
            validate_run_status_transition(terminal, RuntimeRunStatus.RUNNING)
    with pytest.raises(ValueError, match="不允许"):
        validate_run_status_transition(
            RuntimeRunStatus.ACCEPTED,
            RuntimeRunStatus.SUCCEEDED,
        )


def test_runtime_run_events_cover_typed_stream_payloads_and_identity():
    identity = _request_context().execution_identity()
    occurred_at = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
    tool_call = RuntimeToolCall(
        call_id="call-1",
        name="web_search",
        arguments={"query": "Nanobot"},
    )
    usage = RuntimeUsage(
        input_tokens=120,
        output_tokens=30,
        cached_input_tokens=80,
        reasoning_tokens=5,
        cost_microunits=42,
    )
    artifact = RuntimeArtifactRef(
        artifact_id="artifact-1",
        uri="asset://sha256/baseline",
        sha256="b" * 64,
        media_type="text/plain",
        size_bytes=12,
    )
    error = RuntimeRunError(
        code="provider_timeout",
        message="模型请求超时",
        retryable=True,
    )
    events = (
        RuntimeRunEvent(
            "event-1",
            identity,
            1,
            RuntimeRunEventKind.STATUS,
            RuntimeRunStatus.ACCEPTED,
            occurred_at,
        ),
        RuntimeRunEvent(
            "event-2",
            identity,
            2,
            RuntimeRunEventKind.TEXT_DELTA,
            RuntimeRunStatus.RUNNING,
            occurred_at,
            text_delta="增量",
        ),
        RuntimeRunEvent(
            "event-3",
            identity,
            3,
            RuntimeRunEventKind.TOOL_ACTIVITY,
            RuntimeRunStatus.RUNNING,
            occurred_at,
            tool_call=tool_call,
        ),
        RuntimeRunEvent(
            "event-4",
            identity,
            4,
            RuntimeRunEventKind.USAGE,
            RuntimeRunStatus.RUNNING,
            occurred_at,
            usage=usage,
        ),
        RuntimeRunEvent(
            "event-5",
            identity,
            5,
            RuntimeRunEventKind.ARTIFACT,
            RuntimeRunStatus.RUNNING,
            occurred_at,
            artifact=artifact,
        ),
        RuntimeRunEvent(
            "event-6",
            identity,
            6,
            RuntimeRunEventKind.ERROR,
            RuntimeRunStatus.RUNNING,
            occurred_at,
            error=error,
        ),
        RuntimeRunEvent(
            "event-7",
            identity,
            7,
            RuntimeRunEventKind.END,
            RuntimeRunStatus.SUCCEEDED,
            occurred_at,
            attributes=(RuntimeAttribute("finish_reason", "stop"),),
        ),
    )

    assert [event.kind.value for event in events] == [
        "status",
        "text_delta",
        "tool_activity",
        "usage",
        "artifact",
        "error",
        "end",
    ]
    assert events[1].run_id == "run-1"
    assert events[1].turn_id == "turn-1"
    assert events[1].correlation_id == "correlation-1"
    assert events[1].actor.actor_id == "10001"
    assert events[1].owner.canonical_id == "qq:user:10001"
    assert usage.total_tokens == 150


def test_runtime_run_event_rejects_ambiguous_payload_and_terminal_shape():
    identity = _request_context().execution_identity()
    occurred_at = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="缺少对应 payload"):
        RuntimeRunEvent(
            "event-text-empty",
            identity,
            1,
            RuntimeRunEventKind.TEXT_DELTA,
            RuntimeRunStatus.RUNNING,
            occurred_at,
        )
    with pytest.raises(ValueError, match="不能携带"):
        RuntimeRunEvent(
            "event-wrong-payload",
            identity,
            1,
            RuntimeRunEventKind.STATUS,
            RuntimeRunStatus.RUNNING,
            occurred_at,
            text_delta="不应出现",
        )
    with pytest.raises(ValueError, match="end 事件必须携带终态"):
        RuntimeRunEvent(
            "event-nonterminal-end",
            identity,
            1,
            RuntimeRunEventKind.END,
            RuntimeRunStatus.RUNNING,
            occurred_at,
        )
    with pytest.raises(ValueError, match="终态只能由 end"):
        RuntimeRunEvent(
            "event-terminal-status",
            identity,
            1,
            RuntimeRunEventKind.STATUS,
            RuntimeRunStatus.FAILED,
            occurred_at,
        )
    with pytest.raises(ValueError, match="包含时区"):
        RuntimeRunEvent(
            "event-naive-time",
            identity,
            1,
            RuntimeRunEventKind.STATUS,
            RuntimeRunStatus.RUNNING,
            datetime(2026, 8, 3, 8, 0),
        )
    with pytest.raises(ValueError, match="非负整数"):
        RuntimeUsage(input_tokens=-1)


def test_registered_tool_execution_port_dispatches_frozen_binding():
    request = RuntimeToolExecutionRequest(
        context=_request_context(),
        tool_call=RuntimeToolCall(
            call_id="call-deterministic",
            name="memory_query",
            arguments={"query": "运行时合同"},
        ),
        execution_port_id="tool.memory_query.execute",
        idempotency_key="idem-memory-query",
        timeout_seconds=2,
    )
    seen = []

    async def execute_tool(current):
        seen.append(current)
        return RuntimeToolExecutionResult(
            tool_call=RuntimeToolCall(
                call_id=current.tool_call.call_id,
                name=current.tool_call.name,
                arguments=current.arguments,
                status=RuntimeToolCallStatus.COMPLETED,
                result={"items": ["命中"]},
            ),
            metadata={"structured_content": {"items": ["命中"]}},
        )

    port = RegisteredToolExecutionPort(
        {
            "tool.memory_query.execute": execute_tool,
        }
    )

    assert isinstance(port, ToolExecutionPort)
    result = run_async(port.execute(request))
    assert result.success is True
    assert result.output == {"items": ["命中"]}
    assert result.tool_call_id == "call-deterministic"
    assert seen == [request]
    with pytest.raises(TypeError):
        request.arguments["query"] = "修改"  # type: ignore[index]
    with pytest.raises(TypeError):
        result.metadata["new"] = True  # type: ignore[index]


def test_registered_tool_execution_port_fails_closed_and_enforces_timeout():
    request = RuntimeToolExecutionRequest(
        context=_request_context(),
        tool_call=RuntimeToolCall(
            call_id="call-timeout",
            name="slow_tool",
            arguments={},
        ),
        execution_port_id="tool.slow.execute",
        idempotency_key="idem-slow",
        timeout_seconds=0.01,
    )

    async def slow_tool(_request):
        await asyncio.sleep(1)
        raise AssertionError("超时后不应继续")

    missing_port = RegisteredToolExecutionPort({})
    with pytest.raises(
        AgentRuntimeCapabilityError,
        match="execution binding 未注册",
    ):
        run_async(missing_port.execute(request))

    port = RegisteredToolExecutionPort({"tool.slow.execute": slow_tool})
    with pytest.raises(TimeoutError):
        run_async(port.execute(request))


def test_kt_tool_execution_adapter_uses_public_tool_without_kt_executor(monkeypatch):
    from nanobot_kt.tool_execution_adapter import (
        KtRegisteredToolExecutionAdapter,
    )

    class PublicTool:
        async def execute(self, args, context=None):
            assert args == {"query": "合同"}
            assert context is None
            return SimpleNamespace(
                output='{"items":["结果"]}',
                error=None,
                exit_code=0,
                metadata={"structured_content": {"items": ["结果"]}},
            )

    class ForbiddenExecutor:
        def __getattribute__(self, name):
            raise AssertionError(f"不应访问 KT executor.{name}")

    agent = SimpleNamespace(
        registry=SimpleNamespace(
            get_tool=lambda name: PublicTool() if name == "memory_query" else None,
        ),
        executor=ForbiddenExecutor(),
    )
    finished = []
    reset_tokens = []
    monkeypatch.setattr(
        "nanobot_kt.tool_execution_adapter.begin_tool_trace",
        lambda name, args, *, tool_call_id="": (tool_call_id, 1.0),
    )
    monkeypatch.setattr(
        "nanobot_kt.tool_execution_adapter.finish_tool_trace",
        lambda *args, **kwargs: finished.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "nanobot_kt.tool_execution_adapter.set_tool_trace_context",
        lambda tool_call_id: f"token:{tool_call_id}",
    )
    monkeypatch.setattr(
        "nanobot_kt.tool_execution_adapter.reset_tool_trace_context",
        reset_tokens.append,
    )
    request = RuntimeToolExecutionRequest(
        context=_request_context(),
        tool_call=RuntimeToolCall(
            call_id="call-public-tool",
            name="memory_query",
            arguments={"query": "合同"},
        ),
        execution_port_id="tool.memory_query.execute",
        idempotency_key="idem-public-tool",
        timeout_seconds=1,
    )
    port = KtRegisteredToolExecutionAdapter(agent)

    assert isinstance(port, ToolExecutionPort)
    result = run_async(port.execute(request))

    assert result.success is True
    assert result.tool_call_id == "call-public-tool"
    assert result.output == '{"items":["结果"]}'
    assert result.metadata["structured_content"] == {"items": ["结果"]}
    assert finished[-1][1]["status"] == "success"
    assert reset_tokens == ["token:call-public-tool"]


def test_direct_tool_execution_source_has_no_kt_executor_job_dependency():
    source = (
        Path(__file__).parents[1] / "nanobot_kt" / "direct_tool_execution.py"
    ).read_text(encoding="utf-8")

    assert "agent.executor" not in source
    assert ".submit(" not in source
    assert ".wait_for(" not in source


def test_core_agent_runtime_contract_has_no_framework_imports():
    package_dir = Path(__file__).parents[1] / "core" / "agent_runtime"
    forbidden_roots = {"fastapi", "sqlalchemy", "kohakuterrarium", "nanobot_kt"}
    imported_roots: set[str] = set()
    for source_path in package_dir.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

    assert imported_roots.isdisjoint(forbidden_roots)


def test_fake_agent_runtime_satisfies_contract_and_records_lifecycle():
    runtime = FakeAgentRuntime(runtime_id="fake:test")
    assert isinstance(runtime, AgentRuntimePort)
    assert isinstance(runtime, ConversationPort)
    assert runtime.runtime_capabilities.runtime_id == "fake:test"
    assert runtime.runtime_capabilities.supports(
        RuntimeCapability.RUN,
        RuntimeCapability.RUN_STREAM,
        RuntimeCapability.RUN_EVENT,
    )

    run_async(runtime.start())
    runtime.replace_conversation((RuntimeMessage("system", "规则"),))
    expected = AgentTurnResult(
        raw_result="ok",
        messages=(
            RuntimeMessage("system", "规则"),
            RuntimeMessage("assistant", "完成"),
        ),
    )
    runtime.queue_result(expected)
    result = run_async(
        runtime.execute_turn(AgentTurnRequest(_request_context(), "你好"))
    )

    assert result == expected
    assert runtime.requests[0].context.request_id == "req-1"
    assert [event.current_state for event in runtime.lifecycle_events] == [
        RuntimeLifecycleState.STARTING,
        RuntimeLifecycleState.RUNNING,
    ]

    run_async(runtime.stop())
    assert runtime.state is RuntimeLifecycleState.STOPPED
    assert runtime.lifecycle_events[-1].current_state is RuntimeLifecycleState.STOPPED


def test_fake_agent_runtime_exposes_run_event_and_run_stream_interfaces():
    runtime = FakeAgentRuntime(runtime_id="fake:events")
    run_async(runtime.start())
    expected = AgentTurnResult(
        raw_result="ok",
        messages=(RuntimeMessage("assistant", "你好"),),
        tool_calls=(
            RuntimeToolCall(
                call_id="call-1",
                name="reply",
                arguments={"content": "你好"},
                status=RuntimeToolCallStatus.COMPLETED,
                result="你好",
            ),
        ),
    )
    runtime.queue_result(expected)
    runtime.queue_text_deltas("你", "好")
    sink = InMemoryRunEventSink()
    assert isinstance(sink, RunEventSink)

    result = run_async(
        runtime.run_event(
            AgentTurnRequest(_request_context(), "你好", stream=True),
            sink.append,
        )
    )

    assert result == expected
    callback_events = sink.events
    assert [event.kind for event in callback_events] == [
        RuntimeRunEventKind.STATUS,
        RuntimeRunEventKind.STATUS,
        RuntimeRunEventKind.TEXT_DELTA,
        RuntimeRunEventKind.TEXT_DELTA,
        RuntimeRunEventKind.TOOL_ACTIVITY,
        RuntimeRunEventKind.END,
    ]
    assert [event.sequence for event in callback_events] == list(range(1, 7))
    assert callback_events[-1].status is RuntimeRunStatus.SUCCEEDED
    with pytest.raises(ValueError, match="重复写入"):
        run_async(sink.append(callback_events[0]))

    runtime.queue_result(
        AgentTurnResult(
            raw_result="stream-ok",
            messages=(RuntimeMessage("assistant", "流式"),),
        )
    )
    runtime.queue_text_deltas("流", "式")

    async def collect_stream():
        return [
            event
            async for event in runtime.run_stream(
                AgentTurnRequest(_request_context(), "继续")
            )
        ]

    stream_events = run_async(collect_stream())

    assert [event.text_delta for event in stream_events if event.text_delta] == [
        "流",
        "式",
    ]
    assert stream_events[-1].kind is RuntimeRunEventKind.END
    assert runtime.requests[-1].stream is True


class _Conversation:
    def __init__(self) -> None:
        self.messages: list[object] = []

    def clear(self, keep_system: bool = True) -> None:
        if keep_system:
            self.messages = [
                message
                for message in self.messages
                if getattr(message, "role", "") == "system"
            ]
        else:
            self.messages = []

    def append(self, role: str, content: object, **kwargs: object) -> object:
        message = SimpleNamespace(role=role, content=content, **kwargs)
        self.messages.append(message)
        return message

    def get_messages(self) -> list[object]:
        return list(self.messages)


class _PluginManager:
    def __init__(self) -> None:
        self._plugins: list[object] = []

    def register(self, plugin: object) -> None:
        self._plugins.append(plugin)

    def get_plugin(self, name: str) -> object | None:
        return next(
            (
                plugin
                for plugin in self._plugins
                if getattr(plugin, "name", None) == name
            ),
            None,
        )


class _KtAgent:
    def __init__(self) -> None:
        self._running = False
        self._interrupt_requested = False
        self.interrupt_count = 0
        self.events: list[object] = []
        self.runtime_contexts: list[dict[str, object]] = []
        self.plugins = _PluginManager()
        self.config = SimpleNamespace(name="test-creature")
        self.controller = SimpleNamespace(
            conversation=_Conversation(),
            llm=SimpleNamespace(
                config=SimpleNamespace(
                    model="old-model",
                    temperature=0.0,
                    max_tokens=64,
                ),
                provider_name="old-provider",
            ),
            _pending_events=[],
            _event_queue=asyncio.Queue(),
            _pending_injections=[],
            _get_native_tool_schemas=lambda: [],
        )

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def inject_event(self, event: object) -> str:
        from core.agent_runtime.request_scope import (
            require_current_runtime_context,
        )

        self.events.append(event)
        self.runtime_contexts.append(require_current_runtime_context())
        self.controller.conversation.append("user", getattr(event, "content", ""))
        self.controller.conversation.append("assistant", "KT 完成")
        return "raw-ok"

    def interrupt(self) -> None:
        self.interrupt_count += 1
        self._interrupt_requested = True


def test_native_and_kt_runtimes_share_minimal_port_contract():
    """同一组生命周期、会话和执行断言同时约束两个真实 Runtime。"""

    from core.agent_runtime import NativeAgentRuntime
    from nanobot_kt.runtime_adapter import build_kt_runtime

    class ContractCompletionPort:
        @property
        def adapter_id(self):
            return "completion:contract"

        async def complete_chat(self, request):
            assert [message["role"] for message in request.messages] == [
                "system",
                "user",
            ]
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Native 完成",
                        }
                    }
                ]
            }

        async def stream_chat(self, request):
            del request
            if False:
                yield {}

    async def assert_contract(runtime, expected_assistant):
        assert isinstance(runtime, AgentRuntimePort)
        assert isinstance(runtime, ConversationPort)
        assert runtime.state is RuntimeLifecycleState.NEW
        assert runtime.runtime_capabilities.supports(
            RuntimeCapability.RUN,
            RuntimeCapability.RUN_STREAM,
            RuntimeCapability.RUN_EVENT,
            RuntimeCapability.CONVERSATION,
            RuntimeCapability.MODEL_ROUTE,
            RuntimeCapability.TOOL_POLICY,
            RuntimeCapability.TOOL_INSPECTION,
            RuntimeCapability.INTERRUPT,
        )
        assert runtime.runtime_capabilities.supports(
            RuntimeCapability.CHECKPOINT_RECOVERY
        ) is False
        assert runtime.install_tool_policy().ready is True

        await runtime.start()
        assert runtime.state is RuntimeLifecycleState.RUNNING
        assert (
            runtime.replace_conversation((RuntimeMessage("system", "合同规则"),)) == 1
        )
        assert runtime.clear_pending_events().total == 0

        context = replace(_request_context(), plans=(), deadline_at=None)
        result = await runtime.run(AgentTurnRequest(context, "合同消息"))

        assert [(message.role, message.content) for message in result.messages] == [
            ("system", "合同规则"),
            ("user", "合同消息"),
            ("assistant", expected_assistant),
        ]
        assert runtime.read_conversation() == result.messages
        assert isinstance(runtime.inspect_tool_calls(), tuple)
        assert isinstance(runtime.list_tool_names(), tuple)

        await runtime.stop()
        assert runtime.state is RuntimeLifecycleState.STOPPED
        assert (
            runtime.lifecycle_events[-1].current_state is RuntimeLifecycleState.STOPPED
        )

    native = NativeAgentRuntime(
        ContractCompletionPort(),
        RegisteredToolExecutionPort({}),
        tool_plan_resolver=lambda: None,
        available_tool_names=(),
    )
    kt = build_kt_runtime(_KtAgent())

    async def run_contracts():
        await assert_contract(native, "Native 完成")
        await assert_contract(kt, "KT 完成")

    run_async(run_contracts())


def test_kt_runtime_adapter_wraps_turn_conversation_route_and_interrupt():
    from nanobot_kt.runtime_adapter import build_kt_runtime

    agent = _KtAgent()
    runtime = build_kt_runtime(agent)
    assert isinstance(runtime, AgentRuntimePort)
    assert isinstance(runtime, ConversationPort)
    assert runtime.runtime_capabilities.supports(
        RuntimeCapability.RUN,
        RuntimeCapability.RUN_STREAM,
        RuntimeCapability.RUN_EVENT,
    )
    assert runtime.runtime_id == "kt:test-creature"

    run_async(runtime.start())
    assert runtime.state is RuntimeLifecycleState.RUNNING
    assert runtime.replace_conversation((RuntimeMessage("system", "系统规则"),)) == 1

    result = run_async(
        runtime.run(
            AgentTurnRequest(
                _request_context(),
                "当前消息",
                stream=True,
                event_attributes=(RuntimeAttribute("source", "contract-test"),),
            )
        )
    )

    assert result.raw_result == "raw-ok"
    assert [(message.role, message.content) for message in result.messages] == [
        ("system", "系统规则"),
        ("user", "当前消息"),
        ("assistant", "KT 完成"),
    ]
    assert getattr(agent.events[0], "context")["stream"] is True
    assert getattr(agent.events[0], "context")["source"] == "contract-test"
    assert agent.runtime_contexts == [
        {
            "chat_type": "private",
            "runtime_chat_type": "private",
            "is_group": False,
            "is_super_user": False,
            "session_id": "private_10001",
            "group_id": "",
            "user_id": "10001",
            "platform": "qq",
            "sender_name": "",
            "trace_id": "trace-1",
            "run_id": "run-1",
            "turn_id": "turn-1",
            "correlation_id": "correlation-1",
            "actor_type": "user",
            "actor_id": "10001",
            "actor_parent_id": "",
            "owner_type": "user",
            "owner_id": "10001",
            "message_id": "",
        }
    ]
    from core.agent_runtime.request_scope import get_current_runtime_context

    assert get_current_runtime_context() is None

    route = RuntimeModelRoute(
        route_id="reply/default",
        model_id="new-model",
        provider_id="new-api",
        temperature=0.3,
        max_tokens=256,
    )
    runtime.set_model_route(route)
    assert agent.controller.llm.config.model == "new-model"
    assert agent.controller.llm.config.temperature == 0.3
    assert agent.controller.llm.config.max_tokens == 256
    assert agent.controller.llm.provider_name == "new-api"

    assert runtime.interrupt(reason="用户取消") is True
    assert agent.interrupt_count == 1

    run_async(runtime.stop())
    assert runtime.state is RuntimeLifecycleState.STOPPED


@pytest.mark.asyncio
@pytest.mark.parametrize("already_started", (False, True))
async def test_kt_runtime_adapter_uses_managed_hooks_on_public_plugin_path(
    already_started: bool,
):
    from core.runtime.extensions import RuntimeFailurePolicy
    from core.runtime.plugin_lifecycle import (
        RuntimeHookPatch,
        RuntimeHookPoint,
        RuntimePluginBinding,
        RuntimePluginDescriptor,
        RuntimePluginHookDescriptor,
        RuntimePluginManager,
    )
    from nanobot_kt.runtime_adapter import build_kt_runtime

    trace: list[str] = []

    class Plugin:
        async def on_load(self, context):
            trace.append(f"load:{context['runtime_id']}")

        async def on_unload(self):
            trace.append("unload")

        async def invoke(self, invocation):
            direction = str(invocation.fields.get("direction", ""))
            trace.append(
                f"hook:{invocation.point.value}:{direction or invocation.hook_id}"
            )
            if invocation.point is RuntimeHookPoint.PRE_TOOL:
                return RuntimeHookPatch({"arguments": {"value": 2}})
            if invocation.point is RuntimeHookPoint.POST_TOOL:
                return RuntimeHookPatch({"output": "KT Hook 已封装"})
            return None

    def hook(
        hook_id: str,
        point: RuntimeHookPoint,
        fields: tuple[str, ...],
        *,
        mutable: tuple[str, ...] = (),
    ) -> RuntimePluginHookDescriptor:
        return RuntimePluginHookDescriptor(
            hook_id=hook_id,
            point=point,
            order=0,
            timeout_seconds=1,
            failure_policy=RuntimeFailurePolicy.FAIL_OPEN,
            readable_fields=fields,
            mutable_fields=mutable,
            trusted_builtin=bool(mutable),
        )

    manager = RuntimePluginManager(
        "kt:test-creature",
        (
            RuntimePluginBinding(
                RuntimePluginDescriptor(
                    plugin_id="builtin.kt-integration",
                    version="1.0.0",
                    order=0,
                    required=True,
                    lifecycle_timeout_seconds=1,
                    hooks=(
                        hook(
                            "pre.model",
                            RuntimeHookPoint.PRE_MODEL,
                            ("model", "messages"),
                        ),
                        hook(
                            "post.model",
                            RuntimeHookPoint.POST_MODEL,
                            ("model", "response"),
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
                            "interrupt",
                            RuntimeHookPoint.INTERRUPT,
                            ("reason",),
                        ),
                        hook(
                            "complete",
                            RuntimeHookPoint.COMPLETION,
                            ("result", "message_count"),
                        ),
                    ),
                ),
                Plugin(),
            ),
        ),
        diagnostic_emitter=lambda diagnostic: None,
    )
    agent = _KtAgent()
    agent._running = already_started
    transformed: dict[str, object] = {}

    async def inject_event(event: object) -> str:
        managed = agent.plugins.get_plugin("nanobot_managed_runtime_plugins")
        assert managed is not None
        messages = [{"role": "user", "content": getattr(event, "content", "")}]
        await managed.pre_llm_call(messages, model="kt-model", tools=[])
        args = await managed.pre_tool_execute(
            {"value": 1},
            tool_name="echo",
            job_id="call-kt-1",
        )
        transformed["arguments"] = args
        transformed["output"] = await managed.post_tool_execute(
            "原始结果",
            tool_name="echo",
            job_id="call-kt-1",
            args=args,
        )
        await managed.post_llm_call(
            messages,
            "KT 完成",
            {},
            model="kt-model",
        )
        agent.controller.conversation.append(
            "user",
            getattr(event, "content", ""),
        )
        agent.controller.conversation.append("assistant", "KT 完成")
        return "raw-managed"

    agent.inject_event = inject_event
    runtime = build_kt_runtime(agent, plugin_manager=manager)
    if not already_started:
        await runtime.start()

    async def ledger_handler(event):
        trace.append(f"ledger:{event.kind.value}")

    result = await runtime.run_event(
        AgentTurnRequest(_request_context(), "执行 KT Hook"),
        ledger_handler,
    )
    runtime.interrupt(reason="用户取消")
    await manager.drain_background_tasks()
    await runtime.stop()

    assert result.raw_result == "raw-managed"
    assert transformed == {
        "arguments": {"value": 2},
        "output": "KT Hook 已封装",
    }
    assert "hook:pre_model:pre.model" in trace
    assert "hook:post_model:post.model" in trace
    assert "hook:pre_tool:pre.tool" in trace
    assert "hook:post_tool:post.tool" in trace
    assert "hook:event:input" in trace
    assert "hook:completion:complete" in trace
    assert "hook:interrupt:interrupt" in trace
    assert trace.index("ledger:status") < trace.index("hook:event:output")
    assert trace[-1] == "unload"


def test_kt_runtime_adapter_run_stream_forwards_real_output_and_usage():
    from nanobot_kt.output import BufferedOutput
    from nanobot_kt.runtime_adapter import build_kt_runtime

    agent = _KtAgent()
    agent._running = True
    output = BufferedOutput()
    agent.controller.llm.last_usage = {
        "prompt_tokens": 8,
        "completion_tokens": 2,
        "cached_tokens": 3,
    }

    async def process_event(event):
        agent.events.append(event)
        agent.controller.conversation.append("user", getattr(event, "content", ""))
        await output.write_stream("你")
        await output.write_stream("好")
        agent.controller.conversation.append(
            "assistant",
            "",
            tool_calls=[
                {
                    "id": "call-reply",
                    "type": "function",
                    "function": {"name": "reply", "arguments": "{}"},
                }
            ],
        )
        agent.controller.conversation.append(
            "tool",
            "你好",
            tool_call_id="call-reply",
            name="reply",
        )
        return "raw-stream-ok"

    agent.inject_event = process_event
    runtime = build_kt_runtime(agent, output_sink=output)

    async def collect_stream():
        return [
            event
            async for event in runtime.run_stream(
                AgentTurnRequest(_request_context(), "当前消息")
            )
        ]

    events = run_async(collect_stream())

    assert [event.kind for event in events] == [
        RuntimeRunEventKind.STATUS,
        RuntimeRunEventKind.STATUS,
        RuntimeRunEventKind.TEXT_DELTA,
        RuntimeRunEventKind.TEXT_DELTA,
        RuntimeRunEventKind.TOOL_ACTIVITY,
        RuntimeRunEventKind.USAGE,
        RuntimeRunEventKind.END,
    ]
    assert [event.text_delta for event in events if event.text_delta] == ["你", "好"]
    assert events[4].tool_call.call_id == "call-reply"
    assert events[4].tool_call.status is RuntimeToolCallStatus.COMPLETED
    assert events[5].usage.input_tokens == 8
    assert events[5].usage.output_tokens == 2
    assert events[5].usage.cached_input_tokens == 3
    assert events[-1].status is RuntimeRunStatus.SUCCEEDED
    assert [event.sequence for event in events] == list(range(1, 8))
    assert getattr(agent.events[0], "context")["stream"] is True


def test_kt_runtime_adapter_uses_public_tools_without_touching_pending_state():
    from nanobot_kt.runtime_adapter import build_kt_runtime

    agent = _KtAgent()
    agent._running = True
    runtime = build_kt_runtime(agent)

    status = runtime.install_tool_policy()
    assert status.ready is True
    assert status.guard_installed is True
    assert status.schema_filter_installed is True

    reset = runtime.clear_pending_events()
    assert reset.pending_events == 0
    assert reset.queued_events == 0
    assert reset.pending_injections == 0
    assert reset.total == 0

    requested = RuntimeToolCall(
        call_id="call-1",
        name="reply",
        arguments='{"content":"ok"}',
    )
    runtime.replace_conversation(
        (
            RuntimeMessage("assistant", "", tool_calls=(requested,)),
            RuntimeMessage("tool", "done", name="reply", tool_call_id="call-1"),
        )
    )
    calls = runtime.inspect_tool_calls()
    assert len(calls) == 1
    assert calls[0].status is RuntimeToolCallStatus.COMPLETED
    assert calls[0].result == "done"

    agent.registry = SimpleNamespace(
        list_tools=lambda: ["reply", {"name": "memory_query"}],
    )
    assert runtime.list_tool_names() == ("memory_query", "reply")


def test_kt_composition_factory_accepts_explicit_provider_route_applier():
    from nanobot_kt.runtime_adapter import build_kt_runtime

    agent = _KtAgent()
    agent._running = True
    applied: list[tuple[object, RuntimeModelRoute]] = []

    def apply_route(target: object, route: RuntimeModelRoute) -> None:
        applied.append((target, route))

    runtime = build_kt_runtime(
        agent,
        runtime_id="kt:explicit",
        route_applier=apply_route,
    )
    route = RuntimeModelRoute(
        route_id="reply/remote",
        model_id="provider-model",
        provider_id="provider",
        timeout_seconds=150,
        enable_thinking=False,
    )

    runtime.set_model_route(route)

    assert applied == [(agent, route)]
    assert runtime.runtime_id == "kt:explicit"

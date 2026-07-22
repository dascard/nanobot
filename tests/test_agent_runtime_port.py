from __future__ import annotations

import ast
import asyncio
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent_runtime import (
    AgentRuntimePort,
    AgentTurnRequest,
    AgentTurnResult,
    FakeAgentRuntime,
    RequestRuntimeContext,
    RuntimeAttribute,
    RuntimeChatType,
    RuntimeFeature,
    RuntimeLifecycleState,
    RuntimeMessage,
    RuntimeModelRoute,
    RuntimeOwnerType,
    RuntimePlanKind,
    RuntimePlanRef,
    RuntimePrincipal,
    RuntimeToolCall,
    RuntimeToolCallStatus,
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


class _KtAgent:
    def __init__(self) -> None:
        self._running = False
        self._interrupt_requested = False
        self.interrupt_count = 0
        self.events: list[object] = []
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

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def _process_event(self, event: object) -> str:
        self.events.append(event)
        self.controller.conversation.append("user", getattr(event, "content", ""))
        self.controller.conversation.append("assistant", "KT 完成")
        return "raw-ok"

    def interrupt(self) -> None:
        self.interrupt_count += 1
        self._interrupt_requested = True


def test_kt13_runtime_adapter_wraps_turn_conversation_route_and_interrupt():
    from nanobot_kt.runtime_adapter import build_kt13_runtime

    agent = _KtAgent()
    runtime = build_kt13_runtime(agent)
    assert isinstance(runtime, AgentRuntimePort)
    assert runtime.runtime_id == "kt13:test-creature"

    run_async(runtime.start())
    assert runtime.state is RuntimeLifecycleState.RUNNING
    assert runtime.replace_conversation((RuntimeMessage("system", "系统规则"),)) == 1

    result = run_async(
        runtime.execute_turn(
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


def test_kt13_runtime_adapter_encapsulates_tool_and_pending_private_fallbacks():
    from nanobot_kt.runtime_adapter import build_kt13_runtime

    agent = _KtAgent()
    agent._running = True
    runtime = build_kt13_runtime(agent)

    status = runtime.install_tool_policy()
    assert status.ready is True
    assert status.guard_installed is True
    assert status.schema_filter_installed is True

    agent.controller._pending_events.extend(["one", "two"])
    agent.controller._event_queue.put_nowait("queued")
    agent.controller._pending_injections.append("injection")
    reset = runtime.clear_pending_events()
    assert reset.pending_events == 2
    assert reset.queued_events == 1
    assert reset.pending_injections == 1
    assert reset.total == 4

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


def test_kt13_composition_factory_accepts_explicit_provider_route_applier():
    from nanobot_kt.runtime_adapter import build_kt13_runtime

    agent = _KtAgent()
    agent._running = True
    applied: list[tuple[object, RuntimeModelRoute]] = []

    def apply_route(target: object, route: RuntimeModelRoute) -> None:
        applied.append((target, route))

    runtime = build_kt13_runtime(
        agent,
        runtime_id="kt13:explicit",
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
    assert runtime.runtime_id == "kt13:explicit"

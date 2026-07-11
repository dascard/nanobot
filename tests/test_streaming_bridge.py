import asyncio
import json
from contextlib import suppress
from types import SimpleNamespace

import pytest


def _make_request_scope_bridge(monkeypatch, *, prompt_error, finish_calls):
    """构造只运行到 Prompt Runtime 的最小 Bridge。"""
    from nanobot_kt.bridge import NanobotBridge
    from nanobot_kt.output import BufferedOutput

    class FakeUnitOfWork:
        def __enter__(self):
            self.db = None
            return self

        def __exit__(self, *_exc):
            return False

        def commit(self):
            return None

        def rollback(self):
            return None

    tool_plan = SimpleNamespace(
        enabled={},
        disabled={},
        runtime_tool_prompt="",
        executable_tool_names=set(),
        sent_tool_schemas=[],
        sha256="b" * 64,
    )
    output = BufferedOutput()
    bridge = NanobotBridge.__new__(NanobotBridge)
    bridge.creature_path = "creatures/nanobot"
    bridge._output = output
    bridge._agent = SimpleNamespace(
        controller=SimpleNamespace(
            conversation=SimpleNamespace(_messages=[]),
        ),
        executor=SimpleNamespace(_session=SimpleNamespace(extra={})),
        _interrupt_requested=False,
    )
    bridge._session_locks = {}
    bridge._last_prompt_render_meta = {}

    async def fail_prompt_runtime(_prompt_input):
        raise prompt_error

    monkeypatch.setattr(
        "nanobot_kt.kt_adapter.reset_conversation_to_system",
        lambda _agent: (0, 0),
    )
    monkeypatch.setattr("core.tracing.new_trace_id", lambda: "trace-request")
    monkeypatch.setattr(
        "core.tracing.RunTracer.start_run",
        lambda **_kwargs: SimpleNamespace(run_id="run-request"),
    )
    monkeypatch.setattr(
        "core.tracing.RunTracer.finish_run",
        lambda run_id, **kwargs: finish_calls.append((run_id, kwargs)),
    )
    monkeypatch.setattr("core.settings_service.settings.get", lambda _key, default=None: default)
    monkeypatch.setattr("core.tool_plan.build_tool_plan", lambda **_kwargs: tool_plan)
    monkeypatch.setattr(
        "core.runtime_tool_service.record_runtime_tool_decision",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr("core.uow.UnitOfWork", FakeUnitOfWork)
    monkeypatch.setattr(
        "nanobot_kt.prompt_runtime.build_prompt_runtime",
        fail_prompt_runtime,
    )
    return bridge, output, tool_plan


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_type", "expected_status"),
    [
        (RuntimeError, "error"),
        (asyncio.CancelledError, "cancelled"),
    ],
    ids=["runtime-error", "cancelled"],
)
async def test_request_scope_restores_context_and_stream_after_prompt_failure(
    monkeypatch,
    error_type,
    expected_status,
):
    from core.final_tools import (
        get_current_final_tools,
        reset_current_final_tools,
        set_current_final_tools,
    )
    from core.tool_plan import (
        get_current_tool_plan,
        reset_current_tool_plan,
        set_current_tool_plan,
    )
    from core.tracing_context import (
        get_trace_context,
        reset_trace_context,
        set_trace_context,
    )

    finish_calls = []
    bridge, output, _tool_plan = _make_request_scope_bridge(
        monkeypatch,
        prompt_error=error_type("prompt failed"),
        finish_calls=finish_calls,
    )
    queue = asyncio.Queue()
    outer_final_tools = object()
    outer_tool_plan = object()
    trace_tokens = set_trace_context("outer-trace", "outer-run")
    final_tools_token = set_current_final_tools(outer_final_tools)
    tool_plan_token = set_current_tool_plan(outer_tool_plan)
    try:
        with pytest.raises(error_type, match="prompt failed"):
            await bridge.handle_message(
                "你好",
                user_id="u1",
                session_id="private_u1",
                metadata={"runtime_preset": "none"},
                stream=True,
                stream_queue=queue,
            )

        assert len(finish_calls) == 1
        assert finish_calls[0][0] == "run-request"
        assert finish_calls[0][1]["status"] == expected_status
        assert get_trace_context() == ("outer-trace", "outer-run")
        assert get_current_final_tools() is outer_final_tools
        assert get_current_tool_plan() is outer_tool_plan
        assert output._stream_queue is None
    finally:
        reset_current_tool_plan(tool_plan_token)
        reset_current_final_tools(final_tools_token)
        reset_trace_context(trace_tokens)


@pytest.mark.asyncio
async def test_request_scope_restores_final_tools_when_tool_plan_set_fails(monkeypatch):
    from core.final_tools import (
        get_current_final_tools,
        reset_current_final_tools,
        set_current_final_tools,
    )
    from core.tool_plan import (
        get_current_tool_plan,
        reset_current_tool_plan,
        set_current_tool_plan,
    )
    from core.tracing_context import (
        get_trace_context,
        reset_trace_context,
        set_trace_context,
    )

    finish_calls = []
    bridge, output, _tool_plan = _make_request_scope_bridge(
        monkeypatch,
        prompt_error=AssertionError("Prompt Runtime 不应执行"),
        finish_calls=finish_calls,
    )

    def fail_set_tool_plan(_plan):
        raise RuntimeError("tool plan set failed")

    monkeypatch.setattr("core.tool_plan.set_current_tool_plan", fail_set_tool_plan)
    outer_final_tools = object()
    outer_tool_plan = object()
    trace_tokens = set_trace_context("outer-trace", "outer-run")
    final_tools_token = set_current_final_tools(outer_final_tools)
    tool_plan_token = set_current_tool_plan(outer_tool_plan)
    try:
        with pytest.raises(RuntimeError, match="tool plan set failed"):
            await bridge.handle_message(
                "你好",
                user_id="u1",
                session_id="private_u1",
                metadata={"runtime_preset": "none"},
                stream=True,
                stream_queue=asyncio.Queue(),
            )

        assert len(finish_calls) == 1
        assert finish_calls[0][1]["status"] == "error"
        assert get_trace_context() == ("outer-trace", "outer-run")
        assert get_current_final_tools() is outer_final_tools
        assert get_current_tool_plan() is outer_tool_plan
        assert output._stream_queue is None
    finally:
        reset_current_tool_plan(tool_plan_token)
        reset_current_final_tools(final_tools_token)
        reset_trace_context(trace_tokens)


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_disable_active_request_stream():
    from nanobot_kt.bridge import NanobotBridge
    from nanobot_kt.output import BufferedOutput

    active_queue = asyncio.Queue()
    waiting_queue = asyncio.Queue()
    output = BufferedOutput()
    output.enable_stream(active_queue)
    lock = asyncio.Lock()
    await lock.acquire()

    bridge = NanobotBridge.__new__(NanobotBridge)
    bridge._agent = SimpleNamespace(_interrupt_requested=False)
    bridge._output = output
    bridge._session_locks = {"same-session": lock}
    waiting_task = asyncio.create_task(
        bridge.handle_message(
            "等待中的请求",
            session_id="same-session",
            stream=True,
            stream_queue=waiting_queue,
        )
    )
    try:
        await asyncio.sleep(0)
        assert waiting_task.done() is False
        waiting_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting_task
        assert output._stream_queue is active_queue
    finally:
        try:
            if not waiting_task.done():
                waiting_task.cancel()
            with suppress(asyncio.CancelledError):
                await waiting_task
        finally:
            lock.release()


@pytest.mark.asyncio
async def test_bridge_pool_passes_stream_flag_to_child(monkeypatch):
    import nanobot_kt.bridge as bridge_mod

    captured = {}

    class FakeBridge:
        def __init__(self, _creature_path="creatures/nanobot"):
            pass

        async def start(self):
            pass

        async def stop(self):
            pass

        async def handle_message(self, query, **kwargs):
            captured.update(kwargs)
            return "ok"

    monkeypatch.setattr(bridge_mod, "NanobotBridge", FakeBridge)

    pool = bridge_mod.NanobotBridgePool()
    await pool.start()
    try:
        result = await pool.handle_message(
            "你好",
            user_id="u1",
            session_id="private_u1",
            stream=True,
            stream_queue=asyncio.Queue(),
        )
    finally:
        await pool.stop()

    assert result == "ok"
    assert captured["stream"] is True
    assert captured["stream_queue"] is not None


def test_prepare_output_for_request_enables_stream_and_clears_reply_cache(monkeypatch):
    from unittest.mock import MagicMock

    from nanobot_kt.bridge import NanobotBridge

    bridge = NanobotBridge.__new__(NanobotBridge)
    bridge._output = MagicMock()
    cleared = []
    monkeypatch.setattr("core.reply_runtime_cache.clear_last_reply", lambda: cleared.append(True))
    queue = asyncio.Queue()

    bridge._prepare_output_for_request(stream_queue=queue, stream_enabled=True)

    bridge._output.clear.assert_called_once_with()
    bridge._output.enable_stream.assert_called_once_with(queue)
    assert cleared == [True]


def test_prepare_output_for_request_disables_stream_when_not_streaming(monkeypatch):
    from unittest.mock import MagicMock

    from nanobot_kt.bridge import NanobotBridge

    bridge = NanobotBridge.__new__(NanobotBridge)
    bridge._output = MagicMock()
    monkeypatch.setattr("core.reply_runtime_cache.clear_last_reply", lambda: None)

    bridge._prepare_output_for_request(stream_queue=None, stream_enabled=False)

    bridge._output.clear.assert_called_once_with()
    bridge._output.enable_stream.assert_not_called()
    bridge._output.disable_stream.assert_called_once_with()


@pytest.mark.asyncio
async def test_bridge_handle_message_streams_controller_text_deltas(monkeypatch):
    from kohakuterrarium.core.agent_handlers import AgentHandlersMixin
    from kohakuterrarium.core.controller import Controller, ControllerConfig
    from kohakuterrarium.modules.output.router import OutputRouter

    from nanobot_kt.bridge import NanobotBridge
    from nanobot_kt.output import BufferedOutput

    captured_route_kwargs = {}

    class FakeLLM:
        provider_name = "unit"
        last_tool_calls = []
        last_assistant_extra_fields = {}
        last_assistant_content_parts = None
        last_usage = {}

        def __init__(self):
            self.config = SimpleNamespace(model="old-model", temperature=None, max_tokens=None)
            self.extra_body = {}
            self.base_url = "http://unit.test/v1"
            self._api_key = "test"
            self._timeout = 1.0
            self._client = None
            self.seen_messages = None
            self.seen_kwargs = None

        async def chat(self, messages, **kwargs):
            self.seen_messages = [dict(msg) for msg in messages]
            self.seen_kwargs = dict(kwargs)
            for chunk in ("你", "好"):
                yield chunk

    class StreamingHarness(AgentHandlersMixin):
        def __init__(self, llm, output):
            self.config = SimpleNamespace(name="unit-agent", output_wiring=[])
            self.controller = Controller(
                llm,
                ControllerConfig(
                    system_prompt="unit system",
                    include_job_status=False,
                    include_tools_list=False,
                    tool_format="native",
                ),
            )
            self.output_router = OutputRouter(output)
            self.controller.output_router = self.output_router
            self.executor = SimpleNamespace(_session=SimpleNamespace(extra={}))
            self.registry = SimpleNamespace(_tools={}, list_tools=lambda: [])
            self.trigger_manager = SimpleNamespace(set_context_all=lambda _context: None)
            self._interrupt_requested = False
            self._processing_task = None
            self._active_handles = {}
            self._direct_job_meta = {}
            self._bg_controller_notify = {}
            self._termination_checker = None
            self.iteration_budget = None
            self.compact_manager = None
            self._wiring_resolver = None
            self._last_turn_text = []
            self._turn_usage_accum = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_tokens": 0,
                "total_tokens": 0,
            }

        async def _process_event(self, event):
            await self._process_event_with_controller(event, self.controller)
            self.controller.conversation.append(
                "tool",
                json.dumps(
                    {"NANOBOT_REPLY_OUTPUT": {"content": "你好"}},
                    ensure_ascii=False,
                ),
                tool_call_id="call_reply",
                name="reply",
            )

    class FakeUnitOfWork:
        def __enter__(self):
            self.db = None
            return self

        def __exit__(self, *_exc):
            return False

        def commit(self):
            return None

        def rollback(self):
            return None

    class FakeNewAPIClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def sync_models_to_registry(self, force=False):
            return None

        def estimate_complexity(self, *_args, **_kwargs):
            return 1

        def get_ordered_candidates(self, **_kwargs):
            captured_route_kwargs.update(_kwargs)
            return [{"id": "unit-model", "intelligence": 1, "context_window": 128000}]

        @classmethod
        def get_failure_tracker(cls):
            return None

    async def fake_build_prompt_runtime(prompt_input):
        return SimpleNamespace(
            pre_event_messages=[{"role": "system", "content": "unit system"}],
            event_content=prompt_input.user_input,
            meta_update={},
            prompt_key=prompt_input.prompt_key,
            prompt_mode=prompt_input.prompt_mode,
            prompt_source="unit",
            prompt_runtime_path="",
            prompt_default_path="",
            prompt_sha256="a" * 64,
        )

    monkeypatch.setattr("core.tracing.new_trace_id", lambda: "trace-stream")
    monkeypatch.setattr(
        "core.tracing.RunTracer.start_run",
        lambda **_kwargs: SimpleNamespace(run_id="run-stream"),
    )
    finish_calls = []
    monkeypatch.setattr(
        "core.tracing.RunTracer.finish_run",
        lambda run_id, **kwargs: finish_calls.append((run_id, kwargs)),
    )
    monkeypatch.setattr("core.tracing.RunTracer.update_prompt_source", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("core.tracing_context.set_trace_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("core.tracing_context.reset_trace_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("core.final_tools.set_current_final_tools", lambda _plan: object())
    monkeypatch.setattr("core.final_tools.reset_current_final_tools", lambda _token: None)
    monkeypatch.setattr("core.tool_plan.set_current_tool_plan", lambda _plan: object())
    monkeypatch.setattr("core.tool_plan.reset_current_tool_plan", lambda _token: None)
    monkeypatch.setattr("core.runtime_tool_service.record_runtime_tool_decision", lambda **_kwargs: False)
    monkeypatch.setattr("core.uow.UnitOfWork", FakeUnitOfWork)
    monkeypatch.setattr("nanobot_kt.bridge.LLM_MODEL_REPLY", "", raising=False)
    monkeypatch.setattr(
        "core.tool_plan.build_tool_plan",
        lambda **_kwargs: SimpleNamespace(
            enabled={},
            disabled={},
            runtime_tool_prompt="",
            executable_tool_names=set(),
            sent_tool_schemas=[],
            sha256="b" * 64,
        ),
    )
    monkeypatch.setattr("core.settings_service.settings.get", lambda key, default=None: default)
    monkeypatch.setattr("nanobot_kt.prompt_runtime.build_prompt_runtime", fake_build_prompt_runtime)
    monkeypatch.setattr(
        "nanobot_kt.model_runtime.resolve_reply_route_plan",
        lambda **_kwargs: SimpleNamespace(
            provider_id="unit",
            registry_provider="unit",
            timeout=1.0,
            temperature=None,
            max_tokens=None,
            enable_thinking="auto",
            base_url="http://unit.test/v1",
            api_key="test",
        ),
    )
    monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient", FakeNewAPIClient)
    monkeypatch.setattr(
        "nanobot_kt.bridge.registry.get_models_by_provider",
        lambda _provider: [{"id": "unit-model"}],
    )
    monkeypatch.setattr(
        "nanobot_kt.bridge.registry.get_model_info",
        lambda _model: {"enabled": True},
    )

    output = BufferedOutput()
    fake_llm = FakeLLM()
    bridge = NanobotBridge.__new__(NanobotBridge)
    bridge.creature_path = "creatures/nanobot"
    bridge._output = output
    bridge._agent = StreamingHarness(fake_llm, output)
    bridge._session_locks = {}
    bridge._last_prompt_render_meta = {}

    queue = asyncio.Queue()
    result = await bridge.handle_message(
        "你好",
        user_id="u1",
        session_id="private_u1",
        metadata={
            "complexity": 1,
            "runtime_preset": "none",
            "prompt_runtime_engine_override": "v1",
            "enable_reply_contract_retry": False,
        },
        stream=True,
        stream_queue=queue,
    )

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    assert result == "你好"
    assert events == [
        {"status": "delta", "text": "你"},
        {"status": "delta", "text": "好"},
        {"status": "final", "text": "你好", "replace": True, "source": "bridge"},
    ]
    assert fake_llm.seen_kwargs["stream"] is True
    assert captured_route_kwargs["required_capabilities"]["supports_stream"] is True
    user_wire = next(msg for msg in fake_llm.seen_messages if msg["role"] == "user")
    assert user_wire == {"role": "user", "content": "你好"}
    assert len(finish_calls) == 1
    assert finish_calls[0][1]["status"] == "success"
    assert output._stream_queue is None

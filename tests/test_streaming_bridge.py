import asyncio
import json
from types import SimpleNamespace

import pytest


def test_message_stream_flag_is_internal_not_wire():
    from kohakuterrarium.llm.message import Message, UserMessage

    msg = UserMessage("你好", stream=True)
    assert msg.stream is True
    assert "stream" not in msg.to_dict()

    restored = Message.from_dict({"role": "user", "content": "你好", "stream": True})
    assert restored.stream is True
    assert "stream" not in restored.to_dict()


@pytest.mark.asyncio
async def test_controller_user_message_carries_stream_without_wire_leak():
    from kohakuterrarium.core.controller import Controller, ControllerConfig
    from nanobot_kt.kt_adapter import create_user_event

    class FakeLLM:
        provider_name = "fake"
        last_tool_calls = []
        last_assistant_extra_fields = {}
        last_assistant_content_parts = None
        last_usage = {}

        def __init__(self):
            self.seen_messages = None

        async def chat(self, messages, **_kwargs):
            self.seen_messages = messages
            yield "ok"

    llm = FakeLLM()
    controller = Controller(llm, ControllerConfig(include_job_status=False))
    await controller.push_event(create_user_event("你好", stream=True))

    async for _event in controller.run_once():
        pass

    user_msg = next(msg for msg in controller.conversation.get_messages() if msg.role == "user")
    assert user_msg.stream is True
    user_wire = next(msg for msg in llm.seen_messages if msg["role"] == "user")
    assert user_wire == {"role": "user", "content": "你好"}


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


@pytest.mark.asyncio
async def test_bridge_handle_message_streams_controller_text_deltas(monkeypatch):
    from kohakuterrarium.core.agent_handlers import AgentHandlersMixin
    from kohakuterrarium.core.controller import Controller, ControllerConfig
    from kohakuterrarium.modules.output.router import OutputRouter

    from nanobot_kt.bridge import NanobotBridge
    from nanobot_kt.output import BufferedOutput

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
    monkeypatch.setattr("core.tracing.RunTracer.finish_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("core.tracing.RunTracer.update_prompt_source", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("core.tracing_context.set_trace_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("core.tracing_context.reset_trace_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("core.final_tools.set_current_final_tools", lambda _plan: object())
    monkeypatch.setattr("core.final_tools.reset_current_final_tools", lambda _token: None)
    monkeypatch.setattr("core.tool_plan.set_current_tool_plan", lambda _plan: object())
    monkeypatch.setattr("core.tool_plan.reset_current_tool_plan", lambda _token: None)
    monkeypatch.setattr("core.runtime_tool_service.record_runtime_tool_decision", lambda **_kwargs: False)
    monkeypatch.setattr("core.uow.UnitOfWork", FakeUnitOfWork)
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
    bridge._legacy_prompt_meta = {}
    bridge._last_prompt_render_meta = {}

    queue = asyncio.Queue()
    result = await bridge.handle_message(
        "你好",
        user_id="u1",
        session_id="private_u1",
        metadata={
            "complexity": 1,
            "reply_model": "unit-model",
            "runtime_preset": "none",
            "prompt_runtime_engine_override": "v1",
            "prompt_system_mode_override": "legacy",
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
    ]
    assert fake_llm.seen_kwargs["stream"] is True
    user_wire = next(msg for msg in fake_llm.seen_messages if msg["role"] == "user")
    assert user_wire == {"role": "user", "content": "你好"}
    user_message = next(
        msg for msg in bridge._agent.controller.conversation.get_messages()
        if msg.role == "user"
    )
    assert user_message.stream is True

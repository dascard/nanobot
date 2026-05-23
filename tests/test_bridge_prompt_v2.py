import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeConversation:
    def __init__(self):
        self._messages = [SimpleNamespace(role="system", content="旧 system")]

    def append(self, role, content):
        self._messages.append(SimpleNamespace(role=role, content=content))

    def get_messages(self):
        return list(self._messages)

    def to_messages(self):
        return list(self._messages)

    def find_last_user_index(self):
        for idx in range(len(self._messages) - 1, -1, -1):
            if getattr(self._messages[idx], "role", "") == "user":
                return idx
        return -1

    def truncate_from(self, idx):
        self._messages = self._messages[:idx]


class _FakeOutput:
    def __init__(self):
        self._buffer = []

    def clear(self):
        self._buffer.clear()

    def enable_stream(self, _queue):
        return None

    def get_response(self):
        return "".join(self._buffer)


@pytest.mark.asyncio
async def test_bridge_engine_v2_uses_prompt_plan_for_conversation_and_user_event(monkeypatch, db_session):
    from core import database
    from core.prompt_v2.schema import PromptCompileRequest, PromptPlan
    from core.settings_service import settings
    from nanobot_kt.bridge import NanobotBridge

    settings.set_session_factory(database.SessionLocal)

    bridge = NanobotBridge.__new__(NanobotBridge)
    bridge.creature_path = "creatures/nanobot"
    bridge._output = _FakeOutput()
    bridge._session_locks = {}
    bridge._legacy_prompt_meta = {}
    bridge._last_prompt_render_meta = {}

    conversation = _FakeConversation()
    seen_events = []

    async def fake_process_event(event):
        seen_events.append(event)
        bridge._output._buffer.append(json.dumps({"action": "reply", "content": "V2 回复"}, ensure_ascii=False))
        return "ok"

    agent = SimpleNamespace(
        controller=SimpleNamespace(conversation=conversation),
        registry=SimpleNamespace(_tools={"reply": object(), "no_reply": object()}),
        _process_event=fake_process_event,
        executor=SimpleNamespace(_session=SimpleNamespace(extra={})),
    )
    bridge._agent = agent

    captured_requests = []

    async def fake_compile(request, *, strict_audit=False):
        assert isinstance(request, PromptCompileRequest)
        assert strict_audit is True
        captured_requests.append(request)
        return PromptPlan(
            engine="v2",
            chat_type="group",
            prompt_key="chat_group",
            messages=[
                {"role": "system", "content": "V2_SYSTEM_ONLY"},
                {"role": "user", "content": "<user_input>\nPLAN_USER\n</user_input>"},
            ],
            tool_schemas=[],
            section_hashes={"base_contract": "a" * 64},
            prompt_sha256="b" * 64,
            token_estimate=10,
            warnings=[],
            debug={"template_path": "/tmp/chat_group.md"},
        )

    monkeypatch.setattr("core.prompt_v2.compiler.compile_prompt_plan", fake_compile)
    monkeypatch.setattr(
        "core.prompt_assembler.PromptAssembler.build",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("PromptAssembler must not run for successful V2 live requests")
        ),
    )
    monkeypatch.setattr("clients.classifier_client.resolve_model_route", lambda _route: {
        "provider_id": "newapi",
        "base_url": "http://127.0.0.1:1/v1",
        "api_key": "test",
        "timeout": 1,
    })
    monkeypatch.setattr("clients.classifier_client.ensure_model_route_enabled", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("nanobot_kt.bridge.registry.get_models_by_provider", lambda _provider: [{"id": "fake"}])
    monkeypatch.setattr("core.tool_plan.build_effective_tool_schemas", lambda _enabled: [
        {"type": "function", "function": {"name": "reply"}},
    ])

    fake_client = MagicMock()
    fake_client.sync_models_to_registry = AsyncMock()
    fake_client.estimate_complexity = MagicMock(return_value=3)
    fake_client.get_ordered_candidates = MagicMock(return_value=[
        {"id": "fake-model", "intelligence": 8, "cost_input_1m": 0.0, "context_window": 128000}
    ])
    monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient", lambda *args, **kwargs: fake_client)

    result = await bridge.handle_message(
        "原始当前",
        user_id="u1",
        session_id="group_1001",
        sender_name="雀",
        metadata={
            "prompt_runtime_engine_override": "v2",
            "prompt_system_mode_override": "managed",
            "chat_type": "group",
            "is_group": True,
            "group_id": "1001",
            "history_messages": [{"role": "user", "content": "旧历史"}],
            "history_header": "<conversation_context>历史</conversation_context>",
            "runtime_preset": "none",
            "reply_model": "fake-model",
            "enable_reply_contract_retry": False,
        },
    )

    assert result == "V2 回复"
    assert captured_requests
    assert captured_requests[0].user_input == "原始当前"
    assert captured_requests[0].tool_schemas == [
        {"type": "function", "function": {"name": "reply"}},
    ]
    assert [m.content for m in conversation._messages] == ["V2_SYSTEM_ONLY"]
    assert seen_events
    assert seen_events[0].content == "<user_input>\nPLAN_USER\n</user_input>"


@pytest.mark.asyncio
async def test_bridge_engine_v2_fails_fast_when_prompt_audit_fails(monkeypatch, db_session):
    from core import database
    from core.prompt_v2.audit import PromptAuditError
    from core.settings_service import settings
    from nanobot_kt.bridge import NanobotBridge

    settings.set_session_factory(database.SessionLocal)

    bridge = NanobotBridge.__new__(NanobotBridge)
    bridge.creature_path = "creatures/nanobot"
    bridge._output = _FakeOutput()
    bridge._session_locks = {}
    bridge._legacy_prompt_meta = {}
    bridge._last_prompt_render_meta = {}

    conversation = _FakeConversation()
    seen_events = []

    async def fake_process_event(event):
        seen_events.append(event)
        bridge._output._buffer.append("不应该调用")
        return "ok"

    bridge._agent = SimpleNamespace(
        controller=SimpleNamespace(conversation=conversation),
        registry=SimpleNamespace(_tools={"reply": object(), "no_reply": object()}),
        _process_event=fake_process_event,
        executor=SimpleNamespace(_session=SimpleNamespace(extra={})),
    )

    async def fake_compile(*_args, **kwargs):
        assert kwargs.get("strict_audit") is True
        raise PromptAuditError(["runtime_tool_prompt must appear once, got 2"])

    monkeypatch.setattr("core.prompt_v2.compiler.compile_prompt_plan", fake_compile)

    result = await bridge.handle_message(
        "原始当前",
        user_id="u1",
        session_id="group_1002",
        sender_name="雀",
        metadata={
            "prompt_runtime_engine_override": "v2",
            "chat_type": "group",
            "is_group": True,
            "group_id": "1002",
            "runtime_preset": "none",
            "enable_reply_contract_retry": False,
        },
    )

    assert result == ""
    assert bridge.pop_last_reply_meta("group_1002")["_agent_result"] == "prompt_v2_audit_failed"
    assert seen_events == []


@pytest.mark.asyncio
async def test_bridge_engine_v2_can_fallback_to_v1_when_audit_policy_allows(monkeypatch, db_session):
    from core import database
    from core.database import AgentRun
    from core.prompt_v2.audit import PromptAuditError
    from core.prompt_v2.schema import PromptPlan
    from core.settings_service import settings
    from nanobot_kt.bridge import NanobotBridge

    settings.set_session_factory(database.SessionLocal)
    monkeypatch.setenv("NANOBOT_PROMPT_V2_AUDIT_FAILURE_POLICY", "fallback_v1")
    settings.invalidate()

    bridge = NanobotBridge.__new__(NanobotBridge)
    bridge.creature_path = "creatures/nanobot"
    bridge._output = _FakeOutput()
    bridge._session_locks = {}
    bridge._legacy_prompt_meta = {}
    bridge._last_prompt_render_meta = {}

    conversation = _FakeConversation()
    seen_events = []

    async def fake_process_event(event):
        seen_events.append(event)
        bridge._output._buffer.append('{"action":"reply","content":"fallback 回复"}')
        return "ok"

    bridge._agent = SimpleNamespace(
        controller=SimpleNamespace(conversation=conversation),
        registry=SimpleNamespace(_tools={"reply": object(), "no_reply": object()}),
        _process_event=fake_process_event,
        executor=SimpleNamespace(_session=SimpleNamespace(extra={})),
    )

    failed_plan = PromptPlan(
        engine="v2",
        chat_type="group",
        prompt_key="chat_group",
        messages=[{"role": "user", "content": "<user_input>\n坏计划\n</user_input>"}],
        tool_schemas=[],
        section_hashes={},
        prompt_sha256="c" * 64,
        token_estimate=1,
        warnings=[],
        debug={},
    )

    async def fake_compile(*_args, **kwargs):
        assert kwargs.get("strict_audit") is True
        raise PromptAuditError(["runtime_tool_prompt must appear once, got 0"], plan=failed_plan)

    monkeypatch.setattr("core.prompt_v2.compiler.compile_prompt_plan", fake_compile)

    build_calls = []

    def fake_build(self, context, *, trace_id="", run_id=""):
        build_calls.append(context)
        return SimpleNamespace(
            prompt_key=context.prompt_key,
            prompt_mode=context.mode,
            prompt_source="Legacy fallback prompt",
            prompt_runtime_path="/tmp/v1.md",
            prompt_default_path="/tmp/default.md",
            prompt_sha256="d" * 64,
            pre_event_messages=[{"role": "system", "content": "V1_SYSTEM"}],
            event_content="<user_input>\nV1_USER\n</user_input>",
        )

    monkeypatch.setattr("core.prompt_assembler.PromptAssembler.build", fake_build)
    monkeypatch.setattr("clients.classifier_client.resolve_model_route", lambda _route: {
        "provider_id": "newapi",
        "base_url": "http://127.0.0.1:1/v1",
        "api_key": "test",
        "timeout": 1,
    })
    monkeypatch.setattr("clients.classifier_client.ensure_model_route_enabled", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("nanobot_kt.bridge.registry.get_models_by_provider", lambda _provider: [{"id": "fake"}])
    monkeypatch.setattr("core.tool_schema_preview.build_effective_tool_schemas", lambda _enabled: [])

    fake_client = MagicMock()
    fake_client.sync_models_to_registry = AsyncMock()
    fake_client.estimate_complexity = MagicMock(return_value=3)
    fake_client.get_ordered_candidates = MagicMock(return_value=[
        {"id": "fake-model", "intelligence": 8, "cost_input_1m": 0.0, "context_window": 128000}
    ])
    monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient", lambda *args, **kwargs: fake_client)

    result = await bridge.handle_message(
        "原始当前",
        user_id="u1",
        session_id="group_1003",
        sender_name="雀",
        metadata={
            "prompt_runtime_engine_override": "v2",
            "chat_type": "group",
            "is_group": True,
            "group_id": "1003",
            "runtime_preset": "none",
            "reply_model": "fake-model",
            "enable_reply_contract_retry": False,
        },
    )

    assert result == "fallback 回复"
    assert build_calls
    assert build_calls[0].prompt_key == "group_chat"
    assert seen_events
    assert seen_events[0].content == "<user_input>\nV1_USER\n</user_input>"

    run = db_session.query(AgentRun).filter(AgentRun.session_id == "group_1003").first()
    assert run is not None
    assert '"prompt_v2_audit_failed": true' in run.meta_json
    assert '"prompt_fallback": "v1"' in run.meta_json
    assert "runtime_tool_prompt must appear once" in run.meta_json


@pytest.mark.asyncio
async def test_bridge_tool_plan_does_not_mutate_registry_tools(monkeypatch, db_session):
    from core import database
    from core.prompt_v2.schema import PromptCompileRequest, PromptPlan
    from core.settings_service import settings
    from nanobot_kt.bridge import NanobotBridge

    settings.set_session_factory(database.SessionLocal)

    bridge = NanobotBridge.__new__(NanobotBridge)
    bridge.creature_path = "creatures/nanobot"
    bridge._output = _FakeOutput()
    bridge._session_locks = {}
    bridge._legacy_prompt_meta = {}
    bridge._last_prompt_render_meta = {}

    conversation = _FakeConversation()
    registry_tools = {
        "reply": object(),
        "no_reply": object(),
        "python_sandbox": object(),
    }
    registry = SimpleNamespace(_tools=registry_tools)
    registry_before = dict(registry_tools)

    async def fake_process_event(event):
        assert "python_sandbox" in registry._tools
        bridge._output._buffer.append(json.dumps({"action": "reply", "content": "ok"}, ensure_ascii=False))
        return "ok"

    bridge._agent = SimpleNamespace(
        controller=SimpleNamespace(conversation=conversation),
        registry=registry,
        _process_event=fake_process_event,
        executor=SimpleNamespace(_session=SimpleNamespace(extra={})),
    )

    async def fake_compile(request, *, strict_audit=False):
        assert isinstance(request, PromptCompileRequest)
        return PromptPlan(
            engine="v2",
            chat_type="group",
            prompt_key="chat_group",
            messages=[
                {"role": "system", "content": "V2_SYSTEM_ONLY"},
                {"role": "user", "content": "<user_input>\nPLAN_USER\n</user_input>"},
            ],
            tool_schemas=request.tool_schemas,
            section_hashes={"base_contract": "a" * 64},
            prompt_sha256="b" * 64,
            token_estimate=10,
            warnings=[],
            debug={"template_path": "/tmp/chat_group.md"},
        )

    monkeypatch.setattr("core.prompt_v2.compiler.compile_prompt_plan", fake_compile)
    monkeypatch.setattr("clients.classifier_client.resolve_model_route", lambda _route: {
        "provider_id": "newapi",
        "base_url": "http://127.0.0.1:1/v1",
        "api_key": "test",
        "timeout": 1,
    })
    monkeypatch.setattr("clients.classifier_client.ensure_model_route_enabled", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("nanobot_kt.bridge.registry.get_models_by_provider", lambda _provider: [{"id": "fake"}])

    fake_client = MagicMock()
    fake_client.sync_models_to_registry = AsyncMock()
    fake_client.estimate_complexity = MagicMock(return_value=3)
    fake_client.get_ordered_candidates = MagicMock(return_value=[
        {"id": "fake-model", "intelligence": 8, "cost_input_1m": 0.0, "context_window": 128000}
    ])
    monkeypatch.setattr("nanobot_kt.bridge.NewAPIClient", lambda *args, **kwargs: fake_client)

    result = await bridge.handle_message(
        "原始当前",
        user_id="u1",
        session_id="group_1004",
        sender_name="雀",
        metadata={
            "prompt_runtime_engine_override": "v2",
            "chat_type": "group",
            "is_group": True,
            "group_id": "1004",
            "runtime_preset": "none",
            "reply_model": "fake-model",
            "enable_reply_contract_retry": False,
        },
    )

    assert result == "ok"
    assert registry._tools == registry_before

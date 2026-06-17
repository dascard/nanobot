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


def _prompt_tool_plan(**overrides):
    defaults = {
        "runtime_tool_prompt": "<runtime_tool_prompt>工具</runtime_tool_prompt>",
        "sent_tool_schemas": [
            {"type": "function", "function": {"name": "reply"}},
        ],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_bridge_prompt_runtime_engine_defaults_to_v2_and_invalid_falls_back(monkeypatch):
    from core.settings_service import settings
    from nanobot_kt.bridge import NanobotBridge

    bridge = NanobotBridge.__new__(NanobotBridge)

    monkeypatch.setattr(settings, "get", lambda _key, _default=None: None)
    assert bridge._prompt_runtime_engine() == "v2"

    monkeypatch.setattr(settings, "get", lambda _key, _default=None: "bad-engine")
    assert bridge._prompt_runtime_engine() == "v2"

    monkeypatch.setattr(settings, "get", lambda _key, _default=None: "v1")
    assert bridge._prompt_runtime_engine() == "v1"


def test_bridge_resolve_prompt_runtime_engine_honors_v1_override_and_invalid_falls_back(monkeypatch):
    from core.settings_service import settings
    from nanobot_kt.bridge import NanobotBridge

    bridge = NanobotBridge.__new__(NanobotBridge)
    monkeypatch.setattr(settings, "get", lambda _key, _default=None: "v2")

    assert bridge._resolve_prompt_runtime_engine({"prompt_runtime_engine_override": "v1"}) == "v1"
    assert bridge._resolve_prompt_runtime_engine({"prompt_engine_override": "v1"}) == "v1"
    assert bridge._resolve_prompt_runtime_engine({"prompt_runtime_engine_override": "bad"}) == "v2"
    assert bridge._resolve_prompt_runtime_engine({}) == "v2"


def test_bridge_build_prompt_runtime_input_for_v2(monkeypatch):
    from nanobot_kt.bridge import NanobotBridge, PromptRuntimeAssemblyContext

    bridge = NanobotBridge.__new__(NanobotBridge)
    monkeypatch.setattr(bridge, "_prompt_v2_audit_failure_policy", lambda: "fail_fast")
    monkeypatch.setattr(bridge, "_prompt_system_mode", lambda: "legacy")

    prompt_input = bridge._build_prompt_runtime_input(
        PromptRuntimeAssemblyContext(
            prompt_engine="v2",
            prompt_mode="v2",
            prompt_key="chat_group",
            chat_type="group",
            runtime_chat_type="group",
            session_id="group_1001",
            user_id="u1",
            group_id="1001",
            sender_name="雀",
            query="当前问题",
            persona_text="画像",
            history_header="历史头",
            history_messages=[{"role": "user", "content": "旧消息"}],
            runtime_tool_prompt="工具提示",
            effort_constraint="short",
            trace_id="trace_1",
            run_id="run_1",
            is_group=True,
            meta={
                "prompt_system_mode_override": "managed",
                "sender_id": "sender_1",
                "session_name": "测试群",
                "trigger_reason": "direct",
                "timing_decision": "continue",
                "message_id": "msg_1",
                "source_message_ids": ["msg_0", "", "  ", 42],
                "self_id": "bot_self",
                "bot_id": "bot_1",
                "character_name": "七濑",
                "bot_aliases": ["bot", ""],
                "group_profile_context": "群画像",
                "expression_context": "表达",
                "jargon_context": "黑话",
                "context_debug": {"group_memory_injected": True},
            },
            tool_plan=_prompt_tool_plan(),
        )
    )

    assert prompt_input.prompt_engine == "v2"
    assert prompt_input.prompt_mode == "v2"
    assert prompt_input.prompt_key == "chat_group"
    assert prompt_input.chat_type == "group"
    assert prompt_input.runtime_chat_type == "group"
    assert prompt_input.sender_id == "sender_1"
    assert prompt_input.bot_name == "七濑"
    assert prompt_input.source_message_ids == ["msg_0", "42"]
    assert prompt_input.persona_text == "画像"
    assert prompt_input.history_messages == [{"role": "user", "content": "旧消息"}]
    assert prompt_input.runtime_tool_prompt == "工具提示"
    assert prompt_input.tool_schemas == [
        {"type": "function", "function": {"name": "reply"}},
    ]
    assert prompt_input.group_profile_context == "群画像"
    assert prompt_input.expression_context == "表达"
    assert prompt_input.jargon_context == "黑话"
    assert prompt_input.debug == {"context_debug": {"group_memory_injected": True}}
    assert prompt_input.audit_failure_policy == "fail_fast"


def test_bridge_build_prompt_runtime_input_for_v1_uses_prompt_mode(monkeypatch):
    from nanobot_kt.bridge import NanobotBridge, PromptRuntimeAssemblyContext

    bridge = NanobotBridge.__new__(NanobotBridge)
    monkeypatch.setattr(bridge, "_prompt_v2_audit_failure_policy", lambda: "fail_fast")
    monkeypatch.setattr(bridge, "_prompt_system_mode", lambda: "shadow")

    prompt_input = bridge._build_prompt_runtime_input(
        PromptRuntimeAssemblyContext(
            prompt_engine="v1",
            prompt_mode="managed",
            prompt_key="group_chat",
            chat_type="group",
            runtime_chat_type="group",
            session_id="group_1001",
            user_id="u1",
            group_id="1001",
            sender_name="雀",
            query="当前问题",
            persona_text="",
            history_header="",
            history_messages=[],
            runtime_tool_prompt="",
            effort_constraint="",
            trace_id="trace_1",
            run_id="run_1",
            is_group=True,
            meta={"prompt_mode_override": "bad"},
            tool_plan=_prompt_tool_plan(sent_tool_schemas=[]),
        )
    )

    assert prompt_input.prompt_engine == "v1"
    assert prompt_input.prompt_mode == "managed"
    assert prompt_input.prompt_key == "group_chat"
    assert prompt_input.persona_text == "无已存储画像"


def test_bridge_build_prompt_runtime_input_falls_back_when_tool_schemas_unavailable(monkeypatch):
    from nanobot_kt.bridge import NanobotBridge, PromptRuntimeAssemblyContext

    class BrokenToolPlan:
        @property
        def sent_tool_schemas(self):
            raise RuntimeError("schemas unavailable")

    bridge = NanobotBridge.__new__(NanobotBridge)
    monkeypatch.setattr(bridge, "_prompt_v2_audit_failure_policy", lambda: "fail_fast")
    monkeypatch.setattr(bridge, "_prompt_system_mode", lambda: "shadow")

    prompt_input = bridge._build_prompt_runtime_input(
        PromptRuntimeAssemblyContext(
            prompt_engine="v2",
            prompt_mode="v2",
            prompt_key="chat_private",
            chat_type="private",
            runtime_chat_type="private_superuser",
            session_id="u1",
            user_id="u1",
            group_id="",
            sender_name="雀",
            query="当前问题",
            persona_text="",
            history_header="",
            history_messages=[],
            runtime_tool_prompt="",
            effort_constraint="",
            trace_id="trace_1",
            run_id="run_1",
            is_group=False,
            meta={"bot_name": "七濑"},
            tool_plan=BrokenToolPlan(),
        )
    )

    assert prompt_input.prompt_mode == "v2"
    assert prompt_input.runtime_chat_type == "private_superuser"
    assert prompt_input.bot_name == "七濑"
    assert prompt_input.tool_schemas == []


@pytest.mark.asyncio
async def test_bridge_engine_v2_uses_prompt_plan_for_conversation_and_user_event(monkeypatch, db_session):
    from core import database
    from core.database import AgentRun, GroupMemory
    from core.prompt_v2.schema import PromptCompileRequest, PromptPlan
    from core.settings_service import settings
    from nanobot_kt.bridge import NanobotBridge

    settings.set_session_factory(database.SessionLocal)
    db_session.add(GroupMemory(
        id=11,
        group_id="group_1001",
        memory_type="topic",
        content="群里经常讨论 UI 层次",
        content_hash="bridge-success-memory-11",
        confidence=0.9,
        evidence_count=2,
        evidence_log_ids_json="[1, 2]",
        decay_score=1.0,
        status="active",
        inject_policy="auto",
    ))
    db_session.add(GroupMemory(
        id=12,
        group_id="group_1001",
        memory_type="style",
        content="群里喜欢直接指出问题",
        content_hash="bridge-success-memory-12",
        confidence=0.9,
        evidence_count=2,
        evidence_log_ids_json="[3, 4]",
        decay_score=1.0,
        status="active",
        inject_policy="auto",
    ))
    db_session.commit()

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
            debug={
                "template_path": "/tmp/chat_group.md",
                "context_debug": {
                    "group_memory_injected": True,
                    "group_memory_ids": [11, 12],
                    "group_memory_context_chars": 620,
                    "group_profile_mode": "on",
                },
            },
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
    run = db_session.query(AgentRun).filter(AgentRun.session_id == "group_1001").first()
    assert run is not None
    assert '"group_memory_injected": true' in run.meta_json
    assert '"group_memory_ids": [11, 12]' in run.meta_json
    assert '"group_profile_mode": "on"' in run.meta_json
    db_session.expire_all()
    refreshed = {
        row.id: row
        for row in db_session.query(GroupMemory).filter(GroupMemory.id.in_([11, 12])).all()
    }
    assert refreshed[11].injected_count == 1
    assert refreshed[12].injected_count == 1
    assert refreshed[11].last_injected_at is not None


@pytest.mark.asyncio
async def test_bridge_engine_v2_uses_character_name_as_bot_name(monkeypatch, db_session):
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
    bridge._agent = SimpleNamespace(
        controller=SimpleNamespace(conversation=_FakeConversation()),
        registry=SimpleNamespace(_tools={"reply": object(), "no_reply": object()}),
        _process_event=AsyncMock(return_value="ok"),
        executor=SimpleNamespace(_session=SimpleNamespace(extra={})),
    )

    captured_requests = []

    async def fake_compile(request, *, strict_audit=False):
        assert isinstance(request, PromptCompileRequest)
        captured_requests.append(request)
        return PromptPlan(
            engine="v2",
            chat_type="private",
            prompt_key="chat_private",
            messages=[
                {"role": "system", "content": "V2_SYSTEM_ONLY"},
                {"role": "user", "content": "<user_input>\nPLAN_USER\n</user_input>"},
            ],
            tool_schemas=[],
            section_hashes={"base_contract": "a" * 64},
            prompt_sha256="b" * 64,
            token_estimate=10,
            warnings=[],
            debug={"template_path": "/tmp/chat_private.md"},
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

    await bridge.handle_message(
        "原始当前",
        user_id="u1",
        session_id="u1",
        sender_name="雀",
        metadata={
            "prompt_runtime_engine_override": "v2",
            "chat_type": "private",
            "character_name": "七濑",
            "runtime_preset": "none",
            "reply_model": "fake-model",
            "enable_reply_contract_retry": False,
        },
    )

    assert captured_requests
    assert captured_requests[0].bot_name == "七濑"


@pytest.mark.asyncio
async def test_bridge_engine_v2_fails_fast_when_prompt_audit_fails(monkeypatch, db_session):
    from core import database
    from core.database import GroupMemory
    from core.prompt_v2.audit import PromptAuditError
    from core.settings_service import settings
    from nanobot_kt.bridge import NanobotBridge

    settings.set_session_factory(database.SessionLocal)
    db_session.add(GroupMemory(
        id=21,
        group_id="group_1002",
        memory_type="topic",
        content="audit failure should not count this",
        content_hash="bridge-audit-fail-memory-21",
        confidence=0.9,
        evidence_count=2,
        evidence_log_ids_json="[1, 2]",
        decay_score=1.0,
        status="active",
        inject_policy="auto",
    ))
    db_session.commit()

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
            "context_debug": {
                "group_memory_injected": True,
                "group_memory_ids": [21],
                "group_memory_context_chars": 120,
                "group_profile_mode": "on",
            },
        },
    )

    assert result == ""
    assert bridge.pop_last_reply_meta("group_1002")["_agent_result"] == "prompt_v2_audit_failed"
    assert seen_events == []
    memory = db_session.query(GroupMemory).filter(GroupMemory.id == 21).first()
    assert memory.injected_count == 0
    assert memory.last_injected_at is None


@pytest.mark.asyncio
async def test_bridge_engine_v2_ignores_fallback_v1_policy_when_audit_fails(monkeypatch, db_session):
    from core import database
    from core.database import AgentRun
    from core.prompt_v2.audit import PromptAuditError
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

    seen_events = []

    async def fake_process_event(event):
        seen_events.append(event)
        bridge._output._buffer.append('{"action":"reply","content":"不应发送"}')
        return "ok"

    bridge._agent = SimpleNamespace(
        controller=SimpleNamespace(conversation=_FakeConversation()),
        registry=SimpleNamespace(_tools={"reply": object(), "no_reply": object()}),
        _process_event=fake_process_event,
        executor=SimpleNamespace(_session=SimpleNamespace(extra={})),
    )

    async def fake_compile(*_args, **kwargs):
        assert kwargs.get("strict_audit") is True
        raise PromptAuditError(["runtime_tool_prompt must appear once, got 0"])

    monkeypatch.setattr("core.prompt_v2.compiler.compile_prompt_plan", fake_compile)
    monkeypatch.setattr(
        "core.prompt_assembler.PromptAssembler.build",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("V2 audit failure must not fallback to PromptAssembler")
        ),
    )

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
            "enable_reply_contract_retry": False,
        },
    )

    assert result == ""
    assert seen_events == []
    assert bridge.pop_last_reply_meta("group_1003")["_agent_result"] == "prompt_v2_audit_failed"
    run = db_session.query(AgentRun).filter(AgentRun.session_id == "group_1003").first()
    assert run is not None
    assert '"prompt_v2_audit_failed": true' in run.meta_json
    assert "prompt_fallback" not in run.meta_json
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

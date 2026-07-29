import hashlib
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


def _wire_tool_schema(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _append_reply_exchange(
    conversation: _FakeConversation,
    content: str,
    *,
    call_id: str,
) -> None:
    from creatures.nanobot.prompts.skills.reply.tool import build_reply_output

    conversation._messages.extend([
        SimpleNamespace(
            role="assistant",
            content="",
            tool_calls=[{
                "id": call_id,
                "type": "function",
                "function": {"name": "reply", "arguments": "{}"},
            }],
        ),
        SimpleNamespace(
            role="tool",
            name="reply",
            tool_call_id=call_id,
            content=build_reply_output(content),
        ),
    ])


def _prompt_tool_plan(**overrides):
    defaults = {
        "runtime_tool_prompt": "<runtime_tool_prompt>工具</runtime_tool_prompt>",
        "sent_tool_schemas": [_wire_tool_schema("reply")],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _prompt_template_resolutions() -> dict:
    return {
        "base_contract": {
            "template_key": "chat/main",
            "active_source": "runtime",
            "active_path": "/runtime/chat/main.md",
            "runtime_path": "/runtime/chat/main.md",
            "default_path": "/default/chat/main.md",
            "active_sha256": "c" * 64,
            "runtime_sha256": "c" * 64,
            "default_sha256": "d" * 64,
            "baseline_version": None,
            "drift_status": "untracked_legacy",
        }
    }


def test_bridge_prompt_runtime_engine_defaults_to_prompt_and_invalid_falls_back(monkeypatch):
    from core.settings_service import settings
    from nanobot_kt.bridge import NanobotBridge

    bridge = NanobotBridge.__new__(NanobotBridge)

    monkeypatch.setattr(settings, "get", lambda _key, _default=None: None)
    assert bridge._prompt_runtime_engine() == "prompt"

    monkeypatch.setattr(settings, "get", lambda _key, _default=None: "bad-engine")
    assert bridge._prompt_runtime_engine() == "prompt"

    monkeypatch.setattr(settings, "get", lambda _key, _default=None: "v1")
    assert bridge._prompt_runtime_engine() == "prompt"


def test_bridge_resolve_prompt_runtime_engine_treats_v1_as_canonical_runtime(monkeypatch):
    from core.settings_service import settings
    from nanobot_kt.bridge import NanobotBridge

    bridge = NanobotBridge.__new__(NanobotBridge)
    monkeypatch.setattr(settings, "get", lambda _key, _default=None: "v1")

    assert bridge._prompt_runtime_engine() == "prompt"
    assert bridge._resolve_prompt_runtime_engine({"prompt_runtime_engine_override": "v1"}) == "prompt"
    assert bridge._resolve_prompt_runtime_engine({"prompt_engine_override": "v1"}) == "prompt"
    assert bridge._resolve_prompt_runtime_engine({"prompt_runtime_engine_override": "bad"}) == "prompt"


def test_bridge_build_prompt_runtime_input_maps_v2_alias_to_prompt(monkeypatch):
    from nanobot_kt.bridge import NanobotBridge, PromptRuntimeAssemblyContext

    bridge = NanobotBridge.__new__(NanobotBridge)
    monkeypatch.setattr(bridge, "_prompt_v2_audit_failure_policy", lambda: "fail_fast")

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
            session_guidance="本群回答保持简洁",
            session_guidance_chat_stream_id="qq:1001:group",
            history_header="历史头",
            history_messages=[{"role": "user", "content": "旧消息"}],
            runtime_tool_prompt="工具提示",
            effort_constraint="short",
            trace_id="trace_1",
            run_id="run_1",
            is_group=True,
            meta={
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
                # 旧字段即使仍由上游透传，也不能进入 Prompt DTO。
                "expression_context": "表达",
                "jargon_context": "黑话",
                "context_debug": {"group_memory_injected": True},
            },
            tool_plan=_prompt_tool_plan(),
        )
    )

    assert prompt_input.prompt_engine == "prompt"
    assert prompt_input.prompt_mode == "prompt"
    assert prompt_input.prompt_key == "chat_group"
    assert prompt_input.chat_type == "group"
    assert prompt_input.runtime_chat_type == "group"
    assert prompt_input.sender_id == "sender_1"
    assert prompt_input.bot_name == "七濑"
    assert prompt_input.source_message_ids == ["msg_0", "42"]
    assert prompt_input.persona_text == "画像"
    assert prompt_input.session_guidance == "本群回答保持简洁"
    assert prompt_input.session_guidance_chat_stream_id == "qq:1001:group"
    assert prompt_input.history_messages == [{"role": "user", "content": "旧消息"}]
    assert prompt_input.runtime_tool_prompt == "工具提示"
    assert prompt_input.tool_schemas == [_wire_tool_schema("reply")]
    assert prompt_input.group_profile_context == "群画像"
    assert not hasattr(prompt_input, "expression_context")
    assert not hasattr(prompt_input, "jargon_context")
    assert prompt_input.debug == {"context_debug": {"group_memory_injected": True}}
    assert prompt_input.audit_failure_policy == "fail_fast"


def test_bridge_build_prompt_runtime_input_passes_platform(monkeypatch):
    from nanobot_kt.bridge import NanobotBridge, PromptRuntimeAssemblyContext

    bridge = NanobotBridge.__new__(NanobotBridge)
    monkeypatch.setattr(bridge, "_prompt_v2_audit_failure_policy", lambda: "fail_fast")

    prompt_input = bridge._build_prompt_runtime_input(
        PromptRuntimeAssemblyContext(
            prompt_engine="prompt",
            prompt_mode="prompt",
            prompt_key="chat_private",
            chat_type="private",
            runtime_chat_type="private",
            platform="web",
            session_id="private_u1",
            user_id="u1",
            group_id="",
            sender_name="用户",
            query="你好",
            persona_text="画像",
            history_header="",
            history_messages=[],
            runtime_tool_prompt="[RuntimeTool]",
            effort_constraint="",
            trace_id="trace-1",
            run_id="run-1",
            is_group=False,
            meta={"platform": "web", "user_id": "u1"},
            tool_plan=_prompt_tool_plan(sent_tool_schemas=[]),
        )
    )

    assert prompt_input.platform == "web"


@pytest.mark.asyncio
async def test_research_metadata_reaches_real_internal_private_strict_compiler(
    monkeypatch,
    db_session,
):
    from core import database
    from core.settings_service import settings
    from nanobot_kt.bridge import NanobotBridge
    from nanobot_kt.prompt_runtime import (
        PromptRuntimeAuditFailure,
        build_prompt_runtime as real_build_prompt_runtime,
    )

    settings.set_session_factory(database.SessionLocal)
    bridge = NanobotBridge.__new__(NanobotBridge)
    bridge.creature_path = "creatures/nanobot"
    bridge._output = _FakeOutput()
    bridge._session_locks = {}
    bridge._last_prompt_render_meta = {}
    bridge._agent = SimpleNamespace(
        controller=SimpleNamespace(conversation=_FakeConversation()),
        registry=SimpleNamespace(_tools={}),
        executor=SimpleNamespace(_session=SimpleNamespace(extra={})),
    )
    monkeypatch.setattr(bridge, "_log_agent_result", lambda *_args, **_kwargs: None)
    captured_renders = []
    monkeypatch.setattr(
        "core.tracing.PromptTracer.record_render",
        lambda **kwargs: captured_renders.append(kwargs),
    )

    captured_inputs = []
    captured_results = []
    captured_plans = []
    from core.prompt_v2.compiler import compile_prompt_plan as real_compile_prompt_plan

    async def capture_real_compile(request, *, strict_audit=True):
        plan = await real_compile_prompt_plan(request, strict_audit=strict_audit)
        captured_plans.append(plan)
        return plan

    monkeypatch.setattr(
        "core.prompt_v2.compiler.compile_prompt_plan",
        capture_real_compile,
    )

    async def compile_then_stop(prompt_input):
        captured_inputs.append(prompt_input)
        result = await real_build_prompt_runtime(prompt_input)
        captured_results.append(result)
        raise PromptRuntimeAuditFailure(
            "测试在真实严格编译后停止",
            meta_update={"test_stop_after_compile": True},
        )

    monkeypatch.setattr(
        "nanobot_kt.prompt_runtime.build_prompt_runtime",
        compile_then_stop,
    )

    response = await bridge.handle_message(
        "调查 Prompt Flow 的内部研究路径",
        user_id="research-user",
        session_id="research_flow-v2",
        sender_name="主动研究任务",
        metadata={
            "platform": "internal",
            "chat_type": "research",
            "is_group": False,
            "is_superuser": False,
            "user_id": "research-user",
            "runtime_preset": "research",
            "dry_run": True,
        },
    )

    assert response == ""
    assert len(captured_inputs) == 1
    assert captured_inputs[0].platform == "internal"
    assert captured_inputs[0].chat_type == "private"
    assert len(captured_results) == 1
    assert len(captured_plans) == 1
    flow_node_ids = [section["node_id"] for section in captured_plans[0].flow_sections]
    assert "base_contract" in flow_node_ids
    assert "private_policy" in flow_node_ids
    assert "qq_common_policy" not in flow_node_ids
    assert "qq_group_policy" not in flow_node_ids
    template_resolutions = captured_plans[0].template_resolutions
    base_resolution = template_resolutions["base_contract"]
    assert captured_results[0].prompt_template_resolutions == template_resolutions
    assert captured_results[0].prompt_runtime_path == (base_resolution["runtime_path"] or "")
    assert captured_results[0].prompt_default_path == (base_resolution["default_path"] or "")
    assert captured_results[0].prompt_sha256 == captured_plans[0].prompt_sha256
    assert captured_renders[0]["prompt_template_resolutions"] == template_resolutions
    assert captured_renders[0]["prompt_sha256"] == captured_plans[0].prompt_sha256
    assert "template_resolutions" not in captured_renders[0]["variables"]
    assert "template_paths" not in captured_renders[0]["variables"]
    assert captured_results[0].prompt_key == "chat_private"
    assert captured_results[0].meta_update["prompt_engine"] == "prompt"
    # 编译失败发生在 Agent turn 之前；请求上下文应通过 Prompt 输入合同验证，
    # 不再依赖 KT executor 的私有临时状态。
    assert captured_inputs[0].platform == "internal"
    assert captured_inputs[0].chat_type == "private"


def test_bridge_build_prompt_runtime_input_passes_explicit_super_user_fact(monkeypatch):
    from nanobot_kt.bridge import NanobotBridge, PromptRuntimeAssemblyContext

    bridge = NanobotBridge.__new__(NanobotBridge)
    monkeypatch.setattr(bridge, "_prompt_v2_audit_failure_policy", lambda: "fail_fast")

    prompt_input = bridge._build_prompt_runtime_input(
        PromptRuntimeAssemblyContext(
            prompt_engine="prompt",
            prompt_mode="prompt",
            prompt_key="chat_private",
            chat_type="private",
            runtime_chat_type="private_superuser",
            platform="qq",
            session_id="private_u1",
            user_id="u1",
            group_id="",
            sender_name="用户",
            query="我是不是超级用户",
            persona_text="",
            history_header="",
            history_messages=[],
            runtime_tool_prompt="[RuntimeTool]",
            effort_constraint="",
            trace_id="trace-1",
            run_id="run-1",
            is_group=False,
            is_super_user=True,
            meta={"is_superuser": True, "user_id": "u1"},
            tool_plan=_prompt_tool_plan(sent_tool_schemas=[]),
        )
    )

    assert prompt_input.is_super_user is True


@pytest.mark.asyncio
async def test_build_prompt_runtime_passes_super_user_fact_to_compile_request(monkeypatch):
    from core.prompt_v2.schema import PromptPlan
    from nanobot_kt.prompt_runtime import PromptRuntimeInput, build_prompt_runtime

    captured = {}

    async def fake_compile_prompt_plan(request, *, strict_audit=False):
        captured["is_super_user"] = request.is_super_user
        return PromptPlan(
            engine="prompt",
            chat_type="private",
            platform="qq",
            prompt_key="chat_private",
            messages=[{"role": "user", "content": "<user_input>\n你好\n</user_input>"}],
            tool_schemas=[],
            section_hashes={},
            prompt_sha256="a" * 64,
            token_estimate=11,
            message_token_estimate=7,
            tool_schema_token_estimate=4,
            warnings=[],
            debug={
                "template_resolutions": _prompt_template_resolutions(),
                "message_token_estimate": 7,
                "tool_schema_token_estimate": 4,
                "token_estimate": 11,
            },
        )

    monkeypatch.setattr("core.prompt_v2.compiler.compile_prompt_plan", fake_compile_prompt_plan)
    recorded_render = {}
    monkeypatch.setattr(
        "core.tracing.PromptTracer.record_render",
        lambda **kwargs: recorded_render.update(kwargs),
    )

    result = await build_prompt_runtime(
        PromptRuntimeInput(
            prompt_engine="prompt",
            prompt_mode="prompt",
            prompt_key="chat_private",
            chat_type="private",
            runtime_chat_type="private_superuser",
            session_id="private_u1",
            user_id="u1",
            group_id="",
            sender_name="用户",
            sender_id="u1",
            session_name="",
            trigger_reason="",
            timing_decision="",
            current_message_id="",
            source_message_ids=[],
            self_id="",
            bot_id="",
            bot_name="",
            bot_aliases=[],
            user_input="你好",
            persona_text="",
            history_header="",
            history_messages=[],
            runtime_tool_prompt="[RuntimeTool]",
            effort_constraint="",
            trace_id="trace-1",
            run_id="run-1",
            is_super_user=True,
        )
    )

    assert captured["is_super_user"] is True
    assert result.message_token_estimate == 7
    assert result.tool_schema_token_estimate == 4
    assert result.token_estimate == 11
    assert recorded_render["token_estimate"] == 11
    assert recorded_render["variables"]["message_token_estimate"] == 7
    assert recorded_render["variables"]["tool_schema_token_estimate"] == 4


@pytest.mark.asyncio
async def test_build_prompt_runtime_rejects_missing_base_template_resolution(monkeypatch):
    from core.prompt_v2.schema import PromptPlan
    from nanobot_kt.prompt_runtime import (
        PromptRuntimeAuditFailure,
        PromptRuntimeInput,
        build_prompt_runtime,
    )

    async def fake_compile_prompt_plan(_request, *, strict_audit=False):
        assert strict_audit is True
        return PromptPlan(
            engine="prompt",
            chat_type="private",
            platform="qq",
            prompt_key="chat_private",
            messages=[{"role": "user", "content": "<user_input>hi</user_input>"}],
            tool_schemas=[],
            section_hashes={"base_contract": "a" * 64},
            prompt_sha256="b" * 64,
            token_estimate=1,
            warnings=[],
            debug={"template_resolutions": {}},
        )

    monkeypatch.setattr(
        "core.prompt_v2.compiler.compile_prompt_plan",
        fake_compile_prompt_plan,
    )
    monkeypatch.setattr(
        "core.tracing.PromptTracer.record_render",
        lambda **_kwargs: pytest.fail("来源合同失败时不应写渲染记录"),
    )

    with pytest.raises(PromptRuntimeAuditFailure, match="base_contract"):
        await build_prompt_runtime(PromptRuntimeInput(
            prompt_engine="prompt",
            prompt_mode="prompt",
            prompt_key="chat_private",
            chat_type="private",
            runtime_chat_type="private",
            session_id="private-missing-resolution",
            user_id="missing-resolution-user",
            group_id="",
            sender_name="用户",
            sender_id="missing-resolution-user",
            session_name="",
            trigger_reason="",
            timing_decision="",
            current_message_id="",
            source_message_ids=[],
            self_id="",
            bot_id="",
            bot_name="",
            bot_aliases=[],
            user_input="hi",
            persona_text="",
            history_header="",
            history_messages=[],
            runtime_tool_prompt="[RuntimeTool]",
            effort_constraint="",
            trace_id="trace-missing-resolution",
            run_id="run-missing-resolution",
        ))


def test_bridge_build_prompt_runtime_input_coerces_v1_to_canonical_runtime(monkeypatch):
    from nanobot_kt.bridge import NanobotBridge, PromptRuntimeAssemblyContext

    bridge = NanobotBridge.__new__(NanobotBridge)
    monkeypatch.setattr(bridge, "_prompt_v2_audit_failure_policy", lambda: "fail_fast")

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

    assert prompt_input.prompt_engine == "prompt"
    assert prompt_input.prompt_mode == "prompt"
    assert prompt_input.prompt_key == "chat_group"
    assert prompt_input.persona_text == "无已存储画像"


@pytest.mark.asyncio
async def test_build_prompt_runtime_rejects_v1_live_prompt(monkeypatch):
    from nanobot_kt.prompt_runtime import PromptRuntimeInput, build_prompt_runtime

    with pytest.raises(ValueError, match="unsupported prompt engine"):
        await build_prompt_runtime(PromptRuntimeInput(
            prompt_engine="v1",
            prompt_mode="legacy",
            prompt_key="group_chat",
            chat_type="group",
            runtime_chat_type="group",
            session_id="group_1",
            user_id="u1",
            group_id="1",
            sender_name="雀",
            sender_id="u1",
            session_name="",
            trigger_reason="",
            timing_decision="",
            current_message_id="",
            source_message_ids=[],
            self_id="",
            bot_id="",
            bot_name="",
            bot_aliases=[],
            user_input="hi",
            persona_text="",
            history_header="",
            history_messages=[],
            runtime_tool_prompt="",
            effort_constraint="",
            trace_id="t",
            run_id="r",
            is_group=True,
        ))


@pytest.mark.asyncio
async def test_build_prompt_runtime_passes_platform_to_compile_request(monkeypatch):
    from core.prompt_v2.schema import PromptPlan
    from nanobot_kt.prompt_runtime import PromptRuntimeInput, build_prompt_runtime

    captured = {}

    async def fake_compile_prompt_plan(request, *, strict_audit=False):
        captured["platform"] = request.platform
        captured["normalized_platform"] = request.normalized_platform
        return PromptPlan(
            engine="prompt",
            chat_type="private",
            platform=request.normalized_platform,
            prompt_key="chat_private",
            messages=[{"role": "user", "content": "<user_input>\n你好\n</user_input>"}],
            tool_schemas=[],
            section_hashes={},
            prompt_sha256="a" * 64,
            token_estimate=1,
            warnings=[],
            debug={"template_resolutions": _prompt_template_resolutions()},
        )

    monkeypatch.setattr("core.prompt_v2.compiler.compile_prompt_plan", fake_compile_prompt_plan)
    monkeypatch.setattr("core.tracing.PromptTracer.record_render", lambda **_kwargs: None)

    result = await build_prompt_runtime(PromptRuntimeInput(
        prompt_engine="prompt",
        prompt_mode="prompt",
        prompt_key="chat_private",
        chat_type="private",
        runtime_chat_type="private",
        platform="web",
        session_id="private_u1",
        user_id="u1",
        group_id="",
        sender_name="用户",
        sender_id="u1",
        session_name="",
        trigger_reason="",
        timing_decision="",
        current_message_id="",
        source_message_ids=[],
        self_id="",
        bot_id="",
        bot_name="",
        bot_aliases=[],
        user_input="你好",
        persona_text="画像",
        history_header="",
        history_messages=[],
        runtime_tool_prompt="[RuntimeTool]",
        effort_constraint="",
        trace_id="trace-1",
        run_id="run-1",
    ))

    assert captured["platform"] == "web"
    assert captured["normalized_platform"] == "web"
    assert result.prompt_key == "chat_private"


def test_bridge_build_prompt_runtime_input_rejects_unavailable_tool_schemas(monkeypatch):
    from nanobot_kt.bridge import NanobotBridge, PromptRuntimeAssemblyContext

    class BrokenToolPlan:
        @property
        def sent_tool_schemas(self):
            raise RuntimeError("schemas unavailable")

    bridge = NanobotBridge.__new__(NanobotBridge)
    monkeypatch.setattr(bridge, "_prompt_v2_audit_failure_policy", lambda: "fail_fast")

    with pytest.raises(RuntimeError, match="ToolPlan schema 快照失败") as exc:
        bridge._build_prompt_runtime_input(
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

    assert isinstance(exc.value.__cause__, RuntimeError)
    assert str(exc.value.__cause__) == "schemas unavailable"


@pytest.mark.asyncio
async def test_bridge_event_capabilities_reuse_validated_tool_schema_snapshot(
    monkeypatch,
):
    from nanobot_kt.bridge import NanobotBridge, PromptRuntimeAssemblyContext

    class ReadOnceToolPlan:
        def __init__(self):
            self.access_count = 0

        @property
        def sent_tool_schemas(self):
            self.access_count += 1
            if self.access_count > 1:
                raise RuntimeError("schemas must not be read twice")
            return [_wire_tool_schema("reply")]

    tool_plan = ReadOnceToolPlan()
    bridge = NanobotBridge.__new__(NanobotBridge)
    monkeypatch.setattr(bridge, "_prompt_v2_audit_failure_policy", lambda: "fail_fast")
    prompt_input = bridge._build_prompt_runtime_input(
        PromptRuntimeAssemblyContext(
            prompt_engine="v2",
            prompt_mode="v2",
            prompt_key="chat_private",
            chat_type="private",
            runtime_chat_type="private",
            session_id="private_snapshot-user",
            user_id="snapshot-user",
            group_id="",
            sender_name="用户",
            query="当前问题",
            persona_text="",
            history_header="",
            history_messages=[],
            runtime_tool_prompt="",
            effort_constraint="",
            trace_id="trace_snapshot",
            run_id="run_snapshot",
            is_group=False,
            meta={},
            tool_plan=tool_plan,
        )
    )

    payload = await bridge._prepare_event_payload(
        prompt_event_content="<user_input>当前问题</user_input>",
        files=None,
        tool_schemas=prompt_input.tool_schemas,
    )

    assert tool_plan.access_count == 1
    assert payload.required_capabilities == {
        "supports_stream": True,
        "supports_tools": True,
    }


@pytest.mark.asyncio
async def test_bridge_engine_v2_uses_prompt_plan_for_conversation_and_user_event(monkeypatch, db_session):
    from core import database
    from core import tool_plan as tool_plan_module
    from core.database import AgentRun, ChatStreamConfig, GroupMemory, PromptRenderLog
    from core.prompt_v2.schema import PromptCompileRequest, PromptPlan
    from core.session_guidance import resolve_session_guidance as real_resolve_guidance
    from core.settings_service import settings
    from nanobot_kt.bridge import NanobotBridge

    settings.set_session_factory(database.SessionLocal)
    guidance_body = "GROUP_GUIDANCE_BODY_SENTINEL"
    guidance_sha = hashlib.sha256(guidance_body.encode("utf-8")).hexdigest()
    db_session.add(ChatStreamConfig(
        chat_stream_id="qq:1001:group",
        session_guidance=guidance_body,
    ))
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
    bridge._last_prompt_render_meta = {}
    monkeypatch.setattr(
        bridge,
        "_apply_runtime_model_route",
        lambda *_args, **_kwargs: None,
    )

    conversation = _FakeConversation()
    seen_events = []
    seen_runtime_contexts = []

    async def fake_process_event(event):
        from core.agent_runtime.request_scope import (
            require_current_runtime_context,
        )

        seen_events.append(event)
        seen_runtime_contexts.append(require_current_runtime_context())
        _append_reply_exchange(
            conversation,
            "V2 回复",
            call_id="call_prompt_v2_reply",
        )
        return "ok"

    agent = SimpleNamespace(
        controller=SimpleNamespace(conversation=conversation),
        registry=SimpleNamespace(_tools={"reply": object(), "no_reply": object()}),
        _process_event=fake_process_event,
        executor=SimpleNamespace(_session=SimpleNamespace(extra={})),
    )
    bridge._agent = agent

    captured_requests = []
    captured_guidance_calls = []
    template_resolutions = _prompt_template_resolutions()

    def capture_resolve_guidance(db, *, platform, chat_type, session_id):
        captured_guidance_calls.append({
            "platform": platform,
            "chat_type": chat_type,
            "session_id": session_id,
        })
        return real_resolve_guidance(
            db,
            platform=platform,
            chat_type=chat_type,
            session_id=session_id,
        )

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
                "template_paths": {"base_contract": "/runtime/chat/main.md"},
                "template_resolutions": template_resolutions,
                "request_prompt_sha256": "b" * 64,
                "session_guidance_chat_stream_id": request.session_guidance_chat_stream_id,
                "session_guidance_configured": bool(request.session_guidance),
                "session_guidance_chars": len(request.session_guidance),
                "session_guidance_sha256": hashlib.sha256(
                    request.session_guidance.encode("utf-8")
                ).hexdigest() if request.session_guidance else "",
                "session_guidance_resolution_status": "configured",
                "session_guidance_status": "emitted",
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
        "nanobot_kt.bridge.resolve_session_guidance",
        capture_resolve_guidance,
        raising=False,
    )
    monkeypatch.setattr("clients.classifier_client.resolve_model_route", lambda _route: {
        "provider_id": "newapi",
        "base_url": "http://127.0.0.1:1/v1",
        "api_key": "test",
        "timeout": 1,
    })
    monkeypatch.setattr("clients.classifier_client.ensure_model_route_enabled", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("nanobot_kt.bridge.registry.get_models_by_provider", lambda _provider: [{"id": "fake"}])
    original_build_tool_plan = tool_plan_module.build_tool_plan
    captured_tool_plan_calls = []

    def capture_build_tool_plan(*args, **kwargs):
        captured_tool_plan_calls.append(dict(kwargs))
        return original_build_tool_plan(*args, **kwargs)

    monkeypatch.setattr(tool_plan_module, "build_tool_plan", capture_build_tool_plan)
    monkeypatch.setattr("core.tool_plan.build_effective_tool_schemas", lambda _enabled: [
        _wire_tool_schema("reply"),
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
            "chat_type": "group",
            "is_group": True,
            "is_superuser": True,
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
    assert captured_requests[0].is_super_user is True
    assert captured_requests[0].group_id == "1001"
    assert captured_requests[0].session_guidance == guidance_body
    assert captured_requests[0].session_guidance_chat_stream_id == "qq:1001:group"
    assert captured_requests[0].tool_schemas == [_wire_tool_schema("reply")]
    assert captured_guidance_calls == [{
        "platform": "qq",
        "chat_type": "group",
        "session_id": "group_1001",
    }]
    assert [
        m.content
        for m in conversation._messages
        if getattr(m, "role", "") == "system"
    ] == ["V2_SYSTEM_ONLY"]
    assert seen_events
    assert seen_events[0].content == "<user_input>\nPLAN_USER\n</user_input>"
    assert [call["chat_type"] for call in captured_tool_plan_calls] == ["group"]
    assert [call["group_id"] for call in captured_tool_plan_calls] == ["1001"]
    assert len(seen_runtime_contexts) == 1
    runtime_context = seen_runtime_contexts[0]
    assert runtime_context["is_super_user"] is True
    assert runtime_context["runtime_chat_type"] == "group"
    assert runtime_context["group_id"] == "1001"
    assert bridge._agent.executor._session.extra == {}
    from core.agent_runtime.request_scope import get_current_runtime_context

    assert get_current_runtime_context() is None
    run = db_session.query(AgentRun).filter(AgentRun.session_id == "group_1001").first()
    assert run is not None
    assert run.group_id == "1001"
    assert run.prompt_source == "runtime"
    assert run.prompt_runtime_path == "/runtime/chat/main.md"
    assert run.prompt_default_path == "/default/chat/main.md"
    assert run.prompt_sha256 == "b" * 64
    assert json.loads(run.prompt_template_resolutions_json) == template_resolutions
    prompt_render = (
        db_session.query(PromptRenderLog)
        .filter(PromptRenderLog.run_id == run.run_id)
        .one()
    )
    assert prompt_render.prompt_source == "runtime"
    assert prompt_render.prompt_sha256 == "b" * 64
    assert json.loads(prompt_render.prompt_template_resolutions_json) == template_resolutions
    assert '"group_memory_injected": true' in run.meta_json
    assert '"group_memory_ids": [11, 12]' in run.meta_json
    assert '"group_profile_mode": "on"' in run.meta_json
    run_meta = json.loads(run.meta_json)
    assert run_meta["platform"] == "qq"
    assert run_meta["chat_type"] == "group"
    assert run_meta["session_guidance_chat_stream_id"] == "qq:1001:group"
    assert run_meta["session_guidance_configured"] is True
    assert run_meta["session_guidance_chars"] == len(guidance_body)
    assert run_meta["session_guidance_sha256"] == guidance_sha
    assert run_meta["session_guidance_resolution_status"] == "configured"
    assert run_meta["session_guidance_status"] == "emitted"
    assert guidance_body not in run.meta_json
    db_session.expire_all()
    refreshed = {
        row.id: row
        for row in db_session.query(GroupMemory).filter(GroupMemory.id.in_([11, 12])).all()
    }
    assert refreshed[11].injected_count == 1
    assert refreshed[12].injected_count == 1
    assert refreshed[11].last_injected_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_super_user,expected_super_user",
    [(True, True), ("false", False)],
)
async def test_bridge_engine_v2_maps_private_runtime_metadata(
    monkeypatch,
    db_session,
    raw_super_user,
    expected_super_user,
):
    from core import database
    from core import runtime_tool_service as runtime_tool_service_module
    from core import tool_plan as tool_plan_module
    from core.database import AgentRun, ChatStreamConfig
    from core.prompt_v2.schema import PromptCompileRequest, PromptPlan
    from core.session_guidance import resolve_session_guidance as real_resolve_guidance
    from core.settings_service import settings
    from nanobot_kt.bridge import NanobotBridge

    settings.set_session_factory(database.SessionLocal)
    guidance_body = "PRIVATE_GUIDANCE_BODY_SENTINEL"
    guidance_sha = hashlib.sha256(guidance_body.encode("utf-8")).hexdigest()
    db_session.add(ChatStreamConfig(
        chat_stream_id="qq:placeholder:private",
        session_guidance=guidance_body,
    ))
    db_session.commit()

    bridge = NanobotBridge.__new__(NanobotBridge)
    bridge.creature_path = "creatures/nanobot"
    bridge._output = _FakeOutput()
    bridge._session_locks = {}
    bridge._last_prompt_render_meta = {}
    monkeypatch.setattr(
        bridge,
        "_apply_runtime_model_route",
        lambda *_args, **_kwargs: None,
    )
    seen_runtime_contexts = []

    async def fake_process_event(_event):
        from core.agent_runtime.request_scope import (
            require_current_runtime_context,
        )

        seen_runtime_contexts.append(require_current_runtime_context())
        return "ok"

    bridge._agent = SimpleNamespace(
        controller=SimpleNamespace(conversation=_FakeConversation()),
        registry=SimpleNamespace(_tools={"reply": object(), "no_reply": object()}),
        _process_event=fake_process_event,
        executor=SimpleNamespace(_session=SimpleNamespace(extra={})),
    )

    captured_requests = []
    captured_tool_plan_calls = []
    captured_decision_calls = []
    captured_assembly_contexts = []
    captured_prompt_inputs = []
    captured_guidance_calls = []

    original_build_tool_plan = tool_plan_module.build_tool_plan
    original_record_runtime_tool_decision = (
        runtime_tool_service_module.record_runtime_tool_decision
    )
    original_build_prompt_runtime_input = bridge._build_prompt_runtime_input

    def capture_build_tool_plan(*args, **kwargs):
        captured_tool_plan_calls.append(dict(kwargs))
        return original_build_tool_plan(*args, **kwargs)

    def capture_record_runtime_tool_decision(*args, **kwargs):
        captured_decision_calls.append(dict(kwargs))
        return original_record_runtime_tool_decision(*args, **kwargs)

    def capture_build_prompt_runtime_input(context):
        captured_assembly_contexts.append(context)
        prompt_input = original_build_prompt_runtime_input(context)
        captured_prompt_inputs.append(prompt_input)
        return prompt_input

    def capture_resolve_guidance(db, *, platform, chat_type, session_id):
        captured_guidance_calls.append({
            "platform": platform,
            "chat_type": chat_type,
            "session_id": session_id,
        })
        return real_resolve_guidance(
            db,
            platform=platform,
            chat_type=chat_type,
            session_id=session_id,
        )

    monkeypatch.setattr(tool_plan_module, "build_tool_plan", capture_build_tool_plan)
    monkeypatch.setattr(
        runtime_tool_service_module,
        "record_runtime_tool_decision",
        capture_record_runtime_tool_decision,
    )
    monkeypatch.setattr(
        bridge,
        "_build_prompt_runtime_input",
        capture_build_prompt_runtime_input,
    )
    monkeypatch.setattr(
        "nanobot_kt.bridge.resolve_session_guidance",
        capture_resolve_guidance,
        raising=False,
    )

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
            debug={
                "template_path": "/tmp/chat_private.md",
                "template_resolutions": _prompt_template_resolutions(),
                "session_guidance_chat_stream_id": request.session_guidance_chat_stream_id,
                "session_guidance_configured": bool(request.session_guidance),
                "session_guidance_chars": len(request.session_guidance),
                "session_guidance_sha256": hashlib.sha256(
                    request.session_guidance.encode("utf-8")
                ).hexdigest() if request.session_guidance else "",
                "session_guidance_resolution_status": "configured",
                "session_guidance_status": "emitted",
            },
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
        _wire_tool_schema("reply"),
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
        session_id="private_placeholder",
        sender_name="雀",
        metadata={
            "prompt_runtime_engine_override": "v2",
            "chat_type": "private",
            "is_group": False,
            "group_id": "private_placeholder",
            "user_id": "u1",
            "character_name": "七濑",
            "is_superuser": raw_super_user,
            "runtime_preset": "none",
            "reply_model": "fake-model",
            "enable_reply_contract_retry": False,
        },
    )

    assert captured_requests
    assert captured_requests[0].bot_name == "七濑"
    assert captured_requests[0].is_super_user is expected_super_user
    assert captured_requests[0].session_id == "private_placeholder"
    assert captured_requests[0].group_id == ""
    assert captured_requests[0].session_guidance == guidance_body
    assert (
        captured_requests[0].session_guidance_chat_stream_id
        == "qq:placeholder:private"
    )
    assert [call["group_id"] for call in captured_tool_plan_calls] == [""]
    assert [call["group_id"] for call in captured_decision_calls] == [""]
    assert [call["session_id"] for call in captured_decision_calls] == [
        "private_placeholder"
    ]
    assert [context.group_id for context in captured_assembly_contexts] == [""]
    assert [context.session_id for context in captured_assembly_contexts] == [
        "private_placeholder"
    ]
    assert [prompt_input.group_id for prompt_input in captured_prompt_inputs] == [""]
    assert [prompt_input.session_id for prompt_input in captured_prompt_inputs] == [
        "private_placeholder"
    ]
    assert [context.session_guidance for context in captured_assembly_contexts] == [
        guidance_body
    ]
    assert [prompt_input.session_guidance for prompt_input in captured_prompt_inputs] == [
        guidance_body
    ]
    assert captured_guidance_calls == [{
        "platform": "qq",
        "chat_type": "private",
        "session_id": "private_placeholder",
    }]
    assert len(seen_runtime_contexts) == 1
    runtime_context = seen_runtime_contexts[0]
    assert runtime_context["is_super_user"] is expected_super_user
    assert runtime_context["group_id"] == ""
    assert runtime_context["session_id"] == "private_placeholder"
    assert runtime_context["runtime_chat_type"] == (
        "private_superuser" if expected_super_user else "private"
    )
    assert runtime_context["user_id"] == "u1"
    assert bridge._agent.executor._session.extra == {}
    from core.agent_runtime.request_scope import get_current_runtime_context

    assert get_current_runtime_context() is None
    run = (
        db_session.query(AgentRun)
        .filter(AgentRun.session_id == "private_placeholder")
        .first()
    )
    assert run is not None
    assert run.group_id == ""
    run_meta = json.loads(run.meta_json)
    assert run_meta["platform"] == "qq"
    assert run_meta["chat_type"] == "private"
    assert run_meta["session_guidance_chat_stream_id"] == "qq:placeholder:private"
    assert run_meta["session_guidance_configured"] is True
    assert run_meta["session_guidance_chars"] == len(guidance_body)
    assert run_meta["session_guidance_sha256"] == guidance_sha
    assert run_meta["session_guidance_resolution_status"] == "configured"
    assert run_meta["session_guidance_status"] == "emitted"
    assert guidance_body not in run.meta_json


@pytest.mark.asyncio
async def test_guidance_db_failure_stops_before_prompt_compile_and_model(
    monkeypatch,
    db_session,
    caplog,
):
    from core import database
    from core.database import AgentRun
    from core.settings_service import settings
    from nanobot_kt.bridge import NanobotBridge

    settings.set_session_factory(database.SessionLocal)
    guidance_body = "FAILED_GUIDANCE_BODY_SENTINEL"
    bridge = NanobotBridge.__new__(NanobotBridge)
    bridge.creature_path = "creatures/nanobot"
    bridge._output = _FakeOutput()
    bridge._session_locks = {}
    bridge._last_prompt_render_meta = {}
    bridge._agent = SimpleNamespace(
        controller=SimpleNamespace(conversation=_FakeConversation()),
        registry=SimpleNamespace(_tools={"reply": object(), "no_reply": object()}),
        _process_event=AsyncMock(side_effect=AssertionError("模型事件不得执行")),
        executor=SimpleNamespace(_session=SimpleNamespace(extra={})),
    )
    called = {
        "tool_plan": False,
        "tool_decision": False,
        "compile": False,
        "model": False,
    }

    def fail_resolve(*_args, **_kwargs):
        raise RuntimeError("guidance db failed")

    async def forbidden_compile(*_args, **_kwargs):
        called["compile"] = True
        raise AssertionError("Prompt 编译器不得执行")

    def forbidden_tool_plan(*_args, **_kwargs):
        called["tool_plan"] = True
        raise AssertionError("工具计划不得执行")

    def forbidden_tool_decision(*_args, **_kwargs):
        called["tool_decision"] = True
        raise AssertionError("工具决策不得写入")

    async def forbidden_model(*_args, **_kwargs):
        called["model"] = True
        raise AssertionError("模型循环不得执行")

    monkeypatch.setattr(
        "nanobot_kt.bridge.resolve_session_guidance",
        fail_resolve,
        raising=False,
    )
    monkeypatch.setattr("core.tool_plan.build_tool_plan", forbidden_tool_plan)
    monkeypatch.setattr(
        "core.runtime_tool_service.record_runtime_tool_decision",
        forbidden_tool_decision,
    )
    monkeypatch.setattr("core.prompt_v2.compiler.compile_prompt_plan", forbidden_compile)
    monkeypatch.setattr(bridge, "_run_model_loop", forbidden_model)

    with pytest.raises(RuntimeError, match="guidance db failed"):
        await bridge.handle_message(
            "原始当前",
            user_id="u1",
            session_id="group_1004",
            sender_name="用户",
            metadata={
                "chat_type": "group",
                "is_group": True,
                "group_id": "1004",
                "platform": "qq",
                "runtime_preset": "none",
                "enable_reply_contract_retry": False,
            },
        )

    assert called == {
        "tool_plan": False,
        "tool_decision": False,
        "compile": False,
        "model": False,
    }
    run = db_session.query(AgentRun).filter(AgentRun.session_id == "group_1004").one()
    run_meta = json.loads(run.meta_json)
    assert run.status == "error"
    assert run_meta["platform"] == "qq"
    assert run_meta["chat_type"] == "group"
    assert guidance_body not in run.meta_json
    assert guidance_body not in run.error
    assert guidance_body not in caplog.text


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
    assert run.group_id == "1003"
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
    bridge._last_prompt_render_meta = {}
    monkeypatch.setattr(
        bridge,
        "_apply_runtime_model_route",
        lambda *_args, **_kwargs: None,
    )

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
        _append_reply_exchange(
            conversation,
            "ok",
            call_id="call_tool_plan_reply",
        )
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
            debug={
                "template_path": "/tmp/chat_group.md",
                "template_resolutions": _prompt_template_resolutions(),
            },
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

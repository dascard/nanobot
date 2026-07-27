from tests.async_helpers import run_async
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _tool(name):
    return {"type": "function", "function": {"name": name, "parameters": {}}}


def test_final_tools_filter_removes_unallowed_framework_tools():
    from core.final_tools import FinalToolSet, filter_payload_tools

    final_tools = FinalToolSet(
        allowed={"reply"},
        disabled={"python_sandbox": "测试禁用"},
    )
    payload = {
        "tools": [
            _tool("reply"),
            _tool("python_sandbox"),
            _tool("skill"),
            _tool("memory_read"),
        ],
        "tool_choice": "auto",
    }

    result = filter_payload_tools(payload, final_tools)

    assert [tool["function"]["name"] for tool in result["tools"]] == ["reply"]
    assert result["tool_choice"] == "auto"


def test_final_tools_filter_normalizes_and_clips_tuple_tools():
    from core.final_tools import FinalToolSet, filter_payload_tools

    result = filter_payload_tools(
        {
            "tools": (_tool("reply"), _tool("python_sandbox")),
            "tool_choice": "auto",
        },
        FinalToolSet(
            allowed={"reply"},
            disabled={"python_sandbox": "测试禁用"},
        ),
    )

    assert isinstance(result["tools"], list)
    assert [tool["function"]["name"] for tool in result["tools"]] == ["reply"]


def test_final_tools_filter_removes_tool_choice_when_no_tools_remain():
    from core.final_tools import FinalToolSet, filter_payload_tools

    result = filter_payload_tools(
        {"tools": [_tool("skill")], "tool_choice": "auto"},
        FinalToolSet(allowed=set(), disabled={}),
    )

    assert "tools" not in result
    assert "tool_choice" not in result


def test_final_tools_filter_is_pure_clipping_without_schema_growth(monkeypatch):
    from core.final_tools import filter_payload_tools
    from core.prompt_v2 import tool_templates
    from core.prompt_v2.tool_templates import get_tool_template_policy
    from core.tool_plan import build_tool_plan

    plan = build_tool_plan(chat_type="private", runtime_preset="full")
    schemas = list(plan.sent_tool_schemas)
    original_descriptions = {
        tool["function"]["name"]: tool["function"]["description"]
        for tool in schemas
    }
    overlay_calls = []

    def fail_if_overlay_called(_schema):
        overlay_calls.append(True)
        raise AssertionError("final filter must not overlay tool templates")

    monkeypatch.setattr(
        tool_templates,
        "overlay_tool_schema_description",
        fail_if_overlay_called,
    )
    payload = {"tools": schemas, "tool_choice": "auto"}
    result = filter_payload_tools(payload, plan)

    assert result["tools"] == schemas
    assert payload["tools"] == schemas
    assert overlay_calls == []
    for tool in result["tools"]:
        description = tool["function"]["description"]
        assert description == original_descriptions[tool["function"]["name"]]
        policy = get_tool_template_policy(tool["function"]["name"])
        if policy and policy.body:
            assert description.startswith(policy.body[:1600].rstrip())
        assert "[V2ToolTemplate:" not in description
        assert "sha256:" not in description

    result["tools"][0]["function"]["description"] = "调用方修改"
    assert payload["tools"][0]["function"]["description"] != "调用方修改"
    assert plan.sent_tool_schemas[0]["function"]["description"] != "调用方修改"


def test_final_tools_group_override_requires_nonempty_group_scope(db_session):
    from core.database import ToolOverride
    from core.final_tools import resolve_final_tools

    db_session.add(ToolOverride(
        tool_name="memory_query",
        scope_type="group",
        scope_id="private_placeholder",
        enabled=0,
        reason="群级禁用",
    ))
    db_session.commit()

    private_tools = resolve_final_tools(
        chat_type="private",
        group_id="",
        runtime_preset="full",
        db=db_session,
    )
    group_tools = resolve_final_tools(
        chat_type="group",
        group_id="private_placeholder",
        runtime_preset="full",
        db=db_session,
    )

    assert "memory_query" in private_tools.allowed
    assert "memory_query" not in private_tools.disabled
    assert "memory_query" not in group_tools.allowed
    assert group_tools.disabled["memory_query"] == "群级禁用"


def test_openai_sdk_tracer_filters_tools_before_request(monkeypatch):
    from core.final_tools import FinalToolSet, final_tools_scope
    from core.llm_sdk_tracing import install_openai_chat_completion_tracer

    recorded = []
    finished = []
    original_seen = {}
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.record_request",
        staticmethod(lambda **kwargs: recorded.append(kwargs) or 1001),
    )
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.finish_request",
        staticmethod(lambda **kwargs: finished.append(kwargs)),
    )

    async def create(**kwargs):
        original_seen.update(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}

    llm = SimpleNamespace(
        _client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=create)))),
        _api_key="reply-key",
        _extra_headers={},
        base_url="http://same-provider.test/v1",
        provider_name="newapi",
    )

    assert install_openai_chat_completion_tracer(
        llm,
        provider="newapi",
        base_url="http://same-provider.test/v1",
    )
    with final_tools_scope(FinalToolSet(allowed={"reply"}, disabled={"python_sandbox": "禁用"})):
        run_async(llm._client.chat.completions.create(
            model="manual-model",
            messages=[{"role": "user", "content": "你好"}],
            tools=[_tool("reply"), _tool("python_sandbox"), _tool("skill")],
            tool_choice="auto",
        ))

    assert [tool["function"]["name"] for tool in original_seen["tools"]] == ["reply"]
    assert [tool["function"]["name"] for tool in recorded[0]["request"]["tools"]] == ["reply"]
    assert finished[0]["log_id"] == 1001


def test_new_api_client_build_payload_uses_final_tools_scope():
    from clients.new_api_client import NewAPIClient
    from core.final_tools import FinalToolSet, final_tools_scope

    client = NewAPIClient(api_key="key", base_url="http://newapi.test/v1")

    with final_tools_scope(FinalToolSet(allowed={"reply"}, disabled={"python_sandbox": "禁用"})):
        payload = client._build_payload(
            messages=[{"role": "user", "content": "你好"}],
            tools=[_tool("reply"), _tool("python_sandbox"), _tool("skill")],
            temperature=0,
            stream=False,
            model="test-model",
        )

    assert [tool["function"]["name"] for tool in payload["tools"]] == ["reply"]


@pytest.mark.parametrize(
    ("kwargs", "model_info", "expected"),
    [
        (
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "看图"},
                            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
                        ],
                    }
                ],
                "tools": None,
                "stream": False,
            },
            {"id": "text-only", "supports_image": False},
            "supports_image",
        ),
        (
            {
                "messages": [{"role": "user", "content": "查一下"}],
                "tools": [_tool("search")],
                "stream": False,
            },
            {"id": "no-tools", "supports_tools": False},
            "supports_tools",
        ),
        (
            {
                "messages": [{"role": "user", "content": "hello"}],
                "tools": None,
                "stream": True,
            },
            {"id": "no-stream", "supports_stream": False},
            "supports_stream",
        ),
    ],
)
def test_new_api_payload_rejects_required_capabilities_when_model_lacks_support(
    kwargs,
    model_info,
    expected,
):
    from clients.new_api_client import NewAPIClient

    client = NewAPIClient(api_key="key", base_url="http://newapi.test/v1")

    with pytest.raises(ValueError, match=expected):
        client._build_payload(
            temperature=0,
            model=str(model_info["id"]),
            model_info=model_info,
            **kwargs,
        )


def test_runtime_preset_prompt_does_not_expand_enabled_tool_schema():
    from core.runtime_tool_service import build_runtime_tool_prompt

    prompt = build_runtime_tool_prompt(
        enabled={"reply": True, "ai_daily": True, "bash": False},
        disabled={"bash": "群聊强制禁用"},
        chat_type="group",
    )

    assert "真实可调用工具以 API tools schema 为准" in prompt
    assert "reply：" not in prompt
    assert "ai_daily：" not in prompt
    assert "bash：群聊强制禁用" in prompt
    assert "no_reply(reason)" not in prompt


def test_runtime_tool_prompt_disambiguates_sql_and_memory():
    from core.runtime_tool_service import build_runtime_tool_prompt

    prompt = build_runtime_tool_prompt(
        enabled={"reply": True, "no_reply": True, "sql_analysis": True, "memory_query": True},
        disabled={},
        chat_type="private",
    )

    assert "聊天记录" in prompt
    assert "sql_analysis" in prompt
    assert "memory_query 只查询已生成的结构化摘要" in prompt
    assert "chat_logs/conversation_turns" in prompt


def test_superuser_private_tool_defaults_are_more_open():
    from core.runtime_tool_service import resolve_effective_tools

    private_enabled, _ = resolve_effective_tools(chat_type="private", runtime_preset="full")
    superuser_enabled, _ = resolve_effective_tools(chat_type="private_superuser", runtime_preset="full")

    assert private_enabled["group_analysis"] is False
    assert superuser_enabled["group_analysis"] is True
    assert superuser_enabled["bash"] is False


def test_effective_tool_schema_preview_uses_real_descriptions():
    from core.tool_schema_preview import build_effective_tool_schemas

    schemas = build_effective_tool_schemas({
        "persona_update": True,
        "python_sandbox": True,
        "schedule_task": True,
        "memory_read": True,
        "memory_query": True,
    })
    by_name = {item["function"]["name"]: item["function"] for item in schemas}

    assert "当前用户画像" in by_name["persona_update"]["description"]
    assert "schema 无参数" in by_name["persona_update"]["description"]
    assert "python_sandbox" not in by_name
    assert "memory_read" not in by_name
    assert "Asia/Shanghai" in by_name["schedule_task"]["parameters"]["properties"]["cron_expr"]["description"]
    assert by_name["schedule_task"]["parameters"]["properties"]["target_type"]["enum"] == ["private", "group"]
    assert by_name["memory_query"]["parameters"]["properties"]["run_in_background"]


def test_build_tool_plan_extra_disabled_removes_tool():
    from core.tool_plan import build_tool_plan

    reason = "定时任务会话禁用(防递归)"
    plan = build_tool_plan(
        chat_type="private",
        runtime_preset="full",
        extra_disabled={"schedule_task": reason},
    )

    assert "schedule_task" not in plan.executable_tool_names
    assert "schedule_task" not in plan.sent_tool_names
    assert plan.disabled_reason("schedule_task") == reason


def test_build_tool_plan_extra_disabled_only_disables():
    from core.tool_plan import build_tool_plan

    baseline = build_tool_plan(chat_type="private", runtime_preset="full")
    plan = build_tool_plan(
        chat_type="private",
        runtime_preset="full",
        extra_disabled={"not_a_real_tool": "无效名不引入能力"},
    )

    # 未知名字只减不增:可执行集合不得超出基线
    assert plan.executable_tool_names <= baseline.executable_tool_names
    assert "not_a_real_tool" not in plan.executable_tool_names

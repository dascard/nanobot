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


def test_final_tools_filter_removes_tool_choice_when_no_tools_remain():
    from core.final_tools import FinalToolSet, filter_payload_tools

    result = filter_payload_tools(
        {"tools": [_tool("skill")], "tool_choice": "auto"},
        FinalToolSet(allowed=set(), disabled={}),
    )

    assert "tools" not in result
    assert "tool_choice" not in result


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
    assert "no_reply(reason)" in prompt


def test_runtime_tool_prompt_disambiguates_sql_and_memory():
    from core.runtime_tool_service import build_runtime_tool_prompt

    prompt = build_runtime_tool_prompt(
        enabled={"reply": True, "no_reply": True, "sql_analysis": True, "memory_read": True},
        disabled={},
        chat_type="private",
    )

    assert "聊天记录" in prompt
    assert "sql_analysis" in prompt
    assert "memory_read 只用于长期记忆" in prompt
    assert "chat_logs/conversation_turns" in prompt


def test_superuser_private_tool_defaults_are_more_open():
    from core.runtime_tool_service import resolve_effective_tools

    private_enabled, _ = resolve_effective_tools(chat_type="private", runtime_preset="full")
    superuser_enabled, _ = resolve_effective_tools(chat_type="private_superuser", runtime_preset="full")

    assert private_enabled["group_analysis"] is False
    assert superuser_enabled["group_analysis"] is True
    assert superuser_enabled["bash"] is True


def test_effective_tool_schema_preview_uses_real_descriptions():
    from core.tool_schema_preview import build_effective_tool_schemas

    schemas = build_effective_tool_schemas({
        "persona_update": True,
        "python_sandbox": True,
        "schedule_task": True,
        "memory_read": True,
    })
    by_name = {item["function"]["name"]: item["function"] for item in schemas}

    assert "普通聊天里出现的新信息不要主动调用" in by_name["persona_update"]["description"]
    assert "简单聊天记录查询" in by_name["python_sandbox"]["description"]
    assert "Asia/Shanghai" in by_name["schedule_task"]["description"]
    assert by_name["schedule_task"]["parameters"]["properties"]["target_type"]["enum"] == ["private", "group"]
    assert by_name["memory_read"]["parameters"]["properties"]["run_in_background"]

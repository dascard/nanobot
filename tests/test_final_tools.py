import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock


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
        asyncio.run(llm._client.chat.completions.create(
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


def test_tool_policy_prompt_does_not_expand_enabled_tool_schema():
    from core.tool_policy_service import build_tool_policy_prompt

    prompt = build_tool_policy_prompt(
        enabled={"reply": True, "news_search": True, "bash": False},
        disabled={"bash": "群聊强制禁用"},
        chat_type="group",
    )

    assert "真实可调用工具以 API tools schema 为准" in prompt
    assert "reply：" not in prompt
    assert "news_search：" not in prompt
    assert "bash：群聊强制禁用" in prompt
    assert "no_reply(reason)" in prompt

import asyncio
from tests.async_helpers import run_async
from types import SimpleNamespace
from unittest.mock import AsyncMock


KT_DOC_TEXT = """业务提示保留

## Available Sub-Agents

- memory_read: Search and retrieve from memory

Sub-agents are called as tools via the API (param: `task`).

## Available Functions

**Tools:**
- `reply`: send reply
- `skill`: Invoke a procedural skill by name

Use the `info` tool for full documentation on any function.

## Skills

Procedural skills loaded for this session.

## Tool Usage

Tools are called via the API's native function calling mechanism.
You do not need to format tool calls manually.

### Background Execution

Sub-agents run in background by default.
You may ONLY call tools listed in the "Available Functions" section above.

## 业务规则

这段应该保留。
"""


def test_strip_kt_framework_tool_docs_keeps_business_system_text():
    from core.llm_request_sanitizer import strip_kt_framework_tool_docs

    messages = [
        {"role": "system", "content": KT_DOC_TEXT},
        {"role": "user", "content": "用户提到了 Available Functions，不应被改"},
    ]

    sanitized = strip_kt_framework_tool_docs(messages)

    system_text = sanitized[0]["content"]
    assert "业务提示保留" in system_text
    assert "## 业务规则" in system_text
    assert "这段应该保留" in system_text
    assert "Available Functions" not in system_text
    assert "Available Sub-Agents" not in system_text
    assert "## Skills" not in system_text
    assert "Tool Usage" not in system_text
    assert "Background Execution" not in system_text
    assert "Use the `info` tool" not in system_text
    assert sanitized[1]["content"] == "用户提到了 Available Functions，不应被改"


def test_new_api_payload_sanitizes_framework_docs():
    from clients.new_api_client import NewAPIClient

    client = NewAPIClient(api_key="key", base_url="http://newapi.test/v1")
    payload = client._build_payload(
        messages=[{"role": "system", "content": KT_DOC_TEXT}],
        tools=None,
        temperature=0,
        stream=False,
        model="test-model",
    )

    system_text = payload["messages"][0]["content"]
    assert "业务提示保留" in system_text
    assert "Available Functions" not in system_text
    assert "Background Execution" not in system_text


def test_openai_sdk_tracer_sanitizes_messages_before_request(monkeypatch):
    from core.llm_sdk_tracing import install_openai_chat_completion_tracer

    recorded = []
    original_seen = {}
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.record_request",
        staticmethod(lambda **kwargs: recorded.append(kwargs) or 2001),
    )
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.finish_request",
        staticmethod(lambda **kwargs: None),
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
    run_async(llm._client.chat.completions.create(
        model="manual-model",
        messages=[{"role": "system", "content": KT_DOC_TEXT}],
    ))

    sent_text = original_seen["messages"][0]["content"]
    logged_text = recorded[0]["request"]["messages"][0]["content"]
    assert "Available Functions" not in sent_text
    assert "Tool Usage" not in sent_text
    assert "Available Functions" not in logged_text
    assert "Tool Usage" not in logged_text

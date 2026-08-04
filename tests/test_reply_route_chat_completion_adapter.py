from __future__ import annotations

import json

import pytest

from clients.reply_route_chat_completion_adapter import (
    ReplyRouteChatCompletionAdapter,
    ReplyRouteUnavailableError,
)
from core.model_provider import ChatCompletionRequest, ReplyRoutePlan


class _FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_any(self):
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        payload: dict | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status = status
        self._body = json.dumps(payload or {}).encode("utf-8")
        self.content = _FakeContent(chunks or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def read(self) -> bytes:
        return self._body


class _FakeSession:
    closed = False

    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.response


def _route(**overrides) -> ReplyRoutePlan:
    values = {
        "provider_id": "team_gateway",
        "registry_provider": "team-gateway",
        "base_url": "http://model.test/v1",
        "api_key": "secret-key",
        "timeout": 30.0,
        "profile_id": "reply-main",
        "model": "model-a",
        "capabilities": {
            "supports_stream": True,
            "supports_tools": True,
            "supports_image": True,
        },
    }
    values.update(overrides)
    return ReplyRoutePlan(**values)


def _request(*, stream: bool = False) -> ChatCompletionRequest:
    del stream
    return ChatCompletionRequest(
        messages=({"role": "user", "content": "你好"},),
        tools=({
            "type": "function",
            "function": {
                "name": "reply",
                "parameters": {"type": "object"},
            },
        },),
        manual_model="model-a",
        max_tokens=512,
        trace_source="native_agent",
    )


def test_reply_route_adapter_rejects_unsupported_or_unsafe_route():
    adapter = ReplyRouteChatCompletionAdapter()

    with pytest.raises(ReplyRouteUnavailableError, match="anthropic"):
        adapter.bind_route(_route(driver_type="anthropic"))
    with pytest.raises(ReplyRouteUnavailableError, match="Provider 原生工具"):
        adapter.bind_route(_route(provider_native_tools=("web_search",)))
    with pytest.raises(ReplyRouteUnavailableError, match="受控字段"):
        adapter.bind_route(_route(extra_body={"tools": []}))
    with pytest.raises(ReplyRouteUnavailableError, match="driver_options"):
        adapter.bind_route(_route(driver_options={"unknown": True}))


@pytest.mark.asyncio
async def test_reply_route_adapter_sends_frozen_openai_payload_and_keeps_raw_tool_calls():
    response = _FakeResponse(payload={
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "reply", "arguments": "{}"},
                }],
            }
        }],
        "usage": {"prompt_tokens": 4, "completion_tokens": 2},
    })
    session = _FakeSession(response)
    adapter = ReplyRouteChatCompletionAdapter(session_provider=lambda: session)
    adapter.bind_route(_route(
        reasoning_effort="high",
        service_tier="priority",
        extra_headers={"X-Route": "main"},
        extra_body={"parallel_tool_calls": False},
        cost_input_1m=0.5,
        cost_output_1m=2.0,
    ))

    result = await adapter.complete_chat(_request())

    assert result["choices"][0]["message"]["tool_calls"][0]["id"] == "call-1"
    assert result["usage"]["cost_microunits"] == 6
    call = session.calls[0]
    assert call["url"] == "http://model.test/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer secret-key"
    assert call["headers"]["X-Route"] == "main"
    assert call["json"]["model"] == "model-a"
    assert call["json"]["reasoning_effort"] == "high"
    assert call["json"]["service_tier"] == "priority"
    assert call["json"]["parallel_tool_calls"] is False
    assert call["json"]["tools"][0]["function"]["name"] == "reply"


@pytest.mark.asyncio
async def test_reply_route_adapter_stream_decodes_fragmented_sse_and_http_error():
    chunks = [
        b'data: {"choices":[{"delta":{"content":"\xe4\xbd',
        b'\xa0\xe5\xa5\xbd"}}]}\n\n',
        b'data: {"usage":{"prompt_tokens":3,"completion_tokens":1},"choices":[]}\n\n',
        b"data: [DONE]\n\n",
    ]
    stream_session = _FakeSession(_FakeResponse(chunks=chunks))
    adapter = ReplyRouteChatCompletionAdapter(
        session_provider=lambda: stream_session
    )
    adapter.bind_route(_route())

    events = [event async for event in adapter.stream_chat(_request())]

    assert events[0]["choices"][0]["delta"]["content"] == "你好"
    assert events[1]["usage"]["prompt_tokens"] == 3

    error_session = _FakeSession(_FakeResponse(status=401))
    adapter = ReplyRouteChatCompletionAdapter(
        session_provider=lambda: error_session
    )
    adapter.bind_route(_route())
    error = await adapter.complete_chat(_request())
    assert error["error"]["code"] == "http_401"
    assert "unauthorized" in error["error"]["message"]


@pytest.mark.asyncio
async def test_reply_route_adapter_records_native_provider_stream_metrics(
    monkeypatch,
):
    recorded: list[dict] = []
    finished: list[dict] = []
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.record_request",
        staticmethod(lambda **kwargs: recorded.append(kwargs) or 91),
    )
    monkeypatch.setattr(
        "core.tracing.LLMRequestTracer.finish_request",
        staticmethod(lambda **kwargs: finished.append(kwargs)),
    )
    chunks = [
        b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
        b'data: {"usage":{"prompt_tokens":10,"completion_tokens":2},'
        b'"choices":[]}\n\n',
        b"data: [DONE]\n\n",
    ]
    session = _FakeSession(_FakeResponse(chunks=chunks))
    adapter = ReplyRouteChatCompletionAdapter(session_provider=lambda: session)
    adapter.bind_route(_route())
    request = ChatCompletionRequest(
        messages=({"role": "user", "content": "你好"},),
        manual_model="model-a",
        trace_id="trace-native",
        run_id="run-native",
        trace_source="native_agent",
    )

    events = [event async for event in adapter.stream_chat(request)]

    assert len(events) == 2
    assert recorded[0]["trace_id"] == "trace-native"
    assert recorded[0]["run_id"] == "run-native"
    assert recorded[0]["provider"] == "team_gateway"
    assert recorded[0]["model"] == "model-a"
    assert finished[0]["log_id"] == 91
    assert finished[0]["status"] == "stream_success"
    assert finished[0]["response"]["usage"]["prompt_tokens"] == 10
    assert finished[0]["response"]["stream_metrics"]["first_content_ms"] >= 0


@pytest.mark.asyncio
async def test_reply_route_adapter_fails_when_request_model_differs_from_preset():
    adapter = ReplyRouteChatCompletionAdapter()
    adapter.bind_route(_route())
    request = ChatCompletionRequest(
        messages=({"role": "user", "content": "你好"},),
        manual_model="model-b",
    )

    with pytest.raises(ReplyRouteUnavailableError, match="不一致"):
        await adapter.complete_chat(request)

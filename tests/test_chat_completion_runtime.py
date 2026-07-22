"""原始 Chat Completion Port、Adapter 与生命周期合同。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest

from clients.chat_completion_adapter import NewAPIChatCompletionAdapter
from core.model_provider.chat_runtime import (
    ChatCompletionPort,
    ChatCompletionRequest,
    ChatCompletionRuntime,
    ChatCompletionRuntimeState,
)


class _FakePort:
    @property
    def adapter_id(self) -> str:
        return "fake_chat"

    async def complete_chat(
        self,
        request: ChatCompletionRequest,
    ) -> Mapping[str, Any]:
        return {"manual_model": request.manual_model}

    async def stream_chat(
        self,
        request: ChatCompletionRequest,
    ) -> AsyncIterator[Mapping[str, Any]]:
        yield {"trace_source": request.trace_source}


@pytest.mark.asyncio
async def test_chat_completion_runtime_has_explicit_fail_closed_lifecycle():
    runtime = ChatCompletionRuntime()
    request = ChatCompletionRequest(
        messages=({"role": "user", "content": "测试"},),
        manual_model="model-a",
        trace_source="contract-test",
    )

    assert runtime.state is ChatCompletionRuntimeState.NEW
    with pytest.raises(RuntimeError, match="尚未启动"):
        await runtime.complete(request)

    port = _FakePort()
    assert isinstance(port, ChatCompletionPort)
    runtime.start(port)
    runtime.start(port)

    assert await runtime.complete(request) == {"manual_model": "model-a"}
    assert [chunk async for chunk in runtime.stream(request)] == [
        {"trace_source": "contract-test"}
    ]
    assert runtime.introspect() == {
        "state": "running",
        "adapter_id": "fake_chat",
    }

    with pytest.raises(RuntimeError, match="其他 Adapter"):
        runtime.start(_FakePort())

    runtime.stop()
    assert runtime.state is ChatCompletionRuntimeState.STOPPED
    with pytest.raises(RuntimeError, match="已经停止"):
        await runtime.complete(request)


@pytest.mark.asyncio
async def test_new_api_chat_adapter_maps_raw_and_stream_requests():
    calls: list[tuple[str, dict[str, Any]]] = []

    class _Client:
        async def chat_completion(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(("complete", kwargs))
            return {"choices": [{"message": {"content": "完成"}}]}

        async def chat_completion_stream(self, **kwargs: Any):
            calls.append(("stream", kwargs))
            yield {"choices": [{"delta": {"content": "完"}}]}

    adapter = NewAPIChatCompletionAdapter(_Client())  # type: ignore[arg-type]
    request = ChatCompletionRequest(
        messages=({"role": "user", "content": "测试"},),
        tools=({"type": "function", "function": {"name": "lookup"}},),
        temperature=0.2,
        model_tier="smart",
        manual_model="model-a",
        max_tokens=1200,
        trace_id="trace-1",
        run_id="run-1",
        trace_source="agent_step",
        enable_thinking="false",
    )

    response = await adapter.complete_chat(request)
    chunks = [chunk async for chunk in adapter.stream_chat(request)]

    assert response["choices"][0]["message"]["content"] == "完成"
    assert chunks[0]["choices"][0]["delta"]["content"] == "完"
    assert [kind for kind, _ in calls] == ["complete", "stream"]
    for _, kwargs in calls:
        assert kwargs["manual_model"] == "model-a"
        assert kwargs["llm_source"] == "agent_step"
        assert kwargs["tools"][0]["function"]["name"] == "lookup"
        assert kwargs["max_tokens"] == 1200


def test_chat_completion_request_freezes_top_level_payloads():
    message = {"role": "user", "content": "原始"}
    request = ChatCompletionRequest(messages=(message,))
    message["content"] = "篡改"

    assert request.messages[0]["content"] == "原始"
    with pytest.raises(TypeError):
        request.messages[0]["content"] = "非法"  # type: ignore[index]

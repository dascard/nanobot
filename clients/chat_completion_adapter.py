"""把 NewAPI transport 适配为核心 ChatCompletionPort。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from clients.new_api_client import NewAPIClient
from core.model_provider.chat_runtime import ChatCompletionRequest


class NewAPIChatCompletionAdapter:
    """保留原始 tool_calls/stream delta 的生产 Adapter。"""

    def __init__(self, client: NewAPIClient) -> None:
        self._client = client

    @property
    def adapter_id(self) -> str:
        return "new_api_chat_completion"

    @staticmethod
    def _kwargs(request: ChatCompletionRequest) -> dict[str, Any]:
        return {
            "messages": [dict(message) for message in request.messages],
            "tools": (
                [dict(tool) for tool in request.tools]
                if request.tools is not None
                else None
            ),
            "temperature": request.temperature,
            "model_tier": request.model_tier,
            "manual_model": request.manual_model,
            "max_tokens": request.max_tokens,
            "trace_id": request.trace_id,
            "run_id": request.run_id,
            "llm_source": request.trace_source,
            "enable_thinking": request.enable_thinking,
        }

    async def complete_chat(
        self,
        request: ChatCompletionRequest,
    ) -> Mapping[str, Any]:
        return await self._client.chat_completion(**self._kwargs(request))

    async def stream_chat(
        self,
        request: ChatCompletionRequest,
    ) -> AsyncIterator[Mapping[str, Any]]:
        async for chunk in self._client.chat_completion_stream(
            **self._kwargs(request),
        ):
            yield chunk


__all__ = ["NewAPIChatCompletionAdapter"]

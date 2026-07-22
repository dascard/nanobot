"""原始 Chat Completions 的 Port 与显式进程生命周期。

该合同保留 OpenAI-compatible 原始响应所需的 tool_calls 和 streaming delta，
但不依赖具体 HTTP 客户端、模型目录或配置来源。生产 Adapter 由 bootstrap 注入。
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable


class ChatCompletionRuntimeState(StrEnum):
    NEW = "new"
    RUNNING = "running"
    STOPPED = "stopped"


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class ChatCompletionRequest:
    """业务层可提交的原始 Chat Completions 请求。"""

    messages: tuple[Mapping[str, Any], ...]
    tools: tuple[Mapping[str, Any], ...] | None = None
    temperature: float = 0.7
    model_tier: str = "smart"
    manual_model: str = ""
    max_tokens: int | None = None
    trace_id: str = ""
    run_id: str = ""
    trace_source: str = ""
    enable_thinking: Any = "auto"

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("chat completion messages 不能为空")
        if self.max_tokens is not None and self.max_tokens <= 0:
            raise ValueError("chat completion max_tokens 必须大于 0")
        object.__setattr__(
            self,
            "messages",
            tuple(_freeze_mapping(message) for message in self.messages),
        )
        if self.tools is not None:
            object.__setattr__(
                self,
                "tools",
                tuple(_freeze_mapping(tool) for tool in self.tools),
            )
        object.__setattr__(self, "model_tier", str(self.model_tier or "smart").strip())
        object.__setattr__(self, "manual_model", str(self.manual_model or "").strip())
        object.__setattr__(self, "trace_id", str(self.trace_id or "").strip())
        object.__setattr__(self, "run_id", str(self.run_id or "").strip())
        object.__setattr__(self, "trace_source", str(self.trace_source or "").strip())


@runtime_checkable
class ChatCompletionPort(Protocol):
    """原始非流式及流式模型响应能力。"""

    @property
    def adapter_id(self) -> str: ...

    async def complete_chat(
        self,
        request: ChatCompletionRequest,
    ) -> Mapping[str, Any]: ...

    def stream_chat(
        self,
        request: ChatCompletionRequest,
    ) -> AsyncIterator[Mapping[str, Any]]: ...


class ChatCompletionRuntime:
    """由应用启动和停止的 Chat Completion 组合运行时。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = ChatCompletionRuntimeState.NEW
        self._port: ChatCompletionPort | None = None

    @property
    def state(self) -> ChatCompletionRuntimeState:
        with self._lock:
            return self._state

    def start(self, port: ChatCompletionPort) -> None:
        if not isinstance(port, ChatCompletionPort):
            raise TypeError("port 未实现 ChatCompletionPort")
        with self._lock:
            if self._state is ChatCompletionRuntimeState.RUNNING:
                if self._port is port:
                    return
                raise RuntimeError("Chat Completion 运行时已由其他 Adapter 启动")
            self._port = port
            self._state = ChatCompletionRuntimeState.RUNNING

    def stop(self) -> None:
        with self._lock:
            self._port = None
            self._state = ChatCompletionRuntimeState.STOPPED

    def _require_port(self) -> ChatCompletionPort:
        with self._lock:
            if self._state is not ChatCompletionRuntimeState.RUNNING:
                raise RuntimeError("Chat Completion 运行时尚未启动或已经停止")
            port = self._port
        if port is None:
            raise RuntimeError("Chat Completion Adapter 未配置")
        return port

    async def complete(
        self,
        request: ChatCompletionRequest,
    ) -> dict[str, Any]:
        response = await self._require_port().complete_chat(request)
        if not isinstance(response, Mapping):
            raise TypeError("Chat Completion Adapter 返回了无效响应合同")
        return dict(response)

    async def stream(
        self,
        request: ChatCompletionRequest,
    ) -> AsyncIterator[dict[str, Any]]:
        stream = self._require_port().stream_chat(request)
        async for chunk in stream:
            if not isinstance(chunk, Mapping):
                raise TypeError("Chat Completion Adapter 返回了无效流式响应合同")
            yield dict(chunk)

    def introspect(self) -> dict[str, object]:
        with self._lock:
            return {
                "state": self._state.value,
                "adapter_id": (
                    str(self._port.adapter_id) if self._port is not None else ""
                ),
            }


_CHAT_COMPLETION_RUNTIME = ChatCompletionRuntime()


def start_chat_completion_runtime(port: ChatCompletionPort) -> None:
    _CHAT_COMPLETION_RUNTIME.start(port)


def stop_chat_completion_runtime() -> None:
    _CHAT_COMPLETION_RUNTIME.stop()


def chat_completion_runtime_status() -> dict[str, object]:
    return _CHAT_COMPLETION_RUNTIME.introspect()


class RuntimeChatCompletionClient:
    """旧调用签名的兼容 façade；实际能力来自启动期注入的 Port。"""

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        timeout: int | None = None,
        **_kwargs: Any,
    ) -> None:
        # 兼容参数不落盘、不进入诊断；生产连接配置只由 composition root 持有。
        del api_key, base_url, timeout

    @staticmethod
    def _request(
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        model_tier: str = "smart",
        manual_model: str = "",
        max_tokens: int | None = None,
        trace_id: str = "",
        run_id: str = "",
        llm_source: str = "",
        enable_thinking: Any = "auto",
        **_kwargs: Any,
    ) -> ChatCompletionRequest:
        return ChatCompletionRequest(
            messages=tuple(messages),
            tools=tuple(tools) if tools is not None else None,
            temperature=temperature,
            model_tier=model_tier,
            manual_model=manual_model,
            max_tokens=max_tokens,
            trace_id=trace_id,
            run_id=run_id,
            trace_source=llm_source,
            enable_thinking=enable_thinking,
        )

    async def chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        return await _CHAT_COMPLETION_RUNTIME.complete(self._request(**kwargs))

    async def chat_completion_stream(
        self,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        async for chunk in _CHAT_COMPLETION_RUNTIME.stream(self._request(**kwargs)):
            yield chunk


__all__ = [
    "ChatCompletionPort",
    "ChatCompletionRequest",
    "ChatCompletionRuntime",
    "ChatCompletionRuntimeState",
    "RuntimeChatCompletionClient",
    "chat_completion_runtime_status",
    "start_chat_completion_runtime",
    "stop_chat_completion_runtime",
]

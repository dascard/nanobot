"""按业务 route_key 调用模型的显式运行时 Port。

领域模块只依赖本文件的请求合同；具体配置解析和 HTTP Adapter 由 bootstrap 在启动
阶段注入。运行时停止后调用会 fail-closed，避免后台线程在关停后隐式重建客户端。
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from core.model_provider.contracts import ModelProviderResponse
from core.model_provider.route_registry import resolve_model_route_key


class RouteModelRuntimeState(StrEnum):
    NEW = "new"
    RUNNING = "running"
    STOPPED = "stopped"


class RouteModelRuntimeUnavailableError(RuntimeError):
    """路由模型组合根尚未启动、已停止或未安装 Adapter。"""


@dataclass(frozen=True, slots=True)
class RouteModelRequest:
    route_key: str
    messages: tuple[Mapping[str, Any], ...] = ()
    system_prompt: str = ""
    user_message: str = ""
    max_tokens: int | None = None
    temperature: float | None = None
    timeout_seconds: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        route_key = str(self.route_key or "").strip()
        if not route_key:
            raise ValueError("route_key 不能为空")
        object.__setattr__(
            self,
            "route_key",
            resolve_model_route_key(route_key),
        )
        object.__setattr__(
            self,
            "messages",
            tuple(dict(message) for message in self.messages),
        )
        object.__setattr__(self, "metadata", dict(self.metadata))


@runtime_checkable
class RouteModelCompletionPort(Protocol):
    """由外部 Adapter 实现的 route_key 模型生成端口。"""

    @property
    def adapter_id(self) -> str: ...

    def complete_route(self, request: RouteModelRequest) -> ModelProviderResponse: ...


class RouteModelRuntime:
    """单进程模型路由组合根，生命周期由应用 bootstrap 显式持有。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = RouteModelRuntimeState.NEW
        self._port: RouteModelCompletionPort | None = None

    @property
    def state(self) -> RouteModelRuntimeState:
        with self._lock:
            return self._state

    def start(self, port: RouteModelCompletionPort) -> None:
        if not isinstance(port, RouteModelCompletionPort):
            raise TypeError("port 未实现 RouteModelCompletionPort")
        with self._lock:
            if self._state is RouteModelRuntimeState.RUNNING:
                if self._port is port:
                    return
                raise RuntimeError("模型路由运行时已由其他 Adapter 启动")
            self._port = port
            self._state = RouteModelRuntimeState.RUNNING

    def stop(self) -> None:
        with self._lock:
            self._port = None
            self._state = RouteModelRuntimeState.STOPPED

    def complete(self, request: RouteModelRequest) -> ModelProviderResponse:
        with self._lock:
            if self._state is not RouteModelRuntimeState.RUNNING:
                raise RouteModelRuntimeUnavailableError(
                    "模型路由运行时尚未启动或已经停止"
                )
            port = self._port
        if port is None:
            raise RouteModelRuntimeUnavailableError(
                "模型路由 Adapter 未配置"
            )
        response = port.complete_route(request)
        if not isinstance(response, ModelProviderResponse):
            raise TypeError("模型路由 Adapter 返回了无效响应合同")
        return response

    def introspect(self) -> dict[str, object]:
        with self._lock:
            return {
                "state": self._state.value,
                "adapter_id": (
                    str(self._port.adapter_id) if self._port is not None else ""
                ),
            }


_ROUTE_MODEL_RUNTIME = RouteModelRuntime()


def start_route_model_runtime(port: RouteModelCompletionPort) -> None:
    _ROUTE_MODEL_RUNTIME.start(port)


def stop_route_model_runtime() -> None:
    _ROUTE_MODEL_RUNTIME.stop()


def route_model_runtime_status() -> dict[str, object]:
    return _ROUTE_MODEL_RUNTIME.introspect()


def call_model_route_response(
    route_key: str = "timing_gate",
    messages: list[dict[str, Any]] | None = None,
    *,
    system_prompt: str = "",
    user_message: str = "",
    max_tokens: int | None = None,
    temperature: float | None = None,
    timeout: float | None = None,
) -> ModelProviderResponse:
    """兼容现有领域调用签名，并委托给启动期注入的 Adapter。"""

    return _ROUTE_MODEL_RUNTIME.complete(RouteModelRequest(
        route_key=route_key,
        messages=tuple(messages or ()),
        system_prompt=system_prompt,
        user_message=user_message,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_seconds=timeout,
    ))


__all__ = [
    "RouteModelCompletionPort",
    "RouteModelRequest",
    "RouteModelRuntime",
    "RouteModelRuntimeState",
    "RouteModelRuntimeUnavailableError",
    "call_model_route_response",
    "route_model_runtime_status",
    "start_route_model_runtime",
    "stop_route_model_runtime",
]

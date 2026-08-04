"""框架无关的确定性工具执行 binding 调度器。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import inspect
from types import MappingProxyType

from core.agent_runtime.contracts import (
    RuntimeToolExecutionRequest,
    RuntimeToolExecutionResult,
)
from core.agent_runtime.errors import AgentRuntimeCapabilityError


ToolExecutionHandler = Callable[
    [RuntimeToolExecutionRequest],
    RuntimeToolExecutionResult | Awaitable[RuntimeToolExecutionResult],
]


class RegisteredToolExecutionPort:
    """只按显式 port ID 分派，不发现框架 registry 或隐式回退。"""

    def __init__(
        self,
        bindings: Mapping[str, ToolExecutionHandler],
        *,
        port_id: str = "tool-execution:registered",
    ) -> None:
        normalized_port_id = str(port_id or "").strip()
        if not normalized_port_id:
            raise ValueError("ToolExecutionPort.port_id 不能为空")
        normalized: dict[str, ToolExecutionHandler] = {}
        for binding_id, handler in bindings.items():
            key = str(binding_id or "").strip()
            if not key:
                raise ValueError("工具 execution binding ID 不能为空")
            if not callable(handler):
                raise TypeError(f"工具 execution binding {key} 不可调用")
            if key in normalized:
                raise ValueError(f"工具 execution binding 重复：{key}")
            normalized[key] = handler
        self._port_id = normalized_port_id
        self._bindings = MappingProxyType(normalized)

    @property
    def port_id(self) -> str:
        return self._port_id

    @property
    def binding_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._bindings))

    async def execute(
        self,
        request: RuntimeToolExecutionRequest,
    ) -> RuntimeToolExecutionResult:
        if not isinstance(request, RuntimeToolExecutionRequest):
            raise TypeError("request 必须是 RuntimeToolExecutionRequest")
        handler = self._bindings.get(request.execution_port_id)
        if handler is None:
            raise AgentRuntimeCapabilityError(
                f"工具 execution binding 未注册：{request.execution_port_id}",
                runtime_id=self.port_id,
            )

        async def invoke() -> RuntimeToolExecutionResult:
            result = handler(request)
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, RuntimeToolExecutionResult):
                raise TypeError("工具 execution handler 返回了无效结果")
            if result.tool_call_id != request.tool_call.call_id:
                raise ValueError("工具 execution result 的 tool_call_id 不匹配")
            return result

        return await asyncio.wait_for(
            invoke(),
            timeout=request.timeout_seconds,
        )


__all__ = [
    "RegisteredToolExecutionPort",
    "ToolExecutionHandler",
]

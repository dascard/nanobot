"""业务入口使用的框架无关 Agent Gateway Port。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AgentMessageGatewayPort(Protocol):
    """处理一个已由业务层组装完成的消息。"""

    async def handle_message(
        self,
        content: str,
        *,
        user_id: str = "",
        session_id: str = "",
        sender_name: str = "",
        metadata: Mapping[str, Any] | None = None,
        stream_queue: Any = None,
        stream: bool = False,
    ) -> Any: ...


@runtime_checkable
class ManagedAgentGatewayPort(AgentMessageGatewayPort, Protocol):
    """可独立创建和释放的消息 Gateway。"""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


@runtime_checkable
class ResearchAgentRuntimePort(ManagedAgentGatewayPort, Protocol):
    """主动研究所需的最小守卫能力，不暴露 Agent/Plugin 私有对象。"""

    def research_tool_guards_ready(self) -> bool: ...

    def install_research_budget_guard(self, guard: object) -> bool: ...


__all__ = [
    "AgentMessageGatewayPort",
    "ManagedAgentGatewayPort",
    "ResearchAgentRuntimePort",
]

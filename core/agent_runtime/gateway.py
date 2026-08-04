"""Composition Root 管理的进程级 Agent Gateway 绑定。

业务模块只依赖这里的框架无关访问面。KT 的共享 Bridge 和隔离实例工厂由
``bootstrap`` 在 ``runtime.agent`` 模块启动成功后绑定，并在关停前清除。
"""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock
from typing import TypeVar, cast

from core.agent_runtime.errors import AgentRuntimeStateError
from core.agent_runtime.gateway_contracts import (
    AgentMessageGatewayPort,
    ManagedAgentGatewayPort,
    ResearchAgentRuntimePort,
)


AgentGatewayProvider = Callable[[], AgentMessageGatewayPort | None]
IsolatedAgentGatewayFactory = Callable[[], ManagedAgentGatewayPort | None]
ResearchAgentRuntimeFactory = Callable[[], ResearchAgentRuntimePort | None]
GatewayPortT = TypeVar("GatewayPortT")


_lock = RLock()
_gateway_provider: AgentGatewayProvider | None = None
_isolated_gateway_factory: IsolatedAgentGatewayFactory | None = None
_research_runtime_factory: ResearchAgentRuntimeFactory | None = None


def bind_agent_runtime(
    *,
    gateway_provider: AgentGatewayProvider,
    isolated_gateway_factory: IsolatedAgentGatewayFactory,
    research_runtime_factory: ResearchAgentRuntimeFactory | None = None,
) -> None:
    """原子绑定共享 Gateway 与隔离实例工厂；禁止静默覆盖。"""

    if not callable(gateway_provider):
        raise TypeError("gateway_provider 必须可调用")
    if not callable(isolated_gateway_factory):
        raise TypeError("isolated_gateway_factory 必须可调用")
    resolved_research_factory = research_runtime_factory or isolated_gateway_factory
    if not callable(resolved_research_factory):
        raise TypeError("research_runtime_factory 必须可调用")
    global _gateway_provider
    global _isolated_gateway_factory
    global _research_runtime_factory
    with _lock:
        if (
            _gateway_provider is not None
            or _isolated_gateway_factory is not None
            or _research_runtime_factory is not None
        ):
            raise AgentRuntimeStateError(
                "Agent Runtime 已绑定，禁止隐式替换",
                runtime_id="process-agent-gateway",
            )
        _gateway_provider = gateway_provider
        _isolated_gateway_factory = isolated_gateway_factory
        _research_runtime_factory = resolved_research_factory


def clear_agent_runtime_bindings() -> None:
    """幂等清除绑定，使后续读取立即 fail closed。"""

    global _gateway_provider
    global _isolated_gateway_factory
    global _research_runtime_factory
    with _lock:
        _gateway_provider = None
        _isolated_gateway_factory = None
        _research_runtime_factory = None


def agent_runtime_binding_state() -> str:
    with _lock:
        bound = (
            _gateway_provider is not None
            and _isolated_gateway_factory is not None
            and _research_runtime_factory is not None
        )
    return "running" if bound else "stopped"


def _resolve(
    factory: Callable[[], object | None] | None,
    expected_type: type[GatewayPortT],
    *,
    port_name: str,
) -> GatewayPortT:
    if factory is None:
        raise AgentRuntimeStateError(
            "Agent Gateway 当前不可用",
            runtime_id="process-agent-gateway",
        )
    try:
        value = factory()
    except AgentRuntimeStateError:
        raise
    except Exception as exc:
        raise AgentRuntimeStateError(
            "Agent Gateway 当前不可用",
            runtime_id="process-agent-gateway",
        ) from exc
    if value is None:
        raise AgentRuntimeStateError(
            "Agent Gateway 当前不可用",
            runtime_id="process-agent-gateway",
        )
    if not isinstance(value, expected_type):
        raise AgentRuntimeStateError(
            f"{port_name} 未实现所需 Port",
            runtime_id="process-agent-gateway",
        )
    return cast(GatewayPortT, value)


def get_agent_gateway() -> AgentMessageGatewayPort:
    with _lock:
        provider = _gateway_provider
    return _resolve(
        provider,
        AgentMessageGatewayPort,
        port_name="Agent Message Gateway",
    )


def create_isolated_agent_gateway() -> ManagedAgentGatewayPort:
    with _lock:
        factory = _isolated_gateway_factory
    return _resolve(
        factory,
        ManagedAgentGatewayPort,
        port_name="Isolated Agent Gateway",
    )


def create_research_agent_runtime() -> ResearchAgentRuntimePort:
    with _lock:
        factory = _research_runtime_factory
    return _resolve(
        factory,
        ResearchAgentRuntimePort,
        port_name="Research Agent Runtime",
    )


__all__ = [
    "AgentGatewayProvider",
    "IsolatedAgentGatewayFactory",
    "ResearchAgentRuntimeFactory",
    "agent_runtime_binding_state",
    "bind_agent_runtime",
    "clear_agent_runtime_bindings",
    "create_isolated_agent_gateway",
    "create_research_agent_runtime",
    "get_agent_gateway",
]

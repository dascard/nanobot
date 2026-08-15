"""Composition Root 管理的进程级 Agent Gateway 绑定。

业务模块只依赖这里的框架无关访问面。KT 的共享 Bridge 和隔离实例工厂由
``bootstrap`` 在 ``runtime.agent`` 模块启动成功后绑定，并在关停前清除。
"""

from __future__ import annotations

from collections.abc import Callable
import hashlib
from threading import RLock
from typing import TypeVar, cast

from core.agent_runtime.errors import AgentRuntimeStateError
from core.agent_runtime.gateway_contracts import (
    AgentMessageGatewayPort,
    ManagedAgentGatewayPort,
    ResearchAgentRuntimePort,
)
from core.agent_runtime.registry import (
    AgentGatewayProvider,
    AgentRuntimeDescriptor,
    AgentRuntimeRegistration,
    AgentRuntimeRegistry,
    IsolatedAgentGatewayFactory,
    ResearchAgentRuntimeFactory,
)


GatewayPortT = TypeVar("GatewayPortT")


_lock = RLock()
_runtime_registry: AgentRuntimeRegistry | None = None


def _compatibility_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def bind_agent_runtime_registry(registry: AgentRuntimeRegistry) -> None:
    """原子绑定完整冻结注册表；禁止静默覆盖。"""

    if not isinstance(registry, AgentRuntimeRegistry):
        raise TypeError("registry 必须是 AgentRuntimeRegistry")
    global _runtime_registry
    with _lock:
        if _runtime_registry is not None:
            raise AgentRuntimeStateError(
                "Agent Runtime 已绑定，禁止隐式替换",
                runtime_id="process-agent-gateway",
            )
        _runtime_registry = registry


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
    descriptor = AgentRuntimeDescriptor(
        agent_id="nanobot",
        display_name="Nanobot",
        description="兼容单 Agent Composition Root",
        adapter="compatibility",
        source_ref="process-agent-gateway",
        source_sha256=_compatibility_sha256("nanobot:compatibility:source"),
        runtime_policy_sha256=_compatibility_sha256(
            "nanobot:compatibility:runtime-policy"
        ),
        allowed_entrypoints=(
            "agent_link",
            "chat",
            "research",
            "scheduled",
        ),
        default=True,
    )
    bind_agent_runtime_registry(AgentRuntimeRegistry.build((
        AgentRuntimeRegistration(
            descriptor=descriptor,
            gateway_provider=gateway_provider,
            isolated_gateway_factory=isolated_gateway_factory,
            research_runtime_factory=resolved_research_factory,
        ),
    )))


def clear_agent_runtime_bindings() -> None:
    """幂等清除绑定，使后续读取立即 fail closed。"""

    global _runtime_registry
    with _lock:
        _runtime_registry = None


def agent_runtime_binding_state() -> str:
    with _lock:
        bound = _runtime_registry is not None
    return "running" if bound else "stopped"


def _resolve(
    factory: Callable[[], object | None] | None,
    expected_type: type[GatewayPortT],
    *,
    port_name: str,
    runtime_id: str,
) -> GatewayPortT:
    if factory is None:
        raise AgentRuntimeStateError(
            "Agent Gateway 当前不可用",
            runtime_id=runtime_id,
        )
    try:
        value = factory()
    except AgentRuntimeStateError:
        raise
    except Exception as exc:
        raise AgentRuntimeStateError(
            "Agent Gateway 当前不可用",
            runtime_id=runtime_id,
        ) from exc
    if value is None:
        raise AgentRuntimeStateError(
            "Agent Gateway 当前不可用",
            runtime_id=runtime_id,
        )
    if not isinstance(value, expected_type):
        raise AgentRuntimeStateError(
            f"{port_name} 未实现所需 Port",
            runtime_id=runtime_id,
        )
    return cast(GatewayPortT, value)


def _require_registry() -> AgentRuntimeRegistry:
    with _lock:
        registry = _runtime_registry
    if registry is None:
        raise AgentRuntimeStateError(
            "Agent Gateway 当前不可用",
            runtime_id="process-agent-gateway",
        )
    return registry


def get_agent_gateway(
    agent_id: str = "",
    *,
    entrypoint: str = "chat",
) -> AgentMessageGatewayPort:
    registry = _require_registry()
    registration = registry.require_registration(
        agent_id,
        entrypoint=entrypoint,
    )
    return _resolve(
        registration.gateway_provider,
        AgentMessageGatewayPort,
        port_name="Agent Message Gateway",
        runtime_id=registration.descriptor.agent_id,
    )


def create_isolated_agent_gateway(
    agent_id: str = "",
    *,
    entrypoint: str = "scheduled",
) -> ManagedAgentGatewayPort:
    registry = _require_registry()
    registration = registry.require_registration(
        agent_id,
        entrypoint=entrypoint,
    )
    return _resolve(
        registration.isolated_gateway_factory,
        ManagedAgentGatewayPort,
        port_name="Isolated Agent Gateway",
        runtime_id=registration.descriptor.agent_id,
    )


def create_research_agent_runtime(
    agent_id: str = "",
) -> ResearchAgentRuntimePort:
    registry = _require_registry()
    registration = registry.require_registration(
        agent_id,
        entrypoint="research",
    )
    factory = registration.research_runtime_factory
    if factory is None:
        raise AgentRuntimeStateError(
            "Research Agent Runtime 当前不可用",
            runtime_id=registration.descriptor.agent_id,
        )
    return _resolve(
        factory,
        ResearchAgentRuntimePort,
        port_name="Research Agent Runtime",
        runtime_id=registration.descriptor.agent_id,
    )


def list_registered_agents() -> tuple[AgentRuntimeDescriptor, ...]:
    return _require_registry().descriptors()


def get_agent_runtime_registry() -> AgentRuntimeRegistry:
    """返回当前冻结注册表，供诊断和受信 Composition Root 使用。"""

    return _require_registry()


__all__ = [
    "AgentGatewayProvider",
    "IsolatedAgentGatewayFactory",
    "ResearchAgentRuntimeFactory",
    "agent_runtime_binding_state",
    "bind_agent_runtime",
    "bind_agent_runtime_registry",
    "clear_agent_runtime_bindings",
    "create_isolated_agent_gateway",
    "create_research_agent_runtime",
    "get_agent_runtime_registry",
    "get_agent_gateway",
    "list_registered_agents",
]

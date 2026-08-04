from __future__ import annotations

import pytest


class _MessageGateway:
    async def handle_message(self, content: str, **kwargs):
        del kwargs
        return content


class _ManagedGateway(_MessageGateway):
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class _ResearchGateway(_ManagedGateway):
    def research_tool_guards_ready(self) -> bool:
        return True

    def install_research_budget_guard(self, guard: object) -> bool:
        del guard
        return True


@pytest.fixture(autouse=True)
def _clear_gateway_binding():
    from core.agent_runtime.gateway import clear_agent_runtime_bindings

    clear_agent_runtime_bindings()
    yield
    clear_agent_runtime_bindings()


def test_gateway_binding_is_explicit_and_fail_closed():
    from core.agent_runtime import AgentRuntimeStateError
    from core.agent_runtime.gateway import (
        agent_runtime_binding_state,
        bind_agent_runtime,
        create_isolated_agent_gateway,
        get_agent_gateway,
    )

    shared = _MessageGateway()
    isolated = _ManagedGateway()

    assert agent_runtime_binding_state() == "stopped"
    with pytest.raises(AgentRuntimeStateError):
        get_agent_gateway()
    with pytest.raises(AgentRuntimeStateError):
        create_isolated_agent_gateway()

    bind_agent_runtime(
        gateway_provider=lambda: shared,
        isolated_gateway_factory=lambda: isolated,
    )

    assert agent_runtime_binding_state() == "running"
    assert get_agent_gateway() is shared
    assert create_isolated_agent_gateway() is isolated


def test_gateway_binding_rejects_implicit_replacement():
    from core.agent_runtime import AgentRuntimeStateError
    from core.agent_runtime.gateway import bind_agent_runtime

    bind_agent_runtime(
        gateway_provider=object,
        isolated_gateway_factory=object,
    )

    with pytest.raises(AgentRuntimeStateError, match="已绑定"):
        bind_agent_runtime(
            gateway_provider=object,
            isolated_gateway_factory=object,
        )


def test_gateway_binding_validates_factory_result():
    from core.agent_runtime import AgentRuntimeStateError
    from core.agent_runtime.gateway import (
        bind_agent_runtime,
        create_isolated_agent_gateway,
        get_agent_gateway,
    )

    bind_agent_runtime(
        gateway_provider=lambda: None,
        isolated_gateway_factory=lambda: None,
    )

    with pytest.raises(AgentRuntimeStateError, match="不可用"):
        get_agent_gateway()
    with pytest.raises(AgentRuntimeStateError, match="不可用"):
        create_isolated_agent_gateway()


def test_gateway_binding_rejects_wrong_port_and_resolves_research_port():
    from core.agent_runtime import AgentRuntimeStateError
    from core.agent_runtime.gateway import (
        bind_agent_runtime,
        create_research_agent_runtime,
        get_agent_gateway,
    )

    research = _ResearchGateway()
    bind_agent_runtime(
        gateway_provider=lambda: object(),
        isolated_gateway_factory=_ManagedGateway,
        research_runtime_factory=lambda: research,
    )

    with pytest.raises(AgentRuntimeStateError, match="未实现所需 Port"):
        get_agent_gateway()
    assert create_research_agent_runtime() is research

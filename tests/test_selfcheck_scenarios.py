"""跨层确定性自检场景测试。"""

from __future__ import annotations


def _agent_registry():
    from core.agent_runtime.registry import (
        AgentRuntimeDescriptor,
        AgentRuntimeRegistration,
        AgentRuntimeRegistry,
    )

    descriptor = AgentRuntimeDescriptor(
        agent_id="testbot",
        display_name="TestBot",
        description="自检路由测试 Agent",
        adapter="native",
        source_ref="creatures/testbot",
        source_sha256="2" * 64,
        runtime_policy_sha256="3" * 64,
        allowed_entrypoints=("chat", "agent_link", "a2a"),
        default=True,
    )
    return AgentRuntimeRegistry.build((AgentRuntimeRegistration(
        descriptor=descriptor,
        gateway_provider=lambda: None,
        isolated_gateway_factory=lambda: None,
    ),))


def test_session_default_gate_executes_runtime_query_without_writing(db_session):
    from core.database import ChatStreamConfig
    from core.selfcheck.scenarios import run_functional_scenario

    before = db_session.query(ChatStreamConfig).count()
    result = run_functional_scenario(
        "session.default-gate.functional",
        db=db_session,
        testing=True,
    )

    assert result.status == "passed"
    assert result.evidence == {
        "database_only": True,
        "default_agent_id": "nanobot",
    }
    assert db_session.query(ChatStreamConfig).count() == before


def test_agent_routing_scenarios_use_real_registry_capability_gates(db_session):
    from core.selfcheck.scenarios import run_functional_scenario

    registry = _agent_registry()
    chat = run_functional_scenario(
        "agent.routing.functional",
        db=db_session,
        testing=True,
        agent_registry=registry,
    )
    a2a = run_functional_scenario(
        "agent.a2a-routing.functional",
        db=db_session,
        testing=True,
        agent_registry=registry,
    )

    assert chat.status == "passed"
    assert chat.metrics["route_count"] == 1
    assert a2a.status == "passed"
    assert a2a.metrics["route_count"] == 2


def test_tool_runtime_scenario_builds_native_and_kt_bindings(db_session):
    from bootstrap.selfcheck_runtime import RuntimeSelfcheckDiagnosticsAdapter
    from core.selfcheck.scenarios import run_functional_scenario

    result = run_functional_scenario(
        "tool.runtime-bindings.functional",
        db=db_session,
        testing=True,
        runtime_diagnostics=RuntimeSelfcheckDiagnosticsAdapter(),
    )

    assert result.status == "passed"
    assert result.metrics["active_tool_count"] >= 20
    assert result.metrics["kt_binding_count"] in {
        0,
        result.metrics["active_tool_count"],
    }


def test_every_rag_source_executes_readonly_pipeline(db_session):
    from core.semantic.schema import ensure_semantic_schema
    from core.selfcheck.scenarios import run_functional_scenario

    ensure_semantic_schema(db_session.bind)
    sources = (
        "memory",
        "memory_digest",
        "session_summary",
        "group_memory",
        "sticker",
        "knowledge",
        "group_analysis",
        "all",
    )

    results = {
        source: run_functional_scenario(
            f"rag.{source.replace('_', '-')}.smoke",
            db=db_session,
            testing=True,
            source_id=source,
        )
        for source in sources
    }

    assert {source: result.status for source, result in results.items()} == {
        source: "passed" for source in sources
    }


def test_model_route_scenario_never_calls_provider(db_session, monkeypatch):
    from bootstrap.selfcheck_runtime import RuntimeSelfcheckDiagnosticsAdapter
    from core.selfcheck.scenarios import run_functional_scenario

    def forbidden(*_args, **_kwargs):
        raise AssertionError("配置自检不应调用模型 Provider")

    monkeypatch.setattr(
        "core.model_provider.route_runtime.call_model_route_response",
        forbidden,
    )
    result = run_functional_scenario(
        "model.route-configuration.functional",
        db=db_session,
        testing=True,
        runtime_diagnostics=RuntimeSelfcheckDiagnosticsAdapter(),
    )

    assert result.status in {"passed", "degraded", "inconclusive"}

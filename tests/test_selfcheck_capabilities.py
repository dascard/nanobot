"""自检能力清单的冻结、完整性与 Admin API 合同。"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest
ROOT = Path(__file__).resolve().parents[1]
HEADERS = {"Authorization": "Bearer selfcheck-test-token"}


def _api_routes(app) -> set[tuple[str, str]]:
    schema = app.openapi()
    return {
        (method.upper(), path)
        for path, path_item in schema["paths"].items()
        if path.startswith("/api/")
        for method in ("get", "put", "post", "delete", "patch")
        if method in path_item
    }


def _agent_descriptor():
    from core.agent_runtime.registry import AgentRuntimeDescriptor

    return AgentRuntimeDescriptor(
        agent_id="testbot",
        display_name="TestBot",
        description="自检能力清单测试 Agent",
        adapter="native",
        source_ref="creatures/testbot",
        source_sha256="0" * 64,
        runtime_policy_sha256="1" * 64,
        allowed_entrypoints=("chat",),
        default=True,
    )


def _literal_webui_routes() -> set[str]:
    app_source = (ROOT / "webui" / "src" / "App.jsx").read_text(
        encoding="utf-8"
    )
    manifest_source = (
        ROOT / "webui" / "src" / "features" / "manifest.jsx"
    ).read_text(encoding="utf-8")
    static_routes = set(re.findall(r'<Route\s+path="([^"]+)"', app_source))
    manifest_routes = set(
        re.findall(r"\broute:\s*'([^']+)'", manifest_source)
    )
    return (static_routes | manifest_routes) - {"*"}


def test_capability_descriptor_reports_honest_coverage_states():
    from core.selfcheck.capabilities import CapabilityDescriptor

    unverified = CapabilityDescriptor(
        capability_id="api.get.example",
        kind="api",
        source_id="GET /api/example",
        label="示例接口",
        owner="tests",
    )
    covered = CapabilityDescriptor(
        capability_id="worker.example",
        kind="worker",
        source_id="example-worker",
        label="示例 Worker",
        owner="tests",
        probe_ids=("worker.example.heartbeat",),
    )
    exempted = CapabilityDescriptor(
        capability_id="webui.redirect.example",
        kind="webui",
        source_id="/legacy",
        label="兼容重定向",
        owner="tests",
        coverage_policy="exempt",
        exemption_reason="仅保留兼容重定向，不承载业务交互",
    )

    assert unverified.coverage_status == "unverified"
    assert covered.coverage_status == "covered"
    assert exempted.coverage_status == "exempted"
    with pytest.raises(ValueError, match="豁免原因"):
        CapabilityDescriptor(
            capability_id="webui.redirect.invalid",
            kind="webui",
            source_id="/invalid",
            label="非法豁免",
            owner="tests",
            coverage_policy="exempt",
        )


def test_capability_registry_discovers_every_runtime_surface(client):
    from core.model_provider.route_registry import list_model_route_descriptors
    from core.selfcheck.capabilities import (
        RAG_CAPABILITY_SOURCES,
        WORKER_CAPABILITY_SOURCES,
        build_capability_registry,
        capability_coverage_summary,
    )
    from core.tool_registration import TOOL_REGISTRATION_REGISTRY

    agents = (_agent_descriptor(),)
    first = build_capability_registry(client.app, agent_descriptors=agents)
    second = build_capability_registry(client.app, agent_descriptors=agents)

    assert first.namespace == "selfcheck_capability"
    assert first.sha256 == second.sha256
    assert first.canonical_json == second.canonical_json
    assert len(first) == len(first.items)

    by_kind: dict[str, list] = {}
    for descriptor in first:
        by_kind.setdefault(descriptor.kind, []).append(descriptor)

    discovered_api_routes = {
        (
            dict(descriptor.attributes)["method"],
            dict(descriptor.attributes)["path"],
        )
        for descriptor in by_kind["api"]
    }
    assert discovered_api_routes == _api_routes(client.app)
    assert {
        descriptor.source_id for descriptor in by_kind["tool"]
    } == {
        registration.name
        for registration in TOOL_REGISTRATION_REGISTRY.registry_snapshot
    }
    assert {
        descriptor.source_id for descriptor in by_kind["model_route"]
    } == {
        route.route_key for route in list_model_route_descriptors()
    }
    assert {
        descriptor.source_id for descriptor in by_kind["agent"]
    } == {agent.agent_id for agent in agents}
    assert {
        descriptor.source_id for descriptor in by_kind["rag_source"]
    } == set(RAG_CAPABILITY_SOURCES)
    assert {
        descriptor.source_id for descriptor in by_kind["worker"]
    } == set(WORKER_CAPABILITY_SOURCES)

    summary = capability_coverage_summary(first)
    assert summary["total"] == len(first)
    assert (
        summary["covered"]
        + summary["unverified"]
        + summary["exempted"]
        == len(first)
    )
    assert summary["unverified"] > 0
    assert summary["required_unverified"] > 0
    assert summary["by_kind"]["api"]["total"] == len(
        _api_routes(client.app)
    )


def test_webui_capability_manifest_matches_every_literal_route():
    manifest = json.loads(
        (ROOT / "config" / "webui-capabilities.v1.json").read_text(
            encoding="utf-8"
        )
    )
    routes = [item["route"] for item in manifest["capabilities"]]

    assert len(routes) == len(set(routes))
    assert set(routes) == _literal_webui_routes()
    for item in manifest["capabilities"]:
        assert item["feature_id"]
        assert item["label"]
        assert item["owner"]
        assert item["criticality"] in {"critical", "high", "medium", "low"}
        if item.get("coverage_policy") == "exempt":
            assert item.get("exemption_reason")


def test_critical_webui_backend_operations_exist_in_openapi(client):
    manifest = json.loads(
        (ROOT / "config" / "webui-capabilities.v1.json").read_text(
            encoding="utf-8"
        )
    )
    operation_ids = {
        operation["operationId"]
        for path_item in client.app.openapi()["paths"].values()
        for method in ("get", "put", "post", "delete", "patch")
        for operation in (path_item.get(method),)
        if isinstance(operation, dict)
    }
    by_route = {
        item["route"]: item
        for item in manifest["capabilities"]
    }
    for route in ("/rag-debug", "/self-check"):
        bindings = by_route[route]["backend_operation_ids"]
        assert bindings
        assert set(bindings) <= operation_ids


def test_selfcheck_capabilities_admin_api(client, monkeypatch):
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "selfcheck-test-token",
    )
    monkeypatch.setattr(
        "api.admin.selfcheck_routes._registered_agent_descriptors",
        lambda: (_agent_descriptor(),),
    )

    unauthorized = client.get("/api/v1/admin/self-check/capabilities")
    response = client.get(
        "/api/v1/admin/self-check/capabilities",
        headers=HEADERS,
    )

    assert unauthorized.status_code == 401
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["registry"]["namespace"] == "selfcheck_capability"
    assert payload["registry"]["generation"] == 1
    assert len(payload["registry"]["sha256"]) == 64
    assert payload["coverage"]["total"] == len(payload["items"])
    assert {
        "api",
        "webui",
        "agent",
        "tool",
        "model_route",
        "rag_source",
        "worker",
    } <= {item["kind"] for item in payload["items"]}
    assert {item["coverage_status"] for item in payload["items"]} <= {
        "covered",
        "unverified",
        "exempted",
    }
    assert "passed" not in {item["coverage_status"] for item in payload["items"]}

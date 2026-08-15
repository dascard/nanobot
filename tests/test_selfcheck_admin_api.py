"""自检 Probe、执行与历史查询 Admin API 测试。"""

from __future__ import annotations

import json

HEADERS = {"Authorization": "Bearer selfcheck-api-token"}


def _agent_descriptor():
    from core.agent_runtime.registry import AgentRuntimeDescriptor

    return AgentRuntimeDescriptor(
        agent_id="testbot",
        display_name="TestBot",
        description="自检 API 测试 Agent",
        adapter="native",
        source_ref="creatures/testbot",
        source_sha256="4" * 64,
        runtime_policy_sha256="5" * 64,
        allowed_entrypoints=("chat",),
        default=True,
    )


def _enable(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "selfcheck-api-token",
    )
    monkeypatch.setattr(
        "api.admin.selfcheck_routes._registered_agent_descriptors",
        lambda: (_agent_descriptor(),),
    )


def test_selfcheck_probe_catalog_and_selected_run_history(client, monkeypatch):
    _enable(client, monkeypatch)

    probes = client.get("/api/v1/admin/self-check/probes", headers=HEADERS)
    run = client.post(
        "/api/v1/admin/self-check/runs",
        headers=HEADERS,
        json={
            "trigger": "manual",
            "check_ids": [
                "database.connectivity",
                "database.integrity",
                "session.database_only_default",
            ],
        },
    )

    assert probes.status_code == 200, probes.text
    probe_payload = probes.json()
    assert probe_payload["registry"]["namespace"] == "selfcheck_probe"
    assert len(probe_payload["items"]) >= 30
    assert all(item["destructive"] is False for item in probe_payload["items"])

    assert run.status_code == 200, run.text
    report = run.json()
    assert report["trigger"] == "manual"
    assert report["environment"] == "ci"
    assert report["summary"]["total"] == 3
    assert {item["check_id"] for item in report["results"]} == {
        "database.connectivity",
        "database.integrity",
        "session.database_only_default",
    }
    assert all(item["status"] == "passed" for item in report["results"])

    history = client.get(
        "/api/v1/admin/self-check/runs?limit=10",
        headers=HEADERS,
    )
    detail = client.get(
        f"/api/v1/admin/self-check/runs/{report['run_id']}",
        headers=HEADERS,
    )
    assert history.status_code == 200, history.text
    assert history.json()["items"][0]["run_id"] == report["run_id"]
    assert detail.status_code == 200, detail.text
    assert detail.json()["run_id"] == report["run_id"]
    assert len(detail.json()["results"]) == 3


def test_selfcheck_run_rejects_unknown_or_duplicate_check_ids(client, monkeypatch):
    _enable(client, monkeypatch)

    unknown = client.post(
        "/api/v1/admin/self-check/runs",
        headers=HEADERS,
        json={"trigger": "manual", "check_ids": ["missing.probe"]},
    )
    duplicate = client.post(
        "/api/v1/admin/self-check/runs",
        headers=HEADERS,
        json={
            "trigger": "manual",
            "check_ids": ["database.connectivity", "database.connectivity"],
        },
    )
    missing = client.get(
        "/api/v1/admin/self-check/runs/sc_missing",
        headers=HEADERS,
    )

    assert unknown.status_code == 422
    assert duplicate.status_code == 422
    assert missing.status_code == 404


def test_model_canary_authorization_rejects_coerced_boolean(client, monkeypatch):
    _enable(client, monkeypatch)

    response = client.post(
        "/api/v1/admin/self-check/runs",
        headers=HEADERS,
        json={
            "trigger": "manual",
            "check_ids": ["model.reply-canary.functional"],
            "allow_model_checks": "true",
        },
    )

    assert response.status_code == 422


def test_model_canary_requires_explicit_authorization(client, monkeypatch):
    _enable(client, monkeypatch)

    def forbidden(_nonce: str, _timeout: float) -> str:
        raise AssertionError("未授权时不应调用模型")

    monkeypatch.setattr(
        "core.selfcheck.engine._default_model_canary_runner",
        forbidden,
    )
    response = client.post(
        "/api/v1/admin/self-check/runs",
        headers=HEADERS,
        json={
            "trigger": "manual",
            "check_ids": ["model.reply-canary.functional"],
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["status"] == "skipped"
    assert result["detail_code"] == "model_check_not_authorized"


def test_authorized_model_canary_checks_structured_semantics(client, monkeypatch):
    _enable(client, monkeypatch)
    monkeypatch.setattr(
        "core.selfcheck.engine._default_model_canary_runner",
        lambda nonce, _timeout: json.dumps({
            "status": "ok",
            "answer": 42,
            "nonce": nonce,
        }),
    )
    response = client.post(
        "/api/v1/admin/self-check/runs",
        headers=HEADERS,
        json={
            "trigger": "manual",
            "check_ids": ["model.reply-canary.functional"],
            "allow_model_checks": True,
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["status"] == "passed"
    assert result["metrics"]["semantic_match"] is True

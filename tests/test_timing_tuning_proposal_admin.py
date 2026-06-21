import json


def _auth_header():
    return {"Authorization": "Bearer test-token"}


def test_timing_tuning_proposal_admin_returns_missing_report(client, monkeypatch, tmp_path):
    from api import admin_routes

    missing = tmp_path / "missing.json"
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(admin_routes, "TIMING_TUNING_PROPOSAL_REPORT", missing, raising=False)

    response = client.get(
        "/api/v1/admin/evals/timing-tuning/proposal",
        headers=_auth_header(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["exists"] is False
    assert payload["report_path"] == str(missing)
    assert payload["readiness"]["ready"] is False
    assert payload["readiness"]["blocking_reasons"][0]["code"] == "proposal_report_missing"


def test_timing_tuning_proposal_admin_reads_report(client, monkeypatch, tmp_path):
    from api import admin_routes

    report = tmp_path / "proposal.json"
    report.write_text(
        json.dumps(
            {
                "proposal_version": 1,
                "readiness": {
                    "ready": False,
                    "blocking_reasons": [{"code": "missing_action_truth"}],
                },
                "candidate_sets": [],
                "simulation": {"flip_count": 0},
                "blocked_actions": ["auto_apply", "baseline_update", "gate_change"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(admin_routes, "TIMING_TUNING_PROPOSAL_REPORT", report, raising=False)

    response = client.get(
        "/api/v1/admin/evals/timing-tuning/proposal",
        headers=_auth_header(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["exists"] is True
    assert payload["report_path"] == str(report)
    assert payload["report"]["proposal_version"] == 1
    assert payload["report"]["blocked_actions"] == [
        "auto_apply",
        "baseline_update",
        "gate_change",
    ]


def test_timing_tuning_proposal_admin_reports_invalid_json(client, monkeypatch, tmp_path):
    from api import admin_routes

    report = tmp_path / "proposal.json"
    report.write_text("{bad json", encoding="utf-8")
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(admin_routes, "TIMING_TUNING_PROPOSAL_REPORT", report, raising=False)

    response = client.get(
        "/api/v1/admin/evals/timing-tuning/proposal",
        headers=_auth_header(),
    )

    assert response.status_code == 500
    assert "invalid proposal report" in response.json()["detail"]

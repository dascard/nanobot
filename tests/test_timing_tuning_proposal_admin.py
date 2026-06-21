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


def test_timing_tuning_proposal_review_records_admin_audit(
    client,
    db_session,
    monkeypatch,
    tmp_path,
):
    from api import admin_routes
    from core.database import AdminAuditLog

    report = tmp_path / "proposal.json"
    report.write_text(
        json.dumps(
            {
                "proposal_version": 1,
                "generated_at": "2026-06-21T12:00:00",
                "source": {"run_id": "run_1"},
                "readiness": {"ready": False, "blocking_reasons": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(
        admin_routes,
        "TIMING_TUNING_PROPOSAL_REPORT",
        report,
        raising=False,
    )

    response = client.post(
        "/api/v1/admin/evals/timing-tuning/proposal/reviews",
        headers=_auth_header(),
        json={
            "decision": "needs_data",
            "reason_code": "missing_action_truth",
            "note": "需要补人工 truth",
            "reviewer": "admin",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["decision"] == "needs_data"
    assert payload["report_path"] == str(report)
    assert len(payload["proposal_sha256"]) == 64
    audit = (
        db_session.query(AdminAuditLog)
        .filter_by(action="review_timing_tuning_proposal")
        .one()
    )
    detail = json.loads(audit.detail_json)
    assert audit.target_type == "timing_tuning_proposal"
    assert audit.target_id == payload["proposal_sha256"]
    assert detail["decision"] == "needs_data"
    assert detail["reason_code"] == "missing_action_truth"
    assert detail["report_path"] == str(report)

    state = client.get(
        "/api/v1/admin/evals/timing-tuning/proposal/review",
        headers=_auth_header(),
    )

    assert state.status_code == 200
    review_state = state.json()
    assert review_state["exists"] is True
    assert review_state["proposal_sha256"] == payload["proposal_sha256"]
    assert review_state["review"]["decision"] == "needs_data"


def test_timing_tuning_proposal_review_rejects_invalid_decision(
    client,
    monkeypatch,
    tmp_path,
):
    from api import admin_routes

    report = tmp_path / "proposal.json"
    report.write_text('{"proposal_version":1}', encoding="utf-8")
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(
        admin_routes,
        "TIMING_TUNING_PROPOSAL_REPORT",
        report,
        raising=False,
    )

    response = client.post(
        "/api/v1/admin/evals/timing-tuning/proposal/reviews",
        headers=_auth_header(),
        json={
            "decision": "apply_now",
            "reason_code": "x",
            "note": "",
            "reviewer": "admin",
        },
    )

    assert response.status_code == 422


def test_timing_tuning_proposal_review_state_returns_missing_report(
    client,
    monkeypatch,
    tmp_path,
):
    from api import admin_routes

    missing = tmp_path / "missing.json"
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(
        admin_routes,
        "TIMING_TUNING_PROPOSAL_REPORT",
        missing,
        raising=False,
    )

    response = client.get(
        "/api/v1/admin/evals/timing-tuning/proposal/review",
        headers=_auth_header(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["exists"] is False
    assert payload["report_path"] == str(missing)
    assert payload["review"] is None
    assert payload["readiness"]["blocking_reasons"][0]["code"] == (
        "proposal_report_missing"
    )

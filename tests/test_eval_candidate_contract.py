import json
from datetime import datetime, timedelta

import pytest

from core.database import EvalCandidate


def _db_now() -> datetime:
    # SQLite ORM DateTime fixture 保持 naive 本地墙钟时间语义。
    return datetime.now()  # noqa: DTZ005


def _auth_header():
    return {"Authorization": "Bearer test-token"}


def _insert_candidate(db_session, *, case_id="cand_timing_gate_1", suite="timing_gate"):
    row = EvalCandidate(
        case_id=case_id,
        suite=suite,
        source="db",
        source_ref="chatlog:1",
        description="candidate",
        input_json=json.dumps({"message": "nanobot 帮我看看"}, ensure_ascii=False),
        expected_json=json.dumps({"needs_label": True}, ensure_ascii=False),
        tags_json=json.dumps(["sampled", suite], ensure_ascii=False),
        status="candidate",
        fingerprint=f"fp-{case_id}",
    )
    db_session.add(row)
    db_session.commit()
    return row


def _redirect_promote_root(monkeypatch, tmp_path):
    from core.eval_sampling import store

    monkeypatch.setattr(store, "__file__", str(tmp_path / "core" / "eval_sampling" / "store.py"))
    monkeypatch.setattr(store, "REPO_ROOT", tmp_path, raising=False)
    return store


def test_validate_expected_rejects_empty_needs_label_and_unknown_key():
    from evals.expected_contract import validate_expected_contract

    for expected in ({}, {"needs_label": True}, {"unscored_field": "x"}):
        with pytest.raises(ValueError):
            validate_expected_contract("timing_gate", expected)


def test_validate_expected_accepts_scored_keys():
    from evals.expected_contract import validate_expected_contract

    validate_expected_contract("timing_gate", {"timing_action": "continue", "should_reply": True})
    validate_expected_contract("model_routing", {"model_used": "vision-model"})


def test_sticker_expected_fields_are_scored():
    from evals.schema import EvalCase, EvalOutput
    from evals.scorers import score_case

    case = EvalCase(
        id="sticker_contract",
        suite="sticker",
        expected={"served_sticker_id": 74, "send_source": "public_proxy"},
    )
    output = EvalOutput(
        case_id=case.id,
        suite=case.suite,
        raw={"served_sticker_id": 200, "send_source": "qq_temp"},
    )

    score = score_case(case, output)

    assert not score["passed"]
    assert any("served_sticker_id mismatch" in error for error in score["errors"])
    assert any("send_source mismatch" in error for error in score["errors"])


def test_sticker_runner_outputs_expected_contract_fields():
    from evals.run import load_cases
    from evals.runners.sticker_runner import run_sticker_case

    cases = {
        case.id: case
        for case in load_cases("regression")
        if case.id
        in {
            "regression_sticker_duplicate_canonical_001",
            "regression_sticker_public_proxy_001",
        }
    }

    duplicate = run_sticker_case(cases["regression_sticker_duplicate_canonical_001"])
    public_proxy = run_sticker_case(cases["regression_sticker_public_proxy_001"])

    assert duplicate.raw["served_sticker_id"] == 74
    assert public_proxy.raw["send_source"] == "public_proxy"


def test_label_candidate_rejects_empty_or_needs_label(db_session):
    from core.eval_sampling.store import label_candidate

    for expected in ({}, {"needs_label": True}):
        case_id = f"cand_{len(str(expected))}"
        _insert_candidate(db_session, case_id=case_id)

        with pytest.raises(ValueError):
            label_candidate(db_session, case_id, expected)


def test_eval_label_candidate_accepts_expected_json_legacy_field(
    client,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    _insert_candidate(db_session)

    response = client.post(
        "/api/v1/admin/evals/candidates/cand_timing_gate_1/label",
        headers=_auth_header(),
        json={"expected_json": {"timing_action": "continue"}},
    )

    assert response.status_code == 200, response.text
    assert response.json()["expected"] == {"timing_action": "continue"}


def test_eval_expected_contract_endpoint_exposes_scoreable_keys(client, monkeypatch):
    from evals.expected_contract import SCOREABLE_EXPECTED_KEYS

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")

    response = client.get(
        "/api/v1/admin/evals/expected-contract",
        headers=_auth_header(),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert sorted(payload["scoreable_keys"]) == sorted(SCOREABLE_EXPECTED_KEYS)
    assert set(payload["field_schema"]) == set(SCOREABLE_EXPECTED_KEYS)
    assert payload["suite_presets"]["timing_gate"]["fields"][0] == "timing_action"
    assert payload["field_schema"]["timing_action"]["type"] == "enum"
    assert payload["field_schema"]["timing_action"]["values"] == [
        "continue",
        "wait",
        "no_reply",
    ]
    for deprecated in (
        "expected_action",
        "should_learn",
        "quality",
        "category",
        "meaning",
        "delay_seconds",
    ):
        assert deprecated in payload["deprecated_keys"]
        assert deprecated not in payload["scoreable_keys"]


def test_rendering_contract_expected_preset_uses_scoreable_fields():
    from evals.expected_contract import expected_contract_payload
    from evals.expected_contract import validate_expected_contract

    payload = expected_contract_payload()
    fields = payload["suite_presets"]["rendering_contract"]["fields"]

    assert fields == [
        "should_reply",
        "send_mode",
        "reply_to_message_id",
        "mentions",
        "must_contain",
        "must_not_contain",
    ]
    validate_expected_contract(
        "rendering_contract",
        {
            "should_reply": True,
            "send_mode": "quote",
            "reply_to_message_id": "msg-1",
            "mentions": ["10001"],
            "must_contain": ["[CQ:image"],
            "must_not_contain": ["base64://"],
        },
    )


@pytest.mark.parametrize(
    ("suite", "expected", "message"),
    [
        ("timing_gate", {"timing_action": 123}, "timing_action"),
        ("timing_gate", {"timing_action": "maybe"}, "timing_action"),
        ("timing_gate", {"should_reply": "false"}, "should_reply"),
        ("group_reply", {"required_tools": "reply"}, "required_tools"),
        ("sticker", {"http_status": "200"}, "http_status"),
        ("timing_gate", {"expected_action": "continue"}, "expected_action"),
    ],
)
def test_validate_expected_rejects_bad_types_and_deprecated_keys(suite, expected, message):
    from evals.expected_contract import validate_expected_contract

    with pytest.raises(ValueError, match=message):
        validate_expected_contract(suite, expected)


def test_validate_expected_accepts_typed_values():
    from evals.expected_contract import validate_expected_contract

    validate_expected_contract(
        "group_reply",
        {
            "should_reply": True,
            "required_tools": ["reply"],
            "mentions": [{"user_id": "456"}],
            "must_contain": ["关键句"],
            "send_mode": "quote",
            "reply_to_message_id": "m-1",
        },
    )
    validate_expected_contract("sticker", {"http_status": 200, "served_sticker_id": 74})


def test_eval_label_candidate_rejects_conflicting_expected_fields(
    client,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    _insert_candidate(db_session)

    response = client.post(
        "/api/v1/admin/evals/candidates/cand_timing_gate_1/label",
        headers=_auth_header(),
        json={
            "expected": {"timing_action": "continue"},
            "expected_json": {"timing_action": "no_reply"},
        },
    )

    assert response.status_code == 400
    assert "expected" in response.json()["detail"]


def test_eval_list_candidates_returns_summary_and_readiness(client, db_session, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    _insert_candidate(db_session, case_id="cand_a")
    _insert_candidate(db_session, case_id="cand_b")

    response = client.get(
        "/api/v1/admin/evals/candidates",
        headers=_auth_header(),
        params={"suite": "timing_gate", "limit": 20},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 2
    assert payload["summary"]["total"] == 2
    assert payload["summary"]["by_status"]["candidate"] == 2
    assert payload["summary"]["by_suite"]["timing_gate"] == 2
    assert payload["summary"]["readiness"]["blocked"] == 2
    assert payload["items"][0]["readiness"]["ready"] is False
    assert payload["items"][0]["readiness"]["blocking_reasons"]


def test_candidate_trend_report_groups_current_snapshot_by_created_date(db_session):
    from core.eval_sampling.store import candidate_trend_report

    today = _db_now().replace(hour=10, minute=0, second=0, microsecond=0)
    old_day = today - timedelta(days=1)
    rows = [
        EvalCandidate(
            case_id="cand_trend_blocked",
            suite="timing_gate",
            source="db",
            status="candidate",
            expected_json=json.dumps({"needs_label": True}, ensure_ascii=False),
            created_at=old_day,
            updated_at=old_day,
        ),
        EvalCandidate(
            case_id="cand_trend_ready",
            suite="timing_gate",
            source="db",
            status="labeled",
            expected_json=json.dumps({"timing_action": "continue"}, ensure_ascii=False),
            created_at=today,
            updated_at=today,
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()

    report = candidate_trend_report(
        db_session,
        days=2,
        suite="timing_gate",
        source="db",
        target_dataset="trend_target",
    )

    assert report["ok"] is True
    assert report["filters"]["bucket"] == "day"
    assert report["summary"]["total"] == 2
    assert report["summary"]["by_status"] == {"candidate": 1, "labeled": 1}
    assert report["summary"]["readiness"] == {"ready": 1, "blocked": 1}
    assert len(report["buckets"]) == 2
    by_date = {bucket["date"]: bucket for bucket in report["buckets"]}
    old_bucket = by_date[old_day.date().isoformat()]
    today_bucket = by_date[today.date().isoformat()]
    assert old_bucket["created"] == 1
    assert old_bucket["by_status"]["candidate"] == 1
    assert old_bucket["readiness"] == {"ready": 0, "blocked": 1}
    assert {reason["code"] for reason in old_bucket["top_blocking_reasons"]} == {
        "expected_invalid",
        "invalid_status",
    }
    assert today_bucket["created"] == 1
    assert today_bucket["by_status"]["labeled"] == 1
    assert today_bucket["readiness"] == {"ready": 1, "blocked": 0}


def test_candidates_trend_api_is_read_only(client, db_session, monkeypatch):
    from core.database import AdminAuditLog

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    db_session.add(
        EvalCandidate(
            case_id="cand_trend_api",
            suite="timing_gate",
            source="api",
            status="candidate",
            created_at=_db_now(),
            updated_at=_db_now(),
        )
    )
    db_session.commit()

    response = client.get(
        "/api/v1/admin/evals/candidates/trend",
        headers=_auth_header(),
        params={"days": 30, "suite": "timing_gate", "source": "api"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["summary"]["total"] == 1
    assert payload["buckets"][0]["by_source"]["api"] == 1
    assert db_session.query(AdminAuditLog).count() == 0
    assert db_session.query(EvalCandidate).filter_by(case_id="cand_trend_api").one().status == "candidate"


def test_eval_patch_candidate_rejects_direct_labeled_promoted_and_unknown_status(
    client,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    _insert_candidate(db_session)

    for status in ("labeled", "promoted", "invalid"):
        response = client.patch(
            "/api/v1/admin/evals/candidates/cand_timing_gate_1",
            headers=_auth_header(),
            json={"status": status},
        )
        assert response.status_code == 400
        assert status in response.text

    ok = client.patch(
        "/api/v1/admin/evals/candidates/cand_timing_gate_1",
        headers=_auth_header(),
        json={"priority": 10, "note": "优先处理"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["priority"] == 10
    assert ok.json()["note"] == "优先处理"


def test_candidate_triage_transitions_return_audit_payload(db_session):
    from core.eval_sampling.store import (
        defer_candidate,
        get_candidate,
        reject_candidate,
        reopen_candidate,
    )

    _insert_candidate(db_session, case_id="cand_reject")
    rejected = reject_candidate(
        db_session,
        "cand_reject",
        reason_code="low_value",
        note="普通寒暄，不进入稳定集",
    )

    assert rejected["candidate"]["status"] == "rejected"
    assert rejected["audit"] == {
        "before_status": "candidate",
        "after_status": "rejected",
        "reason_code": "low_value",
        "note": "普通寒暄，不进入稳定集",
        "defer_until": "",
    }
    assert get_candidate(db_session, "cand_reject").status == "rejected"

    _insert_candidate(db_session, case_id="cand_defer")
    deferred = defer_candidate(
        db_session,
        "cand_defer",
        reason_code="needs_more_context",
        note="等后续对话补齐上下文",
        defer_until="2026-06-30",
    )

    assert deferred["candidate"]["status"] == "deferred"
    assert deferred["audit"]["before_status"] == "candidate"
    assert deferred["audit"]["after_status"] == "deferred"
    assert deferred["audit"]["reason_code"] == "needs_more_context"
    assert deferred["audit"]["defer_until"] == "2026-06-30"

    reopened = reopen_candidate(
        db_session,
        "cand_defer",
        reason_code="defer_expired",
        note="到期复核",
    )

    assert reopened["candidate"]["status"] == "candidate"
    assert reopened["audit"]["before_status"] == "deferred"
    assert reopened["audit"]["after_status"] == "candidate"
    assert reopened["audit"]["reason_code"] == "defer_expired"


def test_candidate_triage_rejects_invalid_transitions_and_reason_codes(
    db_session,
    tmp_path,
    monkeypatch,
):
    _redirect_promote_root(monkeypatch, tmp_path)
    from core.eval_sampling.store import (
        defer_candidate,
        get_candidate,
        label_candidate,
        promote_candidate,
        reject_candidate,
        reopen_candidate,
    )

    _insert_candidate(db_session, case_id="cand_promoted")
    label_candidate(db_session, "cand_promoted", {"timing_action": "continue"})
    promote_candidate(db_session, "cand_promoted")

    for action in (
        lambda: reject_candidate(db_session, "cand_promoted", reason_code="low_value"),
        lambda: defer_candidate(db_session, "cand_promoted", reason_code="needs_more_context"),
        lambda: reopen_candidate(db_session, "cand_promoted", reason_code="new_evidence"),
    ):
        with pytest.raises(ValueError, match="invalid status transition"):
            action()

    _insert_candidate(db_session, case_id="cand_bad_reason")
    with pytest.raises(ValueError, match="invalid reason_code"):
        reject_candidate(db_session, "cand_bad_reason", reason_code="unknown_reason")

    _insert_candidate(db_session, case_id="cand_rejected")
    reject_candidate(db_session, "cand_rejected", reason_code="low_value")
    assert get_candidate(db_session, "cand_rejected").status == "rejected"
    with pytest.raises(ValueError, match="candidate status"):
        label_candidate(db_session, "cand_rejected", {"timing_action": "continue"})


def test_candidate_readiness_blocks_status_error_suite_invalid_expected_and_existing_target(
    db_session,
    tmp_path,
    monkeypatch,
):
    _redirect_promote_root(monkeypatch, tmp_path)
    from core.eval_sampling.store import candidate_readiness, get_candidate, label_candidate

    _insert_candidate(db_session, case_id="cand_needs_label")
    needs_label = get_candidate(db_session, "cand_needs_label")
    readiness = candidate_readiness(needs_label, target_dataset="timing_gate")
    assert readiness["ready"] is False
    assert readiness["can_label"] is True
    assert [reason["code"] for reason in readiness["blocking_reasons"]] == [
        "invalid_status",
        "expected_invalid",
    ]

    _insert_candidate(db_session, case_id="cand_error", suite="error")
    label_candidate(db_session, "cand_error", {"timing_action": "continue"})
    error_readiness = candidate_readiness(
        get_candidate(db_session, "cand_error"),
        target_dataset="timing_gate",
    )
    assert error_readiness["ready"] is False
    assert any(
        reason["code"] == "suite_not_runnable"
        for reason in error_readiness["blocking_reasons"]
    )

    _insert_candidate(db_session, case_id="cand_ready")
    label_candidate(db_session, "cand_ready", {"timing_action": "continue"})
    target = tmp_path / "evals" / "cases" / "timing_gate" / "cand_ready.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    conflict = candidate_readiness(
        get_candidate(db_session, "cand_ready"),
        target_dataset="timing_gate",
    )
    assert conflict["ready"] is False
    assert any(
        reason["code"] == "target_case_exists"
        for reason in conflict["blocking_reasons"]
    )


def test_promote_candidate_rejects_unlabeled_and_file_conflict(db_session, tmp_path, monkeypatch):
    _redirect_promote_root(monkeypatch, tmp_path)
    from core.eval_sampling.store import label_candidate, promote_candidate

    _insert_candidate(db_session)

    with pytest.raises(ValueError, match="labeled"):
        promote_candidate(db_session, "cand_timing_gate_1")

    label_candidate(db_session, "cand_timing_gate_1", {"timing_action": "continue"})
    target = tmp_path / "evals" / "cases" / "regression" / "cand_timing_gate_1.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        promote_candidate(db_session, "cand_timing_gate_1")


def test_promote_candidate_rejects_non_runnable_suite(db_session, tmp_path, monkeypatch):
    _redirect_promote_root(monkeypatch, tmp_path)
    from core.eval_sampling.store import get_candidate, label_candidate, promote_candidate

    _insert_candidate(db_session, case_id="cand_error", suite="error")
    label_candidate(db_session, "cand_error", {"timing_action": "continue"})

    with pytest.raises(ValueError, match="suite_not_runnable"):
        promote_candidate(db_session, "cand_error", target_dataset="timing_gate")

    assert get_candidate(db_session, "cand_error").status == "labeled"
    assert not (tmp_path / "evals" / "cases" / "timing_gate" / "cand_error.json").exists()


def test_eval_candidates_preflight_returns_ready_and_blocked_items(
    client,
    db_session,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    _redirect_promote_root(monkeypatch, tmp_path)
    from core.eval_sampling.store import label_candidate

    _insert_candidate(db_session, case_id="cand_ready")
    label_candidate(db_session, "cand_ready", {"timing_action": "continue"})
    _insert_candidate(db_session, case_id="cand_error", suite="error")
    label_candidate(db_session, "cand_error", {"timing_action": "continue"})

    response = client.post(
        "/api/v1/admin/evals/candidates/preflight",
        headers=_auth_header(),
        json={
            "case_ids": ["cand_ready", "cand_error", "missing_case"],
            "target_dataset": "timing_gate",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is False
    assert payload["total"] == 3
    assert payload["ready"] == 1
    assert payload["blocked"] == 2
    by_id = {item["case_id"]: item for item in payload["items"]}
    assert by_id["cand_ready"]["readiness"]["ready"] is True
    assert by_id["cand_error"]["readiness"]["blocking_reasons"][0]["code"] == "suite_not_runnable"
    assert by_id["missing_case"]["readiness"]["blocking_reasons"][0]["code"] == "candidate_not_found"


def test_eval_candidate_triage_endpoints_write_audit_detail(
    client,
    db_session,
    monkeypatch,
):
    from core.database import AdminAuditLog

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    _insert_candidate(db_session, case_id="cand_defer_api")

    response = client.post(
        "/api/v1/admin/evals/candidates/cand_defer_api/defer",
        headers=_auth_header(),
        json={
            "reason_code": "needs_more_context",
            "note": "缺少后续上下文",
            "defer_until": "2026-06-30",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "deferred"

    audit = (
        db_session.query(AdminAuditLog)
        .filter(AdminAuditLog.action == "defer_candidate")
        .order_by(AdminAuditLog.id.desc())
        .first()
    )
    assert audit is not None
    detail = json.loads(audit.detail_json)
    assert detail["before_status"] == "candidate"
    assert detail["after_status"] == "deferred"
    assert detail["reason_code"] == "needs_more_context"
    assert detail["note"] == "缺少后续上下文"
    assert detail["defer_until"] == "2026-06-30"

    rejected = client.post(
        "/api/v1/admin/evals/candidates/cand_defer_api/reject",
        headers=_auth_header(),
        json={"reason_code": "low_value", "note": "复核后拒绝"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"

    reopened = client.post(
        "/api/v1/admin/evals/candidates/cand_defer_api/reopen",
        headers=_auth_header(),
        json={"reason_code": "operator_correction", "note": "恢复到候选"},
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "candidate"


def test_candidate_batch_audit_dry_run_is_read_only(client, db_session, monkeypatch):
    from core.database import AdminAuditLog
    from core.eval_sampling.store import get_candidate, label_candidate

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    _insert_candidate(db_session, case_id="cand_batch_ready")
    label_candidate(db_session, "cand_batch_ready", {"timing_action": "continue"})
    _insert_candidate(db_session, case_id="cand_batch_error", suite="error")
    label_candidate(db_session, "cand_batch_error", {"timing_action": "continue"})

    response = client.post(
        "/api/v1/admin/evals/candidates/batch-audit",
        headers=_auth_header(),
        json={
            "dry_run": True,
            "case_ids": ["cand_batch_ready", "cand_batch_error"],
            "target_dataset": "timing_gate",
            "batch_note": "人工复核",
            "decisions": [
                {"case_id": "cand_batch_ready", "decision": "promote_ready"},
                {
                    "case_id": "cand_batch_error",
                    "decision": "defer",
                    "reason_code": "needs_batch_review",
                    "defer_until": "2026-06-30",
                },
            ],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["audit_log_id"] is None
    assert payload["total"] == 2
    assert payload["ready"] == 1
    assert payload["blocked"] == 1
    assert payload["counts"]["by_decision"]["promote_ready"] == 1
    assert payload["counts"]["by_decision"]["defer"] == 1
    assert payload["counts"]["by_blocking_reason"]["suite_not_runnable"] == 1
    assert db_session.query(AdminAuditLog).count() == 0
    assert get_candidate(db_session, "cand_batch_ready").status == "labeled"
    assert get_candidate(db_session, "cand_batch_error").status == "labeled"


def test_candidate_batch_audit_apply_writes_single_audit_log(client, db_session, monkeypatch):
    from core.database import AdminAuditLog

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    _insert_candidate(db_session, case_id="cand_batch_audit_1")
    _insert_candidate(db_session, case_id="cand_batch_audit_2")

    response = client.post(
        "/api/v1/admin/evals/candidates/batch-audit",
        headers=_auth_header(),
        json={
            "dry_run": False,
            "case_ids": ["cand_batch_audit_1", "cand_batch_audit_2"],
            "target_dataset": "timing_gate",
            "batch_note": "写入审计",
            "decisions": [
                {"case_id": "cand_batch_audit_1", "decision": "needs_label"},
                {
                    "case_id": "cand_batch_audit_2",
                    "decision": "reject",
                    "reason_code": "low_value",
                    "note": "价值较低",
                },
            ],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["dry_run"] is False
    assert payload["audit_log_id"]

    audit = db_session.query(AdminAuditLog).filter_by(action="audit_eval_candidate_batch").one()
    assert audit.target_type == "eval_candidate_batch"
    assert audit.target_id == payload["batch_id"]
    detail = json.loads(audit.detail_json)
    assert detail["batch_id"] == payload["batch_id"]
    assert detail["batch_note"] == "写入审计"
    assert detail["counts"]["by_decision"]["needs_label"] == 1
    assert detail["counts"]["by_reason_code"]["low_value"] == 1
    assert [item["case_id"] for item in detail["items"]] == [
        "cand_batch_audit_1",
        "cand_batch_audit_2",
    ]


def test_candidate_batch_audit_rejects_invalid_scope_decision_and_stale_status(
    client,
    db_session,
    monkeypatch,
):
    from core.database import AdminAuditLog

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    _insert_candidate(db_session, case_id="cand_batch_invalid")

    cases = [
        {"dry_run": True},
        {"dry_run": True, "case_ids": ["cand_batch_invalid", "cand_batch_invalid"]},
        {
            "dry_run": True,
            "case_ids": ["cand_batch_invalid"],
            "decisions": [{"case_id": "cand_batch_invalid", "decision": "unknown"}],
        },
        {
            "dry_run": True,
            "case_ids": ["cand_batch_invalid"],
            "decisions": [
                {
                    "case_id": "cand_batch_invalid",
                    "decision": "reject",
                    "reason_code": "needs_batch_review",
                }
            ],
        },
        {
            "dry_run": False,
            "case_ids": ["missing_case"],
        },
        {
            "dry_run": False,
            "case_ids": ["cand_batch_invalid"],
            "decisions": [
                {
                    "case_id": "cand_batch_invalid",
                    "decision": "noop",
                    "expected_status": "labeled",
                }
            ],
        },
    ]

    for body in cases:
        response = client.post(
            "/api/v1/admin/evals/candidates/batch-audit",
            headers=_auth_header(),
            json=body,
        )
        assert response.status_code == 400, response.text

    assert db_session.query(AdminAuditLog).count() == 0


def test_promote_candidate_dry_run_does_not_write_or_change_status(
    db_session,
    tmp_path,
    monkeypatch,
):
    _redirect_promote_root(monkeypatch, tmp_path)
    from core.eval_sampling.store import get_candidate, label_candidate, plan_candidate_promotion

    _insert_candidate(db_session)
    label_candidate(db_session, "cand_timing_gate_1", {"timing_action": "continue"})

    plan = plan_candidate_promotion(db_session, "cand_timing_gate_1", target_dataset="timing_gate")

    assert plan["target_dataset"] == "timing_gate"
    assert plan["path"].endswith("evals/cases/timing_gate/cand_timing_gate_1.json")
    assert not (tmp_path / "evals" / "cases" / "timing_gate" / "cand_timing_gate_1.json").exists()
    assert get_candidate(db_session, "cand_timing_gate_1").status == "labeled"


def test_promote_candidate_writes_target_dataset_and_meta(db_session, tmp_path, monkeypatch):
    _redirect_promote_root(monkeypatch, tmp_path)
    from core.eval_sampling.store import get_candidate, label_candidate, promote_candidate

    _insert_candidate(db_session)
    label_candidate(db_session, "cand_timing_gate_1", {"timing_action": "continue"})

    path = promote_candidate(db_session, "cand_timing_gate_1", target_dataset="timing_gate")

    assert path.endswith("evals/cases/timing_gate/cand_timing_gate_1.json")
    target = tmp_path / "evals" / "cases" / "timing_gate" / "cand_timing_gate_1.json"
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["expected"] == {"timing_action": "continue"}
    assert data["tags"] == ["sampled", "timing_gate", "promoted"]
    assert data["meta"]["origin"] == "eval_candidate"
    assert data["meta"]["source_ref"] == "chatlog:1"
    assert get_candidate(db_session, "cand_timing_gate_1").status == "promoted"


def test_eval_promote_candidate_dry_run_uses_target_dataset(
    client,
    db_session,
    monkeypatch,
    tmp_path,
):
    _redirect_promote_root(monkeypatch, tmp_path)
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    from core.eval_sampling.store import get_candidate, label_candidate

    _insert_candidate(db_session)
    label_candidate(db_session, "cand_timing_gate_1", {"timing_action": "continue"})

    response = client.post(
        "/api/v1/admin/evals/candidates/cand_timing_gate_1/promote",
        headers=_auth_header(),
        json={"dry_run": True, "target_dataset": "timing_gate"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["dry_run"] is True
    assert payload["target_dataset"] == "timing_gate"
    assert payload["path"].endswith("evals/cases/timing_gate/cand_timing_gate_1.json")
    assert not (tmp_path / "evals" / "cases" / "timing_gate" / "cand_timing_gate_1.json").exists()
    assert get_candidate(db_session, "cand_timing_gate_1").status == "labeled"


def test_eval_promote_candidate_apply_response_matches_dry_run_contract(
    client,
    db_session,
    monkeypatch,
    tmp_path,
):
    _redirect_promote_root(monkeypatch, tmp_path)
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    from core.eval_sampling.store import label_candidate

    _insert_candidate(db_session)
    label_candidate(db_session, "cand_timing_gate_1", {"timing_action": "continue"})

    response = client.post(
        "/api/v1/admin/evals/candidates/cand_timing_gate_1/promote",
        headers=_auth_header(),
        json={"dry_run": False, "target_dataset": "timing_gate"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["dry_run"] is False
    assert payload["case_id"] == "cand_timing_gate_1"
    assert payload["suite"] == "timing_gate"
    assert payload["target_dataset"] == "timing_gate"
    assert payload["path"].endswith("evals/cases/timing_gate/cand_timing_gate_1.json")

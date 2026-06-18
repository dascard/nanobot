import json

import pytest

from core.database import EvalCandidate


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

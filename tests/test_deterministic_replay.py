from __future__ import annotations

import hashlib
import json

import pytest

from core.agent_runtime.contracts import RuntimeToolCallStatus
from core.agent_runtime.recovery import (
    RuntimeSideEffectState,
    RuntimeToolEffectClass,
)
from core.replay import (
    FrozenEvent,
    FrozenModelResponse,
    FrozenReplayFixture,
    FrozenToolOutcome,
    ReplayComponentRef,
    ReplayContractError,
    ReplayFault,
    ReplayFaultKind,
    ReplayScript,
    ReplayStatus,
    ReplayUsage,
    ReplayVariant,
    compare_replays,
    initial_replay_state,
    model_request_sha256,
    run_fault_matrix,
    run_replay,
)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _component(label: str) -> ReplayComponentRef:
    return ReplayComponentRef(label, _sha(label))


def _variant(prefix: str) -> ReplayVariant:
    return ReplayVariant(
        runtime=_component(f"{prefix}-runtime"),
        prompt=_component(f"{prefix}-prompt"),
        model=_component(f"{prefix}-model"),
        skill_set=_component(f"{prefix}-skills"),
        context_policy=_component(f"{prefix}-context"),
    )


def _fixture() -> FrozenReplayFixture:
    return FrozenReplayFixture(
        fixture_id="fixture-private-001",
        source_run_id="run-production-redacted",
        events=(
            FrozenEvent(
                event_id="event-inbound-1",
                sequence=1,
                kind="user_input",
                status="accepted",
                payload_sha256=_sha("redacted-user-input"),
            ),
            FrozenEvent(
                event_id="event-context-2",
                sequence=2,
                kind="context_manifest",
                status="resolved",
                payload_sha256=_sha("redacted-context-manifest"),
            ),
        ),
    )


def _script(
    fixture: FrozenReplayFixture,
    variant: ReplayVariant,
    *,
    output_label: str,
    with_receipt: bool = True,
) -> ReplayScript:
    state = list(initial_replay_state(fixture))
    tool = FrozenToolOutcome(
        tool_call_id="tool-call-publish-1",
        tool_name="asset_publish",
        request_sha256=_sha(f"{output_label}-tool-request"),
        result_sha256=_sha(f"{output_label}-tool-result"),
        effect_class=RuntimeToolEffectClass.EXTERNAL,
        status=RuntimeToolCallStatus.COMPLETED,
        receipt_id="receipt-publish-1" if with_receipt else "",
        receipt_state=(
            RuntimeSideEffectState.COMPLETED if with_receipt else None
        ),
    )
    first_response_sha = _sha(f"{output_label}-planning-response")
    first = FrozenModelResponse(
        step_id="model-step-1",
        request_sha256=model_request_sha256(
            fixture,
            variant,
            step_id="model-step-1",
            state_sha256s=state,
        ),
        response_sha256=first_response_sha,
        stream_chunk_sha256s=(
            _sha(f"{output_label}-chunk-1"),
            _sha(f"{output_label}-chunk-2"),
        ),
        tool_outcomes=(tool,),
        usage=ReplayUsage(
            input_tokens=120,
            output_tokens=30,
            reasoning_tokens=10,
            cost_microunits=400,
        ),
    )
    state.extend((first_response_sha, tool.result_sha256))
    second = FrozenModelResponse(
        step_id="model-step-2",
        request_sha256=model_request_sha256(
            fixture,
            variant,
            step_id="model-step-2",
            state_sha256s=state,
        ),
        response_sha256=_sha(f"{output_label}-final-response"),
        usage=ReplayUsage(
            input_tokens=80,
            output_tokens=20,
            cost_microunits=250,
        ),
    )
    return ReplayScript(variant=variant, model_responses=(first, second))


def test_frozen_fixture_and_script_round_trip_without_raw_content():
    fixture = _fixture()
    script = _script(fixture, _variant("baseline"), output_label="baseline")

    assert FrozenReplayFixture.from_dict(fixture.to_dict()) == fixture
    assert ReplayScript.from_dict(script.to_dict()) == script
    serialized = str({"fixture": fixture.to_dict(), "script": script.to_dict()})
    assert "redacted-user-input" not in serialized
    assert "planning-response" not in serialized
    assert "asset_publish" in serialized

    invalid = fixture.to_dict()
    invalid["raw_message"] = "不允许进入冻结 fixture"
    with pytest.raises(ReplayContractError, match="未允许字段"):
        FrozenReplayFixture.from_dict(invalid)


def test_semantic_replay_is_deterministic_and_never_executes_side_effects():
    fixture = _fixture()
    script = _script(fixture, _variant("baseline"), output_label="baseline")

    first = run_replay(fixture, script)
    second = run_replay(fixture, script)

    assert first.to_dict() == second.to_dict()
    assert first.status is ReplayStatus.SUCCEEDED
    assert first.to_dict()["replay_mode"] == "semantic"
    assert first.to_dict()["offline"] is True
    assert first.to_dict()["wire_exact"] is False
    assert first.model_external_call_count == 0
    assert first.tool_external_call_count == 0
    assert first.side_effect_execution_count == 0
    assert first.duplicate_side_effect_execution_count == 0
    assert first.reused_receipt_ids == ("receipt-publish-1",)
    assert [event.sequence for event in first.events] == list(
        range(1, len(first.events) + 1)
    )


def test_frozen_model_response_is_bound_to_exact_variant_request():
    fixture = _fixture()
    baseline = _script(
        fixture,
        _variant("baseline"),
        output_label="baseline",
    )
    mismatched = ReplayScript(
        variant=_variant("candidate"),
        model_responses=baseline.model_responses,
    )

    with pytest.raises(ReplayContractError, match="请求摘要不匹配"):
        run_replay(fixture, mismatched)


def test_ab_diff_covers_runtime_prompt_model_skill_and_context():
    fixture = _fixture()
    baseline = _script(
        fixture,
        _variant("baseline"),
        output_label="baseline",
    )
    candidate = _script(
        fixture,
        _variant("candidate"),
        output_label="candidate",
    )

    comparison = compare_replays(fixture, baseline, candidate).to_dict()

    assert comparison["diff"]["changed_dimensions"] == [
        "runtime",
        "prompt",
        "model",
        "skill_set",
        "context_policy",
    ]
    assert comparison["diff"]["output_changed"] is True
    assert comparison["quality_judgement"] is None
    assert comparison["requires_quality_evaluation"] is True
    assert comparison["baseline"]["substitutes"][
        "model_external_call_count"
    ] == 0


def test_standard_fault_matrix_covers_six_faults_and_prevents_duplicates():
    fixture = _fixture()
    script = _script(fixture, _variant("baseline"), output_label="baseline")

    matrix = run_fault_matrix(fixture, script)

    assert matrix["total"] == 6
    assert matrix["passed"] == 6
    assert matrix["failed"] == 0
    assert matrix["complete_coverage"] is True
    assert matrix["missing_fault_kinds"] == []
    assert matrix["duplicate_side_effect_execution_count"] == 0
    results = {item["fault_kind"]: item for item in matrix["results"]}
    assert results["model_timeout"]["report"]["status"] == "timed_out"
    assert results["stream_interrupted"]["report"]["failure_code"] == (
        "stream_interrupted"
    )
    assert results["lease_lost"]["report"]["status"] == "cancelled"
    for kind in ("db_locked", "sandbox_restarted"):
        report = results[kind]["report"]
        assert report["status"] == "succeeded"
        assert report["counts"]["recovery_count"] >= 1
        assert report["counts"][
            "duplicate_side_effect_execution_count"
        ] == 0
        assert report["reused_receipt_ids"] == ["receipt-publish-1"]


def test_effectful_tool_without_receipt_fails_closed():
    fixture = _fixture()
    script = _script(
        fixture,
        _variant("baseline"),
        output_label="baseline",
        with_receipt=False,
    )

    report = run_replay(fixture, script)

    assert report.status is ReplayStatus.FAILED
    assert report.failure_code == "unsafe_side_effect_missing_receipt"
    assert report.side_effect_execution_count == 0
    assert report.tool_external_call_count == 0


def test_db_lock_retry_exhaustion_does_not_repeat_completed_effect():
    fixture = _fixture()
    script = _script(fixture, _variant("baseline"), output_label="baseline")
    report = run_replay(
        fixture,
        script,
        fault=ReplayFault(
            ReplayFaultKind.DB_LOCKED,
            target_id="tool-call-publish-1",
            repeat_count=3,
        ),
        checkpoint_retry_limit=2,
    )

    assert report.status is ReplayStatus.FAILED
    assert report.failure_code == "db_locked"
    assert report.checkpoint_retry_count == 3
    assert report.reused_receipt_ids == ("receipt-publish-1",)
    assert report.side_effect_execution_count == 0
    assert report.duplicate_side_effect_execution_count == 0


def test_replay_cli_executes_compare_and_complete_fault_matrix(tmp_path):
    from evals.replay import main

    fixture = _fixture()
    baseline = _script(
        fixture,
        _variant("baseline"),
        output_label="baseline",
    )
    candidate = _script(
        fixture,
        _variant("candidate"),
        output_label="candidate",
    )
    compare_input = tmp_path / "compare.json"
    compare_output = tmp_path / "compare-report.json"
    compare_input.write_text(json.dumps({
        "fixture": fixture.to_dict(),
        "baseline": baseline.to_dict(),
        "candidate": candidate.to_dict(),
    }), encoding="utf-8")

    assert main([
        "compare",
        "--input",
        str(compare_input),
        "--output",
        str(compare_output),
    ]) == 0
    compare_report = json.loads(compare_output.read_text(encoding="utf-8"))
    assert compare_report["diff"]["changed_dimensions"] == list(
        ReplayVariant.DIMENSIONS
    )

    fault_input = tmp_path / "faults.json"
    fault_output = tmp_path / "fault-report.json"
    fault_input.write_text(json.dumps({
        "fixture": fixture.to_dict(),
        "script": baseline.to_dict(),
    }), encoding="utf-8")
    assert main([
        "fault-matrix",
        "--input",
        str(fault_input),
        "--output",
        str(fault_output),
    ]) == 0
    fault_report = json.loads(fault_output.read_text(encoding="utf-8"))
    assert fault_report["complete_coverage"] is True
    assert fault_report["passed"] == 6


def test_admin_replay_routes_execute_and_persist_safe_reports(
    client,
    db_session,
    monkeypatch,
):
    from core.database import AdminAuditLog
    from core.eval_sampling.store import get_run

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    headers = {"Authorization": "Bearer test-token"}
    fixture = _fixture()
    baseline = _script(
        fixture,
        _variant("baseline"),
        output_label="baseline",
    )
    candidate = _script(
        fixture,
        _variant("candidate"),
        output_label="candidate",
    )

    compare_response = client.post(
        "/api/v1/admin/evals/replay/compare",
        headers=headers,
        json={
            "fixture": fixture.to_dict(),
            "baseline": baseline.to_dict(),
            "candidate": candidate.to_dict(),
        },
    )

    assert compare_response.status_code == 200
    compare_payload = compare_response.json()
    compare_run, compare_results = get_run(
        db_session,
        compare_payload["run_id"],
    )
    assert compare_run["suite"] == "semantic_replay_compare"
    assert len(compare_results) == 2
    assert all(item["passed"] for item in compare_results)

    fault_response = client.post(
        "/api/v1/admin/evals/replay/fault-matrix",
        headers=headers,
        json={
            "fixture": fixture.to_dict(),
            "script": baseline.to_dict(),
        },
    )

    assert fault_response.status_code == 200
    fault_payload = fault_response.json()
    fault_run, fault_results = get_run(db_session, fault_payload["run_id"])
    assert fault_run["suite"] == "semantic_replay_fault_matrix"
    assert len(fault_results) == 6
    assert all(item["passed"] for item in fault_results)
    assert fault_payload["duplicate_side_effect_execution_count"] == 0

    audits = db_session.query(AdminAuditLog).filter(
        AdminAuditLog.action.in_({
            "run_semantic_replay_compare",
            "run_semantic_replay_fault_matrix",
        })
    ).all()
    assert len(audits) == 2
    assert all("redacted-user-input" not in row.detail_json for row in audits)


def test_admin_replay_route_rejects_raw_unknown_fixture_fields(
    client,
    monkeypatch,
):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    fixture = _fixture().to_dict()
    fixture["raw_prompt"] = "不应被接受"
    script = _script(
        _fixture(),
        _variant("baseline"),
        output_label="baseline",
    ).to_dict()

    response = client.post(
        "/api/v1/admin/evals/replay/compare",
        headers={"Authorization": "Bearer test-token"},
        json={
            "fixture": fixture,
            "baseline": script,
            "candidate": script,
        },
    )

    assert response.status_code == 422
    assert "未允许字段" in response.json()["detail"]

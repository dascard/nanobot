"""脱敏 trajectory 到正式 Skill 版本的经验候选闭环测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from core.db.models.observability import AgentRun
from core.db.models.skill import SkillEvaluationRow
from core.database import AdminAuditLog
from core.repositories.run_viewer import OfflineRunViewRepository
from core.skill_candidates import (
    NO_SKILL_BASELINE_SHA256,
    SKILL_CANDIDATE_SCHEMA_VERSION,
    SkillCandidateContractError,
    SkillCandidateEvaluationEvidence,
    SkillCandidateStore,
    SkillDraftSpec,
    SkillExperienceCandidate,
    evaluate_skill_candidate,
    extract_skill_candidate,
    publish_candidate_to_skill_registry,
    sanitize_experience_text,
    skill_candidate_catalog_payload,
)
from core.skills import (
    SkillLifecycleService,
    SkillScopeTarget,
    parse_skill_bundle,
)
from evals.harness_registry import EVAL_HARNESS_REGISTRY
from evals.skill_candidates import main as skill_candidate_cli_main


SOURCE_REVISION = "1" * 40
DATASET_SHA256 = "2" * 64
EVIDENCE_SHA256 = "3" * 64


def _redaction() -> dict[str, str]:
    return {
        "hidden_reasoning": "omitted",
        "prompt_and_messages": "omitted",
        "tool_arguments_and_results": "omitted",
        "sandbox_command_and_output": "omitted",
        "secrets_and_credentials": "omitted",
        "available_evidence": "hashes_counts_statuses_and_versions",
    }


def _run_view(
    run_id: str,
    *,
    status: str,
    tool_name: str = "web_search",
    secret_suffix: str = "",
) -> dict[str, object]:
    failed = status != "succeeded"
    tool_status = "failed" if failed else "succeeded"
    timeline = [
        {
            "span_id": f"run:{run_id}",
            "parent_span_id": "",
            "kind": "run",
            "name": "agent.reply",
            "status": status,
            "turn_id": "turn-1",
            "started_at": "2026-08-09T00:00:00+00:00",
            "finished_at": "2026-08-09T00:00:01+00:00",
            "duration_ms": 1000,
            "offset_ms": 0,
            "attempt": 1,
        },
        {
            "span_id": f"prompt:{run_id}",
            "parent_span_id": f"run:{run_id}",
            "kind": "prompt",
            "name": "chat.main",
            "status": "succeeded",
            "turn_id": "turn-1",
            "started_at": "2026-08-09T00:00:00+00:00",
            "finished_at": "2026-08-09T00:00:00+00:00",
            "duration_ms": 1,
            "offset_ms": 0,
            "attempt": 1,
        },
        {
            "span_id": f"tool:{run_id}",
            "parent_span_id": f"run:{run_id}",
            "kind": "tool",
            "name": f"{tool_name}{secret_suffix}",
            "status": tool_status,
            "turn_id": "turn-1",
            "started_at": "2026-08-09T00:00:00+00:00",
            "finished_at": "2026-08-09T00:00:01+00:00",
            "duration_ms": 500,
            "offset_ms": 100,
            "attempt": 1,
        },
        {
            "span_id": f"llm:{run_id}",
            "parent_span_id": f"prompt:{run_id}",
            "kind": "llm",
            "name": "gateway/model-a",
            "status": "succeeded" if not failed else "failed",
            "turn_id": "turn-1",
            "started_at": "2026-08-09T00:00:00+00:00",
            "finished_at": "2026-08-09T00:00:01+00:00",
            "duration_ms": 400,
            "offset_ms": 500,
            "attempt": 1,
        },
    ]
    failures = []
    if failed:
        failures = [{
            "span_id": f"tool:{run_id}",
            "kind": "tool",
            "name": f"{tool_name}{secret_suffix}",
            "status": "failed",
            "code": "tool_execution_failed",
            "error_type": "timeout",
            "retryable": True,
        }]
    return {
        "schema_version": "1.0",
        "source": "persisted_evidence",
        "offline": True,
        "run_id": run_id,
        "trace_id": f"trace-{run_id}",
        "turn_ids": ["turn-1"],
        "summary": {
            "status": status,
            "duration_ms": 1000,
            "span_count": len(timeline),
            "failed_span_count": len(failures),
            "retry_count": 0,
            "recovery_count": 0,
        },
        "spans": [],
        "timeline": timeline,
        "dag": {"nodes": [], "edges": []},
        "waterfall": {"totals": {}, "items": []},
        "context_manifest": {"available": False},
        "failures": failures,
        "retries": [],
        "recoveries": [],
        "versions": {},
        "redaction": _redaction(),
    }


def _spec(
    *,
    name: str = "research-workflow",
    version: str = "1.0.0",
    baseline: str = NO_SKILL_BASELINE_SHA256,
    target_key: str = "qq:user:skill-candidate",
    allowed_tools: tuple[str, ...] = ("web_search",),
) -> SkillDraftSpec:
    return SkillDraftSpec(
        name=name,
        version=version,
        description="复用成功流程并规避失败模式",
        target_scope="user",
        target_scope_key=target_key,
        baseline_bundle_sha256=baseline,
        source_revision=SOURCE_REVISION,
        created_at="2026-08-09T01:00:00+00:00",
        capability_tags=("深入研究", "research"),
        applies_to=("chat", "private"),
        allowed_tools=allowed_tools,
    )


def _candidate(
    *,
    spec: SkillDraftSpec | None = None,
) -> SkillExperienceCandidate:
    return extract_skill_candidate(
        (
            _run_view("run-success", status="succeeded"),
            _run_view("run-failure", status="failed"),
        ),
        spec=spec or _spec(),
        extra_evidence_by_run={
            "run-success": ("4" * 64,),
            "run-failure": ("5" * 64,),
        },
    )


def _evaluation(
    candidate: SkillExperienceCandidate,
    **overrides,
) -> SkillCandidateEvaluationEvidence:
    values = {
        "schema_version": SKILL_CANDIDATE_SCHEMA_VERSION,
        "candidate_sha256": candidate.candidate_sha256,
        "candidate_bundle_sha256": candidate.parsed_bundle.bundle_sha256,
        "baseline_bundle_sha256": candidate.baseline_bundle_sha256,
        "source_revision": candidate.source_revision,
        "harness_registry_sha256": EVAL_HARNESS_REGISTRY.sha256,
        "dataset_sha256": DATASET_SHA256,
        "suite_id": "skill-experience-heldout-v1",
        "evaluator_id": "independent-skill-gate",
        "evaluator_version": "1.0.0",
        "started_at": "2026-08-09T02:00:00+00:00",
        "finished_at": "2026-08-09T02:05:00+00:00",
        "safety_passed": True,
        "safety_failure_count": 0,
        "baseline_score_micros": 700_000,
        "candidate_score_micros": 800_000,
        "baseline_cost_microunits": 100,
        "candidate_cost_microunits": 110,
        "approved_cost_microunits": 120,
        "source_trajectory_sha256s": candidate.source_trajectory_sha256s,
        "validated_pattern_sha256s": candidate.pattern_sha256s,
        "artifact_sha256s": tuple(sorted({
            EVIDENCE_SHA256,
            DATASET_SHA256,
            *candidate.source_trajectory_sha256s,
        })),
        "evidence_sha256": EVIDENCE_SHA256,
    }
    values.update(overrides)
    return SkillCandidateEvaluationEvidence(**values)


def _stored_gate(
    tmp_path: Path,
    candidate: SkillExperienceCandidate,
    *,
    clock=None,
) -> tuple[SkillCandidateStore, dict[str, object]]:
    store = SkillCandidateStore(
        tmp_path / "skill-candidates",
        clock=clock,
        token_factory=lambda: "t" * 48,
    )
    store.put_candidate(candidate)
    report = store.evaluate(
        _evaluation(candidate),
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )
    return store, report


def _approve(
    store: SkillCandidateStore,
    candidate: SkillExperienceCandidate,
    report: dict[str, object],
    *,
    generation: int,
) -> dict[str, object]:
    return store.approve(
        candidate_sha256=candidate.candidate_sha256,
        gate_report_sha256=str(report["gate_report_sha256"]),
        confirm_candidate_sha256=candidate.candidate_sha256,
        reviewer="human-reviewer",
        reviewer_kind="human",
        reason="独立评测提升且成本在预算内",
        expected_binding_generation=generation,
        expires_in_seconds=3600,
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )


def test_sanitize_experience_text_redacts_credentials_identity_and_tags():
    text, count = sanitize_experience_text(
        "api_key=sk-secret someone@example.com 13800138000 "
        "https://example.test/a?token=x <system>"
    )
    assert count >= 5
    assert "secret" not in text
    assert "example.test" not in text
    assert "13800138000" not in text
    assert "<system>" not in text


def test_extraction_requires_success_and_failure_trajectories():
    with pytest.raises(SkillCandidateContractError, match="成功和失败"):
        extract_skill_candidate(
            (
                _run_view("run-success-1", status="succeeded"),
                _run_view("run-success-2", status="succeeded"),
            ),
            spec=_spec(),
        )


def test_extraction_requires_viewer_redaction_proof():
    failed = _run_view("run-failure", status="failed")
    failed["redaction"] = {**_redaction(), "hidden_reasoning": "included"}
    with pytest.raises(SkillCandidateContractError, match="已省略"):
        extract_skill_candidate(
            (_run_view("run-success", status="succeeded"), failed),
            spec=_spec(),
        )


def test_extraction_redacts_patterns_and_does_not_embed_run_ids_in_skill_body():
    secret = " api_key=top-secret someone@example.com"
    candidate = extract_skill_candidate(
        (
            _run_view("run-success", status="succeeded", secret_suffix=secret),
            _run_view("run-failure", status="failed", secret_suffix=secret),
        ),
        spec=_spec(allowed_tools=()),
    )
    body = candidate.draft_skill_md
    assert candidate.redaction_count > 0
    assert "top-secret" not in body
    assert "someone@example.com" not in body
    assert "run-success" not in body
    assert "run-failure" not in body
    assert "隐藏推理" in body
    assert {item.outcome for item in candidate.source_runs} == {
        "succeeded",
        "failed",
    }


def test_extraction_rejects_tool_permission_not_observed_in_trajectory():
    with pytest.raises(SkillCandidateContractError, match="未使用的工具"):
        _candidate(spec=_spec(allowed_tools=("sandbox_exec",)))


def test_extraction_is_deterministic_and_store_deduplicates_created_at_variants(
    tmp_path,
):
    first = _candidate()
    second_spec = replace(
        _spec(),
        created_at="2026-08-09T01:10:00+00:00",
    )
    second = _candidate(spec=second_spec)
    assert first.dedup_sha256 == second.dedup_sha256
    assert first.candidate_sha256 != second.candidate_sha256
    store = SkillCandidateStore(tmp_path / "store")
    stored_first = store.put_candidate(first)
    stored_second = store.put_candidate(second)
    assert stored_first["deduplicated"] is False
    assert stored_second["deduplicated"] is True
    assert stored_second["candidate_sha256"] == first.candidate_sha256


def test_candidate_round_trip_verifies_skill_projection_and_hash():
    candidate = _candidate()
    restored = SkillExperienceCandidate.from_dict(candidate.to_dict())
    assert restored == candidate
    tampered = candidate.to_dict()
    tampered["bundle_sha256"] = "f" * 64
    with pytest.raises(SkillCandidateContractError, match="投影"):
        SkillExperienceCandidate.from_dict(tampered)


def test_candidate_contract_rejects_forged_source_support_and_permissions():
    candidate = _candidate()
    forged_step = replace(
        candidate.process_steps[0],
        supporting_run_ids=("run-failure",),
        pattern_sha256="",
    )
    with pytest.raises(SkillCandidateContractError, match="成功来源"):
        replace(
            candidate,
            process_steps=(forged_step, *candidate.process_steps[1:]),
            dedup_sha256="",
            candidate_sha256="",
        )
    forged_md = candidate.draft_skill_md.replace(
        'nanobot.permissions: "tool:web_search"',
        'nanobot.permissions: "network:search"',
    )
    with pytest.raises(SkillCandidateContractError, match="权限"):
        replace(
            candidate,
            draft_skill_md=forged_md,
            dedup_sha256="",
            candidate_sha256="",
        )


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"evaluator_id": "trajectory-skill-extractor"}, "evaluator_not_independent"),
        ({"harness_registry_sha256": "a" * 64}, "harness_registry_drift"),
        ({"safety_passed": False}, "safety_gate_failed"),
        ({"safety_failure_count": 1}, "safety_gate_failed"),
        ({"candidate_score_micros": 700_000}, "quality_not_improved"),
        ({"candidate_cost_microunits": 121}, "cost_budget_exceeded"),
        (
            {
                "candidate_cost_microunits": 121,
                "approved_cost_microunits": 200,
            },
            "cost_regression_exceeded",
        ),
        ({"source_trajectory_sha256s": ("a" * 64,)}, "trajectory_evidence_mismatch"),
        ({"validated_pattern_sha256s": ("b" * 64,)}, "pattern_coverage_incomplete"),
    ],
)
def test_independent_gate_blocks_incomplete_or_regressed_evidence(override, code):
    candidate = _candidate()
    evidence = _evaluation(candidate, **override)
    report = evaluate_skill_candidate(
        candidate,
        evidence,
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )
    assert report["passed"] is False
    assert code in {item["code"] for item in report["errors"]}


def test_gate_requires_artifacts_for_dataset_and_every_source_trajectory():
    candidate = _candidate()
    evidence = _evaluation(
        candidate,
        artifact_sha256s=(EVIDENCE_SHA256,),
    )
    report = evaluate_skill_candidate(
        candidate,
        evidence,
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )
    assert report["passed"] is False
    assert report["errors"][0]["code"] == "artifact_evidence_incomplete"


def test_gate_passes_and_answers_baseline_quality_cost_and_sources():
    candidate = _candidate()
    report = evaluate_skill_candidate(
        candidate,
        _evaluation(candidate),
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )
    assert report["passed"] is True
    assert report["baseline_bundle_sha256"] == NO_SKILL_BASELINE_SHA256
    assert report["quality_delta_micros"] == 100_000
    assert report["candidate_cost_microunits"] == 110
    assert report["source_run_ids"] == ["run-failure", "run-success"]


def test_store_rebuilds_gate_and_rejects_tampered_report(tmp_path):
    candidate = _candidate()
    store, report = _stored_gate(tmp_path, candidate)
    path = (
        store.root
        / "gate_reports"
        / f"{report['gate_report_sha256']}.json"
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    value["candidate_score_micros"] = 999_999
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(SkillCandidateContractError, match="篡改"):
        store.get_gate_report(
            str(report["gate_report_sha256"]),
            current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
        )


def test_approval_requires_exact_hash_passed_gate_and_independent_human(tmp_path):
    candidate = _candidate()
    store, report = _stored_gate(tmp_path, candidate)
    with pytest.raises(SkillCandidateContractError, match="hash"):
        store.approve(
            candidate_sha256=candidate.candidate_sha256,
            gate_report_sha256=str(report["gate_report_sha256"]),
            confirm_candidate_sha256="a" * 64,
            reviewer="human-reviewer",
            reviewer_kind="human",
            reason="确认",
            expected_binding_generation=0,
            expires_in_seconds=3600,
            current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
        )
    with pytest.raises(SkillCandidateContractError, match="独立"):
        store.approve(
            candidate_sha256=candidate.candidate_sha256,
            gate_report_sha256=str(report["gate_report_sha256"]),
            confirm_candidate_sha256=candidate.candidate_sha256,
            reviewer=candidate.generator_id,
            reviewer_kind="human",
            reason="确认",
            expected_binding_generation=0,
            expires_in_seconds=3600,
            current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
        )


def test_failed_gate_cannot_be_human_approved(tmp_path):
    candidate = _candidate()
    store = SkillCandidateStore(tmp_path / "failed-gate")
    store.put_candidate(candidate)
    report = store.evaluate(
        _evaluation(candidate, candidate_score_micros=600_000),
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )
    assert report["passed"] is False
    with pytest.raises(SkillCandidateContractError, match="未通过"):
        store.approve(
            candidate_sha256=candidate.candidate_sha256,
            gate_report_sha256=str(report["gate_report_sha256"]),
            confirm_candidate_sha256=candidate.candidate_sha256,
            reviewer="human-reviewer",
            reviewer_kind="human",
            reason="不能批准失败门禁",
            expected_binding_generation=0,
            expires_in_seconds=3600,
            current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
        )


def test_approval_token_plaintext_is_returned_once_and_never_persisted(tmp_path):
    candidate = _candidate()
    store, report = _stored_gate(tmp_path, candidate)
    approval = _approve(store, candidate, report, generation=0)
    token = approval["approval_token"]
    assert token == "t" * 48
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in store.root.rglob("*.json")
    )
    assert token not in persisted
    assert "approval_token" not in persisted
    assert "token_sha256" not in json.dumps(
        store.get_approval(str(approval["approval_id"]))
    )


def test_expired_approval_cannot_publish(tmp_path):
    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    candidate = _candidate()
    store, report = _stored_gate(tmp_path, candidate, clock=lambda: now)
    approval = _approve(store, candidate, report, generation=0)
    expired_store = SkillCandidateStore(
        store.root,
        clock=lambda: now + timedelta(hours=2),
    )
    with pytest.raises(SkillCandidateContractError, match="过期"):
        expired_store.publish(
            candidate_sha256=candidate.candidate_sha256,
            approval_id=str(approval["approval_id"]),
            approval_token=str(approval["approval_token"]),
            current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
            publisher=lambda *_args: {},
        )


def test_new_skill_publication_is_real_registry_install_with_evaluation_and_receipt(
    tmp_path,
    db_session,
):
    candidate = _candidate()
    store, report = _stored_gate(tmp_path, candidate)
    approval = _approve(store, candidate, report, generation=0)
    receipt = store.publish(
        candidate_sha256=candidate.candidate_sha256,
        approval_id=str(approval["approval_id"]),
        approval_token=str(approval["approval_token"]),
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
        publisher=lambda item, gate, human: publish_candidate_to_skill_registry(
            db_session,
            item,
            gate,
            human,
        ),
    )
    binding = next(
        item
        for item in SkillLifecycleService(db_session).list_bindings(
            target=candidate.target
        )
        if item.skill_name == candidate.parsed_bundle.name
    )
    assert receipt["publication_mode"] == "installed_active"
    assert receipt["rollback_action"] == "skill.uninstall"
    assert receipt["rollback"] == {
        "required_for_runtime_revert": True,
        "method": "POST",
        "path": "/api/v1/admin/skills/uninstall",
        "body": {
            "scope": "user",
            "scope_key": "qq:user:skill-candidate",
            "skill_name": "research-workflow",
            "expected_generation": receipt["binding_generation"],
        },
    }
    assert binding.active_package_id == receipt["package_id"]
    assert binding.active_version == "1.0.0"
    evaluation = db_session.execute(
        select(SkillEvaluationRow).where(
            SkillEvaluationRow.package_id == receipt["package_id"]
        )
    ).scalar_one()
    assert evaluation.passed is True
    assert evaluation.evidence_sha256 == report["gate_report_sha256"]
    assert receipt["reviewer"] == "human-reviewer"
    assert receipt["quality_delta_micros"] == 100_000
    assert receipt["candidate_cost_microunits"] == 110
    assert store.get_publication(str(receipt["publication_id"])) == receipt
    rolled_back = SkillLifecycleService(db_session).uninstall(
        candidate.target,
        candidate.parsed_bundle.name,
        expected_generation=int(receipt["binding_generation"]),
        actor_id="human:rollback-reviewer",
    )
    db_session.commit()
    assert rolled_back.status == "uninstalled"
    assert rolled_back.previous_package_id == receipt["package_id"]


def _base_bundle(name: str, version: str):
    return parse_skill_bundle(
        (
            "---\n"
            f"name: {name}\n"
            "description: 已发布基础 Skill。\n"
            "metadata:\n"
            f'  version: "{version}"\n'
            '  nanobot.permissions: ""\n'
            '  nanobot.capabilities: "research"\n'
            '  nanobot.applies-to: "chat"\n'
            "---\n\n"
            "# 基础流程\n\n保持当前稳定行为。\n"
        ).encode("utf-8")
    )


def test_existing_skill_publication_stages_version_without_runtime_activation(
    tmp_path,
    db_session,
):
    target = SkillScopeTarget("user", "qq:user:existing-skill")
    base = _base_bundle("research-workflow", "1.0.0")
    before = SkillLifecycleService(db_session).install(
        target,
        base,
        actor_id="admin",
        source_label="manual",
        trusted_source=True,
    )
    db_session.commit()
    candidate = _candidate(spec=_spec(
        version="2.0.0",
        baseline=base.bundle_sha256,
        target_key=target.scope_key,
    ))
    store, report = _stored_gate(tmp_path, candidate)
    approval = _approve(store, candidate, report, generation=before.generation)
    receipt = store.publish(
        candidate_sha256=candidate.candidate_sha256,
        approval_id=str(approval["approval_id"]),
        approval_token=str(approval["approval_token"]),
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
        publisher=lambda item, gate, human: publish_candidate_to_skill_registry(
            db_session,
            item,
            gate,
            human,
        ),
    )
    after = next(
        item
        for item in SkillLifecycleService(db_session).list_bindings(target=target)
        if item.skill_name == "research-workflow"
    )
    versions = SkillLifecycleService(db_session).list_versions(
        target=target,
        skill_name="research-workflow",
    )
    assert receipt["publication_mode"] == "version_staged"
    assert receipt["rollback_action"] == "none_runtime_unchanged"
    assert receipt["rollback"]["required_for_runtime_revert"] is False
    assert after.active_package_id == before.active_package_id
    assert after.active_version == "1.0.0"
    assert {item.version for item in versions} == {"1.0.0", "2.0.0"}


def test_publication_rejects_baseline_drift_and_does_not_consume_token(
    tmp_path,
    db_session,
):
    target = SkillScopeTarget("user", "qq:user:drift-skill")
    base = _base_bundle("research-workflow", "1.0.0")
    before = SkillLifecycleService(db_session).install(
        target,
        base,
        actor_id="admin",
        trusted_source=True,
    )
    db_session.commit()
    candidate = _candidate(spec=_spec(
        version="2.0.0",
        baseline="a" * 64,
        target_key=target.scope_key,
    ))
    store, report = _stored_gate(tmp_path, candidate)
    approval = _approve(store, candidate, report, generation=before.generation)
    with pytest.raises(SkillCandidateContractError, match="基线"):
        store.publish(
            candidate_sha256=candidate.candidate_sha256,
            approval_id=str(approval["approval_id"]),
            approval_token=str(approval["approval_token"]),
            current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
            publisher=lambda item, gate, human: (
                publish_candidate_to_skill_registry(
                    db_session,
                    item,
                    gate,
                    human,
                )
            ),
        )
    assert store.state()["publication_ids"] == []


def test_publication_token_is_single_use(tmp_path, db_session):
    candidate = _candidate(spec=_spec(target_key="qq:user:single-use"))
    store, report = _stored_gate(tmp_path, candidate)
    approval = _approve(store, candidate, report, generation=0)
    kwargs = {
        "candidate_sha256": candidate.candidate_sha256,
        "approval_id": str(approval["approval_id"]),
        "approval_token": str(approval["approval_token"]),
        "current_harness_registry_sha256": EVAL_HARNESS_REGISTRY.sha256,
        "publisher": lambda item, gate, human: (
            publish_candidate_to_skill_registry(
                db_session,
                item,
                gate,
                human,
            )
        ),
    }
    store.publish(**kwargs)
    with pytest.raises(SkillCandidateContractError, match="已用于发布"):
        store.publish(**kwargs)


def test_offline_cli_extracts_deduplicates_and_gates(tmp_path, capsys):
    spec_path = tmp_path / "spec.json"
    success_path = tmp_path / "success.json"
    failure_path = tmp_path / "failure.json"
    spec_path.write_text(json.dumps(_spec().to_dict()), encoding="utf-8")
    success_path.write_text(
        json.dumps(_run_view("run-success", status="succeeded")),
        encoding="utf-8",
    )
    failure_path.write_text(
        json.dumps(_run_view("run-failure", status="failed")),
        encoding="utf-8",
    )
    root = tmp_path / "cli-store"
    args = [
        "--root",
        str(root),
        "extract",
        "--spec",
        str(spec_path),
        "--run-view",
        str(success_path),
        "--run-view",
        str(failure_path),
    ]
    assert skill_candidate_cli_main(args) == 0
    candidate_payload = json.loads(capsys.readouterr().out)
    assert candidate_payload["deduplicated"] is False
    assert skill_candidate_cli_main(args) == 0
    assert json.loads(capsys.readouterr().out)["deduplicated"] is True

    candidate = SkillCandidateStore(root).get_candidate(
        candidate_payload["candidate_sha256"]
    )
    evidence_path = tmp_path / "evaluation.json"
    evidence_path.write_text(
        json.dumps(_evaluation(candidate).to_dict()),
        encoding="utf-8",
    )
    assert skill_candidate_cli_main([
        "--root",
        str(root),
        "gate",
        "--evidence",
        str(evidence_path),
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["passed"] is True


def test_catalog_declares_isolation_human_gate_and_no_auto_existing_activation():
    catalog = skill_candidate_catalog_payload()
    assert catalog["candidate_area"] == "isolated_content_addressed_store"
    assert catalog["publication"]["approval"] == (
        "human_exact_hash_single_use_token"
    )
    assert catalog["publication"]["existing_skill"] == (
        "stage_version_without_activation"
    )
    assert "automatic_existing_skill_activation" in catalog["blocked_actions"]
    assert "broad_scope_new_skill_activation" in catalog["blocked_actions"]


def test_new_skill_publication_rejects_broad_scope_auto_activation(
    tmp_path,
    db_session,
):
    candidate = _candidate(spec=replace(
        _spec(target_key="project:shared"),
        target_scope="project",
    ))
    store, report = _stored_gate(tmp_path, candidate)
    approval = _approve(store, candidate, report, generation=0)
    with pytest.raises(SkillCandidateContractError, match="user scope"):
        store.publish(
            candidate_sha256=candidate.candidate_sha256,
            approval_id=str(approval["approval_id"]),
            approval_token=str(approval["approval_token"]),
            current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
            publisher=lambda item, gate, human: (
                publish_candidate_to_skill_registry(
                    db_session,
                    item,
                    gate,
                    human,
                )
            ),
        )


def test_persisted_run_repository_builds_redacted_view_from_real_agent_run(
    db_session,
):
    db_session.add(AgentRun(
        run_id="run-persisted-candidate",
        trace_id="trace-persisted-candidate",
        session_id="session-persisted-candidate",
        user_id="user-persisted-candidate",
        chat_type="private",
        status="succeeded",
        run_type="chat",
    ))
    db_session.commit()
    view = OfflineRunViewRepository(db_session).build_persisted(
        "run-persisted-candidate"
    )
    assert view["run_id"] == "run-persisted-candidate"
    assert view["offline"] is True
    assert view["summary"]["status"] == "succeeded"
    assert view["redaction"]["hidden_reasoning"] == "omitted"


def test_admin_api_executes_extract_gate_approval_and_real_publication(
    client,
    db_session,
    monkeypatch,
    tmp_path,
):
    from api import admin_routes
    from api.admin import skill_candidate_routes

    root = tmp_path / "api-skill-candidates"
    monkeypatch.setattr(skill_candidate_routes, "SKILL_CANDIDATE_ROOT", root)
    monkeypatch.setattr(admin_routes, "NANOBOT_ADMIN_TOKEN", "skill-candidate-token")
    viewers = {
        "run-api-success": _run_view("run-api-success", status="succeeded"),
        "run-api-failure": _run_view("run-api-failure", status="failed"),
    }
    monkeypatch.setattr(
        OfflineRunViewRepository,
        "build_persisted",
        lambda _self, run_id: viewers[run_id],
    )
    headers = {"Authorization": "Bearer skill-candidate-token"}
    assert client.get(
        "/api/v1/admin/skills/candidates/catalog"
    ).status_code == 401

    extracted = client.post(
        "/api/v1/admin/skills/candidates/extract",
        headers=headers,
        json={
            "run_ids": ["run-api-success", "run-api-failure"],
            "name": "api-experience-skill",
            "version": "1.0.0",
            "description": "API 经验候选完整链路",
            "target_scope": "user",
            "target_scope_key": "qq:user:api-skill-candidate",
            "baseline_bundle_sha256": NO_SKILL_BASELINE_SHA256,
            "source_revision": SOURCE_REVISION,
            "created_at": "2026-08-09T05:00:00+00:00",
            "capability_tags": ["经验流程"],
            "applies_to": ["private"],
            "allowed_tools": ["web_search"],
            "extra_evidence_by_run": {
                "run-api-success": ["8" * 64],
                "run-api-failure": ["9" * 64],
            },
        },
    )
    assert extracted.status_code == 201, extracted.text
    candidate_sha = extracted.json()["candidate_sha256"]
    candidate = SkillCandidateStore(root).get_candidate(candidate_sha)

    gate_response = client.post(
        "/api/v1/admin/skills/candidates/evaluations",
        headers=headers,
        json={"evidence": _evaluation(candidate).to_dict()},
    )
    assert gate_response.status_code == 201, gate_response.text
    report = gate_response.json()
    assert report["passed"] is True

    approval_response = client.post(
        "/api/v1/admin/skills/candidates/approvals",
        headers=headers,
        json={
            "candidate_sha256": candidate_sha,
            "gate_report_sha256": report["gate_report_sha256"],
            "confirm_candidate_sha256": candidate_sha,
            "reviewer_kind": "human",
            "reason": "API 人工确认独立评测结果。",
            "expected_binding_generation": 0,
            "expires_in_seconds": 3600,
        },
    )
    assert approval_response.status_code == 201, approval_response.text
    approval = approval_response.json()
    token = approval["approval_token"]
    assert "token_sha256" not in approval

    published = client.post(
        "/api/v1/admin/skills/candidates/publish",
        headers=headers,
        json={
            "candidate_sha256": candidate_sha,
            "approval_id": approval["approval_id"],
            "approval_token": token,
        },
    )
    assert published.status_code == 201, published.text
    receipt = published.json()
    assert receipt["publication_mode"] == "installed_active"
    assert receipt["rollback_action"] == "skill.uninstall"

    listed = client.get(
        "/api/v1/admin/skills",
        headers=headers,
        params={
            "scope": "user",
            "scope_key": "qq:user:api-skill-candidate",
            "skill_name": "api-experience-skill",
        },
    )
    assert listed.status_code == 200
    assert any(
        item["package_id"] == receipt["package_id"]
        for item in listed.json()["versions"]
    )
    state = client.get(
        "/api/v1/admin/skills/candidates/state",
        headers=headers,
    )
    assert state.status_code == 200
    assert receipt["publication_id"] in state.json()["publication_ids"]

    audit_text = "\n".join(
        row.detail_json
        for row in db_session.query(AdminAuditLog).filter(
            AdminAuditLog.action.like("skill_candidate.%")
        )
    )
    assert token not in audit_text
    assert "approval_token\"" not in audit_text


def test_public_modules_do_not_expose_network_git_or_self_approval():
    source = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "core/skill_candidates/extraction.py",
            "core/skill_candidates/store.py",
            "evals/skill_candidates.py",
        )
    )
    assert "import subprocess" not in source
    assert "git commit" not in source
    assert "git push" not in source
    assert "requests." not in source
    assert "httpx." not in source

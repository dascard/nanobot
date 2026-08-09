from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from core.database import AdminAuditLog
from core.evolution_control import (
    EVOLUTION_SCHEMA_VERSION,
    EvolutionCandidateBundle,
    EvolutionContractError,
    EvolutionControlStore,
    EvolutionGateEvidence,
    EvolutionGenerationProof,
    EvolutionSplitResult,
    EvolutionTarget,
    FrozenDatasetManifest,
    FrozenDatasetSplit,
    evolution_catalog_payload,
)
from evals.evolution_control import main as evolution_cli_main
from evals.harness_registry import EVAL_HARNESS_REGISTRY


SOURCE_REVISION = "a" * 40
BASE_SHA256 = "b" * 64
PROMPT_SHA256 = "c" * 64
OFFLINE_GATE_SHA256 = "d" * 64
SAFETY_SHA256 = "e" * 64
EVIDENCE_SHA256 = "f" * 64
FIXED_NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


def _manifest(*, source_revision: str = SOURCE_REVISION) -> FrozenDatasetManifest:
    splits = tuple(
        FrozenDatasetSplit(
            role=role,
            source_id=f"fixture/{role}",
            revision="fixture-v1",
            license_id="MIT",
            artifact_sha256=f"{index:x}" * 64,
            expected_count=10,
            answers_visible_to_generator=role in {"baseline", "training"},
        )
        for index, role in enumerate(
            ("baseline", "training", "validation", "test"),
            start=1,
        )
    )
    return FrozenDatasetManifest(
        schema_version=EVOLUTION_SCHEMA_VERSION,
        dataset_id="agent_eval_v1",
        revision="frozen-v1",
        source_revision=source_revision,
        created_at=FIXED_NOW.isoformat(),
        splits=splits,
    )


def _target(
    *,
    content_sha256: str = PROMPT_SHA256,
    kind: str = "prompt",
    changes: dict | None = None,
) -> EvolutionTarget:
    if changes is None:
        changes = {
            "bundle_id": "prompts.v2.candidate",
            "version": "2026.08.09",
            "content_sha256": content_sha256,
            "artifact_uri": f"artifact://sha256/{content_sha256}",
        }
    resource_id = (
        str(changes["route_key"])
        if kind == "routing" and "route_key" in changes
        else f"{kind}/default"
    )
    return EvolutionTarget(
        kind=kind,
        resource_id=resource_id,
        base_sha256=BASE_SHA256,
        changes=changes,
    )


def _candidate(
    manifest: FrozenDatasetManifest,
    *,
    candidate_id: str = "candidate_v1",
    content_sha256: str = PROMPT_SHA256,
    generator_id: str = "offline_generator_v1",
) -> EvolutionCandidateBundle:
    return EvolutionCandidateBundle(
        schema_version=EVOLUTION_SCHEMA_VERSION,
        candidate_id=candidate_id,
        created_at=FIXED_NOW.isoformat(),
        dataset_sha256=manifest.dataset_sha256,
        generation=EvolutionGenerationProof(
            lane="offline_deterministic",
            actor_kind="offline_generator",
            generator_id=generator_id,
            generator_version="1.0.0",
            source_revision=manifest.source_revision,
            seed=42,
            parent_bundle_sha256="",
            production_data_access=False,
            network_access=False,
            repository_write_access=False,
        ),
        target=_target(content_sha256=content_sha256),
        rationale="基于冻结训练集生成的最小 Prompt 候选。",
        evidence_sha256s=(EVIDENCE_SHA256,),
    )


def _split_result(
    manifest: FrozenDatasetManifest,
    role: str,
    *,
    expected_total: int | None = None,
    baseline_score_micros: int = 600_000,
    candidate_score_micros: int = 600_001,
    domain_regression_count: int = 0,
) -> EvolutionSplitResult:
    total = (
        manifest.split(role).expected_count
        if expected_total is None
        else expected_total
    )
    return EvolutionSplitResult(
        role=role,
        dataset_artifact_sha256=manifest.split(role).artifact_sha256,
        expected_total=total,
        correct=total,
        incorrect=0,
        infrastructure_failure=0,
        timeout=0,
        explicitly_excluded=0,
        missing=0,
        baseline_score_micros=baseline_score_micros,
        candidate_score_micros=candidate_score_micros,
        domain_regression_count=domain_regression_count,
    )


def _evidence(
    candidate: EvolutionCandidateBundle,
    manifest: FrozenDatasetManifest,
    **overrides,
) -> EvolutionGateEvidence:
    values = {
        "schema_version": EVOLUTION_SCHEMA_VERSION,
        "candidate_sha256": candidate.candidate_sha256,
        "dataset_sha256": manifest.dataset_sha256,
        "source_revision": manifest.source_revision,
        "harness_registry_sha256": EVAL_HARNESS_REGISTRY.sha256,
        "offline_gate_report_sha256": OFFLINE_GATE_SHA256,
        "offline_gate_passed": True,
        "safety_suite_id": "evolution_safety_v1",
        "safety_passed": True,
        "safety_failures": 0,
        "safety_artifact_sha256": SAFETY_SHA256,
        "evaluator_id": "independent_evaluator_v1",
        "evaluator_version": "1.0.0",
        "started_at": FIXED_NOW.isoformat(),
        "finished_at": FIXED_NOW.isoformat(),
        "split_results": (
            _split_result(manifest, "validation"),
            _split_result(
                manifest,
                "test",
                candidate_score_micros=600_000,
            ),
        ),
        "approved_cost_microunits": 1_200,
        "baseline_cost_microunits": 1_000,
        "candidate_cost_microunits": 1_100,
        "artifact_sha256s": (
            OFFLINE_GATE_SHA256,
            SAFETY_SHA256,
            EVIDENCE_SHA256,
        ),
    }
    values.update(overrides)
    return EvolutionGateEvidence(**values)


def _stored_candidate(tmp_path: Path):
    store = EvolutionControlStore(tmp_path / "evolution", clock=lambda: FIXED_NOW)
    manifest = _manifest()
    candidate = _candidate(manifest)
    store.put_dataset(manifest)
    store.put_candidate(candidate)
    return store, manifest, candidate


def test_dataset_freezes_exact_four_splits_and_seals_held_out_metadata():
    manifest = _manifest()

    restored = FrozenDatasetManifest.from_dict(manifest.to_dict())
    generation_view = restored.generation_view()

    assert tuple(item.role for item in restored.splits) == (
        "baseline",
        "training",
        "validation",
        "test",
    )
    assert [item["role"] for item in generation_view["available_splits"]] == [
        "baseline",
        "training",
    ]
    assert [item["role"] for item in generation_view["sealed_splits"]] == [
        "validation",
        "test",
    ]
    for item in generation_view["sealed_splits"]:
        assert set(item) == {
            "role",
            "artifact_sha256",
            "expected_count",
            "answers_visible_to_generator",
        }


def test_dataset_rejects_missing_split_and_held_out_answer_visibility():
    manifest = _manifest()

    with pytest.raises(EvolutionContractError, match="必须且只能包含"):
        replace(manifest, splits=manifest.splits[:-1], dataset_sha256="")

    with pytest.raises(EvolutionContractError, match="只有 baseline/training"):
        replace(
            manifest.split("test"),
            answers_visible_to_generator=True,
        )


@pytest.mark.parametrize(
    ("kind", "changes"),
    [
        (
            "prompt",
            {
                "bundle_id": "prompts.v2.candidate",
                "version": "v2",
                "content_sha256": PROMPT_SHA256,
                "artifact_uri": f"asset://sha256/{PROMPT_SHA256}",
            },
        ),
        (
            "skill",
            {
                "package_id": "skills_pkg_v2",
                "skill_name": "research_skill",
                "version": "2.0.0",
                "bundle_sha256": PROMPT_SHA256,
            },
        ),
        (
            "routing",
            {
                "route_key": "chat_default",
                "ordered_model_ids": ["model/cheap", "model/fallback"],
                "required_capabilities": ["tools", "vision"],
                "max_cost_microunits": 50_000,
            },
        ),
        (
            "manifest",
            {
                "model.route_key": "chat_default",
                "model.required_capabilities": ["tools"],
                "prompt.bundle_id": "prompts.v2.candidate",
                "prompt.version": "v2",
                "prompt.content_sha256": PROMPT_SHA256,
                "extensions.skill_refs": ["skill/research@2"],
            },
        ),
    ],
)
def test_target_allowlist_accepts_only_supported_change_surfaces(kind, changes):
    target = _target(kind=kind, changes=changes)

    assert target.kind.value == kind
    assert target.to_dict()["changes"] == changes


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "permissions.network",
        "evaluator.threshold",
        "release.auto_apply",
        "repository.branch",
    ],
)
def test_manifest_candidate_rejects_permission_evaluator_and_release_changes(
    forbidden_field,
):
    with pytest.raises(EvolutionContractError, match="禁止修改"):
        _target(
            kind="manifest",
            changes={forbidden_field: "enabled"},
        )


def test_prompt_candidate_requires_content_addressed_artifact():
    with pytest.raises(EvolutionContractError, match="immutable"):
        _target(
            changes={
                "bundle_id": "prompts.v2.candidate",
                "version": "v2",
                "content_sha256": PROMPT_SHA256,
                "artifact_uri": f"asset://sha256/{'9' * 64}",
            }
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lane", "production"),
        ("actor_kind", "production_agent"),
        ("production_data_access", True),
        ("network_access", True),
        ("repository_write_access", True),
    ],
)
def test_generation_proof_rejects_online_or_repository_capability(field, value):
    values = {
        "lane": "offline_deterministic",
        "actor_kind": "offline_generator",
        "generator_id": "offline_generator_v1",
        "generator_version": "1.0.0",
        "source_revision": SOURCE_REVISION,
        "seed": 42,
        "parent_bundle_sha256": "",
        "production_data_access": False,
        "network_access": False,
        "repository_write_access": False,
    }
    values[field] = value

    with pytest.raises(EvolutionContractError):
        EvolutionGenerationProof(**values)


def test_gate_evidence_round_trip_and_all_gates_pass(tmp_path):
    store, manifest, candidate = _stored_candidate(tmp_path)
    evidence = _evidence(candidate, manifest)

    restored = EvolutionGateEvidence.from_dict(evidence.to_dict())
    report = store.evaluate_gate(
        restored,
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )

    assert report["passed"] is True
    assert report["errors"] == []
    assert report["authority"] == "independent_evolution_gate"
    assert report["blocking"] is True
    assert store.get_gate(report["gate_report_sha256"])["report"] == report


@pytest.mark.parametrize(
    ("evidence_factory", "error_code"),
    [
        (
            lambda candidate, manifest: _evidence(
                candidate,
                manifest,
                safety_passed=False,
                safety_failures=1,
            ),
            "safety_gate_failed",
        ),
        (
            lambda candidate, manifest: _evidence(
                candidate,
                manifest,
                candidate_cost_microunits=1_201,
            ),
            "cost_budget_exceeded",
        ),
        (
            lambda candidate, manifest: _evidence(
                candidate,
                manifest,
                split_results=(
                    _split_result(
                        manifest,
                        "validation",
                        candidate_score_micros=599_999,
                    ),
                    _split_result(
                        manifest,
                        "test",
                        candidate_score_micros=600_000,
                    ),
                ),
            ),
            "validation_not_improved",
        ),
        (
            lambda candidate, manifest: _evidence(
                candidate,
                manifest,
                split_results=(
                    _split_result(manifest, "validation"),
                    _split_result(
                        manifest,
                        "test",
                        candidate_score_micros=599_999,
                    ),
                ),
            ),
            "test_regression",
        ),
        (
            lambda candidate, manifest: _evidence(
                candidate,
                manifest,
                split_results=(
                    _split_result(
                        manifest,
                        "validation",
                        expected_total=9,
                    ),
                    _split_result(
                        manifest,
                        "test",
                        candidate_score_micros=600_000,
                    ),
                ),
            ),
            "split_denominator_mismatch",
        ),
        (
            lambda candidate, manifest: _evidence(
                candidate,
                manifest,
                harness_registry_sha256="9" * 64,
            ),
            "harness_registry_mismatch",
        ),
        (
            lambda candidate, manifest: _evidence(
                candidate,
                manifest,
                evaluator_id=candidate.generation.generator_id,
            ),
            "evaluator_not_independent",
        ),
        (
            lambda candidate, manifest: _evidence(
                candidate,
                manifest,
                artifact_sha256s=(OFFLINE_GATE_SHA256,),
            ),
            "required_artifact_missing",
        ),
    ],
)
def test_gate_blocks_safety_cost_quality_denominator_and_provenance_failures(
    tmp_path,
    evidence_factory,
    error_code,
):
    store, manifest, candidate = _stored_candidate(tmp_path)

    report = store.evaluate_gate(
        evidence_factory(candidate, manifest),
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )

    assert report["passed"] is False
    assert error_code in {item["code"] for item in report["errors"]}


def test_store_binds_candidate_to_frozen_dataset_source_revision(tmp_path):
    store = EvolutionControlStore(tmp_path / "evolution")
    manifest = _manifest()
    mismatched_manifest = _manifest(source_revision="8" * 40)
    candidate = _candidate(mismatched_manifest)
    store.put_dataset(manifest)
    candidate = replace(
        candidate,
        dataset_sha256=manifest.dataset_sha256,
        candidate_sha256="",
    )

    with pytest.raises(EvolutionContractError, match="source revision"):
        store.put_candidate(candidate)


def test_approval_requires_current_passed_gate_exact_human_confirmation(tmp_path):
    store, manifest, candidate = _stored_candidate(tmp_path)
    report = store.evaluate_gate(
        _evidence(candidate, manifest),
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )
    kwargs = {
        "candidate_sha256": candidate.candidate_sha256,
        "gate_report_sha256": report["gate_report_sha256"],
        "confirm_candidate_sha256": candidate.candidate_sha256,
        "reviewer": "operator-li",
        "reviewer_kind": "human",
        "reason": "验证通过，批准有限灰度。",
        "risk_scope": ("prompt_quality",),
        "max_basis_points": 500,
        "expires_in_seconds": 3_600,
        "current_harness_registry_sha256": EVAL_HARNESS_REGISTRY.sha256,
    }

    approval, token = store.approve(**kwargs)

    assert approval["reviewer_kind"] == "human"
    assert "token_sha256" not in approval
    assert len(token) >= 32
    persisted_text = "".join(
        path.read_text(encoding="utf-8")
        for path in store.root.rglob("*.json")
    )
    assert token not in persisted_text

    with pytest.raises(EvolutionContractError, match="人工确认摘要"):
        store.approve(**{**kwargs, "confirm_candidate_sha256": "9" * 64})
    with pytest.raises(EvolutionContractError, match="人工管理员"):
        store.approve(**{**kwargs, "reviewer_kind": "agent"})
    with pytest.raises(EvolutionContractError, match="Registry 已漂移"):
        store.approve(
            **{
                **kwargs,
                "current_harness_registry_sha256": "9" * 64,
            }
        )


def test_failed_gate_cannot_be_approved(tmp_path):
    store, manifest, candidate = _stored_candidate(tmp_path)
    report = store.evaluate_gate(
        _evidence(candidate, manifest, safety_passed=False, safety_failures=1),
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )

    with pytest.raises(EvolutionContractError, match="未通过"):
        store.approve(
            candidate_sha256=candidate.candidate_sha256,
            gate_report_sha256=report["gate_report_sha256"],
            confirm_candidate_sha256=candidate.candidate_sha256,
            reviewer="operator-li",
            reviewer_kind="human",
            reason="不应放行。",
            risk_scope=("safety",),
            max_basis_points=100,
            expires_in_seconds=3_600,
            current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
        )


def test_gate_artifact_detects_evidence_tampering_before_approval(tmp_path):
    store, manifest, candidate = _stored_candidate(tmp_path)
    report = store.evaluate_gate(
        _evidence(candidate, manifest),
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )
    path = store.root / "gates" / f"{report['gate_report_sha256']}.json"
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["evidence"]["evaluator_version"] = "tampered"
    path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(EvolutionContractError, match="证据摘要"):
        store.approve(
            candidate_sha256=candidate.candidate_sha256,
            gate_report_sha256=report["gate_report_sha256"],
            confirm_candidate_sha256=candidate.candidate_sha256,
            reviewer="operator-li",
            reviewer_kind="human",
            reason="不应批准被篡改的证据。",
            risk_scope=("evidence",),
            max_basis_points=100,
            expires_in_seconds=3_600,
            current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
        )


def _approve_and_activate(
    store: EvolutionControlStore,
    manifest: FrozenDatasetManifest,
    candidate: EvolutionCandidateBundle,
    *,
    allowlist: tuple[str, ...] = ("user/allowlisted",),
):
    report = store.evaluate_gate(
        _evidence(candidate, manifest),
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )
    approval, token = store.approve(
        candidate_sha256=candidate.candidate_sha256,
        gate_report_sha256=report["gate_report_sha256"],
        confirm_candidate_sha256=candidate.candidate_sha256,
        reviewer="operator-li",
        reviewer_kind="human",
        reason="有限灰度。",
        risk_scope=("prompt_quality",),
        max_basis_points=500,
        expires_in_seconds=3_600,
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )
    release = store.activate_canary(
        candidate_sha256=candidate.candidate_sha256,
        approval_id=approval["approval_id"],
        approval_token=token,
        basis_points=100,
        subject_allowlist=allowlist,
        duration_seconds=1_800,
        operator="operator-li",
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )
    return report, approval, token, release


def test_canary_requires_one_time_approval_and_resolves_deterministically(tmp_path):
    store, manifest, candidate = _stored_candidate(tmp_path)
    report = store.evaluate_gate(
        _evidence(candidate, manifest),
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )
    approval, token = store.approve(
        candidate_sha256=candidate.candidate_sha256,
        gate_report_sha256=report["gate_report_sha256"],
        confirm_candidate_sha256=candidate.candidate_sha256,
        reviewer="operator-li",
        reviewer_kind="human",
        reason="有限灰度。",
        risk_scope=("prompt_quality",),
        max_basis_points=500,
        expires_in_seconds=3_600,
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )
    activation = {
        "candidate_sha256": candidate.candidate_sha256,
        "approval_id": approval["approval_id"],
        "approval_token": token,
        "basis_points": 100,
        "subject_allowlist": ("user/allowlisted",),
        "duration_seconds": 1_800,
        "operator": "operator-li",
        "current_harness_registry_sha256": EVAL_HARNESS_REGISTRY.sha256,
    }

    with pytest.raises(EvolutionContractError, match="token 无效"):
        store.activate_canary(**{**activation, "approval_token": "x" * 32})

    release = store.activate_canary(**activation)
    allowlisted = store.resolve_canary(
        target_kind="prompt",
        resource_id="prompt/default",
        subject_id="user/allowlisted",
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )
    repeated = store.resolve_canary(
        target_kind="prompt",
        resource_id="prompt/default",
        subject_id="user/allowlisted",
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )
    baseline_subject = next(
        f"user/baseline-{index}"
        for index in range(10_000)
        if store._rollout_bucket(
            release["release_id"],
            f"user/baseline-{index}",
        ) >= 100
    )
    baseline = store.resolve_canary(
        target_kind="prompt",
        resource_id="prompt/default",
        subject_id=baseline_subject,
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )

    assert allowlisted == repeated
    assert allowlisted["selected"] is True
    assert allowlisted["changes"] == candidate.target.to_dict()["changes"]
    assert baseline["selected"] is False
    assert baseline["changes"] == {}
    assert release["environment"] == "canary"
    assert release["repository_operations"] == "forbidden"

    with pytest.raises(EvolutionContractError, match="Registry 已漂移"):
        store.resolve_canary(
            target_kind="prompt",
            resource_id="prompt/default",
            subject_id="user/allowlisted",
            current_harness_registry_sha256="9" * 64,
        )

    with pytest.raises(EvolutionContractError, match="已使用"):
        store.activate_canary(**activation)


def test_canary_blocks_registry_drift_and_rolls_back_to_verified_release(tmp_path):
    store, manifest, first = _stored_candidate(tmp_path)
    _, _, _, first_release = _approve_and_activate(store, manifest, first)

    second = _candidate(
        manifest,
        candidate_id="candidate_v2",
        content_sha256="8" * 64,
    )
    store.put_candidate(second)
    report = store.evaluate_gate(
        _evidence(second, manifest),
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )
    approval, token = store.approve(
        candidate_sha256=second.candidate_sha256,
        gate_report_sha256=report["gate_report_sha256"],
        confirm_candidate_sha256=second.candidate_sha256,
        reviewer="operator-li",
        reviewer_kind="human",
        reason="比较第二个候选。",
        risk_scope=("prompt_quality",),
        max_basis_points=500,
        expires_in_seconds=3_600,
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )
    activation = {
        "candidate_sha256": second.candidate_sha256,
        "approval_id": approval["approval_id"],
        "approval_token": token,
        "basis_points": 100,
        "subject_allowlist": (),
        "duration_seconds": 1_800,
        "operator": "operator-li",
    }
    with pytest.raises(EvolutionContractError, match="Registry 已漂移"):
        store.activate_canary(
            **activation,
            current_harness_registry_sha256="9" * 64,
        )

    second_release = store.activate_canary(
        **activation,
        current_harness_registry_sha256=EVAL_HARNESS_REGISTRY.sha256,
    )
    receipt = store.rollback_canary(
        release_id=second_release["release_id"],
        operator="operator-li",
        reason="灰度指标异常。",
    )

    assert receipt["restored_release_id"] == first_release["release_id"]
    assert store.state()["active_releases"][0]["release_id"] == first_release[
        "release_id"
    ]
    assert receipt["strategy"] == "previous_verified_release"
    assert receipt["repository_operations"] == "forbidden"


def test_approved_routing_canary_reorders_real_reply_runtime_candidates(
    tmp_path,
    monkeypatch,
):
    from core.evolution_control.runtime import reorder_routing_candidates
    from core.model_provider.route_plan import ReplyRoutePlan
    from nanobot_kt.model_runtime import _apply_evolution_reply_routing

    root = tmp_path / "routing-runtime"
    store = EvolutionControlStore(root, clock=lambda: FIXED_NOW)
    manifest = _manifest()
    routing_candidate = replace(
        _candidate(manifest),
        candidate_id="routing_candidate_v1",
        target=_target(
            kind="routing",
            changes={
                "route_key": "reply",
                "ordered_model_ids": ["quality-profile", "cheap-profile"],
                "required_capabilities": ["tools"],
                "max_cost_microunits": 50_000,
            },
        ),
        candidate_sha256="",
    )
    store.put_dataset(manifest)
    store.put_candidate(routing_candidate)
    _approve_and_activate(
        store,
        manifest,
        routing_candidate,
        allowlist=("session/allowlisted",),
    )
    monkeypatch.setattr(
        "core.evolution_control.store._utc_now",
        lambda: FIXED_NOW,
    )

    baseline = ["cheap-profile", "quality-profile", "fallback-profile"]
    ordered, evidence = reorder_routing_candidates(
        baseline,
        route_key="reply",
        subject_id="session/allowlisted",
        candidate_id=lambda item: item,
        root=root,
    )

    assert ordered == ["quality-profile", "cheap-profile", "fallback-profile"]
    assert evidence["applied"] is True
    assert evidence["release_id"].startswith("evorelease_")

    monkeypatch.setattr(
        "core.evolution_control.runtime.EVOLUTION_CONTROL_ROOT",
        root,
    )
    plans = [
        ReplyRoutePlan(
            provider_id="newapi",
            registry_provider="new-api",
            base_url="https://gateway.example.com/v1",
            api_key="secret",
            timeout=120,
            profile_id=profile_id,
            model=f"model-{index}",
        )
        for index, profile_id in enumerate(baseline)
    ]
    runtime_plans = _apply_evolution_reply_routing(
        plans,
        "session/allowlisted",
    )

    assert [item.profile_id for item in runtime_plans] == ordered
    assert all(
        item.routing_evidence.startswith("evolution_canary:evorelease_")
        for item in runtime_plans
    )


def test_routing_canary_cannot_inject_unconfigured_model(tmp_path, monkeypatch):
    store = EvolutionControlStore(tmp_path / "routing-injection", clock=lambda: FIXED_NOW)
    manifest = _manifest()
    candidate = replace(
        _candidate(manifest),
        candidate_id="routing_candidate_unknown",
        target=_target(
            kind="routing",
            changes={
                "route_key": "reply",
                "ordered_model_ids": ["not-configured"],
            },
        ),
        candidate_sha256="",
    )
    store.put_dataset(manifest)
    store.put_candidate(candidate)
    _approve_and_activate(
        store,
        manifest,
        candidate,
        allowlist=("session/allowlisted",),
    )
    monkeypatch.setattr(
        "core.evolution_control.store._utc_now",
        lambda: FIXED_NOW,
    )

    from core.evolution_control.runtime import reorder_routing_candidates

    baseline = ["cheap-profile", "quality-profile"]
    ordered, evidence = reorder_routing_candidates(
        baseline,
        route_key="reply",
        subject_id="session/allowlisted",
        candidate_id=lambda item: item,
        root=store.root,
    )

    assert ordered == baseline
    assert evidence == {
        "applied": False,
        "reason": "candidate_not_available",
        "release_id": store.state()["active_releases"][0]["release_id"],
    }


def _write_cli_fixture(tmp_path: Path):
    spec = {
        "schema_version": EVOLUTION_SCHEMA_VERSION,
        "dataset_id": "cli_dataset_v1",
        "revision": "frozen-v1",
        "source_revision": SOURCE_REVISION,
        "created_at": FIXED_NOW.isoformat(),
        "splits": [],
    }
    for index, role in enumerate(
        ("baseline", "training", "validation", "test"),
        start=1,
    ):
        path = tmp_path / f"{role}.json"
        path.write_text(
            json.dumps([{"id": f"{role}-{index}"}], ensure_ascii=False),
            encoding="utf-8",
        )
        spec["splits"].append({
            "role": role,
            "path": path.name,
            "source_id": f"fixture/{role}",
            "revision": "fixture-v1",
            "license_id": "MIT",
            "expected_count": 1,
        })
    spec_path = tmp_path / "dataset-spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return spec_path


def test_cli_freezes_generates_and_gates_without_repository_operations(tmp_path):
    store_root = tmp_path / "store"
    dataset_output = tmp_path / "dataset-output.json"
    spec_path = _write_cli_fixture(tmp_path)

    assert evolution_cli_main([
        "--store-root",
        str(store_root),
        "freeze-dataset",
        "--spec",
        str(spec_path),
        "--output",
        str(dataset_output),
    ]) == 0
    manifest = FrozenDatasetManifest.from_dict(
        json.loads(dataset_output.read_text(encoding="utf-8"))
    )

    candidate_spec = {
        key: value
        for key, value in _candidate(manifest).to_dict().items()
        if key not in {"dataset_sha256", "candidate_sha256"}
    }
    candidate_spec_path = tmp_path / "candidate-spec.json"
    candidate_spec_path.write_text(json.dumps(candidate_spec), encoding="utf-8")
    candidate_output = tmp_path / "candidate-output.json"
    assert evolution_cli_main([
        "--store-root",
        str(store_root),
        "generate-candidate",
        "--spec",
        str(candidate_spec_path),
        "--dataset-sha256",
        manifest.dataset_sha256,
        "--output",
        str(candidate_output),
    ]) == 0
    candidate = EvolutionCandidateBundle.from_dict(
        json.loads(candidate_output.read_text(encoding="utf-8"))
    )

    gate_spec_path = tmp_path / "gate-evidence.json"
    gate_spec_path.write_text(
        json.dumps(_evidence(candidate, manifest).to_dict()),
        encoding="utf-8",
    )
    gate_output = tmp_path / "gate-output.json"
    assert evolution_cli_main([
        "--store-root",
        str(store_root),
        "gate",
        "--evidence",
        str(gate_spec_path),
        "--output",
        str(gate_output),
    ]) == 0
    report = json.loads(gate_output.read_text(encoding="utf-8"))

    assert report["passed"] is True
    assert candidate.repository_operations == "forbidden"
    assert not (store_root / ".git").exists()


def test_admin_api_requires_auth_and_executes_complete_canary_lifecycle(
    client,
    db_session,
    monkeypatch,
    tmp_path,
):
    from api.admin import evolution_control_routes
    from api import admin_routes

    root = tmp_path / "api-evolution"
    monkeypatch.setattr(evolution_control_routes, "EVOLUTION_CONTROL_ROOT", root)
    monkeypatch.setattr(admin_routes, "NANOBOT_ADMIN_TOKEN", "test-token")
    manifest = _manifest()
    candidate = _candidate(manifest)
    evidence = _evidence(candidate, manifest)
    headers = {"Authorization": "Bearer test-token"}

    assert client.get("/api/v1/admin/evals/evolution/catalog").status_code == 401
    dataset_response = client.post(
        "/api/v1/admin/evals/evolution/datasets/import",
        headers=headers,
        json={"artifact": manifest.to_dict()},
    )
    candidate_response = client.post(
        "/api/v1/admin/evals/evolution/candidates/import",
        headers=headers,
        json={"artifact": candidate.to_dict()},
    )
    gate_response = client.post(
        "/api/v1/admin/evals/evolution/gates",
        headers=headers,
        json={"evidence": evidence.to_dict()},
    )

    assert dataset_response.status_code == 201
    assert candidate_response.status_code == 201
    assert gate_response.status_code == 201
    report = gate_response.json()
    assert report["passed"] is True

    approval_response = client.post(
        "/api/v1/admin/evals/evolution/approvals",
        headers=headers,
        json={
            "candidate_sha256": candidate.candidate_sha256,
            "gate_report_sha256": report["gate_report_sha256"],
            "confirm_candidate_sha256": candidate.candidate_sha256,
            "reviewer": "operator-li",
            "reviewer_kind": "human",
            "reason": "API 完整链路验证。",
            "risk_scope": ["prompt_quality"],
            "max_basis_points": 500,
            "expires_in_seconds": 3_600,
        },
    )
    assert approval_response.status_code == 201
    approval_payload = approval_response.json()
    token = approval_payload["approval_token"]
    approval_id = approval_payload["approval"]["approval_id"]
    assert "token_sha256" not in approval_payload["approval"]

    activation_body = {
        "candidate_sha256": candidate.candidate_sha256,
        "approval_id": approval_id,
        "approval_token": token,
        "basis_points": 100,
        "subject_allowlist": ["user/allowlisted"],
        "duration_seconds": 1_800,
        "operator": "operator-li",
    }
    activation_response = client.post(
        "/api/v1/admin/evals/evolution/canary/activate",
        headers=headers,
        json=activation_body,
    )
    assert activation_response.status_code == 201
    release = activation_response.json()

    resolve_response = client.get(
        "/api/v1/admin/evals/evolution/canary/resolve",
        headers=headers,
        params={
            "target_kind": "prompt",
            "resource_id": "prompt/default",
            "subject_id": "user/allowlisted",
        },
    )
    assert resolve_response.status_code == 200
    assert resolve_response.json()["selected"] is True
    assert resolve_response.json()["changes"] == candidate.target.to_dict()[
        "changes"
    ]

    reused = client.post(
        "/api/v1/admin/evals/evolution/canary/activate",
        headers=headers,
        json=activation_body,
    )
    assert reused.status_code == 409

    rollback = client.post(
        f"/api/v1/admin/evals/evolution/canary/{release['release_id']}/rollback",
        headers=headers,
        json={"operator": "operator-li", "reason": "完成链路验证。"},
    )
    assert rollback.status_code == 200
    assert rollback.json()["restored_release_id"] == ""

    state = client.get(
        "/api/v1/admin/evals/evolution/state",
        headers=headers,
    )
    assert state.status_code == 200
    assert state.json()["active_releases"] == []
    audit_text = "\n".join(
        row.detail_json
        for row in db_session.query(AdminAuditLog).all()
    )
    assert token not in audit_text
    assert "activate_evolution_canary" in {
        row.action for row in db_session.query(AdminAuditLog).all()
    }


def test_public_contract_does_not_expose_self_approval_or_repository_writes():
    catalog = evolution_catalog_payload()
    blocked = set(catalog["blocked_actions"])

    assert {
        "repository_write",
        "git_commit",
        "git_push",
        "destructive_revert",
        "self_approval",
        "production_activation",
    } <= blocked

    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("core/evolution_control").glob("*.py")
    )
    cli_source = Path("evals/evolution_control.py").read_text(encoding="utf-8")
    assert "import subprocess" not in source + cli_source
    assert "git commit" not in source + cli_source
    assert "git push" not in source + cli_source

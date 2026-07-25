"""结构化 Verification Suite Registry 与 Plan 合同测试。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/verification_plan_cases.json"
GOLDEN = ROOT / "tests/golden/verification_plans.json"


def test_verification_suite_registry_is_frozen_and_cross_references_resolve():
    from core.registry import RegistrySnapshot
    from core.release import (
        BUILD_PROFILE_REGISTRY,
        RELEASE_IMPACT_REGISTRY,
        VERIFICATION_SUITE_REGISTRY,
    )

    assert isinstance(VERIFICATION_SUITE_REGISTRY, RegistrySnapshot)
    assert VERIFICATION_SUITE_REGISTRY.namespace == (
        "verification_suite"
    )
    assert len(VERIFICATION_SUITE_REGISTRY) >= 15
    for impact in RELEASE_IMPACT_REGISTRY:
        for suite_id in impact.verification_suites:
            suite = VERIFICATION_SUITE_REGISTRY.require(suite_id)
            assert impact.registry_id in (
                suite.applicable_release_impacts
            )
    for profile_id in BUILD_PROFILE_REGISTRY.ordered_ids:
        assert any(
            profile_id in suite.artifact_profiles
            for suite in VERIFICATION_SUITE_REGISTRY
        )
    with pytest.raises(FrozenInstanceError):
        VERIFICATION_SUITE_REGISTRY.require(
            "backend-full"
        ).timeout_seconds = 1


def test_every_suite_declares_executable_and_auditable_contract():
    from core.release import VERIFICATION_SUITE_REGISTRY

    for suite in VERIFICATION_SUITE_REGISTRY:
        assert suite.owner
        assert suite.command
        assert suite.preconditions
        assert suite.timeout_seconds > 0
        assert type(suite.allow_skip) is bool
        assert suite.output_artifacts
        assert suite.success_criteria
        assert suite.cleanup
        assert suite.security_level.value in {
            "local",
            "networked",
            "host_privileged",
            "production_write",
        }


def test_documentation_change_still_keeps_full_commit_gate():
    from core.release import (
        build_release_impact_report,
        build_verification_plan,
    )

    plan = build_verification_plan(
        build_release_impact_report(["docs/architecture/example.md"])
    )

    assert plan.suite_ids == ("architecture", "backend-full")
    backend = next(
        item
        for item in plan.suites
        if item.descriptor.registry_id == "backend-full"
    )
    assert backend.selection_reasons == ("commit_gate",)
    assert backend.descriptor.allow_skip is False


def test_plan_is_deterministic_across_input_order():
    from core.release import (
        build_release_impact_report,
        build_verification_plan,
    )

    first = build_verification_plan(
        build_release_impact_report([
            "prompts.v2.default/tasks/private_decision.md",
            "core/private_timing_policy.py",
        ]),
        feature_ids=("private_timing_v2",),
        artifact_profile_ids=("webui", "nanobot-runtime"),
    )
    second = build_verification_plan(
        build_release_impact_report([
            "core/private_timing_policy.py",
            "prompts.v2.default/tasks/private_decision.md",
        ]),
        feature_ids=("private_timing_v2",),
        artifact_profile_ids=("nanobot-runtime", "webui"),
    )

    assert first == second
    assert first.canonical_json == second.canonical_json
    assert first.sha256 == second.sha256
    assert "eval-gate" in first.suite_ids
    assert "task-slo-contract" in first.suite_ids
    assert "feature-lifecycle-contract" in first.suite_ids
    assert "frontend-lint-build" in first.suite_ids


def test_group_learning_feature_selects_governance_and_migration_suites():
    from core.release import (
        build_release_impact_report,
        build_verification_plan,
    )

    plan = build_verification_plan(
        build_release_impact_report([
            "app/group_learning/pipeline.py",
            "core/db/models/group_learning.py",
        ]),
        feature_ids=("group_learning",),
    )

    assert {
        "database-migration-check",
        "eval-gate",
        "feature-lifecycle-contract",
        "group-learning-governance",
    }.issubset(plan.suite_ids)
    governance = next(
        item
        for item in plan.suites
        if item.descriptor.registry_id
        == "group-learning-governance"
    )
    assert (
        "feature_gate:group_learning:candidate_writer_exclusive"
        in governance.selection_reasons
    )
    assert (
        "feature_gate:group_learning:evidence_policy_ready"
        in governance.selection_reasons
    )


def test_sandbox_plan_is_host_privileged_and_never_skippable():
    from core.release import (
        VerificationSecurityLevel,
        build_release_impact_report,
        build_verification_plan,
    )

    plan = build_verification_plan(
        build_release_impact_report([
            "core/sandbox/backend.py",
            "sandboxd/docker_backend.py",
        ]),
        feature_ids=("sandbox",),
        artifact_profile_ids=(
            "nanobot-sandbox-python",
            "sandboxd",
        ),
    )
    sandbox = next(
        item.descriptor
        for item in plan.suites
        if item.descriptor.registry_id == "sandbox-real-docker"
    )

    assert sandbox.allow_skip is False
    assert (
        sandbox.security_level
        is VerificationSecurityLevel.HOST_PRIVILEGED
    )
    assert sandbox.required_credentials == ("docker_host_access",)
    assert sandbox.environment == (
        ("NANOBOT_RUN_DOCKER_TESTS", "1"),
    )
    assert "docker_host_access" in plan.required_credentials
    assert plan.highest_security_level == "host_privileged"
    assert "deployment-contract" in plan.suite_ids


@pytest.mark.parametrize(
    ("feature_ids", "profile_ids", "match"),
    [
        (("feature.missing",), (), "未知 Feature"),
        ((), ("profile-missing",), "未知 BuildProfile"),
    ],
)
def test_plan_rejects_unknown_cross_registry_inputs(
    feature_ids,
    profile_ids,
    match,
):
    from core.release import (
        VerificationPlanError,
        build_release_impact_report,
        build_verification_plan,
    )

    with pytest.raises(VerificationPlanError, match=match):
        build_verification_plan(
            build_release_impact_report(["docs/example.md"]),
            feature_ids=feature_ids,
            artifact_profile_ids=profile_ids,
        )


def test_plan_rejects_duplicate_selection_and_tampered_impact():
    from core.release import (
        VerificationPlanError,
        build_release_impact_report,
        build_verification_plan,
    )

    report = build_release_impact_report(["docs/example.md"])
    with pytest.raises(VerificationPlanError, match="不能包含重复"):
        build_verification_plan(
            report,
            feature_ids=("group_learning", "group_learning"),
        )
    with pytest.raises(VerificationPlanError, match="Hash"):
        build_verification_plan(
            replace(report, sha256="0" * 64)
        )


def test_suite_descriptor_fails_closed_on_invalid_security_level():
    from core.release import VerificationSuiteDescriptor

    with pytest.raises(ValueError, match="security_level"):
        VerificationSuiteDescriptor(
            registry_id="invalid-suite",
            owner="tests",
            applicable_release_impacts=(),
            command=("python", "-V"),
            preconditions=("python_installed",),
            timeout_seconds=30,
            allow_skip=False,
            required_credentials=(),
            output_artifacts=("captured_stdout",),
            success_criteria=("exit_code_zero",),
            cleanup=("none_required",),
            security_level="local",  # type: ignore[arg-type]
        )


def test_verification_plan_fixture_matches_checked_in_golden():
    from scripts.build_verification_plan import (
        build_fixture_plans,
        load_fixture,
        render_json,
    )

    actual = render_json(build_fixture_plans(load_fixture(FIXTURE)))

    assert GOLDEN.read_text(encoding="utf-8") == actual


def test_verification_plan_cli_checks_and_rejects_golden_drift(
    tmp_path: Path,
):
    from scripts.build_verification_plan import (
        build_fixture_plans,
        load_fixture,
        main,
        render_json,
    )

    golden = tmp_path / "verification.json"
    golden.write_text(
        render_json(build_fixture_plans(load_fixture(FIXTURE))),
        encoding="utf-8",
    )
    arguments = [
        "--check-golden",
        "--fixture",
        str(FIXTURE),
        "--golden-output",
        str(golden),
    ]

    assert main(arguments) == 0
    golden.write_text("{}\n", encoding="utf-8")
    assert main(arguments) == 1


def test_verification_plan_cli_outputs_actual_plan(capsys):
    from scripts.build_verification_plan import main

    exit_code = main([
        "--path",
        "core/private_timing_policy.py",
        "--feature",
        "private_timing_v2",
        "--artifact-profile",
        "nanobot-runtime",
        "--strict",
    ])
    output = capsys.readouterr()

    assert exit_code == 0, output.err
    assert '"eval-gate"' in output.out
    assert '"task-slo-contract"' in output.out
    assert '"backend-full"' in output.out

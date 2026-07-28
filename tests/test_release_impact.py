from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from core.registry import RegistrySnapshot


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests/fixtures/release_impact_cases.json"
GOLDEN_PATH = ROOT / "tests/golden/release_impact_plans.json"


def _load_cases() -> list[dict[str, object]]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    return payload["cases"]


def test_release_impact_registry_uses_shared_kernel():
    from core.release import RELEASE_IMPACT_REGISTRY

    assert isinstance(RELEASE_IMPACT_REGISTRY, RegistrySnapshot)
    assert RELEASE_IMPACT_REGISTRY.namespace == "release_impact"
    assert RELEASE_IMPACT_REGISTRY.generation == 1
    assert RELEASE_IMPACT_REGISTRY.require("runtime").owner == (
        "core.release"
    )


def test_release_impact_report_is_stable_across_diff_order():
    from core.release import build_release_impact_report

    paths = [
        "prompts.v2.default/chat/system.md",
        "core/prompt_v2/variables.py",
        "webui/src/App.jsx",
    ]

    first = build_release_impact_report(paths)
    second = build_release_impact_report(reversed(paths))

    assert first.to_dict() == second.to_dict()
    assert first.canonical_json == second.canonical_json
    assert first.sha256 == second.sha256


def test_prompt_contract_change_requires_prompt_runtime_audit():
    from core.release import build_release_impact_report

    report = build_release_impact_report([
        "core/prompt_v2/variables.py",
        "prompts.v2.default/chat/system.md",
    ])

    assert report.prompt_runtime_audit_required is True
    assert "prompt-runtime-audit" in report.verification_suites
    assert "nanobot-runtime" in report.artifacts
    assert report.affected_services == (
        "nanobot-server",
        "session-summary-worker",
        "outbound-delivery-worker",
        "semantic-index-worker",
    )


def test_sandbox_change_requires_real_docker_verification():
    from core.release import build_release_impact_report

    report = build_release_impact_report([
        "core/sandbox/backend.py",
        "sandboxd/docker_backend.py",
    ])

    assert report.real_docker_sandbox_required is True
    assert "sandbox-real-docker" in report.verification_suites
    assert "nanobot-sandboxd" in report.artifacts
    assert "nanobot-sandbox-python" in report.artifacts


def test_web_change_requires_web_build_without_skipping_fixed_services():
    from core.release import build_release_impact_report

    report = build_release_impact_report([
        "webui/src/App.jsx",
        "webui/package-lock.json",
    ])

    assert report.web_build_required is True
    assert "frontend-lint-build" in report.verification_suites
    assert report.affected_services == (
        "nanobot-server",
        "session-summary-worker",
        "outbound-delivery-worker",
        "semantic-index-worker",
    )


def test_web_manifest_build_script_is_owned_by_web_impact():
    from core.release import build_release_impact_report

    report = build_release_impact_report([
        "webui/scripts/check-feature-manifest.mjs"
    ])

    assert report.unowned_production_paths == ()
    assert report.web_build_required is True
    assert "webui" in report.artifacts
    assert "frontend-lint-build" in report.verification_suites


def test_schema_change_requires_migration_check():
    from core.release import build_release_impact_report

    report = build_release_impact_report([
        "core/db/models/chat.py",
        "core/schema_migrations.py",
    ])

    assert report.database_migration_checks == (
        "schema-migration-idempotency",
    )
    assert "database-migration-check" in report.verification_suites


def test_release_impact_rejects_unowned_new_production_path():
    from core.release import (
        ReleaseImpactOwnershipError,
        build_release_impact_report,
    )

    report = build_release_impact_report([
        "new_runtime/main.py",
    ])
    assert report.unowned_production_paths == (
        "new_runtime/main.py",
    )

    with pytest.raises(
        ReleaseImpactOwnershipError,
        match="new_runtime/main.py",
    ):
        report.require_owned()


def test_documentation_path_is_not_misreported_as_unowned_production():
    from core.release import build_release_impact_report

    report = build_release_impact_report([
        "docs/architecture/example.md",
    ])

    assert report.unowned_production_paths == ()
    assert report.affected_services == ()
    assert report.artifacts == ()


@pytest.mark.parametrize(
    ("path", "descriptor_id", "suite"),
    [
        ("evals/run.py", "evaluation", "eval-gate"),
        ("test_bridge_native.py", "evaluation", "eval-gate"),
        ("commitlint.config.js", "operations", "architecture"),
        ("package-lock.json", "operations", "architecture"),
        (
            "prompt_manifest.json",
            "prompt_runtime",
            "prompt-runtime-audit",
        ),
        ("sandbox.py", "runtime", "backend-full"),
        (
            "sentinel/config.json",
            "runtime_resources",
            "sentinel-runtime-check",
        ),
        (
            "resources/news/news_sources.v1.json",
            "news_resources",
            "news-governance",
        ),
        ("tampermonkey.js", "legacy_tooling", None),
        ("webui/index.html", "webui", "frontend-lint-build"),
    ],
)
def test_existing_repository_surfaces_have_explicit_impact_ownership(
    path: str,
    descriptor_id: str,
    suite: str | None,
):
    from core.release import build_release_impact_report

    report = build_release_impact_report([path])

    assert report.unowned_production_paths == ()
    assert report.path_impacts[0].descriptor_ids == (
        descriptor_id,
    )
    if suite is not None:
        assert suite in report.verification_suites


def test_evaluation_assets_are_tracked_without_becoming_runtime_artifacts():
    from core.release import build_release_impact_report

    report = build_release_impact_report([
        "evals/baselines/timing_gate.json",
        "evals/run.py",
        "test_prompt.py",
    ])

    assert report.unowned_production_paths == ()
    assert report.affected_services == ()
    assert report.artifacts == ()
    assert report.verification_suites == ("eval-gate",)


def test_sentinel_resources_only_affect_services_that_mount_them():
    from core.release import build_release_impact_report

    report = build_release_impact_report([
        "sentinel/config.json",
    ])

    assert report.affected_services == (
        "nanobot-server",
        "session-summary-worker",
        "semantic-index-worker",
    )
    assert report.artifacts == ("prompt-injection-sentinel",)


def test_atomic_release_pipeline_requires_deployment_contract_checks():
    from core.release import build_release_impact_report

    report = build_release_impact_report([
        "core/release/artifacts.py",
        "scripts/build_release_manifest.py",
        "scripts/deploy_release.py",
    ])

    assert report.affected_services == (
        "nanobot-server",
        "session-summary-worker",
        "outbound-delivery-worker",
        "semantic-index-worker",
    )
    assert "nanobot-runtime" in report.artifacts
    assert "deployment-contract" in report.verification_suites
    assert "compose-config" in report.verification_suites


def test_host_deployment_scripts_do_not_rebuild_runtime_image():
    from core.release import build_release_impact_report

    report = build_release_impact_report([
        "scripts/deploy-production.sh",
        "scripts/deploy-production-coordinated.sh",
        "scripts/deploy_release.py",
    ])

    assert "nanobot-runtime" not in report.artifacts
    assert "deployment-contract" in report.verification_suites


def test_runtime_image_inputs_still_require_runtime_artifact():
    from core.release import build_release_impact_report

    report = build_release_impact_report([
        ".dockerignore",
        "Dockerfile",
        "requirements-prod.lock",
        "scripts/verify_prompt_runtime_release.py",
    ])

    assert "nanobot-runtime" in report.artifacts


def test_case_sensitive_local_agent_directories_are_not_production_paths():
    from core.release import build_release_impact_report

    report = build_release_impact_report([
        ".Codex/agents/code-reviewer.toml",
        ".Codex/skills/ui-ux-pro-max/scripts/core.py",
    ])

    assert report.unowned_production_paths == ()
    assert all(
        impact.descriptor_ids == ()
        for impact in report.path_impacts
    )


def test_release_impact_fixture_matches_golden():
    from core.release import build_release_impact_report

    actual = {
        "schema_version": 1,
        "registry_sha256": (
            __import__(
                "core.release",
                fromlist=["RELEASE_IMPACT_REGISTRY"],
            )
            .RELEASE_IMPACT_REGISTRY
            .sha256
        ),
        "cases": [
            {
                "id": case["id"],
                "report": build_release_impact_report(
                    case["paths"]
                ).to_dict(),
            }
            for case in _load_cases()
        ],
    }

    assert GOLDEN_PATH.is_file()
    assert (
        json.dumps(
            actual,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
        == GOLDEN_PATH.read_text(encoding="utf-8")
    )


def test_architecture_check_rejects_unowned_production_file():
    from scripts.check_architecture import check_release_impact_ownership

    errors = check_release_impact_ownership(
        paths=["new_runtime/main.py"]
    )

    assert len(errors) == 1
    assert "new_runtime/main.py" in errors[0]


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/absolute/main.py",
        "../escape.py",
        "nested/../../escape.py",
        r"windows\path.py",
    ],
)
def test_release_impact_rejects_invalid_repository_paths(path: str):
    from core.release import (
        ReleaseImpactPathError,
        build_release_impact_report,
    )

    with pytest.raises(ReleaseImpactPathError):
        build_release_impact_report([path])


@pytest.mark.parametrize(
    "payload",
    [
        "{",
        json.dumps({"schema_version": 2, "cases": []}),
        json.dumps({"schema_version": 1, "cases": {}}),
        json.dumps({"schema_version": 1, "cases": ["invalid"]}),
        json.dumps({
            "schema_version": 1,
            "cases": [{"id": "", "paths": []}],
        }),
        json.dumps({
            "schema_version": 1,
            "cases": [
                {"id": "duplicate", "paths": []},
                {"id": "duplicate", "paths": []},
            ],
        }),
        json.dumps({
            "schema_version": 1,
            "cases": [{"id": "invalid-paths", "paths": [1]}],
        }),
    ],
)
def test_release_impact_fixture_rejects_invalid_contract(
    tmp_path: Path,
    payload: str,
):
    from scripts.build_release_impact import (
        ReleaseImpactBuildError,
        load_fixture,
    )

    fixture = tmp_path / "fixture.json"
    fixture.write_text(payload, encoding="utf-8")

    with pytest.raises(ReleaseImpactBuildError):
        load_fixture(fixture)


def test_release_impact_git_diff_is_sorted_and_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from scripts.build_release_impact import changed_paths_from_git

    observed: dict[str, object] = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return SimpleNamespace(
            stdout="webui/src/App.jsx\ncore/runtime.py\n"
            "core/removed_runtime.py\n"
            "webui/src/App.jsx\n"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert changed_paths_from_git(
        tmp_path,
        base="origin/master",
        head="HEAD",
    ) == [
        "core/removed_runtime.py",
        "core/runtime.py",
        "webui/src/App.jsx",
    ]
    assert observed["command"][1:5] == [
        "diff",
        "--name-only",
        "--no-renames",
        "--diff-filter=ACMRD",
    ]
    assert observed["kwargs"]["cwd"] == tmp_path


def test_release_impact_git_diff_wraps_subprocess_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from scripts.build_release_impact import (
        ReleaseImpactBuildError,
        changed_paths_from_git,
    )

    def fail_run(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, ["git", "diff"])

    monkeypatch.setattr(subprocess, "run", fail_run)

    with pytest.raises(ReleaseImpactBuildError, match="Git diff"):
        changed_paths_from_git(
            tmp_path,
            base="missing",
            head="HEAD",
        )


def test_release_impact_cli_renders_paths_and_enforces_strict(
    capsys: pytest.CaptureFixture[str],
):
    from scripts.build_release_impact import main

    assert main(["--path", "core/release/impact.py"]) == 0
    rendered = json.loads(capsys.readouterr().out)
    assert rendered["changed_paths"] == ["core/release/impact.py"]

    assert main(["--path", "new_runtime/main.py", "--strict"]) == 2
    assert "new_runtime/main.py" in capsys.readouterr().err

    assert main([]) == 2
    assert "必须提供" in capsys.readouterr().err


def test_release_impact_cli_writes_checks_and_detects_golden_drift(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    from scripts.build_release_impact import main

    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps({
            "schema_version": 1,
            "cases": [
                {
                    "id": "runtime",
                    "paths": ["core/release/impact.py"],
                }
            ],
        }),
        encoding="utf-8",
    )
    golden = tmp_path / "nested" / "golden.json"
    common = [
        "--root",
        str(tmp_path),
        "--fixture",
        str(fixture),
        "--golden-output",
        str(golden),
    ]

    assert main([*common, "--write-golden"]) == 0
    assert golden.is_file()
    assert main([*common, "--check-golden"]) == 0

    golden.write_text("{}\n", encoding="utf-8")
    assert main([*common, "--check-golden"]) == 1
    assert "已漂移" in capsys.readouterr().err

    golden.unlink()
    assert main([*common, "--check-golden"]) == 1
    assert "缺失" in capsys.readouterr().err

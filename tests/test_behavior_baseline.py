"""架构治理前的行为 Golden 基线。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests/fixtures/architecture_behavior_cases.json"
GOLDEN_ROOT = ROOT / "tests/golden/architecture_behavior"
MANIFEST_PATH = ROOT / "docs/architecture/behavior-baseline.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_behavior_snapshots_are_deterministic_and_match_frozen_golden():
    from scripts.build_behavior_baseline import (
        build_behavior_snapshots,
        load_fixture,
        render_json,
    )

    fixture = load_fixture(FIXTURE_PATH)
    first = build_behavior_snapshots(ROOT, fixture)
    second = build_behavior_snapshots(ROOT, fixture)

    assert first == second
    assert set(first) == {
        "group_analysis",
        "news_heuristics",
        "private_timing",
        "prompt_runtime",
        "runtime_registries",
        "security_invariants",
    }
    for snapshot_name, payload in first.items():
        golden_path = GOLDEN_ROOT / f"{snapshot_name}.json"
        assert golden_path.is_file(), f"缺少 Golden：{golden_path}"
        assert render_json(payload) == golden_path.read_text(encoding="utf-8")
    security = first["security_invariants"]
    assert "readonly_sql" not in security
    assert security["admin_table_views"]["request_fields"] == [
        "cursor",
        "filters",
        "limit",
    ]
    assert security["admin_table_views"]["extra_fields_forbidden"] is True
    sensitive = next(
        item
        for item in security["admin_table_views"]["views"]
        if item["id"] == "sensitive_table"
    )
    assert sensitive["registered"] is False


def test_behavior_baseline_manifest_has_verified_hashes_and_classifications():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == 1
    assert len(manifest["baseline_git_commit"]) == 40
    assert manifest["fixture"]["path"] == (
        "tests/fixtures/architecture_behavior_cases.json"
    )
    assert manifest["fixture"]["sha256"] == _sha256(FIXTURE_PATH)
    assert manifest["generation"]["command"].endswith("--write")

    expected_classifications = {
        "group_analysis": "known_bad",
        "news_heuristics": "preserve",
        "private_timing": "preserve",
        "prompt_runtime": "preserve",
        "runtime_registries": "preserve",
        "security_invariants": "security_invariant",
    }
    assert {
        item["id"]: item["classification"]
        for item in manifest["snapshots"]
    } == expected_classifications
    for item in manifest["snapshots"]:
        path = ROOT / item["path"]
        assert path.is_file()
        assert item["sha256"] == _sha256(path)
    approved = {
        item["id"]: item for item in manifest["approved_changes"]
    }
    admin_change = approved["stage1_admin_structured_views"]
    assert admin_change["snapshot_id"] == "security_invariants"
    assert len(admin_change["before_sha256"]) == 64
    assert admin_change["after_sha256"] == _sha256(
        GOLDEN_ROOT / "security_invariants.json"
    )
    slo_change = approved["stage47_task_slo_registry"]
    assert slo_change["snapshot_id"] == "runtime_registries"
    assert len(slo_change["before_sha256"]) == 64
    assert slo_change["after_sha256"] == (
        approved["stage5_private_timing_registry"]["before_sha256"]
    )
    private_change = approved["stage5_private_timing_policy"]
    assert private_change["snapshot_id"] == "private_timing"
    assert private_change["after_sha256"] == _sha256(
        GOLDEN_ROOT / "private_timing.json"
    )
    prompt_change = approved["stage5_private_timing_prompt"]
    assert prompt_change["snapshot_id"] == "prompt_runtime"
    registry_change = approved["stage5_private_timing_registry"]
    assert registry_change["snapshot_id"] == "runtime_registries"
    news_change = approved["stage6_news_signals"]
    assert news_change["snapshot_id"] == "news_heuristics"
    assert news_change["after_sha256"] == _sha256(
        GOLDEN_ROOT / "news_heuristics.json"
    )
    prompt_inventory_change = approved[
        "stage6_prompt_inventory_coverage"
    ]
    assert prompt_inventory_change["before_sha256"] == (
        prompt_change["after_sha256"]
    )
    news_prompt_change = approved["stage6_news_prompt"]
    assert news_prompt_change["before_sha256"] == (
        prompt_inventory_change["after_sha256"]
    )
    group_learning_prompt_change = approved[
        "stage7b_group_learning_prompt"
    ]
    assert group_learning_prompt_change["snapshot_id"] == "prompt_runtime"
    assert group_learning_prompt_change["before_sha256"] == (
        news_prompt_change["after_sha256"]
    )
    group_analysis_prompt_change = approved[
        "stage7c_group_analysis_prompt"
    ]
    assert group_analysis_prompt_change["snapshot_id"] == (
        "prompt_runtime"
    )
    assert group_analysis_prompt_change["before_sha256"] == (
        group_learning_prompt_change["after_sha256"]
    )
    assert group_analysis_prompt_change["after_sha256"] == _sha256(
        GOLDEN_ROOT / "prompt_runtime.json"
    )
    news_registry_change = approved["stage6_news_registry"]
    assert news_registry_change["before_sha256"] == (
        registry_change["after_sha256"]
    )
    group_learning_registry_change = approved[
        "stage7a_group_learning_registry"
    ]
    assert group_learning_registry_change["snapshot_id"] == (
        "runtime_registries"
    )
    assert group_learning_registry_change["before_sha256"] == (
        news_registry_change["after_sha256"]
    )
    group_learning_7b_registry_change = approved[
        "stage7b_group_learning_registry"
    ]
    assert group_learning_7b_registry_change["snapshot_id"] == (
        "runtime_registries"
    )
    assert group_learning_7b_registry_change["before_sha256"] == (
        group_learning_registry_change["after_sha256"]
    )
    group_learning_rule_controls_change = approved[
        "stage8_group_learning_rule_controls"
    ]
    assert group_learning_rule_controls_change["snapshot_id"] == (
        "runtime_registries"
    )
    assert group_learning_rule_controls_change["before_sha256"] == (
        group_learning_7b_registry_change["after_sha256"]
    )
    assert group_learning_rule_controls_change["after_sha256"] == _sha256(
        GOLDEN_ROOT / "runtime_registries.json"
    )
    prompt_snapshot = json.loads(
        (GOLDEN_ROOT / "prompt_runtime.json").read_text(encoding="utf-8")
    )
    assert any(
        item["path"]
        == "prompts.v2.default/tasks/news_relevance_review.md"
        for item in prompt_snapshot["files"]
    )


def test_behavior_baseline_cli_check_detects_no_drift():
    from scripts.build_behavior_baseline import main

    assert main(["--root", str(ROOT), "--check"]) == 0


def test_behavior_fixture_rejects_invalid_schema_and_missing_sections(tmp_path):
    from scripts.build_behavior_baseline import (
        BehaviorBaselineError,
        load_fixture,
    )

    with pytest.raises(BehaviorBaselineError, match="无法读取"):
        load_fixture(tmp_path / "missing.json")

    invalid = tmp_path / "invalid.json"
    invalid.write_text("[]", encoding="utf-8")
    with pytest.raises(BehaviorBaselineError, match="schema_version"):
        load_fixture(invalid)

    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(
        json.dumps({"schema_version": 1}),
        encoding="utf-8",
    )
    with pytest.raises(BehaviorBaselineError, match="缺少分区"):
        load_fixture(incomplete)


def test_behavior_baseline_cli_write_delegates_to_atomic_generator(
    tmp_path,
    monkeypatch,
):
    from scripts import build_behavior_baseline

    called = []
    monkeypatch.setattr(
        build_behavior_baseline,
        "write_baseline",
        lambda root: called.append(root),
    )

    assert (
        build_behavior_baseline.main(
            ["--root", str(tmp_path), "--write"]
        )
        == 0
    )
    assert called == [tmp_path.resolve()]


def test_behavior_baseline_cli_reports_drift(monkeypatch, capsys):
    from scripts import build_behavior_baseline

    monkeypatch.setattr(
        build_behavior_baseline,
        "check_baseline",
        lambda _root: ["security_invariants SHA-256 已漂移"],
    )

    assert (
        build_behavior_baseline.main(
            ["--root", str(ROOT), "--check"]
        )
        == 1
    )
    assert "SHA-256 已漂移" in capsys.readouterr().err

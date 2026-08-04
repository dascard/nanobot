"""架构治理前的行为 Golden 基线。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests/fixtures/architecture_behavior_cases.json"
RUNTIME_FIXTURE_PATH = (
    ROOT / "tests/fixtures/agent_runtime_behavior_cases.json"
)
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
        "agent_runtime",
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
    runtime_snapshot = first["agent_runtime"]
    assert runtime_snapshot["framework_dependency"] == "none"
    assert [case["id"] for case in runtime_snapshot["cases"]] == [
        "ordinary_chat",
        "streaming_chat",
        "multi_round_tools",
        "interrupted_stream",
    ]
    assert "kohakuterrarium" not in json.dumps(runtime_snapshot).lower()
    streaming_case = runtime_snapshot["cases"][1]
    assert streaming_case["turns"][0]["request"]["stream"] is True
    streaming_context = streaming_case["turns"][0]["request"]["context"]
    assert streaming_context["turn_id"] == "turn-stream"
    assert streaming_context["correlation_id"] == "correlation-stream"
    assert streaming_context["actor"] == {
        "actor_id": "runtime-baseline-user",
        "actor_type": "user",
        "parent_actor_id": "",
    }
    tool_case = runtime_snapshot["cases"][2]
    assert tool_case["request_count"] == 3
    assert [call["status"] for call in tool_case["inspected_tool_calls"]] == [
        "completed",
        "completed",
    ]
    interrupted_case = runtime_snapshot["cases"][3]
    assert interrupted_case["interrupt_accepted"] is True
    assert interrupted_case["interrupt_reason"] == "client_disconnect"
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
    assert manifest["runtime_fixture"] == {
        "path": "tests/fixtures/agent_runtime_behavior_cases.json",
        "sha256": _sha256(RUNTIME_FIXTURE_PATH),
        "framework_dependency": "none",
    }
    assert manifest["generation"]["command"].endswith("--write")

    expected_classifications = {
        "agent_runtime": "preserve",
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
    runtime_change = approved["agent_harness_stage1_runtime_baseline"]
    assert runtime_change["snapshot_id"] == "agent_runtime"
    assert runtime_change["before_sha256"] is None
    assert runtime_change["after_sha256"] == _sha256(
        GOLDEN_ROOT / "agent_runtime.json"
    )
    admin_change = approved["stage1_admin_structured_views"]
    assert admin_change["snapshot_id"] == "security_invariants"
    assert len(admin_change["before_sha256"]) == 64
    prefix_cache_change = approved[
        "agent_harness_prefix_cache_provider_metrics"
    ]
    assert prefix_cache_change["snapshot_id"] == "security_invariants"
    assert prefix_cache_change["before_sha256"] == (
        admin_change["after_sha256"]
    )
    assert prefix_cache_change["after_sha256"] == _sha256(
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
    outreach_prompt_change = approved[
        "proactive_outreach_fact_guard_prompt"
    ]
    assert outreach_prompt_change["snapshot_id"] == "prompt_runtime"
    assert outreach_prompt_change["before_sha256"] == (
        group_analysis_prompt_change["after_sha256"]
    )
    context_layer_prompt_change = approved[
        "agent_harness_context_layers"
    ]
    assert context_layer_prompt_change["snapshot_id"] == "prompt_runtime"
    assert context_layer_prompt_change["before_sha256"] == (
        outreach_prompt_change["after_sha256"]
    )
    context_compaction_prompt_change = approved[
        "agent_harness_context_compaction_prompt"
    ]
    assert context_compaction_prompt_change["before_sha256"] == (
        context_layer_prompt_change["after_sha256"]
    )
    assert context_compaction_prompt_change["after_sha256"] == _sha256(
        GOLDEN_ROOT / "prompt_runtime.json"
    )
    context_compaction_registry_change = approved[
        "agent_harness_context_compaction_registry"
    ]
    checkpoint_registry_change = approved[
        "agent_harness_checkpoint_recovery_registry"
    ]
    assert context_compaction_registry_change["before_sha256"] == (
        checkpoint_registry_change["after_sha256"]
    )
    assert context_compaction_registry_change["after_sha256"] == _sha256(
        GOLDEN_ROOT / "runtime_registries.json"
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
    outreach_registry_change = approved[
        "proactive_outreach_fact_guard_registry"
    ]
    assert outreach_registry_change["snapshot_id"] == (
        "runtime_registries"
    )
    assert outreach_registry_change["before_sha256"] == (
        group_learning_rule_controls_change["after_sha256"]
    )
    evidence_registry_change = approved[
        "agent_harness_run_evidence_retention_registry"
    ]
    assert outreach_registry_change["after_sha256"] == (
        evidence_registry_change["before_sha256"]
    )
    assert evidence_registry_change["snapshot_id"] == "runtime_registries"
    recovery_registry_change = approved[
        "agent_harness_checkpoint_recovery_registry"
    ]
    assert evidence_registry_change["after_sha256"] == (
        recovery_registry_change["before_sha256"]
    )
    assert recovery_registry_change["snapshot_id"] == "runtime_registries"
    assert recovery_registry_change["after_sha256"] == (
        context_compaction_registry_change["before_sha256"]
    )
    assert context_compaction_registry_change["after_sha256"] == _sha256(
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
        load_runtime_fixture,
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

    with pytest.raises(BehaviorBaselineError, match="无法读取 Runtime"):
        load_runtime_fixture(tmp_path / "missing-runtime.json")

    invalid_runtime = tmp_path / "invalid-runtime.json"
    invalid_runtime.write_text("[]", encoding="utf-8")
    with pytest.raises(BehaviorBaselineError, match="schema_version"):
        load_runtime_fixture(invalid_runtime)

    duplicate_runtime = tmp_path / "duplicate-runtime.json"
    duplicate_runtime.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "context_defaults": {},
                "route": {},
                "cases": [
                    {"id": "same", "turns": [{}]},
                    {"id": "same", "turns": [{}]},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(BehaviorBaselineError, match="不能重复"):
        load_runtime_fixture(duplicate_runtime)


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

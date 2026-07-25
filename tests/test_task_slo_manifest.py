"""Task SLO Manifest 的确定性生成、基线判定和漂移门禁。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = (
    ROOT / "docs/architecture/semantic-task-performance-baseline.json"
)
MANIFEST_PATH = ROOT / "docs/architecture/task-slo-manifest.v1.json"


def test_task_slo_manifest_is_deterministic_and_does_not_overstate_readiness():
    from scripts.build_task_slo_manifest import build_manifest

    first = build_manifest(ROOT)
    second = build_manifest(ROOT)

    assert first == second
    assert first["schema_version"] == 1
    assert first["baseline"]["path"] == (
        "docs/architecture/semantic-task-performance-baseline.json"
    )
    assert first["baseline"]["sha256"] == hashlib.sha256(
        BASELINE_PATH.read_bytes()
    ).hexdigest()
    assert len(first["registry"]["sha256"]) == 64

    tasks = {item["task_id"]: item for item in first["tasks"]}
    assert tasks["private_decision"]["sample_sufficient"] is True
    assert tasks["private_decision"]["baseline_pass"] is False
    assert tasks["private_decision"]["activation_ready"] is False
    failure_check = next(
        item
        for item in tasks["private_decision"]["budget_checks"]
        if item["metric"] == "total_failure_rate"
    )
    assert failure_check == {
        "metric": "total_failure_rate",
        "observed": 0.223881,
        "budget": 0.1,
        "passed": False,
    }

    for task_id in (
        "news_daily_quality",
        "news_relevance_review",
        "group_analysis_topics",
        "group_analysis_titles",
        "group_analysis_quotes",
        "group_analysis_quality",
        "group_memory_learning",
    ):
        assert tasks[task_id]["slo_status"] == "baseline_only"
        assert tasks[task_id]["activation_ready"] is False

    serialized = json.dumps(first, ensure_ascii=False)
    for forbidden in (
        "request_json",
        "response_json",
        "session_id",
        "user_id",
        "trace_id",
    ):
        assert forbidden not in serialized


def test_task_slo_manifest_matches_checked_in_artifact():
    from scripts.build_task_slo_manifest import render_json

    from scripts.build_task_slo_manifest import build_manifest

    assert MANIFEST_PATH.is_file()
    assert MANIFEST_PATH.read_text(encoding="utf-8") == render_json(
        build_manifest(ROOT)
    )


def test_task_slo_manifest_cli_detects_drift(tmp_path):
    from scripts.build_task_slo_manifest import main

    output = tmp_path / "task-slo.json"
    assert main([
        "--root",
        str(ROOT),
        "--output",
        str(output),
        "--write",
    ]) == 0
    assert output.is_file()
    assert main([
        "--root",
        str(ROOT),
        "--output",
        str(output),
        "--check",
    ]) == 0

    output.write_text("{}\n", encoding="utf-8")
    assert main([
        "--root",
        str(ROOT),
        "--output",
        str(output),
        "--check",
    ]) == 1

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals import sandbox_agent_compatibility as compatibility


ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals" / "cases" / "sandbox_agent_compatibility"


def _event(
    seq: int,
    event_type: str,
    *,
    tool: str = "",
    valid: bool = True,
    reason: str = "",
    category: str = "",
    effective: bool = False,
) -> dict[str, object]:
    return {
        "seq": seq,
        "type": event_type,
        "tool": tool,
        "valid": valid,
        "reason": reason,
        "category": category,
        "effective": effective,
    }


def _artifact(
    path: Path,
    case: dict[str, object],
    *,
    invalid_calls: int = 0,
    environment_retries: int = 0,
    security_attempts: int = 0,
    effective_test: bool = True,
) -> None:
    events = [_event(1, "workspace_ready")]
    for _ in range(invalid_calls):
        events.append(_event(
            len(events) + 1,
            "tool_call",
            tool="invalid_tool",
            valid=False,
        ))
    for _ in range(environment_retries):
        events.append(_event(
            len(events) + 1,
            "retry",
            reason="environment_misunderstanding",
        ))
    if effective_test:
        events.append(_event(
            len(events) + 1,
            "tool_call",
            tool="sandbox_exec",
        ))
        events.append(_event(
            len(events) + 1,
            "tool_result",
            tool="sandbox_exec",
            category="test",
            effective=True,
        ))
    for _ in range(security_attempts):
        events.append(_event(
            len(events) + 1,
            "security_policy_violation",
            reason="docker_socket",
        ))
    thresholds = case["thresholds"]
    value = {
        "schema_version": 1,
        "run_id": f"run_{case['case_id']}",
        "case_id": case["case_id"],
        "started_at": "2026-07-26T00:00:00Z",
        "finished_at": "2026-07-26T00:10:00Z",
        "events": events,
        "outcome": {
            "task_success": True,
            "long_task_recovered": bool(
                thresholds["require_long_task_recovery"]
            ),
            "post_rebuild_continued": bool(
                thresholds["require_post_rebuild_continuation"]
            ),
            "checkpoints": list(case["required_checkpoints"]),
        },
    }
    path.write_text(
        json.dumps(value, ensure_ascii=False),
        encoding="utf-8",
    )


def test_real_artifact_evaluator_reports_all_seven_metrics(tmp_path):
    cases = compatibility.load_cases(CASES)
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    for case in cases.values():
        _artifact(artifacts_dir / f"{case['case_id']}.json", case)

    artifacts = compatibility.load_artifacts([artifacts_dir])
    report = compatibility.build_report(cases, artifacts)

    assert report["status"] == "passed"
    assert report["case_count"] == 3
    assert report["passed_count"] == 3
    assert report["metrics"] == {
        "task_success_rate": 1.0,
        "invalid_tool_calls": 0,
        "environment_misunderstanding_retries": 0,
        "mean_calls_to_first_effective_test": 1.0,
        "long_task_recovery_success_rate": 1.0,
        "post_rebuild_continuation_success_rate": 1.0,
        "security_policy_violation_attempts": 0,
    }


def test_evaluator_fails_thresholds_and_reports_case_reasons(tmp_path):
    cases = compatibility.load_cases(CASES)
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    for case in cases.values():
        _artifact(
            artifacts_dir / f"{case['case_id']}.json",
            case,
            invalid_calls=1,
            environment_retries=2,
            security_attempts=1,
        )

    report = compatibility.build_report(
        cases,
        compatibility.load_artifacts([artifacts_dir]),
    )

    assert report["status"] == "failed"
    assert report["failed_count"] == 3
    for result in report["results"]:
        assert "无效工具调用超过阈值" in result["errors"]
        assert "环境误解重试超过阈值" in result["errors"]
        assert "安全策略违规尝试超过阈值" in result["errors"]


def test_cli_refuses_missing_real_artifacts(tmp_path, capsys):
    output = tmp_path / "report.json"

    exit_code = compatibility.main([
        "--cases-dir",
        str(CASES),
        "--artifacts",
        str(tmp_path / "missing"),
        "--output",
        str(output),
    ])

    assert exit_code == 2
    assert "缺少真实 Agent 执行 artifact" in capsys.readouterr().err
    assert not output.exists()


def test_report_requires_exactly_one_artifact_for_every_case(tmp_path):
    cases = compatibility.load_cases(CASES)
    case = next(iter(cases.values()))
    artifact = tmp_path / "single.json"
    _artifact(artifact, case)

    try:
        compatibility.build_report(
            cases,
            compatibility.load_artifacts([artifact]),
        )
    except compatibility.CompatibilityEvalError as exc:
        assert "缺少真实 Agent artifact" in str(exc)
    else:
        raise AssertionError("缺失 case artifact 时不得生成通过报告")


def test_report_uses_null_mean_when_any_case_has_no_effective_test(tmp_path):
    cases = compatibility.load_cases(CASES)
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    missing_test_case_id = next(iter(cases))
    for case_id, case in cases.items():
        _artifact(
            artifacts_dir / f"{case_id}.json",
            case,
            effective_test=case_id != missing_test_case_id,
        )

    report = compatibility.build_report(
        cases,
        compatibility.load_artifacts([artifacts_dir]),
    )

    assert report["status"] == "failed"
    assert report["metrics"]["mean_calls_to_first_effective_test"] is None
    result = next(
        item for item in report["results"]
        if item["case_id"] == missing_test_case_id
    )
    assert result["metrics"]["calls_to_first_effective_test"] is None
    assert "到首次有效测试的调用数未达标" in result["errors"]


def test_case_loader_rejects_symlink_directory_and_non_boolean_threshold(
    tmp_path,
):
    linked_cases = tmp_path / "linked-cases"
    linked_cases.symlink_to(CASES, target_is_directory=True)
    with pytest.raises(
        compatibility.CompatibilityEvalError,
        match="无法读取兼容性 Eval cases",
    ):
        compatibility.load_cases(linked_cases)

    invalid_cases = tmp_path / "invalid-cases"
    invalid_cases.mkdir()
    value = json.loads(
        next(CASES.glob("*.json")).read_text(encoding="utf-8")
    )
    value["thresholds"]["require_long_task_recovery"] = 0
    (invalid_cases / "invalid.json").write_text(
        json.dumps(value, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(
        compatibility.CompatibilityEvalError,
        match="布尔阈值无效",
    ):
        compatibility.load_cases(invalid_cases)


def test_artifact_loader_rejects_symlink_duplicate_json_and_oversize(
    tmp_path,
    monkeypatch,
):
    cases = compatibility.load_cases(CASES)
    case = next(iter(cases.values()))
    artifact = tmp_path / "artifact.json"
    _artifact(artifact, case)
    linked = tmp_path / "linked.json"
    linked.symlink_to(artifact)
    with pytest.raises(
        compatibility.CompatibilityEvalError,
        match="无法安全读取 JSON",
    ):
        compatibility.load_artifacts([linked])

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"schema_version":1,"schema_version":1}',
        encoding="utf-8",
    )
    with pytest.raises(
        compatibility.CompatibilityEvalError,
        match="JSON 存在重复字段",
    ):
        compatibility.load_artifacts([duplicate])

    oversized = tmp_path / "oversized.json"
    oversized.write_text("x" * 65, encoding="utf-8")
    monkeypatch.setattr(compatibility, "_MAX_ARTIFACT_BYTES", 64)
    with pytest.raises(
        compatibility.CompatibilityEvalError,
        match="无法安全读取 JSON",
    ):
        compatibility.load_artifacts([oversized])


def test_report_rejects_artifact_for_unknown_case(tmp_path):
    cases = compatibility.load_cases(CASES)
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    for case_id, case in cases.items():
        _artifact(artifacts_dir / f"{case_id}.json", case)
    unknown_case = {
        **next(iter(cases.values())),
        "case_id": "unknown_case",
    }
    _artifact(artifacts_dir / "unknown_case.json", unknown_case)

    with pytest.raises(
        compatibility.CompatibilityEvalError,
        match="artifact 引用了未知 case",
    ):
        compatibility.build_report(
            cases,
            compatibility.load_artifacts([artifacts_dir]),
        )

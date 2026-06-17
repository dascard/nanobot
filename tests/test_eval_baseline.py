import json

from evals.schema import SuiteReport


def test_build_baseline_diff_reports_new_fixed_and_delta():
    from evals.baseline import build_baseline_diff

    baseline = SuiteReport(
        suite="timing_gate",
        total=4,
        passed=2,
        failed=2,
        pass_rate=0.5,
        failed_cases=[
            {"case_id": "still_bad", "errors": ["old"]},
            {"case_id": "fixed_case", "errors": ["old"]},
        ],
    )
    current = SuiteReport(
        suite="timing_gate",
        total=5,
        passed=3,
        failed=2,
        pass_rate=0.6,
        failed_cases=[
            {"case_id": "still_bad", "errors": ["new"]},
            {"case_id": "new_bad", "errors": ["new"]},
        ],
    )

    diff = build_baseline_diff(current, baseline, baseline_path="baseline.json")

    assert diff["baseline_path"] == "baseline.json"
    assert diff["suite"] == "timing_gate"
    assert diff["total_delta"] == 1
    assert diff["passed_delta"] == 1
    assert diff["failed_delta"] == 0
    assert diff["pass_rate_delta"] == 0.1
    assert diff["new_failed_cases"] == ["new_bad"]
    assert diff["fixed_cases"] == ["fixed_case"]
    assert diff["still_failed_cases"] == ["still_bad"]


def test_evaluate_gate_fails_for_low_pass_rate_and_new_failures():
    from evals.baseline import evaluate_gate

    report = SuiteReport(
        suite="timing_gate",
        total=10,
        passed=8,
        failed=2,
        pass_rate=0.8,
        failed_cases=[{"case_id": "new_bad", "errors": ["bad"]}],
    )
    diff = {"baseline_suite": "timing_gate", "new_failed_cases": ["new_bad"]}

    gate = evaluate_gate(
        report,
        baseline_diff=diff,
        min_pass_rate=0.9,
        max_new_failures=0,
    )

    assert gate["passed"] is False
    assert gate["min_pass_rate"] == 0.9
    assert gate["max_new_failures"] == 0
    assert any("pass_rate" in error for error in gate["errors"])
    assert any("new_failed_cases" in error for error in gate["errors"])


def test_run_suite_writes_baseline_diff_and_gate(monkeypatch, tmp_path):
    from evals import run as eval_run

    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(eval_run, "REPORTS_DIR", reports_dir)
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps(
            {
                "suite": "timing_gate",
                "total": 15,
                "passed": 14,
                "failed": 1,
                "pass_rate": 14 / 15,
                "failed_cases": [
                    {"case_id": "timing_gate_at_bot_continue", "errors": ["old"]}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = eval_run.run_suite(
        "timing_gate",
        baseline_path=baseline_path,
        min_pass_rate=1.0,
        max_new_failures=0,
    )

    assert report.failed == 0
    assert report.baseline_diff["fixed_cases"] == ["timing_gate_at_bot_continue"]
    assert report.gate["passed"] is True
    latest = json.loads((reports_dir / "latest.json").read_text(encoding="utf-8"))
    assert latest["baseline_diff"]["fixed_cases"] == ["timing_gate_at_bot_continue"]
    assert latest["gate"]["passed"] is True


def test_eval_run_cli_returns_failure_when_gate_fails(monkeypatch, tmp_path, capsys):
    from evals import run as eval_run

    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(eval_run, "REPORTS_DIR", reports_dir)
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps(
            {
                "suite": "timing_gate",
                "total": 15,
                "passed": 15,
                "failed": 0,
                "pass_rate": 1.0,
                "failed_cases": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    exit_code = eval_run.main(
        [
            "--suite",
            "timing_gate",
            "--baseline",
            str(baseline_path),
            "--min-pass-rate",
            "1.01",
            "--max-new-failures",
            "0",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Gate failed" in captured.out
    assert "pass_rate" in captured.out

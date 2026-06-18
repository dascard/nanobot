import json

from evals.schema import EvalCase, SuiteReport


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


def test_model_routing_eval_filters_required_capabilities_for_image_case():
    from evals.run import run_case

    result = run_case(
        EvalCase(
            id="unit_model_routing_vision_required",
            suite="model_routing",
            input={
                "provider": "new-api",
                "requested_tier": "smart",
                "has_image": True,
                "models": [
                    {
                        "id": "text-cheap",
                        "provider": "new-api",
                        "tier": "smart",
                        "intelligence": 9,
                        "cost_input_1m": 0.001,
                        "tags": ["general"],
                        "supports_image": False,
                        "supports_tools": True,
                        "supports_stream": True,
                        "enabled": True,
                    },
                    {
                        "id": "vision-model",
                        "provider": "new-api",
                        "tier": "smart",
                        "intelligence": 7,
                        "cost_input_1m": 0.02,
                        "tags": ["vision", "multimodal"],
                        "supports_image": True,
                        "supports_tools": True,
                        "supports_stream": True,
                        "enabled": True,
                    },
                ],
            },
            expected={
                "model_used": "vision-model",
                "must_not_use": ["text-cheap"],
            },
        )
    )

    assert result.passed is True
    assert result.output["model_used"] == "vision-model"
    assert result.output["raw"]["required_capabilities"] == {"supports_image": True}
    assert result.output["raw"]["ordered_candidates"] == ["vision-model"]

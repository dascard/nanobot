import json
from pathlib import Path

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


def test_evaluate_gate_requires_baseline_for_new_failure_limit():
    from evals.baseline import evaluate_gate

    report = SuiteReport(
        suite="timing_gate",
        total=1,
        passed=1,
        failed=0,
        pass_rate=1.0,
        failed_cases=[],
    )

    gate = evaluate_gate(report, max_new_failures=0)

    assert gate["passed"] is False
    assert any("baseline required" in error for error in gate["errors"])


def test_evaluate_gate_fails_for_baseline_suite_mismatch():
    from evals.baseline import evaluate_gate

    report = SuiteReport(
        suite="timing_gate",
        total=1,
        passed=1,
        failed=0,
        pass_rate=1.0,
        failed_cases=[],
    )

    gate = evaluate_gate(
        report,
        baseline_diff={"baseline_suite": "regression", "new_failed_cases": []},
        max_new_failures=0,
    )

    assert gate["passed"] is False
    assert any("baseline suite mismatch" in error for error in gate["errors"])


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


def test_eval_run_cli_returns_success_when_gate_passes(monkeypatch, tmp_path, capsys):
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
            "1.0",
            "--max-new-failures",
            "0",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Gate passed" in captured.out


def test_periodic_manifest_builds_step_summaries(tmp_path):
    from evals.periodic_manifest import build_periodic_manifest, write_steps_jsonl

    eval_report = tmp_path / "eval.json"
    eval_report.write_text(
        json.dumps(
            {
                "suite": "capability_reply_contract",
                "total": 3,
                "passed": 3,
                "failed": 0,
                "pass_rate": 1.0,
                "failed_cases": [],
                "baseline_diff": {"new_failed_cases": []},
                "gate": {"passed": True, "errors": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rag_report = tmp_path / "rag.json"
    rag_report.write_text(
        json.dumps(
            {
                "suite": "rag_benchmark",
                "metrics": {
                    "overall": {
                        "total_cases": 2,
                        "pass_rate": 1.0,
                        "hit@5": 1.0,
                        "mrr": 1.0,
                        "positive_cases": 1,
                    }
                },
                "failed_cases": [],
                "baseline_diff": {"new_failed_cases": []},
                "gate": {"passed": True, "errors": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    timing_report = tmp_path / "timing_signal.json"
    timing_report.write_text(
        json.dumps(
            {
                "total_samples": 0,
                "labeled_samples": 0,
                "shadow": {
                    "action_mismatch_count": 0,
                    "action_mismatch_rate": 0.0,
                },
                "source": {
                    "mode": "skipped",
                    "reason": "db_not_found",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    steps_path = tmp_path / "steps.jsonl"
    write_steps_jsonl(
        steps_path,
        [
            {
                "name": "capability reply contract",
                "kind": "eval_suite",
                "suite": "capability_reply_contract",
                "exit_code": 0,
                "report_paths": [str(eval_report)],
                "baseline_path": "evals/baselines/capability_reply_contract.json",
            },
            {
                "name": "rag benchmark manual fixture deterministic gate",
                "kind": "rag_benchmark",
                "suite": "rag_benchmark",
                "exit_code": 0,
                "report_paths": [str(rag_report)],
                "baseline_path": "evals/baselines/rag_benchmark.json",
            },
            {
                "name": "timing signal audit",
                "kind": "timing_signal_audit",
                "suite": "timing_signal_audit",
                "exit_code": 0,
                "report_paths": [str(timing_report)],
            },
        ],
    )

    manifest = build_periodic_manifest(
        steps_path=steps_path,
        run_id="unit_run",
        started_at="2026-06-20T10:00:00+08:00",
        finished_at="2026-06-20T10:05:00+08:00",
        exit_code=0,
        trigger="local",
    )

    assert manifest["manifest_version"] == 1
    assert manifest["run_id"] == "unit_run"
    assert manifest["status"] == "passed"
    assert [step["kind"] for step in manifest["steps"]] == [
        "eval_suite",
        "rag_benchmark",
        "timing_signal_audit",
    ]
    assert manifest["steps"][0]["summary"]["pass_rate"] == 1.0
    assert manifest["steps"][0]["gate_passed"] is True
    assert manifest["steps"][1]["summary"]["hit@5"] == 1.0
    assert manifest["steps"][2]["summary"]["total_samples"] == 0
    assert manifest["steps"][2]["notes"]["reason"] == "db_not_found"


def test_timing_gate_gate_script_uses_stable_baseline():
    script = Path("scripts/run_timing_gate_gate.sh")

    assert script.exists()
    text = script.read_text(encoding="utf-8")
    assert "evals/baselines/timing_gate.json" in text
    assert "--suite timing_gate" in text
    assert "--min-pass-rate 1.0" in text
    assert "--max-new-failures 0" in text
    assert "NANOBOT_ADMIN_TOKEN" in text


def test_eval_pr_gate_script_runs_stable_suites():
    script = Path("scripts/run_eval_pr_gate.sh")

    assert script.exists()
    text = script.read_text(encoding="utf-8")
    assert "scripts/run_timing_gate_gate.sh" in text
    assert "--suite capability_model_routing" in text
    assert "evals/baselines/capability_model_routing.json" in text
    assert "--suite capability_reply_contract" in text
    assert "evals/baselines/capability_reply_contract.json" in text
    assert "--suite capability_rendering_contract" in text
    assert "evals/baselines/capability_rendering_contract.json" in text
    assert "evals.rag_benchmark.run" in text
    assert "--provider-mode deterministic" in text
    assert "--manual-only" in text
    assert "--fixture positive_v1" in text
    assert "--fixture-db tmp/rag_benchmark/fixtures/positive_v1.db" in text
    assert "evals/baselines/rag_benchmark.json" in text
    assert "--min-hit-at-5 1.0" in text
    assert "--min-mrr 1.0" in text
    assert "max-unexpected-source-rate" in text


def test_eval_periodic_script_runs_stable_suites():
    script = Path("scripts/run_eval_periodic.sh")

    assert script.exists()
    text = script.read_text(encoding="utf-8")
    assert "run_step" in text
    assert "scripts/run_timing_gate_gate.sh" in text
    assert "--suite capability_model_routing" in text
    assert "evals/baselines/capability_model_routing.json" in text
    assert "--suite capability_reply_contract" in text
    assert "evals/baselines/capability_reply_contract.json" in text
    assert "--suite capability_rendering_contract" in text
    assert "evals/baselines/capability_rendering_contract.json" in text
    assert "evals.rag_benchmark.run" in text
    assert "--provider-mode deterministic" in text
    assert "--manual-only" in text
    assert "--fixture positive_v1" in text
    assert "--fixture-db tmp/rag_benchmark/fixtures/positive_v1.db" in text
    assert "evals/baselines/rag_benchmark.json" in text
    assert "--min-hit-at-5 1.0" in text
    assert "--min-mrr 1.0" in text
    assert "--max-unexpected-source-rate 0.0" in text


def test_eval_periodic_script_keeps_going_for_archival_reports():
    script = Path("scripts/run_eval_periodic.sh")

    assert script.exists()
    text = script.read_text(encoding="utf-8")
    assert "status=0" in text
    assert "status=1" in text
    assert "return 0" in text
    assert "exit \"$status\"" in text


def test_eval_periodic_script_writes_manifest():
    script = Path("scripts/run_eval_periodic.sh")

    text = script.read_text(encoding="utf-8")
    assert "PERIODIC_RUN_ID" in text
    assert "PERIODIC_STEPS_JSONL" in text
    assert "record_step" in text
    assert "python -B -m evals.periodic_manifest" in text
    assert "periodic_manifest_latest.json" in text
    assert "runs/${PERIODIC_RUN_ID}/manifest.json" in text


def test_eval_pr_gate_workflow_runs_unified_script():
    workflow = Path(".github/workflows/timing-gate-eval.yml")

    assert workflow.exists()
    text = workflow.read_text(encoding="utf-8")
    assert "name: Eval PR Gate" in text
    assert "eval-pr-gate:" in text
    assert "scripts/run_eval_pr_gate.sh" in text


def test_eval_workflow_has_periodic_schedule_and_manual_dispatch():
    workflow = Path(".github/workflows/timing-gate-eval.yml")

    text = workflow.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "schedule:" in text
    assert 'cron: "20 20 * * 0"' in text
    assert "scripts/run_eval_periodic.sh" in text


def test_eval_workflow_uploads_report_artifacts():
    workflow = Path(".github/workflows/timing-gate-eval.yml")

    text = workflow.read_text(encoding="utf-8")
    assert "actions/upload-artifact@v4" in text
    assert "if: always()" in text
    assert "evals/reports/*.json" in text
    assert "tmp/rag_benchmark/reports/*.json" in text
    assert "tmp/rag_benchmark/reports/*.md" in text
    assert "if-no-files-found: warn" in text


def test_eval_workflow_uploads_periodic_manifest():
    workflow = Path(".github/workflows/timing-gate-eval.yml")

    text = workflow.read_text(encoding="utf-8")
    assert "evals/reports/periodic_manifest_*.json" in text
    assert "evals/reports/runs/**/manifest.json" in text


def test_eval_workflow_artifact_retention_is_bounded():
    workflow = Path(".github/workflows/timing-gate-eval.yml")

    text = workflow.read_text(encoding="utf-8")
    assert "retention-days: 14" in text


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


def test_capability_dataset_uses_case_suite_as_runner():
    from evals.run import load_cases, run_suite

    cases = load_cases("capability_model_routing")

    assert cases
    assert {case.suite for case in cases} == {"model_routing"}
    report = run_suite("capability_model_routing")
    assert report.total == len(cases)
    assert report.failed == 0


def test_capability_reply_contract_dataset_uses_reply_runner():
    from evals.run import load_cases, run_suite

    cases = load_cases("capability_reply_contract")

    assert {case.id for case in cases} == {
        "reply_quote_to_bot_001",
        "reply_at_bot_mention_mode_001",
        "reply_directed_to_other_no_reply_001",
    }
    assert {case.suite for case in cases} == {"reply_contract"}
    report = run_suite("capability_reply_contract")
    assert report.total == len(cases)
    assert report.failed == 0


def test_capability_rendering_contract_dataset_runs_offline():
    from evals.run import load_cases, run_suite

    cases = load_cases("capability_rendering_contract")

    assert {case.id for case in cases} == {
        "render_text_html_order_001",
        "render_image_url_as_cq_001",
        "render_generated_image_public_url_001",
        "render_generated_image_without_public_url_001",
        "render_reply_meta_preserved_001",
    }
    assert {case.suite for case in cases} == {"rendering_contract"}
    report = run_suite("capability_rendering_contract")
    assert report.total == len(cases)
    assert report.failed == 0

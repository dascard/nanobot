import json


def _manifest(
    run_id: str,
    *,
    started_at: str,
    finished_at: str | None = None,
    status: str = "passed",
    exit_code: int = 0,
    steps: list[dict] | None = None,
) -> dict:
    return {
        "manifest_version": 1,
        "run_id": run_id,
        "run_type": "periodic",
        "trigger": "local",
        "started_at": started_at,
        "finished_at": finished_at or started_at,
        "status": status,
        "exit_code": exit_code,
        "git": {"sha": run_id, "ref": "master", "repository": ""},
        "steps": steps or [],
    }


def _eval_step(
    suite: str,
    *,
    pass_rate: float,
    failed: int = 0,
    new_failed: int = 0,
) -> dict:
    return {
        "name": suite,
        "kind": "eval_suite",
        "suite": suite,
        "status": "passed" if failed == 0 else "failed",
        "exit_code": 0 if failed == 0 else 1,
        "summary": {
            "total": 10,
            "passed": 10 - failed,
            "failed": failed,
            "pass_rate": pass_rate,
        },
        "gate_passed": failed == 0,
        "new_failed_cases": [f"new_{i}" for i in range(new_failed)],
        "failed_cases": [{"case_id": f"case_{i}"} for i in range(failed)],
    }


def _rag_step(*, pass_rate: float, hit_at_5: float, mrr: float) -> dict:
    return {
        "name": "rag stable gate",
        "kind": "rag_benchmark",
        "suite": "rag_benchmark",
        "status": "passed",
        "exit_code": 0,
        "summary": {
            "total_cases": 13,
            "positive_cases": 4,
            "pass_rate": pass_rate,
            "hit@5": hit_at_5,
            "mrr": mrr,
        },
        "gate_passed": True,
    }


def _timing_step(*, mismatch_count: int, mismatch_rate: float) -> dict:
    return {
        "name": "timing signal audit",
        "kind": "timing_signal_audit",
        "suite": "timing_signal_audit",
        "status": "passed",
        "exit_code": 0,
        "summary": {
            "total_samples": 20,
            "labeled_samples": 5,
            "action_mismatch_count": mismatch_count,
            "action_mismatch_rate": mismatch_rate,
        },
        "notes": {"mode": "sampled"},
    }


def test_artifact_trends_builds_series_and_deltas():
    from evals.artifact_trends import build_artifact_trends, dedupe_manifests

    older_incomplete = _manifest(
        "run_1",
        started_at="2026-06-20T10:00:00+08:00",
        finished_at="2026-06-20T10:01:00+08:00",
        steps=[],
    )
    older = _manifest(
        "run_1",
        started_at="2026-06-20T10:00:00+08:00",
        finished_at="2026-06-20T10:03:00+08:00",
        steps=[
            _eval_step("timing_gate", pass_rate=1.0),
            _rag_step(pass_rate=1.0, hit_at_5=1.0, mrr=1.0),
            _timing_step(mismatch_count=1, mismatch_rate=0.05),
        ],
    )
    newer = _manifest(
        "run_2",
        started_at="2026-06-20T11:00:00+08:00",
        finished_at="2026-06-20T11:05:00+08:00",
        status="failed",
        exit_code=1,
        steps=[
            _eval_step("timing_gate", pass_rate=0.8, failed=2, new_failed=1),
            _rag_step(pass_rate=0.9, hit_at_5=0.75, mrr=0.7),
            _timing_step(mismatch_count=3, mismatch_rate=0.15),
        ],
    )

    trends = build_artifact_trends(
        dedupe_manifests([newer, older_incomplete, older]),
        manifest_globs=["evals/reports/*-periodic_manifest.json"],
    )

    assert trends["source"]["manifest_count"] == 2
    assert trends["source"]["run_count"] == 2
    assert trends["source"]["manifest_globs"] == [
        "evals/reports/*-periodic_manifest.json"
    ]
    assert trends["summary"]["latest_run_id"] == "run_2"
    assert trends["summary"]["previous_run_id"] == "run_1"
    assert trends["summary"]["failed_run_count"] == 1
    assert trends["summary"]["latest_failed_step_count"] == 1
    assert trends["series"]["runs"][0]["run_id"] == "run_1"
    assert trends["series"]["runs"][0]["duration_sec"] == 180.0

    eval_item = trends["series"]["eval_suites"]["timing_gate"][-1]
    assert eval_item["pass_rate_delta"] == -0.2
    assert eval_item["failed_delta"] == 2
    assert eval_item["new_failed_count"] == 1
    assert eval_item["failed_cases"] == [{"case_id": "case_0"}, {"case_id": "case_1"}]

    rag_item = trends["series"]["rag_benchmark"][-1]
    assert rag_item["pass_rate_delta"] == -0.1
    assert rag_item["hit@5_delta"] == -0.25
    assert rag_item["mrr_delta"] == -0.3

    timing_item = trends["series"]["timing_signal_audit"][-1]
    assert timing_item["label_coverage_rate"] == 0.25
    assert timing_item["action_mismatch_count_delta"] == 2
    assert timing_item["action_mismatch_rate_delta"] == 0.1
    assert timing_item["notes"] == {"mode": "sampled"}

    assert {item["type"] for item in trends["regressions"]} >= {
        "run_failed",
        "gate_failed",
        "eval_pass_rate_drop",
        "eval_new_failures",
        "rag_hit_at_5_drop",
        "timing_action_mismatch_rate_increase",
    }


def test_artifact_trends_ignores_latest_report_paths(tmp_path):
    from evals.artifact_trends import build_artifact_trends

    latest = tmp_path / "latest.json"
    latest.write_text(
        json.dumps({"metrics": {"overall": {"hit@5": 0.0}}}),
        encoding="utf-8",
    )
    manifest = _manifest(
        "run_1",
        started_at="2026-06-20T10:00:00+08:00",
        steps=[
            {
                **_rag_step(pass_rate=1.0, hit_at_5=1.0, mrr=1.0),
                "report_paths": [str(latest)],
            }
        ],
    )

    trends = build_artifact_trends([manifest])

    assert trends["series"]["rag_benchmark"][0]["hit@5"] == 1.0


def test_artifact_trends_keeps_unknown_steps_without_metrics():
    from evals.artifact_trends import build_artifact_trends

    manifest = _manifest(
        "run_1",
        started_at="2026-06-20T10:00:00+08:00",
        steps=[
            {
                "name": "custom",
                "kind": "custom_kind",
                "suite": "custom_suite",
                "status": "failed",
                "exit_code": 1,
                "summary": {},
                "report_missing": True,
            }
        ],
    )

    trends = build_artifact_trends([manifest])

    assert trends["summary"]["latest_failed_step_count"] == 1
    assert trends["series"]["runs"][0]["failed_step_count"] == 1
    assert trends["series"]["eval_suites"] == {}
    assert trends["series"]["rag_benchmark"] == []
    assert trends["series"]["timing_signal_audit"] == []
    assert any(item["type"] == "report_missing" for item in trends["regressions"])

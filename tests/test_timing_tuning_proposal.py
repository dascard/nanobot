import json


def _manifest(
    audit_path: str = "evals/reports/runs/run_1/timing_signal_audit.json",
) -> dict:
    return {
        "manifest_version": 1,
        "run_id": "run_1",
        "git": {"sha": "abc123", "ref": "master", "repository": ""},
        "steps": [
            {
                "kind": "timing_signal_audit",
                "suite": "timing_signal_audit",
                "report_paths": [audit_path],
                "summary": {"total_samples": 20, "labeled_samples": 10},
            }
        ],
    }


def _trends() -> dict:
    return {
        "trend_version": 1,
        "source": {
            "run_count": 3,
            "deduped_run_ids": ["run_1", "run_2", "run_3"],
        },
        "summary": {"latest_run_id": "run_3", "previous_run_id": "run_2"},
        "series": {
            "runs": [],
            "eval_suites": {},
            "rag_benchmark": [],
            "timing_signal_audit": [],
        },
        "regressions": [],
    }


def _analysis(ready: bool = True) -> dict:
    return {
        "analysis_version": 1,
        "readiness": {
            "ready": ready,
            "blocking_reasons": [] if ready else [{"code": "low_label_coverage"}],
        },
        "signals": [],
        "recommendations": [],
    }


def _audit(
    *,
    total_samples: int = 20,
    source: dict | None = None,
    samples: list[dict] | None = None,
) -> dict:
    return {
        "total_samples": total_samples,
        "labeled_samples": 10 if total_samples else 0,
        "signals": {},
        "shadow": {"action_mismatch_count": 0, "action_mismatch_rate": 0.0},
        "samples": samples or [],
        "source": source or {"mode": "sampled"},
    }


def _params() -> dict:
    return {
        "candidate_version": 1,
        "source": {"author": "manual", "reason": "unit"},
        "candidates": [
            {
                "id": "ack_threshold_soften_v1",
                "description": "降低 s_ack",
                "scope": "timing_score",
                "param_diff": {"s_ack": 0.75},
                "expected_effect": "减少误杀",
                "risk_level": "medium",
            }
        ],
    }


def _reason_codes(report: dict) -> set[str]:
    return {
        item["code"]
        for item in report["readiness"]["blocking_reasons"]
    }


def test_proposal_blocks_missing_inputs_and_does_not_crash():
    from evals.timing_tuning_proposal import build_timing_tuning_proposal

    report = build_timing_tuning_proposal(
        manifest=None,
        trends=None,
        analysis=None,
        timing_audit=None,
        baseline=None,
        params=None,
        source_paths={},
    )

    assert report["proposal_version"] == 1
    assert report["readiness"]["ready"] is False
    assert _reason_codes(report) >= {
        "manifest_missing",
        "trends_missing",
        "analysis_missing",
        "timing_audit_missing",
        "baseline_missing",
        "missing_param_candidates",
    }
    assert report["apply_policy"] == "manual_only"
    assert report["blocked_actions"] == [
        "auto_apply",
        "baseline_update",
        "gate_change",
    ]


def test_proposal_blocks_skipped_zero_and_missing_action_truth():
    from evals.timing_tuning_proposal import build_timing_tuning_proposal

    skipped = build_timing_tuning_proposal(
        manifest=_manifest(),
        trends=_trends(),
        analysis=_analysis(),
        timing_audit=_audit(
            total_samples=0,
            source={"mode": "skipped", "reason": "db_not_found"},
        ),
        baseline={"suite": "timing_gate"},
        params=_params(),
        source_paths={
            "timing_audit": "evals/reports/runs/run_1/timing_signal_audit.json",
        },
    )
    no_truth = build_timing_tuning_proposal(
        manifest=_manifest(),
        trends=_trends(),
        analysis=_analysis(),
        timing_audit=_audit(
            samples=[
                {
                    "log_id": 1,
                    "signal_name": "s_ack",
                    "label": "false_positive",
                }
            ],
        ),
        baseline={"suite": "timing_gate"},
        params=_params(),
        source_paths={
            "timing_audit": "evals/reports/runs/run_1/timing_signal_audit.json",
        },
    )

    assert "timing_audit_skipped" in _reason_codes(skipped)
    assert "timing_zero_samples" in _reason_codes(skipped)
    assert "missing_action_truth" in _reason_codes(no_truth)
    assert no_truth["candidate_sets"][0]["id"] == "ack_threshold_soften_v1"

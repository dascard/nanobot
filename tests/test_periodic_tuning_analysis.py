def _trends(
    *,
    trend_version: int = 1,
    run_count: int = 3,
    latest_run_id: str = "run_3",
    previous_run_id: str = "run_2",
    timing_items: list[dict] | None = None,
    rag_items: list[dict] | None = None,
    eval_suites: dict[str, list[dict]] | None = None,
    regressions: list[dict] | None = None,
) -> dict:
    return {
        "trend_version": trend_version,
        "source": {
            "run_count": run_count,
            "deduped_run_ids": [f"run_{index}" for index in range(1, run_count + 1)],
        },
        "summary": {
            "latest_run_id": latest_run_id,
            "previous_run_id": previous_run_id,
            "latest_status": "passed",
        },
        "series": {
            "runs": [],
            "eval_suites": eval_suites or {},
            "rag_benchmark": rag_items or [],
            "timing_signal_audit": timing_items or [],
        },
        "regressions": regressions or [],
    }


def _audit(
    *,
    total_samples: int = 20,
    labeled_samples: int = 10,
    signals: dict | None = None,
    samples: list[dict] | None = None,
    source: dict | None = None,
) -> dict:
    return {
        "total_samples": total_samples,
        "labeled_samples": labeled_samples,
        "signals": signals or {},
        "shadow": {
            "total_samples": total_samples,
            "action_mismatch_count": 0,
            "action_mismatch_rate": 0.0,
            "mismatches_by_signal": {},
        },
        "samples": samples or [],
        "source": source or {"db": "data/nanobot.db"},
    }


def _reason_codes(report: dict) -> set[str]:
    return {
        item["code"]
        for item in report["readiness"]["blocking_reasons"]
    }


def _recommendation_codes(report: dict) -> set[str]:
    return {
        item["reason_code"]
        for item in report["recommendations"]
    }


def test_tuning_analysis_blocks_unsupported_trend_version():
    from evals.tuning_analysis import build_tuning_analysis

    report = build_tuning_analysis(_trends(trend_version=2), timing_audit=_audit())

    assert report["analysis_version"] == 1
    assert report["readiness"]["ready"] is False
    assert "unsupported_trend_version" in _reason_codes(report)
    assert "unsupported_trend_version" in _recommendation_codes(report)
    assert all(item["type"] != "candidate_adjustment" for item in report["recommendations"])


def test_tuning_analysis_blocks_insufficient_runs():
    from evals.tuning_analysis import build_tuning_analysis

    report = build_tuning_analysis(_trends(run_count=2), timing_audit=_audit())

    assert report["readiness"]["ready"] is False
    assert "insufficient_runs" in _reason_codes(report)
    recommendation = report["recommendations"][0]
    assert recommendation["type"] == "collect_more_artifact"
    assert recommendation["area"] == "artifact_health"
    assert recommendation["evidence"]["run_count"] == 2


def test_tuning_analysis_blocks_missing_skipped_and_zero_timing_audit():
    from evals.tuning_analysis import build_tuning_analysis

    missing = build_tuning_analysis(_trends(), timing_audit=None)
    skipped = build_tuning_analysis(
        _trends(),
        timing_audit=_audit(
            total_samples=0,
            labeled_samples=0,
            source={"mode": "skipped", "reason": "db_not_found"},
        ),
    )
    zero = build_tuning_analysis(
        _trends(),
        timing_audit=_audit(total_samples=0, labeled_samples=0),
    )

    assert "timing_audit_missing" in _reason_codes(missing)
    assert "timing_audit_skipped" in _reason_codes(skipped)
    assert "timing_zero_samples" in _reason_codes(zero)
    assert "db_not_found" in skipped["source"]["timing_audit_reason"]


def test_tuning_analysis_recommends_labeling_for_low_label_coverage():
    from evals.tuning_analysis import build_tuning_analysis

    report = build_tuning_analysis(
        _trends(),
        timing_audit=_audit(total_samples=20, labeled_samples=2),
    )

    assert report["readiness"]["ready"] is False
    assert "low_label_coverage" in _reason_codes(report)
    assert "low_label_coverage" in _recommendation_codes(report)
    assert report["summary"]["label_more_samples_count"] == 1


def test_tuning_analysis_flags_high_signal_false_positive_rate_with_evidence():
    from evals.tuning_analysis import build_tuning_analysis

    signals = {
        "s_ack": {
            "samples": 10,
            "labeled_samples": 6,
            "false_positive_count": 2,
            "true_positive_count": 4,
            "unknown_count": 4,
            "false_positive_rate": 0.333333,
            "actions": {"no_reply": 8, "reply_now": 2},
            "suggestion": "review_threshold",
        }
    }
    samples = [
        {
            "log_id": 101,
            "signal_name": "s_ack",
            "signal_value": 0.85,
            "label": "false_positive",
            "runtime_action": "no_reply",
            "scoring_action": "reply_now",
            "action_mismatch": True,
            "text_preview": "好的，再帮我查下昨天的新闻",
        },
        {
            "log_id": 102,
            "signal_name": "s_ack",
            "signal_value": 0.85,
            "label": "false_positive",
            "runtime_action": "no_reply",
            "scoring_action": "no_reply",
            "action_mismatch": False,
            "text_preview": "嗯，继续说",
        },
    ]

    report = build_tuning_analysis(
        _trends(),
        timing_audit=_audit(signals=signals, samples=samples),
    )

    signal = report["signals"][0]
    assert signal["name"] == "s_ack"
    assert signal["label_coverage_rate"] == 0.6
    assert signal["false_positive_rate"] == 0.333333
    assert signal["mismatch_count"] == 0
    assert signal["evidence_samples"][0]["log_id"] == 101
    review = [
        item for item in report["recommendations"]
        if item["reason_code"] == "high_false_positive_rate"
    ][0]
    assert review["type"] == "manual_review"
    assert review["area"] == "timing_signal"
    assert review["evidence"]["signal"] == "s_ack"
    assert review["evidence"]["sample_log_ids"] == [101, 102]

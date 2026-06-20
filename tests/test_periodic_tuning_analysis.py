import json


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
    assert all(
        item["type"] != "candidate_adjustment"
        for item in report["recommendations"]
    )


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


def test_tuning_analysis_blocks_insufficient_timing_samples():
    from evals.tuning_analysis import build_tuning_analysis

    report = build_tuning_analysis(
        _trends(),
        timing_audit=_audit(total_samples=10, labeled_samples=5),
        min_total_samples=20,
    )

    assert report["readiness"]["ready"] is False
    assert "insufficient_timing_samples" in _reason_codes(report)
    recommendation = [
        item for item in report["recommendations"]
        if item["reason_code"] == "insufficient_timing_samples"
    ][0]
    assert recommendation["type"] == "collect_more_artifact"
    assert recommendation["area"] == "timing_signal"
    assert recommendation["evidence"] == {
        "total_samples": 10,
        "min_total_samples": 20,
    }


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


def test_tuning_analysis_recommends_review_for_timing_rag_and_eval_regressions():
    from evals.tuning_analysis import build_tuning_analysis

    trends = _trends(
        timing_items=[{
            "run_id": "run_3",
            "action_mismatch_count_delta": 2,
            "action_mismatch_rate_delta": 0.1,
        }],
        rag_items=[{
            "run_id": "run_3",
            "pass_rate_delta": -0.1,
            "hit@5_delta": -0.25,
            "mrr_delta": -0.3,
        }],
        eval_suites={
            "timing_gate": [{
                "run_id": "run_3",
                "suite": "timing_gate",
                "pass_rate_delta": -0.2,
                "failed_delta": 2,
                "new_failed_count": 1,
            }]
        },
        regressions=[{"type": "rag_mrr_drop", "run_id": "run_3", "delta": -0.3}],
    )

    report = build_tuning_analysis(trends, timing_audit=_audit())
    codes = _recommendation_codes(report)

    assert "timing_action_mismatch_increase" in codes
    assert "rag_metric_drop" in codes
    assert "eval_suite_regression" in codes
    assert report["summary"]["must_review_count"] == 3
    assert report["regression_refs"] == [
        {
            "type": "rag_mrr_drop",
            "run_id": "run_3",
            "delta": -0.3,
            "source": "artifact_trends",
        }
    ]
    assert all(item["type"] != "candidate_adjustment" for item in report["recommendations"])


def test_tuning_analysis_emits_no_change_when_ready_and_stable():
    from evals.tuning_analysis import build_tuning_analysis

    report = build_tuning_analysis(_trends(), timing_audit=_audit())

    assert report["readiness"]["ready"] is True
    assert report["recommendations"] == [{
        "type": "no_change",
        "area": "artifact_health",
        "severity": "info",
        "reason_code": "stable_metrics",
        "message": "周期趋势和 TimingSignal audit 未显示需要调参的退化信号",
        "evidence": {"run_count": 3},
    }]
    assert report["summary"]["no_change_count"] == 1


def test_resolve_timing_audit_path_prefers_explicit_then_manifest(tmp_path):
    from evals.tuning_analysis import resolve_timing_audit_path

    explicit = tmp_path / "explicit.json"
    explicit.write_text("{}", encoding="utf-8")
    manifest_report = tmp_path / "manifest-audit.json"
    manifest_report.write_text("{}", encoding="utf-8")
    manifest = {
        "steps": [{
            "kind": "timing_signal_audit",
            "suite": "timing_signal_audit",
            "report_paths": [str(manifest_report)],
        }]
    }

    assert resolve_timing_audit_path(manifest, explicit) == explicit
    assert resolve_timing_audit_path(manifest, None) == manifest_report
    assert resolve_timing_audit_path({"steps": []}, None) is None


def test_tuning_analysis_cli_writes_report(tmp_path, capsys):
    from evals import tuning_analysis

    trends_path = tmp_path / "artifact_trends_latest.json"
    audit_path = tmp_path / "timing_signal_audit_latest.json"
    manifest_path = tmp_path / "periodic_manifest_latest.json"
    out_path = tmp_path / "tuning_analysis_latest.json"
    trends_path.write_text(json.dumps(_trends(), ensure_ascii=False), encoding="utf-8")
    audit_path.write_text(json.dumps(_audit(), ensure_ascii=False), encoding="utf-8")
    manifest_path.write_text(json.dumps({"steps": []}, ensure_ascii=False), encoding="utf-8")

    exit_code = tuning_analysis.main([
        "--trends",
        str(trends_path),
        "--timing-audit",
        str(audit_path),
        "--manifest",
        str(manifest_path),
        "--out",
        str(out_path),
    ])

    captured = capsys.readouterr()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert captured.out.strip() == f"tuning_analysis={out_path}"
    assert payload["analysis_version"] == 1
    assert payload["source"]["trends_path"] == str(trends_path)
    assert payload["source"]["timing_audit_path"] == str(audit_path)
    assert payload["source"]["manifest_path"] == str(manifest_path)
    assert payload["readiness"]["ready"] is True

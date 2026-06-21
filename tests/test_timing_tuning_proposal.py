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


def test_proposal_rejects_explicit_latest_timing_audit(tmp_path):
    from evals.timing_tuning_proposal import resolve_proposal_timing_audit_path

    latest = tmp_path / "timing_signal_audit_latest.json"
    latest.write_text("{}", encoding="utf-8")

    resolved, blocking = resolve_proposal_timing_audit_path(
        _manifest(str(latest)),
        latest,
        run_id="run_1",
    )

    assert resolved is None
    assert blocking[0]["code"] == "explicit_latest_audit"


def test_proposal_blocks_audit_run_mismatch_and_invalid_truth():
    from evals.timing_tuning_proposal import build_timing_tuning_proposal

    report = build_timing_tuning_proposal(
        manifest=_manifest(),
        trends=_trends(),
        analysis=_analysis(),
        timing_audit=_audit(
            samples=[{"log_id": 1, "signal_name": "s_ack", "final_timing_action": "maybe"}],
            source={"mode": "sampled", "run_id": "other_run"},
        ),
        baseline={"suite": "timing_gate"},
        params=_params(),
        source_paths={
            "timing_audit": "evals/reports/runs/run_1/timing_signal_audit.json",
        },
    )

    assert "audit_run_mismatch" in _reason_codes(report)
    assert "invalid_action_truth" in _reason_codes(report)


def test_proposal_candidate_governance_blocks_duplicate_id_and_empty_diff():
    from evals.timing_tuning_proposal import build_timing_tuning_proposal

    params = _params()
    params["candidates"].append({
        "id": "ack_threshold_soften_v1",
        "description": "重复候选",
        "scope": "timing_score",
        "param_diff": {},
        "risk_level": "low",
        "expected_effect": "无",
        "evidence_refs": [{"type": "timing_signal_audit_sample", "log_id": 101}],
    })
    params["candidates"].append({
        "description": "缺少 ID",
        "scope": "timing_score",
        "param_diff": {"s_ack": 0.65},
        "risk_level": "low",
    })

    report = build_timing_tuning_proposal(
        manifest=_manifest(),
        trends=_trends(),
        analysis=_analysis(),
        timing_audit=_audit(
            samples=[{"final_timing_action": "continue"}],
            source={"mode": "sampled", "run_id": "run_1"},
        ),
        baseline={"suite": "timing_gate"},
        params=params,
        source_paths={"timing_audit": "evals/reports/runs/run_1/timing_signal_audit.json"},
    )

    codes = _reason_codes(report)
    assert "duplicate_candidate_id" in codes
    assert "empty_candidate_param_diff" in codes
    assert "missing_candidate_id" in codes
    assert report["candidate_sets"][1]["expected_effect"] == "无"
    assert report["candidate_sets"][1]["evidence_refs"] == [
        {"type": "timing_signal_audit_sample", "log_id": 101}
    ]


def test_proposal_blocks_truth_sample_without_replay_input():
    from evals.timing_tuning_proposal import build_timing_tuning_proposal

    report = build_timing_tuning_proposal(
        manifest=_manifest(),
        trends=_trends(),
        analysis=_analysis(),
        timing_audit=_audit(
            samples=[
                {
                    "log_id": 101,
                    "signal_name": "s_ack",
                    "final_timing_action": "continue",
                }
            ],
            source={"mode": "sampled", "run_id": "run_1"},
        ),
        baseline={"suite": "timing_gate"},
        params=_params(),
        source_paths={"timing_audit": "evals/reports/runs/run_1/timing_signal_audit.json"},
    )

    assert "missing_replay_input" in _reason_codes(report)


def test_timing_tuning_proposal_parser_defaults_params_and_accepts_run_id():
    from evals.timing_tuning_proposal import DEFAULT_PARAMS, build_parser

    args = build_parser().parse_args(["--run-id", "run_1"])

    assert args.run_id == "run_1"
    assert args.params == str(DEFAULT_PARAMS)


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


def test_proposal_rejects_unsupported_candidate_params():
    from evals.timing_tuning_proposal import build_timing_tuning_proposal

    params = _params()
    params["candidates"][0]["param_diff"] = {"unknown_param": 1}

    report = build_timing_tuning_proposal(
        manifest=_manifest(),
        trends=_trends(),
        analysis=_analysis(),
        timing_audit=_audit(samples=[{"expected_action": "continue"}]),
        baseline={"suite": "timing_gate"},
        params=params,
        source_paths={
            "timing_audit": "evals/reports/runs/run_1/timing_signal_audit.json",
        },
    )

    assert "unsupported_proposal_input" in _reason_codes(report)
    assert report["parameters"] == [
        {
            "candidate_id": "ack_threshold_soften_v1",
            "name": "unknown_param",
            "value": 1,
        }
    ]


def test_timing_tuning_proposal_cli_writes_report(tmp_path, capsys):
    from evals import timing_tuning_proposal

    manifest = tmp_path / "periodic_manifest_latest.json"
    trends = tmp_path / "artifact_trends_latest.json"
    analysis = tmp_path / "tuning_analysis_latest.json"
    audit = tmp_path / "runs" / "run_1" / "timing_signal_audit.json"
    baseline = tmp_path / "timing_gate.json"
    params = tmp_path / "param_candidates.json"
    out = tmp_path / "timing_tuning_proposal_latest.json"
    audit.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(_manifest(str(audit)), ensure_ascii=False),
        encoding="utf-8",
    )
    trends.write_text(json.dumps(_trends(), ensure_ascii=False), encoding="utf-8")
    analysis.write_text(
        json.dumps(_analysis(), ensure_ascii=False),
        encoding="utf-8",
    )
    audit.write_text(
        json.dumps(
            _audit(
                samples=[
                    {
                        "log_id": 101,
                        "signal_name": "s_ack",
                        "final_timing_action": "continue",
                        "timing_input": {
                            "text": "好的",
                            "is_group": True,
                            "is_at_bot": True,
                            "trigger_reason": "at_bot",
                        },
                    }
                ]
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    baseline.write_text(
        json.dumps({"suite": "timing_gate"}, ensure_ascii=False),
        encoding="utf-8",
    )
    params.write_text(json.dumps(_params(), ensure_ascii=False), encoding="utf-8")

    exit_code = timing_tuning_proposal.main(
        [
            "--manifest",
            str(manifest),
            "--trends",
            str(trends),
            "--analysis",
            str(analysis),
            "--baseline",
            str(baseline),
            "--params",
            str(params),
            "--out",
            str(out),
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert captured.out.strip() == f"timing_tuning_proposal={out}"
    assert payload["source"]["timing_audit_path"] == str(audit)
    assert payload["source"]["params_path"] == str(params)
    assert payload["readiness"]["ready"] is True


def test_timing_tuning_proposal_cli_has_no_apply_modes():
    import pytest
    from evals import timing_tuning_proposal

    for option in ("--apply", "--update-baseline", "--write-config", "--promote"):
        with pytest.raises(SystemExit) as excinfo:
            timing_tuning_proposal.main([option])

        assert excinfo.value.code == 2


def test_timing_tuning_proposal_cli_preserves_analysis_blocking(tmp_path):
    from evals import timing_tuning_proposal

    manifest = tmp_path / "periodic_manifest_latest.json"
    trends = tmp_path / "artifact_trends_latest.json"
    analysis = tmp_path / "tuning_analysis_latest.json"
    audit = tmp_path / "runs" / "run_1" / "timing_signal_audit.json"
    baseline = tmp_path / "timing_gate.json"
    params = tmp_path / "param_candidates.json"
    out = tmp_path / "timing_tuning_proposal_latest.json"
    audit.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(_manifest(str(audit)), ensure_ascii=False),
        encoding="utf-8",
    )
    trends.write_text(json.dumps(_trends(), ensure_ascii=False), encoding="utf-8")
    analysis.write_text(
        json.dumps(_analysis(ready=False), ensure_ascii=False),
        encoding="utf-8",
    )
    audit.write_text(
        json.dumps(
            _audit(
                samples=[
                    {
                        "log_id": 101,
                        "signal_name": "s_ack",
                        "final_timing_action": "continue",
                        "timing_input": {
                            "text": "好的",
                            "is_group": True,
                            "is_at_bot": True,
                            "trigger_reason": "at_bot",
                        },
                    }
                ]
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    baseline.write_text(
        json.dumps({"suite": "timing_gate"}, ensure_ascii=False),
        encoding="utf-8",
    )
    params.write_text(json.dumps(_params(), ensure_ascii=False), encoding="utf-8")

    exit_code = timing_tuning_proposal.main(
        [
            "--manifest",
            str(manifest),
            "--trends",
            str(trends),
            "--analysis",
            str(analysis),
            "--baseline",
            str(baseline),
            "--params",
            str(params),
            "--out",
            str(out),
        ]
    )

    payload = json.loads(out.read_text(encoding="utf-8"))
    reasons = payload["readiness"]["blocking_reasons"]
    assert exit_code == 0
    assert payload["readiness"]["ready"] is False
    assert reasons == [
        {
            "code": "unsupported_proposal_input",
            "message": "tuning analysis 未 ready",
            "source": "tuning_analysis",
            "upstream_reasons": [{"code": "low_label_coverage"}],
        }
    ]


def test_timing_tuning_proposal_cli_includes_case_simulation(tmp_path):
    from evals import timing_tuning_proposal

    manifest = tmp_path / "periodic_manifest_latest.json"
    trends = tmp_path / "artifact_trends_latest.json"
    analysis = tmp_path / "tuning_analysis_latest.json"
    audit = tmp_path / "runs" / "run_1" / "timing_signal_audit.json"
    cases = tmp_path / "cases"
    baseline = tmp_path / "timing_gate.json"
    params = tmp_path / "param_candidates.json"
    out = tmp_path / "timing_tuning_proposal_latest.json"
    audit.parent.mkdir(parents=True)
    cases.mkdir()
    manifest.write_text(
        json.dumps(_manifest(str(audit)), ensure_ascii=False),
        encoding="utf-8",
    )
    trends.write_text(json.dumps(_trends(), ensure_ascii=False), encoding="utf-8")
    analysis.write_text(
        json.dumps(_analysis(), ensure_ascii=False),
        encoding="utf-8",
    )
    audit.write_text(
        json.dumps(
            _audit(
                samples=[
                    {
                        "log_id": 101,
                        "signal_name": "s_ack",
                        "final_timing_action": "continue",
                        "timing_input": {
                            "text": "好的",
                            "is_group": True,
                            "is_at_bot": True,
                            "trigger_reason": "at_bot",
                        },
                    }
                ]
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (cases / "at_bot_ack_continue_001.json").write_text(
        json.dumps(
            {
                "case_id": "at_bot_ack_continue_001",
                "input": {
                    "text": "好的",
                    "is_group": True,
                    "is_at_bot": True,
                    "trigger_reason": "at_bot",
                },
                "expected": {"timing_action": "continue"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    baseline.write_text(
        json.dumps({"suite": "timing_gate"}, ensure_ascii=False),
        encoding="utf-8",
    )
    candidate_params = _params()
    candidate_params["candidates"][0]["param_diff"] = {"s_ack": 0.5}
    params.write_text(
        json.dumps(candidate_params, ensure_ascii=False),
        encoding="utf-8",
    )

    exit_code = timing_tuning_proposal.main(
        [
            "--manifest",
            str(manifest),
            "--trends",
            str(trends),
            "--analysis",
            str(analysis),
            "--baseline",
            str(baseline),
            "--params",
            str(params),
            "--cases",
            str(cases),
            "--out",
            str(out),
        ]
    )

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["simulation"]["case_count"] == 1
    assert payload["simulation"]["candidate_count"] == 1
    assert payload["simulation"]["flip_count"] == 2
    assert payload["simulation"]["flips"][0]["case_id"] == "at_bot_ack_continue_001"
    assert payload["simulation"]["sources"]["audit_sample_count"] == 1
    assert {
        item["source_type"]
        for item in payload["simulation"]["flips"]
    } == {"eval_case", "timing_signal_audit_sample"}

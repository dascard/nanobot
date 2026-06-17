"""TimingGate 真实日志信号审计测试。"""

import json
from types import SimpleNamespace


def test_extract_timing_signal_samples_reads_scoring_sub_signals():
    from core.eval_sampling.timing_signal_audit import extract_timing_signal_samples

    row = SimpleNamespace(
        id=11,
        session_id="group_42",
        role="ambient",
        content="[A]: 好的",
        meta_json=json.dumps(
            {
                "timing_gate": {
                    "action": "no_reply",
                    "reason": "ambient scoring shortcut",
                    "trigger_reason": "ambient",
                    "scoring": {
                        "stage": "rule_shortcut",
                        "action": "no_reply",
                        "model_used": False,
                        "signals": {
                            "sub_signals": {
                                "s_ack": 0.85,
                                "s_transport": 0.0,
                                "w_marker": 0.0,
                            }
                        },
                    },
                }
            },
            ensure_ascii=False,
        ),
    )

    samples = extract_timing_signal_samples(
        [row],
        signal_names=("s_ack", "s_transport", "w_marker"),
    )

    assert len(samples) == 1
    assert samples[0]["log_id"] == 11
    assert samples[0]["signal_name"] == "s_ack"
    assert samples[0]["signal_value"] == 0.85
    assert samples[0]["runtime_action"] == "no_reply"
    assert samples[0]["scoring_action"] == "no_reply"
    assert samples[0]["text_preview"] == "[A]: 好的"


def test_build_timing_signal_audit_report_counts_labels_and_suggestions():
    from core.eval_sampling.timing_signal_audit import build_timing_signal_audit_report

    samples = [
        {
            "signal_name": "s_ack",
            "label": "false_positive",
            "runtime_action": "no_reply",
            "scoring_action": "no_reply",
        },
        {
            "signal_name": "s_ack",
            "label": "true_positive",
            "runtime_action": "continue",
            "scoring_action": "no_reply",
        },
        {
            "signal_name": "s_transport",
            "label": "unknown",
            "runtime_action": "wait",
            "scoring_action": "wait",
        },
        {
            "signal_name": "w_marker",
            "label": "false_positive",
            "runtime_action": "wait",
            "scoring_action": "continue",
        },
    ]

    report = build_timing_signal_audit_report(samples)

    assert report["total_samples"] == 4
    assert report["signals"]["s_ack"]["samples"] == 2
    assert report["signals"]["s_ack"]["false_positive_count"] == 1
    assert report["signals"]["s_ack"]["false_positive_rate"] == 0.5
    assert report["signals"]["w_marker"]["suggestion"] == "review_threshold"
    assert report["shadow"]["action_mismatch_count"] == 2
    assert report["shadow"]["action_mismatch_rate"] == 0.5


def test_timing_signal_audit_cli_writes_report(tmp_path, db_session):
    from core.database import ChatLog
    from evals.timing_signal_audit import run_audit

    db_session.add(ChatLog(
        user_id="group_42",
        session_id="group_42",
        role="ambient",
        content="[A]: 好的",
        meta_json=json.dumps(
            {
                "timing_gate": {
                    "action": "wait",
                    "trigger_reason": "ambient",
                    "scoring": {
                        "stage": "rule_shortcut",
                        "action": "no_reply",
                        "model_used": False,
                        "signals": {"sub_signals": {"s_ack": 0.85}},
                    },
                }
            },
            ensure_ascii=False,
        ),
    ))
    db_session.commit()
    out = tmp_path / "audit.json"

    report = run_audit(db_session, output_path=out, limit=20)

    assert report["total_samples"] == 1
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["signals"]["s_ack"]["samples"] == 1
    assert payload["shadow"]["action_mismatch_count"] == 1

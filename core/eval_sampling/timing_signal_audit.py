"""TimingGate 日志信号审计纯函数。"""

from __future__ import annotations

import json
from typing import Any, Iterable


SIGNAL_NAMES = ("s_ack", "s_transport", "w_marker")
FALSE_POSITIVE_LABELS = {"false_positive", "fp", "误判", "假阳性"}
TRUE_POSITIVE_LABELS = {"true_positive", "tp", "正确"}


def safe_json(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def _get_field(row: Any, name: str, default: Any = "") -> Any:
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def _to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def extract_timing_signal_samples(
    rows: Iterable[Any],
    *,
    signal_names: tuple[str, ...] = SIGNAL_NAMES,
    min_value: float = 0.01,
) -> list[dict]:
    """从 ChatLog 行中抽取命中的 TimingGate scoring 信号样本。"""
    samples: list[dict] = []
    wanted = tuple(str(name) for name in signal_names)

    for row in rows:
        if str(_get_field(row, "role", "")) != "ambient":
            continue
        meta = safe_json(_get_field(row, "meta_json", "{}"))
        timing_gate = meta.get("timing_gate")
        if not isinstance(timing_gate, dict):
            continue
        scoring = timing_gate.get("scoring")
        if not isinstance(scoring, dict):
            continue
        signals = scoring.get("signals")
        if not isinstance(signals, dict):
            continue
        sub_signals = signals.get("sub_signals")
        if not isinstance(sub_signals, dict):
            continue

        runtime_action = str(timing_gate.get("action") or "")
        scoring_action = str(scoring.get("action") or "")
        for signal_name in wanted:
            value = _to_float(sub_signals.get(signal_name))
            if value < min_value:
                continue
            samples.append({
                "log_id": _get_field(row, "id", None),
                "session_id": str(_get_field(row, "session_id", "") or ""),
                "signal_name": signal_name,
                "signal_value": value,
                "runtime_action": runtime_action,
                "trigger_reason": str(timing_gate.get("trigger_reason") or ""),
                "scoring_stage": str(scoring.get("stage") or ""),
                "scoring_action": scoring_action,
                "model_used": bool(scoring.get("model_used", False)),
                "model_action": str(scoring.get("model_action") or ""),
                "action_mismatch": bool(runtime_action and scoring_action and runtime_action != scoring_action),
                "reason": str(timing_gate.get("reason") or ""),
                "text_preview": str(_get_field(row, "content", "") or "")[:200],
            })

    return samples


def normalize_label(value: str) -> str:
    label = str(value or "").strip().lower()
    if label in FALSE_POSITIVE_LABELS:
        return "false_positive"
    if label in TRUE_POSITIVE_LABELS:
        return "true_positive"
    return "unknown"


def merge_timing_signal_labels(
    samples: list[dict[str, Any]],
    labels: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按 log_id + signal_name 合并人工标注，返回新样本列表。"""
    label_by_key = {
        (label.get("log_id"), str(label.get("signal_name") or "")): label
        for label in labels
        if label.get("log_id") is not None and label.get("signal_name")
    }
    merged: list[dict[str, Any]] = []
    for sample in samples:
        item = dict(sample)
        label = label_by_key.get((sample.get("log_id"), str(sample.get("signal_name") or "")))
        if label:
            item.update(label)
        merged.append(item)
    return merged


def _empty_signal_stats() -> dict:
    return {
        "samples": 0,
        "labeled_samples": 0,
        "false_positive_count": 0,
        "true_positive_count": 0,
        "unknown_count": 0,
        "false_positive_rate": 0.0,
        "actions": {},
        "suggestion": "needs_label",
    }


def build_timing_signal_audit_report(samples: list[dict]) -> dict:
    """聚合人工标注和 shadow action 对比结果。"""
    signals: dict[str, dict] = {}
    mismatch_count = 0
    mismatches_by_signal: dict[str, int] = {}

    for sample in samples:
        signal_name = str(sample.get("signal_name") or "")
        if not signal_name:
            continue
        stats = signals.setdefault(signal_name, _empty_signal_stats())
        stats["samples"] += 1

        runtime_action = str(sample.get("runtime_action") or "")
        if runtime_action:
            actions = stats["actions"]
            actions[runtime_action] = int(actions.get(runtime_action, 0)) + 1

        label = normalize_label(str(sample.get("label") or ""))
        if label == "false_positive":
            stats["labeled_samples"] += 1
            stats["false_positive_count"] += 1
        elif label == "true_positive":
            stats["labeled_samples"] += 1
            stats["true_positive_count"] += 1
        else:
            stats["unknown_count"] += 1

        runtime = str(sample.get("runtime_action") or "")
        scoring = str(sample.get("scoring_action") or "")
        if bool(sample.get("action_mismatch")) or (runtime and scoring and runtime != scoring):
            mismatch_count += 1
            mismatches_by_signal[signal_name] = int(mismatches_by_signal.get(signal_name, 0)) + 1

    labeled_total = 0
    for stats in signals.values():
        labeled = int(stats["labeled_samples"])
        labeled_total += labeled
        if labeled > 0:
            rate = stats["false_positive_count"] / labeled
            stats["false_positive_rate"] = round(rate, 6)
            stats["suggestion"] = "review_threshold" if rate >= 0.2 else "keep_threshold"

    total = len(samples)
    mismatch_rate = (mismatch_count / total) if total else 0.0
    return {
        "total_samples": total,
        "labeled_samples": labeled_total,
        "signals": signals,
        "shadow": {
            "total_samples": total,
            "action_mismatch_count": mismatch_count,
            "action_mismatch_rate": round(mismatch_rate, 6),
            "mismatches_by_signal": mismatches_by_signal,
        },
    }

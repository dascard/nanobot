"""RAG benchmark baseline diff 与 gate 纯函数。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_METRIC_KEYS = (
    "overall.pass_rate",
    "overall.hit@5",
    "overall.mrr",
    "overall.degraded_rate",
    "overall.case_false_positive_rate",
    "overall.unexpected_source_rate",
)


def load_rag_baseline(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def failed_case_ids(report: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for item in report.get("case_scores") or []:
        if not item.get("ok"):
            case_id = str(item.get("case_id") or "")
            if case_id:
                result.add(case_id)
    return result


def metric_value(report: dict[str, Any], dotted_key: str) -> float:
    value: Any = report.get("metrics") or {}
    for part in dotted_key.split("."):
        if not isinstance(value, dict):
            return 0.0
        value = value.get(part)
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def build_rag_baseline_diff(
    current: dict[str, Any],
    baseline: dict[str, Any],
    *,
    baseline_path: str = "",
) -> dict[str, Any]:
    current_failed = failed_case_ids(current)
    baseline_failed = failed_case_ids(baseline)
    metric_deltas = {
        key: round(metric_value(current, key) - metric_value(baseline, key), 12)
        for key in DEFAULT_METRIC_KEYS
    }
    return {
        "baseline_path": baseline_path,
        "baseline_suite": str(baseline.get("suite") or ""),
        "baseline_provider_mode": str(baseline.get("provider_mode") or ""),
        "baseline_case_scope": str(baseline.get("case_scope") or ""),
        "total_delta": int(
            metric_value(current, "overall.total_cases")
            - metric_value(baseline, "overall.total_cases")
        ),
        "pass_rate_delta": metric_deltas["overall.pass_rate"],
        "hit_at_5_delta": metric_deltas["overall.hit@5"],
        "mrr_delta": metric_deltas["overall.mrr"],
        "degraded_rate_delta": metric_deltas["overall.degraded_rate"],
        "new_failed_cases": sorted(current_failed - baseline_failed),
        "fixed_cases": sorted(baseline_failed - current_failed),
        "still_failed_cases": sorted(current_failed & baseline_failed),
        "metric_deltas": metric_deltas,
    }


def evaluate_rag_gate(
    report: dict[str, Any],
    *,
    baseline_diff: dict[str, Any] | None = None,
    min_pass_rate: float | None = None,
    min_hit_at_5: float | None = None,
    min_mrr: float | None = None,
    max_new_failures: int | None = None,
    max_degraded_rate: float | None = None,
    max_unexpected_source_rate: float | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    if metric_value(report, "overall.total_cases") <= 0:
        errors.append("no_cases_executed")
    if (
        min_pass_rate is not None
        and metric_value(report, "overall.pass_rate") < min_pass_rate
    ):
        errors.append("pass_rate below threshold")
    if (
        min_hit_at_5 is not None
        and metric_value(report, "overall.hit@5") < min_hit_at_5
    ):
        errors.append("hit@5 below threshold")
    if min_mrr is not None and metric_value(report, "overall.mrr") < min_mrr:
        errors.append("mrr below threshold")
    if (
        max_degraded_rate is not None
        and metric_value(report, "overall.degraded_rate") > max_degraded_rate
    ):
        errors.append("degraded_rate above threshold")
    if (
        max_unexpected_source_rate is not None
        and metric_value(report, "overall.unexpected_source_rate")
        > max_unexpected_source_rate
    ):
        errors.append("unexpected_source_rate above threshold")
    if max_new_failures is not None:
        if baseline_diff is None:
            errors.append("baseline required when max_new_failures is set")
        elif len(baseline_diff.get("new_failed_cases") or []) > max_new_failures:
            errors.append("new_failed_cases exceeds threshold")
    if baseline_diff is not None:
        if baseline_diff.get("baseline_suite") not in {"", "rag_benchmark"}:
            errors.append(
                "baseline suite mismatch: "
                f"current=rag_benchmark baseline={baseline_diff.get('baseline_suite')}"
            )
        baseline_provider = baseline_diff.get("baseline_provider_mode")
        if baseline_provider and baseline_provider != str(report.get("provider_mode") or ""):
            errors.append("baseline provider_mode mismatch")
        baseline_scope = baseline_diff.get("baseline_case_scope")
        if baseline_scope and baseline_scope != str(report.get("case_scope") or ""):
            errors.append("baseline case_scope mismatch")
    return {"passed": not errors, "errors": errors}

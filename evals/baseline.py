"""Eval baseline diff 与门禁纯函数。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.schema import SuiteReport


def load_baseline_report(path: str | Path) -> SuiteReport:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return SuiteReport(**data)


def failed_case_ids(report: SuiteReport) -> set[str]:
    return {
        str(item.get("case_id") or "")
        for item in report.failed_cases
        if item.get("case_id")
    }


def build_baseline_diff(
    current: SuiteReport,
    baseline: SuiteReport,
    *,
    baseline_path: str = "",
) -> dict[str, Any]:
    current_failed = failed_case_ids(current)
    baseline_failed = failed_case_ids(baseline)
    return {
        "baseline_path": baseline_path,
        "suite": current.suite,
        "baseline_suite": baseline.suite,
        "total_delta": current.total - baseline.total,
        "passed_delta": current.passed - baseline.passed,
        "failed_delta": current.failed - baseline.failed,
        "pass_rate_delta": round(current.pass_rate - baseline.pass_rate, 12),
        "current_failed_cases": sorted(current_failed),
        "baseline_failed_cases": sorted(baseline_failed),
        "new_failed_cases": sorted(current_failed - baseline_failed),
        "fixed_cases": sorted(baseline_failed - current_failed),
        "still_failed_cases": sorted(current_failed & baseline_failed),
    }


def evaluate_gate(
    report: SuiteReport,
    *,
    baseline_diff: dict[str, Any] | None = None,
    min_pass_rate: float | None = None,
    max_new_failures: int | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    new_failure_count = 0

    if min_pass_rate is not None and report.pass_rate < min_pass_rate:
        errors.append(
            f"pass_rate below threshold: actual={report.pass_rate:.4f} min={min_pass_rate:.4f}"
        )

    if max_new_failures is not None:
        if baseline_diff is None:
            errors.append("baseline required when max_new_failures is set")
        else:
            baseline_suite = str(baseline_diff.get("baseline_suite") or "")
            if baseline_suite and baseline_suite != report.suite:
                errors.append(
                    f"baseline suite mismatch: current={report.suite} baseline={baseline_suite}"
                )
            new_failures = baseline_diff.get("new_failed_cases") or []
            new_failure_count = len(new_failures)
            if new_failure_count > max_new_failures:
                errors.append(
                    "new_failed_cases exceeds threshold: "
                    f"actual={new_failure_count} max={max_new_failures}"
                )

    return {
        "passed": not errors,
        "errors": errors,
        "min_pass_rate": min_pass_rate,
        "max_new_failures": max_new_failures,
        "new_failure_count": new_failure_count,
    }

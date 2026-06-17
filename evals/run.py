"""Eval runner——CLI 入口。 python -m evals.run --suite regression"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from evals.baseline import build_baseline_diff, evaluate_gate, load_baseline_report
from evals.schema import EvalCase, EvalResult, SuiteReport
from evals.scorers import score_case

HERE = Path(__file__).resolve().parent
CASES_DIR = HERE / "cases"
REPORTS_DIR = HERE / "reports"


def load_cases(suite: str, *, include_candidates: bool = False) -> list[EvalCase]:
    cases: list[EvalCase] = []
    suite_dir = CASES_DIR / suite
    if not suite_dir.is_dir():
        for root, _, files in os.walk(CASES_DIR):
            # 默认排除 candidates 目录，避免 needs_label 的 case 混入正式 eval
            if not include_candidates and "candidates" in Path(root).parts:
                continue
            for f in files:
                if f.endswith(".json"):
                    data = json.loads((Path(root) / f).read_text(encoding="utf-8"))
                    if data.get("suite") == suite:
                        cases.append(EvalCase(**data))
        return cases
    for f in sorted(suite_dir.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        cases.append(EvalCase(**data))
    return cases


def run_case(case: EvalCase) -> EvalResult:
    """执行单个 eval case，分发给对应 suite runner。"""
    suite = case.suite
    output = None

    if suite in ("sticker",):
        from evals.runners.sticker_runner import run_sticker_case
        output = run_sticker_case(case)
    elif suite in ("memory_learning",):
        from evals.runners.memory_runner import run_memory_case
        output = run_memory_case(case)
    elif suite in ("moderation",):
        from evals.runners.moderation_runner import run_moderation_case
        output = run_moderation_case(case)
    elif suite in ("model_routing",):
        from evals.runners.model_routing_runner import run_model_routing_case
        output = run_model_routing_case(case)
    elif suite in ("group_reply", "reply_contract"):
        from evals.runners.group_reply_runner import run_group_reply_case
        output = run_group_reply_case(case)
    elif suite in ("timing_gate",):
        from evals.runners.timing_gate_runner import run_timing_gate_case
        output = run_timing_gate_case(case)
    elif suite in ("error",):
        # error 候选来自日志抽样，只有 needs_label=True，不可运行
        return EvalResult(
            case_id=case.id, suite=case.suite, passed=False, score=0.0,
            errors=[f"error suite candidates are not runnable — needs manual labeling first"],
        )
    else:
        return EvalResult(
            case_id=case.id, suite=case.suite, passed=False, score=0.0,
            errors=[f"unknown suite: {suite}"],
        )

    score = score_case(case, output)
    return EvalResult(
        case_id=case.id, suite=case.suite, passed=score["passed"],
        score=score["score"], errors=score["errors"],
        output=output.model_dump(),
    )


def _build_report(suite: str, results: list[EvalResult]) -> SuiteReport:
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    return SuiteReport(
        suite=suite, total=len(results), passed=passed, failed=failed,
        pass_rate=passed / len(results) if results else 0.0,
        failed_cases=[{"case_id": r.case_id, "errors": r.errors} for r in results if not r.passed],
    )


def _attach_baseline_and_gate(
    report: SuiteReport,
    *,
    baseline_path: str | Path | None = None,
    min_pass_rate: float | None = None,
    max_new_failures: int | None = None,
) -> SuiteReport:
    baseline_diff = None
    if baseline_path is not None:
        baseline = load_baseline_report(baseline_path)
        baseline_diff = build_baseline_diff(
            report,
            baseline,
            baseline_path=str(baseline_path),
        )
        report.baseline_diff = baseline_diff

    if min_pass_rate is not None or max_new_failures is not None:
        report.gate = evaluate_gate(
            report,
            baseline_diff=baseline_diff,
            min_pass_rate=min_pass_rate,
            max_new_failures=max_new_failures,
        )
    return report


def run_suite(
    suite: str = "regression",
    *,
    include_candidates: bool = False,
    baseline_path: str | Path | None = None,
    min_pass_rate: float | None = None,
    max_new_failures: int | None = None,
) -> SuiteReport:
    cases = load_cases(suite, include_candidates=include_candidates)
    if not cases:
        return _attach_baseline_and_gate(
            SuiteReport(suite=suite, total=0, passed=0, failed=0, pass_rate=0.0),
            baseline_path=baseline_path,
            min_pass_rate=min_pass_rate,
            max_new_failures=max_new_failures,
        )
    results = [run_case(c) for c in cases]
    report = _build_report(suite, results)
    report = _attach_baseline_and_gate(
        report,
        baseline_path=baseline_path,
        min_pass_rate=min_pass_rate,
        max_new_failures=max_new_failures,
    )
    # 写入 reports/
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d")
    latest = REPORTS_DIR / "latest.json"
    dated = REPORTS_DIR / f"{ts}-{suite}.json"
    payload = report.model_dump(exclude_none=True)
    latest.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    dated.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def run_suite_with_details(
    suite: str = "regression", *, include_candidates: bool = False
) -> tuple[SuiteReport, list[dict]]:
    """同 run_suite，但同时返回每个 case 的详细结果（避免二次跑 eval）。"""
    cases = load_cases(suite, include_candidates=include_candidates)
    if not cases:
        return SuiteReport(suite=suite, total=0, passed=0, failed=0, pass_rate=0.0), []
    results = [run_case(c) for c in cases]
    report = _build_report(suite, results)
    case_results = [
        {
            "case_id": r.case_id, "suite": r.suite, "passed": r.passed,
            "score": r.score, "errors": r.errors, "output": r.output,
        }
        for r in results
    ]
    return report, case_results


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", default="regression")
    parser.add_argument("--include-candidates", action="store_true",
                        help="Include candidates/ directory in case search")
    parser.add_argument("--baseline", default=None,
                        help="Baseline report JSON path for diff")
    parser.add_argument("--min-pass-rate", type=float, default=None,
                        help="Fail gate if current pass rate is below this value")
    parser.add_argument("--max-new-failures", type=int, default=None,
                        help="Fail gate if new failed cases exceed this value")
    args = parser.parse_args(argv)
    report = run_suite(
        args.suite,
        include_candidates=args.include_candidates,
        baseline_path=args.baseline,
        min_pass_rate=args.min_pass_rate,
        max_new_failures=args.max_new_failures,
    )
    print(f"Suite: {report.suite}  total={report.total}  passed={report.passed}  failed={report.failed}  pass_rate={report.pass_rate:.1%}")
    if report.baseline_diff:
        diff = report.baseline_diff
        print(f"Baseline: {diff.get('baseline_path')}")
        print(
            "Diff: "
            f"new_failed={len(diff.get('new_failed_cases') or [])} "
            f"fixed={len(diff.get('fixed_cases') or [])} "
            f"still_failed={len(diff.get('still_failed_cases') or [])}"
        )
    if report.failed_cases:
        print("Failed:")
        for fc in report.failed_cases:
            print(f"  {fc['case_id']}: {fc['errors']}")
    if report.gate:
        if report.gate.get("passed"):
            print("Gate passed")
            return 0
        print("Gate failed:")
        for error in report.gate.get("errors") or []:
            print(f"  {error}")
        return 1
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

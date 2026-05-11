"""Eval runner——CLI 入口。 python -m evals.run --suite regression"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

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


def run_suite(suite: str = "regression", *, include_candidates: bool = False) -> SuiteReport:
    cases = load_cases(suite, include_candidates=include_candidates)
    if not cases:
        return SuiteReport(suite=suite, total=0, passed=0, failed=0, pass_rate=0.0)
    results = [run_case(c) for c in cases]
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    report = SuiteReport(
        suite=suite, total=len(results), passed=passed, failed=failed,
        pass_rate=passed / len(results) if results else 0.0,
        failed_cases=[{"case_id": r.case_id, "errors": r.errors} for r in results if not r.passed],
    )
    # 写入 reports/
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d")
    latest = REPORTS_DIR / "latest.json"
    dated = REPORTS_DIR / f"{ts}-{suite}.json"
    payload = report.model_dump()
    latest.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    dated.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", default="regression")
    parser.add_argument("--include-candidates", action="store_true",
                        help="Include candidates/ directory in case search")
    args = parser.parse_args()
    report = run_suite(args.suite, include_candidates=args.include_candidates)
    print(f"Suite: {report.suite}  total={report.total}  passed={report.passed}  failed={report.failed}  pass_rate={report.pass_rate:.1%}")
    if report.failed_cases:
        print("Failed:")
        for fc in report.failed_cases:
            print(f"  {fc['case_id']}: {fc['errors']}")
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

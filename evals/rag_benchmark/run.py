"""RAG benchmark runner CLI。"""

from __future__ import annotations

import argparse
from pathlib import Path

from evals.rag_benchmark.adapters import run_case_with_adapter
from evals.rag_benchmark.baseline import (
    build_rag_baseline_diff,
    evaluate_rag_gate,
    load_rag_baseline,
)
from evals.rag_benchmark.cases import load_cases
from evals.rag_benchmark.report import build_rag_report_payload, write_reports
from evals.rag_benchmark.sample import _readonly_session
from evals.rag_benchmark.schema import BenchmarkCase, BenchmarkResult, CaseScore
from evals.rag_benchmark.scoring import score_case


def run_benchmark(
    db_path: str | Path,
    cases: list[BenchmarkCase],
    *,
    use_runtime_providers: bool = True,
    provider_mode: str | None = None,
) -> tuple[list[BenchmarkResult], list[CaseScore]]:
    db = _readonly_session(db_path)
    try:
        results = [
            run_case_with_adapter(
                db,
                case,
                use_runtime_providers=use_runtime_providers if provider_mode is None else None,
                provider_mode=provider_mode,
                readonly=True,
            )
            for case in cases
        ]
        scores = [score_case(case, result) for case, result in zip(cases, results)]
        return results, scores
    finally:
        db.close()


def _effective_provider_mode(args: argparse.Namespace) -> str:
    if args.no_runtime_providers:
        return "no_reranker_baseline"
    return str(args.provider_mode)


def _case_scope(args: argparse.Namespace) -> str:
    return "manual" if args.manual_only else "manual+generated"


def _generated_dir(args: argparse.Namespace) -> Path:
    if args.manual_only:
        return Path(args.generated) / "__manual_only_disabled__"
    return Path(args.generated)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/nanobot.db")
    parser.add_argument("--manual", default="evals/cases/rag_benchmark/manual")
    parser.add_argument("--generated", default="tmp/rag_benchmark/generated")
    parser.add_argument("--report-out", default="tmp/rag_benchmark/reports")
    parser.add_argument("--no-runtime-providers", action="store_true")
    parser.add_argument(
        "--provider-mode",
        choices=["deterministic", "no_reranker_baseline", "runtime"],
        default="deterministic",
    )
    parser.add_argument("--manual-only", action="store_true")
    parser.add_argument("--baseline", default=None)
    parser.add_argument("--min-pass-rate", type=float, default=None)
    parser.add_argument("--min-hit-at-5", type=float, default=None)
    parser.add_argument("--min-mrr", type=float, default=None)
    parser.add_argument("--max-new-failures", type=int, default=None)
    parser.add_argument("--max-degraded-rate", type=float, default=None)
    parser.add_argument("--max-unexpected-source-rate", type=float, default=None)
    args = parser.parse_args(argv)

    provider_mode = _effective_provider_mode(args)
    case_scope = _case_scope(args)
    cases = load_cases(manual_dir=args.manual, generated_dir=_generated_dir(args))
    results, scores = run_benchmark(
        args.db,
        cases,
        use_runtime_providers=not args.no_runtime_providers,
        provider_mode=provider_mode,
    )
    report_payload = build_rag_report_payload(
        cases,
        results,
        scores,
        provider_mode=provider_mode,
        case_scope=case_scope,
    )
    baseline_diff = None
    if args.baseline:
        baseline_diff = build_rag_baseline_diff(
            report_payload,
            load_rag_baseline(args.baseline),
            baseline_path=str(args.baseline),
        )
    gate = evaluate_rag_gate(
        report_payload,
        baseline_diff=baseline_diff,
        min_pass_rate=args.min_pass_rate,
        min_hit_at_5=args.min_hit_at_5,
        min_mrr=args.min_mrr,
        max_new_failures=args.max_new_failures,
        max_degraded_rate=args.max_degraded_rate,
        max_unexpected_source_rate=args.max_unexpected_source_rate,
    )
    paths = write_reports(
        cases,
        results,
        scores,
        report_out=args.report_out,
        provider_mode=provider_mode,
        case_scope=case_scope,
        baseline_diff=baseline_diff,
        gate=gate,
    )
    passed = sum(1 for score in scores if score.ok)
    print(f"cases={len(cases)} passed={passed} failed={len(scores) - passed}")
    if gate["passed"]:
        print("Gate passed")
    else:
        print("Gate failed:")
        for error in gate["errors"]:
            print(f"- {error}")
    print(f"json={paths['json']}")
    print(f"markdown={paths['markdown']}")
    return 0 if passed == len(scores) and gate["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

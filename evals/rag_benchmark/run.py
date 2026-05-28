"""RAG benchmark runner CLI。"""

from __future__ import annotations

import argparse
from pathlib import Path

from evals.rag_benchmark.adapters import run_case_with_adapter
from evals.rag_benchmark.cases import load_cases
from evals.rag_benchmark.report import write_reports
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


def main() -> int:
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
    args = parser.parse_args()

    cases = load_cases(manual_dir=args.manual, generated_dir=args.generated)
    results, scores = run_benchmark(
        args.db,
        cases,
        use_runtime_providers=not args.no_runtime_providers,
        provider_mode="no_reranker_baseline" if args.no_runtime_providers else args.provider_mode,
    )
    paths = write_reports(cases, results, scores, report_out=args.report_out)
    passed = sum(1 for score in scores if score.ok)
    print(f"cases={len(cases)} passed={passed} failed={len(scores) - passed}")
    print(f"json={paths['json']}")
    print(f"markdown={paths['markdown']}")
    return 0 if passed == len(scores) else 1


if __name__ == "__main__":
    raise SystemExit(main())

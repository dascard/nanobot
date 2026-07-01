"""RAG benchmark 报告输出。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from evals.rag_benchmark.schema import BenchmarkCase, BenchmarkResult, CaseScore
from evals.rag_benchmark.scoring import aggregate_scores


def _dump_model(value):
    return value.model_dump() if hasattr(value, "model_dump") else value.dict()


def _markdown(
    metrics: dict,
    scores: list[CaseScore],
    *,
    gate: dict | None = None,
    baseline_diff: dict | None = None,
) -> str:
    lines = ["# RAG Benchmark Report", ""]
    overall = metrics.get("overall", {})
    lines.extend([
        f"- total_cases: {overall.get('total_cases', 0)}",
        f"- pass_rate: {overall.get('pass_rate', 0):.2%}",
        f"- hit@5: {overall.get('hit@5', 0):.2%}",
        f"- mrr: {overall.get('mrr', 0):.3f}",
        "",
        "## Failed Cases",
        "",
    ])
    failed = [score for score in scores if not score.ok]
    if not failed:
        lines.append("无")
    else:
        for score in failed:
            lines.append(f"- `{score.case_id}`: {'; '.join(score.errors)}")
    if gate is not None:
        lines.extend([
            "",
            "## Gate",
            "",
            f"- passed: {bool(gate.get('passed'))}",
        ])
        for error in gate.get("errors") or []:
            lines.append(f"- error: {error}")
    if baseline_diff is not None:
        lines.extend([
            "",
            "## Baseline Diff",
            "",
            f"- baseline_path: {baseline_diff.get('baseline_path', '')}",
            f"- total_delta: {baseline_diff.get('total_delta', 0)}",
            f"- new_failed_cases: {len(baseline_diff.get('new_failed_cases') or [])}",
            f"- fixed_cases: {len(baseline_diff.get('fixed_cases') or [])}",
        ])
    return "\n".join(lines) + "\n"


def build_rag_report_payload(
    cases: list[BenchmarkCase],
    results: list[BenchmarkResult],
    scores: list[CaseScore],
    *,
    provider_mode: str = "",
    case_scope: str = "",
    baseline_diff: dict | None = None,
    gate: dict | None = None,
) -> dict:
    metrics = aggregate_scores(cases, scores)
    case_scores = [_dump_model(score) for score in scores]
    return {
        "suite": "rag_benchmark",
        "provider_mode": provider_mode,
        "case_scope": case_scope,
        "metrics": metrics,
        "failed_cases": [
            {"case_id": score.case_id, "errors": score.errors}
            for score in scores
            if not score.ok
        ],
        "case_scores": case_scores,
        "cases": [_dump_model(case) for case in cases],
        "results": [_dump_model(result) for result in results],
        "scores": case_scores,
        "baseline_diff": baseline_diff,
        "gate": gate,
    }


def write_reports(
    cases: list[BenchmarkCase],
    results: list[BenchmarkResult],
    scores: list[CaseScore],
    *,
    report_out: str | Path = "tmp/rag_benchmark/reports",
    report_id: str | None = None,
    provider_mode: str = "",
    case_scope: str = "",
    baseline_diff: dict | None = None,
    gate: dict | None = None,
) -> dict[str, Path | str]:
    root = Path(report_out)
    root.mkdir(parents=True, exist_ok=True)
    report_id = report_id or datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:8]
    payload = build_rag_report_payload(
        cases,
        results,
        scores,
        provider_mode=provider_mode,
        case_scope=case_scope,
        baseline_diff=baseline_diff,
        gate=gate,
    )
    json_path = root / "latest.json"
    md_path = root / "latest.md"
    run_json_path = root / f"{report_id}.json"
    run_md_path = root / f"{report_id}.md"
    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    markdown_text = _markdown(
        payload["metrics"],
        scores,
        gate=gate,
        baseline_diff=baseline_diff,
    )
    run_json_path.write_text(json_text, encoding="utf-8")
    run_md_path.write_text(markdown_text, encoding="utf-8")
    tmp_json = root / f".latest.{report_id}.json.tmp"
    tmp_md = root / f".latest.{report_id}.md.tmp"
    tmp_json.write_text(json_text, encoding="utf-8")
    tmp_md.write_text(markdown_text, encoding="utf-8")
    os.replace(tmp_json, json_path)
    os.replace(tmp_md, md_path)
    return {
        "report_id": report_id,
        "json": json_path,
        "markdown": md_path,
        "run_json": run_json_path,
        "run_markdown": run_md_path,
    }

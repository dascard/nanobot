"""RAG benchmark 报告输出。"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from evals.rag_benchmark.schema import BenchmarkCase, BenchmarkResult, CaseScore
from evals.rag_benchmark.scoring import aggregate_scores


def _dump_model(value):
    return value.model_dump() if hasattr(value, "model_dump") else value.dict()


def _markdown(metrics: dict, scores: list[CaseScore]) -> str:
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
    return "\n".join(lines) + "\n"


def write_reports(
    cases: list[BenchmarkCase],
    results: list[BenchmarkResult],
    scores: list[CaseScore],
    *,
    report_out: str | Path = "tmp/rag_benchmark/reports",
    report_id: str | None = None,
) -> dict[str, Path | str]:
    root = Path(report_out)
    root.mkdir(parents=True, exist_ok=True)
    report_id = report_id or datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:8]
    metrics = aggregate_scores(cases, scores)
    payload = {
        "metrics": metrics,
        "cases": [_dump_model(case) for case in cases],
        "results": [_dump_model(result) for result in results],
        "scores": [_dump_model(score) for score in scores],
    }
    json_path = root / "latest.json"
    md_path = root / "latest.md"
    run_json_path = root / f"{report_id}.json"
    run_md_path = root / f"{report_id}.md"
    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    markdown_text = _markdown(metrics, scores)
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

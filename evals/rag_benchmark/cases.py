"""RAG benchmark case 加载器。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from evals.rag_benchmark.schema import BenchmarkCase


def _iter_case_files(path: Path) -> Iterable[Path]:
    if not path.exists():
        return []
    return sorted(
        item
        for item in path.rglob("*")
        if item.is_file() and item.suffix.lower() in {".json", ".jsonl"}
    )


def _load_json_file(path: Path) -> list[BenchmarkCase]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [BenchmarkCase(**item) for item in data]
    return [BenchmarkCase(**data)]


def _load_jsonl_file(path: Path) -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        cases.append(BenchmarkCase(**json.loads(text)))
    return cases


def load_cases(
    *,
    manual_dir: str | Path = "evals/cases/rag_benchmark/manual",
    generated_dir: str | Path = "tmp/rag_benchmark/generated",
) -> list[BenchmarkCase]:
    """按 manual -> generated 的顺序加载 case。"""

    cases: list[BenchmarkCase] = []
    for root in (Path(manual_dir), Path(generated_dir)):
        for path in _iter_case_files(root):
            if path.suffix.lower() == ".jsonl":
                cases.extend(_load_jsonl_file(path))
            else:
                cases.extend(_load_json_file(path))
    return cases

# P4-4 RAG baseline 门禁实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为现有 `evals.rag_benchmark` 专用评测体系增加稳定 baseline、命令行 gate、Admin 展示和文档收口。

**架构：** 保留 `BenchmarkCase` / `BenchmarkResult` / `CaseScore` 模型，不并入通用 `EvalCase`。新增 `evals/rag_benchmark/baseline.py` 负责 baseline report、diff 和 gate 纯函数；`evals/rag_benchmark/run.py` 负责 CLI 参数和退出码；Admin route 与 WebUI 只消费 gate 结果。

**技术栈：** Python、Pydantic、pytest、FastAPI、React、SQLite readonly URI。

---

## 文件结构

- 创建：`evals/rag_benchmark/baseline.py`
  - 职责：构建 baseline report、加载 baseline 文件、计算 diff、评估 gate。
- 修改：`evals/rag_benchmark/run.py`
  - 职责：新增 gate CLI 参数，运行后附加 baseline diff / gate，按 gate 返回退出码。
- 修改：`evals/rag_benchmark/report.py`
  - 职责：报告 payload 和 Markdown 输出包含 baseline diff / gate。
- 创建：`evals/baselines/rag_benchmark.json`
  - 职责：仓库内 RAG manual deterministic 稳定 baseline。
- 修改：`api/admin/rag_benchmark_routes.py`
  - 职责：Admin benchmark run 可接收 baseline gate 参数，响应包含 gate 结果。
- 修改：`webui/src/features/rag/RagBenchmarkPage.jsx`
  - 职责：展示 RAG benchmark gate 状态、baseline diff 和失败 case。
- 修改：`tests/test_rag_benchmark.py`
  - 职责：覆盖 baseline 纯函数、CLI gate、报告输出。
- 修改：`tests/test_rag_benchmark_admin.py`
  - 职责：覆盖 Admin gate 参数与响应结构。
- 修改：`tests/test_rag_benchmark_webui.py`
  - 职责：覆盖 WebUI gate 文案和字段引用。
- 修改：`docs/evals.md`
  - 职责：记录 RAG gate 命令、baseline 更新规则、generated case 边界。
- 修改：`docs/todo.md`
  - 职责：同步 P4-4 阶段状态。
- 修改：`docs/plan_walkthrough.md`
  - 职责：同步详细计划、验证记录和提交边界。

## 任务 1：baseline 纯函数

**文件：**
- 创建：`evals/rag_benchmark/baseline.py`
- 修改：`tests/test_rag_benchmark.py`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_rag_benchmark.py` 末尾追加：

```python
def test_rag_baseline_diff_reports_new_fixed_and_metric_deltas():
    from evals.rag_benchmark.baseline import (
        build_rag_baseline_diff,
        evaluate_rag_gate,
    )

    baseline = {
        "suite": "rag_benchmark",
        "provider_mode": "deterministic",
        "case_scope": "manual",
        "metrics": {
            "overall": {
                "total_cases": 2,
                "passed_cases": 1,
                "pass_rate": 0.5,
                "hit@5": 0.25,
                "mrr": 0.1,
                "degraded_rate": 0.0,
                "case_false_positive_rate": 0.0,
                "unexpected_source_rate": 0.0,
            }
        },
        "case_scores": [
            {"case_id": "fixed_case", "ok": False, "errors": ["old"]},
            {"case_id": "stable_case", "ok": True, "errors": []},
        ],
    }
    current = {
        "suite": "rag_benchmark",
        "provider_mode": "deterministic",
        "case_scope": "manual",
        "metrics": {
            "overall": {
                "total_cases": 3,
                "passed_cases": 2,
                "pass_rate": 2 / 3,
                "hit@5": 0.5,
                "mrr": 0.3,
                "degraded_rate": 0.0,
                "case_false_positive_rate": 0.0,
                "unexpected_source_rate": 0.0,
            }
        },
        "case_scores": [
            {"case_id": "fixed_case", "ok": True, "errors": []},
            {"case_id": "stable_case", "ok": True, "errors": []},
            {"case_id": "new_failed_case", "ok": False, "errors": ["new"]},
        ],
    }

    diff = build_rag_baseline_diff(current, baseline, baseline_path="baseline.json")

    assert diff["baseline_path"] == "baseline.json"
    assert diff["total_delta"] == 1
    assert diff["new_failed_cases"] == ["new_failed_case"]
    assert diff["fixed_cases"] == ["fixed_case"]
    assert diff["still_failed_cases"] == []
    assert diff["metric_deltas"]["overall.hit@5"] == 0.25

    gate = evaluate_rag_gate(
        current,
        baseline_diff=diff,
        min_pass_rate=1.0,
        max_new_failures=0,
        max_degraded_rate=0.0,
    )

    assert gate["passed"] is False
    assert "pass_rate below threshold" in gate["errors"]
    assert "new_failed_cases exceeds threshold" in gate["errors"]
```

- [ ] **步骤 2：运行红灯测试**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py::test_rag_baseline_diff_reports_new_fixed_and_metric_deltas -v -p no:cacheprovider
```

预期：FAIL，报错包含 `ModuleNotFoundError: No module named 'evals.rag_benchmark.baseline'`。

- [ ] **步骤 3：实现 baseline 纯函数**

创建 `evals/rag_benchmark/baseline.py`：

```python
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
            result.add(str(item.get("case_id") or ""))
    return {item for item in result if item}


def _metric(report: dict[str, Any], dotted_key: str) -> float:
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
        key: round(_metric(current, key) - _metric(baseline, key), 12)
        for key in DEFAULT_METRIC_KEYS
    }
    return {
        "baseline_path": baseline_path,
        "baseline_suite": str(baseline.get("suite") or ""),
        "baseline_provider_mode": str(baseline.get("provider_mode") or ""),
        "baseline_case_scope": str(baseline.get("case_scope") or ""),
        "total_delta": int(_metric(current, "overall.total_cases") - _metric(baseline, "overall.total_cases")),
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
    if _metric(report, "overall.total_cases") <= 0:
        errors.append("no_cases_executed")
    if min_pass_rate is not None and _metric(report, "overall.pass_rate") < min_pass_rate:
        errors.append("pass_rate below threshold")
    if min_hit_at_5 is not None and _metric(report, "overall.hit@5") < min_hit_at_5:
        errors.append("hit@5 below threshold")
    if min_mrr is not None and _metric(report, "overall.mrr") < min_mrr:
        errors.append("mrr below threshold")
    if max_degraded_rate is not None and _metric(report, "overall.degraded_rate") > max_degraded_rate:
        errors.append("degraded_rate above threshold")
    if (
        max_unexpected_source_rate is not None
        and _metric(report, "overall.unexpected_source_rate") > max_unexpected_source_rate
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
                f"baseline suite mismatch: current=rag_benchmark baseline={baseline_diff.get('baseline_suite')}"
            )
        baseline_provider = baseline_diff.get("baseline_provider_mode")
        if baseline_provider and baseline_provider != str(report.get("provider_mode") or ""):
            errors.append("baseline provider_mode mismatch")
        baseline_scope = baseline_diff.get("baseline_case_scope")
        if baseline_scope and baseline_scope != str(report.get("case_scope") or ""):
            errors.append("baseline case_scope mismatch")
    return {"passed": not errors, "errors": errors}
```

- [ ] **步骤 4：运行绿灯测试**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py::test_rag_baseline_diff_reports_new_fixed_and_metric_deltas -v -p no:cacheprovider
```

预期：PASS，`1 passed`。

- [ ] **步骤 5：提交任务 1**

运行：

```bash
git add evals/rag_benchmark/baseline.py tests/test_rag_benchmark.py
git commit -m "feat(评测): 增加 RAG baseline 计算"
```

## 任务 2：CLI gate 与报告输出

**文件：**
- 修改：`evals/rag_benchmark/run.py`
- 修改：`evals/rag_benchmark/report.py`
- 修改：`tests/test_rag_benchmark.py`

- [ ] **步骤 1：编写 CLI 红灯测试**

在 `tests/test_rag_benchmark.py` 追加：

```python
def test_rag_benchmark_cli_fails_gate_on_new_failure(tmp_path, monkeypatch, capsys):
    from evals.rag_benchmark import run as rag_run

    manual = tmp_path / "manual"
    generated = tmp_path / "generated"
    reports = tmp_path / "reports"
    manual.mkdir()
    generated.mkdir()
    db_path = tmp_path / "rag.db"
    db = _session_for(db_path)
    db.close()
    (manual / "empty_not_allowed.json").write_text(json.dumps({
        "id": "empty_not_allowed",
        "suite": "rag_benchmark",
        "source_type": "sticker",
        "case_type": "positive",
        "query": "no result",
        "expected": {"candidate_ids": ["sticker:missing:sticker"], "hit_at": 5},
        "meta": {"origin": "manual_hard"},
    }, ensure_ascii=False), encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({
        "suite": "rag_benchmark",
        "provider_mode": "deterministic",
        "case_scope": "manual",
        "metrics": {"overall": {"total_cases": 0, "passed_cases": 0, "pass_rate": 0.0}},
        "case_scores": [],
    }, ensure_ascii=False), encoding="utf-8")

    exit_code = rag_run.main([
        "--db", str(db_path),
        "--manual", str(manual),
        "--generated", str(generated),
        "--report-out", str(reports),
        "--provider-mode", "deterministic",
        "--manual-only",
        "--baseline", str(baseline),
        "--min-pass-rate", "1.0",
        "--max-new-failures", "0",
    ])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Gate failed" in captured.out
    assert "new_failed_cases exceeds threshold" in captured.out
```

- [ ] **步骤 2：运行 CLI 红灯测试**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_cli_fails_gate_on_new_failure -v -p no:cacheprovider
```

预期：FAIL，报错包含 `main() takes 0 positional arguments` 或 `unrecognized arguments: --baseline`。

- [ ] **步骤 3：修改 `run.py` 支持 argv 和 gate 参数**

在 `evals/rag_benchmark/run.py` 中：

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    ...
    parser.add_argument("--manual-only", action="store_true")
    parser.add_argument("--baseline", default=None)
    parser.add_argument("--min-pass-rate", type=float, default=None)
    parser.add_argument("--min-hit-at-5", type=float, default=None)
    parser.add_argument("--min-mrr", type=float, default=None)
    parser.add_argument("--max-new-failures", type=int, default=None)
    parser.add_argument("--max-degraded-rate", type=float, default=None)
    parser.add_argument("--max-unexpected-source-rate", type=float, default=None)
    args = parser.parse_args(argv)
```

新增 helper：

```python
def _case_scope(args) -> str:
    return "manual" if args.manual_only else "manual+generated"
```

运行时如果 `args.manual_only` 为真，把 `generated_dir` 指向不存在目录或空目录：

```python
generated_dir = Path("__missing_generated__") if args.manual_only else Path(args.generated)
cases = load_cases(manual_dir=args.manual, generated_dir=generated_dir)
```

构建 report dict：

```python
from evals.rag_benchmark.baseline import (
    build_rag_baseline_diff,
    evaluate_rag_gate,
    load_rag_baseline,
)
from evals.rag_benchmark.scoring import aggregate_scores

metrics = aggregate_scores(cases, scores)
report_payload = build_rag_report_payload(
    cases,
    results,
    scores,
    provider_mode=args.provider_mode,
    case_scope=_case_scope(args),
)
```

`build_rag_report_payload()` 可以放在 `report.py`，也可以先放在 `run.py`，但后续报告输出必须复用同一结构。

- [ ] **步骤 4：修改 `report.py` 支持 gate 字段**

在 `evals/rag_benchmark/report.py` 中让 `write_reports()` 接收可选参数：

```python
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
```

payload 增加：

```python
"suite": "rag_benchmark",
"provider_mode": provider_mode,
"case_scope": case_scope,
"failed_cases": [
    {"case_id": score.case_id, "errors": score.errors}
    for score in scores
    if not score.ok
],
"case_scores": [_dump_model(score) for score in scores],
"baseline_diff": baseline_diff,
"gate": gate,
```

Markdown 增加 gate 摘要：

```python
if gate:
    lines.extend([
        "",
        "## Gate",
        "",
        f"- passed: {bool(gate.get('passed'))}",
    ])
    for error in gate.get("errors") or []:
        lines.append(f"- error: {error}")
```

- [ ] **步骤 5：运行 CLI 绿灯测试**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_cli_fails_gate_on_new_failure -v -p no:cacheprovider
```

预期：PASS，`1 passed`。

- [ ] **步骤 6：补通过路径测试**

在 `tests/test_rag_benchmark.py` 追加：

```python
def test_rag_benchmark_cli_passes_manual_deterministic_gate(tmp_path, capsys):
    from evals.rag_benchmark import run as rag_run

    manual = tmp_path / "manual"
    generated = tmp_path / "generated"
    reports = tmp_path / "reports"
    manual.mkdir()
    generated.mkdir()
    db_path = tmp_path / "rag.db"
    db = _session_for(db_path)
    db.close()
    (manual / "constraint.json").write_text(json.dumps({
        "id": "constraint",
        "suite": "rag_benchmark",
        "source_type": "sticker",
        "case_type": "constraint_only",
        "query": "表情包",
        "expected": {"candidate_ids": [], "allow_empty": True, "max_reranker_candidates": 10},
        "meta": {"origin": "manual_hard"},
    }, ensure_ascii=False), encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({
        "suite": "rag_benchmark",
        "provider_mode": "deterministic",
        "case_scope": "manual",
        "metrics": {
            "overall": {
                "total_cases": 1,
                "passed_cases": 1,
                "pass_rate": 1.0,
                "hit@5": 0.0,
                "mrr": 0.0,
                "degraded_rate": 0.0,
                "case_false_positive_rate": 0.0,
                "unexpected_source_rate": 0.0,
            }
        },
        "case_scores": [{"case_id": "constraint", "ok": True, "errors": []}],
    }, ensure_ascii=False), encoding="utf-8")

    exit_code = rag_run.main([
        "--db", str(db_path),
        "--manual", str(manual),
        "--generated", str(generated),
        "--report-out", str(reports),
        "--provider-mode", "deterministic",
        "--manual-only",
        "--baseline", str(baseline),
        "--min-pass-rate", "1.0",
        "--max-new-failures", "0",
        "--max-degraded-rate", "0.0",
        "--max-unexpected-source-rate", "0.0",
    ])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Gate passed" in captured.out
    report = json.loads((reports / "latest.json").read_text(encoding="utf-8"))
    assert report["gate"]["passed"] is True
    assert report["baseline_diff"]["new_failed_cases"] == []
```

- [ ] **步骤 7：运行任务 2 全部测试**

运行：

```bash
python -B -m pytest \
tests/test_rag_benchmark.py::test_rag_baseline_diff_reports_new_fixed_and_metric_deltas \
tests/test_rag_benchmark.py::test_rag_benchmark_cli_fails_gate_on_new_failure \
tests/test_rag_benchmark.py::test_rag_benchmark_cli_passes_manual_deterministic_gate \
-v -p no:cacheprovider
```

预期：PASS，`3 passed`。

- [ ] **步骤 8：提交任务 2**

运行：

```bash
git add evals/rag_benchmark/run.py evals/rag_benchmark/report.py tests/test_rag_benchmark.py
git commit -m "feat(评测): 支持 RAG baseline 门禁"
```

## 任务 3：稳定 baseline 文件

**文件：**
- 创建：`evals/baselines/rag_benchmark.json`
- 修改：`tests/test_rag_benchmark.py`

- [ ] **步骤 1：编写 baseline 文件结构测试**

在 `tests/test_rag_benchmark.py` 追加：

```python
def test_rag_benchmark_baseline_file_matches_manual_gate_contract():
    from evals.rag_benchmark.baseline import load_rag_baseline

    baseline = load_rag_baseline("evals/baselines/rag_benchmark.json")

    assert baseline["suite"] == "rag_benchmark"
    assert baseline["provider_mode"] == "deterministic"
    assert baseline["case_scope"] == "manual"
    assert baseline["metrics"]["overall"]["total_cases"] >= 1
    assert "case_scores" in baseline
    assert all("case_id" in item and "ok" in item for item in baseline["case_scores"])
```

- [ ] **步骤 2：运行红灯测试**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract -v -p no:cacheprovider
```

预期：FAIL，报错包含 `No such file or directory: 'evals/baselines/rag_benchmark.json'`。

- [ ] **步骤 3：创建 baseline 文件**

创建 `evals/baselines/rag_benchmark.json`，首版使用仓库内 manual safe cases。若本地 DB 不可用，先用当前 manual constraint-only case 的确定性结果构造 baseline：

```json
{
  "suite": "rag_benchmark",
  "provider_mode": "deterministic",
  "case_scope": "manual",
  "metrics": {
    "overall": {
      "total_cases": 3,
      "positive_cases": 0,
      "passed_cases": 3,
      "pass_rate": 1.0,
      "hit@1": 0.0,
      "hit@3": 0.0,
      "hit@5": 0.0,
      "mrr": 0.0,
      "case_false_positive_rate": 0.0,
      "forbidden_hit_rate@5": 0.0,
      "unexpected_source_rate": 0.0,
      "degraded_rate": 0.0,
      "avg_latency_ms": 0.0,
      "p95_latency_ms": 0,
      "avg_reranker_candidates": 0.0,
      "max_reranker_candidates": 0
    }
  },
  "failed_cases": [],
  "case_scores": [
    {"case_id": "group_memory_manual_filter_constraint_001", "source_type": "group_memory", "case_type": "constraint_only", "ok": true, "errors": []},
    {"case_id": "knowledge_manual_citation_constraint_001", "source_type": "knowledge", "case_type": "constraint_only", "ok": true, "errors": []},
    {"case_id": "sticker_manual_generic_constraint_001", "source_type": "sticker", "case_type": "constraint_only", "ok": true, "errors": []}
  ]
}
```

实现后用实际 gate 结果校准 metrics。若 gate 运行产生非 0 latency，baseline 可以保留 latency 字段，但 gate 阈值不依赖精确 latency。

- [ ] **步骤 4：运行 baseline 文件测试**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract -v -p no:cacheprovider
```

预期：PASS，`1 passed`。

- [ ] **步骤 5：运行 RAG gate 命令**

运行：

```bash
python -B -m evals.rag_benchmark.run \
--manual evals/cases/rag_benchmark/manual \
--generated tmp/rag_benchmark/empty \
--provider-mode deterministic \
--manual-only \
--baseline evals/baselines/rag_benchmark.json \
--min-pass-rate 1.0 \
--max-new-failures 0 \
--max-degraded-rate 0.0 \
--max-unexpected-source-rate 0.0
```

预期：退出码 0，输出包含 `Gate passed`。

- [ ] **步骤 6：提交任务 3**

运行：

```bash
git add evals/baselines/rag_benchmark.json tests/test_rag_benchmark.py
git commit -m "test(评测): 固化 RAG baseline"
```

## 任务 4：Admin 和 WebUI 展示 gate 结果

**文件：**
- 修改：`api/admin/rag_benchmark_routes.py`
- 修改：`webui/src/features/rag/RagBenchmarkPage.jsx`
- 修改：`tests/test_rag_benchmark_admin.py`
- 修改：`tests/test_rag_benchmark_webui.py`

- [ ] **步骤 1：编写 Admin 红灯测试**

在 `tests/test_rag_benchmark_admin.py` 追加：

```python
def test_benchmark_run_returns_gate_when_baseline_requested(client, tmp_path, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    db_path = tmp_path / "benchmark.db"
    _engine, db = _file_db(db_path)
    _seed_memory_case(db)
    db.close()
    routes, manual, _generated, reports, _backups, _trash = _configure_paths(monkeypatch, tmp_path, db_path)
    baseline = tmp_path / "baseline.json"
    monkeypatch.setattr(routes, "BENCHMARK_BASELINE_PATH", baseline)
    (manual / "memory_case.json").write_text(json.dumps({
        "id": "memory_case",
        "suite": "rag_benchmark",
        "source_type": "memory",
        "case_type": "positive",
        "query": "RAG benchmark readonly",
        "expected": {"candidate_ids": ["memory_digest:42:digest:level2"], "hit_at": 5},
        "meta": {"origin": "manual"},
    }, ensure_ascii=False), encoding="utf-8")
    baseline.write_text(json.dumps({
        "suite": "rag_benchmark",
        "provider_mode": "deterministic",
        "case_scope": "manual",
        "metrics": {"overall": {"total_cases": 1, "passed_cases": 1, "pass_rate": 1.0}},
        "case_scores": [{"case_id": "memory_case", "ok": True, "errors": []}],
    }, ensure_ascii=False), encoding="utf-8")

    response = client.post(
        "/api/v1/admin/rag/benchmark/run",
        headers=_auth_header(),
        json={
            "provider_mode": "deterministic",
            "include_generated": False,
            "baseline_path": str(baseline),
            "min_pass_rate": 1.0,
            "max_new_failures": 0,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["gate"]["passed"] is True
    assert data["baseline_diff"]["new_failed_cases"] == []
    assert json.loads((reports / "latest.json").read_text(encoding="utf-8"))["gate"]["passed"] is True
```

- [ ] **步骤 2：运行 Admin 红灯测试**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark_admin.py::test_benchmark_run_returns_gate_when_baseline_requested -v -p no:cacheprovider
```

预期：FAIL，响应缺少 `gate` 或请求模型拒绝未知字段。

- [ ] **步骤 3：扩展 Admin 请求模型**

在 `api/admin/rag_benchmark_routes.py` 中新增常量：

```python
BENCHMARK_BASELINE_PATH = Path("evals/baselines/rag_benchmark.json")
```

扩展 `BenchmarkRunRequest`：

```python
baseline_path: str = ""
min_pass_rate: float | None = None
min_hit_at_5: float | None = None
min_mrr: float | None = None
max_new_failures: int | None = None
max_degraded_rate: float | None = None
max_unexpected_source_rate: float | None = None
```

在 `run_benchmark_web()` 中复用 `evals.rag_benchmark.baseline`：

```python
baseline_diff = None
gate = None
case_scope = "manual" if not body.include_generated else "manual+generated"
report_payload = build_rag_report_payload(
    selected[:len(scores)],
    results,
    scores,
    provider_mode=provider_mode,
    case_scope=case_scope,
)
baseline_path = Path(body.baseline_path) if body.baseline_path else BENCHMARK_BASELINE_PATH
if baseline_path.exists() and (
    body.min_pass_rate is not None
    or body.max_new_failures is not None
    or body.max_degraded_rate is not None
):
    baseline = load_rag_baseline(baseline_path)
    baseline_diff = build_rag_baseline_diff(
        report_payload,
        baseline,
        baseline_path=_safe_rel_path(baseline_path),
    )
    gate = evaluate_rag_gate(
        report_payload,
        baseline_diff=baseline_diff,
        min_pass_rate=body.min_pass_rate,
        min_hit_at_5=body.min_hit_at_5,
        min_mrr=body.min_mrr,
        max_new_failures=body.max_new_failures,
        max_degraded_rate=body.max_degraded_rate,
        max_unexpected_source_rate=body.max_unexpected_source_rate,
    )
```

返回 payload 增加：

```python
"baseline_diff": baseline_diff,
"gate": gate,
```

`write_reports()` 调用传入 `provider_mode`、`case_scope`、`baseline_diff` 和 `gate`。

- [ ] **步骤 4：运行 Admin 绿灯测试**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark_admin.py::test_benchmark_run_returns_gate_when_baseline_requested -v -p no:cacheprovider
```

预期：PASS，`1 passed`。

- [ ] **步骤 5：编写 WebUI 静态测试**

在 `tests/test_rag_benchmark_webui.py` 追加：

```python
def test_rag_benchmark_page_exposes_gate_status_and_baseline_diff():
    source = PAGE.read_text(encoding="utf-8")

    assert "baseline_path" in source
    assert "min_pass_rate" in source
    assert "max_new_failures" in source
    assert "baseline_diff" in source
    assert "gate" in source
    assert "Gate passed" in source
    assert "Gate failed" in source
    assert "new_failed_cases" in source
```

- [ ] **步骤 6：运行 WebUI 红灯测试**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark_webui.py::test_rag_benchmark_page_exposes_gate_status_and_baseline_diff -v -p no:cacheprovider
```

预期：FAIL，缺少 gate 相关字段或文案。

- [ ] **步骤 7：修改 WebUI 页面**

在 `webui/src/features/rag/RagBenchmarkPage.jsx` 中：

- run 请求 payload 增加 `baseline_path`、`min_pass_rate`、`max_new_failures`、`max_degraded_rate`、`max_unexpected_source_rate`。
- 页面展示 `latestRun.gate?.passed`。
- 页面展示 `latestRun.baseline_diff?.new_failed_cases`、`fixed_cases`、`still_failed_cases`。
- 门禁失败时在失败明细附近展示 `gate.errors`。

- [ ] **步骤 8：运行 Admin / WebUI 回归**

运行：

```bash
python -B -m pytest tests/test_rag_benchmark_admin.py tests/test_rag_benchmark_webui.py -v -p no:cacheprovider
```

预期：PASS。

- [ ] **步骤 9：提交任务 4**

运行：

```bash
git add api/admin/rag_benchmark_routes.py webui/src/features/rag/RagBenchmarkPage.jsx tests/test_rag_benchmark_admin.py tests/test_rag_benchmark_webui.py
git commit -m "feat(评测): 展示 RAG 门禁结果"
```

## 任务 5：文档收口

**文件：**
- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/rag-baseline-gate.md`

- [ ] **步骤 1：更新 `docs/evals.md`**

在 `RAG Benchmark 边界` 后追加 RAG gate 命令：

```markdown
RAG benchmark 稳定门禁只运行 manual case 和 deterministic provider：

```bash
python -B -m evals.rag_benchmark.run \
--manual evals/cases/rag_benchmark/manual \
--generated tmp/rag_benchmark/empty \
--provider-mode deterministic \
--manual-only \
--baseline evals/baselines/rag_benchmark.json \
--min-pass-rate 1.0 \
--max-new-failures 0 \
--max-degraded-rate 0.0 \
--max-unexpected-source-rate 0.0
```

Generated case 只作为本地 DB 采样候选，不进入仓库稳定 baseline。
人工确认后的样本应保存为 manual case，再纳入 gate。
```

- [ ] **步骤 2：更新 `docs/todo.md`**

把 P4 现状改为 P4-4 已完成，并说明下一阶段进入 P4-5 更多 suite PR gate 和周期性复跑。

- [ ] **步骤 3：更新 `docs/plan_walkthrough.md`**

在进度总览中把 P4-4 标记为已完成，新增 P4-5 下一步。记录 P4-4 设计、计划、P4-4A、P4-4B、P4-4C 的提交边界和验证命令。

- [ ] **步骤 4：勾选本计划已完成步骤**

在 `.Codex/plans/rag-baseline-gate.md` 中把已完成任务复选框改为 `[x]`，并记录实际测试输出。

- [ ] **步骤 5：运行文档自检**

运行：

```bash
rg -n "T[O]DO|待[定]|后续[实]现|类似[任]务|添加[适]当|为上[述]" \
.Codex/plans/rag-baseline-gate.md docs/evals.md docs/todo.md docs/plan_walkthrough.md
```

预期：无输出。

运行：

```bash
python - <<'PY'
from pathlib import Path
for path in [
    Path(".Codex/plans/rag-baseline-gate.md"),
    Path("docs/evals.md"),
    Path("docs/todo.md"),
    Path("docs/plan_walkthrough.md"),
]:
    data = path.read_text(encoding="utf-8")
    if "\ufffd" in data:
        raise SystemExit(f"U+FFFD found in {path}")
print("U+FFFD scan passed")
PY
```

预期：输出 `U+FFFD scan passed`。

运行：

```bash
git diff --check -- \
.Codex/plans/rag-baseline-gate.md \
docs/evals.md docs/todo.md docs/plan_walkthrough.md
```

预期：无输出。

- [ ] **步骤 6：提交任务 5**

运行：

```bash
git add .Codex/plans/rag-baseline-gate.md docs/evals.md docs/todo.md docs/plan_walkthrough.md
git commit -m "docs(评测): 收口 RAG 门禁状态"
```

## 总体验收

P4-4 完成后运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py tests/test_rag_benchmark_admin.py tests/test_rag_benchmark_webui.py -v -p no:cacheprovider
```

```bash
python -B -m evals.rag_benchmark.run \
--manual evals/cases/rag_benchmark/manual \
--generated tmp/rag_benchmark/empty \
--provider-mode deterministic \
--manual-only \
--baseline evals/baselines/rag_benchmark.json \
--min-pass-rate 1.0 \
--max-new-failures 0 \
--max-degraded-rate 0.0 \
--max-unexpected-source-rate 0.0
```

```bash
python -B -m pytest tests/ -v -p no:cacheprovider
```

全部命令退出码必须为 0，最后再做文档占位词扫描、U+FFFD 扫描和 `git diff --check`。

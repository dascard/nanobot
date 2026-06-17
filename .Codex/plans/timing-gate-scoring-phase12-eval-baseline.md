# TimingGate Eval 基线与回归门禁实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 或在当前会话中逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把 `timing_gate` eval 从手动看报告升级为可复跑的 baseline diff 与阈值门禁，支持提交前用固定基线检查 pass rate 和新增失败 case。

**架构：** 新增 `evals/baseline.py` 作为纯函数层，负责读取旧报告、计算新旧失败 case 差异、评估阈值门禁；扩展 `evals/schema.py` 给 `SuiteReport` 增加可选 `baseline_diff` 和 `gate` 字段；扩展 `evals/run.py` 的 `run_suite()` 与 CLI，把 baseline 信息写入 `evals/reports/latest.json` 和日期报告。现有 admin `/evals/run` 继续只消费既有字段，保持兼容。

**技术栈：** Python 3.13、pytest、pydantic v2、现有 `evals.run` / `evals.schema` / `evals.cases.timing_gate`。

---

## 文件结构

- 创建：`evals/baseline.py`
  - 纯函数模块。读取 baseline JSON，计算 diff，执行阈值门禁。
  - 不运行 eval，不写报告文件，便于单元测试。
- 修改：`evals/schema.py`
  - 给 `SuiteReport` 增加可选 `baseline_diff` 和 `gate` 字段。
  - 不改变既有必填字段，兼容旧报告和 admin 端。
- 修改：`evals/run.py`
  - `run_suite()` 增加 `baseline_path`、`min_pass_rate`、`max_new_failures` 参数。
  - CLI 增加 `--baseline`、`--min-pass-rate`、`--max-new-failures`。
  - 报告落盘时带上 baseline diff 和 gate 结果。
- 创建：`tests/test_eval_baseline.py`
  - 覆盖 diff 纯函数、gate 纯函数、`run_suite()` 报告落盘、CLI 退出码。
- 修改：`docs/plan_walkthrough.md`
  - 阶段 12 完成后标记为已完成，并把下一步改为阶段 13。

---

### 任务 1：新增 baseline diff 纯函数

**文件：**
- 创建：`evals/baseline.py`
- 测试：`tests/test_eval_baseline.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_eval_baseline.py` 中新增：

```python
from evals.schema import SuiteReport


def test_build_baseline_diff_reports_new_fixed_and_delta():
    from evals.baseline import build_baseline_diff

    baseline = SuiteReport(
        suite="timing_gate",
        total=4,
        passed=2,
        failed=2,
        pass_rate=0.5,
        failed_cases=[
            {"case_id": "still_bad", "errors": ["old"]},
            {"case_id": "fixed_case", "errors": ["old"]},
        ],
    )
    current = SuiteReport(
        suite="timing_gate",
        total=5,
        passed=3,
        failed=2,
        pass_rate=0.6,
        failed_cases=[
            {"case_id": "still_bad", "errors": ["new"]},
            {"case_id": "new_bad", "errors": ["new"]},
        ],
    )

    diff = build_baseline_diff(current, baseline, baseline_path="baseline.json")

    assert diff["baseline_path"] == "baseline.json"
    assert diff["suite"] == "timing_gate"
    assert diff["total_delta"] == 1
    assert diff["passed_delta"] == 1
    assert diff["failed_delta"] == 0
    assert diff["pass_rate_delta"] == 0.1
    assert diff["new_failed_cases"] == ["new_bad"]
    assert diff["fixed_cases"] == ["fixed_case"]
    assert diff["still_failed_cases"] == ["still_bad"]
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_eval_baseline.py::test_build_baseline_diff_reports_new_fixed_and_delta -q -p no:cacheprovider
```

预期：FAIL，报错 `ModuleNotFoundError: No module named 'evals.baseline'`。

- [ ] **步骤 3：编写最少实现代码**

在 `evals/baseline.py` 中实现：

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.schema import SuiteReport


def load_baseline_report(path: str | Path) -> SuiteReport:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return SuiteReport(**data)


def failed_case_ids(report: SuiteReport) -> set[str]:
    return {str(item.get("case_id") or "") for item in report.failed_cases if item.get("case_id")}


def build_baseline_diff(current: SuiteReport, baseline: SuiteReport, *, baseline_path: str = "") -> dict[str, Any]:
    ...
```

实现要求：

- `new_failed_cases`、`fixed_cases`、`still_failed_cases` 使用排序后的字符串列表，保证报告稳定。
- `pass_rate_delta` 保留浮点值，不转字符串。
- `suite` 来自 current；`baseline_suite` 记录 baseline 的 suite。
- 如果 current 与 baseline 的 suite 不同，仍返回 diff，但调用方 gate 会报错。

- [ ] **步骤 4：运行测试验证通过**

运行步骤 2 命令，预期 PASS。

---

### 任务 2：新增阈值门禁纯函数

**文件：**
- 修改：`evals/baseline.py`
- 测试：`tests/test_eval_baseline.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_eval_baseline.py` 中新增：

```python
def test_evaluate_gate_fails_for_low_pass_rate_and_new_failures():
    from evals.baseline import evaluate_gate
    from evals.schema import SuiteReport

    report = SuiteReport(
        suite="timing_gate",
        total=10,
        passed=8,
        failed=2,
        pass_rate=0.8,
        failed_cases=[{"case_id": "new_bad", "errors": ["bad"]}],
    )
    diff = {"baseline_suite": "timing_gate", "new_failed_cases": ["new_bad"]}

    gate = evaluate_gate(
        report,
        baseline_diff=diff,
        min_pass_rate=0.9,
        max_new_failures=0,
    )

    assert gate["passed"] is False
    assert gate["min_pass_rate"] == 0.9
    assert gate["max_new_failures"] == 0
    assert any("pass_rate" in error for error in gate["errors"])
    assert any("new_failed_cases" in error for error in gate["errors"])
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_eval_baseline.py::test_evaluate_gate_fails_for_low_pass_rate_and_new_failures -q -p no:cacheprovider
```

预期：FAIL，报错 `cannot import name 'evaluate_gate'`。

- [ ] **步骤 3：编写最少实现代码**

在 `evals/baseline.py` 中新增：

```python
def evaluate_gate(
    report: SuiteReport,
    *,
    baseline_diff: dict[str, Any] | None = None,
    min_pass_rate: float | None = None,
    max_new_failures: int | None = None,
) -> dict[str, Any]:
    ...
```

实现要求：

- `min_pass_rate is not None` 且 `report.pass_rate < min_pass_rate` 时失败。
- `max_new_failures is not None` 时必须提供 baseline diff；否则失败并提示 `baseline required`。
- `len(baseline_diff["new_failed_cases"]) > max_new_failures` 时失败。
- baseline suite 与 current suite 不一致时失败。
- 返回字段包含：
  - `passed`
  - `errors`
  - `min_pass_rate`
  - `max_new_failures`
  - `new_failure_count`

- [ ] **步骤 4：运行测试验证通过**

运行步骤 2 命令，预期 PASS。

---

### 任务 3：扩展 `run_suite()` 报告落盘

**文件：**
- 修改：`evals/schema.py`
- 修改：`evals/run.py`
- 测试：`tests/test_eval_baseline.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_eval_baseline.py` 中新增：

```python
import json
from pathlib import Path


def test_run_suite_writes_baseline_diff_and_gate(monkeypatch, tmp_path):
    from evals import run as eval_run

    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(eval_run, "REPORTS_DIR", reports_dir)
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps({
            "suite": "timing_gate",
            "total": 15,
            "passed": 14,
            "failed": 1,
            "pass_rate": 14 / 15,
            "failed_cases": [{"case_id": "timing_gate_at_bot_continue", "errors": ["old"]}],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    report = eval_run.run_suite(
        "timing_gate",
        baseline_path=baseline_path,
        min_pass_rate=1.0,
        max_new_failures=0,
    )

    assert report.failed == 0
    assert report.baseline_diff["fixed_cases"] == ["timing_gate_at_bot_continue"]
    assert report.gate["passed"] is True
    latest = json.loads((reports_dir / "latest.json").read_text(encoding="utf-8"))
    assert latest["baseline_diff"]["fixed_cases"] == ["timing_gate_at_bot_continue"]
    assert latest["gate"]["passed"] is True
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_eval_baseline.py::test_run_suite_writes_baseline_diff_and_gate -q -p no:cacheprovider
```

预期：FAIL，报错 `run_suite() got an unexpected keyword argument 'baseline_path'` 或 `SuiteReport` 无 `baseline_diff` 字段。

- [ ] **步骤 3：编写最少实现代码**

在 `evals/schema.py` 中扩展：

```python
class SuiteReport(BaseModel):
    suite: str
    total: int
    passed: int
    failed: int
    pass_rate: float
    failed_cases: list[dict[str, Any]] = Field(default_factory=list)
    baseline_diff: dict[str, Any] | None = None
    gate: dict[str, Any] | None = None
```

在 `evals/run.py` 中：

- 抽出 `_build_report(results, suite)`，让 `run_suite()` 和 `run_suite_with_details()` 共享。
- `run_suite()` 新增参数：
  - `baseline_path: str | Path | None = None`
  - `min_pass_rate: float | None = None`
  - `max_new_failures: int | None = None`
- 当提供 baseline 或 gate 参数时：
  - 调用 `load_baseline_report()`
  - 调用 `build_baseline_diff()`
  - 调用 `evaluate_gate()`
  - 写入 `report.baseline_diff` 与 `report.gate`

- [ ] **步骤 4：运行测试验证通过**

运行步骤 2 命令，预期 PASS。

---

### 任务 4：扩展 CLI 参数和退出码

**文件：**
- 修改：`evals/run.py`
- 测试：`tests/test_eval_baseline.py`

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_eval_baseline.py` 中新增：

```python
def test_eval_run_cli_returns_failure_when_gate_fails(monkeypatch, tmp_path, capsys):
    from evals import run as eval_run

    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(eval_run, "REPORTS_DIR", reports_dir)
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps({
            "suite": "timing_gate",
            "total": 15,
            "passed": 15,
            "failed": 0,
            "pass_rate": 1.0,
            "failed_cases": [],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    exit_code = eval_run.main([
        "--suite", "timing_gate",
        "--baseline", str(baseline_path),
        "--min-pass-rate", "1.01",
        "--max-new-failures", "0",
    ])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Gate failed" in captured.out
    assert "pass_rate" in captured.out
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_eval_baseline.py::test_eval_run_cli_returns_failure_when_gate_fails -q -p no:cacheprovider
```

预期：FAIL，报错 `main() takes 0 positional arguments but 1 was given` 或 parser 不认识新参数。

- [ ] **步骤 3：编写最少实现代码**

在 `evals/run.py` 中：

- `main(argv: list[str] | None = None)`。
- parser 增加：
  - `--baseline`
  - `--min-pass-rate`
  - `--max-new-failures`
- 将参数传给 `run_suite()`。
- 输出 baseline diff 摘要：
  - `Baseline: <path>`
  - `new_failed=<n> fixed=<n> still_failed=<n>`
- 若 `report.gate["passed"] is False`：
  - 输出 `Gate failed:`
  - 逐行输出 gate errors
  - 返回 `1`
- 未启用 gate 时保留旧语义：`report.failed == 0` 返回 `0`，否则返回 `1`。

- [ ] **步骤 4：运行测试验证通过**

运行步骤 2 命令，预期 PASS。

---

### 任务 5：阶段验证与文档同步

**文件：**
- 修改：`docs/plan_walkthrough.md`

- [ ] **步骤 1：运行阶段定向测试**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py -q -p no:cacheprovider
```

预期：全部 PASS。

- [ ] **步骤 2：运行 TimingGate 回归**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/test_timing_score.py tests/test_timing_runtime.py tests/test_timing_gate_prompt_policy.py -q -p no:cacheprovider
```

预期：全部 PASS。

- [ ] **步骤 3：运行全量测试**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m pytest tests/ -q -p no:cacheprovider --durations=20
```

预期：全部 PASS，记录最慢测试用于观察是否有 bug 拖慢。

- [ ] **步骤 4：手动运行 CLI 门禁**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; python -B -m evals.run --suite timing_gate --baseline evals/reports/2026-06-17-timing_gate.json --min-pass-rate 1.0 --max-new-failures 0
```

预期：退出码 0，输出 pass rate、baseline diff 和 gate passed 摘要。

- [ ] **步骤 5：同步阶段计划状态**

修改 `docs/plan_walkthrough.md`：

- 进度总览中阶段 12 标记为「已完成」。
- 阶段 12 详细段落记录：
  - 新增 `evals/baseline.py`
  - `run_suite()` 和 CLI 支持 baseline / gate
  - 验证命令与结果
- 「下一步」改为阶段 13 文档收尾。

- [ ] **步骤 6：Commit**

只暂存本阶段文件：

```bash
git add evals/baseline.py evals/schema.py evals/run.py tests/test_eval_baseline.py docs/plan_walkthrough.md
git commit -m "feat(评测): 添加 timing gate 基线门禁"
```

如果 `.Codex/plans/timing-gate-scoring-phase12-eval-baseline.md` 尚未单独提交，先单独提交计划文件，不与实现混在一起。

---

## 自检

- 规格覆盖：覆盖 `docs/todo.md` 路线项 8 中「固化基线快照与指标口径」「`run.py` 增 baseline diff + 阈值门禁」两项；候选标注闭环与 CI 配置留给后续路线项，不混入阶段 12。
- 占位符扫描：本计划已通过禁用占位词检查，没有不可执行占位。
- 类型一致性：新增字段统一命名为 `baseline_diff` 和 `gate`，测试、schema、runner、CLI 使用同一名称。
- 兼容性：旧报告没有 `baseline_diff` / `gate` 时仍可由 `SuiteReport(**data)` 读取；admin eval 端点继续使用 `suite`、`total`、`passed`、`failed`、`pass_rate`、`failed_cases`。

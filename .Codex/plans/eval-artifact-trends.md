# 跨 artifact 周期趋势实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 基于 periodic manifest 生成只读跨 artifact 周期趋势 JSON 报告。

**架构：** 新增 `evals/artifact_trends.py`，只读取 manifest 中已经固化的 summary，不回读可变 `latest.json` 报告。模块提供纯函数聚合、文件写入和 CLI 三层接口；测试用临时 manifest fixture 验证排序、去重、delta、回归提示和 CLI 输出。

**技术栈：** Python 标准库、pytest、JSON 文件、现有 `evals.periodic_manifest` manifest schema。

---

## 文件结构

- 创建：`evals/artifact_trends.py`
  - 负责 manifest 加载、去重、趋势聚合、回归提示、报告写入和 CLI。
  - 不依赖数据库、不调用评测运行脚本、不修改 gate 退出码。
- 创建：`tests/test_eval_artifact_trends.py`
  - 覆盖纯函数、latest 不回读、未知 step 容错和 CLI。
- 修改：`docs/evals.md`
  - 增加“跨 artifact 周期趋势”说明和 CLI 示例。
  - 修正末尾“下一步转向真实样本运营动作”的旧口径。
- 修改：`docs/todo.md`
  - 更新路线项 8 当前状态，标记周期运行 manifest 后的下一阶段为跨 artifact 趋势。
- 修改：`docs/plan_walkthrough.md`
  - 新增真实样本运营 8 或独立“跨 artifact 周期趋势”阶段记录。
  - 同步当前目标和进度总览。

## 数据合同

输入 manifest 顶层字段：

```json
{
  "manifest_version": 1,
  "run_id": "20260620_120000_local",
  "run_type": "periodic",
  "trigger": "local",
  "started_at": "2026-06-20T12:00:00+08:00",
  "finished_at": "2026-06-20T12:03:00+08:00",
  "exit_code": 0,
  "status": "passed",
  "git": {"sha": "abc", "ref": "master", "repository": ""},
  "steps": []
}
```

输出报告顶层字段：

```json
{
  "trend_version": 1,
  "source": {
    "manifest_count": 2,
    "run_count": 2,
    "manifest_globs": [],
    "deduped_run_ids": []
  },
  "summary": {},
  "series": {
    "runs": [],
    "eval_suites": {},
    "rag_benchmark": [],
    "timing_signal_audit": []
  },
  "regressions": []
}
```

## 任务 1：趋势聚合纯函数

**文件：**

- 创建：`tests/test_eval_artifact_trends.py`
- 创建：`evals/artifact_trends.py`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_eval_artifact_trends.py` 中新增 fixture helper 和两个测试：

```python
import json
from pathlib import Path


def _manifest(
    run_id: str,
    *,
    started_at: str,
    status: str = "passed",
    exit_code: int = 0,
    steps: list[dict] | None = None,
) -> dict:
    return {
        "manifest_version": 1,
        "run_id": run_id,
        "run_type": "periodic",
        "trigger": "local",
        "started_at": started_at,
        "finished_at": started_at.replace("00:00", "03:00"),
        "status": status,
        "exit_code": exit_code,
        "git": {"sha": run_id, "ref": "master", "repository": ""},
        "steps": steps or [],
    }


def _eval_step(suite: str, *, pass_rate: float, failed: int = 0, new_failed: int = 0) -> dict:
    return {
        "name": suite,
        "kind": "eval_suite",
        "suite": suite,
        "status": "passed" if failed == 0 else "failed",
        "exit_code": 0 if failed == 0 else 1,
        "summary": {
            "total": 10,
            "passed": 10 - failed,
            "failed": failed,
            "pass_rate": pass_rate,
        },
        "gate_passed": failed == 0,
        "new_failed_cases": [f"new_{i}" for i in range(new_failed)],
        "failed_cases": [{"case_id": f"case_{i}"} for i in range(failed)],
    }


def _rag_step(*, pass_rate: float, hit_at_5: float, mrr: float) -> dict:
    return {
        "name": "rag stable gate",
        "kind": "rag_benchmark",
        "suite": "rag_benchmark",
        "status": "passed",
        "exit_code": 0,
        "summary": {
            "total_cases": 13,
            "positive_cases": 4,
            "pass_rate": pass_rate,
            "hit@5": hit_at_5,
            "mrr": mrr,
        },
        "gate_passed": True,
    }


def _timing_step(*, mismatch_count: int, mismatch_rate: float) -> dict:
    return {
        "name": "timing signal audit",
        "kind": "timing_signal_audit",
        "suite": "timing_signal_audit",
        "status": "passed",
        "exit_code": 0,
        "summary": {
            "total_samples": 20,
            "labeled_samples": 5,
            "action_mismatch_count": mismatch_count,
            "action_mismatch_rate": mismatch_rate,
        },
        "notes": {"mode": "sampled"},
    }


def test_artifact_trends_builds_series_and_deltas():
    from evals.artifact_trends import build_artifact_trends, dedupe_manifests

    older = _manifest(
        "run_1",
        started_at="2026-06-20T10:00:00+08:00",
        steps=[
            _eval_step("timing_gate", pass_rate=1.0),
            _rag_step(pass_rate=1.0, hit_at_5=1.0, mrr=1.0),
            _timing_step(mismatch_count=1, mismatch_rate=0.05),
        ],
    )
    newer = _manifest(
        "run_2",
        started_at="2026-06-20T11:00:00+08:00",
        status="failed",
        exit_code=1,
        steps=[
            _eval_step("timing_gate", pass_rate=0.8, failed=2, new_failed=1),
            _rag_step(pass_rate=0.9, hit_at_5=0.75, mrr=0.7),
            _timing_step(mismatch_count=3, mismatch_rate=0.15),
        ],
    )

    trends = build_artifact_trends(dedupe_manifests([newer, older]))

    assert trends["summary"]["latest_run_id"] == "run_2"
    assert trends["summary"]["previous_run_id"] == "run_1"
    assert trends["summary"]["failed_run_count"] == 1
    eval_item = trends["series"]["eval_suites"]["timing_gate"][-1]
    assert eval_item["pass_rate_delta"] == -0.2
    assert eval_item["failed_delta"] == 2
    rag_item = trends["series"]["rag_benchmark"][-1]
    assert rag_item["hit@5_delta"] == -0.25
    assert rag_item["mrr_delta"] == -0.3
    timing_item = trends["series"]["timing_signal_audit"][-1]
    assert timing_item["label_coverage_rate"] == 0.25
    assert timing_item["action_mismatch_rate_delta"] == 0.1
    assert {item["type"] for item in trends["regressions"]} >= {
        "run_failed",
        "gate_failed",
        "eval_pass_rate_drop",
        "eval_new_failures",
        "rag_hit_at_5_drop",
        "timing_action_mismatch_rate_increase",
    }


def test_artifact_trends_ignores_latest_report_paths(tmp_path):
    from evals.artifact_trends import build_artifact_trends

    latest = tmp_path / "latest.json"
    latest.write_text(json.dumps({"metrics": {"overall": {"hit@5": 0.0}}}), encoding="utf-8")
    manifest = _manifest(
        "run_1",
        started_at="2026-06-20T10:00:00+08:00",
        steps=[
            {
                **_rag_step(pass_rate=1.0, hit_at_5=1.0, mrr=1.0),
                "report_paths": [str(latest)],
            }
        ],
    )

    trends = build_artifact_trends([manifest])

    assert trends["series"]["rag_benchmark"][0]["hit@5"] == 1.0
```

- [ ] **步骤 2：运行测试验证红灯**

运行：

```bash
python -B -m pytest tests/test_eval_artifact_trends.py::test_artifact_trends_builds_series_and_deltas tests/test_eval_artifact_trends.py::test_artifact_trends_ignores_latest_report_paths -q -p no:cacheprovider
```

预期：失败，报错 `ModuleNotFoundError: No module named 'evals.artifact_trends'`。

- [ ] **步骤 3：编写最小实现**

创建 `evals/artifact_trends.py`，实现：

```python
"""跨周期 artifact 趋势聚合工具。"""
from __future__ import annotations

import argparse
import glob
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def load_periodic_manifests(globs: list[str]) -> list[dict[str, Any]]:
    ...


def dedupe_manifests(manifests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ...


def build_artifact_trends(
    manifests: list[dict[str, Any]],
    manifest_globs: list[str] | None = None,
) -> dict[str, Any]:
    ...


def write_trend_report(payload: dict[str, Any], out_path: str | Path) -> Path:
    ...
```

实现要求：

- `load_periodic_manifests()` 用 `glob.glob()` 读取文件，忽略不存在的 glob，JSON 顶层不是对象时抛 `ValueError`。
- `dedupe_manifests()` 过滤 `manifest_version != 1` 和空 `run_id`，按 `run_id` 去重，优先保留 `steps` 更多的一份，再按读入顺序保留后者。
- `build_artifact_trends()` 自身也调用 `dedupe_manifests()`，保证直接传入重复 manifest 时仍稳定。
- `runs` series 计算 `failed_step_count` 和 `duration_sec`。
- eval / RAG / TimingSignal series 都从 `steps[].summary` 读取，不回读 `report_paths`。
- delta 用 `round(current - previous, 10)`，第一条为 `None`。
- `regressions` 只基于最新 run 和每个 series 最新 item。

- [ ] **步骤 4：运行测试验证绿灯**

运行：

```bash
python -B -m pytest tests/test_eval_artifact_trends.py::test_artifact_trends_builds_series_and_deltas tests/test_eval_artifact_trends.py::test_artifact_trends_ignores_latest_report_paths -q -p no:cacheprovider
```

预期：`2 passed`。

- [ ] **步骤 5：补容错测试**

在 `tests/test_eval_artifact_trends.py` 新增：

```python
def test_artifact_trends_keeps_unknown_steps_without_metrics():
    from evals.artifact_trends import build_artifact_trends

    manifest = _manifest(
        "run_1",
        started_at="2026-06-20T10:00:00+08:00",
        steps=[
            {
                "name": "custom",
                "kind": "custom_kind",
                "suite": "custom_suite",
                "status": "failed",
                "exit_code": 1,
                "summary": {},
                "report_missing": True,
            }
        ],
    )

    trends = build_artifact_trends([manifest])

    assert trends["summary"]["latest_failed_step_count"] == 1
    assert trends["series"]["runs"][0]["failed_step_count"] == 1
    assert trends["series"]["eval_suites"] == {}
    assert trends["series"]["rag_benchmark"] == []
    assert trends["series"]["timing_signal_audit"] == []
    assert any(item["type"] == "report_missing" for item in trends["regressions"])
```

- [ ] **步骤 6：运行容错测试验证红绿**

运行：

```bash
python -B -m pytest tests/test_eval_artifact_trends.py::test_artifact_trends_keeps_unknown_steps_without_metrics -q -p no:cacheprovider
```

如果失败，补最小实现；最终预期：`1 passed`。

- [ ] **步骤 7：运行任务 1 定向回归**

运行：

```bash
python -B -m pytest tests/test_eval_artifact_trends.py -q -p no:cacheprovider
```

预期：本文件全部通过。

- [ ] **步骤 8：提交任务 1**

运行：

```bash
git add evals/artifact_trends.py tests/test_eval_artifact_trends.py
git commit -m "feat(评测): 聚合周期趋势报表"
```

## 任务 2：CLI 与报告写入

**文件：**

- 修改：`tests/test_eval_artifact_trends.py`
- 修改：`evals/artifact_trends.py`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_eval_artifact_trends.py` 新增：

```python
def test_artifact_trends_cli_writes_report(tmp_path):
    from evals import artifact_trends

    manifest_path = tmp_path / "2026-06-20-periodic_manifest.json"
    manifest_path.write_text(
        json.dumps(
            _manifest(
                "run_1",
                started_at="2026-06-20T10:00:00+08:00",
                steps=[_eval_step("timing_gate", pass_rate=1.0)],
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    out = tmp_path / "artifact_trends_latest.json"

    exit_code = artifact_trends.main(
        [
            "--manifest-glob",
            str(tmp_path / "*-periodic_manifest.json"),
            "--out",
            str(out),
        ]
    )

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["trend_version"] == 1
    assert payload["source"]["manifest_count"] == 1
    assert payload["source"]["run_count"] == 1
    assert payload["series"]["eval_suites"]["timing_gate"][0]["pass_rate"] == 1.0
    assert payload["regressions"] == []
```

- [ ] **步骤 2：运行测试验证红灯**

运行：

```bash
python -B -m pytest tests/test_eval_artifact_trends.py::test_artifact_trends_cli_writes_report -q -p no:cacheprovider
```

预期：失败，原因是 `main()` 尚未实现或 CLI 未写文件。

- [ ] **步骤 3：实现 CLI**

在 `evals/artifact_trends.py` 中补：

- `write_trend_report()`：创建父目录，用 `json.dumps(..., ensure_ascii=False, indent=2)` 写文件，返回 `Path`。
- `main(argv=None)`：
  - `--manifest-glob` 使用 `action="append"`。
  - 默认 glob 为 `evals/reports/*-periodic_manifest.json` 和 `evals/reports/runs/*/manifest.json`。
  - `--out` 默认 `evals/reports/artifact_trends_latest.json`。
  - 加载 manifest，构建趋势，写报告。
  - `print(f"artifact_trends={path}")`。
  - 返回 `0`。
- 模块末尾：

```python
if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **步骤 4：运行 CLI 测试验证绿灯**

运行：

```bash
python -B -m pytest tests/test_eval_artifact_trends.py::test_artifact_trends_cli_writes_report -q -p no:cacheprovider
```

预期：`1 passed`。

- [ ] **步骤 5：运行真实 CLI smoke**

运行：

```bash
python -B -m evals.artifact_trends --out tmp/artifact_trends_latest.json
```

预期：退出码 0，输出 `artifact_trends=tmp/artifact_trends_latest.json`。

- [ ] **步骤 6：运行任务 2 相邻回归**

运行：

```bash
python -B -m pytest tests/test_eval_artifact_trends.py tests/test_eval_baseline.py tests/test_timing_signal_audit_periodic.py -q -p no:cacheprovider
```

预期：全部通过。

- [ ] **步骤 7：提交任务 2**

运行：

```bash
git add evals/artifact_trends.py tests/test_eval_artifact_trends.py
git commit -m "feat(评测): 导出周期趋势报表"
```

## 任务 3：文档收口

**文件：**

- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/eval-artifact-trends.md`

- [ ] **步骤 1：更新 `docs/evals.md`**

在 periodic manifest 说明附近新增“跨 artifact 周期趋势”：

````markdown
### 跨 artifact 周期趋势

周期运行 manifest 完成后，可以用只读趋势工具聚合多个 run：

```bash
python -B -m evals.artifact_trends \
  --manifest-glob 'evals/reports/*-periodic_manifest.json' \
  --manifest-glob 'evals/reports/runs/*/manifest.json' \
  --out evals/reports/artifact_trends_latest.json
```

趋势报告输出 `series.runs`、`series.eval_suites`、`series.rag_benchmark`、`series.timing_signal_audit` 和 `regressions`。它只用于复盘，不改变 PR gate、周期 gate、baseline 或调参阈值。
````

同时把文件末尾“下一步转向真实样本运营动作”改为“下一步可基于趋势报告做只读调参分析或补充更厚的 TimingSignal artifact”。

- [ ] **步骤 2：更新 `docs/todo.md`**

将路线项 8 的“现状”标题从只写 P4-5H 改为包含真实样本运营 1-8。正文补一句：

```markdown
跨 artifact 周期趋势已基于 periodic manifest 落地，输出只读 `artifact_trends_latest.json`，用于观察 eval / RAG / TimingSignal 的跨 run 漂移，不自动调参。
```

- [ ] **步骤 3：更新 `docs/plan_walkthrough.md`**

新增或同步：

写入一段完成记录，必须包含设计、计划、趋势聚合、CLI 和文档收口的真实提交哈希。提交哈希从 `git log --oneline -n 10` 读取，不保留模板字样。记录内容说明本阶段新增 `evals.artifact_trends`，从 periodic manifest 生成只读 `artifact_trends_latest.json`，不新增 Admin API、WebUI、gate 或调参逻辑。

在进度总览中追加一行：

该行应写成“真实样本运营 8 / 已完成 / 跨 artifact 周期趋势”，目标说明为“基于 periodic manifest 聚合 run、eval、RAG 和 TimingSignal 趋势，只读不调参”，提交列填入本阶段真实提交哈希。

- [ ] **步骤 4：更新本计划**

把已完成任务勾选，补充实际验证命令和提交哈希。

- [ ] **步骤 5：运行文档检查和定向回归**

运行：

```bash
git diff --check -- docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/eval-artifact-trends.md
python -B -m pytest tests/test_eval_artifact_trends.py tests/test_eval_baseline.py tests/test_timing_signal_audit_periodic.py -q -p no:cacheprovider
```

预期：无空白错误，测试全部通过。

- [ ] **步骤 6：运行最终全量回归**

运行：

```bash
python -B -m pytest tests/ -q -p no:cacheprovider
```

预期：全部通过，允许既有 skipped 和 warnings。

- [ ] **步骤 7：提交文档收口**

运行：

```bash
git add docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/eval-artifact-trends.md
git commit -m "docs(计划): 收口周期趋势报表"
```

## 验证记录维护

- 设计文档提交前验证：`python -B -m pytest tests/ -q -p no:cacheprovider`，结果 `1412 passed, 6 skipped, 139 warnings in 104.38s`。

执行阶段每完成一个任务，都在文档收口前追加对应命令、退出码和 pytest 汇总。红灯记录必须说明失败原因，绿灯记录必须包含通过数量。

## 执行边界

- 不修改 `evals.periodic_manifest` 既有 manifest schema。
- 不修改 `scripts/run_eval_periodic.sh`。
- 不修改 `.github/workflows/timing-gate-eval.yml`。
- 不新增 API、WebUI 或数据库表。
- 不更新 baseline。
- 不自动调参。
- 不回读历史 manifest 中的可变 `latest.json`。

# 周期运行 Manifest 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [x]`）语法来跟踪进度。

**目标：** 为周期评测复跑生成统一 manifest，记录一次运行的步骤状态、报告路径和摘要指标。

**架构：** `scripts/run_eval_periodic.sh` 继续负责 keep-going 编排，并把每个步骤结果写入 JSONL。新增 `evals/periodic_manifest.py` 负责读取步骤 JSONL 和报告文件，生成 latest、dated 和 run-scoped manifest。第一版只落文件，不新增 Admin API、WebUI 或调参逻辑。

**技术栈：** Bash、Python 标准库、pytest、JSON。

---

## 设计来源

- 设计文档：`docs/superpowers/specs/2026-06-20-eval-periodic-manifest-design.md`
- 当前前置状态：真实样本运营 1-6 已完成；周期复跑、RAG 报告和 TimingSignal audit 已存在，但没有统一 manifest。
- 不纳入本计划：Admin API、WebUI、阈值调参、baseline 更新、跨 artifact 趋势页面、历史报告迁移。

## 文件结构

- 创建：`evals/periodic_manifest.py`
  - 职责：构建周期 manifest、提取报告摘要、写 latest / dated / run-scoped JSON。
- 修改：`scripts/run_eval_periodic.sh`
  - 职责：为每个周期步骤记录 JSONL，并在退出前调用 manifest helper。
- 修改：`.github/workflows/timing-gate-eval.yml`
  - 职责：把 manifest 文件纳入 artifact 上传。
- 修改：`tests/test_eval_baseline.py`
  - 职责：覆盖 manifest helper、周期脚本和 workflow artifact 契约。
- 修改：`tests/test_timing_signal_audit_periodic.py`
  - 职责：覆盖 skipped TimingSignal audit 报告在 manifest 中的摘要。
- 修改：`docs/evals.md`
  - 职责：记录周期 manifest 文件、字段和排查方式。
- 修改：`docs/todo.md`
  - 职责：同步路线项 8 下一步状态。
- 修改：`docs/plan_walkthrough.md`
  - 职责：记录本阶段提交、验证和下一步。
- 修改：`.Codex/plans/eval-periodic-manifest.md`
  - 职责：执行过程中勾选步骤并写入实际验证记录。

## 任务 1：Manifest helper 与摘要契约

**文件：**

- 创建：`evals/periodic_manifest.py`
- 修改：`tests/test_eval_baseline.py`

- [x] **步骤 1：编写失败的 helper 测试**

在 `tests/test_eval_baseline.py` 中新增：

```python
def test_periodic_manifest_builds_step_summaries(tmp_path):
    from evals.periodic_manifest import build_periodic_manifest, write_steps_jsonl

    eval_report = tmp_path / "eval.json"
    eval_report.write_text(
        json.dumps(
            {
                "suite": "capability_reply_contract",
                "total": 3,
                "passed": 3,
                "failed": 0,
                "pass_rate": 1.0,
                "failed_cases": [],
                "baseline_diff": {"new_failed_cases": []},
                "gate": {"passed": True, "errors": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rag_report = tmp_path / "rag.json"
    rag_report.write_text(
        json.dumps(
            {
                "suite": "rag_benchmark",
                "metrics": {
                    "overall": {
                        "total_cases": 2,
                        "pass_rate": 1.0,
                        "hit@5": 1.0,
                        "mrr": 1.0,
                        "positive_cases": 1,
                    }
                },
                "failed_cases": [],
                "baseline_diff": {"new_failed_cases": []},
                "gate": {"passed": True, "errors": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    timing_report = tmp_path / "timing_signal.json"
    timing_report.write_text(
        json.dumps(
            {
                "total_samples": 0,
                "labeled_samples": 0,
                "shadow": {
                    "action_mismatch_count": 0,
                    "action_mismatch_rate": 0.0,
                },
                "source": {
                    "mode": "skipped",
                    "reason": "db_not_found",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    steps_path = tmp_path / "steps.jsonl"
    write_steps_jsonl(
        steps_path,
        [
            {
                "name": "capability reply contract",
                "kind": "eval_suite",
                "suite": "capability_reply_contract",
                "exit_code": 0,
                "report_paths": [str(eval_report)],
                "baseline_path": "evals/baselines/capability_reply_contract.json",
            },
            {
                "name": "rag benchmark manual fixture deterministic gate",
                "kind": "rag_benchmark",
                "suite": "rag_benchmark",
                "exit_code": 0,
                "report_paths": [str(rag_report)],
                "baseline_path": "evals/baselines/rag_benchmark.json",
            },
            {
                "name": "timing signal audit",
                "kind": "timing_signal_audit",
                "suite": "timing_signal_audit",
                "exit_code": 0,
                "report_paths": [str(timing_report)],
            },
        ],
    )

    manifest = build_periodic_manifest(
        steps_path=steps_path,
        run_id="unit_run",
        started_at="2026-06-20T10:00:00+08:00",
        finished_at="2026-06-20T10:05:00+08:00",
        exit_code=0,
        trigger="local",
    )

    assert manifest["manifest_version"] == 1
    assert manifest["run_id"] == "unit_run"
    assert manifest["status"] == "passed"
    assert [step["kind"] for step in manifest["steps"]] == [
        "eval_suite",
        "rag_benchmark",
        "timing_signal_audit",
    ]
    assert manifest["steps"][0]["summary"]["pass_rate"] == 1.0
    assert manifest["steps"][0]["gate_passed"] is True
    assert manifest["steps"][1]["summary"]["hit@5"] == 1.0
    assert manifest["steps"][2]["summary"]["total_samples"] == 0
    assert manifest["steps"][2]["notes"]["reason"] == "db_not_found"
```

- [x] **步骤 2：运行 helper 红灯测试**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py::test_periodic_manifest_builds_step_summaries -q -p no:cacheprovider
```

预期：失败，错误为 `ModuleNotFoundError: No module named 'evals.periodic_manifest'`。

- [x] **步骤 3：实现 helper 最小代码**

创建 `evals/periodic_manifest.py`，提供这些公开函数：

- `write_steps_jsonl(path, steps)`：创建父目录，把每个 step 以一行 JSON 写入 `path`，使用 `ensure_ascii=False`。
- `build_periodic_manifest`：读取 steps JSONL，按 step 类型提取报告摘要，返回完整 manifest 字典。
- `write_periodic_manifest(manifest, reports_dir="evals/reports")`：写入 `periodic_manifest_latest.json`、`YYYY-MM-DD-periodic_manifest.json` 和 `runs/<run_id>/manifest.json`，返回三个路径。
- `main(argv=None)`：解析 `--steps`、`--run-id`、`--started-at`、`--finished-at`、`--exit-code`、`--trigger`、`--reports-dir`、`--git-sha`、`--git-ref` 和 `--git-repository`，写 manifest 后打印 latest 路径。

`build_periodic_manifest()` 的参数必须覆盖：

- `steps_path: str | Path`
- `run_id: str`
- `started_at: str`
- `finished_at: str`
- `exit_code: int`
- `trigger: str`
- `git: dict[str, str] | None = None`
- `artifacts: list[str] | None = None`

摘要提取规则：

- `eval_suite`：读取 `total`、`passed`、`failed`、`pass_rate`、`failed_cases`、`gate.passed`、`baseline_diff.new_failed_cases`。
- `rag_benchmark`：读取 `metrics.overall`、`failed_cases`、`gate.passed`、`baseline_diff.new_failed_cases`。
- `timing_signal_audit`：读取 `total_samples`、`labeled_samples`、`shadow.action_mismatch_count`、`shadow.action_mismatch_rate`、`source.reason`。
- 报告缺失时保留 step，写入 `report_missing: true` 和空 summary。

- [x] **步骤 4：运行 helper 绿灯测试**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py::test_periodic_manifest_builds_step_summaries -q -p no:cacheprovider
```

预期：`1 passed`。

- [x] **步骤 5：提交任务 1**

运行：

```bash
git add evals/periodic_manifest.py tests/test_eval_baseline.py
git commit -m "feat(评测): 构建周期运行清单"
```

## 任务 2：周期脚本写入 manifest

**文件：**

- 修改：`scripts/run_eval_periodic.sh`
- 修改：`tests/test_eval_baseline.py`
- 修改：`tests/test_timing_signal_audit_periodic.py`

- [x] **步骤 1：编写失败的脚本契约测试**

在 `tests/test_eval_baseline.py` 中新增：

```python
def test_eval_periodic_script_writes_manifest():
    script = Path("scripts/run_eval_periodic.sh")

    text = script.read_text(encoding="utf-8")
    assert "PERIODIC_RUN_ID" in text
    assert "PERIODIC_STEPS_JSONL" in text
    assert "record_step" in text
    assert "python -B -m evals.periodic_manifest" in text
    assert "periodic_manifest_latest.json" in text
    assert "runs/${PERIODIC_RUN_ID}/manifest.json" in text
```

在 `tests/test_timing_signal_audit_periodic.py` 中新增：

```python
def test_periodic_script_indexes_timing_signal_audit_report():
    text = Path("scripts/run_eval_periodic.sh").read_text(encoding="utf-8")

    assert "timing signal audit" in text
    assert "timing_signal_audit" in text
    assert "evals/reports/timing_signal_audit_latest.json" in text
```

- [x] **步骤 2：运行脚本红灯测试**

运行：

```bash
python -B -m pytest \
  tests/test_eval_baseline.py::test_eval_periodic_script_writes_manifest \
  tests/test_timing_signal_audit_periodic.py::test_periodic_script_indexes_timing_signal_audit_report \
  -q \
  -p no:cacheprovider
```

预期：至少第一条失败，原因是周期脚本尚未写 manifest。

- [x] **步骤 3：改造周期脚本**

在 `scripts/run_eval_periodic.sh` 中：

- 初始化 `PERIODIC_RUN_ID`，CI 下优先使用 `GITHUB_RUN_ID` / `GITHUB_RUN_ATTEMPT`，本地使用时间戳。
- 初始化 `PERIODIC_STEPS_JSONL`。
- 增加 `record_step()`，用 Python 向 JSONL 追加步骤记录。
- 扩展 `run_step()`，支持传入 `kind`、`suite`、`baseline_path`、`report_paths`。
- 每个已有步骤都记录固定元数据。
- 退出前调用：

```bash
python -B -m evals.periodic_manifest \
  --steps "$PERIODIC_STEPS_JSONL" \
  --run-id "$PERIODIC_RUN_ID" \
  --started-at "$PERIODIC_STARTED_AT" \
  --finished-at "$PERIODIC_FINISHED_AT" \
  --exit-code "$status" \
  --trigger "${GITHUB_EVENT_NAME:-local}" \
  --reports-dir evals/reports
```

- [x] **步骤 4：运行脚本绿灯测试**

运行：

```bash
python -B -m pytest \
  tests/test_eval_baseline.py::test_eval_periodic_script_writes_manifest \
  tests/test_timing_signal_audit_periodic.py::test_periodic_script_indexes_timing_signal_audit_report \
  -q \
  -p no:cacheprovider
```

预期：`2 passed`。

- [x] **步骤 5：运行周期脚本验证 manifest**

运行：

```bash
bash scripts/run_eval_periodic.sh
python - <<'PY'
import json
from pathlib import Path
payload = json.loads(Path("evals/reports/periodic_manifest_latest.json").read_text(encoding="utf-8"))
assert payload["manifest_version"] == 1
assert payload["run_type"] == "periodic"
assert payload["steps"]
assert any(step["kind"] == "rag_benchmark" for step in payload["steps"])
assert any(step["kind"] == "timing_signal_audit" for step in payload["steps"])
print(payload["run_id"], payload["status"], len(payload["steps"]))
PY
```

预期：周期脚本退出码 0，manifest 断言通过。

- [x] **步骤 6：提交任务 2**

运行：

```bash
git add scripts/run_eval_periodic.sh tests/test_eval_baseline.py tests/test_timing_signal_audit_periodic.py
git commit -m "ci(评测): 输出周期运行清单"
```

## 任务 3：Workflow artifact 与文档收口

**文件：**

- 修改：`.github/workflows/timing-gate-eval.yml`
- 修改：`tests/test_eval_baseline.py`
- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/eval-periodic-manifest.md`

- [x] **步骤 1：编写 workflow artifact 红灯测试**

在 `tests/test_eval_baseline.py` 中新增：

```python
def test_eval_workflow_uploads_periodic_manifest():
    workflow = Path(".github/workflows/timing-gate-eval.yml")

    text = workflow.read_text(encoding="utf-8")
    assert "evals/reports/periodic_manifest_*.json" in text
    assert "evals/reports/runs/**/manifest.json" in text
```

- [x] **步骤 2：运行 workflow 红灯测试**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py::test_eval_workflow_uploads_periodic_manifest -q -p no:cacheprovider
```

预期：失败，原因是 workflow 尚未上传 manifest glob。

- [x] **步骤 3：修改 workflow**

在 `.github/workflows/timing-gate-eval.yml` 的 artifact `path` 中追加：

```yaml
            evals/reports/periodic_manifest_*.json
            evals/reports/runs/**/manifest.json
```

- [x] **步骤 4：运行 workflow 绿灯测试**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py::test_eval_workflow_uploads_periodic_manifest -q -p no:cacheprovider
```

预期：`1 passed`。

- [x] **步骤 5：更新文档**

更新：

- `docs/evals.md`
  - 在「周期性复跑与报告归档」章节说明 manifest 路径、核心字段和排查顺序。
- `docs/todo.md`
  - 标记周期运行 manifest 已完成，下一步改为跨 artifact 趋势或按周期报告调参。
- `docs/plan_walkthrough.md`
  - 顶部追加本阶段设计、计划、实现提交和验证结果。
- `.Codex/plans/eval-periodic-manifest.md`
  - 勾选已完成项并写入实际验证记录。

- [x] **步骤 6：运行最终验证**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py tests/test_timing_signal_audit_periodic.py -q -p no:cacheprovider
bash scripts/run_eval_periodic.sh
python -B -m pytest tests/ -q -p no:cacheprovider
```

预期：全部退出码为 0。

- [x] **步骤 7：提交任务 3**

运行：

```bash
git add .github/workflows/timing-gate-eval.yml tests/test_eval_baseline.py docs/evals.md docs/todo.md docs/plan_walkthrough.md .Codex/plans/eval-periodic-manifest.md
git commit -m "docs(计划): 收口周期运行清单"
```

## 验证记录

- 设计与计划提交：`7e17125 docs(评测): 设计周期运行清单`。
- 任务 1 红灯：`python -B -m pytest tests/test_eval_baseline.py::test_periodic_manifest_builds_step_summaries -q -p no:cacheprovider`，结果 `1 failed, 1 warning in 5.88s`，失败点为 `ModuleNotFoundError: No module named 'evals.periodic_manifest'`。
- 任务 1 绿灯：同一命令，结果 `1 passed, 1 warning in 0.83s`。
- 任务 1 相邻回归：`python -B -m pytest tests/test_eval_baseline.py -q -p no:cacheprovider`，结果 `20 passed, 1 warning in 1.23s`。
- 任务 1 提交：`a4660c1 feat(评测): 构建周期运行清单`。
- 任务 2 红灯：`python -B -m pytest tests/test_eval_baseline.py::test_eval_periodic_script_writes_manifest tests/test_timing_signal_audit_periodic.py::test_periodic_script_indexes_timing_signal_audit_report -q -p no:cacheprovider`，结果 `2 failed, 1 warning in 5.92s`，失败点为周期脚本缺少 `PERIODIC_RUN_ID`、`record_step` 和 `evals/reports/timing_signal_audit_latest.json`。
- 任务 2 绿灯：同一命令，结果 `2 passed, 1 warning in 0.81s`。
- 任务 2 周期脚本验证：`bash scripts/run_eval_periodic.sh` 退出码 0，内部 eval guard `29 passed, 1 warning in 1.84s`，所有 gate passed，manifest 断言输出 `20260620_204359_local passed 7`。
- 任务 2 相邻回归：`python -B -m pytest tests/test_eval_baseline.py tests/test_timing_signal_audit_periodic.py -q -p no:cacheprovider`，结果 `24 passed, 1 warning in 1.71s`。
- 任务 2 提交：`f459acc ci(评测): 输出周期运行清单`。
- 任务 3 红灯：`python -B -m pytest tests/test_eval_baseline.py::test_eval_workflow_uploads_periodic_manifest -q -p no:cacheprovider`，结果 `1 failed, 1 warning in 5.98s`，失败点为 workflow 缺少 `evals/reports/periodic_manifest_*.json`。
- 任务 3 绿灯：同一命令，结果 `1 passed, 1 warning in 0.77s`。
- 最终定向回归：`python -B -m pytest tests/test_eval_baseline.py tests/test_timing_signal_audit_periodic.py -q -p no:cacheprovider`，结果 `25 passed, 1 warning in 2.21s`。
- 最终周期脚本：`bash scripts/run_eval_periodic.sh` 退出码 0，内部 eval guard `30 passed, 1 warning in 1.86s`，所有 gate passed，并写出 `periodic_manifest=evals/reports/periodic_manifest_latest.json`。
- 全量回归：`python -B -m pytest tests/ -q -p no:cacheprovider`，结果 `1412 passed, 6 skipped, 139 warnings in 109.97s`。

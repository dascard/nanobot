# P4-5B 周期性评测复跑与报告归档实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为稳定评测 gate 增加周期性复跑、手动触发和 CI artifact 报告归档。

**架构：** 保留 `scripts/run_eval_pr_gate.sh` 作为 PR / push 的 fail-fast 入口；新增 `scripts/run_eval_periodic.sh` 作为 schedule / manual 的 keep-going 入口。现有 `.github/workflows/timing-gate-eval.yml` 按事件类型选择脚本，并在所有结果下上传 eval / RAG 报告 artifact。

**技术栈：** Bash、GitHub Actions、pytest、`evals.run`、`evals.rag_benchmark.run`。

---

## 当前状态

- P4-5B 设计已完成并提交：`b9e6f20 docs(评测): 设计周期复跑归档`。
- P4-5B 实现计划已完成并提交：`650edb2 docs(计划): 记录周期复跑计划`。
- 任务 1 周期性 keep-going 脚本已完成并提交：`8912585 ci(评测): 增加周期评测脚本`。
- 任务 2 workflow schedule、manual dispatch 和 artifact 归档已完成并提交：`9e80a8b ci(评测): 归档周期评测报告`。
- 任务 3 文档收口已完成，提交目标为 `docs(评测): 收口周期复跑状态`。

## 实际验证摘要

- 任务 1 红灯：两个周期性脚本测试失败于 `assert script.exists()`。
- 任务 1 绿灯：两个周期性脚本测试结果 `2 passed, 1 warning in 0.73s`。
- 任务 1 周期性脚本：`bash scripts/run_eval_periodic.sh` 输出评测守卫 `24 passed, 1 warning in 1.76s`，所有子 gate 均为 `Gate passed`。
- 任务 2 红灯：workflow 三个新增测试失败于缺少 `workflow_dispatch`、`actions/upload-artifact@v4` 和 `retention-days: 14`。
- 任务 2 绿灯：workflow 四个定向测试结果 `4 passed, 1 warning in 0.82s`。
- 任务 2 评测守卫组合：`tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py` 结果 `27 passed, 1 warning in 1.75s`。
- 任务 3 文档自检：占位词扫描无匹配，U+FFFD 扫描通过，`git diff --check` 无输出。
- 任务 3 定向回归：`tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py tests/test_rag_benchmark.py` 结果 `40 passed, 1 warning in 2.42s`。
- 任务 3 周期性脚本：`bash scripts/run_eval_periodic.sh` 输出评测守卫 `27 passed, 1 warning in 1.78s`，所有子 gate 均为 `Gate passed`。
- 任务 3 PR gate：`bash scripts/run_eval_pr_gate.sh` 输出评测守卫 `27 passed, 1 warning in 1.76s`，所有子 gate 均为 `Gate passed`。
- 任务 3 全量回归：`python -B -m pytest tests/ -v -p no:cacheprovider` 结果 `1366 passed, 6 skipped, 139 warnings in 101.52s`。

## 设计来源

- 设计文档：`docs/superpowers/specs/2026-06-18-eval-periodic-reporting-design.md`
- 当前 P4-5B 范围：周期性复跑、手动触发、artifact 报告归档和失败处理文档。
- 不纳入本计划：RAG manual 样本扩充、baseline 更新、runtime provider、真实生产 DB、全量 pytest 周期性运行。

## 文件结构

- 创建：`scripts/run_eval_periodic.sh`
  - 职责：运行与 PR gate 相同的稳定 suite，但使用 keep-going 策略，尽量产出完整报告。
- 修改：`.github/workflows/timing-gate-eval.yml`
  - 职责：新增 `workflow_dispatch`、每周 schedule、周期性入口和 artifact 上传。
- 修改：`tests/test_eval_baseline.py`
  - 职责：为周期性脚本、workflow trigger、artifact 路径和 retention 增加静态守卫。
- 修改：`docs/evals.md`
  - 职责：记录周期性复跑触发方式、报告 artifact、保留天数和失败排查顺序。
- 修改：`docs/todo.md`
  - 职责：同步 P4-5B 阶段状态和验证记录。
- 修改：`docs/plan_walkthrough.md`
  - 职责：记录 P4-5B 的设计、计划、提交边界和验证记录。
- 修改：`.Codex/plans/eval-periodic-reporting.md`
  - 职责：执行完成后勾选步骤并记录实际验证结果。

## 任务 1：新增周期性 keep-going 脚本

**文件：**
- 创建：`scripts/run_eval_periodic.sh`
- 修改：`tests/test_eval_baseline.py`

- [x] **步骤 1：编写脚本红灯测试**

在 `tests/test_eval_baseline.py` 的 `test_eval_pr_gate_script_runs_stable_suites` 后追加：

```python
def test_eval_periodic_script_runs_stable_suites():
    script = Path("scripts/run_eval_periodic.sh")

    assert script.exists()
    text = script.read_text(encoding="utf-8")
    assert "run_step" in text
    assert "scripts/run_timing_gate_gate.sh" in text
    assert "--suite capability_model_routing" in text
    assert "evals/baselines/capability_model_routing.json" in text
    assert "--suite capability_reply_contract" in text
    assert "evals/baselines/capability_reply_contract.json" in text
    assert "--suite capability_rendering_contract" in text
    assert "evals/baselines/capability_rendering_contract.json" in text
    assert "evals.rag_benchmark.run" in text
    assert "--provider-mode deterministic" in text
    assert "--manual-only" in text
    assert "evals/baselines/rag_benchmark.json" in text
    assert "--max-unexpected-source-rate 0.0" in text


def test_eval_periodic_script_keeps_going_for_archival_reports():
    script = Path("scripts/run_eval_periodic.sh")

    assert script.exists()
    text = script.read_text(encoding="utf-8")
    assert "status=0" in text
    assert "status=1" in text
    assert "return 0" in text
    assert "exit \"$status\"" in text
```

- [x] **步骤 2：运行脚本红灯测试**

运行：

```bash
python -B -m pytest \
  tests/test_eval_baseline.py::test_eval_periodic_script_runs_stable_suites \
  tests/test_eval_baseline.py::test_eval_periodic_script_keeps_going_for_archival_reports \
  -v \
  -p no:cacheprovider
```

预期：FAIL，失败点为 `assert script.exists()`，因为 `scripts/run_eval_periodic.sh` 尚不存在。

- [x] **步骤 3：创建周期性脚本**

创建 `scripts/run_eval_periodic.sh`：

```bash
#!/usr/bin/env bash
set -uo pipefail

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY

export PYTHONDONTWRITEBYTECODE=1
export NANOBOT_TESTING="${NANOBOT_TESTING:-1}"
export DATABASE_URL="${DATABASE_URL:-sqlite:///:memory:}"
export NEW_API_KEY="${NEW_API_KEY:-test-key-for-ci}"
export NANOBOT_ADMIN_TOKEN="${NANOBOT_ADMIN_TOKEN:-test-admin-token}"

status=0

run_step() {
  local name="$1"
  shift
  echo "==> ${name}"
  if "$@"; then
    echo "==> ${name}: passed"
    return 0
  fi
  echo "==> ${name}: failed"
  status=1
  return 0
}

run_step "eval guard tests" \
  python -B -m pytest \
    tests/test_eval_baseline.py \
    tests/test_timing_gate_prompt_policy.py \
    -v \
    -p no:cacheprovider

run_step "timing gate" \
  bash scripts/run_timing_gate_gate.sh

run_step "capability model routing" \
  python -B -m evals.run \
    --suite capability_model_routing \
    --baseline evals/baselines/capability_model_routing.json \
    --min-pass-rate 1.0 \
    --max-new-failures 0

run_step "capability reply contract" \
  python -B -m evals.run \
    --suite capability_reply_contract \
    --baseline evals/baselines/capability_reply_contract.json \
    --min-pass-rate 1.0 \
    --max-new-failures 0

run_step "capability rendering contract" \
  python -B -m evals.run \
    --suite capability_rendering_contract \
    --baseline evals/baselines/capability_rendering_contract.json \
    --min-pass-rate 1.0 \
    --max-new-failures 0

run_step "rag benchmark manual deterministic gate" \
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

exit "$status"
```

- [x] **步骤 4：设置脚本可执行位**

运行：

```bash
chmod +x scripts/run_eval_periodic.sh
```

- [x] **步骤 5：运行脚本绿灯测试**

运行：

```bash
python -B -m pytest \
  tests/test_eval_baseline.py::test_eval_periodic_script_runs_stable_suites \
  tests/test_eval_baseline.py::test_eval_periodic_script_keeps_going_for_archival_reports \
  -v \
  -p no:cacheprovider
```

预期：PASS，`2 passed`。

- [x] **步骤 6：运行周期性脚本**

运行：

```bash
bash scripts/run_eval_periodic.sh
```

预期：退出码 0；输出包含每个 `run_step` 的 passed；各子 gate 均输出 `Gate passed`。

- [x] **步骤 7：提交任务 1**

运行：

```bash
git add scripts/run_eval_periodic.sh tests/test_eval_baseline.py
git commit -m "ci(评测): 增加周期评测脚本"
```

## 任务 2：Workflow 接入 schedule、手动触发和 artifact

**文件：**
- 修改：`.github/workflows/timing-gate-eval.yml`
- 修改：`tests/test_eval_baseline.py`

- [x] **步骤 1：编写 workflow 红灯测试**

在 `tests/test_eval_baseline.py` 的 workflow 测试附近追加：

```python
def test_eval_workflow_has_periodic_schedule_and_manual_dispatch():
    workflow = Path(".github/workflows/timing-gate-eval.yml")

    text = workflow.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "schedule:" in text
    assert 'cron: "20 20 * * 0"' in text
    assert "scripts/run_eval_periodic.sh" in text


def test_eval_workflow_uploads_report_artifacts():
    workflow = Path(".github/workflows/timing-gate-eval.yml")

    text = workflow.read_text(encoding="utf-8")
    assert "actions/upload-artifact@v4" in text
    assert "if: always()" in text
    assert "evals/reports/*.json" in text
    assert "tmp/rag_benchmark/reports/*.json" in text
    assert "tmp/rag_benchmark/reports/*.md" in text
    assert "if-no-files-found: warn" in text


def test_eval_workflow_artifact_retention_is_bounded():
    workflow = Path(".github/workflows/timing-gate-eval.yml")

    text = workflow.read_text(encoding="utf-8")
    assert "retention-days: 14" in text
```

- [x] **步骤 2：运行 workflow 红灯测试**

运行：

```bash
python -B -m pytest \
  tests/test_eval_baseline.py::test_eval_workflow_has_periodic_schedule_and_manual_dispatch \
  tests/test_eval_baseline.py::test_eval_workflow_uploads_report_artifacts \
  tests/test_eval_baseline.py::test_eval_workflow_artifact_retention_is_bounded \
  -v \
  -p no:cacheprovider
```

预期：FAIL，失败点为缺少 `workflow_dispatch`、`schedule` 或 artifact 上传配置。

- [x] **步骤 3：修改 workflow**

将 `.github/workflows/timing-gate-eval.yml` 的触发器扩展为：

```yaml
on:
  pull_request:
  workflow_dispatch:
  schedule:
    - cron: "20 20 * * 0"
  push:
    branches:
      - main
      - master
```

将原来的 `Run eval PR gate` 步骤拆成两个条件步骤：

```yaml
      - name: Run eval PR gate
        if: github.event_name == 'pull_request' || github.event_name == 'push'
        run: bash scripts/run_eval_pr_gate.sh

      - name: Run periodic eval gate
        if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'
        run: bash scripts/run_eval_periodic.sh

      - name: Upload eval reports
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: eval-reports-${{ github.run_id }}
          if-no-files-found: warn
          retention-days: 14
          path: |
            evals/reports/*.json
            tmp/rag_benchmark/reports/*.json
            tmp/rag_benchmark/reports/*.md
```

- [x] **步骤 4：运行 workflow 绿灯测试**

运行：

```bash
python -B -m pytest \
  tests/test_eval_baseline.py::test_eval_pr_gate_workflow_runs_unified_script \
  tests/test_eval_baseline.py::test_eval_workflow_has_periodic_schedule_and_manual_dispatch \
  tests/test_eval_baseline.py::test_eval_workflow_uploads_report_artifacts \
  tests/test_eval_baseline.py::test_eval_workflow_artifact_retention_is_bounded \
  -v \
  -p no:cacheprovider
```

预期：PASS，`4 passed`。

- [x] **步骤 5：运行评测守卫组合**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py -v -p no:cacheprovider
```

预期：PASS。

- [x] **步骤 6：提交任务 2**

运行：

```bash
git add .github/workflows/timing-gate-eval.yml tests/test_eval_baseline.py
git commit -m "ci(评测): 归档周期评测报告"
```

## 任务 3：文档收口与最终验证

**文件：**
- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/eval-periodic-reporting.md`

- [x] **步骤 1：更新 `docs/evals.md`**

在统一 PR Gate 章节后新增「周期性复跑与报告归档」说明：

````markdown
## 周期性复跑与报告归档

P4-5B 使用同一组稳定离线 gate 做周期性复跑，但执行入口是：

```bash
bash scripts/run_eval_periodic.sh
```

该脚本与 PR gate 覆盖相同 suite，但采用 keep-going 策略：单个 gate 失败后继续运行后续 gate，最后用累计退出码反映整体结果。这样即使前面的 suite 失败，后续 suite 和 RAG benchmark 仍能产出报告。

`.github/workflows/timing-gate-eval.yml` 的触发方式：

- `pull_request` / `push`：运行 `scripts/run_eval_pr_gate.sh`，保持 fail-fast。
- `schedule` / `workflow_dispatch`：运行 `scripts/run_eval_periodic.sh`，并上传报告 artifact。

周期性 schedule 为 UTC 周日 20:20，即北京时间周一 04:20。

Artifact 名称为 `eval-reports-${{ github.run_id }}`，保留 14 天，包含：

- `evals/reports/*.json`
- `tmp/rag_benchmark/reports/*.json`
- `tmp/rag_benchmark/reports/*.md`

排查失败时，先看 workflow 失败步骤，再下载 artifact。通用 suite 优先看 `evals/reports/YYYY-MM-DD-<suite>.json`，不要只看 `latest.json`；RAG benchmark 优先看 `tmp/rag_benchmark/reports/latest.md` 和对应 run-id JSON。
````

- [x] **步骤 2：更新 `docs/todo.md`**

把路线项 8 的 P4-5B 状态改为已完成，说明周期性 workflow、manual dispatch、artifact 归档和 keep-going 脚本已落地；下一阶段保留 P4-5C RAG manual 样本扩充。

- [x] **步骤 3：更新 `docs/plan_walkthrough.md`**

在后续优先级表中将 P4-5B 标记为已完成，新增 P4-5B 详情章节，记录：

- 设计文档路径。
- 实现计划路径。
- 任务 1 / 任务 2 / 任务 3 的提交边界。
- 红灯 / 绿灯 / 最终验证输出。
- P4-5C 仍为下一步。

- [x] **步骤 4：勾选本计划已完成步骤**

在 `.Codex/plans/eval-periodic-reporting.md` 中把已完成步骤改为 `[x]`，并在文件顶部新增实际提交和验证摘要。

- [x] **步骤 5：运行文档自检**

运行：

```bash
rg -n "T[O]DO|待[定]|后续[实]现|类似[任]务|添加[适]当|为上[述]" \
  .Codex/plans/eval-periodic-reporting.md docs/evals.md docs/todo.md docs/plan_walkthrough.md
python - <<'PY'
from pathlib import Path
for path in [
    Path('.Codex/plans/eval-periodic-reporting.md'),
    Path('docs/evals.md'),
    Path('docs/todo.md'),
    Path('docs/plan_walkthrough.md'),
]:
    data = path.read_text(encoding='utf-8')
    if '\ufffd' in data:
        raise SystemExit(f'U+FFFD found in {path}')
print('U+FFFD scan passed')
PY
git diff --check -- .Codex/plans/eval-periodic-reporting.md docs/evals.md docs/todo.md docs/plan_walkthrough.md
```

预期：`rg` 无输出且退出码为 1；U+FFFD 扫描通过；`git diff --check` 无输出。

- [x] **步骤 6：运行最终验证**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py tests/test_rag_benchmark.py -v -p no:cacheprovider
bash scripts/run_eval_periodic.sh
bash scripts/run_eval_pr_gate.sh
python -B -m pytest tests/ -v -p no:cacheprovider
```

预期：全部退出码为 0，全量 pytest 无 failure。

- [x] **步骤 7：提交任务 3**

运行：

```bash
git add .Codex/plans/eval-periodic-reporting.md docs/evals.md docs/todo.md docs/plan_walkthrough.md
git commit -m "docs(评测): 收口周期复跑状态"
```

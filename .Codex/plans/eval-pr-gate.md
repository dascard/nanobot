# P4-5A 统一评测 PR Gate 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把已稳定的 TimingGate、capability 和 RAG benchmark gate 收敛为一个本地 / CI 共用的 PR gate 入口。

**架构：** 新增 `scripts/run_eval_pr_gate.sh` 作为统一编排层，复用现有 `scripts/run_timing_gate_gate.sh`，并串行调用 capability suite 与 RAG manual deterministic gate。现有 workflow 保留触发条件，但执行入口改为统一脚本。

**技术栈：** Bash、GitHub Actions、pytest、`evals.run`、`evals.rag_benchmark.run`。

**状态（2026-06-18）：** P4-5A 已完成统一脚本、CI 接入、文档收口和最终验证。

**提交记录：**
- 设计提交：`a520fed docs(评测): 设计统一评测门禁`
- 计划提交：`bac2192 docs(计划): 记录统一评测门禁计划`
- 任务 1 提交：`8aa08db ci(评测): 增加统一评测门禁脚本`
- 任务 2 提交：`d8f5739 ci(评测): 接入统一评测门禁`
- 任务 3 提交：本提交 `docs(评测): 收口统一评测门禁状态`

**实际验证摘要：**
- 任务 1 红灯：`tests/test_eval_baseline.py::test_eval_pr_gate_script_runs_stable_suites` 失败于 `assert script.exists()`。
- 任务 1 绿灯：同一测试结果 `1 passed, 1 warning in 0.48s`。
- 任务 1 统一 gate：`bash scripts/run_eval_pr_gate.sh` 输出评测守卫 `22 passed, 1 warning in 1.53s`，`timing_gate`、`capability_model_routing`、`capability_reply_contract`、`capability_rendering_contract` 和 RAG manual deterministic gate 均为 `Gate passed`。
- 任务 2 红灯：`tests/test_eval_baseline.py::test_eval_pr_gate_workflow_runs_unified_script` 失败于 workflow 仍为 `TimingGate Eval`。
- 任务 2 绿灯：同一测试结果 `1 passed, 1 warning in 0.58s`。
- 任务 2 评测守卫组合：`tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py tests/test_rag_benchmark.py` 结果为 `35 passed, 1 warning in 2.21s`。
- 任务 3 文档自检：占位词扫描无匹配，U+FFFD 扫描通过，`git diff --check` 无输出。
- 任务 3 定向回归：`tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py tests/test_rag_benchmark.py` 结果为 `35 passed, 1 warning in 2.34s`。
- 任务 3 统一 gate：`bash scripts/run_eval_pr_gate.sh` 输出评测守卫 `22 passed, 1 warning in 1.77s`，`timing_gate`、`capability_model_routing`、`capability_reply_contract`、`capability_rendering_contract` 和 RAG manual deterministic gate 均为 `Gate passed`。
- 任务 3 全量回归：`python -B -m pytest tests/ -v -p no:cacheprovider` 结果为 `1361 passed, 6 skipped, 139 warnings in 100.83s`。

---

## 设计来源

- 设计文档：`docs/superpowers/specs/2026-06-18-eval-pr-gate-design.md`
- 当前 P4-5A 范围：统一 PR gate 入口。
- 不纳入本计划：周期性复跑、RAG manual 样本扩充、RAG query 巨函数拆分、baseline 更新。

## 文件结构

- 创建：`scripts/run_eval_pr_gate.sh`
  - 职责：统一设置 CI / 本地 gate 环境，并按稳定顺序串行运行所有 PR gate。
- 修改：`.github/workflows/timing-gate-eval.yml`
  - 职责：保留触发条件，将 workflow 名称和执行入口改为统一 eval PR gate。
- 修改：`tests/test_eval_baseline.py`
  - 职责：增加脚本和 workflow 守卫，防止 gate 漏接入。
- 修改：`docs/evals.md`
  - 职责：记录统一 PR gate 的本地运行方式、覆盖范围和失败处理。
- 修改：`docs/todo.md`
  - 职责：同步 P4-5A 阶段状态。
- 修改：`docs/plan_walkthrough.md`
  - 职责：记录 P4-5A 的设计、计划、提交边界和验证记录。
- 修改：`.Codex/plans/eval-pr-gate.md`
  - 职责：执行完成后勾选步骤并记录实际验证结果。

## 任务 1：新增统一 gate 脚本

**文件：**
- 创建：`scripts/run_eval_pr_gate.sh`
- 修改：`tests/test_eval_baseline.py`

- [x] **步骤 1：编写脚本红灯测试**

在 `tests/test_eval_baseline.py` 的 `test_timing_gate_gate_script_uses_stable_baseline` 后追加：

```python
def test_eval_pr_gate_script_runs_stable_suites():
    script = Path("scripts/run_eval_pr_gate.sh")

    assert script.exists()
    text = script.read_text(encoding="utf-8")
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
    assert "max-unexpected-source-rate" in text
```

- [x] **步骤 2：运行红灯测试**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py::test_eval_pr_gate_script_runs_stable_suites -v -p no:cacheprovider
```

预期：FAIL，失败点为 `assert script.exists()`，因为 `scripts/run_eval_pr_gate.sh` 尚不存在。

- [x] **步骤 3：创建统一脚本**

创建 `scripts/run_eval_pr_gate.sh`：

```bash
#!/usr/bin/env bash
set -euo pipefail

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY

export PYTHONDONTWRITEBYTECODE=1
export NANOBOT_TESTING="${NANOBOT_TESTING:-1}"
export DATABASE_URL="${DATABASE_URL:-sqlite:///:memory:}"
export NEW_API_KEY="${NEW_API_KEY:-test-key-for-ci}"
export NANOBOT_ADMIN_TOKEN="${NANOBOT_ADMIN_TOKEN:-test-admin-token}"

python -B -m pytest \
  tests/test_eval_baseline.py \
  tests/test_timing_gate_prompt_policy.py \
  -v \
  -p no:cacheprovider

bash scripts/run_timing_gate_gate.sh

python -B -m evals.run \
  --suite capability_model_routing \
  --baseline evals/baselines/capability_model_routing.json \
  --min-pass-rate 1.0 \
  --max-new-failures 0

python -B -m evals.run \
  --suite capability_reply_contract \
  --baseline evals/baselines/capability_reply_contract.json \
  --min-pass-rate 1.0 \
  --max-new-failures 0

python -B -m evals.run \
  --suite capability_rendering_contract \
  --baseline evals/baselines/capability_rendering_contract.json \
  --min-pass-rate 1.0 \
  --max-new-failures 0

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

- [x] **步骤 4：设置脚本可执行位**

运行：

```bash
chmod +x scripts/run_eval_pr_gate.sh
```

- [x] **步骤 5：运行脚本绿灯测试**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py::test_eval_pr_gate_script_runs_stable_suites -v -p no:cacheprovider
```

预期：PASS，`1 passed`。

- [x] **步骤 6：运行统一 gate 脚本**

运行：

```bash
bash scripts/run_eval_pr_gate.sh
```

预期：退出码 0；输出包含 TimingGate、capability suite 和 RAG benchmark 的 `Gate passed`。

- [x] **步骤 7：提交任务 1**

运行：

```bash
git add scripts/run_eval_pr_gate.sh tests/test_eval_baseline.py
git commit -m "ci(评测): 增加统一评测门禁脚本"
```

## 任务 2：CI workflow 接入统一 gate

**文件：**
- 修改：`.github/workflows/timing-gate-eval.yml`
- 修改：`tests/test_eval_baseline.py`

- [x] **步骤 1：编写 workflow 红灯测试**

将 `tests/test_eval_baseline.py` 中的 `test_timing_gate_workflow_runs_gate_script` 改名并替换为：

```python
def test_eval_pr_gate_workflow_runs_unified_script():
    workflow = Path(".github/workflows/timing-gate-eval.yml")

    assert workflow.exists()
    text = workflow.read_text(encoding="utf-8")
    assert "name: Eval PR Gate" in text
    assert "eval-pr-gate:" in text
    assert "scripts/run_eval_pr_gate.sh" in text
```

- [x] **步骤 2：运行 workflow 红灯测试**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py::test_eval_pr_gate_workflow_runs_unified_script -v -p no:cacheprovider
```

预期：FAIL，失败点为 workflow 仍叫 `TimingGate Eval` 或仍调用 `scripts/run_timing_gate_gate.sh`。

- [x] **步骤 3：修改 workflow**

将 `.github/workflows/timing-gate-eval.yml` 改为：

```yaml
name: Eval PR Gate

on:
  pull_request:
  push:
    branches:
      - main
      - master

jobs:
  eval-pr-gate:
    runs-on: ubuntu-latest
    env:
      NANOBOT_TESTING: "1"
      DATABASE_URL: "sqlite:///:memory:"
      NEW_API_KEY: "test-key-for-ci"
      NANOBOT_ADMIN_TOKEN: "test-admin-token"
      PYTHONDONTWRITEBYTECODE: "1"
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.10"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          python -m pip install -r requirements.txt

      - name: Run eval PR gate
        run: bash scripts/run_eval_pr_gate.sh
```

- [x] **步骤 4：运行 workflow 绿灯测试**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py::test_eval_pr_gate_workflow_runs_unified_script -v -p no:cacheprovider
```

预期：PASS，`1 passed`。

- [x] **步骤 5：运行评测守卫组合**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py tests/test_rag_benchmark.py -v -p no:cacheprovider
```

预期：PASS。

- [x] **步骤 6：提交任务 2**

运行：

```bash
git add .github/workflows/timing-gate-eval.yml tests/test_eval_baseline.py
git commit -m "ci(评测): 接入统一评测门禁"
```

## 任务 3：文档收口与最终验证

**文件：**
- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/eval-pr-gate.md`

- [x] **步骤 1：更新 `docs/evals.md`**

在评测门禁说明区域加入统一入口：

````markdown
### 统一 PR Gate

P4-5A 已将稳定离线 gate 收敛为一个本地 / CI 共用入口：

```bash
bash scripts/run_eval_pr_gate.sh
```

当前覆盖：

- `timing_gate`
- `capability_model_routing`
- `capability_reply_contract`
- `capability_rendering_contract`
- RAG benchmark manual deterministic gate

该入口只运行稳定 baseline gate。周期性复跑、报告归档和 RAG manual 样本扩充分别留给 P4-5B / P4-5C。
````

- [x] **步骤 2：更新 `docs/todo.md`**

把路线项 8 的下一步描述改为：P4-5A 统一 PR gate 已完成，下一阶段进入 P4-5B 周期性复跑和 P4-5C RAG manual 样本扩充。

- [x] **步骤 3：更新 `docs/plan_walkthrough.md`**

在进度表新增或更新 P4-5A 记录：

```markdown
| P4-5A | 已完成 | 统一评测 PR gate | `scripts/run_eval_pr_gate.sh` 串联 TimingGate、capability 和 RAG manual gate，CI workflow 已接入统一入口 | `docs(评测): 设计统一评测门禁` / `docs(计划): 记录统一评测门禁计划` / `ci(评测): 增加统一评测门禁脚本` / `ci(评测): 接入统一评测门禁` / `docs(评测): 收口统一评测门禁状态` |
```

并记录实际验证输出。

- [x] **步骤 4：勾选本计划已完成步骤**

在 `.Codex/plans/eval-pr-gate.md` 中把已完成步骤改为 `[x]`，并在文件顶部新增实际提交和验证摘要。

- [x] **步骤 5：运行文档自检**

运行：

```bash
rg -n "T[O]DO|待[定]|后续[实]现|类似[任]务|添加[适]当|为上[述]" \
  .Codex/plans/eval-pr-gate.md docs/evals.md docs/todo.md docs/plan_walkthrough.md
python - <<'PY'
from pathlib import Path
for path in [
    Path('.Codex/plans/eval-pr-gate.md'),
    Path('docs/evals.md'),
    Path('docs/todo.md'),
    Path('docs/plan_walkthrough.md'),
]:
    data = path.read_text(encoding='utf-8')
    if '\ufffd' in data:
        raise SystemExit(f'U+FFFD found in {path}')
print('U+FFFD scan passed')
PY
git diff --check -- .Codex/plans/eval-pr-gate.md docs/evals.md docs/todo.md docs/plan_walkthrough.md
```

预期：`rg` 无输出且退出码为 1；U+FFFD 扫描通过；`git diff --check` 无输出。

- [x] **步骤 6：运行最终验证**

运行：

```bash
python -B -m pytest tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py tests/test_rag_benchmark.py -v -p no:cacheprovider
bash scripts/run_eval_pr_gate.sh
python -B -m pytest tests/ -v -p no:cacheprovider
```

预期：全部退出码为 0，全量 pytest 无 failure。

- [x] **步骤 7：提交任务 3**

运行：

```bash
git add .Codex/plans/eval-pr-gate.md docs/evals.md docs/todo.md docs/plan_walkthrough.md
git commit -m "docs(评测): 收口统一评测门禁状态"
```

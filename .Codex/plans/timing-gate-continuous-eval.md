# TimingGate 持续评估实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把 P3-3「TimingGate 持续评估」拆成可复跑标注审计和可接入 CI 的回归门禁，完成后每个阶段单独验证、单独提交。

**架构：** P3-3A 在 `timing_signal_audit` 增加离线 labeled report 复跑入口，复用现有聚合函数，不改 timing score 阈值。P3-3B 为 `timing_gate` eval 增加仓库内 baseline、无 action scoring case、统一脚本和 CI workflow，让 PR gate 使用同一条命令。

**技术栈：** Python、pytest、JSON / JSONL、GitHub Actions、现有 `evals` 框架。

---

## 文件职责

- `core/eval_sampling/timing_signal_audit.py`：纯函数层，合并 sidecar labels，继续负责聚合报告。
- `evals/timing_signal_audit.py`：CLI 层，支持 DB 抽样模式和离线 report 复跑模式。
- `tests/test_timing_signal_audit.py`：P3-3A 红绿测试。
- `evals/run.py`：eval runner，可增加 `--no-write-report` 或 `--report-dir`。
- `evals/baselines/timing_gate.json`：稳定 baseline。
- `evals/cases/timing_gate/timing_gate_scoring_*.json`：无 `input.action` 的正式 scoring case。
- `scripts/run_timing_gate_gate.sh`：本地与 CI 共用的门禁入口。
- `.github/workflows/timing-gate-eval.yml`：PR gate。
- `tests/test_eval_baseline.py`：gate 成功和异常配置测试。
- `tests/test_timing_gate_prompt_policy.py`：正式 suite 覆盖和 scoring case 守卫。
- `docs/evals.md`：baseline 更新、失败处理和命令说明。
- `docs/todo.md`、`docs/plan_walkthrough.md`、`docs/superpowers/specs/2026-06-16-timing-gate-scoring-design.md`：状态同步。

---

## 任务 1：P3-3A 标注审计复跑入口

**文件：**
- 修改：`core/eval_sampling/timing_signal_audit.py`
- 修改：`evals/timing_signal_audit.py`
- 测试：`tests/test_timing_signal_audit.py`

- [x] **步骤 1：编写 sidecar label 合并红灯测试**

在 `tests/test_timing_signal_audit.py` 中新增测试，构造两个 sample 和一个 sidecar labels 列表，断言 `(log_id, signal_name)` 匹配后 `label` 被覆盖，`note` 被保留，未匹配样本不变。

预期测试形态：

```python
def test_merge_timing_signal_labels_overrides_by_log_id_and_signal():
    from core.eval_sampling.timing_signal_audit import merge_timing_signal_labels

    samples = [
        {"log_id": 1, "signal_name": "s_ack", "label": "true_positive"},
        {"log_id": 2, "signal_name": "s_transport"},
    ]
    labels = [
        {
            "log_id": 1,
            "signal_name": "s_ack",
            "label": "false_positive",
            "note": "后半句有请求",
        }
    ]

    merged = merge_timing_signal_labels(samples, labels)

    assert merged[0]["label"] == "false_positive"
    assert merged[0]["note"] == "后半句有请求"
    assert "label" not in merged[1]
```

- [x] **步骤 2：运行红灯测试**

运行：

```bash
python -m pytest tests/test_timing_signal_audit.py::test_merge_timing_signal_labels_overrides_by_log_id_and_signal -v
```

预期：失败于 `ImportError` 或 `AttributeError`，因为 `merge_timing_signal_labels` 尚不存在。

- [x] **步骤 3：实现最小纯函数**

在 `core/eval_sampling/timing_signal_audit.py` 增加 `merge_timing_signal_labels(samples, labels)`：

```python
def merge_timing_signal_labels(
    samples: list[dict[str, Any]],
    labels: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    label_by_key = {
        (item.get("log_id"), item.get("signal_name")): item
        for item in labels
        if item.get("log_id") is not None and item.get("signal_name")
    }
    merged: list[dict[str, Any]] = []
    for sample in samples:
        item = dict(sample)
        label = label_by_key.get((sample.get("log_id"), sample.get("signal_name")))
        if label:
            item.update(label)
        merged.append(item)
    return merged
```

实际实现需要复用文件现有 import 风格，并确保 `Any` 已导入。

- [x] **步骤 4：运行合并测试绿灯**

运行：

```bash
python -m pytest tests/test_timing_signal_audit.py::test_merge_timing_signal_labels_overrides_by_log_id_and_signal -v
```

预期：通过。

- [x] **步骤 5：编写离线 report 复跑红灯测试**

新增测试，使用 `tmp_path` 写入一个包含 `samples` 的 report JSON 和一个 labels JSONL，然后调用 CLI `main([...])` 或新 helper，断言输出 report 的 `labeled_samples == 2`，`signals.s_ack.false_positive_count == 1`。

运行：

```bash
python -m pytest tests/test_timing_signal_audit.py -k "input_report or labels" -v
```

预期：失败于 CLI 参数不存在。

- [x] **步骤 6：实现 CLI 离线模式**

在 `evals/timing_signal_audit.py` 中增加：

- `--input-report`：读取已有 report JSON 的 `samples`。
- `--labels`：读取 JSON 数组或 JSONL。
- 当存在 `--input-report` 时，不连接 DB，不执行 `query_timing_rows()`。
- 输出格式仍包含 `samples`、`generated_at`、`source`，`source.mode` 为 `input_report`。

- [x] **步骤 7：运行 P3-3A 定向测试**

运行：

```bash
python -m pytest tests/test_timing_signal_audit.py -v
```

预期：全部通过。

- [x] **步骤 8：运行相邻回归**

运行：

```bash
python -m pytest tests/test_timing_signal_audit.py tests/test_timing_gate_prompt_policy.py -v
```

预期：全部通过。

- [x] **步骤 9：同步文档状态**

更新：

- `docs/todo.md` 路线项 10 的 P3-3A 状态。
- `docs/plan_walkthrough.md` 当前详细计划中的 P3-3A 任务状态。
- `docs/superpowers/specs/2026-06-18-timing-gate-continuous-eval-design.md` 验收勾选状态。

- [x] **步骤 10：提交 P3-3A**

运行：

```bash
git diff --check -- core/eval_sampling/timing_signal_audit.py evals/timing_signal_audit.py tests/test_timing_signal_audit.py docs/todo.md docs/plan_walkthrough.md docs/superpowers/specs/2026-06-18-timing-gate-continuous-eval-design.md .Codex/plans/timing-gate-continuous-eval.md
python -m pytest tests/test_timing_signal_audit.py tests/test_timing_gate_prompt_policy.py -v
```

确认通过后只暂存本阶段文件：

```bash
git add core/eval_sampling/timing_signal_audit.py evals/timing_signal_audit.py tests/test_timing_signal_audit.py docs/todo.md docs/plan_walkthrough.md docs/superpowers/specs/2026-06-18-timing-gate-continuous-eval-design.md .Codex/plans/timing-gate-continuous-eval.md
git commit -m "feat(评测): 支持时机信号标注复跑"
```

---

## 任务 2：P3-3B TimingGate 回归门禁

**文件：**
- 修改：`evals/run.py`
- 创建：`evals/baselines/timing_gate.json`
- 创建：`evals/cases/timing_gate/timing_gate_scoring_*.json`
- 创建：`scripts/run_timing_gate_gate.sh`
- 创建：`.github/workflows/timing-gate-eval.yml`
- 修改：`tests/test_eval_baseline.py`
- 修改：`tests/test_timing_gate_prompt_policy.py`
- 创建或修改：`docs/evals.md`

- [ ] **步骤 1：编写 gate 成功路径红灯测试**

在 `tests/test_eval_baseline.py` 新增 `test_eval_run_cli_returns_success_when_gate_passes()`，使用临时 baseline 和 monkeypatch 后的 `REPORTS_DIR`，断言 `main([...]) == 0` 且输出 `Gate passed`。

运行：

```bash
python -m pytest tests/test_eval_baseline.py::test_eval_run_cli_returns_success_when_gate_passes -v
```

预期：如果当前已可通过，则记录为既有能力；否则按失败信息补最小实现。

- [ ] **步骤 2：编写 gate 异常配置测试**

新增两个纯函数测试：

- `max_new_failures` 设置但没有 baseline diff 时失败。
- baseline suite mismatch 时失败。

运行：

```bash
python -m pytest tests/test_eval_baseline.py -k "baseline_required or suite_mismatch" -v
```

预期：第一条应已通过或接近通过；suite mismatch 若已覆盖则保留为回归守卫。

- [ ] **步骤 3：编写正式 suite scoring case 守卫红灯测试**

在 `tests/test_timing_gate_prompt_policy.py` 新增断言，要求正式 `timing_gate` suite 至少有 2 个 case 不含 `input.action`。

运行：

```bash
python -m pytest tests/test_timing_gate_prompt_policy.py::test_timing_gate_eval_suite_contains_rule_scoring_cases -v
```

预期：失败，因为当前正式 suite 全部带 `input.action`。

- [ ] **步骤 4：新增无 action scoring case**

新增 2 到 3 个 `evals/cases/timing_gate/timing_gate_scoring_*.json`：

- `@bot` 请求：期望 `continue`，`scoring.stage=rule_shortcut`，`model_used=false`。
- 指向他人：期望 `no_reply`，关键 `s_other` 或 `suppression_score` 非 0。
- 连续输入碎片：期望 `wait`，关键 `readiness_score` 低于继续阈值。

- [ ] **步骤 5：运行 suite 守卫绿灯**

运行：

```bash
python -m pytest tests/test_timing_gate_prompt_policy.py -v
```

预期：全部通过。

- [ ] **步骤 6：新增稳定 baseline**

运行当前 suite：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m evals.run --suite timing_gate
```

确认 `failed=0` 后，把当前 `SuiteReport` 结构写入 `evals/baselines/timing_gate.json`，只保留稳定字段：

```json
{
  "suite": "timing_gate",
  "total": 18,
  "passed": 18,
  "failed": 0,
  "pass_rate": 1.0,
  "failed_cases": []
}
```

实际 `total` 以新增 case 后的运行结果为准。

- [ ] **步骤 7：新增统一脚本**

创建 `scripts/run_timing_gate_gate.sh`：

```bash
#!/usr/bin/env bash
set -euo pipefail

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY

export PYTHONDONTWRITEBYTECODE=1
export NANOBOT_TESTING="${NANOBOT_TESTING:-1}"
export DATABASE_URL="${DATABASE_URL:-sqlite:///:memory:}"
export NEW_API_KEY="${NEW_API_KEY:-test-key-for-ci}"
export NANOBOT_ADMIN_TOKEN="${NANOBOT_ADMIN_TOKEN:-test-admin-token}"

python -B -m evals.run \
  --suite timing_gate \
  --baseline evals/baselines/timing_gate.json \
  --min-pass-rate 1.0 \
  --max-new-failures 0
```

- [ ] **步骤 8：新增 CI workflow**

创建 `.github/workflows/timing-gate-eval.yml`，使用 Python 3.10，安装 `requirements.txt`，先跑 `python -m pytest tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py -v`，再跑 `bash scripts/run_timing_gate_gate.sh`。

- [ ] **步骤 9：补充 `--no-write-report` 或 `--report-dir`**

若本地验证发现 report 写入副作用影响 CI 或测试，给 `evals/run.py` 增加 `--no-write-report` 或 `--report-dir`。新增参数必须有测试覆盖，并让脚本 / workflow 使用无副作用模式。

- [ ] **步骤 10：补充评测文档**

新增或更新 `docs/evals.md`，包含：

- 本地运行命令。
- CI gate 运行内容。
- baseline 更新条件。
- `min_pass_rate` 和 `max_new_failures` 含义。
- gate 失败时如何判断是新失败、旧失败修复还是 baseline 过期。

- [ ] **步骤 11：运行 P3-3B 定向验证**

运行：

```bash
python -m pytest tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py -v
bash scripts/run_timing_gate_gate.sh
```

预期：全部通过，脚本 exit code 为 0。

- [ ] **步骤 12：运行全量验证**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider
```

预期：0 failures。

- [ ] **步骤 13：同步文档状态**

更新：

- `docs/todo.md` 路线项 10 和路线项 8。
- `docs/plan_walkthrough.md` P3-3B 验证记录。
- `docs/superpowers/specs/2026-06-16-timing-gate-scoring-design.md` 实施状态。
- 本计划任务勾选状态。

- [ ] **步骤 14：提交 P3-3B**

只暂存本阶段文件：

```bash
git add evals/run.py evals/baselines/timing_gate.json evals/cases/timing_gate/timing_gate_scoring_*.json scripts/run_timing_gate_gate.sh .github/workflows/timing-gate-eval.yml tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py docs/evals.md docs/todo.md docs/plan_walkthrough.md docs/superpowers/specs/2026-06-16-timing-gate-scoring-design.md .Codex/plans/timing-gate-continuous-eval.md
git commit -m "ci(评测): 接入 timing gate 回归门禁"
```

---

## 任务 3：P4 交接记录

**文件：**
- 修改：`docs/plan_walkthrough.md`
- 修改：`docs/todo.md`

- [ ] **步骤 1：确认 P3-3 剩余项**

确认 P3-3 只剩运营执行：更多真实样本标注、定期复跑、按报告决策是否调参。

- [ ] **步骤 2：把通用 candidates 闭环留到 P4**

在 P4-1 中明确后续范围：

- `EvalCandidate` promote 按 suite 输出。
- candidates / labeled 数据目录策略。
- Admin 标注导出或 promote 能力。
- per-capability 数据集扩展。

- [ ] **步骤 3：提交文档收口**

运行：

```bash
git diff --check -- docs/plan_walkthrough.md docs/todo.md .Codex/plans/timing-gate-continuous-eval.md
```

只暂存文档：

```bash
git add docs/plan_walkthrough.md docs/todo.md .Codex/plans/timing-gate-continuous-eval.md
git commit -m "docs(计划): 收口 TimingGate 持续评估"
```

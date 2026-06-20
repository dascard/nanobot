# 周期运行 Manifest 设计

## 背景

真实样本运营 1-6 已完成，当前评测体系已有稳定 PR gate、周期性 keep-going 脚本、RAG benchmark 报告和 TimingGate signal audit 报告。后续目标是按周期报告判断是否调参，以及做跨 artifact 趋势分析。

现有周期入口 `scripts/run_eval_periodic.sh` 会运行多个独立步骤，但产物分散：

- 通用 eval suite 写入 `evals/reports/latest.json` 和 `evals/reports/YYYY-MM-DD-<suite>.json`。
- RAG benchmark 写入 `tmp/rag_benchmark/reports/latest.json`、`latest.md` 和带 run id 的 JSON / Markdown。
- TimingGate signal audit 写入 `evals/reports/timing_signal_audit_latest.json`，缺少真实 DB 时写 skipped JSON。
- GitHub Actions artifact 只上传 glob，没有描述一次周期运行包含哪些报告、哪些步骤失败、哪些步骤被跳过。

因此，后续如果直接读取 `latest.json` 或按文件名扫描历史报告，容易把不同运行的产物混在一起。第一步应补一个周期运行 manifest，把一次周期复跑的步骤、退出状态、报告路径和摘要指标固定下来。

## 目标

为 `scripts/run_eval_periodic.sh` 增加统一 manifest。manifest 用于记录一次周期运行的完整证据，作为后续跨 artifact 趋势和按周期报告调参的稳定输入。

第一版只落文件，不新增 Admin API，不改 WebUI，不调 live 参数。

## 方案选择

### 方案 A：只扩展 Bash，直接拼 JSON

优点是改动少，不需要新增 Python 模块。缺点是 Bash 拼 JSON 容易出转义和缺字段问题，也难以复用已有报告摘要解析。

结论：不采用。

### 方案 B：新增 Python helper，由周期脚本传入步骤结果

周期脚本继续负责 keep-going 编排；每个 `run_step` 结束后追加一行 JSONL 步骤记录。脚本结束前调用 `python -B -m evals.periodic_manifest`，由 Python helper 读取步骤记录和报告文件，写入 manifest。

优点是脚本仍简单，JSON 结构由 Python 保证，测试可以覆盖纯函数和 CLI。缺点是新增一个小模块。

结论：采用。

### 方案 C：先做 Admin API / WebUI 聚合

优点是运营入口更完整。缺点是没有底层 manifest 时，API 只能继续扫描零散文件，容易复制脆弱逻辑。

结论：暂不采用，后续在 manifest 稳定后再做。

## Manifest 契约

默认输出：

- `evals/reports/periodic_manifest_latest.json`
- `evals/reports/YYYY-MM-DD-periodic_manifest.json`
- `evals/reports/runs/<run_id>/manifest.json`

顶层字段：

```json
{
  "manifest_version": 1,
  "run_id": "20260620_180000_local",
  "run_type": "periodic",
  "trigger": "local",
  "started_at": "2026-06-20T18:00:00+08:00",
  "finished_at": "2026-06-20T18:05:00+08:00",
  "exit_code": 0,
  "status": "passed",
  "git": {
    "sha": "",
    "ref": "",
    "repository": ""
  },
  "artifacts": [
    "evals/reports/*.json",
    "tmp/rag_benchmark/reports/*.json",
    "tmp/rag_benchmark/reports/*.md"
  ],
  "steps": []
}
```

步骤字段：

```json
{
  "name": "capability rendering contract",
  "kind": "eval_suite",
  "suite": "capability_rendering_contract",
  "status": "passed",
  "exit_code": 0,
  "baseline_path": "evals/baselines/capability_rendering_contract.json",
  "report_paths": [
    "evals/reports/2026-06-20-capability_rendering_contract.json"
  ],
  "summary": {
    "total": 5,
    "passed": 5,
    "failed": 0,
    "pass_rate": 1.0
  },
  "gate_passed": true,
  "new_failed_cases": [],
  "failed_cases": []
}
```

`steps[].kind` 第一版固定为：

- `pytest_guard`
- `eval_suite`
- `rag_benchmark`
- `timing_signal_audit`

RAG step 的 `summary` 从 `metrics.overall` 提取 `total_cases`、`pass_rate`、`hit@5`、`mrr`、`positive_cases`。TimingGate signal audit step 的 `summary` 提取 `total_samples`、`labeled_samples`、`shadow.action_mismatch_count`、`shadow.action_mismatch_rate`，并在 skipped 时记录 `notes.reason`。

## 写入流程

`scripts/run_eval_periodic.sh` 负责：

1. 创建本次 `run_id` 和临时步骤 JSONL。
2. 每个 `run_step` 运行命令后，记录 `name`、`kind`、`suite`、`baseline_path`、`report_path`、`exit_code`。
3. 继续保持 keep-going：失败步骤只把总 `status` 置为失败，不阻断后续步骤。
4. 结束前调用 `python -B -m evals.periodic_manifest` 写 manifest。
5. 最终退出码仍等于累计状态，保持周期 workflow 当前语义。

`evals.periodic_manifest` 负责：

1. 读取步骤 JSONL。
2. 按步骤引用的报告路径读取摘要。
3. 生成 `periodic_manifest_latest.json`、dated manifest 和 run-scoped manifest。
4. 对缺失报告容错：步骤保留 `status` / `exit_code`，`report_missing=true`，不让 manifest 写入失败覆盖原始 gate 结果。

## 边界

本阶段做：

- 新增 manifest helper 和 CLI。
- 接入 `scripts/run_eval_periodic.sh`。
- 把 manifest 路径纳入 GitHub Actions artifact 上传。
- 在 `docs/evals.md`、`docs/todo.md`、`docs/plan_walkthrough.md` 记录状态。

本阶段不做：

- 不改 `run_eval_pr_gate.sh` 的 fail-fast 语义。
- 不修改 `evals/baselines/*.json`。
- 不调 TimingGate、RAG 或 capability gate 阈值。
- 不新增 Admin API 或 WebUI 页面。
- 不解析历史 artifact 做趋势图。
- 不批量重写既有历史报告。

## 测试策略

TDD 入口：

- `tests/test_eval_baseline.py`
  - manifest helper 能从通用 eval、RAG 和 TimingSignal 报告提取摘要。
  - 周期脚本包含 manifest 步骤记录和 `periodic_manifest_latest.json` 输出。
  - workflow artifact 上传包含 `evals/reports/periodic_manifest_*.json` 和 `evals/reports/runs/**/manifest.json`。

- `tests/test_timing_signal_audit_periodic.py`
  - 缺 DB 时 TimingSignal audit 写 skipped 报告；manifest 能把 skipped reason 索引到对应 step。

验证命令：

```bash
python -B -m pytest tests/test_eval_baseline.py::test_periodic_manifest_builds_step_summaries -q -p no:cacheprovider
python -B -m pytest tests/test_eval_baseline.py::test_eval_periodic_script_writes_manifest tests/test_eval_baseline.py::test_eval_workflow_uploads_periodic_manifest -q -p no:cacheprovider
python -B -m pytest tests/test_eval_baseline.py tests/test_timing_signal_audit_periodic.py -q -p no:cacheprovider
bash scripts/run_eval_periodic.sh
python -B -m pytest tests/ -q -p no:cacheprovider
```

## 后续

manifest 稳定后，下一阶段可以做两件事：

- 跨 artifact 趋势：读取多个 `periodic_manifest`，再按 manifest 指向的通用 eval、RAG 和 TimingSignal 报告做趋势。
- 按周期报告调参：只读分析 manifest 和 TimingSignal labeled report，先生成调参建议，不直接改 live 常量。

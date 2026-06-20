# 跨 artifact 周期趋势设计

> 2026-06-20 · 基于周期运行 manifest 生成只读趋势报告，为后续人工复盘和调参分析提供稳定输入。

## 背景

真实样本运营 1-7 已完成。当前周期评测会写出统一 manifest：

- `evals/reports/periodic_manifest_latest.json`
- `evals/reports/YYYY-MM-DD-periodic_manifest.json`
- `evals/reports/runs/<run_id>/manifest.json`

manifest 已经索引一次周期运行的 run 元信息、步骤状态、报告路径和摘要指标。下一步需要跨多个 manifest 观察趋势，例如某个 suite pass rate 是否下降、RAG `hit@5` 是否退化、TimingSignal audit 的 action mismatch 是否升高。

本阶段只做只读趋势聚合。它不替代人工调参，不改变 PR gate 或周期 gate，也不更新 baseline。

## 目标

新增一个跨 artifact 周期趋势报告：

- 从多个 periodic manifest 读取历史周期运行。
- 按 `run_id` 去重，按 `started_at` 排序。
- 生成 run、通用 eval suite、RAG benchmark、TimingSignal audit 的时间序列。
- 计算 latest vs previous 的关键指标 delta。
- 输出 `evals/reports/artifact_trends_latest.json`。
- 通过 CLI 支持本地和 CI artifact 下载后的离线复盘。

第一版提供机器可读 JSON，不新增 Admin API 和 WebUI。

## 输入

默认输入是 manifest glob：

- `evals/reports/*-periodic_manifest.json`
- `evals/reports/runs/*/manifest.json`

CLI 允许多次传入 `--manifest-glob`。如果调用者显式传入 `evals/reports/periodic_manifest_latest.json`，工具也可以读取，但第一版默认不依赖 latest 文件。

输入合同：

- 只接受 `manifest_version=1` 的 periodic manifest。
- 顶层 `run_id` 为空的 manifest 不参与聚合。
- 相同 `run_id` 出现多次时保留较完整的一份；如果完整度相同，保留排序后最后读到的一份。
- 按 `started_at` 字符串排序；缺失时落到空字符串，仍保留该 run。
- 不沿着历史 manifest 中的 `report_paths` 回读 `latest.json`，避免把可变 latest 当成历史事实。
- RAG `.md` 报告只供人读，不参与机器趋势解析。

## 输出

默认输出路径：

```text
evals/reports/artifact_trends_latest.json
```

报告结构：

```json
{
  "trend_version": 1,
  "source": {
    "manifest_count": 2,
    "run_count": 2,
    "manifest_globs": ["evals/reports/*-periodic_manifest.json"],
    "deduped_run_ids": ["run_1", "run_2"]
  },
  "summary": {
    "latest_run_id": "run_2",
    "previous_run_id": "run_1",
    "latest_status": "failed",
    "failed_run_count": 1,
    "latest_failed_step_count": 1
  },
  "series": {
    "runs": [],
    "eval_suites": {},
    "rag_benchmark": [],
    "timing_signal_audit": []
  },
  "regressions": []
}
```

### Run 序列

每个 run item 包含：

- `run_id`
- `started_at`
- `finished_at`
- `status`
- `exit_code`
- `duration_sec`
- `failed_step_count`
- `git`
- `trigger`

`duration_sec` 从 `started_at` 和 `finished_at` 解析得到；无法解析时为 `null`。

### 通用 eval suite 序列

`series.eval_suites` 以 suite 名称分组。每个 item 包含：

- `run_id`
- `suite`
- `status`
- `exit_code`
- `gate_passed`
- `report_missing`
- `total`
- `passed`
- `failed`
- `pass_rate`
- `pass_rate_delta`
- `failed_delta`
- `new_failed_count`
- `failed_cases`

`pass_rate_delta` 和 `failed_delta` 只与同一 suite 的上一次样本比较。第一条样本 delta 为 `null`。

### RAG benchmark 序列

每个 item 包含：

- `run_id`
- `status`
- `exit_code`
- `gate_passed`
- `report_missing`
- `total_cases`
- `positive_cases`
- `pass_rate`
- `pass_rate_delta`
- `hit@5`
- `hit@5_delta`
- `mrr`
- `mrr_delta`

RAG 趋势只读取 manifest step summary，不回读 `tmp/rag_benchmark/reports/latest.json`。

### TimingSignal audit 序列

每个 item 包含：

- `run_id`
- `status`
- `exit_code`
- `report_missing`
- `total_samples`
- `labeled_samples`
- `label_coverage_rate`
- `action_mismatch_count`
- `action_mismatch_count_delta`
- `action_mismatch_rate`
- `action_mismatch_rate_delta`
- `notes`

第一版使用 manifest 里已有的 TimingSignal summary。完整 per-signal、`mismatches_by_signal`、`scoring_stage`、`model_used` 和 `model_action` 聚合需要更厚的 manifest summary 或回读不可变报告，后续另起阶段处理。

## 回归判定

第一版只做 latest vs previous 的轻量回归提示，不影响退出码。

生成 `regressions` 的规则：

- 最新 run `status != passed`：`run_failed`
- 最新 step `gate_passed is false`：`gate_failed`
- 通用 eval suite `pass_rate_delta < 0`：`eval_pass_rate_drop`
- 通用 eval suite `failed_delta > 0`：`eval_failed_count_increase`
- 通用 eval suite `new_failed_count > 0`：`eval_new_failures`
- RAG `pass_rate_delta < 0`：`rag_pass_rate_drop`
- RAG `hit@5_delta < 0`：`rag_hit_at_5_drop`
- RAG `mrr_delta < 0`：`rag_mrr_drop`
- TimingSignal `action_mismatch_count_delta > 0`：`timing_action_mismatch_count_increase`
- TimingSignal `action_mismatch_rate_delta > 0`：`timing_action_mismatch_rate_increase`
- 任意最新 step `report_missing=true`：`report_missing`

每条回归提示必须携带 `run_id`，能定位到 suite 或 kind 的也带上 `suite` 或 `kind`。

## 模块边界

新增模块：

- `evals/artifact_trends.py`

公开函数：

- `load_periodic_manifests(globs: list[str]) -> list[dict]`
- `dedupe_manifests(manifests: list[dict]) -> list[dict]`
- `build_artifact_trends(manifests: list[dict], manifest_globs: list[str] | None = None) -> dict`
- `write_trend_report(payload: dict, out_path: str | Path) -> Path`

CLI：

```bash
python -B -m evals.artifact_trends \
  --manifest-glob 'evals/reports/*-periodic_manifest.json' \
  --manifest-glob 'evals/reports/runs/*/manifest.json' \
  --out evals/reports/artifact_trends_latest.json
```

CLI 只负责读取、聚合、写文件和打印输出路径；趋势回归不改变退出码。输入为空时仍写合法空报告，方便首次运行和本地环境验证。

## 不做范围

- 不新增 Admin API。
- 不新增 WebUI。
- 不修改 `manifest_version=1` 的既有字段语义。
- 不修改 `scripts/run_eval_periodic.sh` 的 keep-going 和退出码逻辑。
- 不修改 PR gate、周期 gate 或 workflow 阻断条件。
- 不更新 eval / RAG baseline。
- 不调整 TimingGate、RAG 或 capability 阈值。
- 不批量重写历史报告。
- 不回读可变 `latest.json` 作为历史数据。
- 不把趋势提示自动转成调参动作。

## 测试策略

新增 `tests/test_eval_artifact_trends.py`。

红灯用例：

1. 构造两个 manifest，断言排序、去重、run series 和三类 step series。
2. 构造 latest vs previous 退化，断言 `pass_rate_delta`、`hit@5_delta`、`mrr_delta` 和 TimingSignal mismatch delta。
3. 构造 RAG `report_paths` 都指向同一个 `latest.json`，断言趋势只使用 manifest summary，不回读该文件。
4. 构造未知 `kind` 或空 summary，断言工具不报错，只保留通用 run / step 状态。
5. 通过 CLI 写 `artifact_trends_latest.json`，断言输出包含 `trend_version`、`source.manifest_count`、`series` 和 `regressions`。

阶段性验证命令：

```bash
python -B -m pytest tests/test_eval_artifact_trends.py -q -p no:cacheprovider
python -B -m pytest tests/test_eval_artifact_trends.py tests/test_eval_baseline.py tests/test_timing_signal_audit_periodic.py -q -p no:cacheprovider
```

最终验证仍按项目约定运行全量测试：

```bash
python -B -m pytest tests/ -q -p no:cacheprovider
```

## 后续扩展

后续如果要支撑 TimingGate 调参报告，需要先增强不可变 artifact 或 manifest summary，再做单独设计：

- per-signal 样本量、标注量、FP rate 和 action 分布。
- `mismatches_by_signal`。
- `scoring_stage`、`model_used`、`model_action` 聚合。
- score、threshold、margin 和 delay 的分布。
- 参数模拟和人工标注仲裁。

这些内容不进入第一版趋势报告。

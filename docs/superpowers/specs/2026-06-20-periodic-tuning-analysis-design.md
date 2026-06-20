# 周期趋势报告只读调参分析设计

> 2026-06-20 · 基于跨 artifact 周期趋势、周期 manifest 和 TimingSignal raw audit 生成只读分析报告，把趋势退化转成复核、补标注、补 artifact 或暂不调整建议。

## 背景

真实样本运营 1-8 已完成。当前评测运营链路已经具备：

- 周期运行 manifest：记录一次周期复跑的 run 信息、步骤状态、报告路径和摘要指标。
- TimingSignal audit：从真实 ChatLog 抽取 `s_ack`、`s_transport`、`w_marker` 样本，聚合假阳率、标注覆盖率和 runtime / scoring action mismatch。
- 跨 artifact 趋势：`evals.artifact_trends` 从多个 periodic manifest 生成 `artifact_trends_latest.json`，展示 run、通用 eval suite、RAG benchmark 和 TimingSignal audit 的跨 run 漂移。

这些 artifact 能说明「哪里可能退化」，但还不能直接说明「参数应该改成多少」。TimingGate scoring 的核心参数（`β/θ/m/λ/κ`）需要更完整的 score 分布、阈值边界样本、人工 truth 和参数模拟结果；现有 raw audit 主要覆盖信号假阳标签，不等价于最终动作真值。

因此下一阶段先新增只读调参分析报告：读取已有趋势和 raw audit，把可解释证据整理为机器可读建议，供人工复核。第一版不自动调参，不更新 baseline，不改变 PR gate 或周期 gate。

## 目标

新增一个只读分析入口，输出 `evals/reports/tuning_analysis_latest.json`：

- 判断当前趋势数据是否足以支持分析。
- 汇总 TimingSignal raw audit 的样本量、标注覆盖率、per-signal 假阳率和 mismatch 证据。
- 汇总 eval suite、RAG benchmark 和 TimingSignal 的退化引用。
- 给出 `no_change`、`label_more_samples`、`collect_more_artifact`、`manual_review` 或 `candidate_adjustment` 类型建议。
- 明确区分「可复核线索」和「可执行参数变更」。

第一版的 `candidate_adjustment` 只表示「可以进入人工调参讨论的候选方向」，不能包含可直接应用的参数值，也不能被任何脚本自动应用。

## 方案选择

### 方案 A：只读 `artifact_trends_latest.json`

优点是实现简单，输入稳定，历史事实不会被可变 `latest.json` 污染。缺点是趋势报告只包含 manifest summary，缺少 per-signal 假阳率、样本文本、`mismatches_by_signal` 和人工标注细节，无法判断某个信号提取器是否需要复核。

### 方案 B：只读 raw `timing_signal_audit_latest.json`

优点是能看到样本证据和 per-signal 统计。缺点是它只能代表一次审计，不能判断跨周期 run / RAG / eval suite 是否退化，也无法定位周期步骤失败、报告缺失和 gate 状态。

### 方案 C：趋势报告 + raw audit + 可选 manifest

这是推荐方案。趋势报告提供跨周期稳定摘要，raw audit 提供最新或显式指定 run 的信号证据，manifest 提供步骤状态和报告索引。分析器只读这些 artifact，并在证据不足时输出阻断原因，而不是推测参数。

## 输入

默认输入：

- `evals/reports/artifact_trends_latest.json`
- `evals/reports/timing_signal_audit_latest.json`
- `evals/reports/periodic_manifest_latest.json`

CLI 支持显式路径：

```bash
python -B -m evals.tuning_analysis \
  --trends evals/reports/artifact_trends_latest.json \
  --timing-audit evals/reports/timing_signal_audit_latest.json \
  --manifest evals/reports/periodic_manifest_latest.json \
  --out evals/reports/tuning_analysis_latest.json
```

输入规则：

- `--trends` 是主输入，要求 `trend_version == 1`。
- `--timing-audit` 缺省时可从 `--manifest` 最新 `timing_signal_audit` step 的 `report_paths` 中解析第一份存在的 JSON；仍找不到时记录 `timing_audit_missing`。
- raw audit 只用于最新或显式指定路径，不把历史 manifest 中指向的可变 `latest.json` 当作历史事实。
- `--manifest` 是可选输入，用于补充最新周期步骤的 `report_missing`、`notes.mode`、`notes.reason` 和 `report_paths`。
- 不读取生产 DB，不调用 eval runner，不重新运行 RAG benchmark，不修改任何输入 artifact。

## 输出

默认输出路径：

```text
evals/reports/tuning_analysis_latest.json
```

报告结构：

```json
{
  "analysis_version": 1,
  "generated_at": "2026-06-20T21:00:00",
  "source": {
    "trend_version": 1,
    "run_count": 3,
    "latest_run_id": "20260620_205312_local",
    "previous_run_id": "20260619_205312_schedule",
    "trends_path": "evals/reports/artifact_trends_latest.json",
    "timing_audit_path": "evals/reports/timing_signal_audit_latest.json",
    "manifest_path": "evals/reports/periodic_manifest_latest.json"
  },
  "readiness": {
    "ready": false,
    "blocking_reasons": [
      {
        "code": "insufficient_runs",
        "message": "至少需要 3 个周期 run 才生成调参候选建议"
      }
    ]
  },
  "summary": {
    "recommendation_count": 0,
    "must_review_count": 0,
    "no_change_count": 0,
    "label_more_samples_count": 0,
    "collect_more_artifact_count": 0
  },
  "signals": [],
  "recommendations": [],
  "regression_refs": []
}
```

### Source

`source` 记录输入来源和关键 run：

- `trend_version`
- `run_count`
- `latest_run_id`
- `previous_run_id`
- `trends_path`
- `timing_audit_path`
- `manifest_path`
- `timing_audit_mode`
- `timing_audit_reason`

### Readiness

`readiness.ready` 表示是否具备生成候选建议的最低证据。即使 `ready=false`，报告仍可输出补数据建议和回归引用。

阻断原因使用稳定 code：

- `trends_missing`
- `unsupported_trend_version`
- `insufficient_runs`
- `timing_audit_missing`
- `timing_audit_skipped`
- `timing_zero_samples`
- `low_label_coverage`
- `manifest_missing`
- `latest_report_missing`

第一版默认阈值：

- `min_runs = 3`
- `min_total_samples = 20`
- `min_label_coverage = 0.30`
- `min_signal_labeled_samples = 5`
- `high_false_positive_rate = 0.20`

这些阈值只控制分析报告的「证据是否足够」和「是否需要复核」，不控制线上回复行为。

### Signals

`signals[]` 按 raw audit 的 `signals` 聚合：

```json
{
  "name": "s_ack",
  "samples": 12,
  "labeled_samples": 8,
  "label_coverage_rate": 0.666667,
  "false_positive_rate": 0.25,
  "suggestion": "review_threshold",
  "runtime_actions": {
    "no_reply": 7,
    "reply_now": 1
  },
  "mismatch_count": 2,
  "evidence_samples": [
    {
      "log_id": 123,
      "signal_value": 0.85,
      "runtime_action": "no_reply",
      "scoring_action": "reply_now",
      "action_mismatch": true,
      "text_preview": "好的，再帮我查下……"
    }
  ]
}
```

证据样本选择规则：

- 优先选择 `action_mismatch=true` 的样本。
- 其次选择已标注 `false_positive` 的样本。
- 最多保留每个 signal 3 条证据。
- `text_preview` 保留 raw audit 中已经截断的文本，不再展开原始日志。

### Recommendations

建议项结构：

```json
{
  "type": "manual_review",
  "area": "timing_signal",
  "severity": "medium",
  "reason_code": "high_false_positive_rate",
  "message": "s_ack 标注样本假阳率达到 25%，需要人工复核信号提取器",
  "evidence": {
    "signal": "s_ack",
    "false_positive_rate": 0.25,
    "labeled_samples": 8,
    "sample_log_ids": [123, 456]
  }
}
```

`type` 取值：

- `no_change`：当前证据稳定，不建议调整。
- `label_more_samples`：已有样本但标注覆盖不足。
- `collect_more_artifact`：run 数、样本数或不可变 artifact 不足。
- `manual_review`：出现退化、假阳率偏高、mismatch 增加或 gate 失败，需要人工复核。
- `candidate_adjustment`：证据足以进入调参讨论，但仍不包含可直接应用的参数值。

`area` 取值：

- `timing_signal`
- `timing_shadow`
- `rag_benchmark`
- `eval_suite`
- `artifact_health`

`severity` 取值：

- `info`
- `low`
- `medium`
- `high`

第一版建议生成规则：

- `unsupported_trend_version`：输出 `collect_more_artifact`，阻断候选调整。
- `insufficient_runs`：输出 `collect_more_artifact`。
- `timing_audit_missing` / `timing_audit_skipped`：输出 `collect_more_artifact`。
- `timing_zero_samples`：输出 `collect_more_artifact`。
- 全局或 per-signal 标注覆盖不足：输出 `label_more_samples`。
- per-signal `false_positive_rate >= high_false_positive_rate` 且标注样本量足够：输出 `manual_review`。
- `action_mismatch_count_delta > 0` 或 `action_mismatch_rate_delta > 0`：输出 `manual_review`。
- RAG `hit@5_delta < 0`、`mrr_delta < 0` 或 `pass_rate_delta < 0`：输出 `manual_review`。
- eval suite `pass_rate_delta < 0`、`failed_delta > 0` 或 `new_failed_count > 0`：输出 `manual_review`。
- 最新 run、step gate 或 report 缺失异常：输出 `manual_review` 或 `collect_more_artifact`。
- 所有关键指标稳定，且没有 blocking reason：输出 `no_change`。

### Regression Refs

`regression_refs[]` 原样保留趋势报告的 `regressions`，并可以补充来源路径：

```json
{
  "type": "rag_mrr_drop",
  "run_id": "20260620_205312_local",
  "delta": -0.12,
  "source": "artifact_trends"
}
```

`regression_refs` 只作为证据引用，不直接变更参数、不更新 baseline，也不改变门禁退出码。

## 模块边界

新增模块：

- `evals/tuning_analysis.py`

公开函数：

- `load_json_object(path: str | Path) -> dict[str, Any]`
- `resolve_timing_audit_path(manifest: dict[str, Any] | None, explicit_path: str | Path | None) -> Path | None`
- `build_tuning_analysis(trends: dict[str, Any], *, timing_audit: dict[str, Any] | None = None, manifest: dict[str, Any] | None = None, source_paths: dict[str, str] | None = None, min_runs: int = 3, min_total_samples: int = 20, min_label_coverage: float = 0.30, min_signal_labeled_samples: int = 5, high_false_positive_rate: float = 0.20) -> dict[str, Any]`
- `write_tuning_analysis(payload: dict[str, Any], out_path: str | Path) -> Path`

CLI：

```bash
python -B -m evals.tuning_analysis \
  --trends evals/reports/artifact_trends_latest.json \
  --timing-audit evals/reports/timing_signal_audit_latest.json \
  --manifest evals/reports/periodic_manifest_latest.json \
  --out evals/reports/tuning_analysis_latest.json
```

CLI 行为：

- 输入缺失时尽量写出合法报告，并在 `readiness.blocking_reasons` 说明原因。
- JSON 格式错误或顶层不是对象时返回非 0，因为这表示 artifact 损坏。
- 不提供 `--apply`、`--update-baseline`、`--set-threshold` 或任何会写生产配置的参数。
- 不因为发现 `manual_review` 或 `candidate_adjustment` 改变退出码；是否阻断由未来独立 gate 设计决定。

## 不做范围

- 不调整 TimingGate scoring 公式和参数，包括 `β/θ/m/λ/κ`。
- 不输出可直接应用的参数值。
- 不更新 eval、RAG 或 TimingGate baseline。
- 不修改 PR gate、周期 gate、workflow 阻断条件或脚本退出码。
- 不新增 Admin API。
- 不新增 WebUI。
- 不读取或修改生产 DB。
- 不运行新的采样、标注、RAG 或 eval 任务。
- 不批量重写历史报告。
- 不把 `artifact_trends.regressions` 自动转成参数调整。
- 不扩大 TimingSignal audit 采样信号集合。

## 测试策略

新增 `tests/test_periodic_tuning_analysis.py`。

红灯用例：

1. `trend_version != 1` 时报告包含 `unsupported_trend_version`，不生成候选调整。
2. run 数少于 3 时报告包含 `insufficient_runs` 和 `collect_more_artifact`。
3. TimingSignal audit 缺失、skipped 或 `total_samples=0` 时分别输出对应 blocking reason。
4. 全局标注覆盖率低于 30% 时输出 `label_more_samples`。
5. `s_ack.false_positive_rate >= 0.20` 且标注量足够时输出 `manual_review`，并包含样本证据。
6. TimingSignal mismatch count 或 rate 上升时输出 `manual_review`。
7. RAG `hit@5`、`mrr` 或 `pass_rate` 下降时输出 `manual_review`。
8. 通用 eval suite pass rate 下降、新增失败或失败数上升时输出 `manual_review`。
9. `artifact_trends.regressions` 只进入 `regression_refs`，不会直接生成参数写入动作。
10. CLI 写出 `tuning_analysis_latest.json`，输出包含 `analysis_version`、`source`、`readiness`、`recommendations` 和 `regression_refs`。

阶段性验证命令：

```bash
python -B -m pytest tests/test_periodic_tuning_analysis.py -q -p no:cacheprovider
python -B -m pytest tests/test_periodic_tuning_analysis.py tests/test_eval_artifact_trends.py tests/test_timing_signal_audit.py -q -p no:cacheprovider
```

最终验证仍按项目约定运行全量测试：

```bash
python -B -m pytest tests/ -q -p no:cacheprovider
```

## 验收标准

- 设计和实现后，`evals.tuning_analysis` 可以在没有真实 DB 的本地环境生成合法只读报告。
- 当输入 artifact 不足时，报告给出明确 blocking reason，而不是生成调参结论。
- 当 raw audit 有足够 per-signal 标注且假阳率偏高时，报告能定位到 signal 和证据样本。
- 当 RAG、eval suite 或 TimingSignal 趋势退化时，报告能引用对应 run、suite 和 delta。
- 输出报告不包含可自动应用的参数变更，不修改任何 baseline 或 gate。

## 后续扩展

如果需要从「只读分析」推进到「可审核调参提案」，必须先补充更厚的不可变 artifact：

- `participation_score`、`final_score`、`theta`、`margin`、`conflict_score` 和 `soft_reject_cap` 分布。
- 每个样本命中的 `d0`、`linger`、`s_*`、`w_*`、模型动作和模型置信度。
- 人工最终动作 truth，而不只是信号假阳标签。
- 参数模拟报告：旧参数、新参数、翻转样本、预期收益和风险。
- 调参提案的人工审批记录。

这些内容应单独设计，不进入第一版只读分析。

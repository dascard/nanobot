# TimingSignal 不可变 Artifact 加厚设计

> 2026-06-20 · 在周期评测中为 TimingSignal audit 增加 run-scoped / dated 不可变报告，补齐调参分析前的证据链。

## 背景

当前真实样本运营已完成周期运行 manifest、跨 artifact 趋势和只读调参分析。调参分析已经能识别 run 数不足、TimingSignal audit 缺失、skipped、零样本、低标注覆盖和薄样本等阻断条件，但周期审计报告仍主要依赖 `evals/reports/timing_signal_audit_latest.json`。`latest` 适合本地查看，不适合作为跨周期复核和调参提案的唯一证据，因为它会被后续运行覆盖。

本阶段要把 TimingSignal audit 报告变成可追溯的不可变 artifact，同时保持现有 `latest` 兼容入口不变。

## 目标

1. 周期运行继续写 `evals/reports/timing_signal_audit_latest.json`，保证现有文档、CLI 和调参分析默认路径兼容。
2. 同一轮运行额外写出 dated 报告：`evals/reports/YYYY-MM-DD-timing_signal_audit.json`。
3. 同一轮运行额外写出 run-scoped 报告：`evals/reports/runs/<run_id>/timing_signal_audit.json`。
4. `periodic_manifest` 的 TimingSignal step 必须索引这些 JSON 报告路径，优先包含 run-scoped / dated 路径，再包含 latest 路径。
5. GitHub workflow artifact 上传规则必须包含 run-scoped TimingSignal audit 报告。
6. 缺少真实 DB 时，三个输出路径都写出同一份 skipped 报告，且脚本退出码仍为 0。

## 非目标

- 不读取新的生产数据源。
- 不自动调整 `β/θ/m/λ/κ` 等 TimingGate 参数。
- 不生成可执行调参 proposal。
- 不更新 baseline。
- 不改变 PR gate 或周期 gate 的通过条件。
- 不修改 `evals.tuning_analysis` 的默认 `--timing-audit` 路径；manifest 解析只作为显式输入时的补充。

## 设计

### 输出路径

`scripts/run_eval_periodic.sh` 已经生成 `PERIODIC_RUN_ID` 和 `PERIODIC_REPORT_DATE`。本阶段在该脚本中派生：

- `TIMING_SIGNAL_AUDIT_LATEST_OUT=evals/reports/timing_signal_audit_latest.json`
- `TIMING_SIGNAL_AUDIT_DATED_OUT=evals/reports/${PERIODIC_REPORT_DATE}-timing_signal_audit.json`
- `TIMING_SIGNAL_AUDIT_RUN_OUT=evals/reports/runs/${PERIODIC_RUN_ID}/timing_signal_audit.json`

`scripts/run_timing_signal_audit_periodic.sh` 继续接受 `TIMING_SIGNAL_AUDIT_OUT` 作为主输出，并新增可选的 `TIMING_SIGNAL_AUDIT_EXTRA_OUTS`，用 `:` 分隔额外输出路径。脚本先写主输出，再把同一 payload 复制到额外输出路径。

选择复制而不是重复运行 audit，是为了保证同一轮 run 的 latest、dated 和 run-scoped payload 完全一致，避免采样窗口或时间戳漂移。

### manifest 索引

`scripts/run_eval_periodic.sh` 的 TimingSignal step 记录：

```text
evals/reports/runs/<run_id>/timing_signal_audit.json
evals/reports/YYYY-MM-DD-timing_signal_audit.json
evals/reports/timing_signal_audit_latest.json
```

`evals.periodic_manifest` 不需要改变 schema。现有 `report_paths` 已经支持多个路径，`_load_first_report()` 会读取第一个存在的 JSON 并生成摘要。把 run-scoped 路径放在第一位，可以让 manifest 摘要优先基于不可变报告。

### workflow artifact

`.github/workflows/timing-gate-eval.yml` 追加：

```text
evals/reports/runs/**/timing_signal_audit.json
```

保留 `evals/reports/*.json`，使 dated 和 latest 继续归档。

## 验收

1. `tests/test_timing_signal_audit_periodic.py` 覆盖缺 DB 时主输出和额外输出都存在，payload 都是 `source.mode=skipped`。
2. `tests/test_eval_baseline.py` 覆盖周期脚本的 TimingSignal step report paths 包含 run-scoped、dated 和 latest 三类路径，并且 run-scoped 路径排在 latest 之前。
3. `tests/test_eval_baseline.py` 覆盖 workflow artifact glob 包含 run-scoped TimingSignal audit 报告。
4. 定向验证：

```bash
python -B -m pytest tests/test_timing_signal_audit_periodic.py tests/test_eval_baseline.py -q -p no:cacheprovider
```

5. 周期脚本 smoke：

```bash
TIMING_SIGNAL_AUDIT_DB=tmp/missing-timing-audit.db \
PERIODIC_RUN_ID=design_smoke \
bash scripts/run_eval_periodic.sh
```

6. 最终提交前运行：

```bash
python -B -m pytest tests/ -q -p no:cacheprovider
```

## 风险与约束

- `TIMING_SIGNAL_AUDIT_EXTRA_OUTS` 使用 `:` 分隔，路径中不支持冒号；当前项目路径均为相对路径，满足约束。
- 复制 payload 时必须先创建父目录，防止 run-scoped 路径缺目录导致周期脚本失败。
- 不可变 artifact 只保证路径不会被 `latest` 覆盖；同一天多次本地运行的 dated 报告仍会覆盖，真正不可变证据以 `runs/<run_id>/timing_signal_audit.json` 为准。

# 评测与门禁

本文记录当前仓库内 `evals/` 的稳定入口和 TimingGate 门禁规则。

## TimingGate 门禁

本地运行：

```bash
bash scripts/run_timing_gate_gate.sh
```

脚本固定执行：

```bash
python -B -m evals.run --suite timing_gate --baseline evals/baselines/timing_gate.json --min-pass-rate 1.0 --max-new-failures 0
```

门禁含义：

- `--min-pass-rate 1.0`：正式 `timing_gate` suite 必须全部通过。
- `--max-new-failures 0`：相对 baseline 不允许出现新增失败 case。
- `evals/baselines/timing_gate.json`：仓库内稳定基线，不依赖会被覆盖的 `evals/reports/latest.json`。

## CI 入口

`.github/workflows/timing-gate-eval.yml` 在 PR 和主分支 push 上运行：

1. `python -B -m pytest tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py -v -p no:cacheprovider`
2. `bash scripts/run_timing_gate_gate.sh`

Workflow 显式设置 `NANOBOT_TESTING`、`DATABASE_URL`、`NEW_API_KEY` 和 `NANOBOT_ADMIN_TOKEN`，避免测试导入配置时写入 `.env`。

## Baseline 更新规则

只有同时满足以下条件时，才能更新 `evals/baselines/timing_gate.json`：

- 新增或修改的正式 case 已经人工审查，且属于预期行为变化。
- `python -B -m evals.run --suite timing_gate` 当前结果为 `failed=0`。
- 更新后的 `total`、`passed`、`failed`、`pass_rate` 与当前 suite 输出一致。
- 相关行为变化已经有测试或 case 覆盖，不能只刷新 baseline 掩盖回归。

更新后必须运行：

```bash
python -m pytest tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py -v
bash scripts/run_timing_gate_gate.sh
```

## 失败处理

- `pass_rate below threshold`：当前 suite 有失败。先看 `Failed:` 列表，修 case 或修实现。
- `new_failed_cases exceeds threshold`：当前失败不在 baseline 中，是新回归。默认不允许合入。
- `baseline suite mismatch`：baseline 文件不是当前 suite 的基线，需要改回正确路径。
- `fixed_cases` 非空且无新增失败：说明旧失败已修复，可以在审查后刷新 baseline。

## 与 P4 的边界

TimingGate 门禁只负责固定 suite 的确定性回归。通用 `candidates → labeled` 标注闭环、per-capability 数据集扩展、Admin 标注导出和 promote 策略属于 P4 评测体系扩展。

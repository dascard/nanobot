# TimingGate 信号周期审计设计

日期：2026-06-20

## 背景

TimingGate 混合决策已经具备真实日志信号审计入口：`evals.timing_signal_audit`
可以从 `ChatLog` 中抽取 `s_ack`、`s_transport` 和 `w_marker` 等 scoring
信号，输出样本、shadow action mismatch、人工标注后的假阳率和阈值建议。

当前缺口在运营层：周期性评测已经通过 `scripts/run_eval_periodic.sh`
定期复跑稳定 gate 并上传 artifact，但它只覆盖离线稳定 baseline，不会固定产出
TimingGate 真实样本信号审计报告。结果是后续人工标注和调参缺少固定输入，容易退回
临时手工命令。

## 目标

- 新增 TimingGate 信号周期审计脚本，作为独立可复用入口。
- 将该脚本接入 `scripts/run_eval_periodic.sh` 的 keep-going 流程。
- 缺少真实 SQLite DB 时不让周期任务失败，但必须写出可归档的 skipped JSON 报告。
- 存在 DB 时复用现有 `evals.timing_signal_audit` CLI 生成审计报告。
- 让现有 artifact 规则继续归档 `evals/reports/*.json`。
- 用测试守住脚本环境变量、缺库跳过行为和周期入口接入。

## 非目标

- 不修改 TimingGate scoring 公式、阈值或 runtime 决策。
- 不新增生产 DB schema。
- 不把真实样本审计纳入 PR fail-fast gate。
- 不实现双人仲裁、标注 UI 或候选队列统一。
- 不自动把 RAG generated case 晋升为 manual case。
- 不改变 `.github/workflows/timing-gate-eval.yml` 的 artifact 路径，除非现有路径无法归档报告。

## 方案选择

### 方案 A：直接在 `run_eval_periodic.sh` 内嵌审计逻辑

优点是文件少，缺点是周期脚本会继续变长，缺库跳过 JSON 的生成逻辑不便单独测试。

### 方案 B：新增独立脚本并由周期脚本调用

优点是边界清晰：独立脚本负责 DB 发现、参数归一化和报告写入；周期脚本只负责
keep-going 编排。测试也可以直接用临时路径运行脚本，验证缺库跳过行为。

### 方案 C：扩展 `evals.timing_signal_audit` CLI 支持缺库跳过

优点是逻辑集中在 Python；缺点是 CLI 当前职责是执行审计，强行让它感知周期任务和
CI 缺数据场景会让运行语义变混。缺库跳过更像运维编排策略，适合放在脚本层。

推荐采用方案 B。

## 脚本契约

新增 `scripts/run_timing_signal_audit_periodic.sh`。

环境变量：

- `TIMING_SIGNAL_AUDIT_DB`：真实 SQLite DB 路径，默认 `data/nanobot.db`。
- `TIMING_SIGNAL_AUDIT_OUT`：报告路径，默认 `evals/reports/timing_signal_audit_latest.json`。
- `TIMING_SIGNAL_AUDIT_LIMIT`：最大样本数，默认 `200`。
- `TIMING_SIGNAL_AUDIT_AFTER_ID`：只审计大于该 `ChatLog.id` 的日志，默认 `0`。
- `TIMING_SIGNAL_AUDIT_SIGNALS`：逗号分隔信号名，默认由 CLI 使用内置信号集合。

行为：

- 脚本运行前清除代理环境变量，并设置 `PYTHONDONTWRITEBYTECODE=1`。
- 当 `TIMING_SIGNAL_AUDIT_DB` 不存在时：
  - 创建 `TIMING_SIGNAL_AUDIT_OUT` 父目录。
  - 写出 JSON 报告，核心结构沿用空样本报告。
  - `source.mode` 为 `skipped`。
  - `source.reason` 为 `db_not_found`。
  - 退出码为 `0`。
- 当 DB 存在时：
  - 调用 `python -B -m evals.timing_signal_audit --db "$DB" --out "$OUT"`。
  - 传入 `--limit`、`--after-id`。
  - 如果设置了 `TIMING_SIGNAL_AUDIT_SIGNALS`，额外传入 `--signals`。
  - CLI 失败时保留失败退出码，由 `run_eval_periodic.sh` 的 keep-going 逻辑累计状态。

缺库报告示例：

```json
{
  "total_samples": 0,
  "labeled_samples": 0,
  "signals": {},
  "shadow": {
    "total_samples": 0,
    "action_mismatch_count": 0,
    "action_mismatch_rate": 0.0,
    "mismatches_by_signal": {}
  },
  "samples": [],
  "generated_at": "2026-06-20T12:00:00",
  "source": {
    "mode": "skipped",
    "reason": "db_not_found",
    "db": "data/nanobot.db",
    "after_id": 0,
    "limit": 200,
    "signals": []
  }
}
```

## 周期脚本接入

`scripts/run_eval_periodic.sh` 新增一个步骤：

```bash
run_step "timing signal audit" \
  bash scripts/run_timing_signal_audit_periodic.sh
```

该步骤应放在稳定 gate 之后或 RAG gate 之后均可。推荐放在 RAG gate 之后，因为它是
运营报告，不应影响前面稳定 gate 的可读性；如果真实 DB 存在且审计失败，keep-going
仍会把最终退出码置为失败。

现有 workflow 的 artifact 已包含 `evals/reports/*.json`，可以归档
`timing_signal_audit_latest.json`，本阶段无需修改 workflow。

## 测试策略

先写红灯测试：

- 新增 `tests/test_timing_signal_audit_periodic.py`。
- 用 `subprocess.run()` 设置 `TIMING_SIGNAL_AUDIT_DB` 为临时目录下不存在的 DB，
  设置 `TIMING_SIGNAL_AUDIT_OUT` 为临时报告路径。
- 运行 `bash scripts/run_timing_signal_audit_periodic.sh`。
- 断言退出码为 `0`、报告存在、`source.mode == "skipped"`、
  `source.reason == "db_not_found"`、`total_samples == 0`。
- 静态断言 `scripts/run_eval_periodic.sh` 包含
  `scripts/run_timing_signal_audit_periodic.sh`，防止脚本孤立。

绿灯实现后运行：

```bash
python -B -m pytest tests/test_timing_signal_audit_periodic.py -q -p no:cacheprovider
python -B -m pytest tests/test_timing_signal_audit.py tests/test_eval_baseline.py -q -p no:cacheprovider
bash scripts/run_timing_signal_audit_periodic.sh
bash scripts/run_eval_periodic.sh
python -B -m pytest tests/ -q -p no:cacheprovider
```

## 文档更新

更新 `docs/evals.md` 的「周期性复跑与报告归档」章节，说明：

- 周期任务会额外产出 TimingGate signal audit 报告。
- 缺少真实 DB 时报告为 skipped，不代表审计通过或失败。
- 真实 DB 路径通过 `TIMING_SIGNAL_AUDIT_DB` 指定。
- 报告路径默认是 `evals/reports/timing_signal_audit_latest.json`。

最后更新 `docs/plan_walkthrough.md` 和 `.Codex/plans/timing-signal-audit-periodic.md`
的执行状态，记录红灯、绿灯、周期脚本和全量回归证据。

## 验收清单

- `scripts/run_timing_signal_audit_periodic.sh` 存在且可直接运行。
- 缺少 DB 时脚本退出码为 `0`，并写出 skipped JSON 报告。
- `scripts/run_eval_periodic.sh` keep-going 流程调用该脚本。
- workflow artifact 现有 `evals/reports/*.json` 路径可以归档审计报告。
- `docs/evals.md` 说明周期审计报告、缺库跳过和环境变量。
- 定向测试、周期脚本和全量测试均有新鲜验证结果。

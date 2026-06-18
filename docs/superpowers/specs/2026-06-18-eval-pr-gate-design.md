# P4-5A 统一评测 PR Gate 设计

## 背景

P4-1 到 P4-4 已经把评测体系推进到可用状态：通用 `evals.run` 支持 baseline diff 和 gate，TimingGate 已有独立 CI / PR gate，`capability_model_routing`、`capability_reply_contract`、`capability_rendering_contract` 和 RAG benchmark manual gate 都已有稳定 baseline。

当前缺口不是单个 runner 的评分能力，而是稳定 gate 分散在多条手动命令里。P4-5A 的目标是把这些已成熟的稳定 suite 收敛为一个 PR gate 入口，供本地和 CI 使用。

## 目标

- 新增统一脚本 `scripts/run_eval_pr_gate.sh`，串行运行当前仓库内稳定、离线、确定性的 gate。
- CI workflow 从只跑 TimingGate 扩展为统一 eval PR gate。
- 测试覆盖脚本内容和 workflow 调用关系，防止后续 gate 漏接入。
- 文档说明本地运行命令、覆盖 suite、失败处理和与 P4-5B / P4-5C 的边界。

## 非目标

- 不新增 RAG manual case，不更新 `evals/baselines/rag_benchmark.json`。
- 不新增 nightly / weekly 周期性复跑；周期性复跑留给 P4-5B。
- 不改 `evals.run` 的评分逻辑、不引入 meta-suite runner。
- 不调整生产 TimingGate、RAG 或 prompt runtime 行为。

## 方案比较

### 方案 A：新增统一 shell 脚本（推荐）

新增 `scripts/run_eval_pr_gate.sh`，复用现有 `scripts/run_timing_gate_gate.sh`，再串行运行能力数据集和 RAG benchmark gate。

优点：

- 变更小，复用现有 CLI 和 baseline。
- 本地与 CI 使用同一个入口，失败时输出仍来自原 gate。
- 不引入新的 Python 抽象，避免把专用 RAG benchmark 强行并入通用 `EvalCase`。

缺点：

- shell 脚本只能做串行编排，报告聚合能力有限。

### 方案 B：在 `evals.run` 中新增 meta-suite

让 `python -m evals.run --suite pr_gate` 调度多个 suite。

优点：

- 可以在 Python 内聚合报告。

缺点：

- RAG benchmark 仍是专用体系，强行纳入会扩大 P4-5A 范围。
- 需要改通用 eval CLI，风险高于当前阶段收益。

### 方案 C：CI workflow 使用 matrix 分别跑各 gate

每个 suite 一个 CI job 或 matrix entry。

优点：

- CI 输出天然分组，并行能力更好。

缺点：

- 本地没有统一入口。
- workflow 复杂度上升，后续新增 suite 需要同时维护多处命令。

结论：采用方案 A。P4-5A 先建立一个脚本入口；P4-5B 如需要报告归档或周期复跑，再考虑聚合报告和 matrix。

## 统一 Gate 范围

`scripts/run_eval_pr_gate.sh` 按顺序运行：

1. `python -B -m pytest tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py -v -p no:cacheprovider`
2. `bash scripts/run_timing_gate_gate.sh`
3. `python -B -m evals.run --suite capability_model_routing --baseline evals/baselines/capability_model_routing.json --min-pass-rate 1.0 --max-new-failures 0`
4. `python -B -m evals.run --suite capability_reply_contract --baseline evals/baselines/capability_reply_contract.json --min-pass-rate 1.0 --max-new-failures 0`
5. `python -B -m evals.run --suite capability_rendering_contract --baseline evals/baselines/capability_rendering_contract.json --min-pass-rate 1.0 --max-new-failures 0`
6. `python -B -m evals.rag_benchmark.run --manual evals/cases/rag_benchmark/manual --generated tmp/rag_benchmark/empty --provider-mode deterministic --manual-only --baseline evals/baselines/rag_benchmark.json --min-pass-rate 1.0 --max-new-failures 0 --max-degraded-rate 0.0 --max-unexpected-source-rate 0.0`

脚本继续使用当前 CI 需要的固定环境：

- 清理 `http_proxy`、`https_proxy`、`HTTP_PROXY`、`HTTPS_PROXY`、`all_proxy` 和 `ALL_PROXY`。
- 设置 `PYTHONDONTWRITEBYTECODE=1`。
- 设置 `NANOBOT_TESTING=1`。
- 设置 `DATABASE_URL=sqlite:///:memory:`。
- 设置 `NEW_API_KEY=test-key-for-ci`。
- 设置 `NANOBOT_ADMIN_TOKEN=test-admin-token`。

## CI 设计

保留现有 `.github/workflows/timing-gate-eval.yml` 的触发条件：

- `pull_request`
- push 到 `main` 或 `master`

将 workflow 名称和 job 名称改为 Eval PR Gate，并把核心执行步骤改为：

```bash
bash scripts/run_eval_pr_gate.sh
```

安装依赖步骤保持不变。

## 测试设计

在 `tests/test_eval_baseline.py` 增加脚本和 workflow 守卫：

- 断言 `scripts/run_eval_pr_gate.sh` 存在。
- 断言脚本调用 `scripts/run_timing_gate_gate.sh`。
- 断言脚本包含 3 个 capability suite 的 baseline gate。
- 断言脚本包含 RAG benchmark manual deterministic gate。
- 断言 workflow 调用 `scripts/run_eval_pr_gate.sh`。

现有 `test_timing_gate_gate_script_uses_stable_baseline` 保留，确保 TimingGate 子脚本仍独立可用。

## 文档设计

更新 `docs/evals.md`：

- 增加统一 PR gate 本地命令：

```bash
bash scripts/run_eval_pr_gate.sh
```

- 列出当前覆盖 suite。
- 说明 P4-5A 只覆盖稳定离线 gate。
- 说明 generated RAG case 仍不进入 baseline。

更新 `docs/todo.md` 和 `docs/plan_walkthrough.md`：

- P4-5A 完成后标记“更多 suite PR gate”已完成。
- P4-5B / P4-5C 仍分别负责周期性复跑和 RAG manual 样本扩充。

## 验收

P4-5A 完成后运行：

```bash
python -B -m pytest tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py tests/test_rag_benchmark.py -v -p no:cacheprovider
bash scripts/run_eval_pr_gate.sh
python -B -m pytest tests/ -v -p no:cacheprovider
```

预期：

- 定向 pytest 通过。
- 统一 PR gate 输出所有子 gate 通过，退出码为 0。
- 全量 pytest 通过。

## 风险与边界

- RAG benchmark 会写 `tmp/rag_benchmark/reports/latest.json` 和 `latest.md`；这些路径当前不进入提交。
- 各 gate 串行运行会增加 CI 时间，但当前 suite 数量小，优先换取入口一致性。
- 如果某个 suite baseline 后续需要更新，必须先本地跑对应 gate，再更新 baseline 和文档，不在 P4-5A 中自动更新。

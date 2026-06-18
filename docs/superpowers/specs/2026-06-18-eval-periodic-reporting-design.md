# P4-5B 周期性评测复跑与报告归档设计

设计日期：2026-06-18

## 背景

P4-5A 已把稳定离线 gate 收敛到 `scripts/run_eval_pr_gate.sh`，覆盖 TimingGate、三个 capability suite 和 RAG benchmark manual deterministic gate。该入口适合 PR 和主分支 push：失败时快速停止，避免继续消耗 CI 时间。

P4-5B 要补的是另一类能力：即使没有代码变更，也能定期复跑同一组稳定 gate，并把通用 eval 报告和 RAG benchmark 报告作为 CI artifact 保存，方便后续排查漂移、对比报告和人工复核。周期性复跑不负责扩大样本，也不负责更新 baseline；这些分别留给 P4-5C 和后续 baseline 维护流程。

## 目标

- 为稳定评测 gate 增加周期性复跑和手动触发入口。
- 周期性复跑使用 keep-going 策略：单个 suite 失败后继续运行后续 suite，尽量产出完整报告面。
- 归档通用 eval 报告和 RAG benchmark 报告到 GitHub Actions artifact。
- 保持 PR gate 的 fail-fast 行为不变。
- 文档化报告位置、保留策略和失败排查顺序。

## 非目标

- 不新增或修改 RAG manual case。
- 不更新 `evals/baselines/*.json`。
- 不把 RAG benchmark 并入通用 `evals.run`。
- 不在周期性 workflow 中使用 runtime provider、真实 LLM、真实生产 DB 或 generated RAG 样本。
- 不把全量 `pytest tests/` 加进周期性评测最小入口。
- 不提交 `evals/reports/*.json`、`tmp/rag_benchmark/**` 或本地数据库派生报告。

## 当前能力

`scripts/run_eval_pr_gate.sh` 已串联：

- `tests/test_eval_baseline.py`
- `tests/test_timing_gate_prompt_policy.py`
- `scripts/run_timing_gate_gate.sh`
- `capability_model_routing`
- `capability_reply_contract`
- `capability_rendering_contract`
- RAG benchmark manual deterministic gate

通用 eval runner 会写入：

- `evals/reports/latest.json`
- `evals/reports/YYYY-MM-DD-<suite>.json`

RAG benchmark reporter 会写入：

- `tmp/rag_benchmark/reports/latest.json`
- `tmp/rag_benchmark/reports/latest.md`
- `tmp/rag_benchmark/reports/<report_id>.json`
- `tmp/rag_benchmark/reports/<report_id>.md`

这些报告路径已在 `.gitignore` 中排除，归档应通过 CI artifact，而不是提交进仓库。

## 方案选择

### 方案 A：复用 PR gate 脚本并给 workflow 增加 schedule

优点是改动最小。缺点是 `scripts/run_eval_pr_gate.sh` 使用 fail-fast，一个 suite 失败会阻止后续 suite 产出报告。周期性复跑的价值在于保留完整失败面，因此该方案不适合作为 P4-5B 主方案。

### 方案 B：新增周期性脚本，workflow 按事件选择入口

新增 `scripts/run_eval_periodic.sh`，复用 P4-5A 的稳定 suite 列表，但用 keep-going helper 累计退出码。PR / push 仍执行 `scripts/run_eval_pr_gate.sh`；`schedule` 和 `workflow_dispatch` 执行 `scripts/run_eval_periodic.sh`，随后用 `actions/upload-artifact@v4` 在 `if: always()` 条件下上传报告。

这是推荐方案。它保留 PR gate 的快速失败语义，同时满足周期性归档的完整性要求。

### 方案 C：拆成独立 nightly workflow

新增 `.github/workflows/eval-periodic.yml`，完全独立于现有 workflow。优点是边界清楚；缺点是重复安装依赖、环境变量和后续维护入口。当前 P4-5B 只需要稳定 gate 的周期性版本，复用现有 workflow 更直接。

## 最终设计

采用方案 B。

### 脚本

新增 `scripts/run_eval_periodic.sh`。

脚本职责：

- 清理代理环境变量。
- 设置与 PR gate 相同的测试环境变量。
- 通过 `run_step` helper 执行每个稳定 gate。
- 每步失败只记录失败并累计最终状态，不中断后续 gate。
- 结束时返回累计退出码：全部通过返回 0，任一步失败返回 1。

周期性脚本覆盖的稳定 gate 与 PR gate 保持一致：

- 评测守卫 pytest：`tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py`
- TimingGate baseline gate：`scripts/run_timing_gate_gate.sh`
- `capability_model_routing`
- `capability_reply_contract`
- `capability_rendering_contract`
- RAG benchmark manual deterministic gate

### Workflow

修改 `.github/workflows/timing-gate-eval.yml`：

- 保留 `pull_request`。
- 保留 `push` 到 `main` / `master`。
- 新增 `workflow_dispatch`。
- 新增 `schedule`。建议每周一北京时间 04:20 执行，对应 UTC cron：

```yaml
schedule:
  - cron: "20 20 * * 0"
```

GitHub Actions 的 cron 使用 UTC。`20 20 * * 0` 表示 UTC 周日 20:20，也就是北京时间周一 04:20。

执行入口：

- PR / push：`bash scripts/run_eval_pr_gate.sh`
- schedule / workflow_dispatch：`bash scripts/run_eval_periodic.sh`

Artifact：

- 使用 `actions/upload-artifact@v4`。
- `if: always()`，即使 gate 失败也上传已有报告。
- `if-no-files-found: warn`。
- `retention-days: 14`。
- 上传路径：
  - `evals/reports/*.json`
  - `tmp/rag_benchmark/reports/*.json`
  - `tmp/rag_benchmark/reports/*.md`

### 报告与失败处理

报告读取顺序：

1. 先看 workflow job 的失败步骤，确认是哪一个 gate 返回非零。
2. 再下载 artifact。
3. 通用 suite 看 `evals/reports/YYYY-MM-DD-<suite>.json`，不要只看 `latest.json`，因为 latest 会被后续 suite 覆盖。
4. RAG benchmark 看 `tmp/rag_benchmark/reports/latest.md` 和对应 run-id JSON。
5. 如果 failure 是 baseline diff 或 gate threshold，先确认是否为真实回归；不要直接更新 baseline。
6. 如果需要扩样本或更新 RAG manual baseline，转入 P4-5C 或 baseline 维护流程。

## 测试计划

新增或扩展 `tests/test_eval_baseline.py` 的静态守卫：

- `test_eval_periodic_script_runs_stable_suites`：周期性脚本必须覆盖所有稳定 gate。
- `test_eval_periodic_script_keeps_going_for_archival_reports`：周期性脚本必须包含累计状态或 keep-going helper，不得只依赖 `set -e` fail-fast。
- `test_eval_pr_gate_workflow_runs_unified_script`：继续证明 PR / push 入口调用 `scripts/run_eval_pr_gate.sh`。
- `test_eval_workflow_has_periodic_schedule_and_manual_dispatch`：workflow 必须有 `schedule` 和 `workflow_dispatch`。
- `test_eval_workflow_uploads_report_artifacts`：workflow 必须用 `actions/upload-artifact@v4` 上传通用 eval 和 RAG benchmark 报告。
- `test_eval_workflow_artifact_retention_is_bounded`：artifact 必须设置有限 `retention-days`。

验证命令：

```bash
python -B -m pytest tests/test_eval_baseline.py -v -p no:cacheprovider
bash scripts/run_eval_periodic.sh
bash scripts/run_eval_pr_gate.sh
python -B -m pytest tests/test_eval_baseline.py tests/test_timing_gate_prompt_policy.py tests/test_rag_benchmark.py -v -p no:cacheprovider
python -B -m pytest tests/ -v -p no:cacheprovider
```

## 风险控制

- 周期性脚本不替代 PR gate。PR gate 继续 fail-fast。
- 周期性报告只归档 deterministic 稳定 gate，避免上传真实 DB 或敏感 ChatLog 派生内容。
- Artifact 只保留 14 天，避免无限积累。
- RAG benchmark 仍保持独立报告路径和 schema，不与通用 eval 报告强行合并。
- `evals/reports/latest.json` 只能作为最后一个通用 suite 的快捷入口，不能作为周期性归档的唯一证据。

## P4-5C 边界

P4-5C 将单独处理 RAG manual 样本扩充。当前 manual baseline 只有 3 个 `constraint_only` case，且没有 positive 召回样本。扩充时应优先走 `generated -> manual -> baseline` 的人工审查路径；如果只新增 `allow_empty=true` 的 constraint case，可以不依赖真实数据，但覆盖价值有限。positive manual case 需要稳定可检索数据或 fixture DB，否则不适合进入 PR gate baseline。

## 验收标准

- PR / push workflow 仍调用 `scripts/run_eval_pr_gate.sh`。
- schedule / manual workflow 调用 `scripts/run_eval_periodic.sh`。
- 周期性脚本在单个 gate 失败后继续运行后续 gate，并在最后返回失败。
- workflow 总是尝试上传报告 artifact。
- artifact 包含通用 eval JSON 和 RAG benchmark JSON / Markdown。
- 文档写清 UTC cron 与北京时间换算、报告位置和失败处理顺序。
- 不修改 RAG manual case 或 baseline。

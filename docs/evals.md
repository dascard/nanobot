# 评测与门禁

本文记录当前仓库内 `evals/` 的稳定入口、候选标注闭环和本地门禁规则。

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

## 统一 PR Gate

P4-5A 已将稳定离线 gate 收敛为一个本地 / CI 共用入口：

```bash
bash scripts/run_eval_pr_gate.sh
```

当前覆盖：

- `timing_gate`
- `capability_model_routing`
- `capability_reply_contract`
- `capability_rendering_contract`
- RAG benchmark manual+fixture deterministic gate

`.github/workflows/timing-gate-eval.yml` 在 PR 和主分支 push 上调用同一个脚本。Workflow 显式设置 `NANOBOT_TESTING`、`DATABASE_URL`、`NEW_API_KEY` 和 `NANOBOT_ADMIN_TOKEN`，避免测试导入配置时写入 `.env`。

该入口只运行稳定 baseline gate。P4-5B 已完成周期性复跑和报告归档；P4-5C 已完成第一轮 RAG manual 样本扩充；P4-5D 已完成 memory fixture-backed positive RAG case；P4-5E 已补齐 knowledge fixture 引用正例；P4-5F 已补齐 sticker fixture sendable 正例；P4-5G 已补齐 group_memory fixture 正例；P4-5H 已强化 fixture 过滤约束。RAG stable gate 当前包含 9 个 manual constraint case 和 4 个固定 fixture positive case。

## 周期性复跑与报告归档

P4-5B 使用同一组稳定离线 gate 做周期性复跑，但执行入口是：

```bash
bash scripts/run_eval_periodic.sh
```

该脚本与 PR gate 覆盖相同 suite，但采用 keep-going 策略：单个 gate 失败后继续运行后续 gate，最后用累计退出码反映整体结果。这样即使前面的 suite 失败，后续 suite 和 RAG benchmark 仍能产出报告。

`.github/workflows/timing-gate-eval.yml` 的触发方式：

- `pull_request` / `push`：运行 `scripts/run_eval_pr_gate.sh`，保持 fail-fast。
- `schedule` / `workflow_dispatch`：运行 `scripts/run_eval_periodic.sh`，并上传报告 artifact。

周期性 schedule 为 UTC 周日 20:20，即北京时间周一 04:20。

Artifact 名称为 `eval-reports-${{ github.run_id }}`，保留 14 天，包含：

- `evals/reports/*.json`
- `evals/reports/periodic_manifest_*.json`
- `evals/reports/runs/**/manifest.json`
- `tmp/rag_benchmark/reports/*.json`
- `tmp/rag_benchmark/reports/*.md`

周期性入口会在结束前写出统一运行清单（manifest）：

- `evals/reports/periodic_manifest_latest.json`
- `evals/reports/YYYY-MM-DD-periodic_manifest.json`
- `evals/reports/runs/<run_id>/manifest.json`

manifest 记录一次周期运行的 `run_id`、触发来源、开始 / 结束时间、最终退出码、GitHub 环境信息和每个步骤的状态。`steps[]` 会索引通用 eval suite、RAG benchmark 和 TimingGate signal audit 的报告路径，并提取摘要指标：

- 通用 eval suite：`total`、`passed`、`failed`、`pass_rate`、`gate_passed`、`new_failed_cases`。
- RAG benchmark：`total_cases`、`pass_rate`、`hit@5`、`mrr`、`positive_cases`、`gate_passed`。
- TimingGate signal audit：`total_samples`、`labeled_samples`、`action_mismatch_count`、`action_mismatch_rate`，缺库 skipped 时会记录 `notes.reason=db_not_found`。

排查周期性失败时，优先打开 `periodic_manifest_latest.json` 或对应 `runs/<run_id>/manifest.json`。manifest 只负责索引和摘要，不代表调参结论；是否调整 TimingGate、RAG 或 capability gate 阈值必须另起只读分析和人工确认。

### 跨 artifact 周期趋势

周期运行 manifest 完成后，可以用只读趋势工具聚合多个 run：

```bash
python -B -m evals.artifact_trends \
  --manifest-glob 'evals/reports/*-periodic_manifest.json' \
  --manifest-glob 'evals/reports/runs/*/manifest.json' \
  --out evals/reports/artifact_trends_latest.json
```

趋势报告输出 `series.runs`、`series.eval_suites`、`series.rag_benchmark`、`series.timing_signal_audit` 和 `regressions`。报告只读取 manifest 中已经固化的 summary，不回读历史 manifest 指向的可变 `latest.json`。`regressions` 是复盘提示，不改变 PR gate、周期 gate、baseline 或调参阈值。

第一版覆盖：

- run 状态、退出码、失败 step 数和运行时长。
- 通用 eval suite 的 `pass_rate`、失败数、新增失败和 gate 状态。
- RAG benchmark 的 `pass_rate`、`hit@5`、`mrr` 和 positive case 数。
- TimingGate signal audit 的样本量、标注覆盖率和 action mismatch 趋势。

### 周期趋势只读调参分析

跨 artifact 趋势生成后，可以运行只读调参分析：

```bash
python -B -m evals.tuning_analysis \
  --trends evals/reports/artifact_trends_latest.json \
  --timing-audit evals/reports/timing_signal_audit_latest.json \
  --manifest evals/reports/periodic_manifest_latest.json \
  --out evals/reports/tuning_analysis_latest.json
```

报告输出 `readiness`、`signals`、`recommendations` 和 `regression_refs`。它只读现有 artifact，不读取生产 DB，不更新 baseline，不改变 PR gate 或周期 gate。`candidate_adjustment` 只表示可进入人工复核的方向，不包含可自动应用的参数值。

第一版建议类型：

- `no_change`：当前趋势和 raw audit 没有显示需要调整的退化信号。
- `label_more_samples`：样本存在但全局或 per-signal 标注覆盖不足。
- `collect_more_artifact`：run 数、audit 样本或报告 artifact 不足。
- `manual_review`：TimingSignal 假阳率、action mismatch、RAG 指标或 eval suite 指标需要人工复核。
- `candidate_adjustment`：保留为人工调参讨论入口；第一版不输出可自动应用的参数值。

周期性入口还会运行 TimingGate signal audit：

```bash
bash scripts/run_timing_signal_audit_periodic.sh
```

默认读取 `data/nanobot.db`，可通过 `TIMING_SIGNAL_AUDIT_DB` 指向真实 SQLite DB。
默认报告路径是 `evals/reports/timing_signal_audit_latest.json`，会被现有
`evals/reports/*.json` artifact 规则归档。CI 或本地缺少真实 DB 时，脚本会写出
`source.mode=skipped`、`source.reason=db_not_found` 的空报告并退出 0；这只表示本轮没有可审计真实库，不表示信号质量通过。

排查失败时，先看 workflow 失败步骤，再下载 artifact。通用 suite 优先看 `evals/reports/YYYY-MM-DD-<suite>.json`，不要只看 `latest.json`；RAG benchmark 优先看 `tmp/rag_benchmark/reports/latest.md` 和对应 run-id JSON。

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

## 候选标注闭环

通用流程是 `candidates → labeled → dataset case`：

1. 从数据库导出候选 case。
2. 人工在 JSONL 中补齐可评分 `expected`。
3. 导入标签，把候选状态改为 `labeled`。
4. 先 dry-run 检查晋升计划，再写入目标数据集。

常用命令：

```bash
python -m evals.candidates export --suite timing_gate --status candidate --out /tmp/candidates.jsonl
python -m evals.candidates import-labels --labels /tmp/labels.jsonl
python -m evals.candidates promote --suite timing_gate --target-dataset timing_gate --dry-run
python -m evals.candidates promote --suite timing_gate --target-dataset timing_gate --apply
```

标签文件每行是一个 JSON 对象，最小格式如下：

```json
{"case_id":"cand_timing_gate_1","expected":{"timing_action":"continue"},"note":"人工确认"}
```

`expected` 必须满足 `evals.expected_contract.SCOREABLE_EXPECTED_KEYS` 定义的可评分字段契约：

- 不能是空对象。
- 不能保留 `needs_label: true`。
- 不能包含 scorer 不会读取的字段。

Admin 接口兼容旧字段 `expected_json`，但新调用应统一发送 `expected`。WebUI 标注入口按 `{ expected: expectedJson, note }` 提交，避免把人工说明混入 `expected`。

### 候选 readiness 与批量预检

`GET /api/v1/admin/evals/candidates` 会返回 `summary` 和每条候选的 `readiness`。`readiness.ready=true` 表示当前候选可以晋升；blocked 时查看 `readiness.blocking_reasons[].code`。

常见阻断原因：

- `invalid_status`：候选还不是 `labeled`，或已经 `ignored` / `promoted`。
- `expected_invalid`：`expected` 为空、仍包含 `needs_label`，或字段不符合 scorer 契约。
- `suite_not_runnable`：候选 suite 不在当前 eval runner 可运行集合中，例如 `error`。
- `target_dataset_invalid`：目标 dataset 不是安全目录名。
- `target_case_exists`：目标 case 文件已存在。

只读批量预检接口：

```http
POST /api/v1/admin/evals/candidates/preflight
```

请求示例：

```json
{
  "case_ids": ["cand_timing_gate_1"],
  "target_dataset": "timing_gate"
}
```

CLI dry-run 现在也使用同一套 readiness 规则，输出 ready / blocked 聚合结果：

```bash
python -m evals.candidates promote --suite timing_gate --target-dataset timing_gate --dry-run
```

批量 apply 保持严格语义：只要批次存在 blocked candidate，就返回 `ok=false`、`applied=0`，不做部分写入。WebUI「Eval 评测」候选页提供「预检当前页」，该操作只读，不写入 case 文件。

`PATCH /api/v1/admin/evals/candidates/{case_id}` 不能直接把候选改为 `labeled` 或 `promoted`。标注必须走 `/label`，晋升必须走 `/promote`。

### 候选仲裁状态

通用 `EvalCandidate` 支持显式运营动作：

- `reject`：人工确认不进入稳定样本，状态变为 `rejected`。
- `defer`：暂缓处理，状态变为 `deferred`。
- `reopen`：从 `ignored`、`deferred` 或 `rejected` 复开为 `candidate`。

这些动作必须走专用端点：

```http
POST /api/v1/admin/evals/candidates/{case_id}/reject
POST /api/v1/admin/evals/candidates/{case_id}/defer
POST /api/v1/admin/evals/candidates/{case_id}/reopen
```

请求体使用 `reason_code`、`note` 和可选 `defer_until`：

```json
{
  "reason_code": "needs_more_context",
  "note": "缺少后续上下文，等待批次复核",
  "defer_until": "2026-06-30"
}
```

每次动作都会记录统一 Admin audit detail：

- `before_status`：动作前状态。
- `after_status`：动作后状态。
- `reason_code`：固定原因码。
- `note`：人工备注，后端最多保留 1000 字符。
- `defer_until`：暂缓复核时间，仅 `defer` 使用。

当前固定原因码：

- Reject：`duplicate`、`low_value`、`unsafe_or_sensitive`、`not_reproducible`、`out_of_scope`、`bad_sample`。
- Defer：`needs_more_context`、`needs_batch_review`、`waiting_for_baseline`、`needs_product_decision`、`temporary_blocker`。
- Reopen：`new_evidence`、`operator_correction`、`defer_expired`、`needs_relabel`。

`PATCH /api/v1/admin/evals/candidates/{case_id}` 仍不能直接写入 `deferred` 或 `rejected`。WebUI 候选页提供单条「暂缓」「拒绝」「复开」操作，不提供批量仲裁。

RAG benchmark 的 generated / manual case 仍是独立体系，不并入通用 `EvalCandidate`。generated case 的单条提升继续使用 RAG Admin 的 `promote-manual` 接口；如需记录 RAG generated 的 reject / defer，后续应设计 sidecar 或批次审计。

### 候选批次审计

通用 `EvalCandidate` 支持 record-only 人工仲裁批次审计。批次审计记录一次人工复核的输入范围、候选快照、readiness 阻断原因和人工 decision，不批量修改候选状态。

Admin API：

```http
POST /api/v1/admin/evals/candidates/batch-audit
```

请求示例：

```json
{
  "dry_run": true,
  "case_ids": ["cand_timing_gate_1"],
  "target_dataset": "timing_gate",
  "batch_note": "2026-06-20 人工复核第一批",
  "decisions": [
    {
      "case_id": "cand_timing_gate_1",
      "decision": "needs_label",
      "reason_code": "unspecified",
      "note": "可进入后续标注"
    }
  ]
}
```

`dry_run=true` 只返回批次计划，不写 `AdminAuditLog`，不修改 `EvalCandidate`。`dry_run=false` 会重新计算计划，若 `ok=true` 则写入一条审计日志：

- `action`: `audit_eval_candidate_batch`
- `target_type`: `eval_candidate_batch`
- `target_id`: `batch_YYYYMMDD_xxxxxxxx` 格式的批次 ID
- `detail_json`: `filters`、`batch_note`、`counts`、`items` 和 readiness 明细

响应中的 `counts` 包含 `by_status`、`by_suite`、`by_source`、`by_decision`、`by_reason_code` 和 `by_blocking_reason`。`items[].errors` 非空时 `ok=false`；apply 模式会整体拒绝写审计。

支持的审计 decision：

- `noop`：已查看，无进一步动作。
- `needs_label`：建议后续标注。
- `promote_ready`：建议后续晋升，但仍必须走 promote dry-run / apply。
- `reject`：建议单条拒绝。
- `defer`：建议单条暂缓。
- `reopen`：建议单条复开。

这些 decision 只是人工审计结论，不会触发状态流转。单条状态变更仍必须走 `/reject`、`/defer`、`/reopen`、`/label` 或 `/promote`。

CLI 只读导出：

```bash
python -m evals.candidates audit --suite timing_gate --status labeled --target-dataset timing_gate --out /tmp/candidate-audit.json
```

该命令只生成和 Admin dry-run 同结构的 JSON 报告，不写 `AdminAuditLog`，不写 eval case 文件。

WebUI「Eval 评测」候选页提供「批次审计」按钮，基于当前页候选调用只读 preflight，并展示 `counts`、`top_blocking_reasons` 和 `items`。WebUI 第一版不提供批量 apply、批量拒绝或批量暂缓。

### 真实样本趋势报表

通用 `EvalCandidate` 支持只读运营趋势报表。报表按候选创建日期分桶，展示当前状态、readiness 和阻断原因分布。

Admin API：

```http
GET /api/v1/admin/evals/candidates/trend?days=30&suite=timing_gate
```

查询参数：

- `days`：统计窗口，默认 30，范围 1 到 90。
- `suite`：可选，按候选 suite 过滤。
- `status`：可选，按当前候选状态过滤。
- `source`：可选，按候选来源过滤。
- `target_dataset`：可选，用于 readiness 目标文件冲突检查；为空时按候选自身 suite 计算。

CLI 只读导出：

```bash
python -m evals.candidates trend --days 30 --suite timing_gate --out /tmp/candidate-trend.json
```

响应结构包含：

- `summary.total`：当前过滤范围内的候选数量。
- `summary.by_status`、`summary.by_suite`、`summary.by_source`：当前快照分布。
- `summary.readiness.ready` / `summary.readiness.blocked`：按当前 readiness 规则重新计算的可晋升和阻断数量。
- `summary.top_blocking_reasons`：当前主要阻断原因。
- `buckets[]`：按 `EvalCandidate.created_at` 日期分桶的同口径聚合。

注意：趋势报表的日期桶来自 `EvalCandidate.created_at`，但桶内 `status` 和 `readiness` 都是当前快照，不代表历史状态迁移。例如某候选在 6 月 18 日创建、6 月 20 日被拒绝，它仍会落在 6 月 18 日桶内，并计入该桶的 `by_status.rejected`。

WebUI「Eval 评测」页提供「趋势报表」tab，可按 suite、status、source 和 days 刷新报表，并展示完整 JSON payload。该页面不提供调参、批量拒绝、批量暂缓、批量复开或批量晋升。

## Admin WebUI 标注工作台

P4-2 已将 Admin 标注工作台拆为后端契约和 WebUI 两个阶段，设计文档为 `docs/superpowers/specs/2026-06-18-admin-eval-workbench-contract-design.md`，实现计划为 `.Codex/plans/admin-eval-workbench-contract.md`。P4-2A 后端契约 schema/API 已完成；P4-2B WebUI 工作台已接入契约化标注和 promote 预检流程，并通过本轮定向验证、WebUI build 和全量回归。

当前操作流如下：

- WebUI 从 `/api/v1/admin/evals/expected-contract` 读取 expected 契约，不再手写 `expected_action`、`should_learn`、`quality`、`reason`、`delay_seconds` 等不可评分字段。
- 标注请求只提交 scorer 会读取的 `expected` 字段；人工解释写入 `note`，不写入 `expected.reason`。
- Promote 操作必须先发送 `{ "dry_run": true, "target_dataset": "timing_gate" }` 这类明确目标数据集的请求，展示后端返回的 `target_dataset`、`path` 和 case 摘要后，用户再二次确认 apply。
- Apply 请求发送 `{ "dry_run": false, "target_dataset": "timing_gate" }` 这类明确目标数据集的请求，成功后刷新候选列表；目标数据集默认使用候选 suite，并允许人工调整。

## Dataset 与 Suite

`dataset` 是 `evals/cases/<dataset>` 目录名，用于组织数据集和门禁维度。`suite` 是每个 case 内部的执行类型，决定使用哪个 runner / scorer。

因此二者可以不同。例如 `evals/cases/capability_model_routing/model_routing_stream_required_001.json` 属于 `capability_model_routing` dataset，但 case 内 `suite` 是 `model_routing`，运行时仍使用 `model_routing` runner 和 scorer。

本地运行能力数据集门禁：

```bash
python -B -m evals.run --suite capability_model_routing --baseline evals/baselines/capability_model_routing.json --min-pass-rate 1.0 --max-new-failures 0
```

## 能力契约数据集规划

P4-3 设计文档为 `docs/superpowers/specs/2026-06-18-capability-contract-eval-datasets-design.md`。本阶段扩展两个 per-capability 数据集：

- `capability_reply_contract`：组织群聊回复合同样本，case 内 `suite` 复用 `reply_contract` / `group_reply`，不新增 runner。
- `capability_rendering_contract`：组织响应信封到 QQ 出站消息的渲染样本，case 内 `suite` 使用新增 `rendering_contract` runner。

Reply contract gate：

```bash
python -B -m evals.run --suite capability_reply_contract --baseline evals/baselines/capability_reply_contract.json --min-pass-rate 1.0 --max-new-failures 0
```

`rendering_contract` runner 只做离线渲染：读取 `case.input.envelope`，调用 `render_qq_outbound_envelope()`，把最终字符串写入 `EvalOutput.reply_text` 和 `output.raw["rendered_message"]`，把 `reply_meta` 写入 `EvalOutput.reply_meta`。评分继续复用 `must_contain`、`must_not_contain`、`send_mode`、`reply_to_message_id` 和 `mentions`。

Rendering contract gate：

```bash
python -B -m evals.run --suite capability_rendering_contract --baseline evals/baselines/capability_rendering_contract.json --min-pass-rate 1.0 --max-new-failures 0
```

2026-06-18 收口验证：`capability_reply_contract` gate 输出 `total=3 passed=3 failed=0` 和 `Gate passed`；`capability_rendering_contract` gate 输出 `total=5 passed=5 failed=0` 和 `Gate passed`；评测定向回归为 `34 passed, 21 warnings`，渲染相邻回归为 `17 passed, 1 warning`，全量回归为 `1353 passed, 6 skipped, 139 warnings`。

## RAG Benchmark 边界

`evals/rag_benchmark/` 保持独立 benchmark 入口，不并入通用 `EvalCase`。原因是 RAG benchmark 需要独立的召回样本、索引上下文和评分口径；通用 candidates 闭环只负责把可评分的用户交互样本沉淀为稳定 case。

P4-4 已为 RAG benchmark 增加专用 baseline diff 和 gate。P4-5H 后，稳定门禁运行 manual case、固定 fixture positive cases 和 deterministic provider：

```bash
python -B -m evals.rag_benchmark.run \
--manual evals/cases/rag_benchmark/manual \
--generated tmp/rag_benchmark/empty \
--provider-mode deterministic \
--manual-only \
--fixture positive_v1 \
--fixture-db tmp/rag_benchmark/fixtures/positive_v1.db \
--baseline evals/baselines/rag_benchmark.json \
--min-pass-rate 1.0 \
--min-hit-at-5 1.0 \
--min-mrr 1.0 \
--max-new-failures 0 \
--max-degraded-rate 0.0 \
--max-unexpected-source-rate 0.0
```

门禁输出写入 `tmp/rag_benchmark/reports/latest.json` 和 `latest.md`，报告顶层包含 `provider_mode`、`case_scope`、`case_scores`、`failed_cases`、`baseline_diff` 和 `gate`。Admin RAG Benchmark 页面可以在运行时传入 `baseline_path`、`min_pass_rate`、`max_new_failures`、`max_degraded_rate` 和 `max_unexpected_source_rate`，并展示 `Gate passed` / `Gate failed`、新增失败、已修复失败和仍失败 case。

Generated case 只作为本地 DB 采样候选，不进入仓库稳定 baseline。人工确认后的样本应保存为 manual case，再纳入 `evals/baselines/rag_benchmark.json` 对应的 gate。

Admin API 提供 `POST /api/v1/admin/rag/benchmark/cases/{case_id}/promote-manual` 作为单条 generated → manual 仲裁入口。该接口支持 `dry_run=true` 预检目标 `target_case_id`、目标路径和转换后的 case JSON；`dry_run=false` 才会写入 `evals/cases/rag_benchmark/manual/{target_case_id}.json`，并记录 `promote_rag_benchmark_generated_case` 审计。stale generated case 必须先重新刷新 generated，避免把已过期 DB fingerprint 的样本提升为稳定样本。

Promote 不会自动更新 `evals/baselines/rag_benchmark.json`，也不代表样本已进入稳定 gate。只有人工确认 manual case 应纳入稳定门禁时，才同步 baseline 并运行 RAG stable gate。`tmp/rag_benchmark/generated/*` 仍是本地派生产物，不应提交。

P4-5C 已将 manual deterministic gate 的样本从 3 个扩充到 9 个。新增样本仍全部是 `constraint_only`，用于覆盖 memory、knowledge、sticker 和 group_memory 的过滤、scope、citation、sendable 约束。

P4-5D 已新增 `evals/rag_benchmark/fixtures.py` 和 `positive_v1` fixture DB builder。P4-5G 已把 `positive_v1` 从单一 memory 正例扩展为 memory + knowledge + sticker + group_memory 四正例；P4-5H 在保持四个正例不变的基础上补强同 query decoy 和 forbidden 断言：memory 覆盖跨 user、跨 session、跨 source decoy；knowledge 覆盖 `trust_level`、`source_type`、`published_after` decoy；sticker 覆盖其他 stream 和 global decoy；group_memory 保留跨群 decoy。baseline 包含 `memory_fixture_positive_001`、`knowledge_fixture_positive_001`、`sticker_fixture_positive_001` 和 `group_memory_fixture_positive_001`，`metrics.overall.positive_cases=4`、`metrics.source:knowledge.positive_cases=1`、`metrics.source:sticker.positive_cases=1`、`metrics.source:group_memory.positive_cases=1`、`hit@5=1.0`、`mrr=1.0`。knowledge fixture 固定命中 `knowledge:9001:chunk:0`，并通过 `requires_citation=true` 的 `checks.citation=true`；sticker fixture 固定命中 `sticker:9101:sticker`，并通过 `requires_sendable=true` 的 `checks.sendable=true`；group_memory fixture 固定命中 `group_memory:9201:memory`，通过 `requires_group_id=true` 的 `checks.group_filter=true`，并用跨群 decoy `group_memory:9202:memory` 验证 forbidden check 不泄漏。PR gate 使用 `--min-hit-at-5 1.0` 和 `--min-mrr 1.0`，防止正例召回退化。

更新 manual case 或 fixture case 时必须同步 `evals/baselines/rag_benchmark.json`，并保证 baseline 的 `case_scores[*].case_id` 集合与 enabled manual case 和 `fixture_cases("positive_v1")` 的并集一致。`tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract` 会守住这个合同。

## 失败处理

- `pass_rate below threshold`：当前 suite 有失败。先看 `Failed:` 列表，修 case 或修实现。
- `new_failed_cases exceeds threshold`：当前失败不在 baseline 中，是新回归。默认不允许合入。
- `baseline suite mismatch`：baseline 文件不是当前 suite 的基线，需要改回正确路径。
- `fixed_cases` 非空且无新增失败：说明旧失败已修复，可以在审查后刷新 baseline。

## 与 P4 的边界

TimingGate 门禁只负责固定 suite 的确定性回归。通用 `candidates → labeled` 标注闭环、per-capability 数据集扩展、Admin 标注导出和 promote 策略属于 P4 评测体系扩展。当前 P4-1 已先完成 expected 契约、候选标注、promote dry-run、离线 CLI 和首个 `capability_model_routing` 数据集；P4-2 已完成后端 expected 契约和 Admin 标注工作台契约化，并通过全量回归；P4-3 已完成 `capability_reply_contract` / `capability_rendering_contract` 数据集、baseline 和离线 gate；P4-4 已完成 RAG benchmark 专用 baseline、CLI gate、Admin API 和 WebUI 展示；P4-5A 已完成统一 PR gate 入口和 CI 接入；P4-5B 已完成周期性复跑、手动触发和报告 artifact 归档；P4-5C 已完成第一轮 RAG manual 样本扩充；P4-5D 已完成 memory fixture-backed positive RAG case；P4-5E 已完成 knowledge fixture citation 正例；P4-5F 已完成 sticker fixture sendable 正例；P4-5G 已完成 group_memory fixture 正例；P4-5H 已完成 RAG 过滤约束 fixture。真实样本运营已完成 TimingGate signal audit 周期化、RAG generated → manual 仲裁入口、EvalCandidate 运营规则、候选 reject / defer 仲裁状态、人工仲裁批次审计、EvalCandidate 运营趋势报表、周期运行 manifest、跨 artifact 周期趋势和周期趋势只读调参分析。后续可补充更厚的 TimingSignal 不可变 artifact，或在人工确认后设计可审核调参提案。

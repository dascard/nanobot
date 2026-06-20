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
- `tmp/rag_benchmark/reports/*.json`
- `tmp/rag_benchmark/reports/*.md`

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

P4-5C 已将 manual deterministic gate 的样本从 3 个扩充到 9 个。新增样本仍全部是 `constraint_only`，用于覆盖 memory、knowledge、sticker 和 group_memory 的过滤、scope、citation、sendable 约束。

P4-5D 已新增 `evals/rag_benchmark/fixtures.py` 和 `positive_v1` fixture DB builder。P4-5G 已把 `positive_v1` 从单一 memory 正例扩展为 memory + knowledge + sticker + group_memory 四正例；P4-5H 在保持四个正例不变的基础上补强同 query decoy 和 forbidden 断言：memory 覆盖跨 user、跨 session、跨 source decoy；knowledge 覆盖 `trust_level`、`source_type`、`published_after` decoy；sticker 覆盖其他 stream 和 global decoy；group_memory 保留跨群 decoy。baseline 包含 `memory_fixture_positive_001`、`knowledge_fixture_positive_001`、`sticker_fixture_positive_001` 和 `group_memory_fixture_positive_001`，`metrics.overall.positive_cases=4`、`metrics.source:knowledge.positive_cases=1`、`metrics.source:sticker.positive_cases=1`、`metrics.source:group_memory.positive_cases=1`、`hit@5=1.0`、`mrr=1.0`。knowledge fixture 固定命中 `knowledge:9001:chunk:0`，并通过 `requires_citation=true` 的 `checks.citation=true`；sticker fixture 固定命中 `sticker:9101:sticker`，并通过 `requires_sendable=true` 的 `checks.sendable=true`；group_memory fixture 固定命中 `group_memory:9201:memory`，通过 `requires_group_id=true` 的 `checks.group_filter=true`，并用跨群 decoy `group_memory:9202:memory` 验证 forbidden check 不泄漏。PR gate 使用 `--min-hit-at-5 1.0` 和 `--min-mrr 1.0`，防止正例召回退化。

更新 manual case 或 fixture case 时必须同步 `evals/baselines/rag_benchmark.json`，并保证 baseline 的 `case_scores[*].case_id` 集合与 enabled manual case 和 `fixture_cases("positive_v1")` 的并集一致。`tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract` 会守住这个合同。

## 失败处理

- `pass_rate below threshold`：当前 suite 有失败。先看 `Failed:` 列表，修 case 或修实现。
- `new_failed_cases exceeds threshold`：当前失败不在 baseline 中，是新回归。默认不允许合入。
- `baseline suite mismatch`：baseline 文件不是当前 suite 的基线，需要改回正确路径。
- `fixed_cases` 非空且无新增失败：说明旧失败已修复，可以在审查后刷新 baseline。

## 与 P4 的边界

TimingGate 门禁只负责固定 suite 的确定性回归。通用 `candidates → labeled` 标注闭环、per-capability 数据集扩展、Admin 标注导出和 promote 策略属于 P4 评测体系扩展。当前 P4-1 已先完成 expected 契约、候选标注、promote dry-run、离线 CLI 和首个 `capability_model_routing` 数据集；P4-2 已完成后端 expected 契约和 Admin 标注工作台契约化，并通过全量回归；P4-3 已完成 `capability_reply_contract` / `capability_rendering_contract` 数据集、baseline 和离线 gate；P4-4 已完成 RAG benchmark 专用 baseline、CLI gate、Admin API 和 WebUI 展示；P4-5A 已完成统一 PR gate 入口和 CI 接入；P4-5B 已完成周期性复跑、手动触发和报告 artifact 归档；P4-5C 已完成第一轮 RAG manual 样本扩充；P4-5D 已完成 memory fixture-backed positive RAG case；P4-5E 已完成 knowledge fixture citation 正例；P4-5F 已完成 sticker fixture sendable 正例；P4-5G 已完成 group_memory fixture 正例；P4-5H 已完成 RAG 过滤约束 fixture。下一步转向真实样本运营动作。

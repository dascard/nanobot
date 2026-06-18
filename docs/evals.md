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

Admin 接口兼容旧字段 `expected_json`，但新调用应统一发送 `expected`。WebUI 标注入口也按 `{ expected: expectedJson }` 提交，避免把人工标签静默写成空对象。

## Admin WebUI 工作台改造目标

P4-2 已将 Admin 标注工作台拆为后端契约和 WebUI 两个阶段，设计文档为 `docs/superpowers/specs/2026-06-18-admin-eval-workbench-contract-design.md`，实现计划为 `.Codex/plans/admin-eval-workbench-contract.md`。P4-2A 后端契约 schema/API 已完成定向回归；P4-2B WebUI 工作台仍需按契约接入。

实现阶段必须遵守以下边界：

- WebUI 从 `/api/v1/admin/evals/expected-contract` 读取 expected 契约，不再手写 `expected_action`、`should_learn`、`quality`、`reason`、`delay_seconds` 等不可评分字段。
- 标注请求只提交 scorer 会读取的 `expected` 字段；人工解释写入 `note`，不写入 `expected.reason`。
- Promote 操作必须先发送 `{ "dry_run": true, "target_dataset": "..." }`，展示后端返回的 `target_dataset`、`path` 和 case 摘要后，再发送 apply 请求。
- Apply 成功后展示后端返回的真实 `path`，不能写死 `regression`。

## Dataset 与 Suite

`dataset` 是 `evals/cases/<dataset>` 目录名，用于组织数据集和门禁维度。`suite` 是每个 case 内部的执行类型，决定使用哪个 runner / scorer。

因此二者可以不同。例如 `evals/cases/capability_model_routing/model_routing_stream_required_001.json` 属于 `capability_model_routing` dataset，但 case 内 `suite` 是 `model_routing`，运行时仍使用 `model_routing` runner 和 scorer。

本地运行能力数据集门禁：

```bash
python -B -m evals.run --suite capability_model_routing --baseline evals/baselines/capability_model_routing.json --min-pass-rate 1.0 --max-new-failures 0
```

## RAG Benchmark 边界

`evals/rag_benchmark/` 保持独立 benchmark 入口，不并入通用 `EvalCase`。原因是 RAG benchmark 需要独立的召回样本、索引上下文和评分口径；通用 candidates 闭环只负责把可评分的用户交互样本沉淀为稳定 case。

## 失败处理

- `pass_rate below threshold`：当前 suite 有失败。先看 `Failed:` 列表，修 case 或修实现。
- `new_failed_cases exceeds threshold`：当前失败不在 baseline 中，是新回归。默认不允许合入。
- `baseline suite mismatch`：baseline 文件不是当前 suite 的基线，需要改回正确路径。
- `fixed_cases` 非空且无新增失败：说明旧失败已修复，可以在审查后刷新 baseline。

## 与 P4 的边界

TimingGate 门禁只负责固定 suite 的确定性回归。通用 `candidates → labeled` 标注闭环、per-capability 数据集扩展、Admin 标注导出和 promote 策略属于 P4 评测体系扩展。当前 P4-1 已先完成 expected 契约、候选标注、promote dry-run、离线 CLI 和首个 `capability_model_routing` 数据集；P4-2 已进入 Admin 标注工作台契约化与 promote 预检 UI 阶段；RAG 标注闭环和更多 suite 的 PR gate 留在 P4 后续阶段推进。

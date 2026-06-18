# P4-5C RAG manual 样本扩充设计

设计日期：2026-06-18

## 背景

P4-4 已为 `evals.rag_benchmark` 建立专用 baseline diff 和 gate，P4-5A / P4-5B 已把 RAG manual deterministic gate 接入 PR gate、push gate、周期性复跑和 artifact 归档。当前稳定 gate 使用：

```bash
python -B -m evals.rag_benchmark.run \
  --manual evals/cases/rag_benchmark/manual \
  --generated tmp/rag_benchmark/empty \
  --provider-mode deterministic \
  --manual-only \
  --baseline evals/baselines/rag_benchmark.json \
  --min-pass-rate 1.0 \
  --max-new-failures 0 \
  --max-degraded-rate 0.0 \
  --max-unexpected-source-rate 0.0
```

现有 manual 样本只有 3 个，并且全部是 `constraint_only`：

- `group_memory_manual_filter_constraint_001`
- `knowledge_manual_citation_constraint_001`
- `sticker_manual_generic_constraint_001`

这些样本的共同点是 `allow_empty=true`、不依赖固定 candidate id、只验证过滤、citation、sendable、group id 和 reranker 输入规模等稳定约束。Baseline 当前 `positive_cases=0`，`hit@5=0.0`，`mrr=0.0`。这说明当前 RAG gate 能证明部分安全边界，但还缺少更多 source / filter / scope 约束覆盖。

另一个缺口是 baseline 文件没有强制与 manual case 集合一致。新增通过的 case 即使没有更新 baseline，现有 gate 仍可能因为 `total_delta` 不作为失败条件而通过，导致后续 diff 可读性下降。

## 目标

- 扩充仓库内 `evals/cases/rag_benchmark/manual/*.json` 的稳定 manual case。
- 保持稳定 gate 不依赖 generated case、真实生产 DB 指纹、runtime provider 或外部模型。
- 覆盖 memory、knowledge、sticker、group_memory 的更多过滤与 scope 约束。
- 增强 baseline 合同测试，要求 `evals/baselines/rag_benchmark.json` 与当前 manual case 集合一致。
- 更新 baseline，使 `total_cases`、`passed_cases`、`case_scores` 和当前 deterministic manual gate 输出一致。
- 同步 `docs/evals.md`、`docs/todo.md` 和 `docs/plan_walkthrough.md` 的 P4-5C 状态与验证记录。

## 非目标

- 不改 Admin API 或 WebUI。现有页面会自然读取新增 manual JSON，运营入口保持不变。
- 不新增 positive exact candidate case。没有固定 fixture DB 时，positive case 会依赖真实 DB 中的 candidate id，不适合作为稳定 PR gate。
- 不把 generated case 纳入 `evals/baselines/rag_benchmark.json`。
- 不启用 `provider_mode=runtime`。
- 不新增 latency 约束。`max_latency_ms` 对 CI 机器负载敏感，不适合作为当前稳定 gate 条件。
- 不修改 RAG 服务主链路、reranker、embedding 或 sampler。

## 方案对比

### 方案 A：只扩 `constraint_only` manual case，并补 baseline 一致性守卫

新增多组 `constraint_only + allow_empty=true` manual case。样本使用 sentinel user、session、group、stream 或未来日期过滤，让空结果成为稳定通过条件；如果对应服务忽略过滤或泄漏候选，则 `max_merged_candidates=0`、`max_reranker_candidates=0`、`expected_source_type`、`requires_*` 等约束会失败。

优点：不依赖真实 DB 数据，能直接进入 PR gate；改动集中，验证成本低。缺点：仍不覆盖“确实召回目标候选”的 positive 指标，`positive_cases` 继续为 0。

### 方案 B：新增 fixture DB，并加入 positive exact manual case

为 RAG benchmark 准备固定 SQLite fixture，seed memory / sticker / knowledge / group_memory 数据，然后新增 positive case 验证 candidate id 命中。

优点：可以让 `hit@5`、`mrr` 等指标有实际含义。缺点：需要设计 fixture 生命周期、schema 初始化、索引写入和 adapter 运行入口；会扩大 P4-5C 范围，也需要确保 PR gate 使用 fixture 而不是默认 `data/nanobot.db`。

### 方案 C：直接从当前真实 DB 采样 generated case 并转 manual

使用现有 sampler 从本地 DB 生成候选，人工确认后保存为 manual case，并更新 baseline。

优点：样本更接近真实数据。缺点：candidate id 与本地 DB 指纹强绑定，不适合仓库稳定 gate；换环境后容易失败。

## 决策

采用方案 A。

P4-5C 当前阶段优先扩充稳定 `constraint_only` manual case，并补上 baseline 与 manual case 集合一致性测试。Positive exact 样本需要先设计 fixture DB 或专用 fixture runner，单独作为后续阶段推进，不能把真实 DB candidate id 直接放进 PR gate baseline。

## 样本设计

新增 6 个 manual case：

| case id | source_type | 覆盖点 | 稳定性来源 |
|---------|-------------|--------|------------|
| `memory_manual_empty_user_session_constraint_001` | `memory` | `user_id` / `session_id` 过滤不能泄漏候选 | sentinel user/session 应稳定为空；泄漏时 count 上限失败 |
| `memory_manual_digest_source_constraint_001` | `memory` | `source=digest` 时不能混入 `session_summary` | 空结果可过；有结果时 `expected_source_type=memory_digest` |
| `knowledge_manual_future_publish_filter_constraint_001` | `knowledge` | 未来发布时间过滤必须生效 | `published_after=2999-01-01` 应稳定为空 |
| `knowledge_manual_high_trust_citation_constraint_001` | `knowledge` | 高信任过滤下仍要求 citation | 空结果可过；有结果时必须是 knowledge 且有 citation |
| `sticker_manual_empty_scope_constraint_001` | `sticker` | 不存在 stream 且禁用 global 时不能泄漏全局表情 | sentinel stream + `include_global=false` 应稳定为空 |
| `group_memory_manual_empty_group_filter_constraint_001` | `group_memory` | 不存在 group 时不能泄漏其他群记忆 | sentinel group 应稳定为空 |

这些 case 的 `meta.origin` 使用 `manual_hard`，`meta.sensitivity` 使用 `safe`，便于和 generated / local DB 样本区分。

## Baseline 合同

增强 `tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract`：

- 加载 `evals/cases/rag_benchmark/manual`。
- 忽略 `status=disabled` 的 case。
- 断言 baseline 的 `metrics.overall.total_cases` 等于 enabled manual case 数。
- 断言 baseline 的 `case_scores[*].case_id` 集合与 enabled manual case id 集合完全一致。
- 断言 baseline 的 `provider_mode=deterministic`、`case_scope=manual`、`suite=rag_benchmark`。

这样新增样本后，如果只加 JSON 但忘记更新 baseline，定向测试会红灯；如果删除或改名 manual case 后忘记同步 baseline，也会红灯。

## Baseline 更新规则

新增 case 后运行 RAG manual deterministic gate，使用生成报告更新 `evals/baselines/rag_benchmark.json`：

- `metrics.overall.total_cases` 从 3 增加到 9。
- `metrics.overall.passed_cases` 增加到 9。
- `metrics.overall.pass_rate` 保持 `1.0`。
- 全部新增 case 写入 `case_scores`，`ok=true`、`errors=[]`。
- 因本阶段不新增 positive case，`positive_cases`、`hit@1`、`hit@3`、`hit@5` 和 `mrr` 仍保持 `0.0`。
- `failed_cases` 继续为空。

如果 gate 输出出现失败，不刷新 baseline，应修 case 或实现，直到 deterministic manual gate 通过。

## Admin / WebUI 边界

P4-5C 的稳定样本来源是仓库内 `evals/cases/rag_benchmark/manual/*.json`。Admin / WebUI 保持运营入口定位：

- 可以查看、运行、临时新增、更新、删除 manual case。
- 保存 manual case 时后端会强制 `meta.origin="manual"`，更新进入 backup，删除进入 trash。
- generated case 是本地 DB 采样候选，只能作为人工筛选素材。
- 手工提交仓库 JSON 不需要改 Admin / WebUI；新增 case 会通过现有 loader 和页面自然展示。

只有当范围扩展到批量导入、generated 一键 promote、baseline 编辑或新增专用 UI 字段时，才需要单独设计 Admin / WebUI 改造。

## 测试策略

### 红灯

1. 先增强 baseline 合同测试。
2. 新增 6 个 manual JSON 后暂不更新 baseline。
3. 运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract -v -p no:cacheprovider
```

预期失败：baseline 的 case id 集合或 `total_cases` 与 manual case 不一致。

### 绿灯

1. 更新 `evals/baselines/rag_benchmark.json`。
2. 运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py -v -p no:cacheprovider
```

预期通过。

### Gate 验证

运行：

```bash
python -B -m evals.rag_benchmark.run \
  --manual evals/cases/rag_benchmark/manual \
  --generated tmp/rag_benchmark/empty \
  --provider-mode deterministic \
  --manual-only \
  --baseline evals/baselines/rag_benchmark.json \
  --min-pass-rate 1.0 \
  --max-new-failures 0 \
  --max-degraded-rate 0.0 \
  --max-unexpected-source-rate 0.0
```

预期输出 `cases=9 passed=9 failed=0` 和 `Gate passed`。

### 集成回归

运行：

```bash
python -B -m pytest tests/test_rag_benchmark.py tests/test_eval_baseline.py -v -p no:cacheprovider
bash scripts/run_eval_pr_gate.sh
bash scripts/run_eval_periodic.sh
python -B -m pytest tests/ -v -p no:cacheprovider
```

## 分阶段提交

- 设计阶段：`docs(评测): 设计 RAG manual 扩样`
- 实现计划：`docs(计划): 记录 RAG manual 扩样计划`
- P4-5C-1 baseline 合同守卫：`test(评测): 收紧 RAG baseline 合同`
- P4-5C-2 manual case 扩充与 baseline 更新：`test(评测): 扩充 RAG manual 样本`
- P4-5C-3 文档收口：`docs(评测): 收口 RAG manual 扩样状态`

## 验收标准

- 仓库内 manual case 数从 3 增加到 9。
- `evals/baselines/rag_benchmark.json` 与 enabled manual case 集合一致。
- RAG manual deterministic gate 输出 `cases=9 passed=9 failed=0` 和 `Gate passed`。
- PR gate 和周期性 gate 都通过。
- `docs/evals.md`、`docs/todo.md` 和 `docs/plan_walkthrough.md` 记录 P4-5C 的状态、边界和验证结果。
- 本阶段不修改 Admin / WebUI，不提交 generated case、报告文件或真实 DB 派生内容。

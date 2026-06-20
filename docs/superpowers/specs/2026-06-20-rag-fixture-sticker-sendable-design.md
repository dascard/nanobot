# P4-5F RAG sticker fixture sendable 正例门禁设计

设计日期：2026-06-20

## 背景

P4-5D 已把 RAG stable gate 从纯 manual `constraint_only` case 扩展为 `manual+fixture`，并新增 `memory_fixture_positive_001`。P4-5E 已在同一个 `positive_v1` fixture preset 中补齐 `knowledge_fixture_positive_001`，固定验证 `requires_citation=true` 的 citation 正例。

当前 `positive_v1` 已覆盖 memory 与 knowledge 两条 source，但 `docs/todo.md` 和 `docs/plan_walkthrough.md` 的下一步仍指向更多 fixture source 覆盖，典型方向是 sticker / group_memory 正例。manual sticker case 目前仍是 `constraint_only`，可以约束候选数量上限和空结果，但不能证明「可发送表情包可以被稳定召回」。

sticker RAG 已具备适合仓库自包含 fixture 的条件：

- `chunk_from_sticker()` 能把 `StickerMemory` 转为 `SemanticChunk`，并要求 `status=active`、`describe_status=ok`、非 duplicate，以及存在文本索引载荷。
- `StickerRagService.query()` 在 `include_debug=True` 时会输出 `debug_trace.final_candidates`，其中包含 `candidate_id`、`source_type`、`reply_token` 和 `send_code`。
- RAG benchmark adapter 会把 `reply_token` 或 `send_code` 转成 `BenchmarkCandidate.sendable=True`。
- `score_case()` 已支持 `expected.requires_sendable=true`，可以防止正例只命中不可发送候选。

因此，本阶段优先补 sticker fixture positive case，用最小范围验证 `requires_sendable` 门禁。group_memory 正例继续保留为后续 source 覆盖，不与本阶段混在一起。

## 目标

在现有 `positive_v1` fixture preset 中新增一个 sticker positive case。该 case 必须在 deterministic provider 下稳定命中固定 sticker candidate，并通过 `requires_sendable=true` 的评分检查。

成功标准：

- `fixture_cases("positive_v1")` 返回 memory、knowledge、sticker 三类 fixture 正例。
- fixture SQLite DB 包含固定 `StickerMemory` 与对应 `SemanticIndexItem`。
- 新增 case `sticker_fixture_positive_001` 的 expected candidate 为 `sticker:9101:sticker`。
- 新增 case 设置 `requires_sendable=true`，并在 benchmark scoring 中得到 `checks.sendable=true`。
- RAG stable gate 的 `positive_cases` 从 2 增加到 3。
- `evals/baselines/rag_benchmark.json` 与新增 case 后的 stable gate 合同一致。
- PR gate 与 periodic gate 不需要新增脚本参数，继续通过 `--fixture positive_v1` 自动覆盖新增 sticker fixture。

## 非目标

- 不新增 `positive_v2` fixture preset，不拆分现有 gate 参数。
- 不改生产 DB schema，不迁移真实 `sticker_memories` 数据。
- 不改 Admin / WebUI，不新增 RAG Benchmark 页面功能。
- 不启用 runtime provider，不依赖外部 embedding / reranker 模型。
- 不调整 RAG 阈值、relevance gate、rerank 权重或 `hit@5` / `mrr` 门槛。
- 不重构 `StickerRagService` 主流程；除非 fixture 暴露确定性 bug，否则只扩展 fixture seed、测试和 baseline。
- 不在本阶段加入 group_memory positive case；group_memory 选择逻辑包含证据、注入策略、类型限额和渲染预算，适合作为后续独立阶段。

## 现有合同

fixture 入口位于 `evals/rag_benchmark/fixtures.py`：

- `FIXTURE_PRESET = "positive_v1"`。
- `fixture_cases()` 当前返回 memory 与 knowledge 两个 positive case。
- `build_fixture_db()` 会覆盖创建 fixture SQLite DB，并调用 `seed_positive_fixture_db()` 写入 fixture 数据。

sticker RAG 的硬过滤条件包括：

- `StickerMemory.status == "active"`。
- `StickerMemory.dedupe_status != "duplicate"` 且 `duplicate_of_id is None`。
- `StickerMemory.describe_status == "ok"`。
- scope 命中 `chat_stream_id` 或允许 global。
- `sticker_has_text_index_payload(row)` 为真。
- `is_sticker_replyable(row)` 为真。

benchmark 侧 sendable 约束已经存在：

- `BenchmarkExpected.requires_sendable` 表示该 case 要求返回候选可发送。
- `BenchmarkCandidate.sendable` 来自 adapter 对 debug candidate 的 `send_code` / `reply_token` 判断。
- `score_case()` 在 `requires_sendable=true` 时检查所有返回候选的 `sendable` 是否为 `True`。

## 方案对比

### 方案 A：先做 group_memory fixture positive

插入固定 `GroupMemory`，调用 `GroupMemoryRetrievalService.select()`，验证 `group_memory:<id>:memory` 命中。

优点是覆盖尚未进入 positive gate 的 group memory source；缺点是选择逻辑依赖 confidence、decay、evidence、relevance、type limit 和 render budget，多项因素会影响排序。它更适合独立设计一组正例和过滤负例。

### 方案 B：先做 sticker fixture positive

插入固定 `StickerMemory`，用 `chunk_from_sticker()` + `upsert_semantic_chunks()` 写入 semantic index，新增 `requires_sendable=true` 的 positive case。

优点是数据链路与现有 memory / knowledge fixture 形态一致，且能直接覆盖 `sendable` 评分门禁；硬过滤条件明确，fixture 可通过固定 `send_code`、`description`、`tags_json` 和 `describe_status` 保持确定性。缺点是 group_memory source 仍需后续补齐。

### 方案 C：一次加入 sticker 与 group_memory 两个 positive case

同一阶段把 `positive_v1` 扩展到 4 个正例。

优点是一次性覆盖更多 source；缺点是测试、baseline 和文档变更面更大，若 group_memory 选择逻辑暴露问题，会拖慢 sticker sendable 门禁落地。

## 决策

采用方案 B。

P4-5F 聚焦 sticker fixture sendable 正例，先把「可发送表情包稳定召回」变成 gate 合同。group_memory 作为后续阶段单独推进，避免在同一阶段混入不同选择逻辑和不同风险面。

## Fixture 数据设计

新增固定常量：

| 常量 | 值 |
|------|----|
| `STICKER_CASE_ID` | `sticker_fixture_positive_001` |
| `STICKER_ID` | `9101` |
| `STICKER_CANDIDATE_ID` | `sticker:9101:sticker` |
| `STICKER_CHAT_STREAM_ID` | `group:rag-fixture-sticker` |
| `STICKER_QUERY` | `开心拍桌表情包` |
| `STICKER_INDEX_VERSION` | `fixture:v1:sticker` |

新增固定 `StickerMemory`：

| 字段 | 值 |
|------|----|
| `id` | `9101` |
| `chat_stream_id` | `group:rag-fixture-sticker` |
| `sticker_hash` | `fixture-sticker-positive-001` |
| `file_ref` | `https://example.com/fixture-sticker-positive-001.png` |
| `send_code` | `[CQ:image,file=https://example.com/fixture-sticker-positive-001.png]` |
| `name` | `开心拍桌` |
| `description` | 包含「开心」「拍桌」「表情包」等查询词 |
| `tags_json` | `["开心", "拍桌", "表情包"]` |
| `emotions_json` | `["happy"]` |
| `status` | `active` |
| `describe_status` | `ok` |
| `dedupe_status` | `unique` |
| `duplicate_of_id` | `None` |

写入方式：

1. 在 `seed_positive_fixture_db()` 中插入 `StickerMemory`。
2. 调用 `chunk_from_sticker(sticker)` 生成 `SemanticChunk`。
3. 断言或显式检查返回值非空；如果为空，说明 fixture 字段没有满足 sticker 文本索引或可发送条件。
4. 调用 `upsert_semantic_chunks(db, [chunk], index_version=STICKER_INDEX_VERSION)` 写入 semantic index 和 FTS。

对应 case：

```json
{
  "id": "sticker_fixture_positive_001",
  "suite": "rag_benchmark",
  "source_type": "sticker",
  "case_type": "positive",
  "query": "开心拍桌表情包",
  "filters": {
    "chat_stream_id": "group:rag-fixture-sticker",
    "include_global": false
  },
  "expected": {
    "candidate_ids": ["sticker:9101:sticker"],
    "hit_at": 5,
    "expected_source_type": "sticker",
    "requires_sendable": true
  },
  "meta": {
    "origin": "fixture_exact",
    "sensitivity": "safe",
    "fixture": "positive_v1"
  }
}
```

`include_global=false` 让 case 只验证固定 `chat_stream_id` 范围，避免未来 global sticker fixture 或真实数据混入时影响语义。

## 数据流

1. `build_fixture_db()` 创建空 SQLite fixture DB。
2. `seed_positive_fixture_db()` 写入 memory、knowledge 和 sticker fixture 数据。
3. `chunk_from_sticker()` 将固定 `StickerMemory` 转为 semantic chunk。
4. `upsert_semantic_chunks()` 写入 `semantic_index_items` 和 FTS。
5. RAG runner 加载 manual cases，再追加 `fixture_cases("positive_v1")`。
6. sticker case 进入 `StickerRagService.query()`。
7. service 通过 FTS / deterministic provider 找到 `sticker:9101:sticker`，再通过 hard gate 保留该候选。
8. debug adapter 把 `reply_token` / `send_code` 转为 `BenchmarkCandidate(sendable=True)`。
9. scoring 命中 `STICKER_CANDIDATE_ID`，并通过 sendable check。
10. baseline diff 与 gate 使用新增后的 metrics 和 case_scores。

## 测试策略

测试先行，新增或更新以下断言：

- 新增 `test_rag_benchmark_fixture_db_supports_sticker_positive_case`：
  - 构建 fixture DB。
  - 运行 deterministic benchmark。
  - 断言 `sticker_fixture_positive_001` 存在。
  - 断言结果首个候选为 `sticker:9101:sticker`。
  - 断言候选 `sendable is True`。
  - 断言 score `ok=true`、`rank=1`、`hit@5=true`、`checks.sendable=true`。
- 新增或扩展 scorer 守卫：
  - 构造 `requires_sendable=true` 且候选 `sendable=False` 的结果，断言评分失败。
  - 该测试固定评分边界，避免新增 fixture 只证明 adapter 而不证明 scorer。
- 更新 `test_rag_benchmark_cli_runs_manual_fixture_positive_gate`：
  - 临时 baseline 包含 memory、knowledge、sticker 三个 fixture 正例。
  - 断言 overall positive cases 为 3。
  - 断言 `source:sticker.positive_cases == 1`。
  - 断言 sticker case 的 sendable check 为 true。
- 更新 `test_rag_benchmark_baseline_file_matches_manual_gate_contract`：
  - baseline case set 与 manual + fixture cases 精确一致。
  - 断言 baseline 中的 sticker fixture case 通过 sendable check。

## Baseline 与 gate

实现后必须运行 deterministic RAG stable gate 生成报告，再用真实报告更新 `evals/baselines/rag_benchmark.json`。预期合同变化：

- `overall.total_cases` 从 11 增至 12。
- `overall.positive_cases` 从 2 增至 3。
- `overall_fixture.total_cases` 从 2 增至 3。
- `overall_fixture.positive_cases` 从 2 增至 3。
- `source:sticker.positive_cases` 从 0 增至 1。
- `case_scores` 新增 `sticker_fixture_positive_001`，且 `checks.sendable=true`。

`scripts/run_eval_pr_gate.sh` 和 `scripts/run_eval_periodic.sh` 已使用 `--fixture positive_v1`，本阶段不需要修改脚本参数。

## 验收

定向 fixture 测试：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_memory_positive_case \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_knowledge_positive_case \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_sticker_positive_case \
  -v -p no:cacheprovider
```

scorer 与 baseline 合同测试：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest \
  tests/test_rag_benchmark.py::test_scorer_fails_requires_sendable_when_candidate_lacks_sendable \
  tests/test_rag_benchmark.py::test_rag_benchmark_cli_runs_manual_fixture_positive_gate \
  tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract \
  -v -p no:cacheprovider
```

sticker 相邻回归：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest \
  tests/test_sticker_rag.py \
  tests/test_sticker_memory.py \
  tests/test_rag_benchmark.py \
  -v -p no:cacheprovider
```

RAG stable gate：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
PYTHONDONTWRITEBYTECODE=1 NANOBOT_TESTING=1 DATABASE_URL=sqlite:///:memory: \
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

提交前全量验证：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -m pytest tests/ -v -p no:cacheprovider
```

## 风险与回滚

- 如果 sticker fixture 未命中，优先检查 `chat_stream_id` scope、`send_code` 是否被 `is_sticker_replyable()` 接受，以及 `chunk_from_sticker()` 是否返回非空 chunk。
- 如果 deterministic reranker 排名不稳定，应增强 query 与 `description` / `tags_json` 的词面重叠，而不是降低 gate 阈值。
- 如果 `checks.sendable` 为 `None` 或 `False`，说明 debug adapter 没有读到 `reply_token` / `send_code`，应修 fixture 或 adapter 边界。
- 回滚时可以移除 sticker case、fixture seed、baseline 中对应 case，并把 positive case 数恢复为 2；memory 与 knowledge fixture 不应受影响。

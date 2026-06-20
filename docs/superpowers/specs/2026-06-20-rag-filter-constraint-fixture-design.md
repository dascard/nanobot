# RAG 过滤约束 fixture 设计

日期：2026-06-20

## 背景

P4-5D 到 P4-5G 已把 RAG stable gate 从纯 manual `constraint_only` case 扩展为 `manual+fixture`，并在 `positive_v1` fixture preset 中补齐 memory、knowledge、sticker 和 group_memory 四类正例。当前稳定门禁覆盖 9 个 manual constraint case 和 4 个 fixture positive case，`evals.rag_benchmark.run --fixture positive_v1` 预期输出 `cases=13 passed=13 failed=0`。

现有 manual case 的主要价值是「空作用域不泄漏」和「约束不放宽」。例如 sentinel user/session、未来发布时间、空 sticker stream、空 group_id 都允许空结果并限制候选数。它们不能证明「过滤条件命中目标时，正确候选仍能召回，同时同 query 的错误边界候选被过滤」。目前只有 `group_memory_fixture_positive_001` 同时具备目标命中和跨群 decoy 的 `forbidden_candidate_ids` 断言。

本阶段目标是把这类 decoy 防泄漏能力推广到 memory、knowledge 和 sticker 的现有 positive fixture。方案不新增运行时功能，不改生产 RAG 服务，不新建 gate 脚本参数，而是在固定 fixture DB 中补充同 query decoy，并用现有 scorer 的 `forbidden_candidate_ids`、`expected_source_type`、`requires_citation` 和 `requires_sendable` 固化过滤边界。

## 当前能力

`evals/rag_benchmark/schema.py` 已有足够表达力：

- `case_type` 支持 `positive`、`negative`、`constraint_only`。
- `expected.candidate_ids` 表达 must-hit 候选。
- `expected.forbidden_candidate_ids` 表达 must-not-return 候选。
- `expected.expected_source_type` 表达候选 source 类型约束。
- `expected.requires_citation`、`requires_sendable`、`requires_group_id` 表达 source 专属布尔约束。
- `filters` 是 source-specific dict，已承载 user/session、source、trust、date、stream、group 等过滤参数。

`evals/rag_benchmark/scoring.py` 已能完成本阶段需要的判定：

- positive case 未命中 expected candidate 会失败。
- 返回 forbidden candidate 会失败。
- 返回 unexpected source type 会失败。
- citation、sendable、group_filter 检查失败会失败。
- hit@1/3/5 和 MRR 只统计 positive case。

当前不足不是 schema 或 scorer 缺字段，而是 fixture 数据不足：memory、knowledge 和 sticker 只有目标正例，没有同 query、同语义强度的 decoy，因此过滤边界退化时可能只表现为排名变化，未必被明确归因到泄漏。

## 方案选型

### 方案 A：新增 `constraint_v1` fixture preset

新建独立 preset，放入一批 `constraint_only` case，用空结果和候选数上限验证过滤约束。

优点是边界清晰，不影响现有 `positive_v1` 的指标含义。缺点是会引入新 CLI choices、脚本参数、baseline scope 和更多合同测试；更重要的是 `constraint_only + allow_empty=true` 容易出现「空通过」，仍无法证明目标正例可命中。

### 方案 B：增强现有 `positive_v1` 正例（推荐）

保留 4 个 positive case 不变，在现有 seed 中为 memory、knowledge 和 sticker 增加同 query decoy，并把 decoy candidate id 写入对应 positive case 的 `forbidden_candidate_ids`。

优点是复用现有 stable gate、baseline、PR gate 和 periodic gate；每个 case 同时证明「目标能命中」与「decoy 不泄漏」；不会增加 case 数量，也不会让 positive metrics 口径膨胀。缺点是 baseline 的 total / positive 数量不会变化，文档和测试需要明确这是「fixture 强化」而不是「新增 case」。

### 方案 C：扩展通用 metadata scorer

为 `BenchmarkExpected` 增加 `metadata_equals`、`metadata_not_equals`、`forbidden_source_types` 等通用字段，让 scorer 直接检查候选 metadata。

优点是长期表达力更强。缺点是需要 adapter 更完整地暴露 user/session/trust/date/stream 元数据，还要为每个 source 统一字段语义；本阶段用 candidate id forbidden list 已能覆盖最关键的泄漏风险，先做通用 scorer 属于过早抽象。

本阶段采用方案 B。

## 设计目标

- `positive_v1` 仍只包含 4 个 positive fixture case，不新增 fixture preset。
- memory 正例继续固定命中 `memory_digest:fixture-memory-positive-001:card:0`，新增跨 user、跨 session、跨 source decoy，并全部列入 forbidden。
- knowledge 正例继续固定命中 `knowledge:9001:chunk:0`，新增低 trust、错误 `source_type`、旧发布时间 decoy，并全部列入 forbidden。
- sticker 正例继续固定命中 `sticker:9101:sticker`，新增其他 stream 和 global decoy，并全部列入 forbidden。
- group_memory 保留 P4-5G 已有跨群 decoy，不在本阶段改变 case 数量。
- stable gate 仍预期 `cases=13 passed=13 failed=0`，`positive_cases=4`，`hit@5=1.0`，`mrr=1.0`。
- PR gate 和 periodic gate 继续使用 `--fixture positive_v1`，不新增脚本参数。

## 非目标

- 不修改生产 RAG 过滤逻辑。
- 不新增 `requires_user_id`、`requires_session_id`、`requires_chat_stream_id` 等 scorer 字段。
- 不把 fixture preset 拆成多个 preset。
- 不修改 Admin / WebUI。
- 不调整 RAG 阈值、reranker 策略或 provider 模式。
- 不从真实生产数据库采样。

## Source 级设计

### Memory：user/session/source 隔离

现有 case：

- `id`: `memory_fixture_positive_001`
- `query`: `KohakuVQ 端口冲突`
- `filters.source`: `digest`
- `filters.user_id`: `rag_fixture_user`
- `filters.session_id`: `rag_fixture_session`
- `expected.candidate_ids`: `memory_digest:fixture-memory-positive-001:card:0`
- `expected.expected_source_type`: `memory_digest`

新增 decoy：

| 常量 | candidate id | 过滤边界 | seed 特征 |
|------|--------------|----------|-----------|
| `MEMORY_OTHER_USER_CANDIDATE_ID` | `memory_digest:fixture-memory-decoy-other-user:card:0` | `user_id` | `user_id=rag_fixture_other_user`，同 query 文本 |
| `MEMORY_OTHER_SESSION_CANDIDATE_ID` | `memory_digest:fixture-memory-decoy-other-session:card:0` | `session_id` | `user_id=rag_fixture_user`，`session_id=rag_fixture_other_session`，同 query 文本 |
| `MEMORY_SESSION_SUMMARY_CANDIDATE_ID` | `session_summary:fixture-memory-decoy-session-summary:digest:level2` | `source=digest` | `source_type=session_summary`，同 user/session，同 query 文本 |

case 增加：

```json
"forbidden_candidate_ids": [
  "memory_digest:fixture-memory-decoy-other-user:card:0",
  "memory_digest:fixture-memory-decoy-other-session:card:0",
  "session_summary:fixture-memory-decoy-session-summary:digest:level2"
]
```

验收断言：

- 目标 candidate 出现在结果中，且 hit@5 为 true。
- 三个 decoy 均不出现在 `result.candidate_ids`。
- `score.forbidden_hits == []`。
- `score.unexpected_source is false`。

这会覆盖 `MemoryRagService.query()` 对 `source_types`、`user_id`、`session_id` 的过滤，以及通用召回层 FTS / vector / recent rows 的隔离边界。

### Knowledge：trust/source/date 过滤

现有 case：

- `id`: `knowledge_fixture_positive_001`
- `query`: `RAG 引用门禁`
- `expected.candidate_ids`: `knowledge:9001:chunk:0`
- `expected.requires_citation`: true

本阶段把正例文档的 trust 固定为 `high`，并收紧 case filters：

```json
"filters": {
  "min_trust_level": "high",
  "source_type": "manual_file",
  "published_after": "2026-01-01"
}
```

新增 decoy：

| 常量 | candidate id | 过滤边界 | seed 特征 |
|------|--------------|----------|-----------|
| `KNOWLEDGE_LOW_TRUST_CANDIDATE_ID` | `knowledge:9002:chunk:0` | `min_trust_level=high` | `trust_level=low`，`document_kind=manual_file`，发布时间合格 |
| `KNOWLEDGE_WRONG_SOURCE_CANDIDATE_ID` | `knowledge:9003:chunk:0` | `source_type=manual_file` | `trust_level=high`，`document_kind=ai_daily`，发布时间合格 |
| `KNOWLEDGE_OLD_PUBLISHED_CANDIDATE_ID` | `knowledge:9004:chunk:0` | `published_after=2026-01-01` | `trust_level=high`，`document_kind=manual_file`，`published_at=2025-01-01` |

case 增加：

```json
"forbidden_candidate_ids": [
  "knowledge:9002:chunk:0",
  "knowledge:9003:chunk:0",
  "knowledge:9004:chunk:0"
]
```

验收断言：

- 目标 candidate 出现在结果中，且 citation check 为 true。
- 三个 decoy 均不出现在 `result.candidate_ids`。
- `score.forbidden_hits == []`。
- `score.checks["citation"] is true`。

这会覆盖 `KnowledgeRagService._passes_filters()` 中 trust、document_kind/source_type 和 published date 的召回后过滤逻辑，同时保留 citation 正例门禁。

### Sticker：stream/global 过滤

现有 case：

- `id`: `sticker_fixture_positive_001`
- `query`: `开心拍桌表情包`
- `filters.chat_stream_id`: `group:rag-fixture-sticker`
- `filters.include_global`: false
- `expected.candidate_ids`: `sticker:9101:sticker`
- `expected.requires_sendable`: true

新增 decoy：

| 常量 | candidate id | 过滤边界 | seed 特征 |
|------|--------------|----------|-----------|
| `STICKER_OTHER_STREAM_CANDIDATE_ID` | `sticker:9102:sticker` | `chat_stream_id` | `chat_stream_id=group:rag-fixture-sticker-other`，同 query 文本，可发送 |
| `STICKER_GLOBAL_CANDIDATE_ID` | `sticker:9103:sticker` | `include_global=false` | `chat_stream_id=global`，同 query 文本，可发送 |

case 增加：

```json
"forbidden_candidate_ids": [
  "sticker:9102:sticker",
  "sticker:9103:sticker"
]
```

验收断言：

- 目标 candidate 出现在结果中，且 sendable check 为 true。
- 两个 decoy 均不出现在 `result.candidate_ids`。
- `score.forbidden_hits == []`。
- `score.checks["sendable"] is true`。

这会覆盖 `StickerRagService._hard_gate_reason()` 中 `scope` 对 `chat_stream_id` 和 global stream 的过滤。由于 sticker 的第一层 FTS / vector 召回会扫全量 sticker 索引，本阶段只要求 final candidates 不泄漏，不要求 recall 阶段零候选。

### Group Memory：保留跨群 decoy

`group_memory_fixture_positive_001` 已有：

- `expected.candidate_ids`: `group_memory:9201:memory`
- `expected.forbidden_candidate_ids`: `group_memory:9202:memory`
- `expected.requires_group_id`: true
- target group: `group_rag_fixture_memory`
- decoy group: `group_rag_fixture_other`

本阶段只保留并强化测试口径：baseline contract 和 fixture 定向测试继续显式断言 `forbidden_hits == []` 与 `checks.group_filter is true`。不新增 group_memory case，避免和 P4-5G 的交付边界重叠。

## 文件变更计划

### `evals/rag_benchmark/fixtures.py`

新增常量：

- memory decoy IDs、source IDs、user/session IDs。
- knowledge decoy document IDs 和 candidate IDs。
- sticker decoy IDs、stream ID 和 global candidate ID。

调整 seed：

- `seed_positive_fixture_db()` 在写入 memory target 后追加 3 个 memory decoy `SemanticChunk`。
- `_seed_knowledge_positive_fixture()` 改为同时写入 target + 3 个 decoy document/chunk/index rows。
- `_seed_sticker_positive_fixture()` 改为同时写入 target + 2 个 decoy sticker/index rows。

调整 case：

- `_memory_positive_case()`、`_knowledge_positive_case()`、`_sticker_positive_case()` 增加 `forbidden_candidate_ids`。
- `_knowledge_positive_case()` 收紧 `min_trust_level` 和 `published_after`。

### `tests/test_rag_benchmark.py`

扩展现有 fixture 正例测试：

- memory fixture 测试断言 3 个 forbidden decoy 不出现。
- knowledge fixture 测试断言 3 个 forbidden decoy 不出现，且 citation check 保持 true。
- sticker fixture 测试断言 2 个 forbidden decoy 不出现，且 sendable check 保持 true。
- CLI fixture gate 测试继续断言 `positive_cases == 4`，并补充 memory / knowledge / sticker fixture 的 `forbidden_hits == []`。
- baseline contract 测试继续断言 case 集合与 `manual + fixture_cases("positive_v1")` 一致，并补充新增 forbidden 命中为空的断言。

### `evals/baselines/rag_benchmark.json`

运行 stable gate 后用真实报告更新。预期合同不改变：

- `metrics.overall.total_cases == 13`。
- `metrics.overall.positive_cases == 4`。
- `metrics.overall_fixture.total_cases == 4`。
- `metrics.overall_fixture.positive_cases == 4`。
- `hit@5 == 1.0`。
- `mrr == 1.0`。
- 新增或保留 memory / knowledge / sticker / group_memory fixture score 的 `forbidden_hits: []`。

如果实际报告只有 latency 或候选统计轻微变化，也以真实 stable gate 报告为准更新 baseline，避免 baseline diff 噪声。

### 文档

实现完成后同步：

- `docs/evals.md`：说明 stable gate 仍为 9 manual + 4 fixture positive，但 fixture positive 已升级为「正例命中 + decoy 不泄漏」。
- `docs/todo.md`：标记 P4-5H 完成，并记录验证命令结果。
- `docs/plan_walkthrough.md`：新增 P4-5H 阶段详情。
- `.Codex/plans/rag-filter-constraint-fixture.md`：记录执行步骤与真实红绿验证。

## TDD 与验收

### 红灯

先写测试，不改 fixture seed：

```bash
python -B -m pytest \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_memory_positive_case \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_knowledge_positive_case \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_sticker_positive_case \
  -v -p no:cacheprovider
```

预期失败原因：

- 新 decoy 常量尚未定义，或 `expected.forbidden_candidate_ids` 未包含 decoy。
- decoy seed 尚未写入，测试无法证明过滤边界。

随后补 CLI / baseline contract 红灯：

```bash
python -B -m pytest \
  tests/test_rag_benchmark.py::test_rag_benchmark_cli_runs_manual_fixture_positive_gate \
  tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract \
  -v -p no:cacheprovider
```

预期失败原因：

- 报告或 baseline 中 memory / knowledge / sticker fixture 的 `forbidden_hits` 断言尚未补齐。

### 绿灯

实现 fixture seed 和 case 变更后，运行：

```bash
python -B -m pytest \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_memory_positive_case \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_knowledge_positive_case \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_sticker_positive_case \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_group_memory_positive_case \
  -v -p no:cacheprovider
```

然后运行 stable gate：

```bash
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

预期输出：

```text
cases=13 passed=13 failed=0
Gate passed
```

### 回归

相邻回归：

```bash
python -B -m pytest tests/test_rag_benchmark.py tests/test_eval_baseline.py -v -p no:cacheprovider
```

PR gate：

```bash
bash scripts/run_eval_pr_gate.sh
```

周期性 gate：

```bash
bash scripts/run_eval_periodic.sh
```

提交前全量：

```bash
python -B -m pytest tests/ -v -p no:cacheprovider
```

文档与 diff 自检：

```bash
rg -n "TO""DO|TB""D|待""定|占""位|xx""x|FIX""ME" docs/superpowers/specs/2026-06-20-rag-filter-constraint-fixture-design.md .Codex/plans/rag-filter-constraint-fixture.md docs/evals.md docs/todo.md docs/plan_walkthrough.md
rg -n "\x{FFFD}" docs/superpowers/specs/2026-06-20-rag-filter-constraint-fixture-design.md .Codex/plans/rag-filter-constraint-fixture.md docs/evals.md docs/todo.md docs/plan_walkthrough.md
git diff --check
```

## 子 agent 分工建议

本阶段实现可拆为互不重叠的 worker 任务，但最终集成由主线程统一验证：

- Worker A：memory fixture decoy。写入范围为 `evals/rag_benchmark/fixtures.py` 的 memory 常量 / case / seed，以及 `tests/test_rag_benchmark.py` 的 memory fixture 测试块。
- Worker B：knowledge fixture decoy。写入范围为 `evals/rag_benchmark/fixtures.py` 的 knowledge 常量 / case / seed，以及 `tests/test_rag_benchmark.py` 的 knowledge fixture 测试块。
- Worker C：sticker fixture decoy。写入范围为 `evals/rag_benchmark/fixtures.py` 的 sticker 常量 / case / seed，以及 `tests/test_rag_benchmark.py` 的 sticker fixture 测试块。

由于三个 worker 都会触碰 `fixtures.py` 和 `tests/test_rag_benchmark.py`，并行实现时必须分区编辑并由主线程合并。若当前上下文足够，主线程顺序实现更稳；若拆给子 agent，建议只让它们提交 patch 建议，不直接同时写同一文件。

## 风险与控制

- memory 的 decoy 如果过滤失败，可能通过 FTS rowid 回查进入最终候选。`forbidden_candidate_ids` 会直接把这种泄漏判为失败。
- knowledge 的 trust/source/date 过滤发生在召回后，不是第一层 SQL 过滤。decoy 使用同 query 文本，确保 `_passes_filters()` 退化时会进入最终候选并被 forbidden check 捕获。
- sticker 的 stream/global 过滤发生在 hard gate，不是第一层 FTS / vector 过滤。验收只要求 final candidates 不泄漏，不把 recall 阶段候选数作为零泄漏指标。
- 本阶段不增加 case 数量，因此 baseline 的 `total_cases` 和 `positive_cases` 不应变化。测试要显式守住 `positive_cases == 4`，避免把过滤增强误写成新增正例。
- 新 decoy 都使用固定 ID 和固定文本，避免依赖真实数据库或 runtime provider。

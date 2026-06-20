# P4-5G RAG group_memory fixture 正例门禁设计

设计日期：2026-06-20

## 背景

P4-5D 已把 RAG stable gate 从纯 manual `constraint_only` case 扩展为 `manual+fixture`，并新增 `memory_fixture_positive_001`。P4-5E / P4-5F 已在同一个 `positive_v1` fixture preset 中补齐 knowledge citation 正例和 sticker sendable 正例。当前 `positive_v1` 已覆盖 memory、knowledge、sticker 三类正例，但 `source:group_memory` 仍只有 manual constraint case，没有固定 positive case。

现有 manual group memory case 可以约束空 group 不泄漏、`group_id` 过滤和空结果允许语义，但它们是 `constraint_only`，不能证明「固定 `GroupMemory` 行能被 benchmark 稳定召回」。因此本阶段补齐 `group_memory` fixture 正例，把 group memory source 纳入 stable RAG gate 的 positive metrics。

`group_memory` 与前三类 fixture 的一个关键差异是：benchmark adapter 不走 semantic index。`evals.rag_benchmark.adapters._run_group_memory()` 直接调用 `GroupMemoryRetrievalService.select()`，再把选中的 `GroupMemory` 行转成 `group_memory:<id>:memory` 候选。因此本阶段只需要 seed `group_memories` 表，不需要写 `semantic_index_items` 或 FTS。

## 目标

在现有 `positive_v1` fixture preset 中新增一个 group memory positive case。该 case 必须在 deterministic provider 下稳定命中固定 group memory candidate，并通过 `requires_group_id=true` 的评分检查。

成功标准：

- `fixture_cases("positive_v1")` 返回 memory、knowledge、sticker、group memory 四类 fixture 正例。
- fixture SQLite DB 包含固定 `GroupMemory(id=9201)` 正例，以及一个跨群 decoy `GroupMemory(id=9202)`。
- 新增 case `group_memory_fixture_positive_001` 的 expected candidate 为 `group_memory:9201:memory`。
- 新增 case 设置 `requires_group_id=true`，并在 benchmark scoring 中得到 `checks.group_filter=true`。
- 新增 case 设置 `forbidden_candidate_ids=["group_memory:9202:memory"]`，防止跨群 decoy 泄漏。
- RAG stable gate 的 `positive_cases` 从 3 增加到 4。
- `evals/baselines/rag_benchmark.json` 与新增 case 后的 stable gate 合同一致。
- PR gate 与 periodic gate 不需要新增脚本参数，继续通过 `--fixture positive_v1` 自动覆盖新增 group memory fixture。

## 非目标

- 不新增 `positive_v2` fixture preset，不拆分现有 gate 参数。
- 不改生产 DB schema，不迁移真实 `group_memories` 数据。
- 不改 Admin / WebUI，不新增 RAG Benchmark 页面功能。
- 不启用 runtime provider，不依赖外部 embedding / reranker 模型。
- 不调整 group memory relevance 阈值、类型限额、渲染预算、decay 权重或 `hit@5` / `mrr` 门槛。
- 不重构 `GroupMemoryRetrievalService` 主流程；除非 fixture 暴露确定性 bug，否则只扩展 fixture seed、测试和 baseline。
- 不在本阶段设计通用过滤约束 fixture；跨群 decoy 只作为 group memory 正例的防泄漏保护。

## 现有合同

fixture 入口位于 `evals/rag_benchmark/fixtures.py`：

- `FIXTURE_PRESET = "positive_v1"`。
- `fixture_cases()` 当前返回 memory、knowledge、sticker 三个 positive case。
- `build_fixture_db()` 会覆盖创建 fixture SQLite DB，并调用 `seed_positive_fixture_db()` 写入 fixture 数据。

group memory benchmark 链路已经具备正例所需接口：

- `BenchmarkExpected.requires_group_id` 已存在。
- `score_case()` 在 `requires_group_id=true` 时检查所有返回候选的 `group_id` 是否等于 `case.filters["group_id"]`。
- `_run_group_memory()` 会调用 `GroupMemoryRetrievalService.select()`，并输出 `BenchmarkCandidate(group_id=<row.group_id>)`。
- candidate id 格式固定为 `group_memory:<row.id>:memory`。

`GroupMemoryRetrievalService.select()` 的硬过滤条件包括：

- `GroupMemory.group_id == normalize_group_session_id(case.filters["group_id"])`。
- `status == "active"`。
- `inject_policy == "auto"`。
- `confidence >= 0.55`。
- `decay_score >= 0.3`。
- `evidence_log_ids_json` 非空。
- 在无 reranker 的 deterministic provider 下，非 `style` 类型需要通过 lexical relevance 下限。

## 方案对比

### 方案 A：只新增 group memory positive case，不加 decoy

插入固定 `GroupMemory(id=9201)`，新增 `requires_group_id=true` 的 positive case。

优点是范围最小；缺点是只能证明正例可召回，不能防止未来 adapter 或 service 忽略 `group_id` 时仍偶然命中。

### 方案 B：新增 group memory positive case，并 seed 跨群 decoy

插入固定正例 `GroupMemory(id=9201)` 和同内容高相关 decoy `GroupMemory(id=9202)`。case 仍是 positive，但 `expected.forbidden_candidate_ids` 包含 decoy，且 `requires_group_id=true`。

优点是仍保持单一正例阶段，同时能顺手覆盖跨群过滤保护；如果查询忽略 group filter，decoy 会触发 forbidden 或 group filter 失败。缺点是 seed 多一行，baseline 和测试需要断言 forbidden candidate 不出现。

### 方案 C：单独设计过滤约束 fixture

新增一个或多个 `constraint_only` fixture case，seed 多源 decoy，专门验证过滤约束。

优点是过滤语义更系统；缺点是范围会扩大到 memory、knowledge、sticker、group memory 多类 source，且会与当前「补 positive 覆盖空洞」目标混在一起。

## 决策

采用方案 B。

P4-5G 聚焦 group memory fixture positive case，同时在 seed 中加入跨群 decoy 作为低成本过滤保护。过滤约束 fixture 后续单独设计，不抢占本阶段边界。

## Fixture 数据设计

新增固定常量：

| 常量 | 值 |
|------|----|
| `GROUP_MEMORY_CASE_ID` | `group_memory_fixture_positive_001` |
| `GROUP_MEMORY_ID` | `9201` |
| `GROUP_MEMORY_DECOY_ID` | `9202` |
| `GROUP_MEMORY_CANDIDATE_ID` | `group_memory:9201:memory` |
| `GROUP_MEMORY_DECOY_CANDIDATE_ID` | `group_memory:9202:memory` |
| `GROUP_MEMORY_GROUP_ID` | `group_rag_fixture_memory` |
| `GROUP_MEMORY_DECOY_GROUP_ID` | `group_rag_fixture_other` |
| `GROUP_MEMORY_QUERY` | `群体记忆 RAG fixture 正例` |

新增固定正例 `GroupMemory`：

| 字段 | 值 |
|------|----|
| `id` | `9201` |
| `group_id` | `group_rag_fixture_memory` |
| `memory_type` | `topic` |
| `content` | `群体记忆 RAG fixture 正例：本群固定用来验证 group_memory 检索命中。` |
| `content_hash` | `fixture-group-memory-positive-001` |
| `cluster_key` | `rag fixture group memory` |
| `evidence_log_ids_json` | `[920101, 920102]` |
| `confidence` | `0.9` |
| `evidence_count` | `2` |
| `decay_score` | `1.0` |
| `status` | `active` |
| `inject_policy` | `auto` |
| `source` | `fixture` |
| `meta_json` | `{"fixture":"positive_v1","evidence_short_summary":"群体记忆 RAG fixture 正例"}` |
| `first_seen` / `last_seen` / `created_at` / `updated_at` | `2026-06-20 00:00:00` |

新增固定 decoy `GroupMemory`：

| 字段 | 值 |
|------|----|
| `id` | `9202` |
| `group_id` | `group_rag_fixture_other` |
| `memory_type` | `topic` |
| `content` | 与正例高度相似，包含同一查询词 |
| `content_hash` | `fixture-group-memory-decoy-001` |
| `confidence` | `0.95` |
| `evidence_count` | `3` |
| `decay_score` | `1.0` |
| `status` | `active` |
| `inject_policy` | `auto` |
| `source` | `fixture` |

decoy 置信度可以高于正例。这样如果查询链路未来遗漏 group filter，decoy 更可能排在前面并触发失败。

对应 case：

```json
{
  "id": "group_memory_fixture_positive_001",
  "suite": "rag_benchmark",
  "source_type": "group_memory",
  "case_type": "positive",
  "query": "群体记忆 RAG fixture 正例",
  "filters": {
    "group_id": "group_rag_fixture_memory",
    "recent_messages": [],
    "max_chars": 1200
  },
  "expected": {
    "candidate_ids": ["group_memory:9201:memory"],
    "forbidden_candidate_ids": ["group_memory:9202:memory"],
    "hit_at": 5,
    "expected_source_type": "group_memory",
    "requires_group_id": true
  },
  "meta": {
    "origin": "fixture_exact",
    "sensitivity": "safe",
    "fixture": "positive_v1"
  }
}
```

## 数据流

1. `build_fixture_db()` 创建空 SQLite fixture DB。
2. `seed_positive_fixture_db()` 写入 memory、knowledge、sticker 和 group memory fixture 数据。
3. group memory fixture seed 只写 `GroupMemory` 行，不写 semantic index。
4. RAG runner 加载 manual cases，再追加 `fixture_cases("positive_v1")`。
5. group memory case 进入 `_run_group_memory()`。
6. adapter 调用 `GroupMemoryRetrievalService.select(group_id="group_rag_fixture_memory", current_user_input=GROUP_MEMORY_QUERY, recent_messages=[], max_items=5, max_chars=1200)`。
7. service 规范化 group id，SQL 层只读取 `group_rag_fixture_memory`，decoy 所在 `group_rag_fixture_other` 不进入候选集。
8. adapter 输出 `BenchmarkCandidate(candidate_id="group_memory:9201:memory", group_id="group_rag_fixture_memory")`。
9. scoring 命中 `GROUP_MEMORY_CANDIDATE_ID`，没有 forbidden hit，并通过 group filter check。
10. baseline diff 与 gate 使用新增后的 metrics 和 case_scores。

## 测试策略

测试先行，新增或更新以下断言：

- 新增 `test_rag_benchmark_fixture_db_supports_group_memory_positive_case`：
  - 构建 fixture DB。
  - 运行 deterministic benchmark。
  - 断言 `group_memory_fixture_positive_001` 存在。
  - 断言结果候选包含 `group_memory:9201:memory`。
  - 断言结果候选不包含 `group_memory:9202:memory`。
  - 断言候选 `group_id == "group_rag_fixture_memory"`。
  - 断言 score `ok=true`、`hit@5=true`、`checks.group_filter=true`。
- 更新 `test_rag_benchmark_cli_runs_manual_fixture_positive_gate`：
  - 临时 baseline 包含 memory、knowledge、sticker、group memory 四个 fixture 正例。
  - 断言 overall positive cases 为 4。
  - 断言 `source:group_memory.positive_cases == 1`。
  - 断言 group memory case 的 group filter check 为 true。
- 更新 `test_rag_benchmark_baseline_file_matches_manual_gate_contract`：
  - baseline case set 与 manual + fixture cases 精确一致。
  - 断言 baseline 中的 group memory fixture case 通过 group filter check。
  - 断言 baseline 中的 group memory fixture case 没有 forbidden hit。

## Baseline 与 gate

实现后必须运行 deterministic RAG stable gate 生成报告，再用真实报告更新 `evals/baselines/rag_benchmark.json`。预期合同变化：

- `overall.total_cases` 从 12 增至 13。
- `overall.positive_cases` 从 3 增至 4。
- `overall_fixture.total_cases` 从 3 增至 4。
- `overall_fixture.positive_cases` 从 3 增至 4。
- `source:group_memory.positive_cases` 从 0 增至 1。
- `case_scores` 新增 `group_memory_fixture_positive_001`，且 `checks.group_filter=true`、`forbidden_hits=[]`。

`scripts/run_eval_pr_gate.sh` 和 `scripts/run_eval_periodic.sh` 已使用 `--fixture positive_v1`，本阶段不需要修改脚本参数。

## 验收

定向 fixture 测试：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_memory_positive_case \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_knowledge_positive_case \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_sticker_positive_case \
  tests/test_rag_benchmark.py::test_rag_benchmark_fixture_db_supports_group_memory_positive_case \
  -v -p no:cacheprovider
```

baseline 合同测试：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest \
  tests/test_rag_benchmark.py::test_rag_benchmark_cli_runs_manual_fixture_positive_gate \
  tests/test_rag_benchmark.py::test_rag_benchmark_baseline_file_matches_manual_gate_contract \
  -v -p no:cacheprovider
```

group memory 相邻回归：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python -B -m pytest \
  tests/test_group_memory_rag.py \
  tests/test_group_memory_injection.py \
  tests/test_semantic_adapters.py::test_group_memory_one_row_one_chunk \
  tests/test_rag_debug.py::test_rag_debug_group_memory_uses_retrieval_service_not_stub \
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
python -m pytest tests/ -v
```

## 文档同步

实现和 baseline 通过后，同步以下文档：

- `docs/evals.md`：更新 stable RAG gate case 数、fixture 正例数和 `source:group_memory` 覆盖。
- `docs/todo.md`：把 P4-5G 标记为完成，下一步转向过滤约束 fixture 或真实样本运营动作。
- `docs/plan_walkthrough.md`：新增 P4-5G 完成记录、验证结果和下一步。
- `.Codex/plans/rag-group-memory-fixture.md`：勾选执行进度。

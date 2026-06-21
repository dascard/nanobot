# H30 RAG query 拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 将 H30 标为当前 P3 中唯一未完成的 HIGH 可维护性项。范围集中在两个函数：

- `core/knowledge_rag.py` 的 `KnowledgeRagService.query()`
- `core/memory_rag.py` 的 `MemoryRagService.query()`

两个函数都把召回、候选过滤、debug trace、reranker、relevance gate 和结果组装塞在同一个方法里。它们已有较多回归测试，但测试主要覆盖最终行为，缺少对 `stats`、`debug_trace`、`score_breakdown` 和 degraded contract 的集中 characterization。H30 的目标是降低维护成本，为后续更细的 RAG 流式分段与输出契约收敛打基础。

## 目标

- 保持 `query()` 的 public signature、返回结构、排序语义和 debug trace 语义不变。
- 先补 characterization tests，锁住当前外部契约，再做纯重构。
- 将 `KnowledgeRagService.query()` 拆成模块内私有阶段：召回、debug 初始化、候选过滤、rerank、relevance gate、结果组装。
- 将 `MemoryRagService.query()` 拆成模块内私有阶段：召回、debug 初始化、候选过滤、rerank 准备、rerank、relevance gate、parent 结果组装。
- 在第一阶段不引入跨模块基类，不改变 semantic retriever、scoring、reranker provider 或 benchmark gate。

## 非目标

- 不调整 RAG 权重、阈值、排序公式、recency 口径或 relevance gate 规则。
- 不改变 `KnowledgeQueryTool`、`MemoryQueryTool`、Admin RAG debug、RAG benchmark adapter 的消费契约。
- 不把 H30 与 `StickerRagService.query()` 拆分绑定在同一批提交里。
- 不新增数据库字段、迁移、外部依赖或真实 embedding / reranker 调用。
- 不更新 RAG baseline，不生成新的 benchmark 样本，不修改 prompt runtime 模板。

## 现状拆解

### KnowledgeRagService.query()

当前流程为：

1. 兼容 `date_start/date_end` 到 `published_after/published_before`。
2. 检查是否存在向量索引行；有向量行时才调用 embedding provider。
3. 执行 FTS、vector、recent rows 三路召回，并补载 recent limit 外的 recall rows。
4. 按 `fts_ordered -> vector_ordered -> recent_rows` 合并去重。
5. 批量加载 `KnowledgeDocument`，初始化 debug trace。
6. 逐行要求 citation 有效，过滤非 active document，并按 trust、source_type、domain、published date 过滤。
7. 补算 lexical / semantic，按当前准入阈值生成 `_KnowledgeCandidate`，预排序并截到 100。
8. 运行 reranker，metadata 使用 citation，`top_k=100`。
9. 运行 relevance gate，knowledge 传入 `min_reranker=self.min_reranker`。
10. 按 final score 排序，构造 `items`、`stats` 和 `debug_trace`。

### MemoryRagService.query()

当前流程为：

1. 将 `source` 映射为 `memory_digest`、`session_summary` 或二者合集。
2. 按 source、user、session 检查向量索引行；有向量行时才调用 embedding provider。
3. 执行 FTS、vector、recent rows 三路召回，并补载 recent limit 外的 recall rows。
4. 按 `fts_ordered -> vector_ordered -> recent_rows` 合并去重。
5. 初始化 debug trace。
6. 补算 lexical / semantic，按当前准入阈值生成 `_Candidate`，预排序并截到 80。
7. 计算 weak lexical fallback 的 `reranker_skip_reason`，reranker 输入截到 50，超额项标记 `reranker_budget`。
8. 运行 reranker，metadata 使用 row `meta_json`，记录 `reranker_latency_ms`。
9. 运行 relevance gate，memory 不传 `min_reranker`。
10. 按 `(source_type, source_id)` 聚合 parent，每个 parent 保留 top 2 cards，构造 `items`、`stats` 和 `debug_trace`。

## 方案对比

### 方案 A：模块内分阶段拆分

在 `core/knowledge_rag.py` 和 `core/memory_rag.py` 内分别抽私有 dataclass / helper，不新增公共基类。两个 `query()` 只负责串联阶段和返回结果。

优点：diff 集中，行为变化面小，失败时可以定位到单模块。缺点：短期仍保留两处相似 recall 代码。

### 方案 B：先抽公共 RAG base

新增 `core/semantic/rag_base.py`，把三路 recall、score maps、debug hit 组装和 reranker 回填抽成共享工具，两个服务直接复用。

优点：重复更少。缺点：第一阶段同时改两个服务的共享行为，knowledge 的 citation / document filter 和 memory 的 parent grouping 差异会让抽象边界不稳定。

### 方案 C：只补测试，不拆生产代码

先补 characterization tests 与契约扫描，暂不改 `query()` 实现。

优点：风险最低。缺点：不能解决 H30 的主要维护性问题，也不会改善后续流式分段的代码边界。

## 决策

采用方案 A。

第一阶段只做模块内私有 helper 拆分，保留所有现有 public contract。第二阶段在两个模块的阶段边界稳定后，再评估是否抽 `core/semantic/rag_base.py` 承载通用 recall context、`_query_vector()`、`_merge_recall_rows()` 和 debug hit 组装。

## 设计边界

### 共享原则

- `query()` signature 不变。
- `result` 顶层字段保持 `query`、`source`、`degraded`、`fallback_reason`、`stats`、`items`；`include_debug=True` 时继续附加 `debug_trace`。
- FTS / vector 命中必须能越过 recent rows limit。
- recall 合并顺序保持 `fts -> vector -> recent`。
- 没有向量索引行时不调用 embedding provider。
- `limit <= 0` 仍按当前实现通过 `max(1, int(limit))` 兜底。
- `degraded` 继续由 `reranker_provider is None` 决定。

### Knowledge 模块

在 `core/knowledge_rag.py` 内新增模块私有结构：

- `_KnowledgeRecallResult`：保存 `rows`、`rows_by_id`、`fts_hits`、`vector_hits`、`lexical_by_id`、`bm25_by_id`、`semantic_by_id` 和 `query_vector`。
- `_KnowledgeQueryDebug` 可用普通 `dict[str, Any]` 保持现有 JSON 形态，不引入新公开类型。

拆出的 helper：

- `_recall(query) -> _KnowledgeRecallResult`
- `_build_debug_trace(...) -> dict[str, Any]`
- `_filter_candidates(...) -> tuple[list[_KnowledgeCandidate], dict[str, int]]`
- `_rerank(query, candidates, debug_trace) -> None`
- `_apply_relevance_gate(candidates, degraded, debug_trace) -> list[_KnowledgeCandidate]`
- `_build_result(query, ranked, candidates, debug_trace, stats, limit, degraded) -> dict[str, Any]`

必须保留的 knowledge 契约：

- 无 citation 的候选被丢弃，并写入 `stats.skipped_no_citation` 与 `debug_trace.skipped.no_citation`。
- 非 active document、trust、source_type、domain、published date 过滤写入 `stats.skipped_filter` 与 `debug_trace.skipped.filter`。
- reranker 输入 `SemanticCandidate.source_type` 固定为 `"knowledge"`，metadata 使用 citation，`top_k=100`。
- knowledge relevance gate 继续传 `min_reranker=self.min_reranker`。
- item 保留 `candidate_id`、`document_id`、`chunk_id`、`title`、`text`、`citation`、`trust_level`、`score`、`score_breakdown`。
- `score_breakdown` 保留 `lexical`、`semantic`、`reranker`、`raw_reranker`、`trust`、`recency`、`final`；debug candidate 继续包含 `bm25_raw`。

### Memory 模块

在 `core/memory_rag.py` 内新增模块私有结构：

- `_MemoryRecallResult`：保存 `rows`、`rows_by_id`、`fts_hits`、`vector_hits`、`lexical_by_id`、`bm25_by_id`、`semantic_by_id` 和 `query_vector`。
- `_MemoryRerankResult`：保存 `rerank_candidates` 和 `reranker_latency_ms`。

拆出的 helper：

- `_recall(query, source_types, user_id, session_id) -> _MemoryRecallResult`
- `_build_debug_trace(...) -> dict[str, Any]`
- `_filter_candidates(...) -> tuple[list[_Candidate], dict[str, int]]`
- `_prepare_rerank_candidates(candidates, bm25_by_id) -> list[_Candidate]`
- `_rerank(query, candidates, rerank_candidates, debug_trace) -> int`
- `_apply_relevance_gate(candidates, degraded, debug_trace) -> list[_Candidate]`
- `_build_result(query, source, parent_items, candidates, rerank_candidates, debug_trace, stats, degraded) -> dict[str, Any]`

必须保留的 memory 契约：

- `source="all"` 继续映射为 `["memory_digest", "session_summary"]`。
- weak lexical fallback 继续不进入 reranker，并在 debug candidate 的 `skipped_reason` 中体现。
- reranker 输入预算保持 50，预算外候选标记 `reranker_budget`。
- reranker metadata 使用 row `meta_json`，并记录 `stats.reranker_latency_ms` 与 `debug_trace.timings.reranker_latency_ms`。
- parent item 保留 `source_type`、`source`、`source_id`、`parent_score`、`source_prior`、`matched_cards`、`score_breakdown`。
- digest parent 保留 `digest_id`、`digest_source_id`、`matched_digest_row_ids`；session summary parent 保留 `summary_id`。
- card 保留 `candidate_id`、`source_type`、`source_id`、`source_sub_id`、`title`、`text`、`lexical`、`semantic`、`reranker`、`final_score`、`score_breakdown`。

## 测试策略

先补 characterization tests，再拆生产代码。

### Knowledge 测试

- `include_debug=True` 时锁定 `debug_trace` 顶层 key set、`stats` key set、item key set 和 `score_breakdown` key set。
- 增加 FTS recall before recent row limit 测试，对齐 memory 已有覆盖。
- 增加无向量索引行时不调用 embedding provider 的测试。
- 增加 degraded direct query contract：`degraded=True`、`fallback_reason="reranker_unavailable"`、`stats.reranker_candidates == 0`、`debug_trace.reranker_input_pairs == []`。
- 扩展无 citation 测试，断言 `debug_trace.skipped.no_citation` 与 `stats.skipped_no_citation` 一致。

### Memory 测试

- `include_debug=True` 时锁定 `debug_trace` 顶层 key set、`stats` key set、parent item key set、card key set 和 `score_breakdown` key set。
- 增加真实 `source="all"` 服务测试，同时造 digest 和 session summary，断言 SQL filters 和结果来源。
- 增加 reranker budget 50 测试，断言预算外 debug candidate 的 `skipped_reason == "reranker_budget"`。
- 扩展 weak fallback 测试，断言 debug candidate 的 `skipped_reason == "weak_lexical_fallback"`。
- 增加 degraded direct query contract，覆盖 no reranker 时的 result、stats 和 debug trace。

### 回归命令

- 红灯：新增 characterization tests 在拆分前应失败或补齐当前未覆盖字段断言。
- 定向：`python -m pytest tests/test_knowledge_rag.py tests/test_memory_query_rag.py tests/test_rag_debug.py tests/test_rag_benchmark.py -v`
- 全量：`python -m pytest tests/ -v`

## 实施分期

1. 契约测试提交：只改 `tests/test_knowledge_rag.py` 和 `tests/test_memory_query_rag.py`，新增 characterization tests，保持生产代码不变。
2. Knowledge 拆分提交：只改 `core/knowledge_rag.py` 和必要测试，`query()` 改为阶段串联，输出与测试快照一致。
3. Memory 拆分提交：只改 `core/memory_rag.py` 和必要测试，保留 reranker skip、budget、latency 和 parent grouping。
4. 文档收口提交：同步 `docs/todo.md`、`docs/plan_walkthrough.md` 和 H30 计划状态。
5. 公共 helper 评估提交：仅当两个模块内边界稳定且重复仍影响维护时，抽 `core/semantic/rag_base.py`；否则不执行该提交。

每个提交前都运行对应定向测试和 `python -m pytest tests/ -v`。提交时只显式暂存本阶段文件，不使用 `git add .`。

## 风险与缓解

- **debug trace 字段漂移：** 先用 key set 和关键值断言锁住 contract，再拆 helper。
- **召回排序漂移：** 保持 `fts -> vector -> recent` 合并顺序，并用 recent limit 越界召回测试保护。
- **embedding 调用漂移：** 无向量索引行时不调用 embedding provider 的测试覆盖 knowledge 与 memory。
- **reranker 输入漂移：** 分别断言 knowledge 的 citation metadata 和 memory 的 row `meta_json` metadata。
- **degraded 行为漂移：** 两个服务都用 direct query 测试覆盖 `degraded`、`fallback_reason`、`reranker_candidates` 和空 reranker input。
- **过早抽象：** 第一阶段不新增公共基类，避免把 knowledge citation 过滤和 memory parent grouping 压进不稳定抽象。

## 验收标准

- H30 设计与实现计划均已归档并提交。
- `KnowledgeRagService.query()` 和 `MemoryRagService.query()` 的主体缩短为清晰的阶段串联。
- 所有新增 characterization tests 通过。
- 现有 RAG debug、RAG benchmark、knowledge query tool、memory query tool 消费契约不变。
- `docs/todo.md` 中 H30 标记为完成，并在 `docs/plan_walkthrough.md` 记录阶段提交、验证命令和剩余公共 helper 评估结论。
- 全量测试 `python -m pytest tests/ -v` 0 failures。

# RAG 阶段测试报告：04-memory-rag

## 实现范围
MemoryDigest 与 RollingSessionSummary 的统一 Memory RAG 查询服务、FTS/embedding hybrid recall、reranker gate、digest parent 聚合、raw ChatLog 禁止返回、memory_query source=all schema 与 usage 同步。

## 不做范围
不接入具体业务 RAG 召回，不加载真实 embedding/reranker 模型，不执行生产验收。

## 测试函数与需求映射
- `test_memory_query_uses_reranker_after_recall`：验证召回后由 reranker 精排，低分候选不能靠 lexical/semantic 绕过。
- `test_digest_semantic_recall_without_exact_keyword`：验证 query 没有精确关键词时仍可由 embedding semantic recall 命中 digest。
- `test_memory_query_does_not_return_raw_chatlog`：验证 Memory RAG 只返回摘要层文本，不泄漏原始 ChatLog。
- `test_fallback_summary_can_be_indexed_with_lower_prior`：验证 deterministic fallback session summary 可索引，但 source_prior 低于 LLM 摘要。
- `test_memory_query_merges_multiple_cards_from_same_digest`：验证同一 digest 多张 card 聚合为一个 parent，最多展示 top 2 matched cards。
- `test_memory_query_tool_schema_supports_all_source`：验证 `memory_query` schema 支持 `source=all`。

## 输入数据
pytest fixture 使用 in-memory SQLite；Web debug 使用阶段内生成的调试 JSON。

## 预期输出
阶段目标测试通过；Memory RAG 走 hybrid recall + reranker gate；结果按 parent 聚合；不返回原始 ChatLog；`memory_query` usage/schema 与新来源同步。

## 实际输出摘要
```text
....................................                                     [100%]
=============================== warnings summary ===============================
tests/test_rag_debug.py: 17 warnings
  /home/dascard/anaconda3/lib/python3.12/site-packages/sqlalchemy/engine/default.py:941: DeprecationWarning: The default datetime adapter is deprecated as of Python 3.12; see the sqlite3 documentation for suggested replacement recipes
    cursor.execute(statement, parameters)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
36 passed, 17 warnings in 2.23s
```

## pytest 命令
`python -m pytest tests/test_semantic_scoring.py tests/test_rag_debug.py tests/test_rag_test_report.py tests/test_semantic_adapters.py tests/test_semantic_index_worker.py tests/test_memory_query_rag.py -q`

## git diff --check 结果
```text
通过：git diff --check 无输出。
```

## Web debug 输入
```json
{"query":"部署失败","fts_candidates":0,"embedding_candidates":1,"merged_candidates":1,"reranker_candidates":1,"final_items":1,"latency_ms":2,"degraded":false,"cache_hit":false}
```

## Web debug 输出
```json
{"query":"部署失败","fts_candidates":0,"embedding_candidates":1,"merged_candidates":1,"reranker_candidates":1,"final_items":1,"latency_ms":2,"degraded":false,"cache_hit":false}
```

## 性能摘要
```json
{"query":"部署失败","fts_candidates":0,"embedding_candidates":1,"merged_candidates":1,"reranker_candidates":1,"final_items":1,"latency_ms":2,"degraded":false,"cache_hit":false}
```

## 失败修复记录
本阶段红灯后按 TDD 补齐最小实现，随后重新验证通过。

## 未覆盖风险
真实 reranker 模型、业务 RAG 接入和完整 Web 可视化将在后续阶段验证。

# RAG 阶段测试报告：02-source-adapter-chunker-indexer

## 实现范围
SemanticChunk、MemoryDigest/RollingSessionSummary/GroupMemory/Sticker/ai_daily source adapters、canonical source_hash、index_version 和 semantic_index_items + semantic_index_fts upsert。

## 不做范围
不接入具体业务 RAG 召回，不加载真实 embedding/reranker 模型，不执行生产验收。

## 测试函数与需求映射
- `test_memory_digest_recall_cards_become_chunks`：验证 level=2 recall_cards 一卡一 chunk，且 lexical_text 包含关键词。
- `test_memory_digest_level0_is_expand_only`：验证 level=0 digest 默认只用于 expand。
- `test_session_summary_structured_fields_become_chunks`：验证 RollingSessionSummary 结构化字段按 section 切块。
- `test_group_memory_one_row_one_chunk`：验证 GroupMemory 一条记忆一个 chunk，embedding_text 不混入数据库 ID。
- `test_sticker_chunk_excludes_send_code_and_file_path`：验证 Sticker 文本索引排除 send_code、file_ref、local_path。
- `test_ai_daily_item_is_one_knowledge_chunk`：验证 ai_daily 摘要作为 Knowledge chunk 且保留 citation。
- `test_index_version_changes_when_chunk_strategy_changes`：验证 chunk strategy 变化触发不同 index_version。
- `test_source_hash_uses_canonical_json`：验证 source_hash 使用稳定 JSON 和 list normalize。
- `test_reindex_does_not_duplicate_source_sub_id`：验证同一 source_sub_id 重建不会重复写 semantic_index_items/FTS。

## 输入数据
pytest fixture 使用 in-memory SQLite；Web debug 使用阶段内生成的调试 JSON。

## 预期输出
阶段目标测试通过；adapter 输出符合 docs/goal.md 的切分规则；同一 chunk upsert 后 semantic_index_items 与 semantic_index_fts 均保持单条记录。

## 实际输出摘要
```text
.........................                                                [100%]
=============================== warnings summary ===============================
tests/test_rag_debug.py: 17 warnings
  /home/dascard/anaconda3/lib/python3.12/site-packages/sqlalchemy/engine/default.py:941: DeprecationWarning: The default datetime adapter is deprecated as of Python 3.12; see the sqlite3 documentation for suggested replacement recipes
    cursor.execute(statement, parameters)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
25 passed, 17 warnings in 1.38s
```

## pytest 命令
`python -m pytest tests/test_semantic_scoring.py tests/test_rag_debug.py tests/test_rag_test_report.py tests/test_semantic_adapters.py -q`

## git diff --check 结果
```text
通过：git diff --check 无输出。
```

## Web debug 输入
```json
{"query":"adapter-indexer-smoke","fts_candidates":1,"embedding_candidates":0,"merged_candidates":1,"reranker_candidates":0,"final_items":1,"latency_ms":19,"degraded":true,"cache_hit":false}
```

## Web debug 输出
```json
{"query":"adapter-indexer-smoke","fts_candidates":1,"embedding_candidates":0,"merged_candidates":1,"reranker_candidates":0,"final_items":1,"latency_ms":19,"degraded":true,"cache_hit":false}
```

## 性能摘要
```json
{"query":"adapter-indexer-smoke","fts_candidates":1,"embedding_candidates":0,"merged_candidates":1,"reranker_candidates":0,"final_items":1,"latency_ms":19,"degraded":true,"cache_hit":false}
```

## 失败修复记录
本阶段红灯后按 TDD 补齐最小实现，随后重新验证通过。

## 未覆盖风险
真实 reranker 模型、业务 RAG 接入和完整 Web 可视化将在后续阶段验证。

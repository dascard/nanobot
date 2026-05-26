# RAG 阶段测试报告：07-knowledge-library

## 实现范围
Knowledge Library 表结构、manual .txt/.md 分块、KnowledgeChunk 语义索引适配、knowledge_query 检索/展开、citation hard gate、trust/date 过滤、reranker gate、ToolPlan schema 与 RAG Debug knowledge source 输出。

## 不做范围
不加载真实 embedding/reranker 模型，不执行生产环境验收；PDF/DOCX/OCR、URL 全文抓取和 ai_daily 自动入库属于后续阶段。

## 测试函数与需求映射
- `test_knowledge_query_uses_reranker_before_final_score`：验证 reranker gate 先于 trust/source_prior/final score，低相关候选不能靠先验保留。
- `test_knowledge_query_returns_citations`：验证每条结果返回 `document_id`、`chunk_id`、`title`、`trust_level` citation。
- `test_knowledge_query_filters_by_trust_and_date`：验证普通知识查询只返回满足 trust/date 条件的 active 文档。
- `test_expand_returns_document_chunk_not_raw_unbounded_text`：验证 expand 只返回指定 chunk，不展开整篇原始文档。
- `test_knowledge_result_without_citation_is_dropped`：验证没有 citation 的 knowledge 候选被丢弃并计入 debug stats。
- `test_knowledge_query_tool_schema_declares_citation_boundary`：验证 `knowledge_query` schema 暴露 search/expand、trust 过滤和 citation 边界。
- `test_rag_debug_query_runs_knowledge_search_with_citation`：验证 RAG Debug 的 `source_type=knowledge` 走真实检索并输出 citation。

## 输入数据
pytest fixture 使用 in-memory SQLite；Web debug 使用阶段内生成的调试 JSON。

## 预期输出
阶段目标测试通过；`knowledge_query` 仅返回带 citation 的 chunk 级结果，支持 trust/date 过滤和 chunk expand；RAG Debug 可展示 knowledge citation 与分数。

## 实际输出摘要
```text
............................................................             [100%]
=============================== warnings summary ===============================
tests/test_rag_debug.py: 18 warnings
  /home/dascard/anaconda3/lib/python3.12/site-packages/sqlalchemy/engine/default.py:941: DeprecationWarning: The default datetime adapter is deprecated as of Python 3.12; see the sqlite3 documentation for suggested replacement recipes
    cursor.execute(statement, parameters)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
60 passed, 18 warnings in 6.44s
```

## pytest 命令
`python -m pytest tests/test_semantic_scoring.py tests/test_rag_debug.py tests/test_rag_test_report.py tests/test_semantic_adapters.py tests/test_semantic_index_worker.py tests/test_memory_query_rag.py tests/test_group_memory_rag.py tests/test_sticker_rag.py tests/test_knowledge_rag.py tests/test_tool_schema_config.py -q`

## git diff --check 结果
```text
通过：git diff --check 无输出。
```

## Web debug 输入
```json
{"query":"RAG Debug","source_type":"knowledge","fts_candidates":1,"embedding_candidates":0,"merged_candidates":1,"reranker_candidates":0,"final_items":1,"skipped_no_citation":0,"latency_ms":2,"degraded":true,"fallback_reason":"reranker_unavailable","cache_hit":false,"selected_ids":["knowledge:1:chunk:0"],"citation":{"chunk_id":"chunk:0","document_id":"1","published_at":"2026-05-26","title":"Debug 知识","trust_level":"medium","url":""},"score_breakdown":{"lexical":1.0,"semantic":null,"reranker":null,"raw_reranker":null,"trust":0.7,"final":0.914}}
```

## Web debug 输出
```json
{"query":"RAG Debug","source_type":"knowledge","fts_candidates":1,"embedding_candidates":0,"merged_candidates":1,"reranker_candidates":0,"final_items":1,"skipped_no_citation":0,"latency_ms":2,"degraded":true,"fallback_reason":"reranker_unavailable","cache_hit":false,"selected_ids":["knowledge:1:chunk:0"],"citation":{"chunk_id":"chunk:0","document_id":"1","published_at":"2026-05-26","title":"Debug 知识","trust_level":"medium","url":""},"score_breakdown":{"lexical":1.0,"semantic":null,"reranker":null,"raw_reranker":null,"trust":0.7,"final":0.914}}
```

## 性能摘要
```json
{"query":"RAG Debug","fts_candidates":1,"embedding_candidates":0,"merged_candidates":1,"reranker_candidates":0,"final_items":1,"latency_ms":2,"degraded":true,"cache_hit":false}
```

## 失败修复记录
本阶段红灯后按 TDD 补齐最小实现，随后重新验证通过。

## 未覆盖风险
真实 embedding/reranker 模型、生产知识库导入、PDF/DOCX/OCR、URL 全文抓取和完整前端视觉验收将在后续阶段验证。

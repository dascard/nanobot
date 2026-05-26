# RAG 阶段测试报告：08-ai-daily-ingest

## 实现范围
ai_daily best-effort ingest、KnowledgeDocument/KnowledgeChunk 摘要元数据入库、URL/title/summary 分层去重、无 URL summary_hash 限定 source/date、防止 HTML 全文入库、入库失败 ToolResult metadata warning。

## 不做范围
不加载真实 embedding/reranker 模型，不执行生产环境验收；AI Daily Ingest 独立 Admin 页面与批量重放属于后续 Web/Admin 整合。

## 测试函数与需求映射
- `test_ai_daily_ingests_summary_metadata_only`：验证 ai_daily 只入库摘要元数据，不保存 HTML 全文，并写入 knowledge 语义索引。
- `test_ai_daily_ingest_failure_does_not_fail_tool`：验证入库异常不影响 ai_daily 工具返回 HTML。
- `test_duplicate_url_does_not_create_duplicate_active_document`：验证重复 URL 只更新 existing document，不新增 active document。
- `test_ai_daily_ingest_records_warning_in_tool_meta`：验证入库失败写入 ToolResult metadata warning。
- `test_ai_daily_dedup_uses_summary_hash_with_source_and_date`：验证无 URL 时 summary_hash 只在同 source/date 范围内弱去重，不跨来源覆盖。

## 输入数据
pytest fixture 使用 in-memory SQLite；Web debug 使用阶段内生成的调试 JSON。

## 预期输出
阶段目标测试通过；ai_daily 工具返回不受入库失败影响，成功时可将摘要和 citation 入库为 Knowledge 文档并建立语义索引。

## 实际输出摘要
```text
.................................................................        [100%]
=============================== warnings summary ===============================
tests/test_rag_debug.py: 18 warnings
  /home/dascard/anaconda3/lib/python3.12/site-packages/sqlalchemy/engine/default.py:941: DeprecationWarning: The default datetime adapter is deprecated as of Python 3.12; see the sqlite3 documentation for suggested replacement recipes
    cursor.execute(statement, parameters)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
65 passed, 18 warnings in 6.85s
```

## pytest 命令
`python -m pytest tests/test_semantic_scoring.py tests/test_rag_debug.py tests/test_rag_test_report.py tests/test_semantic_adapters.py tests/test_semantic_index_worker.py tests/test_memory_query_rag.py tests/test_group_memory_rag.py tests/test_sticker_rag.py tests/test_knowledge_rag.py tests/test_ai_daily_ingest.py tests/test_tool_schema_config.py -q`

## git diff --check 结果
```text
通过：git diff --check 无输出。
```

## Web debug 输入
```json
{"query":"今天 AI 新闻","source_type":"ai_daily_ingest","created":1,"updated":0,"warnings":[],"active_documents":1,"indexed_chunks":1,"document":{"id":1,"title":"OpenAI 发布新模型","url":"https://example.com/openai-model","summary":"OpenAI 发布新模型，重点是推理能力。","trust_level":"medium"}}
```

## Web debug 输出
```json
{"query":"今天 AI 新闻","source_type":"ai_daily_ingest","created":1,"updated":0,"warnings":[],"active_documents":1,"indexed_chunks":1,"document":{"id":1,"title":"OpenAI 发布新模型","url":"https://example.com/openai-model","summary":"OpenAI 发布新模型，重点是推理能力。","trust_level":"medium"}}
```

## 性能摘要
```json
{"query":"今天 AI 新闻"}
```

## 失败修复记录
本阶段红灯后按 TDD 补齐最小实现，随后重新验证通过。

## 未覆盖风险
真实生产新闻源跑批、Admin ingest 页面、批量重放和完整前端视觉验收将在后续阶段验证。

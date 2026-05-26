# RAG 阶段测试报告：10-admin-web-debug

## 实现范围
RAG Debug score_breakdown、saved runs reopen、export JSON、sticker/knowledge/group_analysis source debug、reranker/final/citation/trust columns、group_analysis stats_logs/prompt_logs 展示。

## 不做范围
不加载真实 embedding/reranker 模型，不执行生产环境验收；本阶段验证 Admin Debug 可观测性和静态 Web 结构，不启动浏览器做视觉截图。

## 测试函数与需求映射
- `test_rag_debug_returns_score_breakdown`：验证 Debug API 每次返回 `score_breakdown`、latency 和 fallback 信息。
- `test_memory_debug_page_contains_reranker_columns`：验证 RAG Debug 页面包含 reranker/final 分数列。
- `test_knowledge_debug_page_requires_citation_columns`：验证页面包含 citation、trust_level、document_id 可视字段。
- `test_group_analysis_debug_page_contains_stats_and_prompt_logs`：验证 group_analysis debug 返回 `stats_logs` 和 `prompt_logs`。
- `test_rag_debug_run_can_be_saved_reopened_and_exported`：验证 debug run 可保存、重开和导出 JSON。

## 输入数据
pytest fixture 使用 in-memory SQLite；Web debug 使用阶段内生成的调试 JSON。

## 预期输出
阶段目标测试通过；Admin RAG Debug 支持保存/重开/导出，并可查看不同 source 的分数、候选、citation 和 group_analysis 证据日志。

## 实际输出摘要
```text
........................................................................ [ 94%]
....                                                                     [100%]
=============================== warnings summary ===============================
tests/test_rag_debug.py: 18 warnings
  /home/dascard/anaconda3/lib/python3.12/site-packages/sqlalchemy/engine/default.py:941: DeprecationWarning: The default datetime adapter is deprecated as of Python 3.12; see the sqlite3 documentation for suggested replacement recipes
    cursor.execute(statement, parameters)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
76 passed, 18 warnings in 7.52s
```

## pytest 命令
`python -m pytest tests/test_semantic_scoring.py tests/test_rag_debug.py tests/test_rag_test_report.py tests/test_semantic_adapters.py tests/test_semantic_index_worker.py tests/test_memory_query_rag.py tests/test_group_memory_rag.py tests/test_sticker_rag.py tests/test_knowledge_rag.py tests/test_ai_daily_ingest.py tests/test_group_analysis_local_rag.py tests/test_admin_web_debug.py tests/test_tool_schema_config.py -q`

## git diff --check 结果
```text
通过：git diff --check 无输出。
```

## Web debug 输入
```json
{"query":"RAG Debug","source_type":"admin_web_debug","score_breakdown":{"latency_ms":1,"degraded":true,"fallback_reason":"rag_debug_stub"},"features":["saved_runs","export_json","citation_columns","reranker_columns","group_analysis_stats_logs","group_analysis_prompt_logs"],"final_items":1}
```

## Web debug 输出
```json
{"query":"RAG Debug","source_type":"admin_web_debug","score_breakdown":{"latency_ms":1,"degraded":true,"fallback_reason":"rag_debug_stub"},"features":["saved_runs","export_json","citation_columns","reranker_columns","group_analysis_stats_logs","group_analysis_prompt_logs"],"final_items":1}
```

## 性能摘要
```json
{"query":"RAG Debug","final_items":1,"latency_ms":1,"degraded":true}
```

## 失败修复记录
本阶段红灯后按 TDD 补齐最小实现，随后重新验证通过。

## 未覆盖风险
真实浏览器视觉验收、生产环境大数据量 Debug 体验和真实模型接入后的延迟曲线仍需单独验证。

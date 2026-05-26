# RAG 阶段测试报告：09-group-analysis-local-rag

## 实现范围
group_analysis 临时 message bundle、lexical top300 预筛、临时 embedding scoring、reranker top40、neighbor expansion、prompt_logs/stats_logs 分离、预算裁剪优先保留高分 bundle，且不写 semantic_index_items。

## 不做范围
不加载真实 embedding/reranker 模型，不执行生产环境验收；Group Analysis Evidence Web 的完整前端展示属于后续 Web/Admin 整合。

## 测试函数与需求映射
- `test_group_analysis_reranks_bundles_before_neighbor_expansion`：验证 reranker top 命中先确定，再做邻居扩展。
- `test_group_analysis_builds_temporary_bundles_not_index_items`：验证 group_analysis 只构造临时 bundles，不写长期 `semantic_index_items`。
- `test_group_analysis_neighbor_expansion_preserves_context`：验证命中 bundle 前后邻居会进入上下文，且最终按时间排序。
- `test_group_analysis_does_not_change_group_stats`：验证 prompt 语料可裁剪，但 `group_stats` 仍基于全量可分析消息。
- `test_group_analysis_limits_embedding_to_lexical_top_candidates`：验证临时 embedding scoring 只作用于 lexical top300 候选。
- `test_group_analysis_budget_preserves_high_score_groups`：验证超预算时优先保留高分 bundle 组。

## 输入数据
pytest fixture 使用 in-memory SQLite；Web debug 使用阶段内生成的调试 JSON。

## 预期输出
阶段目标测试通过；group_analysis 使用临时局部 RAG 压缩 prompt 语料，保留 `stats_logs` 全量统计与 `prompt_logs` 命中证据，且不污染长期语义索引。

## 实际输出摘要
```text
.......................................................................  [100%]
=============================== warnings summary ===============================
tests/test_rag_debug.py: 18 warnings
  /home/dascard/anaconda3/lib/python3.12/site-packages/sqlalchemy/engine/default.py:941: DeprecationWarning: The default datetime adapter is deprecated as of Python 3.12; see the sqlite3 documentation for suggested replacement recipes
    cursor.execute(statement, parameters)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
71 passed, 18 warnings in 7.64s
```

## pytest 命令
`python -m pytest tests/test_semantic_scoring.py tests/test_rag_debug.py tests/test_rag_test_report.py tests/test_semantic_adapters.py tests/test_semantic_index_worker.py tests/test_memory_query_rag.py tests/test_group_memory_rag.py tests/test_sticker_rag.py tests/test_knowledge_rag.py tests/test_ai_daily_ingest.py tests/test_group_analysis_local_rag.py tests/test_tool_schema_config.py -q`

## git diff --check 结果
```text
通过：git diff --check 无输出。
```

## Web debug 输入
```json
{"query":"RAG reranker","source_type":"group_analysis_local_rag","stats_logs":{"total_messages":24,"bundle_count":6,"lexical_candidates":2,"temporary_embedding_scored":0,"reranker_candidates":0,"selected_bundles":4,"selected_messages":16},"prompt_logs":{"hit_bundles":[{"bundle_id":"bundle:2","start":8,"end":11,"lexical":1.0,"semantic":null,"reranker":null,"score":1.0,"text":"[12:08] [u2]: 普通闲聊 8\n[12:09] [u0]: 普通闲聊 9\n[12:10] [u1]: 关键 RAG reranker 讨论\n[12:11] [u2]: 关键 RAG reranker 讨论"},{"bundle_id":"bundle:3","start":12,"end":15,"lexical":1.0,"semantic":null,"reranker":null,"score":1.0,"text":"[12:12] [u0]: 关键 RAG reranker 讨论\n[12:13] [u1]: 关键 RAG reranker 讨论\n[12:14] [u2]: 普通闲聊 14\n[12:15] [u0]: 普通闲聊 15"}],"selected_message_ids":[5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20]},"selected_count":16}
```

## Web debug 输出
```json
{"query":"RAG reranker","source_type":"group_analysis_local_rag","stats_logs":{"total_messages":24,"bundle_count":6,"lexical_candidates":2,"temporary_embedding_scored":0,"reranker_candidates":0,"selected_bundles":4,"selected_messages":16},"prompt_logs":{"hit_bundles":[{"bundle_id":"bundle:2","start":8,"end":11,"lexical":1.0,"semantic":null,"reranker":null,"score":1.0,"text":"[12:08] [u2]: 普通闲聊 8\n[12:09] [u0]: 普通闲聊 9\n[12:10] [u1]: 关键 RAG reranker 讨论\n[12:11] [u2]: 关键 RAG reranker 讨论"},{"bundle_id":"bundle:3","start":12,"end":15,"lexical":1.0,"semantic":null,"reranker":null,"score":1.0,"text":"[12:12] [u0]: 关键 RAG reranker 讨论\n[12:13] [u1]: 关键 RAG reranker 讨论\n[12:14] [u2]: 普通闲聊 14\n[12:15] [u0]: 普通闲聊 15"}],"selected_message_ids":[5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20]},"selected_count":16}
```

## 性能摘要
```json
{"query":"RAG reranker"}
```

## 失败修复记录
本阶段红灯后按 TDD 补齐最小实现，随后重新验证通过。

## 未覆盖风险
真实 embedding/reranker 模型、超大群聊生产窗口和完整前端证据展示将在后续阶段验证。

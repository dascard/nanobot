# RAG 阶段测试报告：05-group-memory-rag

## 实现范围
GroupMemory SQL gate 后不再 top100 预截断、reranker gate 低分硬阻断、disabled/manual hard gate、preview 不记录注入、运行时 cache/debug 字段与 timeout fallback。

## 不做范围
不接入具体业务 RAG 召回，不加载真实 embedding/reranker 模型，不执行生产验收。

## 测试函数与需求映射
- `test_group_memory_does_not_apply_top100_before_relevance`：验证 SQL gate 后不再先截断 top100，高相关旧记忆仍能进入候选。
- `test_old_but_relevant_memory_can_be_selected`：验证旧但与当前输入相关的 GroupMemory 可被选中。
- `test_disabled_or_manual_memory_never_injected`：验证 disabled/manual_only 是 hard gate，reranker 不能覆盖。
- `test_source_prior_does_not_bypass_relevance_gate`：验证高置信/高业务先验不能绕过低 reranker 分数。
- `test_group_memory_preview_does_not_record_injection`：验证 preview 模式只展示 debug，不写 `last_injected_at`/`injected_count`。
- `test_group_memory_rag_timeout_marks_fallback`：验证超时路径返回空 context 并记录 `timeout_fallback`、`latency_ms`。

## 输入数据
pytest fixture 使用 in-memory SQLite；Web debug 使用阶段内生成的调试 JSON。

## 预期输出
阶段目标测试通过；GroupMemory 自动注入链路具有 reranker gate、硬过滤、preview 不落注入统计、timeout fallback 与 cache/debug 字段。

## 实际输出摘要
```text
..........................................                               [100%]
=============================== warnings summary ===============================
tests/test_rag_debug.py: 17 warnings
  /home/dascard/anaconda3/lib/python3.12/site-packages/sqlalchemy/engine/default.py:941: DeprecationWarning: The default datetime adapter is deprecated as of Python 3.12; see the sqlite3 documentation for suggested replacement recipes
    cursor.execute(statement, parameters)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
42 passed, 17 warnings in 2.64s
```

## pytest 命令
`python -m pytest tests/test_semantic_scoring.py tests/test_rag_debug.py tests/test_rag_test_report.py tests/test_semantic_adapters.py tests/test_semantic_index_worker.py tests/test_memory_query_rag.py tests/test_group_memory_rag.py -q`

## git diff --check 结果
```text
通过：git diff --check 无输出。
```

## Web debug 输入
```json
{"query":"本地模型部署量化参数怎么调？","fts_candidates":1,"embedding_candidates":0,"merged_candidates":1,"reranker_candidates":1,"final_items":1,"latency_ms":11,"degraded":false,"cache_hit":false,"selected_ids":[1]}
```

## Web debug 输出
```json
{"query":"本地模型部署量化参数怎么调？","fts_candidates":1,"embedding_candidates":0,"merged_candidates":1,"reranker_candidates":1,"final_items":1,"latency_ms":11,"degraded":false,"cache_hit":false,"selected_ids":[1]}
```

## 性能摘要
```json
{"query":"本地模型部署量化参数怎么调？","fts_candidates":1,"embedding_candidates":0,"merged_candidates":1,"reranker_candidates":1,"final_items":1,"latency_ms":11,"degraded":false,"cache_hit":false}
```

## 失败修复记录
本阶段红灯后按 TDD 补齐最小实现，随后重新验证通过。

## 未覆盖风险
真实 reranker 模型、业务 RAG 接入和完整 Web 可视化将在后续阶段验证。

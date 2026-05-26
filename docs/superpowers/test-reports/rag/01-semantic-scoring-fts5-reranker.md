# RAG 阶段测试报告：01-semantic-scoring-fts5-reranker

## 实现范围
评分、FTS5 安全查询、reranker provider 基础、source quota、semantic schema、RAG debug API 与 WebUI 初始入口。

## 不做范围
不接入具体业务 RAG 召回，不加载真实 embedding/reranker 模型，不执行生产验收。

## 测试函数与需求映射
- semantic scoring：weighted score、BM25、source weight、source quota、relevance gate。
- fts：FTS5 availability degraded 标记和 MATCH query 安全构造。
- reranker：分数归一化、fake provider、provider 基础契约。
- rag debug：schema、API 保存/查询、WebUI 路由注册。

## 输入数据
pytest fixture 使用 in-memory SQLite；Web debug 使用阶段内生成的调试 JSON。

## 预期输出
阶段目标测试通过，debug run 可落库，Web 入口可静态注册。

## 实际输出摘要
```text
................                                                         [100%]
=============================== warnings summary ===============================
tests/test_rag_debug.py: 17 warnings
  /home/dascard/anaconda3/lib/python3.12/site-packages/sqlalchemy/engine/default.py:941: DeprecationWarning: The default datetime adapter is deprecated as of Python 3.12; see the sqlite3 documentation for suggested replacement recipes
    cursor.execute(statement, parameters)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
16 passed, 17 warnings in 1.08s
```

## pytest 命令
`python -m pytest tests/test_semantic_scoring.py tests/test_rag_debug.py tests/test_rag_test_report.py -q`

## git diff --check 结果
```text
通过：git diff --check 无输出。
```

## Web debug 输入
```json
{"query":"端口冲突怎么解决","source_type":"memory","stages":{"sql_filters":{},"fts_hits":[],"embedding_hits":[],"merged_candidates":[],"reranker_input_pairs":[],"final_candidates":[]},"score_breakdown":{"degraded":true,"fallback_reason":"rag_debug_stub","source_weights":{},"latency_ms":0},"candidates":[]}
```

## Web debug 输出
```json
{"query":"端口冲突怎么解决","source_type":"memory","stages":{"sql_filters":{},"fts_hits":[],"embedding_hits":[],"merged_candidates":[],"reranker_input_pairs":[],"final_candidates":[]},"score_breakdown":{"degraded":true,"fallback_reason":"rag_debug_stub","source_weights":{},"latency_ms":0},"candidates":[]}
```

## 性能摘要
```json
{"query":"端口冲突怎么解决","latency_ms":0,"degraded":true}
```

## 失败修复记录
本阶段红灯后按 TDD 补齐最小实现，随后重新验证通过。

## 未覆盖风险
真实 reranker 模型、业务 RAG 接入和完整 Web 可视化将在后续阶段验证。

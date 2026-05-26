# RAG 阶段测试报告：03-semantic-index-worker

## 实现范围
semantic_index_jobs enqueue/claim/recover/finish 状态机、单 job worker 处理、embedding 失败 done_with_warning、delete job 标记索引删除、item 与 FTS 写入事务回滚。

## 不做范围
不接入具体业务 RAG 召回，不加载真实 embedding/reranker 模型，不执行生产验收。

## 测试函数与需求映射
- `test_claim_job_is_atomic`：验证 pending job 被单个 worker claim 后，第二个 worker 不能重复领取。
- `test_embedding_failure_marks_done_with_warning`：验证 embedding provider 异常时 FTS 仍写入，item `embedding_status=failed`，job `done_with_warning`。
- `test_deleted_source_marks_index_deleted`：验证 delete job 将索引 item 标记为 deleted，并删除 FTS row。
- `test_running_job_timeout_recovers_to_pending`：验证超时 running job 可恢复为 pending。
- `test_item_and_fts_write_are_same_transaction`：验证 FTS 写入失败时 item upsert rollback，不留下半写入索引。

## 输入数据
pytest fixture 使用 in-memory SQLite；Web debug 使用阶段内生成的调试 JSON。

## 预期输出
阶段目标测试通过；worker 状态机可领取、恢复、完成和失败；embedding 失败不阻断 FTS；删除源和事务回滚行为符合 docs/goal.md。

## 实际输出摘要
```text
..............................                                           [100%]
=============================== warnings summary ===============================
tests/test_rag_debug.py: 17 warnings
  /home/dascard/anaconda3/lib/python3.12/site-packages/sqlalchemy/engine/default.py:941: DeprecationWarning: The default datetime adapter is deprecated as of Python 3.12; see the sqlite3 documentation for suggested replacement recipes
    cursor.execute(statement, parameters)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
30 passed, 17 warnings in 1.78s
```

## pytest 命令
`python -m pytest tests/test_semantic_scoring.py tests/test_rag_debug.py tests/test_rag_test_report.py tests/test_semantic_adapters.py tests/test_semantic_index_worker.py -q`

## git diff --check 结果
```text
通过：git diff --check 无输出。
```

## Web debug 输入
```json
{"query":"semantic-index-worker-smoke","fts_candidates":1,"embedding_candidates":0,"merged_candidates":1,"reranker_candidates":0,"final_items":1,"latency_ms":27,"degraded":true,"cache_hit":false,"job_status":"done","worker_claimed":true}
```

## Web debug 输出
```json
{"query":"semantic-index-worker-smoke","fts_candidates":1,"embedding_candidates":0,"merged_candidates":1,"reranker_candidates":0,"final_items":1,"latency_ms":27,"degraded":true,"cache_hit":false,"job_status":"done","worker_claimed":true}
```

## 性能摘要
```json
{"query":"semantic-index-worker-smoke","fts_candidates":1,"embedding_candidates":0,"merged_candidates":1,"reranker_candidates":0,"final_items":1,"latency_ms":27,"degraded":true,"cache_hit":false}
```

## 失败修复记录
本阶段红灯后按 TDD 补齐最小实现，随后重新验证通过。

## 未覆盖风险
真实 reranker 模型、业务 RAG 接入和完整 Web 可视化将在后续阶段验证。

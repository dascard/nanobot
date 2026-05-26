# RAG 阶段测试报告：06-sticker-rag

## 实现范围
Sticker text index、search_stickers 语义索引召回、reranker gate、describe_status hard gate、duplicate/inactive/unreplyable hard gate、reply_token/send_code/score_breakdown 结构化返回，以及 RAG Debug 对 sticker source 的真实候选输出。

## 不做范围
不加载真实 embedding/reranker 模型，不执行生产环境验收；本阶段仅验证 Sticker RAG 和现有 RAG Debug 页面上的 sticker source 输出。

## 测试函数与需求映射
- `test_sticker_rag_uses_text_tags_not_image_embedding`：验证表情检索只使用文本描述、标签、情绪，不让图片路径或发送码参与召回。
- `test_sticker_rag_returns_reply_token`：验证结果包含 `reply_token`、`send_code` 和 `score_breakdown`。
- `test_duplicate_or_inactive_sticker_is_filtered`：验证 duplicate、disabled/inactive 表情是 hard gate。
- `test_sticker_search_uses_reranker_before_usage_boost`：验证低 reranker 分数不能被高 usage_count 覆盖。
- `test_undescribed_sticker_is_not_text_rag_candidate`：验证 `describe_status != ok` 不进入文本 RAG。
- `test_sticker_rag_filters_unreplyable_sticker`：验证不可发送表情不会返回。
- `test_rag_debug_query_runs_sticker_search`：验证 RAG Debug 的 `source_type=sticker` 走真实 `search_stickers` 并输出候选。

## 输入数据
pytest fixture 使用 in-memory SQLite；Web debug 使用阶段内生成的调试 JSON。

## 预期输出
阶段目标测试通过；`sticker_search` 在有语义索引时走 Sticker RAG，在无索引时按同一 hard gate 词面降级；RAG Debug 可展示 sticker 候选、reply token 和分数。

## 实际输出摘要
```text
.................................................                        [100%]
=============================== warnings summary ===============================
tests/test_rag_debug.py: 17 warnings
  /home/dascard/anaconda3/lib/python3.12/site-packages/sqlalchemy/engine/default.py:941: DeprecationWarning: The default datetime adapter is deprecated as of Python 3.12; see the sqlite3 documentation for suggested replacement recipes
    cursor.execute(statement, parameters)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
49 passed, 17 warnings in 3.08s
```

## pytest 命令
`python -m pytest tests/test_semantic_scoring.py tests/test_rag_debug.py tests/test_rag_test_report.py tests/test_semantic_adapters.py tests/test_semantic_index_worker.py tests/test_memory_query_rag.py tests/test_group_memory_rag.py tests/test_sticker_rag.py -q`

## git diff --check 结果
```text
通过：git diff --check 无输出。
```

## Web debug 输入
```json
{"query":"震惊猫猫","fts_candidates":1,"embedding_candidates":0,"merged_candidates":2,"reranker_candidates":0,"final_items":1,"latency_ms":83,"degraded":true,"fallback_reason":"reranker_unavailable","cache_hit":false,"selected_ids":[1],"score_breakdown":{"lexical":1.0,"semantic":null,"reranker":null,"raw_reranker":null,"usage":0.0,"final":0.895}}
```

## Web debug 输出
```json
{"query":"震惊猫猫","fts_candidates":1,"embedding_candidates":0,"merged_candidates":2,"reranker_candidates":0,"final_items":1,"latency_ms":83,"degraded":true,"fallback_reason":"reranker_unavailable","cache_hit":false,"selected_ids":[1],"score_breakdown":{"lexical":1.0,"semantic":null,"reranker":null,"raw_reranker":null,"usage":0.0,"final":0.895}}
```

## 性能摘要
```json
{"query":"震惊猫猫","fts_candidates":1,"embedding_candidates":0,"merged_candidates":2,"reranker_candidates":0,"final_items":1,"latency_ms":83,"degraded":true,"cache_hit":false}
```

## 失败修复记录
本阶段红灯后按 TDD 补齐最小实现，随后重新验证通过。

## 未覆盖风险
真实 embedding/reranker 模型、生产表情库重建索引和完整前端视觉验收将在后续阶段验证。

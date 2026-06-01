# 长期摘要 LLM 生成设计

## 背景

WebUI 中的长期摘要来自 `memory_digests`，当前由 `MemoryDigestBuilder` 纯规则生成。它会抽关键词、截取代表消息并渲染 level 0/1/2。这个方案只能作为兜底，不能承担长期记忆的主生成职责；否则 URL、低价值消息和碎片关键词会直接污染召回入口。

近期滚动摘要已有异步 LLM 任务，但生成量很少，不能替代长期 daily digest。长期摘要应在 daily digest 生成时直接调用大模型总结当天有效聊天记录。

## 目标

- 长期 daily digest 默认优先使用 LLM 生成结构化 `MemoryDigest` v2。
- LLM 输出必须经过 JSON 解析、结构审计和质量门禁。
- LLM 失败、返回脏 JSON、质量不达标时，不阻断 daily digest，降级为现有规则摘要。
- 规则摘要继续保留，作为离线测试、LLM 不可用和审计失败兜底。
- level 0/1/2、`meta_json`、`recall_cards` 结构保持兼容，现有 RAG 和 WebUI 不需要改数据读取逻辑。

## 非目标

- 不在聊天请求同步路径里调用长期摘要 LLM。
- 不把历史已生成的 digest 自动重写；旧数据需后续手动 force/backfill。
- 不新增独立 worker 表，daily digest 调度内顺序处理即可。
- 不改变近期 `rolling_session_summaries` 的异步 LLM 任务机制。

## 方案

新增 `app/memory_digest/llm_builder.py`：

- 复用 `MemoryDigestBuilder` 先得到清洗后的确定性候选，避免把 tool/system/纯 URL/图片占位发给 LLM。
- 将清洗后的 level 0 或结构化 meta 作为输入，要求 LLM 输出严格 JSON。
- 输出字段与 v2 meta 对齐：`preview`、`long_summary`、`recall_cards`、`quality`。
- 审计规则：
  - `status` 必须是 `active` 或 `skipped`。
  - active 摘要必须有 brief、topic_flow、至少一张 recall card。
  - recall card 文本不允许包含 URL。
  - quality.score 低于 0.7 或 issues 非空时拒绝提升。
  - 输出超长字段会截断到现有渲染边界。

`core/daily_digest.py` 生成每个 session/date 时调用 LLM builder：

- `MEMORY_DIGEST_LLM_ENABLED` 默认开启。
- LLM 成功时写入 `meta["generator"] = "llm"`、`llm_status = "success"`、`llm_model`。
- LLM 失败时写入规则摘要，并在 meta 标记 `generator = "deterministic_fallback"`、`llm_status = "fallback"`、`llm_error`。

## 测试

- LLM builder 成功解析结构化 JSON 并渲染三层摘要。
- LLM 输出 URL recall card 时审计失败并触发规则兜底。
- daily digest 默认调用 LLM summarizer，写入 `generator=llm`。
- daily digest 在 LLM 异常时仍写入规则摘要。
- 既有 memory digest、RAG、session memory 测试保持通过。


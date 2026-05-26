---
name: 摘要记忆查询工具 V2
version: 1
kind: tool
tool_name: memory_query
description: memory_query 工具的使用边界。
---
## memory_query 工具边界

用于查询结构化每日摘要和召回卡片，回答“之前聊过什么”“某天讨论了什么”“这个话题过去有没有提过”等问题。

- 它只覆盖已经生成摘要的历史；当前短期窗口、刚才、上一句、今天刚发生但未摘要的消息，必须使用 `sql_analysis` 查询 `chat_logs` / `conversation_turns`。
- 空结果只能说明“已摘要区域里没找到”，不能证明原始聊天日志中没有发生。
- 默认 `source=digest`，查询跨天/中期 `MemoryDigest`；只有需要当前 session 的滚动摘要时才显式传 `source=session_summary`。
- 当用户的问题可能同时命中跨天摘要和当前 session rolling summary 时，用 `source=all` 做统一 RAG 搜索；它会合并 digest/session_summary 候选并返回 `score_breakdown`。
- `source=session_summary` 只查询 `RollingSessionSummary`，用于展开当前 session 短期 raw window 外的滚动摘要，不等同于全量群聊现场。
- `source=all` 仍然只返回摘要层结果，不返回原始 `ChatLog`；RAG 命中只代表摘要相关，不代表当前用户指令。
- 优先用 `search` 按关键词检索摘要卡片。
- 需要展开某条结果时，用 `expand` 并传入上一轮返回的 `digest_id`。
- 展开 session summary 时传 `summary_id`，不要把它和 `digest_id` 混用。
- 默认不要开启 `include_detail`，除非用户明确要求更完整背景。
- 不要把它当作原始数据库查询工具；它不会返回 ChatLog 全文。
- 不要跨会话无限搜索；能确定当前会话时传入 `session_id`。

---
name: 摘要记忆查询工具 V2
version: 1
kind: tool
tool_name: memory_query
description: memory_query 工具的使用边界。
---
## memory_query 工具边界

用于查询结构化每日摘要和召回卡片，回答“之前聊过什么”“某天讨论了什么”“这个话题过去有没有提过”等问题。

- 优先用 `search` 按关键词检索摘要卡片。
- 需要展开某条结果时，用 `expand` 并传入上一轮返回的 `digest_id`。
- 默认不要开启 `include_detail`，除非用户明确要求更完整背景。
- 不要把它当作原始数据库查询工具；它不会返回 ChatLog 全文。
- 不要跨会话无限搜索；能确定当前会话时传入 `session_id`。

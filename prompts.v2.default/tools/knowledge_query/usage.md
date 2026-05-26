---
name: 外部知识库查询工具 V2
version: 1
kind: tool
tool_name: knowledge_query
description: knowledge_query 工具的使用边界。
---
## knowledge_query 工具边界

用于查询已经入库的外部知识库，包括手工 Markdown/文本文件、已保存 URL 元数据和历史 ai_daily 摘要。

- 每条返回结果必须带 `citation`；没有 citation 的候选不会返回。
- 低 `trust_level` 内容可以返回，但只能作为低置信参考，不要表述为确定事实。
- 今天、刚刚、实时新闻、最新发布优先使用 `ai_daily`，不要用旧知识库替代实时来源。
- `search` 返回 chunk 级结果和 `document_id` / `chunk_id`；需要更多内容时再用 `expand` 展开该 chunk。
- `expand` 只展开单个 chunk，不返回整篇原始文档。
- 需要限定资料可信度时传 `min_trust_level`；需要限定时间时传 `published_after` / `published_before`。

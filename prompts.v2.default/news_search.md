---
name: 资讯搜索工具 V2
version: 1
kind: tool
tool_name: news_search
description: news_search 兼容工具的使用边界。
---
## news_search 工具边界

用于搜索 AI / 科技领域资讯，是 `ai_daily` 的兼容旧入口。

- 新的日报和简报请求优先使用 `ai_daily`。
- 只有调用方或旧链路明确需要 `news_search` 时才使用该工具。
- 查询应避免宽泛关键词，优先写清主题、机构、产品或时间范围。
- 回复中不要编造来源、日期或结论；工具结果不足时说明不足。

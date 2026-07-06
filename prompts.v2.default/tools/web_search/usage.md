---
name: 网页搜索工具
version: 1
kind: tool
tool_name: web_search
description: web_search 工具的使用边界。
---
## web_search 工具边界

用于查询外部网页搜索结果，返回标题、URL 和摘要。

- 用户询问最新事实、官方公告、产品文档、价格、版本、人物职位、实时状态时优先使用 `web_search`。
- 查询词要包含关键实体和限定词，例如公司名、产品名、日期、版本号或 `site:` 约束。
- 搜索结果只是候选来源，不等于已验证事实；回答时应说明依据，并优先引用更权威的 URL。
- 如果结果不足、过旧或互相矛盾，应继续调整 query 搜索，而不是编造。
- `provider` 留空时会按管理后台已启用 provider 自动 fallback；只有调试或用户明确要求时才指定 provider。

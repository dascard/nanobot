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
- 工具输出使用 `WEB_SEARCH_RESULTS_BEGIN` / `WEB_SEARCH_RESULTS_END` 包裹；回答只能基于该边界内的标题、URL、摘要和时间归纳。
- 如果结果不足、过旧或互相矛盾，应继续调整 query 搜索，而不是编造。
- 如果搜索结果明显不匹配用户问题，应重新搜索；不能把模型记忆或其他网页内容混入当前搜索结果。
- 模型调用时无需选择 provider；系统按管理后台配置自动选择 provider，按启用顺序 fallback，并在第一个相关结果停止。低相关、空结果或错误会继续尝试下一个 provider。

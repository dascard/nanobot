---
name: ai-daily
description: 聚合 AI 与科技领域可信来源并生成可直接发送的日报或简报；用户询问最新模型、近期科技新闻、今日资讯或指定日期动态时使用。
compatibility: Nanobot Server；需要当前 ToolPlan 提供 ai_daily。
metadata:
  version: "1.0.0"
  nanobot.dependencies: ""
  nanobot.permissions: "tool:ai_daily"
  nanobot.capabilities: "ai-news,科技新闻,日报,最新资讯"
  nanobot.applies-to: "chat,private,group,scheduled"
allowed-tools: ai_daily
---

# AI 日报与资讯聚合

用户询问最新 AI 模型、近期科技大事、今日资讯或指定日期新闻时，调用
`ai_daily` 获取受管来源结果。把用户主题放入 `query`；只有用户明确限制数量、
时效或日期时才设置 `max_results`、`freshness`、`target_date`。

工具返回完整报告时直接交付，不根据模型记忆补写“最新”事实，也不要把历史
报告当成本轮结果。来源失败、日期缺失或证据不足时如实说明，不虚构新闻。

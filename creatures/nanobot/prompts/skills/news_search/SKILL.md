---
name: ai_daily
description: 聚合 AI/科技领域可信来源并生成可直接发送的日报/简报
allowed-tools: [ai_daily, news_search]
---

# AI 日报与资讯聚合

聚合固定 AI/科技可信来源，筛选近期资讯，并生成可直接发送的 HTML 日报或简报。`news_search` 是兼容旧名，新请求优先调用 `ai_daily`。

## 何时使用
- 当用户询问最新的 AI 模型、最近的科技大事
- 需要获取有关最新工具或库的版本发布信息
- 当 nanobot 的内建知识库不足以回答当前时刻发生的问题

## 行为
该工具会抓取已配置的官方源、媒体源和中文策展源，按时效、相关性和来源配额选择候选，再生成结构化日报。

## 参数
- `query` (字符串): 日报主题或自然语言请求。
- `max_results` (整数): 候选资讯数量，默认 8。
- `freshness` (字符串): today/latest/week/custom。
- `target_date` (字符串): 用户明确指定日期时填写 YYYY-MM-DD。

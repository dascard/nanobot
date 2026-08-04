---
name: sql-analysis
description: 对聊天档案执行只读 SQL 检索和统计；用户询问上一句、此前对话、聊天记录、活跃度、表结构或可由 SQL 直接回答的历史统计时使用。
compatibility: Nanobot Server；需要当前 ToolPlan 提供 sql_analysis。
metadata:
  version: "1.0.0"
  nanobot.dependencies: ""
  nanobot.permissions: "tool:sql_analysis"
allowed-tools: sql_analysis
---

# 聊天档案只读分析

仅通过 `sql_analysis` 查询受限 SQLite 视图。查询必须只读并带合理 `LIMIT`；
优先使用 `conversation_turns` 获取精简 user/assistant 历史，只有审计 tool、ambient
或原始消息时才查询 `chat_logs`。

`ChatLog` 是完整档案，`ConversationTurn` 是可清理工作记忆。历史清除后不得从
档案推断用户要求清除的对话上下文。群日报和群聊总结使用专用群分析能力，
不要用任意 SQL 模拟。查询无命中时明确说明，不补造记录。

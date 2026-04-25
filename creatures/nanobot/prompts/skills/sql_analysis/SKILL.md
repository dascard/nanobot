---
name: sql_analysis
description: 对聊天日志数据库执行只读 SQL 查询进行数据统计分析
allowed-tools: [sql_analysis]
---

# SQL 数据分析

对 nanobot 的 SQLite 聊天日志库执行只读 SQL 查询，用于查询用户的历史对话习惯、活跃度等。

## 何时使用
- 用户询问聊天统计数据（如发送了多少条消息、最活跃的时间段等）
- 需要按条件筛选历史对话日志
- 需要了解系统的宏观运行情况

## 可用表
- `chat_logs`: 所有对话记录 (id, user_id, session_id, role, content, sender_name, session_name, created_at, processed)
- `users`: 用户列表 (id, created_at)
- `personas`: 用户画像总结 (user_id, persona_json, last_updated)
- `system_prompts`: 系统提示定制 (user_id, prompt_text, created_at)

## 安全要求
该沙箱工具只允许完整的 `SELECT` 语句，不能包含修改数据库的语句。返回的结果是文本格式的数据。

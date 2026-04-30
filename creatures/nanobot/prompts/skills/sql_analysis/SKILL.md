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

### 核心表
- `users`: 用户/群聊 (id, name, history_clear_at, created_at)
  - id: QQ号 或 "group_群号"，name: 用户名/群名（消息入口自动刷新）
- `chat_logs`: 所有消息 (id, user_id, session_id, sender_name, session_name, role, content, processed, created_at)
  - role: user/assistant/ambient/tool，processed: 0=未处理 1=已处理
- `conversation_turns`: 精简对话上下文 (id, user_id, session_id, role, content, created_at)
  - 仅 user/assistant，无 tool 噪声，用于历史注入

### 画像
- `personas`: 压缩画像 JSON (user_id, persona_json, updated_at)
  - persona_json: {"facts": [{content, domain, confidence, evidence, type}], "count": N}
- `persona_facts`: 结构化事实 (id, user_id, domain_primary, content, embedding, cluster_centroid, cluster_id, evidence_count, confidence, fact_type, ...)
- `persona_behaviors`: 行为模式 (id, user_id, domain_primary, pattern, embedding, frequency, last_observed, ...)

### 记忆与其他
- `memory_digests`: 每日日志压缩 (id, user_id, session_id, digest_date, level, content, meta_json, ...)
- `scheduled_tasks`: 定时任务 (id, name, cron_expr, target_type, target_id, prompt_template, enabled, last_run_at)
- `sensitive_data`: 敏感消息隔离存档 (id, user_id, session_id, content, guardrail_status, ...)
- `system_prompts`: 系统提示 (user_id, prompt_text, updated_at)

## 安全要求
该沙箱工具只允许完整的 `SELECT` 语句，不能包含修改数据库的语句。返回的结果是文本格式的数据。

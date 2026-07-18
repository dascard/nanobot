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
- 用户询问“上一句”“刚才说过什么”“之前聊过什么”“某人的历史发言”
- 需要了解系统的宏观运行情况
- 不用于群日报/群聊总结：这种需求直接用 `group_analysis`
- 不要用 `memory_read`、`read`、`grep` 查聊天记录数据库

## 可用表

### 核心表
- `users`: 用户/群聊 (id, name, history_clear_at, created_at)
  - id: QQ号 或 "group_群号"，name: 用户名/群名（消息入口自动刷新）
- `chat_logs`: 所有原始消息 (id, user_id, session_id, sender_name, session_name, role, content, processed, created_at, message_id, source_message_ids_json, meta_json)
  - user_id 是物理发件人 QQ；session_id 是 private_用户ID 或 group_群号
  - role: user/assistant/ambient/tool/model，processed: 0=未处理 1=已处理
- `conversation_turns`: 精简对话上下文 (id, user_id, session_id, role, content, created_at, source_message_ids_json, meta_json)
  - 仅 user/assistant，无 tool 噪声，用于历史注入

## 常用查询模板

查当前私聊上一批用户消息：

```sql
SELECT id, created_at, content
FROM chat_logs
WHERE session_id = 'private_0000000000' AND role = 'user'
ORDER BY id DESC
LIMIT 5
```

查某群最近原始现场：

```sql
SELECT id, created_at, sender_name, content
FROM chat_logs
WHERE session_id = 'group_123456' AND role IN ('user', 'ambient')
ORDER BY id DESC
LIMIT 20
```

查表结构：

```sql
PRAGMA table_info(chat_logs)
```

### 画像
- `personas`: 压缩画像 JSON (user_id, persona_json, status, updated_at)，仅 `active` 可进入运行时
  - persona_json: {"facts": [{content, domain, confidence, evidence, type}], "count": N}
- `persona_facts`: 结构化事实 (id, user_id, domain_primary, content, embedding, cluster_centroid, cluster_id, evidence_count, confidence, fact_type, ...)
- `persona_behaviors`: 行为模式 (id, user_id, domain_primary, pattern, embedding, frequency, last_observed, status, ...)

### 记忆与其他
- `memory_digests`: 每日日志压缩 (id, user_id, session_id, digest_date, level, content, meta_json, ...)
- `scheduled_tasks`: 定时任务 (id, name, cron_expr, target_type, target_id, prompt_template, enabled, last_run_at)
- `sensitive_data`: 敏感消息隔离存档 (id, user_id, session_id, content, guardrail_status, ...)
- `system_prompts`: 系统提示 (user_id, prompt_text, updated_at)

## 安全要求
该沙箱工具只允许完整的 `SELECT` 语句，不能包含修改数据库的语句。返回的结果是文本格式的数据。
如果查询被拒绝为缺少 LIMIT、使用 SELECT * 或 LIMIT 过大，直接修正同一查询后重试；不要因此改用 `memory_read`、`read` 或 `grep`。

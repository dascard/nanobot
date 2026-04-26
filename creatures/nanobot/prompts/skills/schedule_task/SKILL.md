---
name: schedule_task
description: 创建定时推送任务。用户说"每天X点推送Y"时使用，通过 nanobot API 创建 cron 任务。
allowed-tools: []
---

# 创建定时推送任务

当用户要求定时推送某类内容（新闻、提醒、摘要等）时，帮用户拼装好任务参数，告知调用方式。

## 任务参数
- `name`: 任务名称
- `cron_expr`: cron 表达式 "分 时 日 月 周"
- `target_type`: "private" 或 "group"
- `target_id`: QQ号或群号
- `prompt_template`: 给 LLM 的提示词，用于生成推送内容

## 常见 cron 示例
- 每天9点: `0 9 * * *`
- 每天18点: `0 18 * * *`
- 每周一8点: `0 8 * * 1`

## 调用方式

告诉用户用 curl 创建：

POST /api/v1/tasks
Authorization: Bearer $NANOBOT_API_TOKEN
{"name":"daily-news","cron_expr":"0 9 * * *","target_type":"private","target_id":"0000000000","prompt_template":"搜索今天AI新闻，3-5条，每条2行"}

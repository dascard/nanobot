---
name: schedule_task
description: 创建定时推送任务。用户说"每天X点推送Y"时，直接调用 schedule_task 工具创建。
allowed-tools: [schedule_task]
---

# 创建定时推送任务

用户要求定时推送时，直接调用 `schedule_task` 工具。

## 参数说明
- `name`: 任务名称
- `cron_expr`: cron 表达式 "分 时 日 月 周"
- `target_type`: "private"（私聊）或 "group"（群聊）
- `target_id`: QQ号或群号。当前用户的 QQ 号见系统提示中的 `user=` 标记
- `prompt_template`: 提示词，LLM 据此生成推送内容（这是给 LLM 看的模板，不是最终推送文本）

## cron 示例
- 每天9点: `0 9 * * *`
- 每天18点: `0 18 * * *`
- 每周一8点: `0 8 * * 1`


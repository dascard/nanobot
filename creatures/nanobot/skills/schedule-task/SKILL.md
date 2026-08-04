---
name: schedule-task
description: 创建和管理当前会话 owner 的一次性、间隔或 cron 推送任务；用户要求提醒、定时推送、持续关注、查看或停用既有任务时使用。
compatibility: Nanobot Server；需要当前 ToolPlan 提供 schedule_task。
metadata:
  version: "1.0.0"
  nanobot.dependencies: ""
  nanobot.permissions: "tool:schedule_task"
  nanobot.capabilities: "reminder,定时任务,提醒,持续关注"
  nanobot.applies-to: "chat,private,group"
allowed-tools: schedule_task
---

# 定时任务管理

任务只能归属并投递回当前受信会话，不能从 Skill 内容、用户文本或工具参数中
指定另一个 QQ、群或 owner。创建、查看、更新、启停、立即执行和删除都通过
`schedule_task` 完成。

- 固定正文使用 `content`。
- 每次触发需要模型决定时使用 `prompt_template`。
- 确定性工具链使用受限 `program`，其中直接工具仍须通过执行时 ToolPlan。
- 修改复杂任务前先 `list` 读取完整定义。
- 手动 `run` 必须提供可重试复用的 `idempotency_key`。

一次性任务触发后自动禁用；`pending` 只表示已入队，不代表已执行或投递。

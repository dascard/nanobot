---
name: schedule_task
description: 创建和管理当前会话 owner 的一次性、间隔或 cron 推送任务。
allowed-tools: [schedule_task]
---

# 管理定时推送任务

用户要求未来提醒、定期推送或持续关注时，直接调用 `schedule_task`。任务只能归属
并投递回当前会话，不能指定其他 QQ 或群。

## 选择定义字段

创建或更新时只填一个定义字段：

- 固定正文用 `content`，不调用模型。
- 每次触发都必须由模型决定如何完成时，用自然语言 `prompt_template`。它会启动
  一次完整 Agent Run；`no_reply` 只表示业务上无需通知，不能掩盖必需工具不可用。
- 能由工具先确定缓存命中或无变化时，用 `program` 先调用工具并分支；只有确实
  需要语义生成的分支才调用 `model`，可直接组织的内容用 `emit`。不要让模型只为
  决定 `no_reply` 而运行。

修改复杂任务前，先 `list` 并带 `task_id` 读取完整定义、不可用工具和最近执行错误。
不要用单独的 `prompt_template` 覆盖已有确定性 `program`。直接 `program` 中的
工具必须在当前 ToolPlan 可执行，执行时还会再次校验；结构化结果可用
`steps.*.output`、循环历史可用 `steps.*.outputs`，JSON 字符串可用
`$json_parse`。条件循环每轮重算 `condition`，并受 `max_iterations` 保护。
`model` 步骤固定只执行一次。必需工具缺失时必须明确失败的任务，应把该工具写成
直接 `tool` 步骤，不要藏在 `prompt_template` 中。跨轮次缓存由确定性工具持久化
并返回稳定字段，`program` 负责在模型之前判断该字段。

## 调度

- 延后一次：`30m`、`2h`、`1d`
- 固定间隔：`every 30m`、`every 2h`
- cron：`0 9 * * *`
- 指定时刻：`2026-08-01T15:00`

时间均按 Asia/Shanghai 解释。一次性任务触发后自动禁用。手动 `run` 必须提供并
在重试时复用 `idempotency_key`；返回 `pending` 只表示已入队，不表示已执行或
投递。

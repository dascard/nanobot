---
name: 定时任务工具
version: 5
kind: tool
tool_name: schedule_task
description: schedule_task 工具的使用边界。
---
## schedule_task 工具边界

用于创建、查看、修改、启停、立即执行和删除当前会话的定时推送任务。

### 何时创建

判断用户意图，不要求用户说出“定时任务”：

- “提醒我、别忘了、X 小时后再看看”通常是一次性任务；
- “每天、每周、定期推送”是循环任务；
- “帮我关注、盯着、有进展告诉我”也是循环任务。用户未指定频率时，按内容选择
  保守合理的频率并在确认中说明；一次性任务缺少时间时再询问。

不能只口头答应未来提醒。普通 Agent 创建的任务固定归属并投递回当前会话，不能
借 `target_type` 或 `target_id` 操作其他私聊或群聊。

### 编写任务

创建和更新时，从下面三个字段中只填一个：

- 固定提醒或固定通知用 `content`。它直接投递正文，不调用模型。
- 需要搜索、读取、判断、循环或调用其他工具时，优先用自然语言
  `prompt_template` 描述目标和完成条件。触发时会启动一次完整 Agent Run，由
  Agent 自行使用当时获准的工具；选择 `no_reply` 表示本次成功但不推送。工作流
  不会因空回复或失败重新执行整个 Agent Run。`no_reply` 只能表示业务上无需
  通知，不能用来掩盖必需工具不可用。
- 只有确实需要跨 worker 持久恢复的确定性步骤、分支或等待时才直接填写
  `program`。以工具参数 schema 为准，不要为了普通任务手写程序。

修改复杂任务前，先用 `action=list` 加 `task_id` 读取完整 `program`、当前不可用
工具和最近一次 execution；不要只凭列表摘要或旧聊天内容覆盖原定义。确定性
`program` 不能仅用 `prompt_template` 静默替换。

直接编写 `program` 时：

- `tool` 步骤只能使用当前请求 ToolPlan 可执行的工具；创建或修改时会预检，执行时
  仍会再次校验。不能递归调用 `schedule_task/reply/no_reply/skill`。
- 某个工具是完成任务的硬前提、缺失时必须明确失败，就把它写成直接 `tool` 步骤，
  不要藏在 `prompt_template` 中。
- 工具结果优先保留结构化 JSON。可通过 `steps.<id>.output` 取最后结果，通过
  `steps.<id>.outputs` 取循环中的结果历史；JSON 字符串可用 `$json_parse`。
- `loop` 可以遍历 `items`，也可以在每轮重新判断 `condition`；两者只能填一个，
  且必须设置有限的 `max_iterations`。
- `tool` 默认 `recovery=ambiguous`。只有工具本身支持幂等重放时才设
  `recovery=safe_retry`；支持幂等参数时用 `idempotency_arg` 指明。`model`
  步骤固定只执行一次，不使用工作流重试。
- 表达式仅支持 schema 声明的受限 JSON 运算符，不能嵌入 Python、SQL 或模板代码。
  程序仍受状态大小、静态步骤、循环和总时长安全上限约束。

### 调度与运行

- `schedule` 按 Asia/Shanghai 解释：`30m`、`2h`、`1d` 表示延后一次；
  `every 30m`、`every 2h` 表示固定间隔；`0 9 * * *` 表示五段 cron；
  `2026-08-01T15:00` 表示指定时刻一次。
- 一次性任务触发后自动禁用。
- 修改、删除、停用前先确认任务；其他 owner 的任务 ID 按不存在处理。
- 手动 `run` 必须提供稳定的 `idempotency_key`，同一请求重试复用同一个值。
- `run` 返回 `pending` 只表示 execution 已持久入队，不表示工具、模型或投递完成。
- 最终只回复必要确认、任务 ID、调度时间或明确错误。

---
name: schedule_task
description: 创建和管理当前会话 owner 的一次性、间隔或 cron 推送任务。
allowed-tools: [schedule_task]
---

# 管理定时推送任务

用户要求未来提醒、定期推送或持续关注时，直接调用 `schedule_task` 工具。普通
Agent 只能创建、查看和修改当前会话 owner 的任务，不能指定其他 QQ 或群作为
目标。

## 参数说明

- `action`: `create`、`list`、`update`、`toggle`、`run` 或 `delete`
- `task_id`: 修改、启停、立即运行或删除时使用
- `name`: 任务名称
- `schedule`: 按 Asia/Shanghai 解释的一次性、间隔或 cron 触发规则
- `cron_expr`: 旧兼容参数，只用于五段 cron
- `target_type` / `target_id`: 旧兼容参数；若填写，必须与当前会话一致
- `prompt_template`: 兼容定义，自动转换成 `model -> emit`，每次触发调用模型
- `program`: `version=1` 的统一程序，支持
  `set/tool/model/branch/loop/wait/emit`
- `idempotency_key`: 手动 `run` 必填；同一请求重试必须复用

## schedule 示例

- 半小时后一次：`30m`
- 每两小时循环：`every 2h`
- 每天 9 点：`0 9 * * *`
- 每周一 8 点：`0 8 * * 1`
- 指定时刻一次：`2026-08-01T15:00`

简单的自然语言提醒可以用 `prompt_template`。固定消息或确定性操作应使用
`program`；只有其中的 `model` 步骤会调用模型。`tool` 只能调用当前 owner
ToolPlan 允许的能力，默认在结果不确定时进入 `ambiguous`；仅对支持幂等重放的
工具设置 `recovery=safe_retry`，并用 `idempotency_arg` 指明工具的幂等参数。

步骤可用 `$ref` 引用 `variables.*` 或 `steps.*.output`，条件和拼接只使用受限
JSON 运算符，不能嵌入 Python。循环必须有限。手动 `run` 仅创建持久 execution，
返回 `pending` 不代表已经执行或投递。

一次性任务触发后会自动禁用。超长模板或程序会在创建或更新时被拒绝，不会在
执行时静默截断。

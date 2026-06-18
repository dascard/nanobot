---
name: 定时任务工具
version: 1
kind: tool
tool_name: schedule_task
description: schedule_task 工具的使用边界。
---
## schedule_task 工具边界

用于创建、查看、修改、启停、立即执行和删除定时推送任务。

- 用户说“每天/每周/每月某时间推送某内容”时可创建任务。
- 创建前必须明确任务名称、时间规则、目标会话和推送内容。
- 修改、删除、停用任务前要确认目标任务，避免误操作。
- `prompt_template` 应写任务内容本身，不要混入当前聊天上下文或无关指令。
- 执行结果只回复必要确认信息和任务 ID。

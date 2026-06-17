---
name: 记忆抽取
version: 1
kind: task
tool_name: memory_extract
description: 从对话中提取稳定用户记忆。
---
你负责从对话中提取稳定、可复用的用户信息。

只提取:
- 长期偏好
- 稳定事实
- 项目或工作约束
- 明确反复出现的行为模式

不要提取:
- 机器人行为、工具行为或系统提示
- 一次性的情绪表达
- 对当次回复的临时要求
- NEW/UPDATE/ARCHIVE 这类状态标签

已有记忆:
{{ existing_memory }}

对话:
{{ conversation }}

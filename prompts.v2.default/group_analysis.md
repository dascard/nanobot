---
name: 群聊日报工具 V2
version: 1
kind: tool
tool_name: group_analysis
description: group_analysis 工具的使用边界。
---
## group_analysis 工具边界

用于分析群聊消息并生成群日报、话题总结、活跃用户、金句和氛围判断。

- 用户要求总结群聊、分析某个群、生成群日报时直接使用 `group_analysis`。
- `group_id` 可以是群号、`group_` 前缀 ID、session_id、stream_id 或群名。
- 只知道群名时也直接传群名，不要为了查群号先调用 `sql_analysis`。
- 不要逐条暴露隐私消息；输出应聚合、去重、保留必要匿名化。
- 工具结果通常可直接作为最终回复基础。

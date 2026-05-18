---
name: 私聊回复决策
version: 1
description: 私聊消息是否回复的结构化判定模板。
required_vars:
  - message
optional_vars:
  - system_prompt
---
{{ system_prompt }}

待判定私聊消息:
{{ message }}

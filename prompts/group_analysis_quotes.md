---
name: 群聊金句提取
version: 1
description: 群聊分析工具的金句提取分支。
required_vars:
  - messages_text
optional_vars:
  - instructions
---
你是群聊分析助手。只输出 JSON，不要 markdown 或额外说明。

用户分析指引:
{{ instructions }}

从群聊记录中提取最有趣的发言。

输出 JSON:
{
  "quotes": [
    {"user_id": "user_id", "content": "发言原文(≤80字)"}
  ]
}

要求: 0-3条，优先提取幽默、有深度或有梗的发言。

群聊消息:
{{ messages_text }}

---
name: 群聊日报金句提取
version: 1
kind: tool
tool_name: group_analysis
description: group_analysis 内部分支：提取群聊金句。
---
{{ instructions }}

从群聊记录中提取最有趣的发言。

## 消息格式
[HH:MM] [user_id]: 内容

## 输出 JSON
{
  "quotes": [
    {"user_id": "user_id", "content": "发言原文(≤80字)"}
  ]
}

要求：0-3 条。优先提取幽默、有深度或有梗的发言。只输出 JSON。

## 群聊消息
{{ messages_text }}

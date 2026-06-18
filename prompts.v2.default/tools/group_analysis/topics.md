---
name: 群聊日报话题提取
version: 1
kind: tool
tool_name: group_analysis
description: group_analysis 内部分支：提取核心讨论话题。
---
{{ instructions }}

分析以下群聊记录，提取核心讨论话题。

## 消息格式
[HH:MM] [user_id]: 内容

## 输出 JSON
{
  "topics": [
    {"topic": "话题名(≤15字)", "contributors": ["user_id"], "detail": "一句话总结讨论内容"}
  ]
}

要求：2-5 个话题，按讨论热度排序。只输出 JSON。

## 群聊消息
{{ messages_text }}

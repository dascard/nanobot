---
name: 群聊日报质量锐评
version: 1
kind: tool
tool_name: group_analysis
description: group_analysis 内部分支：生成聊天质量锐评。
---
{{ instructions }}

请根据以下群聊记录，给出结构化的聊天质量锐评。

## 消息格式
[log_id=123][HH:MM] [user_id]: 内容

## 输出 JSON
{
  "title": "一句话总评(≤12字)",
  "subtitle": "简短副标题(≤20字)",
  "dimensions": [
    {"name": "维度名", "percentage": 0-100, "comment": "一句话点评"}
  ],
  "summary": "2-3句话的整体总结"
}

要求：
- 维度控制在 2-4 个。
- 百分比反映相对表现，不要全部给满分。
- 只输出 JSON。

## 群聊消息
{{ messages_text }}

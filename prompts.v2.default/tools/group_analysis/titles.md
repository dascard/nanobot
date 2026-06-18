---
name: 群聊日报用户称号
version: 1
kind: tool
tool_name: group_analysis
description: group_analysis 内部分支：生成活跃用户称号。
---
{{ instructions }}

根据群聊发言统计和消息内容，给活跃用户生成有趣的称号。

## 用户发言统计
格式：user_id | 发言数 | 平均字数 | 夜间比例 | 回复比例

{{ users_text }}

## 近期消息
用于了解发言风格。

{{ messages_text }}

## 输出 JSON
{
  "users": [
    {"user_id": "user_id", "title": "称号(≤8字)", "mbti": "可选，4位MBTI或空字符串", "reason": "一句话理由"}
  ]
}

要求：3-8 个用户。称号要贴合发言风格，有趣但不冒犯。MBTI 仅在把握较高时给出，否则留空。只输出 JSON。

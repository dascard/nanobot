---
name: 群聊用户称号
version: 1
description: 群聊分析工具的活跃用户称号分支。
required_vars:
  - users_text
  - messages_text
optional_vars:
  - instructions
---
你是群聊分析助手。只输出 JSON，不要 markdown 或额外说明。

用户分析指引:
{{ instructions }}

根据群聊发言统计和近期消息，给活跃用户生成有趣但不冒犯的称号。

用户发言统计:
{{ users_text }}

近期消息:
{{ messages_text }}

输出 JSON:
{
  "users": [
    {"user_id": "user_id", "title": "称号(≤8字)", "mbti": "可选，4位MBTI或空字符串", "reason": "一句话理由"}
  ]
}

要求: 3-8个用户。MBTI 仅在把握较高时给出，否则留空。

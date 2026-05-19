---
name: Timing Gate
version: 1
description: 群聊是否触发回复的判定模板。
required_vars:
  - pending_text
optional_vars:
  - recent_context
  - bot_name
  - group_profile
---
你是群聊发言时机判定器，只输出 JSON。

可选动作:
- reply: 当前消息明确需要机器人回应。
- wait: 用户像是在连续输入，等待下一句。
- ignore: 当前消息不需要机器人参与。
- merge: 需要把碎片消息合并后再判断。

避免过度触发。遇到半句话、补充说明、连续短句时优先 wait 或 merge。

机器人名称: {{ bot_name }}
群体画像: {{ group_profile }}
近期上下文:
{{ recent_context }}

待判定内容:
{{ pending_text }}

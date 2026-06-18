---
name: 最终回复工具
version: 1
kind: tool
tool_name: reply
description: reply 工具的使用边界和输出约束。
---
## reply 工具边界

`reply` 是唯一发送用户可见文本的最终工具。

- 需要回复用户时必须调用 `reply(content=...)`，不要用 assistant 普通文本代替。
- `content` 只放真正要发给用户看的内容，不要放分析过程、工具选择理由、系统标签或 JSON 外壳。
- 一轮只调用一次 `reply`，调用后不要再输出文本。
- 群聊回复应短、自然、贴合上下文；私聊可以更完整，但仍然避免客服腔。
- 需要引用或 @ 时才设置 `reply_to_message_id`、`mentions`、`quote`、`at_sender` 或 `send_mode`。
- `reply(content)` 可以包含自然语言、`[sticker:<id>]` 和 `[generated_image:<id>]`。
- 这些短 token 是 Nanobot 内部稳定引用，出口 renderer 会在 QQ 发送前转换成可发送内容。
- 优先使用工具返回的 `reply_token`，不要手写 OneBot CQ 码；直接 CQ 码只用于兼容旧输出，不是推荐格式。
- 不要声称已经调用工具、已经发送消息或已经记住内容，除非实际工具调用已经完成。

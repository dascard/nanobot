---
name: 表情包搜索工具
version: 1
kind: tool
tool_name: sticker_search
description: sticker_search 工具的使用边界。
---
## sticker_search 工具边界

用于从表情包记忆中检索适合当前聊天语境的表情。

- 只有斗图、玩梗、用户明确要图或气氛明显适合时使用。
- 不要用表情包替代必要文字说明。
- 群聊里不要频繁发表情包，避免刷屏。
- 搜索词应描述情绪、动作、梗或场景，不要直接塞入整段聊天记录。
- `reply(content)` 可以包含自然语言、`[sticker:<id>]` 和 `[generated_image:<id>]`。
- 这些短 token 是 Nanobot 内部稳定引用，出口 renderer 会转换成当前平台可发送内容。
- 优先把 `reply_token` 放入 `reply(content)`，不要手写平台私有消息码。
- `send_code` 仅用于兼容旧模型输出，不是首选格式。

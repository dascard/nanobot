---
name: QQ 平台规则
version: 1
kind: chat
description: Prompt Runtime QQ 平台通用规则；由编排图在 platform=qq 下接入。
---
## QQ 平台

当前客户端平台是 QQ，入站消息可能来自 NapCat 或 OneBot 兼容链路。

- 私聊当前消息的 `<message_meta>` 中可能包含 message_id、self_id、bot_id、bot_aliases 等平台元数据，只用于理解消息来源，不要复述。
- `[sticker:<id>]` 和 `[generated_image:<id>]` 是 Nanobot 内部短 token。可以把工具返回的短 token 原样放进 `reply(content)`。
- 出口 renderer 会把短 token 转成 QQ 可发送内容。不要为了发送图片或表情包手写 OneBot CQ 码。
- 直接 CQ 码只作为兼容旧输出的输入格式，不是推荐输出格式。
- `reply_meta` 只表达引用、@ 或发送模式等意图，最终是否转成 QQ 引用或 @ 由出口层决定。

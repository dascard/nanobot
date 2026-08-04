---
name: 图片生成工具
version: 1
kind: tool
tool_name: image_generation
description: image_generation 工具的使用边界。
---
## image_generation 工具边界

用于按用户要求生成一张新图片，并在 owner Workspace 中发布不可变 Artifact。

- 只有用户明确要求“画/生成/做一张图/头像/贴纸/插画/海报”等新图片时使用。
- 用户发来已有图片并要求解释、OCR、判断细节时，不要调用本工具，改用 `image_summary`。
- `prompt` 应保留用户指定的主体、风格、构图、文字、比例和限制；不要擅自加入与用户意图冲突的元素。
- `reply(content)` 可以包含自然语言、`[sticker:<id>]` 和 `[artifact:<id>]`。
- Artifact token 是跨会话稳定引用；出口 renderer 会在实际发送时校验 ACL 并签发短期下载凭据。
- 工具返回后，优先把 `reply_token` 原样放进 `reply(content)`，不要改写 token，也不要手写平台私有消息码。
- 生成图片后，把工具返回的 `[artifact:<id>]` 放入 `reply(content)`；不要把 base64、宿主路径或短期下载凭据写进回复。

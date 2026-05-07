# Maibot 群聊与表情包系统实现计划

## 目标修正

用户已明确：表情包自动注册应在范围内，当前 Qwen 视觉模型可作为后台描述器，必要时允许修改 QQbot，并优先兼容文本和图片消息。

## 用户旅程

1. 群友发纯表情包或图片，QQbot 仍把消息送到 Nanobot，服务端写入群聊现场并自动注册可复用表情。
2. 表情没有描述时，服务端后台调用现有 Qwen 视觉能力补齐描述、标签和情绪。
3. 主模型在斗图、玩梗或用户明确要求时调用 `sticker_search`，拿到 CQ 图片码并通过 `reply(content)` 发出。
4. 管理者可以通过 HTTP API 显式注册、搜索和禁用表情。

## TDD 步骤

1. 先写 `tests/test_sticker_memory.py`，覆盖注册去重、搜索排序、禁用过滤和使用统计。
2. 先写 `/group/message` 图片入站测试，要求 `files/client_meta.stickers` 自动注册并以占位文本进入 TimingGate。
3. 先写 `sticker_search` 工具测试，要求输出可直接发送的 CQ 图片码。
4. 先写 prompt contract 测试，要求工具路由和克制使用规则存在。
5. 在 QQbot 仓库补纯图片入站和 CQ 图片出站测试。

## 实现切片

1. Server 数据层：新增 `StickerMemory` 表和 `core/sticker_memory.py`。
2. Server API：扩展 `GroupMessageRequest`，新增 `/stickers/register`、`/stickers/search`、`/stickers/{id}/disable`。
3. Server 工具：新增 `sticker_search` KT 工具并加入 `creatures/nanobot/config.yaml`。
4. Prompt：补充群聊表情包使用规则、工具路由和工具纪律，重建 `prompt.md`。
5. QQbot：群聊 lurker 提取图片/表情消息段；`_send_answer()` 识别纯 CQ 图片码并发送图片。

## 验证

1. 先运行新增测试确认 RED。
2. 实现后运行相关测试到 GREEN。
3. 运行 `python -m pytest tests/ -v`。
4. 在 QQbot 仓库运行相关 pytest。
5. 检查 `git diff`，只提交本次相关文件；不使用 `git add -A`。

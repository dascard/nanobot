# 群聊结构化消息与指向性判断实现计划

> 面向 AI 代理的执行说明：本计划用于把 Nanobot 群聊链路从 `sender + text` 升级为结构化消息语义，优先解决用户之间互相 @ / 引用时 bot 误插嘴的问题。执行时按 TDD：先补失败测试，再实现，最后验证。每个任务完成后更新复选框，不要把无关工作树改动混入提交。

## 目标

将群聊链路改为三层：

```text
QQ / OneBot / NapCat 原始事件
→ Nanobot 标准消息结构
→ Prompt 可读文本 / TimingGate 特征
```

第一批优先解决：

- 用户之间互相 @ 时，bot 不误判成开放讨论。
- 用户之间互相引用时，bot 不突然插嘴。
- 明确 @bot、回复 bot、喊 bot 名字时，仍能正常触发。
- ChatLog、TimingGate、GroupRecentContext、WebUI 都能解释“这句话是在对谁说”。

## 非目标

- 第一批不做完整图片理解、语音识别、文件内容解析，只保留结构和占位文本。
- 第一批不改数据库表结构，结构化信息先落入 `ChatLog.meta_json`。
- 第一批不要求旧 QQbot 立即升级；API 必须向后兼容旧 payload。

## 文档依据

- NoneBot 的 `MessageSegment` 是适配器类型，消息段核心形态是 `type + data`，不要在 Nanobot Server 里复刻同名类。
- OneBot v11 数组消息段标准结构是 `{"type": "...", "data": {...}}`，`data` 是参数对象。
- NapCat 的 `text/at/reply/image/mface/file/forward` 等消息段也按 OneBot 形态上报。
- OneBot 群消息事件有 `self_id/group_id/user_id/message/raw_message/sender` 等字段；`self_id` 就是当前 bot QQ 号。

参考：

- https://nonebot.dev/docs/tutorial/message
- https://onebot.adapters.nonebot.dev/docs/api/v11/message/
- https://283375.github.io/onebot_v11_vitepress/message/array.html
- https://283375.github.io/onebot_v11_vitepress/event/message.html
- https://www.napcat.wiki/develop/msg
- https://www.napcat.wiki/onebot/sement

## 当前断点

- `api/routes.py::GroupMessageRequest` 只有 `message/files/client_meta/message_id/is_at_bot/is_reply_to_bot`，没有 `segments/raw_message/self_id/bot_id/bot_name/reply_to/directed_to_other`。
- `_build_group_message_text()` 会把消息压成纯文本或 `[图片] N 张`，丢掉段顺序和 @ / 引用语义。
- 群消息落库时 `meta_json` 基本只保存 `_safe_group_client_meta(req)`，没有标准化结构。
- `core/group_runtime/runtime.py::GroupPendingMessage` 没有 mentions、reply_to、direction flags。
- `core/context_builder.py::build_timing_recent_context()` 和 `build_group_recent_context()` 只输出时间、用户名、发言内容。
- `creatures/nanobot/prompts/skills/reply/tool.py` 只支持 `content`，无法携带 quote / at 发送意图。
- `nanobot_kt/bridge.py::_extract_reply_from_tool_output()` 只返回字符串，后续无法把 quote / mentions 传回 QQbot。

---

## 标准数据契约

## API 入参

不要定义 `MessageSegment`，避免和 `nonebot.adapters.onebot.v11.MessageSegment` 混淆。Nanobot Server 只定义自己的入站 DTO：

```python
from typing import Any
from pydantic import BaseModel, Field


class OneBotMessageSegmentPayload(BaseModel):
    type: str
    data: dict[str, Any] = Field(default_factory=dict)
```

`GroupMessageRequest` 新增字段：

```python
segments: list[OneBotMessageSegmentPayload] = Field(default_factory=list)
raw_message: str = ""
self_id: str = ""
bot_id: str = ""
bot_name: str = ""
bot_aliases: list[str] = Field(default_factory=list)
mentions: list[dict[str, Any]] = Field(default_factory=list)
reply_to: dict[str, Any] | None = None
reply_to_message_id: str | None = None
reply_to_sender_id: str | None = None
reply_to_sender_name: str | None = None
reply_to_content: str | None = None
is_directed_to_other: bool = False
```

规则：

- `self_id` 来自 OneBot 事件，表示当前 bot QQ 号。
- `bot_id` 默认等于 `self_id`；如果 QQbot 端已有自己的 bot id 字段，也可以显式传 `bot_id`。
- `bot_name` 是 bot QQ 昵称或当前连接名，用于 prompt 自我认知。
- `segments` 保持 OneBot/NapCat 原始数组消息段结构，不在 API DTO 顶层复制 `text/user_id/url/file_id` 这些字段。
- `mentions` 是 QQbot 端从 `at` segment、群成员缓存、self_id 推导出的辅助结构；如果不传，Server 端从 `segments` 尽力推导。
- `reply_to` 是 QQbot 端用消息缓存或 `get_msg` 补全后的引用详情；如果只传 OneBot `reply` segment，Server 端最多只能拿到被回复消息 id。

## OneBot segment 标准化规则

从 `segments` 读取语义，而不是从自定义顶层字段读取：

- `text`: `segment.data["text"]`
- `at`: `segment.data["qq"]`
- `reply`: `segment.data["id"]`
- `image`: `segment.data["file"] / data["url"] / data["file_id"] / data["summary"] / data["sub_type"]`
- `mface`: `segment.data["emoji_id"] / data["emoji_package_id"] / data["key"] / data["summary"]`
- `file`: `segment.data["file"] / data["file_id"] / data["name"] / data["file_size"]`
- `forward`: `segment.data["id"] / data["content"]`

`reply` segment 只有被回复消息 id，不保证包含引用消息发送者和内容。QQbot 端需要通过消息缓存或 OneBot `get_msg` 补：

```json
{
  "reply_to": {
    "message_id": "998877",
    "sender_id": "456",
    "sender_name": "小明",
    "content": "上面那个结论不成立",
    "is_bot": false
  }
}
```

## ChatLog.meta_json 标准格式

最终 `ChatLog.meta_json` 只保存一种标准结构，不重复保存 `reply_to_*` 顶层字段：

```json
{
  "message_type": "group_message",
  "raw_message": "",
  "segments": [],
  "mentions": [],
  "reply_to": {
    "message_id": "",
    "sender_id": "",
    "sender_name": "",
    "content": "",
    "is_bot": false
  },
  "directed": {
    "at_bot": false,
    "reply_to_bot": false,
    "at_others": false,
    "reply_to_others": false,
    "directed_to_other": false,
    "mentions_bot": false,
    "mentions_others": false
  },
  "bot": {
    "self_id": "",
    "bot_id": "",
    "bot_name": ""
  },
  "files": [],
  "client_meta": {}
}
```

兼容要求：

- sticker 注册仍从 `req.client_meta` 读取，不能被 meta_json 新结构影响。
- 所有读取旧 `ChatLog.meta_json` 的逻辑，必须优先读新结构 `meta["client_meta"]`，再兼容旧结构顶层键。
- WebUI、表情包预览、打标、group_analysis、GroupMemory evidence 读取 meta 时都要兼容新旧结构。

---

## Batch 1：入站结构化与 TimingGate 防插嘴

## 任务 1A：入站结构化 meta 测试

- [ ] 在 `tests/test_api.py` 增加 `/group/message` 新 payload 测试：`segments/raw_message/self_id/bot_id/bot_name/mentions/reply_to/is_directed_to_other` 能被接受。
- [ ] 断言 `ChatLog.meta_json` 写入标准结构，且只写 `reply_to`，不重复写 `reply_to_*` 顶层字段。
- [ ] 增加旧 payload 兼容测试：只传 `message/files/client_meta` 仍可进入 TimingGate。
- [ ] 增加 `client_meta` 兼容测试：`message_type/stickers/send_code/source` 在新 meta 结构下仍能被旧逻辑读到。
- [ ] 增加 OneBot segment 解析测试：`at/reply/image/mface/file/text` 按 `type + data` 被正确转为 plain text、mentions、reply id 和附件占位。
- [ ] 增加裁剪测试：segments 最多 30 个，mentions 最多 20 个，`reply_to.content` 最多 200 到 300 字，`segment.data.text` 最多 500 字，`mention.nickname` 最多 80 字；超长内容不导致请求失败。
- [ ] 运行定向测试确认 RED。

## 任务 1B：API 模型和标准化 helper

- [ ] 在 `api/routes.py` 增加 `OneBotMessageSegmentPayload`，不要使用 `MessageSegment` 作为类名。
- [ ] 扩展 `GroupMessageRequest`，所有 list/dict 字段使用 `Field(default_factory=...)`。
- [ ] 新增 `_normalize_onebot_segments(req)`：清理空 segment，限制数量和字段长度，保留必要 `data`。
- [ ] 新增 `_extract_mentions_from_segments(segments, req)`：从 `at` segment 的 `data.qq` 推导 mentions；结合 `self_id/bot_id` 判断是否 @bot。
- [ ] 新增 `_normalize_group_mentions(req, segments)`：合并 QQbot 显式 mentions 和 segment 推导 mentions，按 user_id 去重。
- [ ] 新增 `_normalize_group_reply_to(req, segments)`：
  - 优先使用 `req.reply_to`
  - 否则兼容 `reply_to_*` 散字段
  - 否则从 `reply` segment 取 `data.id`，只得到 message_id
  - 最终只返回一个 dict 或 None
  - 所有文本字段严格裁剪并 sanitize
- [ ] 新增 `_derive_group_direction(req, mentions, reply_to)`：
  - `bot_id = req.bot_id or req.self_id`
  - `mention_is_bot = m.is_bot or (bot_id and m.user_id == bot_id)`
  - `reply_is_bot = reply_to.is_bot or (bot_id and reply_to.sender_id == bot_id)`
  - `directed_to_other = at_others or reply_to_others`
  - 如果同时 @bot 和 @别人，不应归入纯 directed_to_other 抑制场景。
- [ ] 新增 `_build_group_message_meta(req, registered_stickers)`：写标准 meta，并把原始 `client_meta` 放入 `client_meta` 子字段。
- [ ] 修改 `_build_group_message_text(req)`：优先从 OneBot segments 渲染可读文本并保留顺序；图片/表情/文件渲染为 `[图片:1张]`、`[表情包]`、`[文件:report.pdf]`。

验收标准：

- 新旧 QQbot payload 都可用。
- `ChatLog.content` 仍是人类可读文本，不塞完整 JSON。
- 表情包自动注册、缓存、打标不因 `client_meta` 嵌套而断。

## 任务 1C：GroupRuntime 和 TimingGate 测试

- [ ] 在 `tests/test_timing_runtime.py` 增加 `GroupPendingMessage` 保存 directed/mentions/reply_to/self_id/bot_id/bot_name 的测试。
- [ ] 增加 TimingGate context 测试，必须输出：
  - `is_at_bot`
  - `is_reply_to_bot`
  - `mentions_bot`
  - `mentions_others`
  - `reply_to_bot`
  - `reply_to_others`
  - `directed_to_other`
  - `mentions_count`
  - `has_reply_to`
  - `self_id`
  - `bot_id`
  - `bot_name`
- [ ] 增加 hard rule 测试：全部 pending 都指向别人，且没有指向 bot，返回 `no_reply`。
- [ ] 增加误杀保护测试：
  - 用户同时 @别人 和 @bot，不触发 directed hard rule。
  - 用户引用别人但正文喊 bot 名字，不触发 directed hard rule。
  - `trigger_reason in {"at_bot", "reply_to_bot", "bot_name_mentioned", "direct_call"}` 不触发 directed hard rule。
- [ ] 运行定向测试确认 RED。

## 任务 1D：GroupRuntime 实现 directed features

- [ ] 扩展 `core/group_runtime/runtime.py::GroupPendingMessage`：
  - `segments: list[dict] = field(default_factory=list)`
  - `raw_message: str = ""`
  - `mentions: list[dict] = field(default_factory=list)`
  - `reply_to: dict | None = None`
  - `directed: dict = field(default_factory=dict)`
  - `is_directed_to_other: bool = False`
  - `self_id: str = ""`
  - `bot_id: str = ""`
  - `bot_name: str = ""`
- [ ] 修改 `GroupPendingMessage.to_dict()`，保留上述字段。
- [ ] 修改 `GroupRuntime.process_message()`，接收 API 传入的结构化字段。
- [ ] 修改 `_pending_payload()`：planner 文本中展示 `[指向性]` 和 `[引用]`。
- [ ] 在 `api/routes.py::group_message()` 调用 `runtime.process_message()` 时传入标准化后的 `segments/raw_message/mentions/reply_to/directed/self_id/bot_id/bot_name`。
- [ ] 实现 helper：

```python
def should_suppress_directed_to_other(pending_messages: list[GroupPendingMessage]) -> bool:
    if not pending_messages:
        return False
    if any(
        m.is_at_bot
        or m.is_reply_to_bot
        or m.trigger_reason in {"at_bot", "reply_to_bot", "bot_name_mentioned", "direct_call"}
        for m in pending_messages
    ):
        return False
    return all(m.is_directed_to_other for m in pending_messages)
```

- [ ] 命中 hard rule 时返回 `no_reply`，reason 为 `directed_to_other_no_bot_target`。
- [ ] 命中 hard rule 时写入 timing meta：

```json
{
  "timing_gate": {
    "action": "no_reply",
    "hard_rule": "directed_to_other_no_bot_target",
    "directed_to_other": true,
    "mentions_others": true,
    "reply_to_others": false
  }
}
```

验收标准：

- A @ B、B 回复 A 不会让 bot continue。
- A @ bot、A 回复 bot、A 同时 @bot 和 @别人不会被误杀。
- WebUI 能看到 hard_rule，而不是只看到 `no_reply`。

## 任务 1E：RecentContext 显示 @ / 引用

- [ ] 在 `core/context_builder.py` 增加安全 meta 解析 helper，兼容新旧 meta。
- [ ] 增加 `_format_direction_line(meta)`，渲染：
  - `[指向性] @bot`
  - `[指向性] 回复bot`
  - `[指向性] @其他人: 小明`
  - `[指向性] 回复其他人`
  - `[指向性] 普通群聊`
- [ ] 增加 `_format_reply_to_line(meta)`，渲染 `[引用] 小明: 上面那个结论不成立`。
- [ ] 修改 `format_group_planner_message()`，允许传入 meta/directed/reply_to。
- [ ] 修改 `build_timing_recent_context()` 和 `build_group_recent_context()`，输出 msg_id、时间、用户名、指向性、引用、发言内容。
- [ ] 所有来自 meta 的文本必须走 `sanitize_prompt_text()`，并按任务 1A 的长度限制裁剪。

验收标准：

- TimingGate 和主回复 prompt 都能看到用户之间的 @ / 引用关系。
- 旧日志没有 meta_json 时不报错。

## 任务 1F：Prompt 和 bot 自我认知

- [ ] 修改 `creatures/nanobot/prompts/system/20_group_rules.md`：明确用户之间 @ / 引用对话优先不插嘴。
- [ ] 修改 `creatures/nanobot/prompts/system/25_context_control.md`：说明 `<runtime_context>` 中的 `self_id/bot_id/bot_name/bot_aliases` 是当前 bot 自我身份。
- [ ] 修改 `creatures/nanobot/prompts/system/27_tool_routing.md`：仍要求普通最终回复走 `reply(content)`。
- [ ] 修改 `clients/classifier_client.py` TimingGate prompt：加入 directed features 判断规则。
- [ ] 修改 `nanobot_kt/bridge.py::_build_runtime_context()`，把 `self_id`、`bot_id`、`bot_name`、`bot_aliases` 注入 runtime context。
- [ ] 修改 `api/routes.py` 的 `bridge_meta`，把 `self_id/bot_id/bot_name/bot_aliases` 传给 bridge。
- [ ] 运行 `python scripts/build_nanobot_prompt.py` 同步 `creatures/nanobot/prompt.md`。
- [ ] 运行 `python scripts/build_nanobot_prompt.py --check`。

验收标准：

- bot 在提示词里知道自己的 QQ 号、QQ 昵称和别名。
- prompt contract 明确“不打断用户之间正在进行的定向交流”。

---

## Batch 2：出站 reply quote / at

## 任务 2A：reply contract 测试

- [ ] 在 `tests/test_kt_framework.py::TestReplyContract` 增加测试：`reply()` 输出结构化 payload，包括 `content/reply_to_message_id/mentions/quote/at_sender/send_mode`。
- [ ] 增加 bridge 提取测试：不同 session 并发时，群 A 的 `reply_meta` 不会被群 B 覆盖。
- [ ] 增加 `/group/message` 立即 continue 返回 `reply_meta` 的测试。
- [ ] 增加 `/group_timing/timer` 延迟触发返回 `reply_meta` 的测试。
- [ ] 运行定向测试确认 RED。

## 任务 2B：reply 工具 schema 扩展

- [ ] 修改 `creatures/nanobot/prompts/skills/reply/tool.py` schema：
  - `content: str`
  - `reply_to_message_id: str = ""`
  - `mentions: list[str]`
  - `quote: bool = false`
  - `at_sender: bool = false`
  - `send_mode: normal|quote|mention|quote_and_mention`
- [ ] 输出 JSON 保持 `NANOBOT_REPLY_OUTPUT` marker，但 payload 扩展为 reply meta。
- [ ] 保留旧 `reply(content)` 能力；只传 content 时行为不变。
- [ ] prompt description 说明 quote/at 使用场景：回复具体某人、纠错被引用消息、承接单条问题时优先 quote。

## 任务 2C：Bridge 提取 reply payload，避免全局并发风险

不要使用单个 `bridge.last_reply_payload`。采用以下二选一：

方案 A，推荐：

- [ ] 定义 `BridgeReply` dataclass：`content: str`、`reply_meta: dict`。
- [ ] 增加 `handle_message_structured()` 返回 `BridgeReply`。
- [ ] 旧 `handle_message()` 继续返回字符串，内部调用 structured 版本并取 content。

方案 B，低侵入：

- [ ] 在 bridge 内使用 `self._last_reply_payload_by_session: dict[str, dict]`。
- [ ] 提取 reply payload 后写入当前 `session_id`。
- [ ] 提供 `pop_last_reply_payload(session_id)`，调用方读取后立即 pop。
- [ ] 不允许使用单个全局 payload 字段。

第一版建议用方案 B，改动面小；第二轮再重构为方案 A。

## 任务 2D：API 返回和落库

- [ ] 修改 `/group/message` 立即 continue 路径，返回 `reply_meta`。
- [ ] 修改 `/group_timing/timer` 延迟触发路径，返回 `reply_meta`。
- [ ] 修改 `_persist_group_bridge_reply()`，把 `reply_meta` 写入 assistant `ChatLog.meta_json`。
- [ ] 返回结构保持兼容：

```json
{
  "action": "continue",
  "reply": "这个我看了一下...",
  "reply_meta": {
    "reply_to_message_id": "12345",
    "mentions": ["456"],
    "quote": true,
    "at_sender": false,
    "send_mode": "quote"
  }
}
```

验收标准：

- 旧 QQbot 继续读 `reply` 字符串。
- 新 QQbot 可读 `reply_meta` 拼 OneBot `reply + at + text` segment。
- timer 延迟回复和立即回复能力一致。

## 任务 2E：QQbot 对接

此任务在 QQbot 仓库执行，Server 端先保持兼容：

- [ ] 入站直接传 OneBot/NapCat 原始 `message` 数组为 `segments`，不要转换成自定义 segment 类。
- [ ] 从 OneBot 事件传 `self_id` 到 Nanobot；`bot_id` 默认等于 `self_id`。
- [ ] 传 `raw_message` 作为调试字段。
- [ ] 从 `at` segment 生成 mentions；昵称用群成员缓存补。
- [ ] 从 `reply` segment 取得 `data.id`，再用本地消息缓存或 `get_msg` 补 `reply_to_sender_id/reply_to_content`。
- [ ] 保留旧 `message` 字段作为 plain text。
- [ ] 出站读取 `reply_meta`，发送：
  - `reply` segment
  - `at` segment
  - `text` segment
- [ ] 增加端到端测试：A @ B、B 回复 A、A @ bot、A 回复 bot、A 同时 @bot 和 @别人。

---

## Batch 3：WebUI 调试展示

## 任务 3A：群详情消息结构展示

- [ ] 群详情最近 ambient 消息展示：
  - `message_id`
  - `sender`
  - `plain_text`
  - `raw_message`
  - `segments`
  - `mentions`
  - `reply_to`
  - `is_at_bot`
  - `is_reply_to_bot`
  - `is_directed_to_other`
  - `self_id/bot_id/bot_name`
- [ ] JSON 使用可折叠详情，不要默认铺满列表。
- [ ] 旧日志没有结构化 meta 时正常显示。

## 任务 3B：TimingGate 记录展示 directed features

- [ ] TimingGate 记录页展示：
  - `directed_to_other`
  - `mentions_bot`
  - `mentions_others`
  - `reply_to_bot`
  - `reply_to_others`
  - `hard_rule`
  - `fallback_action`
- [ ] 统计 directed hard rule 命中次数。
- [ ] 群详情页能直接回答：为什么这次 no_reply？是否因为用户之间定向交流？

## 任务 3C：bot 回复展示 reply_meta

- [ ] 最近 bot 回复展示 `reply_meta`。
- [ ] 如果是 quote/mention，展示目标 message_id 和 user_id。

---

## 验证命令

Batch 1 定向验证：

```bash
python -m pytest tests/test_api.py -q
python -m pytest tests/test_timing_runtime.py -q
python -m pytest tests/test_timing_gate.py -q
python -m pytest tests/test_prompt_contract.py -q
python scripts/build_nanobot_prompt.py --check
```

Batch 2 定向验证：

```bash
python -m pytest tests/test_kt_framework.py::TestReplyContract -q
python -m pytest tests/test_api.py -q
python -m pytest tests/test_sticker_tool.py -q
```

WebUI 验证：

```bash
cd webui
npm run lint
npm run build
```

全量验证：

```bash
python -m pytest tests/ -v
```

---

## 回滚策略

- API 字段全部是新增字段，旧 QQbot 不受影响。
- `reply_meta` 是新增返回字段，旧 QQbot 可忽略。
- TimingGate directed hard rule 必须集中在单独 helper 或配置开关中，线上误杀过多时可先关闭 hard rule，保留 context 展示和 prompt bias。
- `ChatLog.meta_json` 新结构只追加字段，不迁移旧数据；所有读取路径必须容错。

## 完成标准

- 用户之间互相 @ / 引用时，Nanobot 默认不插嘴。
- 明确 @bot / 回复 bot / 提到 bot 名字时，仍能正常进入回复链路。
- bot 在 prompt/runtime context 中知道自己的 `self_id`、`bot_id`、`bot_name` 和 aliases。
- WebUI 和日志能解释一次群聊消息的指向性、TimingGate 判断和最终 action。
- 新旧 QQbot payload 都可用。
- prompt 构建校验通过，全量 pytest 通过。

# P2-3 QQ 出站渲染契约设计

日期：2026-06-18

## 背景

P2-2 已完成响应信封兼容双写，`/chat`、SSE done、`/group/message` 和定时任务 push 都能返回或消费统一的 `reply`、`messages`、`reply_meta` 和 `meta`。P2-2.5 也已完成 `client_meta` 边界校验，`platform`、`chat_type` 和 trace 关键字段已在入口归一化。

当前剩余缺口集中在 QQ 出站层：响应信封能承载正文和发送意图，但 QQbot 最终收到的仍是旧 `message` 字符串。文本、HTML、图片、表情、引用和 @ 的渲染规则分散在工具层、API 出口和 push 适配层，缺少一份稳定契约。

## 审计结论

本设计基于 2026-06-18 的只读审计和 3 个子 agent 的独立报告。

- `core/message_envelope.py` 已提供 `build_chat_response_envelope()`、`build_group_response_envelope()`、`sanitize_reply_meta()` 和 `envelope_to_message()`。当前 `messages` 只稳定支持 `text` / `html`，`envelope_to_message()` 会忽略非文本类型。
- `core/daily_digest.py` 的 `push_envelope_to_qq()` 当前通过 `envelope_to_message()` 派生旧 `message`，再调用 `push_to_qq(target_type, target_id, message)`。旧 helper 签名被现有调用方和测试依赖，不能在 P2-3 首版破坏。
- `/chat` 断连后台 push 和 route 手动任务 push 已走响应信封；`run_scheduled_tasks()` 也先构造信封再 push。
- `creatures/nanobot/prompts/skills/schedule_task/tool.py` 的 `action == "run"` 分支仍直接调用 `push_to_qq()`，绕过响应信封和未来 renderer。
- 生成图工具返回 `[generated_image:<id>]` 短 token；`core/generated_images.expand_generated_image_refs_in_content(..., allow_base64=False)` 在有公开 URL 时展开为 `[CQ:image,file=<url>]`，无公开 URL 时保留短 token 并记录 warning。QQ-facing 路径已普遍禁用 base64，但 `push_envelope_to_qq()` 自身尚未承担展开责任。
- 表情工具返回 `[sticker:<id>]` 短 token；`ReplyTool` 当前会在工具层把 `[sticker:<id>]` 展开为 `[CQ:image,file=...]` 并记录使用次数。模型也可能直接输出 `sticker_search` 的 `send_code`，即直接内联 CQ 码。
- `reply_meta` 已在私聊和群聊响应中透出，字段白名单为 `send_mode`、`reply_to_message_id`、`mentions`、`quote`、`at_sender`。push 路径目前不消费这些发送意图。
- HTML 报告仍作为完整 HTML 文本交给 QQbot 的 `html_to_pic` 能力处理，Nanobot 端不在 P2-3 首版渲染成图片。

## 目标

P2-3 的目标是建立「响应信封 → QQbot 旧 message 字符串」的集中渲染边界。

- 以响应信封里的 `messages` 作为 canonical 出站内容层，不新增顶层 `segments` 或 `out_segments`。
- 新增集中式 QQ 出站 renderer，由它统一消费 `envelope`、`messages`、`reply` 和 `reply_meta`。
- 首版仍向 QQbot 发送旧 HTTP payload：`target_type`、`target_id`、`message`。结构化渲染结果先作为 Nanobot 内部对象，方便测试和后续 adapter 升级。
- 保留旧兼容输入：`[sticker:<id>]`、`[generated_image:<id>]`、直接 OneBot CQ 码、`reply` 字符串、`messages` 中的 `text` / `html`。
- QQ-facing 路径继续 `allow_base64=False`，优先公开 URL，无公开 URL 时保留短 token，不把大 base64 放入 SSE 或 push。
- 收敛 schedule task 的 run 分支，让它不再绕过响应信封和 renderer。
- 同步工具 prompt 说明，把短 token 定义为兼容输入，由出口 renderer 负责最终 QQ 渲染。

## 非目标

- 不修改 `push_to_qq(target_type, target_id, message) -> bool` 的旧签名。
- 不要求 QQbot 立即支持新的结构化 HTTP payload。
- 不在 P2-3 首版删除模型直接输出 CQ 码的兼容能力。
- 不把 HTML 在 Nanobot 端提前渲染成图片。
- 不把 base64 图片重新带回 QQ-facing 路径。
- 不在本阶段重写入站 `GroupMessageRequest.segments`，P2-3 只处理出站。

## 推荐方案

采用「响应信封 `messages` + 集中 QQ renderer + 旧 payload 适配」方案。

新增模块：

```text
core/qq_outbound_renderer.py
```

模块职责：

- 把响应信封转换为内部 `QQOutboundRenderResult`。
- 输出旧 QQbot `message` 字符串，供 `push_to_qq()` 继续使用。
- 保留渲染 warnings，便于测试和日志记录。
- 统一处理 `[generated_image:<id>]`、`[sticker:<id>]` 和直接 CQ 码。
- 保持 HTML 文本完整，不做截断和二次转义。

建议内部结果：

```python
from dataclasses import dataclass, field


@dataclass(slots=True)
class QQOutboundRenderResult:
    message: str
    messages: list[dict[str, object]]
    reply_meta: dict[str, object]
    warnings: list[str] = field(default_factory=list)
```

首版 renderer 不需要把该对象直接暴露给外部 HTTP API。对外仍只发送旧 `message`，但测试可以断言 `messages`、`reply_meta` 和 `warnings`。

## 内容层约定

响应信封里的 `messages` 是出站内容的 canonical 数组。`reply` 是兼容 fallback，不再作为唯一权威正文。

首版支持的 message item：

| type | 字段 | QQ legacy 渲染 |
| --- | --- | --- |
| `text` | `text` | 原样追加文本，并解析兼容短 token。 |
| `html` | `text` | 原样追加 HTML 文本，交给 QQbot 的 `html_to_pic`。 |
| `image` | `url` 或 `generated_image_id` | 渲染为 `[CQ:image,file=<url>]`；无公开 URL 时保留短 token 并记录 warning。 |
| `at` | `qq` 或 `user_id` | 首版保留结构化信息，是否派生 CQ at 由任务拆分决定。 |
| `reply` | `message_id` | 首版保留结构化信息，是否派生 CQ reply 由任务拆分决定。 |

兼容规则：

- 如果 `messages` 非空，renderer 按数组顺序渲染。
- 如果 `messages` 为空且 `reply` 非空，renderer 从 `reply` 构造一个 `text` message 再渲染。
- 如果两者都为空，renderer 输出空 `message`，调用方跳过 push。
- 直接 CQ 码视为已经渲染好的 QQ legacy 内容，renderer 不拆解、不重写。

## 富媒体渲染规则

### 生成图

`[generated_image:<id>]` 和 `{"type": "image", "generated_image_id": "<id>"}` 使用同一策略：

1. 查询 `public_generated_image_url(image_id)`。
2. 有公开 URL 时渲染为 `[CQ:image,file=<url>]`。
3. 无公开 URL 时保留 `[generated_image:<id>]`，记录 warning。
4. 全程禁止 `base64://` fallback。

这一规则让 SSE done、后台 push、定时任务 push 和 schedule task run 分支拥有相同表现。

### 表情

`[sticker:<id>]` 继续作为兼容输入。P2-3 首版 renderer 需要能识别并展开它，避免新出口绕过 `ReplyTool` 时丢失表情渲染能力。

保守迁移策略：

- 第一阶段：renderer 兼容 `[sticker:<id>]`，`ReplyTool` 仍可继续提前展开，保证行为不倒退。
- 第二阶段：测试覆盖稳定后，再把表情展开责任从 `ReplyTool` 下沉到 renderer，保留使用次数记录。

### HTML

HTML message 不在 Nanobot 端转图片。renderer 只保证完整 HTML 文本能进入旧 `message` 字符串，不做 `format_group_reply_for_transport(..., max_chars=4000)` 这类可能破坏 HTML 的截断。

HTML 判断仍沿用响应信封现状：`type="html"` 优先；从旧 `reply` 派生时只识别 `<article`、`<!doctype`、`<html` 前缀。

### @ 与引用

`reply_meta` 是发送意图 overlay，不承载正文。

首版兼容目标：

- `reply_meta` 在 render result 中完整保留白名单字段。
- `mentions`、`at_sender`、`reply_to_message_id` 不在第一刀强行改写旧 `message`，避免改变 QQbot 现有发送行为。
- 如果后续 QQbot 支持结构化 payload，adapter 可直接消费 render result 的 `reply_meta`。

若要在旧 `message` 中派生 `[CQ:at,qq=...]` 或 `[CQ:reply,id=...]`，必须作为单独任务并配回归测试，避免重复 @ 或重复引用。

## 出口改造

P2-3 应把以下出口收敛到 renderer：

1. `core/daily_digest.push_envelope_to_qq()`：由 `envelope_to_message()` 改为 `render_qq_outbound_envelope()`。
2. `core/daily_digest.run_scheduled_tasks()`：继续先构造响应信封，再调用 `push_envelope_to_qq()`。
3. `api/routes.py` 手动任务 run 和 SSE 断连后台 push：保留响应信封路径，去掉散落的 generated image 展开责任，或仅保留不会破坏统一 renderer 的兼容调用。
4. `app/group_ingress/service.py` 群聊 continue 响应：输出信封仍兼容旧 `reply`，QQbot 适配层应以 renderer 为准。
5. `creatures/nanobot/prompts/skills/schedule_task/tool.py` 的 `action == "run"`：构造响应信封并调用 `push_envelope_to_qq()`，不再直连 `push_to_qq()`。

## Prompt 同步范围

修改出站渲染契约时必须同步以下工具说明，避免模型继续误解短 token 的职责边界：

- `prompts.v2.default/tools/reply/usage.md`
- `prompts.v2.default/tools/sticker_search/usage.md`
- `prompts.v2.default/tools/image_generation/usage.md`
- `data/prompts_v2/tools/reply/usage.md`
- `data/prompts_v2/tools/sticker_search/usage.md`
- `data/prompts_v2/tools/image_generation/usage.md`

推荐口径：

- `reply(content)` 可以包含自然语言、`[sticker:<id>]` 和 `[generated_image:<id>]`。
- 短 token 是模型与 Nanobot 之间的稳定引用，不要求模型手写 OneBot CQ 码。
- 出口 renderer 负责把短 token 渲染为 QQ 可发送内容。
- 直接 CQ 码仍被兼容，但不是推荐输出。

## 测试计划

新增和调整的测试应覆盖：

- `tests/test_qq_outbound_renderer.py`：text、html、混合文本与图片顺序、生成图公开 URL、生成图无公开 URL、表情 token、直接 CQ 码、空信封。
- `tests/test_push_envelope.py`：`push_envelope_to_qq()` 走 renderer，生成图 token 在 push 路径展开，空 message 跳过 push。
- `tests/test_schedule_task_tool.py`：schedule task `action="run"` 不直连 `push_to_qq()`，而是构造信封并走 `push_envelope_to_qq()`。
- `tests/test_api_push_envelope.py`：手动任务 run 与 SSE 断连后台 push 仍使用响应信封和 renderer。
- `tests/test_group_response_envelope.py`：群聊响应信封里的生成图和 HTML 不被旧文本截断逻辑破坏。
- `tests/test_message_envelope.py`：如果扩展 `messages` schema，需要证明 `image` 不再被出站 renderer 静默丢弃。
- Prompt 文档扫描：两个 prompt 根目录的工具 usage 文案保持一致。

## 风险与应对

- 模型可能直接输出 CQ 码：继续兼容，不在 P2-3 首版禁止。
- `sticker_search` 同时暴露 `reply_token` 和 `send_code`：prompt 明确推荐短 token，renderer 兼容两者。
- `NANOBOT_PUBLIC_BASE_URL` 缺失时图片短 token 会原样进入 QQ：记录 warning，避免发送大 base64；运维层面通过配置公开 URL 修复。
- HTML 渲染仍依赖 QQbot：Nanobot 只保证不截断、不转义、不破坏 HTML 文本。
- `reply_meta` 如果直接派生成 CQ at / reply，可能和 QQbot 现有引用逻辑重复：首版只保留结构化发送意图，不强行改写旧 `message`。
- prompt 模板有两个物理根目录：实现时必须同时更新 `prompts.v2.default` 和 `data/prompts_v2`，并用 diff 或扫描验证一致。

## 验收标准

- 新 renderer 成为 QQ push 的唯一信封渲染入口。
- `push_to_qq()` 旧签名保持不变。
- `[generated_image:<id>]` 在 push 路径有公开 URL 时渲染为 CQ image，无公开 URL 时不出现 `base64://`。
- `[sticker:<id>]` 在 renderer 层可被兼容处理。
- schedule task run 分支不再绕过响应信封。
- `/chat`、SSE done、`/group/message` 的旧字段继续兼容。
- prompt 工具说明同步短 token 与出口 renderer 职责。
- 定向测试和全量回归通过。

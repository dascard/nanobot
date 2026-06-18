# SSE 流式收敛设计

> 日期：2026-06-18
> 状态：设计阶段。本文承接 `2026-06-17-streaming-chat-design.md`，只覆盖已贯通真 token 流式后的剩余收敛工作。

## 背景

`/chat` 已支持 `stream` 参数，并已贯通 API、BridgePool、Bridge、KT `Message` 和 `BufferedOutput.write_stream()`。流式请求会返回 SSE，生产回复链路中的 provider token chunk 会进入 `stream_queue`，再由 `_stream_chat()` 下发给前端。`/chat-step` 也已经接入 `run_agent_step_stream()`，可以在 final-answer 阶段输出增量文本。

当前剩余问题集中在「事件契约收敛」而不是「是否能流式」。`/chat` 直接透传队列事件，chunk 粒度跟随 provider；多工具回合里，早期增量文本可能不是最终 `reply()` 工具合同的结果；`/chat` 和 `/chat-step` 的 delta 字段也不一致，前者使用 `text`，后者使用 `content`。这些差异会让前端和调用方难以判断哪些内容可预览、哪些内容是最终权威结果。

## 现状

### `/chat` SSE

- Framing 使用 `data: <json>\n\n`，不使用命名 `event:`。
- 已有事件通过 JSON `status` 区分：`heartbeat`、`progress`、`delta`、`error`、`done`。
- `delta` 事件来自 `BufferedOutput.write_stream()`，字段为 `text`。
- `done` 事件由 API 层构造，已经接入标准响应信封：`reply`、`messages`、`reply_meta`、`meta`，同时保留兼容字段 `answer`。
- API 层目前不合并连续 `delta`，也不补充 chunk 序号、来源或最终性标记。

### `/chat-step` SSE

- Framing 同样使用 `data: <json>\n\n`。
- final-answer 增量事件使用 `{"status": "delta", "content": "<chunk>"}`。
- 工具选择阶段会拼合流式 tool call，最终发送 `tool_call` 事件。
- 该接口服务于 step 调试和单步执行，不完全等价于生产 `/chat` 回复链路。

### Bridge / 输出层

- 主聊天链路中的 provider token 通过 KT OpenAI provider 进入 controller，再由 `BufferedOutput.write_stream()` 写入队列。
- `BufferedOutput.write_stream()` 仍会累积完整 buffer；队列只承载增量事件。
- `progress`、`tool_start` 等事件可能由活动回调写入队列，和 `delta` 共享同一个输出通道。
- `/chat` 当前创建无界 `asyncio.Queue()`，没有显式 backpressure 策略。

## 目标

1. 让 `/chat` SSE 在 API 层具备稳定的事件 adapter，输出经过规范化的事件对象，而不是无条件透传内部队列结构。
2. 为 `/chat` 连续 `delta` 增加小窗口合并能力，减少碎片事件，同时在 `progress`、`error`、`done` 或 runner 完成前正确 flush。
3. 明确展示语义：`delta` 只是草稿预览，`done.answer` 和 `done.reply` 是业务权威结果。
4. 为多工具回合准备显式收敛信号，允许 Bridge 在最终 response 确定后发送 replace/final 类事件。
5. 为 bounded queue 与 backpressure 留出接口边界，避免直接把策略散落在 provider 或 KT vendor 内。
6. 保持已有客户端兼容性：SSE framing 不变，`status` 字段不变，`done` 信封不降级。

## 非目标

- 不在本阶段改用命名 SSE event。
- 不在本阶段重写 KT controller、provider streaming 或 native tool calling 状态机。
- 不强行把 `/chat-step` 的 `delta.content` 迁移为 `/chat` 的 `delta.text`。
- 不改变 `/group/message` 的同步响应合同。
- 不展开增量事件中的图片 token，也不在增量 chunk 中发送 base64。
- 不把草稿 delta 持久化为聊天记录；持久化仍只使用最终 answer。

## 方案选择

### 方案 A：API 层 adapter + delta 合并

在 `_stream_chat()` 消费 `stream_queue` 时增加私有 helper，对内部事件做规范化，并合并当前可立即取得的连续 `delta`。遇到非 delta 事件、heartbeat、runner 完成或队列暂时为空时，先 flush 已合并文本，再继续处理其他事件。

优点是改动集中在 API 层，不碰 KT vendor 和 provider；测试可以直接构造队列事件验证输出顺序。缺点是它只能减少碎片和明确 API 输出形态，不能阻止多工具回合提前产生草稿文本。

### 方案 B：Bridge 输出层 final/replace 收敛事件

在 Bridge 确认最终 `response` 后，向 `stream_queue` 额外写入 `{"status": "final", "text": response, "replace": true}`。前端收到该事件后可以用最终文本替换草稿区域，随后仍以 `done` 信封完成业务状态收口。

优点是能明确解决「草稿 delta 与最终 `reply()` 不一致」的展示收敛问题。缺点是会增加一个新事件类型，需要先定义前端消费规则和与 `done` 的关系。

### 方案 C：输出层合并器 + bounded queue backpressure

把合并窗口和队列上限下沉到 `BufferedOutput` 或独立流式事件模块，让 provider chunk 在写队列前就被节流和合并。队列满时可以阻塞、丢弃草稿 delta 或合并旧 delta。

优点是更接近源头，能真正控制内存和事件速率。缺点是会影响 Bridge / KT 输出层，必须仔细处理 progress 顺序、断连后台任务和 provider 取消语义。

## 决策

P3-1 采用分阶段方案。第一阶段先落地方案 A，只在 `/chat` API 层增加 adapter 与连续 `delta` 合并；第二阶段再引入方案 B 的 final/replace 收敛事件；第三阶段处理方案 C 的 bounded queue 与 backpressure。

这样拆分的原因是：`/chat` API 层当前已经是 SSE 合同边界，先在这里规范化输出风险最小，也能立即降低 provider 小 chunk 对前端的影响。final/replace 与 backpressure 涉及 Bridge 输出语义和队列策略，适合在事件合同稳定后再推进。

## 事件契约

### 保持兼容的字段

`/chat` SSE 继续输出 JSON 对象，继续使用 `status` 区分事件类型：

```json
{"status": "delta", "text": "你好"}
{"status": "progress", "message": "正在调用工具"}
{"status": "heartbeat"}
{"status": "error", "message": "服务器繁忙，请稍后再试"}
{"status": "done", "answer": "最终回复", "reply": "最终回复", "messages": []}
```

客户端必须把 `done.answer` / `done.reply` 视为权威结果。`delta.text` 用于提前展示，不用于确认最终业务状态。

### 第一阶段新增的规范化规则

- `delta.text` 必须是字符串；空字符串不发送。
- 连续 delta 可以合并为一个 delta，但合并不能跨越 `progress`、`error`、`done` 或其他非 delta 事件。
- 当 runner 完成且队列即将 drain 时，必须先 flush 已合并的 delta，再发送 `done`。
- 未识别事件保留原 `status` 和字段，但不能吞掉已经合并的 delta。
- API 层生成的 `heartbeat` 不参与合并。

### 第二阶段新增的收敛事件

Bridge 层在最终 response 确定后可以发送：

```json
{"status": "final", "text": "最终回复", "replace": true, "source": "bridge"}
```

`final` 事件是展示收敛信号，不替代 `done` 信封。客户端收到 `final.replace == true` 时可以替换草稿展示区；收到后续 `done` 时仍以 `done` 的响应信封更新最终状态、富媒体消息和持久化相关 UI。

### 图片 token 与大 chunk

增量 `delta` 和 `final` 只承载文本，不展开 base64 图片。最终 `done.answer` 继续通过 `expand_generated_image_refs_in_content(..., allow_base64=False)` 转换 public URL 或保留短 token，避免单个 SSE data 过大。

## 阶段拆分

### 阶段 A：`/chat` API adapter 与 delta 合并

范围：

- 在 `api/routes.py` 的 `_stream_chat()` 内部增加私有 helper，或在 helper 增长后抽到独立模块。
- 合并连续 `delta.text`，并在非 delta 事件前 flush。
- 保持 `data: <json>\n\n` framing。
- 保持 `done` 信封结构不变。

验收：

- 连续 `delta` 会合并为更少的事件。
- `progress` 会打断合并，事件顺序保持可解释。
- runner 完成前的最后一段 delta 不会丢失。
- `done.answer` 仍是最终权威结果。

### 阶段 B：Bridge final/replace 收敛事件

范围：

- 在 `BufferedOutput` 或 Bridge 封装一个明确的 final/final-replace 发送方法。
- Bridge 在最终 response 确定后发送 `final`，再由 API 层透传或规范化。
- `done` 仍由 API 层构造响应信封。

验收：

- 多工具回合里，即使早期 delta 与最终 reply 不一致，客户端也能收到 replace 信号。
- `final` 不改变持久化结果，不影响 `done` 的响应信封。
- 非流式 `/chat` 不受影响。

### 阶段 C：bounded queue 与 backpressure

范围：

- 把 `/chat` 的 `stream_queue` 改为可配置上限。
- 明确队列满时策略：优先合并草稿 delta，不能丢失 `error`、`final`、`done` 这类控制事件。
- 评估 `progress` 事件是否允许压缩为最新状态。

验收：

- 高碎片 chunk 下内存不会随客户端读取速度无限增长。
- 控制事件不会被草稿 delta 淹没。
- 断连后台持久化和最终 push 行为保持不变。

### 阶段 D：统一流式事件说明

范围：

- 在消息字段标准或专门文档中记录 `/chat` 与 `/chat-step` 的事件差异。
- 明确 `delta.text` 与 `delta.content` 的使用场景。
- 给前端和 QQbot 出站 renderer 标注：SSE 增量是 Web 展示层能力，QQbot 推送仍消费最终信封。

验收：

- 调用方能从文档判断每类事件的权威性和展示方式。
- 后续统一字段时有清晰兼容策略。

## 测试策略

阶段 A 测试集中在 API 层：

- `tests/test_streaming_api.py` 增加连续 delta 合并测试。
- `tests/test_streaming_api.py` 增加 progress 打断合并测试。
- `tests/test_streaming_api.py` 增加 done 前 flush 测试。
- `tests/test_streaming_response_envelope.py` 保护 `done` 信封与 `answer` 权威语义。

阶段 B 测试集中在 Bridge / 输出层：

- `tests/test_streaming_output.py` 覆盖 final/replace 事件发送。
- `tests/test_streaming_bridge.py` 覆盖 Bridge 最终 response 后发送收敛事件。
- API 层增加透传或规范化 `final` 的 SSE 回归。

阶段 C 测试集中在队列策略：

- 用 bounded queue 模拟慢消费者，验证 delta 合并和控制事件保留。
- 覆盖断连后 runner 完成、后台持久化和 push 行为。

## 风险与缓解

- **草稿文本误用为最终结果。** 文档和事件命名必须持续强调 `done` 权威；阶段 B 用 `final.replace` 帮前端收敛展示。
- **合并破坏进度顺序。** 合并只跨连续 delta，非 delta 前强制 flush。
- **过早抽象导致改动扩散。** 阶段 A 先在 API 私有 helper 中实现；只有当 helper 被 `/chat-step` 或 Bridge 复用时再抽模块。
- **backpressure 丢关键事件。** 阶段 C 必须把文本草稿事件和控制事件分级，控制事件不可丢。
- **图片内容撑爆 SSE chunk。** 增量事件不展开图片 token；最终 `done` 继续禁用 base64 展开。

## 子 agent 分工建议

实现阶段可拆给多个子 agent 只读或独立写入：

- API owner：负责 `_stream_chat()` adapter、delta 合并和 `tests/test_streaming_api.py`。
- Bridge owner：负责 final/replace 事件设计落点、`nanobot_kt/output.py` 和 Bridge 回归。
- 文档 owner：负责消息字段标准、前端展示规则和 `/chat-step` 差异说明。

主线程保留接口决策、最终集成、验证和提交，避免多个 agent 同时修改 `api/routes.py`。

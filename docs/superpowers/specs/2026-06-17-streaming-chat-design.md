# 流式聊天重构设计

## 背景

`/chat` 请求已经有 `stream` 字段，`docs/message-field-standard.md` 也把它定义为是否启用 SSE 流式响应。但当前服务端只把它用于选择 `StreamingResponse`，SSE 中间阶段发送工具进度和心跳，最终仍在 `done` 事件一次性返回完整 `answer`。KT controller 内部已经以 `stream=True` 调用 LLM provider，`BufferedOutput.write_stream()` 也会累积 chunk，但没有把 token chunk 转发到 API 层。

本次重构的目标是让 `stream` 从 API 请求贯穿到 bridge 调用上下文，并在 SSE 模式下下发增量文本事件。非流式请求保持现有返回结构和持久化行为。

## 目标

- `ChatProxyRequest.stream` 显式传入 bridge 和 bridge metadata。
- KT 入站 `Message` 对象携带内部 `stream` 字段，用于标识该用户消息来自流式请求。
- `NanobotBridge.handle_message()` 与 `NanobotBridgePool.handle_message()` 支持 `stream` 参数。
- `BufferedOutput.write_stream()` 在启用 stream queue 时发送 `delta` 事件，SSE 可以提前收到 token。
- `_stream_chat()` 继续支持 `progress`、`heartbeat`、`done` 和错误事件。
- 最终持久化仍使用完整 `answer`，不持久化增量事件。
- SSE 输出禁止 base64 图片展开，继续使用短 token 或 public URL。

## 非目标

- 不在本阶段重写 KT 多工具回合状态机。
- 不直接绕过 KT controller 调 `clients.new_api_client.chat_completion_stream()`。
- 不改变 `/group/message` 的响应合同。
- 不把 `stream` 写入 OpenAI message dict 顶层，避免污染 LLM API wire format。

## 方案

### API 层

`proxy_chat()` 构建 `bridge_meta` 时加入 `stream: bool(req.stream)`。非流式 `_do_chat()` 和流式 runner 都把同一个 stream 布尔值传给 bridge。

`_stream_chat()` 对 queue 中的事件做轻量规范化：

- `delta`：增量文本，字段为 `text`。
- `progress`：工具或处理进度。
- `heartbeat`：服务端心跳。
- `done`：最终完整 answer。
- `error`：失败。

已有测试中只检查 `progress` 与 `done`，保持兼容。

### Bridge 层

`NanobotBridge.handle_message()` 增加 `stream: bool = False`。它会把该值写入局部 `meta["stream"]`，并仅当 `stream_queue` 存在且 `stream` 为真时启用输出模块的 token delta 转发。这样调用方传了队列但未开启 stream 时仍只得到进度事件，避免改变非流式或内部测试行为。

`NanobotBridgePool.handle_message()` 同步增加 `stream` 参数并透传给 child bridge。

### Message 层

KT `Message` 增加内部 `stream: bool = False` 字段。该字段只存在于内存对象上，`Message.to_dict()` 不输出它，`Message.from_dict()` 遇到 `stream` 时只恢复到内部字段，不放进 `extra_fields`，避免下游 OpenAI 兼容 API 收到非标准顶层字段。

Bridge 创建用户事件时把 `stream` 写入 `TriggerEvent.context`。Controller 在把本轮事件合并成用户消息时，如果任一事件带有 `stream=True`，则通过 `conversation.append("user", ..., stream=True)` 让最终入站 `Message` 携带流式标记。

### 输出层

`BufferedOutput.write_stream()` 仍累积 chunk 到 `_buffer`，并在 stream queue 存在时向队列写入：

```json
{"status": "delta", "text": "<chunk>"}
```

空 chunk 不发送。队列写入使用受控后台任务集合跟踪，避免 fire-and-forget task 丢失或异常未消费。已有 `on_activity()` 中的进度事件也复用同一个 helper。

### 事件粒度与安全

第一阶段直接按 KT provider 的 chunk 粒度转发，不做二次合并。最终 `done.answer` 仍会经过 `expand_generated_image_refs_in_content(..., allow_base64=False)`，避免 SSE 单 chunk 过大。增量事件只来自 LLM 文本 chunk，不展开图片 token。

### 错误和断连

现有 `_stream_chat()` 的断连后台持久化逻辑保留。客户端断连后 runner 继续完成并推送最终结果；增量事件不会再发送给已断开的客户端。

## 测试策略

- API 测试：stream 请求调用 bridge 时传入 `stream=True`，SSE body 包含 `delta` 与最终 `done`。
- Bridge 测试：`NanobotBridge.handle_message(stream=True, stream_queue=queue)` 会启用输出队列，`stream=False` 不启用 token delta。
- Message 测试：`Message(stream=True)` 在对象上保留标记，但 `to_dict()` 不输出 `stream`。
- Controller 测试：`TriggerEvent.context["stream"]` 会传到用户 `Message.stream`，同时 LLM wire messages 不包含 `stream`。
- Output 测试：`BufferedOutput.write_stream()` 在启用 queue 后发送 `delta`，并且 `on_activity()` 的进度事件仍可发送。
- 回归测试：现有 `progress + done` SSE 测试不破坏；非流式 `/chat` 不受影响。

## 风险

- KT parser 在工具调用格式中也会产生中间文本 chunk，可能包含未完成的结构化标记。第一阶段只把当前已有输出流暴露给 Web SSE；若前端直接展示，需要容忍短暂的未完成文本。
- 多工具回合下最终 `reply()` 工具可能覆盖模型原始文本。第一阶段仍以最终 `done.answer` 为权威，增量只作为提前反馈。
- 如果上游 provider chunk 非常碎，SSE 事件数会增加。后续可增加小窗口合并，但本阶段先不引入复杂 backpressure。

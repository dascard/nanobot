# 普通 API Chat Streaming Result 拆分设计

日期：2026-06-23

## 背景

`api/routes.py` 当前为 P3 超大文件队列中的唯一剩余核心文件，行数为 1227。`/chat` 仍保留在父模块，前序小刀已经拆出 request contract、response contract、persistence、runtime facade、guardrail facade、streaming helper、push envelope、persona context、media precache 和 user block rules。

当前 `_stream_chat()` 内还有一段高耦合收尾逻辑：`_persist_stream_result_after_runner_done()`。它负责等待 stream runner 完成、可选 drain bounded queue、处理 Prompt V2 audit 失败、释放私聊 buffer、持久化 assistant turn，并在客户端断连时通过 QQ push envelope 推送最终结果。这段逻辑不属于 SSE 事件泵本身，但占用父模块大量上下文。

本阶段只拆这一段 streaming result 收尾器，不迁移完整 `_stream_chat()`，也不迁移 `/chat` route。

## 只读审查结论

本阶段使用两个只读 explorer 并行审查：

- 边界审查结论：推荐先抽出 streaming 断连 / runner 完成后的结果收尾器；不建议同刀迁移完整 `_stream_chat()` 或 `proxy_chat()`。
- 测试审查结论：现有测试已经覆盖 SSE delta 合并、done 权威语义、错误脱敏、bounded queue、断连后台 push、runner done 后持久化和 Prompt V2 audit no-send；下一刀需要补新模块源码约束、callback 注入契约和相邻 streaming 回归。

## 方案选择

### 方案 A：拆 streaming result 收尾器（推荐）

新建 `api/chat_streaming_result.py`，迁移 `_persist_stream_result_after_runner_done()` 的主体逻辑。父模块仍创建 runner task、stream queue、SSE event loop 和 `StreamingResponse`，只把 runner result 收尾委托给新模块。

优点：

- 不改变 SSE 主循环、heartbeat、delta flush、done event 或 route 注册。
- 保留 `api.routes` 作为全部 monkeypatch facade。
- 行数收益明显，且边界比完整迁移 `_stream_chat()` 更小。
- 现有断连测试能直接作为行为回归。

风险：

- 依赖项较多，必须用 context + callbacks 显式注入，避免新模块反向导入父模块。
- 必须保留 request DB 和后台 `UnitOfWork` 新 DB 的区别。

### 方案 B：迁移完整 `_stream_chat()`

把 runner、SSE event loop、heartbeat、delta flush、done envelope、断连后台收尾整体迁移到新模块。

优点是行数收益更大。缺点是一次性触碰 SSE 主循环、response envelope、background task、DB、push 和 private buffer，patch point 风险高。

结论：暂缓。方案 A 完成后再评估。

### 方案 C：拆私聊 pre-bridge 决策 / guardrail / buffer flow

迁移 PrivateTimingGate、casual/no_reply、guardrail、私聊缓冲 owner/follower 和 silent guardrail 路径。

优点是业务边界清晰。缺点是 `_private_buffers`、`api.routes.asyncio.sleep`、`api.routes._time.time`、`PRIVATE_BUFFER_*` 常量和多段并发测试强耦合。

结论：暂缓。该边界适合独立设计。

## 目标

- 新增 `api/chat_streaming_result.py`。
- 从 `api/routes.py` 迁移 `_persist_stream_result_after_runner_done()` 主体逻辑。
- 父模块 `_stream_chat()` 继续负责 runner task、stream queue、SSE 循环、done event、error event 和 `StreamingResponse`。
- 父模块继续保留 `/chat` route、`proxy_chat.__module__ == "api.routes"`、`CHAT_STREAM_QUEUE_MAXSIZE`、`_normalize_chat_stream_event()`、`_chat_sse_data()`、`_stream_error_event()` 和 `_chat_response_payload()`。
- 新模块通过 context + callbacks 消费父模块 facade，不导入 `api.routes`。
- 不新增 `asyncio.run()`、`run_awaitable_sync` 或同步函数包装 awaitable。

## 非目标

- 不迁移 `proxy_chat()` 或 `/api/v1/chat` route 注册。
- 不迁移完整 `_stream_chat()`。
- 不迁移 SSE heartbeat / queue wait / delta coalescing / done envelope 生成逻辑。
- 不迁移 `CHAT_STREAM_QUEUE_MAXSIZE`。
- 不迁移 `get_bridge()`、`get_guardrail()`、PrivateTimingGate、guardrail、私聊缓冲状态机或 image precache。
- 不修改 Prompt Runtime 模板、`enriched_query`、conversation 结构、工具输出契约、message envelope、push envelope 或 response envelope。
- 不修改 `ChatProxyRequest.stream`、Bridge `stream` metadata 或 KT `Message.stream` 语义。
- 不改 WebUI / JS。

## 新模块设计

新文件：`api/chat_streaming_result.py`

### `ChatStreamResultCallbacks`

职责：显式注入父模块可 patch facade，避免新模块导入父模块。

建议接口：

```python
@dataclass(frozen=True)
class ChatStreamResultCallbacks:
    drain_stream_queue_until_task_done: Callable[..., Awaitable[None]]
    pop_bridge_reply_meta: Callable[[Any, str], dict[str, Any] | None]
    private_prompt_audit_failure_meta: Callable[[], dict[str, Any]]
    finalize_private_buffer: Callable[..., Awaitable[None]]
    persist_chat_turn: Callable[..., int]
    expand_chat_transport_answer: Callable[[str], str]
    build_chat_push_envelope: Callable[..., Any]
    push_envelope_to_qq: Callable[[str, str, dict[str, Any]], Awaitable[bool]]
```

约束：

- `persist_chat_turn` 必须由父模块在请求内传入，不能在 import 时绑定。
- `push_envelope_to_qq` 由父模块构造 callbacks 时 late import 或注入，方便测试 patch。
- callback 类型使用 `Any` 保持对现有 request / envelope 对象的低耦合。

### `ChatStreamResultContext`

职责：携带一次 stream 收尾所需的运行时状态。

建议接口：

```python
@dataclass(frozen=True)
class ChatStreamResultContext:
    req: Any
    persist_req: Any
    bridge: Any
    result_holder: MutableMapping[str, Any]
    runner_task: asyncio.Task[Any]
    stream_queue: asyncio.Queue[Any]
    platform: str
    bridge_meta: dict[str, Any]
    guardrail_status: str | None
    private_timing_meta: dict[str, Any] | None
    empty_assistant_placeholder: str
    callbacks: ChatStreamResultCallbacks
```

约束：

- `result_holder["answer"]` 是最终业务 answer 的来源。
- `result_holder["error"]` 只用于日志和 placeholder 持久化，不向客户端或 push 泄漏。
- `runner_task` 必须由父模块创建，便于父模块继续控制 SSE 生命周期。

### `persist_stream_result_after_runner_done()`

职责：在 stream runner 完成后执行一致的落库、buffer finalize 和可选 push。

建议接口：

```python
async def persist_stream_result_after_runner_done(
    context: ChatStreamResultContext,
    *,
    push: bool,
    persist_db: Any | None = None,
    drain_stream: bool = False,
) -> None:
    ...
```

行为契约：

- `drain_stream=True` 时创建 drain task，调用 `callbacks.drain_stream_queue_until_task_done(context.stream_queue, context.runner_task)`。
- 始终 `await context.runner_task`，随后等待 drain task。
- runner 失败时：
  - final answer 使用 `context.empty_assistant_placeholder`。
  - 不 push。
  - 仍调用 `callbacks.finalize_private_buffer(req.user_id, placeholder)`。
  - 仍持久化 assistant turn。
- runner 成功且 `pop_bridge_reply_meta(...).get("_agent_result") == "prompt_v2_audit_failed"` 时：
  - final answer 使用 placeholder。
  - assistant meta 使用 `callbacks.private_prompt_audit_failure_meta()`。
  - `assistant_processed=1`。
  - 不 push。
- runner 成功且 answer 非空时：
  - final answer 使用 `str(result_holder.get("answer") or "")`。
  - `push=True` 时允许 push。
- 持久化：
  - `persist_db is not None` 时使用请求 db 写入。
  - `persist_db is None` 时内部使用 `core.uow.UnitOfWork()` 开新 session。
  - 不吞掉 `UnitOfWork` session 未打开错误。
- push：
  - push 前调用 `callbacks.expand_chat_transport_answer(final_answer)`，异常时保留原 answer。
  - 使用 `callbacks.build_chat_push_envelope(...)` 构造 envelope。
  - 使用 `callbacks.push_envelope_to_qq(target_type, target_id, envelope)` 发送。
  - push 成功 / 失败只写日志，不改变持久化结果。
- finally：
  - 若 drain task 未完成，cancel 后 `await asyncio.gather(..., return_exceptions=True)`。

## 父模块接入设计

`api/routes.py` 保留 `_stream_chat()`，只做薄接入：

- 导入 `chat_streaming_result`。
- 在 `_stream_chat()` 内构造 `ChatStreamResultCallbacks` 和 `ChatStreamResultContext`。
- 把原闭包 `_persist_stream_result_after_runner_done()` 替换为一个父模块 wrapper，内部委托 `chat_streaming_result.persist_stream_result_after_runner_done(...)`。
- `finally` 分支保持原调用形态：
  - `runner_task.done()` 时 `await _persist_stream_result_after_runner_done(push=False, persist_db=db)`。
  - runner 未完成时 `background_tasks.add_task(_persist_stream_result_after_runner_done, push=True, persist_db=None, drain_stream=True)`。
- 父模块 wrapper 名称可继续保留在 `_stream_chat()` 局部，降低 diff。

父模块继续保留：

- `stream_queue = asyncio.Queue(maxsize=CHAT_STREAM_QUEUE_MAXSIZE)`。
- `_drain_stream_queue_until_runner_done()` 小闭包。
- `_normalize_chat_stream_event()`、`_chat_sse_data()`、`_stream_error_event()`。
- SSE 主循环、pending delta flush、error event、done event、evolution background task。
- `StreamingResponse(_stream_chat(), media_type="text/event-stream")`。

## 测试设计

新增：`tests/test_api_chat_streaming_result_split.py`

覆盖：

- 新模块不导入 `api.routes`，不包含 `asyncio.run` 或 `run_awaitable_sync`。
- 新模块不直接调用 `get_bridge()`、`get_guardrail()`、`_persist_chat_turn()` 或 `push_envelope_to_qq()`，这些必须通过 context / callbacks 注入。
- `persist_stream_result_after_runner_done()` 成功路径：
  - 等待 runner。
  - 使用 result holder answer。
  - finalize private buffer。
  - 使用传入 `persist_db`。
  - `push=False` 时不 push。
- Prompt V2 audit 失败路径：
  - 使用 placeholder。
  - 写入 private prompt audit meta。
  - `assistant_processed=1`。
  - 不 push。
- 断连后台 push 路径：
  - `persist_db=None` 时使用 `UnitOfWork`。
  - push 前展开 transport answer。
  - 使用 push envelope。
  - bounded queue drain task 会被调用并在 finally 中清理。

修改 split 扫描测试：

- `tests/test_api_group_message_routes_split.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_api_history_log_routes_split.py`
- `tests/test_api_sticker_media_routes_split.py`

将 `api/chat_streaming_result.py` 加入 chat split module 扫描清单。

保留并运行相邻回归：

- `tests/test_api.py::test_stream_disconnect_background_push_uses_result_holder`
- `tests/test_api.py::test_stream_disconnect_drains_bounded_queue_for_background_runner`
- `tests/test_api.py::test_stream_disconnect_after_runner_done_persists_result_holder`
- `tests/test_api.py::test_stream_disconnect_prompt_v2_audit_failure_is_no_send`
- `tests/test_streaming_api.py`
- `tests/test_streaming_response_envelope.py`
- `tests/test_asyncio_run_policy.py`

## 风险与控制

### 父模块 patch point

`api.routes._persist_chat_turn`、`_finalize_private_buffer`、`_pop_bridge_reply_meta`、`_private_prompt_audit_failure_meta`、`_expand_chat_transport_answer` 和 `_build_chat_push_envelope` 继续由父模块传入 callbacks。新模块不在 import 时绑定这些对象。

### request DB 与后台 DB

同步完成分支继续使用请求 `db`；客户端断连后台完成分支继续使用新 `UnitOfWork` session。现有 `test_stream_disconnect_background_push_uses_result_holder` 会继续保护这个区别。

### Prompt V2 audit no-send

Prompt V2 audit 失败路径不 push，assistant turn 继续以 no-context meta 持久化。现有断连 audit 回归和新增单测共同保护。

### bounded queue backpressure

断连后台 drain 是防止 runner 卡死的关键。新模块只接管收尾器内部创建 / cleanup drain task，底层 drain 仍复用 `api.chat_streaming_helpers.drain_stream_queue_until_task_done()`。

### SSE done 权威语义

本阶段不移动 done event 构造。`done.answer`、`done.reply` 和 `done.messages` 仍由父模块使用 `bridge.handle_message()` 返回值生成。

## 验收标准

- `api/chat_streaming_result.py` 存在，且不导入 `api.routes`。
- `/api/v1/chat` route 仍由 `api.routes.proxy_chat` 提供。
- `api/routes.py` 行数下降。
- 断连后台 push、runner done 后持久化、Prompt V2 audit no-send、bounded queue drain 行为保持不变。
- `tests/test_api_chat_streaming_result_split.py` 通过。
- streaming 相邻回归通过。
- `tests/test_asyncio_run_policy.py` 通过。
- 全量测试通过。

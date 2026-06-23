# 普通 API Chat SSE Loop 拆分设计

日期：2026-06-23

## 背景

`docs/todo.md` 的 P3 超大文件拆分队列当前只剩普通 `api/routes.py`，文件为 1163 行。前序小刀已经把 `/chat` 周边的 request contract、response contract、persistence、runtime facade、guardrail facade、streaming helpers、private buffer、push envelope、persona context、media precache、user block rules 和 streaming result 收尾器拆出。

上一刀拆出 `api/chat_streaming_result.py` 后，`_stream_chat()` 内剩余最大的一段低业务耦合逻辑是 SSE queue pump：等待 `stream_queue` / `done`、发送 heartbeat、收集当前可用队列事件、合并连续 delta、runner 完成后 drain 队列尾部并 flush pending delta。这段逻辑只处理 SSE 事件泵，不应直接知道 DB、push、private buffer、Prompt V2 audit、evolution 或 FastAPI route。

本阶段只拆纯 SSE loop，不迁移完整 `_stream_chat()`。

## 只读审查结论

本阶段使用只读 explorer 审计 `_stream_chat()`。结论是完整 `_stream_chat()` 仍同时耦合 runner、请求 DB、私聊 buffer、Prompt V2 audit、断连后台、done envelope、evolution trigger 和 `BackgroundTasks`，一刀迁移风险偏高。更稳妥的边界是先拆纯 SSE queue pump，把业务收尾留在 `api.routes`。

## 方案选择

### 方案 A：新增 `api/chat_sse_loop.py`（推荐）

新模块只暴露 `iter_chat_stream_events()`，输入 `stream_queue`、`done`、`heartbeat_interval` 和 `normalize_event` callback，输出规范化后的事件 dict。父模块继续把事件 dict 交给 `_chat_sse_data()` 序列化成 SSE 字符串。

优点：

- 不触碰 DB、push、private buffer、Prompt V2 audit、done envelope 或 evolution trigger。
- 保留 `CHAT_STREAM_QUEUE_MAXSIZE` 在父模块读取并创建 queue。
- 保留 `StreamingResponse(_stream_chat(), ...)` 在父模块。
- 保留 `_normalize_chat_stream_event()` 和 `_chat_sse_data()` 父模块 facade。
- 新模块可以用纯 asyncio 队列单测覆盖，不需要启动 FastAPI client。

风险：

- 需要精确保持现有 loop 的 task cancel / gather 语义，避免悬挂 task。
- 需要保持 runner 完成后不会等 heartbeat 超时。
- 需要保持尾部 delta 在 done 前 flush。

### 方案 B：扩展 `api/chat_streaming_helpers.py`

把 iterator 函数加到现有 streaming helpers 文件中。

优点是文件数更少。缺点是 `chat_streaming_helpers.py` 已承载 coalescer、ready drain 和断连 drain，再继续扩展会让职责变得混杂。SSE event loop 是独立生命周期 helper，单独文件更清楚。

结论：不采用。

### 方案 C：迁移完整 `_stream_chat()`

把 runner、SSE event loop、在线完成分支、断连 `finally` 和 result 收尾调度整体迁移到 `api/chat_streaming_runtime.py`。

优点是行数收益最大。缺点是会同时触碰业务收尾和连接生命周期，容易破坏 request DB / 后台 `UnitOfWork` 区分、Prompt V2 audit no-send、done envelope 权威语义、bounded queue drain 和 existing monkeypatch。

结论：暂缓。

## 目标

- 新增 `api/chat_sse_loop.py`。
- 迁移 `_stream_chat()` 中纯 SSE queue wait / heartbeat / queue drain / pending delta flush 逻辑。
- 父模块 `_stream_chat()` 继续创建 runner、`done`、`stream_queue`、`StreamEventCoalescer` 和 result context。
- 父模块继续负责 error path、Prompt V2 audit path、success done payload、evolution trigger、断连后台收尾和 `StreamingResponse`。
- 新模块不导入 `api.routes`、FastAPI、DB、`core.daily_digest` 或业务模块。
- 不新增 `asyncio.run()`、`run_awaitable_sync` 或同步函数包装 awaitable。

## 非目标

- 不迁移 `/chat` route 或 `proxy_chat()`。
- 不迁移完整 `_stream_chat()`。
- 不迁移 `StreamingResponse`。
- 不迁移 `CHAT_STREAM_QUEUE_MAXSIZE` 常量或 queue 创建位置。
- 不迁移 `get_bridge()` 调用和 runner 创建。
- 不迁移 `_persist_stream_result_after_runner_done()` 调度。
- 不迁移 `_chat_response_payload()`、`_stream_error_event()`、`_persist_chat_turn()`、`_finalize_private_buffer()`、`_pop_bridge_reply_meta()` 或 `_expand_chat_transport_answer()`。
- 不修改 Prompt Runtime 模板、`enriched_query`、conversation 结构、工具输出契约、message envelope、push envelope 或 response envelope。
- 不处理 WebUI / JS。

## 新模块设计

新文件：`api/chat_sse_loop.py`

### `ChatSseLoopCallbacks`

职责：显式注入事件规范化 callback，避免新模块反向导入父模块或 response contract。

接口：

```python
@dataclass(frozen=True)
class ChatSseLoopCallbacks:
    normalize_event: Callable[[Any], dict[str, Any] | None]
```

### `iter_chat_stream_events()`

职责：把 `stream_queue` 与 `done` event 转换为一串规范化 event dict。

接口：

```python
async def iter_chat_stream_events(
    stream_queue: asyncio.Queue[Any],
    done: asyncio.Event,
    *,
    heartbeat_interval: float,
    coalescer: StreamEventCoalescer,
    callbacks: ChatSseLoopCallbacks,
) -> AsyncIterator[dict[str, Any]]:
    ...
```

行为契约：

- 当 `done.is_set()` 且 `stream_queue.empty()` 时结束。
- 每轮同时等待 `stream_queue.get()` 和 `done.wait()`。
- 等待超过 `heartbeat_interval` 时输出 `{"status": "heartbeat"}`。
- `stream_queue.get()` 完成时，调用 `collect_ready_stream_events()`，并逐个 yield 规范化事件。
- `done.wait()` 完成但没有新事件时立即跳出，不再等待 heartbeat。
- 每轮都取消未完成 task，并 `await asyncio.gather(..., return_exceptions=True)`。
- 主循环退出后 `await asyncio.sleep(0)`，允许 runner 最后的 queue put 进入当前 loop turn。
- drain `stream_queue.get_nowait()` 的尾部事件，继续通过 `collect_ready_stream_events()` 输出。
- 最后调用 `coalescer.flush()`，若存在 pending delta，则 yield。

## 父模块接入设计

`api/routes.py` 保留 `_stream_chat()`，只把 loop 中的事件泵替换为：

```python
sse_callbacks = chat_sse_loop.ChatSseLoopCallbacks(
    normalize_event=_normalize_chat_stream_event,
)
async for event in chat_sse_loop.iter_chat_stream_events(
    stream_queue,
    done,
    heartbeat_interval=heartbeat_interval,
    coalescer=coalescer,
    callbacks=sse_callbacks,
):
    yield _chat_sse_data(event)
```

父模块继续保留：

- `stream_queue = asyncio.Queue(maxsize=CHAT_STREAM_QUEUE_MAXSIZE)`。
- `coalescer = chat_streaming_helpers.StreamEventCoalescer()`。
- `_chat_sse_data()` 序列化。
- error / audit / success done 分支。
- `finally` 中断连后台收尾调度。

## 测试设计

新增：`tests/test_api_chat_sse_loop_split.py`

覆盖：

- 新模块源码约束：
  - 不导入 `api.routes`。
  - 不导入 FastAPI / `StreamingResponse`。
  - 不调用 `get_bridge()` / `get_guardrail()`。
  - 不出现 `_persist_chat_turn(`、`push_envelope_to_qq`、`asyncio.run` 或 `run_awaitable_sync`。
- `iter_chat_stream_events()` heartbeat：
  - `done` 未完成且 queue 空，短 heartbeat 后输出 `{"status": "heartbeat"}`。
- `iter_chat_stream_events()` runner done 快速结束：
  - `done.set()` 且 queue 空时迭代立即结束，不等 heartbeat。
- `iter_chat_stream_events()` delta / progress 顺序：
  - 连续 delta 后遇到 progress，输出合并 delta 再输出 progress。
- `iter_chat_stream_events()` done 后尾部 flush：
  - runner done 后 queue 中剩余 delta 被 drain 并 flush。
- 父模块接入：
  - `api/routes.py` 导入 `chat_sse_loop`。
  - `_stream_chat()` 内不再手写 `asyncio.wait({get_task, done_task}, ...)` 主循环。
  - `StreamingResponse(_stream_chat(), media_type="text/event-stream")` 仍在父模块。

同步更新 chat split module 扫描清单：

- `tests/test_api_group_message_routes_split.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_api_history_log_routes_split.py`
- `tests/test_api_sticker_media_routes_split.py`

相邻回归：

- `tests/test_streaming_api.py`
- `tests/test_streaming_response_envelope.py`
- `tests/test_api_chat_streaming_helpers_split.py`
- `tests/test_api_chat_streaming_result_split.py`
- `tests/test_chat_response_envelope.py`
- `tests/test_asyncio_run_policy.py`

断连路径回归：

- `tests/test_api.py::test_stream_disconnect_background_push_uses_result_holder`
- `tests/test_api.py::test_stream_disconnect_drains_bounded_queue_for_background_runner`
- `tests/test_api.py::test_stream_disconnect_after_runner_done_persists_result_holder`
- `tests/test_api.py::test_stream_disconnect_prompt_v2_audit_failure_is_no_send`

## 验收标准

- `api/chat_sse_loop.py` 存在，且不导入父模块或 FastAPI。
- 新模块定向测试通过。
- 四个 chat split module 扫描测试通过。
- `/chat` streaming 行为回归通过：
  - delta 合并。
  - progress 前 flush。
  - final replace。
  - done 不等待 heartbeat。
  - error 脱敏。
  - bounded queue maxsize 仍受 `api.routes.CHAT_STREAM_QUEUE_MAXSIZE` monkeypatch 影响。
  - done envelope 和 reply meta 语义不变。
- 断连后台路径语义不变。
- `api/routes.py` 行数继续下降。
- 全量测试通过。

## 风险与缓解

- **悬挂 task 风险**：新模块必须保留每轮 cancel pending task 后 gather 的 cleanup 语义；单测覆盖 heartbeat 和 done 快速结束。
- **尾部 delta 丢失风险**：新模块必须在主循环退出后 drain queue 并 flush coalescer；单测覆盖 done 后尾部 delta。
- **父模块 patch point 风险**：新模块只接收 `normalize_event` callback，父模块继续负责 `_chat_sse_data()`、queue 创建和业务收尾。
- **边界扩张风险**：本阶段不迁移 error / audit / done / disconnect finally 分支；任何业务收尾迁移必须另开设计。

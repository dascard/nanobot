# 普通 API Chat Streaming Helper 拆分设计

日期：2026-06-23

## 背景

`api/routes.py` 当前仍是 P3 超大文件队列中的唯一核心文件，行数为 1449。`/chat` 是剩余主要复杂边界，前几刀已经拆出 content helper、response contract、persistence、request contract、runtime facade 和 guardrail facade。本阶段继续小步拆分，但不迁移 `/chat` 路由本体。

`_stream_chat()` 当前承担两类职责：

- 纯 streaming helper：连续 delta 合并、非 delta 前 flush、runner 完成后 drain bounded queue。
- 强业务副作用：等待 Bridge runner、断连后台落库、Prompt audit 失败处理、私聊 buffer finalize、QQ push envelope、evolution 触发和 done envelope。

本设计只拆第一类纯 helper，保留第二类副作用在父模块。

## 方案选择

### 方案 A：拆私聊缓冲基础件

优点是可以继续降低父模块状态密度。缺点是 `_private_buffers`、`_private_lock`、fake clock、follower wait、owner cancel、guardrail 预跑和 bridge cancel 清理都和父模块 monkeypatch 强耦合，测试需要先补 generation / timeout 契约。

结论：暂缓，适合作为后续独立设计。

### 方案 B：整体迁移 `_stream_chat()`

优点是行数收益更大。缺点是需要把 `req`、`db`、`background_tasks`、`bridge`、`persist_req`、`bridge_meta`、`private_timing_meta`、`guardrail_status`、父模块 wrapper 和 late import push 依赖全部打包成 context，patch point 风险高。

结论：暂缓，不作为本阶段边界。

### 方案 C：拆 streaming 纯 helper（推荐）

只新增 `api/chat_streaming_helpers.py`，迁移 delta coalescing 和 bounded queue drain。父模块仍创建 `stream_queue`，仍读取 `CHAT_STREAM_QUEUE_MAXSIZE`，仍负责 `_stream_chat()` 的生命周期、副作用和 `StreamingResponse`。

推荐原因：边界最小，测试可直接红绿验证，且不改变 `/chat` 的 HTTP、DB、push 或 Prompt Runtime 契约。

## 目标

- 新增 `api/chat_streaming_helpers.py`，不导入 `api.routes`。
- 将 `_stream_chat()` 内的 pending delta 合并逻辑抽成可单测 helper。
- 将 `_drain_stream_queue_until_runner_done()` 抽成可单测 async helper。
- 父模块保留 `_stream_chat()`、`StreamingResponse`、`CHAT_STREAM_QUEUE_MAXSIZE`、`SAFE_STREAM_ERROR_MESSAGE`、所有 persistence / push / private buffer finalizer 和 route patch point。
- 保持现有 SSE 事件顺序和 done 权威语义。

## 非目标

- 不迁移 `proxy_chat()` 或 `/chat` 路由注册。
- 不迁移 `_persist_stream_result_after_runner_done()`。
- 不迁移断连后台 push envelope 逻辑。
- 不迁移 `_finalize_private_buffer()`、`_private_buffers`、`_private_lock` 或私聊缓冲状态机。
- 不迁移 `get_bridge()`、`get_guardrail()`、`_persist_chat_turn()`、`_chat_response_payload()` 等父模块 wrapper。
- 不修改 Prompt Runtime 模板、`enriched_query`、conversation 结构、工具输出契约或 message envelope。
- 不新增 `asyncio.run()`、`run_awaitable_sync` 或同步函数包装 awaitable。

## 新模块设计

新文件：`api/chat_streaming_helpers.py`

### `StreamEventCoalescer`

职责：维护 pending delta 文本，输出可发送的规范化 event。

接口：

```python
class StreamEventCoalescer:
    def feed(self, event: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        ...

    def flush(self) -> dict[str, str] | None:
        ...
```

契约：

- `event is None`：不输出。
- `event["status"] == "delta"`：累积 `text`，暂不输出。
- 非 delta event：先输出合并后的 pending delta，再输出该 event。
- `flush()`：输出最后未发送的 delta，随后清空 pending。
- 不处理 SSE 序列化，父模块继续调用 `_chat_sse_data()`。
- 不调用 `_normalize_chat_stream_event()`，父模块以 hook 方式传入规范化函数，保留 wrapper patch point。

### `collect_ready_stream_events()`

职责：从一个已取出的 raw event 开始，合并当前 queue 中立即可读的事件，返回规范化 event 列表。

接口：

```python
def collect_ready_stream_events(
    raw_event: Any,
    stream_queue: asyncio.Queue[Any],
    *,
    normalize_event: Callable[[Any], dict[str, Any] | None],
    coalescer: StreamEventCoalescer,
) -> list[dict[str, Any]]:
    ...
```

契约：

- 首个 raw event 会先经过 `normalize_event`。
- 如果首个事件不是 delta，只返回 `coalescer.feed(event)` 的结果。
- 如果首个事件是 delta，则继续 `get_nowait()` drain 当前 queue 中已就绪事件。
- drain 过程中忽略 `normalize_event(...) is None` 的 raw event。
- drain 过程中遇到非 delta 时，先 flush pending delta，再输出该非 delta event，并继续读取当前已就绪事件。
- drain 结束后 flush pending delta。

### `drain_stream_queue_until_task_done()`

职责：客户端断连后后台 drain bounded queue，避免 Bridge runner 因 `await stream_queue.put(...)` 卡死。

接口：

```python
async def drain_stream_queue_until_task_done(
    stream_queue: asyncio.Queue[Any],
    runner_task: asyncio.Task[Any],
    *,
    poll_timeout: float = 0.1,
) -> None:
    ...
```

契约：

- 循环清空 queue 中所有可读项。
- 如果 `runner_task.done()` 为真，返回。
- 否则等待一个 queue item，最多 `poll_timeout` 秒，超时后继续检查。
- 不读取或修改 `result_holder`。
- 不处理 persistence、push、private buffer 或 done event。

## 父模块接入设计

`api/routes.py` 只做薄接入：

- 导入 `chat_streaming_helpers`。
- 在 `_stream_chat()` 内创建 `coalescer = chat_streaming_helpers.StreamEventCoalescer()`。
- `_yield_queue_event(raw_event)` 改为调用 `collect_ready_stream_events(...)`，然后逐个 `yield _chat_sse_data(event)`。
- 循环尾部 pending delta flush 改为调用 `coalescer.flush()`。
- `_drain_stream_queue_until_runner_done()` 改为调用 `chat_streaming_helpers.drain_stream_queue_until_task_done(stream_queue, runner_task)`。

父模块继续保留：

- `stream_queue = asyncio.Queue(maxsize=CHAT_STREAM_QUEUE_MAXSIZE)`，确保 `api.routes.CHAT_STREAM_QUEUE_MAXSIZE` monkeypatch 仍生效。
- `_normalize_chat_stream_event()`、`_chat_sse_data()`、`_stream_error_event()` wrapper。
- `_persist_stream_result_after_runner_done()` 和断连后台 `BackgroundTasks.add_task()`。
- 所有 DB、push、private buffer 和 evolution 副作用。

## 测试设计

新增：`tests/test_api_chat_streaming_helpers_split.py`

覆盖：

- 新模块不导入父模块、不包含 `asyncio.run` 或 `run_awaitable_sync`。
- `StreamEventCoalescer` 连续 delta 合并。
- 非 delta event 前会 flush pending delta。
- `flush()` 会输出尾部 pending delta 并清空。
- `collect_ready_stream_events()` 会 drain 当前 queue 中已就绪事件，忽略无效 raw event，并在 progress/final/error 前保持 delta 顺序。
- `drain_stream_queue_until_task_done()` 能在 runner 完成前持续清空 bounded queue。

修改现有 split 扫描测试：

- `tests/test_api_history_log_routes_split.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_api_group_message_routes_split.py`
- `tests/test_api_sticker_media_routes_split.py`

将 `api/chat_streaming_helpers.py` 加入不导入父模块和不使用同步 awaitable 包装的扫描清单。

修改 streaming 行为测试：

- `tests/test_streaming_api.py::test_stream_chat_uses_bounded_stream_queue`

把断言从 `captured["maxsize"] > 0` 收紧为 patch 后的精确值，例如 patch `api.routes.CHAT_STREAM_QUEUE_MAXSIZE = 7` 并断言 bridge 捕获到 `queue.maxsize == 7`。

保留并运行相邻回归：

- `tests/test_streaming_api.py`
- `tests/test_streaming_response_envelope.py`
- `tests/test_api.py` 中断连后台落库 / push / prompt audit 流式用例。
- `tests/test_asyncio_run_policy.py`

## 风险与控制

### done 权威语义

delta 和 final 事件只是流式展示，最终 `done.answer`、`done.reply` 和 `done.messages` 必须继续来自 `bridge.handle_message()` 返回值。本阶段不碰 done envelope 构造。

### bounded queue backpressure

断连后台 drain 是防止 runner 卡死的关键。`drain_stream_queue_until_task_done()` 必须保持持续 drain 语义，且父模块仍在断连后台路径创建 drain task。

### 父模块 patch point

新模块不导入 `api.routes`。父模块把 `_normalize_chat_stream_event` 作为 hook 传入，把 SSE 序列化留在父模块，`CHAT_STREAM_QUEUE_MAXSIZE` 继续由父模块读取。

### 断连后台副作用

本阶段不移动 `_persist_stream_result_after_runner_done()`，避免改变 UnitOfWork、新 db session、push envelope late import、图片 token 展开、prompt audit meta 和 private buffer finalize。

## 验收标准

- `api/chat_streaming_helpers.py` 存在，且不导入 `api.routes`。
- `_stream_chat()` 的外部行为不变：delta 合并、progress 顺序、final before done、error 脱敏、done envelope 和断连后台行为通过现有测试。
- `api.routes.CHAT_STREAM_QUEUE_MAXSIZE` monkeypatch 精确影响新建 stream queue。
- 不新增 `asyncio.run()` 或 `run_awaitable_sync`。
- 全量测试通过。

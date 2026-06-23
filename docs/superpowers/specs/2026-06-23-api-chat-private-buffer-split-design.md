# 普通 API Chat Private Buffer 拆分设计

日期：2026-06-23

## 背景

`api/routes.py` 当前仍是 P3 超大文件队列中唯一超过 800 行的核心文件，Chat Streaming Helper 拆分后为 1408 行。`/chat` 主链路还保留私聊缓冲、PrivateTimingGate、guardrail、Prompt Runtime、Bridge、SSE、断连后台落库、push envelope 和 response envelope。

私聊缓冲逻辑集中在 `api.routes.proxy_chat()` 中，当前职责包括：

- 维护 `_private_buffers` 与 `_private_lock`。
- 创建 owner buffer，保存 `queries`、`files`、`qwen_task`、`done`、`deadline`、`window_seconds` 等字段。
- 追加 follower 消息，刷新 deadline，处理 `MAX_BUFFERED_MESSAGES` 溢出合并。
- 让 follower 等待 owner 完成，并在超时时释放 buffer。
- owner 等待最后一次 deadline 后 snapshot 合并后的消息和文件。
- 各类 guardrail、Bridge、SSE、prompt audit 和取消路径调用 `_finalize_private_buffer()` 释放 follower。

现有测试直接依赖父模块 patch point：`api.routes._private_buffers`、`api.routes.PRIVATE_BUFFER_WINDOW_SECONDS`、`api.routes.PRIVATE_BUFFER_WINDOW_WITH_FILES_SECONDS`、`api.routes.PRIVATE_BUFFER_FOLLOWER_TIMEOUT_SECONDS`、`api.routes.asyncio.sleep` 和 `api.routes._time.time`。因此本阶段只能做基础件拆分，不能把完整私聊状态机搬离父模块。

## 方案选择

### 方案 A：整体迁移私聊缓冲状态机

新增 `api/chat_private_buffer.py` 并迁移 owner / follower 主流程、deadline sleep、guardrail task 创建和 finalize 调用。

优点是行数收益较大。缺点是会同时触碰 `get_guardrail()`、`_detect_guardrail()`、fake clock、fake sleep、HTTP response payload、落库和 SSE 断连收尾，容易破坏父模块 monkeypatch 契约。

结论：不采用。

### 方案 B：引入 generation id 并修复旧 finalize 误清新窗口

新增窗口 handle，让 owner / follower timeout / 后台 finalize 只清理自己所属窗口，避免旧任务按 `user_id` 清掉新窗口。

优点是能修复已识别风险。缺点是这是行为变更，会改变当前 `_finalize_private_buffer(user_id)` 的语义，并需要重新审计全部 guardrail、Bridge、SSE 和 prompt audit finalize 调用点。

结论：暂不纳入本刀；作为独立 correctness 任务另行设计。

### 方案 C：拆私聊缓冲基础件（推荐）

新增 `api/chat_private_buffer.py`，只迁移状态容器、状态转移和纯 helper。父模块继续负责 guardrail task 创建、deadline sleep、HTTP response、Bridge、落库、SSE 和 push。父模块保留 `_private_buffers` 同名 dict alias、常量、wrapper 和 `proxy_chat()` 路由本体。

推荐原因：行为等价、改动面小，能先把裸 dict 操作从 `proxy_chat()` 中抽离，同时保持现有测试与 monkeypatch 入口。

## 目标

- 新增 `api/chat_private_buffer.py`，不导入 `api.routes`。
- 迁移 `_join_buffered_messages()`、`_merge_buffered_files()` 和窗口计算主体。
- 新增 `PrivateBufferStore` 管理 `_private_buffers` 与 `_private_lock` 的原子状态转移。
- 父模块 `api.routes` 保留原 helper 名称作为 wrapper，`__module__` 仍为 `api.routes`。
- 父模块 `_private_buffers` 继续是实际 runtime 使用的 dict，旧测试可继续 `clear()` 和断言 `deadline` / `window_seconds`。
- 保持现有私聊缓冲行为不变，包括“最新 follower 是否带文件决定窗口长度”的契约。

## 非目标

- 不迁移 `proxy_chat()` 或 `/chat` 路由注册。
- 不迁移 PrivateTimingGate 分类。
- 不迁移 `get_guardrail()`、`_detect_guardrail()` 或 guardrail task 创建。
- 不迁移 owner deadline sleep loop；父模块继续使用 `api.routes.asyncio.sleep` 和 `api.routes._time.time`。
- 不迁移 Bridge 调用、Prompt Runtime 输入组装、聊天落库、SSE、断连后台 push 或 response envelope。
- 不引入 generation id，不改变 `_finalize_private_buffer(user_id)` 的 user-level 语义。
- 不修改 Prompt Runtime 模板、`enriched_query`、conversation 结构、工具输出契约或 message envelope。
- 不新增 `asyncio.run()`、`run_awaitable_sync` 或同步函数包装 awaitable。

## 新模块设计

新文件：`api/chat_private_buffer.py`

### `PrivateBufferConfig`

职责：承载父模块传入的窗口配置，避免新模块复制常量值。

```python
@dataclass(frozen=True)
class PrivateBufferConfig:
    max_messages: int
    window_seconds: float
    window_with_files_seconds: float
    follower_timeout_seconds: float
```

父模块每次调用 wrapper 时即时构造 config，确保 `monkeypatch.setattr("api.routes.PRIVATE_BUFFER_WINDOW_SECONDS", ...)` 仍影响实际运行。

### 纯 helper

```python
def join_buffered_messages(messages: list[str]) -> str:
    ...

def merge_buffered_files(existing: list[str], incoming: list[str] | None) -> list[str]:
    ...

def private_buffer_window_seconds(files: list[str] | None, config: PrivateBufferConfig) -> float:
    ...
```

契约：

- `join_buffered_messages()` 过滤空字符串，用 `"\n---\n"` 连接。
- `merge_buffered_files()` 保持顺序、过滤空值并去重。
- `private_buffer_window_seconds()` 按当前 incoming files 判断窗口长度，有文件用 `window_with_files_seconds`，否则用 `window_seconds`。

### 状态结果类型

```python
@dataclass(frozen=True)
class PrivateBufferOwnerStarted:
    buffer: dict[str, Any]

@dataclass(frozen=True)
class PrivateBufferFollowerJoined:
    done_event: asyncio.Event

@dataclass(frozen=True)
class PrivateBufferSnapshot:
    messages: list[str]
    files: list[str]
    guardrail_task: asyncio.Task[Any]
```

第一刀保留 buffer entry 为 dict，字段仍为 `queries`、`files`、`qwen_task`、`done`、`result`、`answer`、`deadline`、`window_seconds`，避免一次性打破测试与运行时观察。

### `PrivateBufferStore`

职责：封装 `_private_buffers` 和 `_private_lock`，提供原子状态转移。

```python
class PrivateBufferStore:
    def __init__(self, buffers: dict[str, dict[str, Any]], lock: asyncio.Lock) -> None:
        ...

    async def begin_or_append(
        self,
        user_id: str,
        *,
        merged_query: str,
        files: list[str] | None,
        guardrail_task_factory: Callable[[], asyncio.Task[Any]],
        now: float,
        config: PrivateBufferConfig,
    ) -> PrivateBufferOwnerStarted | PrivateBufferFollowerJoined:
        ...

    async def snapshot(self, user_id: str) -> PrivateBufferSnapshot | None:
        ...

    async def deadline(self, user_id: str) -> float | None:
        ...

    async def store_guardrail_result(self, user_id: str, result: dict[str, Any]) -> None:
        ...

    async def finalize(
        self,
        user_id: str,
        answer: str | None = None,
        *,
        clear_window: bool = True,
    ) -> None:
        ...
```

`begin_or_append()` 契约：

- buffer 不存在或已 done：创建新窗口并返回 owner。
- buffer 未 done：追加 follower 消息，刷新文件、窗口长度和 deadline，返回 follower 的 `done_event`。
- 消息数量低于 `max_messages` 时 append；达到上限时把最新消息合并进最后一条，不静默丢弃。
- 只在 owner 创建时调用 `guardrail_task_factory()`；follower 不创建 guardrail task。
- 锁内只做 dict、event 和 task factory 调用；不等待 guardrail、不 sleep、不调用 Bridge、不落库。

`snapshot()` 契约：

- buffer 缺失时返回 `None`，父模块保持 `private_buffer_missing` 语义。
- buffer 存在时复制 `queries`、`files` 并返回 `qwen_task`。

`deadline()` 契约：

- buffer 缺失时返回 `None`，父模块保持 `private_buffer_missing` 语义。
- buffer 存在时返回当前 `deadline`，父模块继续用 `api.routes._time.time()` 和 `api.routes.asyncio.sleep()` 控制等待。

`finalize()` 契约：

- buffer 缺失时 no-op。
- `answer is not None` 时写入 `buf["answer"]`。
- `done` 未 set 时 set，释放 follower。
- `clear_window=True` 时 pop 当前 `user_id` buffer。
- 保持 user-level finalize 语义，不引入 generation 检查。

## 父模块接入设计

`api/routes.py` 保留以下导出与 patch point：

- `_private_buffers`：继续指向实际 store 使用的 dict。
- `_private_lock`：继续存在，作为 store 使用的 lock。
- `MAX_BUFFERED_MESSAGES`、`PRIVATE_BUFFER_WINDOW_SECONDS`、`PRIVATE_BUFFER_WINDOW_WITH_FILES_SECONDS`、`PRIVATE_BUFFER_FOLLOWER_TIMEOUT_SECONDS`：继续由父模块持有。
- `_join_buffered_messages()`、`_merge_buffered_files()`、`_private_buffer_window_seconds()`、`_finalize_private_buffer()`：保留父模块 wrapper。
- `proxy_chat()`、`get_guardrail()`、`_detect_guardrail()`、`get_bridge()`、`_persist_chat_turn()`：不迁移。

父模块新增轻量 helper：

```python
def _private_buffer_config() -> chat_private_buffer.PrivateBufferConfig:
    return chat_private_buffer.PrivateBufferConfig(
        max_messages=MAX_BUFFERED_MESSAGES,
        window_seconds=PRIVATE_BUFFER_WINDOW_SECONDS,
        window_with_files_seconds=PRIVATE_BUFFER_WINDOW_WITH_FILES_SECONDS,
        follower_timeout_seconds=PRIVATE_BUFFER_FOLLOWER_TIMEOUT_SECONDS,
    )
```

`proxy_chat()` 中私聊缓冲分支改为：

- 父模块先准备 `guardrail`、`merged`、`guardrail_input` 和 `guardrail_task_factory`。
- 调用 `_private_buffer_store.begin_or_append(...)` 完成 owner / follower 判定。
- follower 使用返回的 `done_event` 和父模块 `PRIVATE_BUFFER_FOLLOWER_TIMEOUT_SECONDS` 等待，超时后调用 `_finalize_private_buffer(req.user_id)`。
- owner deadline loop 仍在父模块，通过 `_private_buffer_store.snapshot(req.user_id)` 读取 snapshot。
- guardrail 结果仍由父模块计算并调用 `_private_buffer_store.store_guardrail_result(...)`。

## 测试设计

新增：`tests/test_api_chat_private_buffer_split.py`

覆盖：

- 新模块存在且不导入父模块，不包含 `asyncio.run` 或 `run_awaitable_sync`，不调用 `get_bridge()`、`get_guardrail()` 或 `_persist_chat_turn()`。
- `join_buffered_messages()` 过滤空消息并用 `---` 分隔。
- `merge_buffered_files()` 保持顺序、过滤空值、去重。
- `private_buffer_window_seconds()` 使用父模块传入 config；父模块 wrapper 能反映 monkeypatch 后的常量。
- `PrivateBufferStore.begin_or_append()` 创建 owner，并生成包含既有字段的 buffer dict。
- follower 追加会刷新 `queries`、`files`、`deadline` 和 `window_seconds`，并返回同一个 `done_event`。
- 超过 `max_messages` 时把新消息合并进最后一条，而不是丢弃。
- `snapshot()` 返回复制后的 messages / files 和原 guardrail task。
- `store_guardrail_result()` 写入 `buf["result"]`。
- `finalize()` 写 answer、set done、默认清理；`clear_window=False` 时保留窗口。
- 父模块 wrapper 的 `__module__` 仍为 `api.routes`，`api.routes._private_buffers` 仍是实际 store 使用的 dict。

保留并运行相邻回归：

- 现有 private buffer 行为回归：
  - `test_private_buffer_silent_releases_waiters`
  - `test_private_buffer_refreshes_window_and_persists_merged_messages`
  - `test_private_buffer_merges_files_for_final_bridge_request`
  - `test_private_buffer_text_after_files_shrinks_window_to_five_seconds`
  - `test_private_buffer_owner_cancel_releases_waiters_and_cleans_buffer`
  - `test_private_buffer_bridge_cancel_releases_waiters_and_cleans_buffer`
- asyncio 策略回归：`tests/test_asyncio_run_policy.py`。

## 风险与约束

- 当前 `_finalize_private_buffer(user_id)` 仍可能被旧任务误用于新窗口，本阶段不改变该行为；后续若修复，需要单独引入 window handle / generation id。
- 不把 deadline sleep loop 放进新模块，避免破坏 `api.routes.asyncio.sleep` 与 `api.routes._time.time` 的测试 patch point。
- 不把常量复制到新模块，避免 `api.routes.PRIVATE_BUFFER_WINDOW_SECONDS` monkeypatch 失效。
- 不把 guardrail provider 获取放进新模块，避免破坏 `api.routes.get_guardrail` 和 `_detect_guardrail` patch point。
- 新模块允许使用 `asyncio.Event` 和 `asyncio.Lock`，但禁止 `asyncio.run()`。

## 验证计划

1. 写入 split 红灯测试，确认 `api/chat_private_buffer.py` 不存在导致失败。
2. 写入新模块，跑 `tests/test_api_chat_private_buffer_split.py -v` 变绿。
3. 接入父模块，跑 private buffer 行为回归和 asyncio 策略回归。
4. 跑普通 API chat split 相邻扫描，确认新模块不反向导入父模块。
5. 全量回归通过后同步 `docs/todo.md` 和 `docs/plan_walkthrough.md`。

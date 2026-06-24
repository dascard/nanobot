# 普通 API Chat Route Runner 拆分设计

日期：2026-06-24

## 背景

`docs/todo.md` 的 P3 超大文件拆分当前只剩 `api/routes.py`。该文件当前为
1005 行，目标是低于 800 行。`/chat` 仍保留在父模块中，剩余最大的连续
逻辑集中在 Bridge 调用后的 route runner 编排：

- 非流式 `_do_chat()`、异常转译、Prompt audit 失败转译和 evolution 调度。
- 流式 `_stream_chat()`、runner task、SSE 主循环、错误事件、done payload、
  prompt audit no-send 和断连后台收尾。
- stream / non-streaming 结果 helper 的 callbacks 和 context 组装。

已完成的拆分模块覆盖了请求契约、画像、guardrail、pre-bridge 决策、runtime
payload、SSE loop、stream result 后台收尾、non-stream result 终态收尾和 push
envelope。第三十刀应继续沿用这些边界，不迁移整个 `/chat` endpoint。

## 目标

新增 `api/chat_route_runner.py`，把 `/chat` 中 Bridge 调用后的 route runner
编排迁出父模块，降低 `api/routes.py` 行数并让父模块只保留 HTTP 边界：

- `bridge = get_bridge()` 仍留在 `api.routes`，保留 `patch("api.routes.get_bridge")`
  和现有 monkeypatch 面。
- `StreamingResponse`、`HTTPException`、`BackgroundTasks` 和 `/chat` route
  装饰器仍留在 `api.routes`。
- 新模块只处理 async runner 编排、SSE 事件产出、结果收尾委托和 route 级结果
  描述，不导入父模块。
- 新模块不新增 `asyncio.run`、`run_awaitable_sync`，不新增同步函数包装
  awaitable。

## 非目标

本阶段不做以下事情：

- 不迁移 `/chat` endpoint 到独立 router。
- 不迁移 `/health`。
- 不迁移 `ChatProxyRequest` re-export。
- 不迁移 `_private_buffers`、`_private_lock`、`_private_buffer_store` 和私聊缓冲
  配置常量。
- 不改变 `enriched_query`、`<user_input>` 包裹、`bridge_meta` 字段、
  `history_header`、`history_messages`、`raw_query`、persona 注入或 Prompt
  Runtime 变量名。
- 不改变 SSE 协议、message envelope、push envelope 或 response envelope。
- 不把 DB model、`UnitOfWork`、`SessionLocal`、`ChatLog`、`ConversationTurn`
  迁入新模块。

## 方案

### 新模块职责

`api/chat_route_runner.py` 承载以下职责：

- 定义 `ChatRouteRunnerContext`，保存 `req`、`persist_req`、`bridge`、
  `enriched_query`、`bridge_meta`、`platform`、`guardrail_status`、
  `private_timing_meta`、队列大小、空回复占位符、安全错误文案和 evolution 阈值。
- 定义 `ChatRouteRunnerCallbacks`，由父模块注入所有外部能力：
  `call_bridge_non_streaming`、`persist_chat_turn`、`finalize_private_buffer`、
  `pop_bridge_reply_meta`、`private_prompt_audit_failure_meta`、
  `expand_chat_transport_answer`、`build_chat_push_envelope`、`chat_response_payload`、
  `chat_sse_data`、`stream_error_event`、`add_background_task`、`evolution_task` 和
  `push_envelope_to_qq`。
- 提供 `iter_streaming_chat_response(context)` async generator，替代父模块内嵌
  `_stream_chat()`。
- 提供 `run_non_streaming_chat_response(db, context)`，替代父模块 `_do_chat()` 与
  非流式 route 级异常 / HTTP 结果转译。
- 定义轻量结果对象，例如 `ChatRouteHttpError` 和
  `ChatRouteNonStreamingResult`，让新模块不需要导入 FastAPI 的 `HTTPException`。

### 父模块职责

`api.routes.proxy_chat()` 继续负责：

- HTTP route、依赖注入和鉴权。
- 自动注册用户、用户屏蔽、图片预缓存、persona、history、pre-bridge decision
  和 runtime payload 组装。
- `release_clean_session_transaction(db, label="chat_before_bridge", logger=logger)`。
- `bridge = get_bridge()`。
- 将当前 `db`、`background_tasks` 和父模块 patch point 绑定成 callbacks。
- `req.stream` 时返回 `StreamingResponse(...)`。
- 非流式结果中如有 `ChatRouteHttpError`，在父模块转成 `HTTPException`。

### 流式行为保持

流式路径必须保持以下行为：

- 使用 `CHAT_STREAM_QUEUE_MAXSIZE` 创建 bounded queue，保持 backpressure。
- runner task 调用 `bridge.handle_message(..., stream_queue=stream_queue, stream=True)`。
- SSE 主循环继续委托 `chat_sse_loop.iter_chat_stream_events()`。
- 产出的 SSE 字符串继续通过 `_chat_sse_data()`。
- runner 普通异常时只返回安全错误事件，并持久化空回复占位符。
- Prompt audit failed 时持久化空回复占位符和 audit meta，不发送真实回复。
- 正常完成时持久化原始 answer，SSE done payload 使用 transport answer。
- `pending >= EVOLUTION_THRESHOLD` 时通过父模块注入的 background task facade 调度
  `evolution_task`。
- 客户端断连且 runner 未完成时，登记后台收尾任务，参数保持
  `push=True`、`persist_db=None`、`drain_stream=True`，并立即 finalize 私聊缓冲。

### 非流式行为保持

非流式路径必须保持以下行为：

- 调用 `chat_runtime_facade.call_bridge_non_streaming()` 的语义不变，仍传
  `stream=False`。
- `asyncio.CancelledError` 触发私聊缓冲占位 finalize 后继续向上抛出。
- Bridge 普通异常触发私聊缓冲占位 finalize、占位落库，并返回 502 安全错误描述。
- `chat_non_streaming_result.finalize_non_streaming_chat_result()` 的持久化、
  transport answer、Prompt audit 和 pending 语义不变。
- Prompt audit failed 返回 500 错误描述，由父模块转成 `HTTPException`。
- pending 达阈值时通过父模块注入的 background task facade 调度 `evolution_task`。

## 接口约定

新模块不直接依赖 FastAPI、DB 全局入口或父模块。所有外部副作用都通过
`ChatRouteRunnerCallbacks` 注入。

建议接口形态：

```python
@dataclass(frozen=True)
class ChatRouteRunnerContext:
    req: Any
    persist_req: Any
    bridge: Any
    enriched_query: str
    bridge_meta: dict[str, Any]
    platform: str
    guardrail_status: str | None
    private_timing_meta: dict[str, Any] | None
    queue_maxsize: int
    empty_assistant_placeholder: str
    safe_error_message: str
    evolution_threshold: int
    callbacks: ChatRouteRunnerCallbacks


async def iter_streaming_chat_response(context: ChatRouteRunnerContext):
    ...


async def run_non_streaming_chat_response(db: Any, context: ChatRouteRunnerContext):
    ...
```

父模块可新增 `_chat_route_runner_callbacks(db, background_tasks)` 和
`_chat_route_runner_context(...)` 薄 wrapper，避免把大量 callbacks 直接铺在
`proxy_chat()` 内。

## 测试策略

新增 `tests/test_api_chat_route_runner_split.py`：

- 新模块不导入 `api.routes`、FastAPI route 边界、DB / UoW 边界、`get_bridge()`、
  `get_guardrail()`、`core.daily_digest`、`asyncio.run` 或 `run_awaitable_sync`。
- 流式成功路径：断言 bridge stream 参数、原始 answer 落库、transport answer
  出现在 done payload、pending 达阈值时调度 evolution。
- 流式 runner 异常：断言持久化空回复占位符，SSE 只返回安全错误。
- 流式 Prompt audit failed：断言 audit meta、`assistant_processed=1` 和安全错误
  SSE。
- 流式断连：用 `asyncio.Event` 控制 fake bridge，断言后台收尾被登记且不同步等待
  runner 完成。
- 非流式成功：断言调用 bridge non-streaming 和 result finalize，并返回 payload。
- 非流式 Bridge 异常：断言返回 502 错误描述、占位 finalize 和占位落库。
- 非流式 `CancelledError`：断言 finalize 后重新抛出，不吞取消信号。
- 非流式 Prompt audit failed：断言返回 500 错误描述。
- 父模块瘦身：断言 `api.routes` 不再包含 `_stream_chat()`、`_do_chat()`、内部
  `runner()`、`bridge.handle_message(`、直接构造 stream / non-streaming result
  callbacks 等胶水逻辑。

同步更新现有结构测试：

- `tests/test_api_chat_sse_loop_split.py` 中父模块断言从
  `StreamingResponse(_stream_chat(), ...)` 改为 route runner 生成器边界。
- `tests/test_api_chat_non_streaming_result_split.py` 中旧 `_do_chat()` 父模块哨兵
  改为断言非流式 route runner 已接管。
- 四个普通 API split 扫描清单加入 `api/chat_route_runner.py`：
  `test_api_group_message_routes_split.py`、`test_api_agent_step_routes_split.py`、
  `test_api_history_log_routes_split.py`、`test_api_sticker_media_routes_split.py`。

## 验证计划

红灯阶段：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_route_runner_split.py -v
```

实现阶段定向回归：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_chat_route_runner_split.py \
  tests/test_api_chat_sse_loop_split.py \
  tests/test_api_chat_streaming_result_split.py \
  tests/test_api_chat_streaming_helpers_split.py \
  tests/test_api_chat_non_streaming_result_split.py \
  tests/test_api_chat_runtime_facade_split.py \
  -v
```

扫描回归：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  -v
```

提交前验证：

```bash
python -m compileall api/routes.py api/chat_route_runner.py -q
git diff --check
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
  python -B -m pytest -p no:cacheprovider tests/ -v
```

## Prompt Runtime 核查

本阶段理论上不涉及 Prompt Runtime 模板变更。拆分发生在 runtime payload 已经完成
之后，只移动 Bridge route runner、SSE transport、结果收尾和 HTTP 错误描述。

实现时必须确认没有改动以下内容：

- `enriched_query`
- `<user_input>` 包裹
- `bridge_meta` 字段名和值语义
- `history_header`
- `history_messages`
- `raw_query`
- `persona_text`
- Prompt audit 标记

若实现过程中触碰上述任一项，则必须同步核查 `prompts.v2.default/chat/*`、
`data/prompts_v2/`、`core/prompt_v2/variables.py` 和
`core/prompt_v2/template_registry.py`。

## 风险和缓解

- **SSE 事件顺序风险：** 用新 split 测试覆盖 success、error、audit failed 和
  disconnect；保留 `chat_sse_loop.iter_chat_stream_events()` 作为唯一主循环。
- **断连后台任务风险：** 只把现有 `_persist_stream_result_after_runner_done()` 委托关系
  平移到 callbacks，不改变 `push=True` / `drain_stream=True` 语义。
- **HTTP 边界泄漏风险：** 新模块返回错误描述对象，父模块负责转成 `HTTPException`。
- **patch point 破坏风险：** `get_bridge()`、`_persist_chat_turn()`、
  `_finalize_private_buffer()`、`_chat_response_payload()` 等父模块入口继续保留。
- **async 策略风险：** 新模块保持 async 函数和 async generator，不引入任何同步等待
  awaitable 的封装。

## 后续

若本刀后 `api/routes.py` 仍略高于 800 行，下一刀优先评估
`api/chat_route_prelude.py`，迁移 `/chat` 请求前置装配。若本刀已低于 800 行，
则进入 P3 剩余的 `ruff` 批量清理。

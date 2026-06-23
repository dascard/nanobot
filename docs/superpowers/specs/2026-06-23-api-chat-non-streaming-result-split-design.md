# 普通 API Chat 非流式结果收尾拆分设计

日期：2026-06-23

## 背景

`docs/todo.md` 的 P3 超大文件拆分队列当前仍聚焦普通 `api/routes.py`。上一阶段已拆出 `api/chat_sse_loop.py`，`api/routes.py` 当前为 1121 行，仍超过 800 行目标。

`/chat` 已经拆出 request contract、response contract、persistence、runtime facade、guardrail facade、streaming helpers、streaming result、SSE loop、private buffer、push envelope、persona context、media precache 和 user block rules。非流式路径中，Bridge 成功返回后的结果收尾仍留在 `proxy_chat()` 末尾：弹出 `private_reply_meta`、处理 Prompt V2 audit failure、展开传输层图片 token、finalize 私聊 buffer、落库、判断 evolution 触发，并构造 `_chat_response_payload()`。

这段逻辑与现有 `api/chat_streaming_result.py` 的职责对称，但不涉及 SSE queue、断连后台 push 或 `StreamingResponse`。本阶段只拆非流式成功返回后的收尾，不迁移 Bridge 获取、Bridge 调用、异常控制流或 Prompt Runtime 输入。

## 只读审查结论

本阶段使用两个只读 explorer 分别审计 `/chat` 剩余区块。结论如下：

- 私聊 pre-bridge 区块行数收益更大，但同时涉及 private timing、private buffer、guardrail 编排和多个现有 monkeypatch 入口，适合后续独立设计。
- 非流式 Bridge 成功返回后的收尾边界更清晰，和现有 `chat_streaming_result.py` 对称，能先稳定移走一段 HTTP 成功路径代码。
- persona lookup、`PersonaInjectionService`、runtime payload、`enriched_query` 和 Prompt Runtime 模板链路不应混入本阶段。

因此本阶段选择先拆 `api/chat_non_streaming_result.py`。

## 方案选择

### 方案 A：新增 `api/chat_non_streaming_result.py`（推荐）

新模块暴露 `finalize_non_streaming_chat_result()`，通过 dataclass context 和 callbacks 接收父模块依赖。helper 负责非流式 Bridge 成功返回后的结果收尾，返回结构化 outcome；父模块根据 outcome 抛 HTTP 500、触发 background evolution 或返回 payload。

优点：

- 不触碰 Bridge 调用和 Prompt Runtime 输入。
- 不引入 FastAPI、DB session 创建、SSE 或 push envelope 依赖。
- 保留 `_persist_chat_turn()`、`_chat_response_payload()`、`_expand_chat_transport_answer()`、`_finalize_private_buffer()` 等父模块 patch point。
- 行为可以用纯 callback fake 单测覆盖，风险低于拆 private pre-bridge 编排。

风险：

- helper 内仍会调用父模块传入的持久化 callback，需要清晰区分原始 answer 和 transport answer。
- Prompt V2 audit failure 路径必须保持占位 answer、assistant meta 和 `assistant_processed=1` 语义。
- evolution trigger 仍应由父模块添加后台任务，helper 只返回布尔结果。

### 方案 B：扩展 `api/chat_streaming_result.py`

把非流式结果 helper 放进现有 streaming result 文件。

优点是复用同类 callback 结构。缺点是 streaming result 文件已经承担断连后台 push、queue drain 和 UnitOfWork 写入语义；非流式 HTTP 成功路径没有这些职责，继续放入会混淆流式和非流式生命周期。

结论：不采用。

### 方案 C：迁移 `_do_chat()` 调用和异常路径

把 Bridge 非流式调用、KT error path 和成功收尾一起迁走。

优点是行数收益更大。缺点是异常路径包含 HTTP 502 脱敏、取消时 buffer finalize、异常占位落库等 HTTP 控制流，且 `_do_chat()` 仍依赖 `enriched_query`、`bridge_meta` 和 `chat_runtime_facade`。这会扩大边界并增加 Prompt Runtime 回归风险。

结论：暂缓。

## 目标

- 新增 `api/chat_non_streaming_result.py`。
- 提取非流式 Bridge 成功返回后的收尾逻辑：
  - `private_reply_meta = _pop_bridge_reply_meta(...)`。
  - Prompt V2 audit failure 判断。
  - audit failure 时 finalize 私聊 buffer、按占位 answer 落库、写 `private_prompt_audit_failure_meta()`。
  - 正常路径记录 Bridge answer 日志信息。
  - 传输层展开 generated image token；展开失败时保留原 answer。
  - finalize 私聊 buffer。
  - 调用 `_persist_chat_turn()` 写原始 answer。
  - 判断 `pending >= EVOLUTION_THRESHOLD`。
  - 调用 `_chat_response_payload()` 构造最终 payload。
- 父模块继续负责：
  - `HTTPException` 抛出。
  - `background_tasks.add_task(evolution_task, req.user_id)`。
  - Bridge 获取和 Bridge 调用。
  - KT error path。
  - SSE / streaming path。
- 不新增 `asyncio.run()`、`run_awaitable_sync` 或同步函数包装 awaitable。

## 非目标

- 不迁移 `/chat` route 或 `proxy_chat()`。
- 不迁移 `_do_chat()`、`get_bridge()` 或 `chat_runtime_facade.call_bridge_non_streaming()`。
- 不迁移 KT error path、`asyncio.CancelledError` path 或 HTTP 502 脱敏逻辑。
- 不迁移 `_stream_chat()`、`StreamingResponse`、`chat_sse_loop` 或 `chat_streaming_result`。
- 不迁移 persona lookup、`PersonaInjectionService`、`_build_chat_context()` 或 runtime payload 组装。
- 不修改 Prompt Runtime 模板、`enriched_query`、conversation 结构、工具输出契约、message envelope、push envelope 或 response envelope。
- 不处理 WebUI / JS。

## 新模块设计

新文件：`api/chat_non_streaming_result.py`

### `ChatNonStreamingResultCallbacks`

职责：显式注入父模块 patch point，避免 helper 反向导入 `api.routes`。

接口：

```python
@dataclass(frozen=True)
class ChatNonStreamingResultCallbacks:
    pop_bridge_reply_meta: Callable[[Any, str], dict[str, Any] | None]
    private_prompt_audit_failure_meta: Callable[[], dict[str, Any]]
    finalize_private_buffer: Callable[..., Awaitable[None]]
    persist_chat_turn: Callable[..., int]
    expand_chat_transport_answer: Callable[[str], str]
    chat_response_payload: Callable[..., dict[str, Any]]
```

### `ChatNonStreamingResultContext`

职责：承载非流式收尾所需的 request、Bridge、answer、metadata 和 callback。

接口：

```python
@dataclass(frozen=True)
class ChatNonStreamingResultContext:
    req: Any
    persist_req: Any
    bridge: Any
    answer: str
    platform: str
    bridge_meta: dict[str, Any]
    guardrail_status: str | None
    private_timing_meta: dict[str, Any] | None
    empty_assistant_placeholder: str
    evolution_threshold: int
    callbacks: ChatNonStreamingResultCallbacks
```

### `ChatNonStreamingResult`

职责：让父模块用结构化结果处理 HTTP 层和后台任务。

接口：

```python
@dataclass(frozen=True)
class ChatNonStreamingResult:
    payload: dict[str, Any] | None
    pending: int | None = None
    should_trigger_evolution: bool = False
    prompt_audit_failed: bool = False
```

### `finalize_non_streaming_chat_result()`

职责：执行非流式成功结果收尾。

接口：

```python
async def finalize_non_streaming_chat_result(
    db: Any,
    context: ChatNonStreamingResultContext,
) -> ChatNonStreamingResult:
    ...
```

行为契约：

- 始终通过 callback 弹出 `private_reply_meta`。
- 当 `private_reply_meta["_agent_result"] == "prompt_v2_audit_failed"`：
  - 使用 `empty_assistant_placeholder` finalize 私聊 buffer。
  - 调用 `persist_chat_turn()`，传入占位 answer、`assistant_meta=private_prompt_audit_failure_meta()`、`assistant_processed=1` 和 `timing_meta`。
  - 返回 `prompt_audit_failed=True`、`payload=None`、`should_trigger_evolution=False`。
  - 不调用 `expand_chat_transport_answer()` 或 `chat_response_payload()`。
- 正常路径：
  - 日志记录 answer 长度和空 answer 状态。
  - 默认 `transport_answer = answer`。
  - 尝试调用 `expand_chat_transport_answer(answer)`；异常时记录 warning，并保留原 answer。
  - 使用原始 answer finalize 私聊 buffer。
  - 使用原始 answer 调用 `persist_chat_turn()`，不得写入展开后的 transport answer。
  - 当 `pending >= evolution_threshold` 时返回 `should_trigger_evolution=True`。
  - 调用 `chat_response_payload()` 构造 payload，传入 `answer=transport_answer`、`reply_meta=private_reply_meta`、`platform`、`chat_type`、`unprocessed_logs`、`guardrail_status` 和 `include_answer_chunks=True`。

## 父模块接入设计

`api/routes.py` 保留 `_do_chat()` 和异常路径，Bridge 成功返回后替换为：

```python
result_callbacks = chat_non_streaming_result.ChatNonStreamingResultCallbacks(
    pop_bridge_reply_meta=_pop_bridge_reply_meta,
    private_prompt_audit_failure_meta=_private_prompt_audit_failure_meta,
    finalize_private_buffer=_finalize_private_buffer,
    persist_chat_turn=_persist_chat_turn,
    expand_chat_transport_answer=_expand_chat_transport_answer,
    chat_response_payload=_chat_response_payload,
)
result_context = chat_non_streaming_result.ChatNonStreamingResultContext(
    req=req,
    persist_req=persist_req,
    bridge=bridge,
    answer=answer,
    platform=platform,
    bridge_meta=bridge_meta,
    guardrail_status=guardrail_status,
    private_timing_meta=private_timing_meta,
    empty_assistant_placeholder=EMPTY_ASSISTANT_PLACEHOLDER,
    evolution_threshold=EVOLUTION_THRESHOLD,
    callbacks=result_callbacks,
)
result = await chat_non_streaming_result.finalize_non_streaming_chat_result(db, result_context)
if result.prompt_audit_failed:
    raise HTTPException(status_code=500, detail="系统暂时不可用，请稍后再试")
if result.should_trigger_evolution:
    logger.info(
        "[/chat] Evolution triggered: user=%s, pending=%s, threshold=%s",
        req.user_id,
        result.pending,
        EVOLUTION_THRESHOLD,
    )
    background_tasks.add_task(evolution_task, req.user_id)
return result.payload
```

父模块继续保留：

- `get_bridge()` 和 `_do_chat()`。
- `except asyncio.CancelledError` 和 `except Exception` 的 KT error path。
- `HTTPException`。
- `background_tasks.add_task(evolution_task, req.user_id)`。
- `_chat_response_payload()`、`_expand_chat_transport_answer()`、`_persist_chat_turn()`、`_finalize_private_buffer()` 等 wrapper。

## 测试设计

新增：`tests/test_api_chat_non_streaming_result_split.py`

覆盖：

- 新模块源码约束：
  - 不导入 `api.routes`。
  - 不导入 FastAPI / `StreamingResponse` / `BackgroundTasks`。
  - 不调用 `get_bridge()` / `get_guardrail()`。
  - 不导入 `core.daily_digest` 或 `push_envelope_to_qq`。
  - 不出现 `asyncio.run` 或 `run_awaitable_sync`。
- 正常成功路径：
  - 弹出 bridge reply meta。
  - `expand_chat_transport_answer()` 接收原始 answer。
  - `finalize_private_buffer(user_id, answer)` 使用原始 answer。
  - `persist_chat_turn()` 写入原始 answer，而不是 transport answer。
  - `chat_response_payload()` 使用展开后的 transport answer。
  - `pending` 透传，达到阈值时 `should_trigger_evolution=True`。
- Prompt V2 audit failure 路径：
  - 使用占位 answer finalize 私聊 buffer。
  - 使用占位 answer 落库。
  - 写入 `assistant_meta=private_prompt_audit_failure_meta()`。
  - 写入 `assistant_processed=1`。
  - 不调用 transport expand。
  - 不构造成功 payload。
  - 返回 `prompt_audit_failed=True`。
- transport expand 失败降级：
  - helper 不抛出。
  - payload 使用原始 answer。
  - DB persist 仍写原始 answer。
- 父模块接入：
  - `api/routes.py` 导入 `chat_non_streaming_result`。
  - 成功路径调用 `finalize_non_streaming_chat_result()`。
  - `HTTPException(status_code=500, detail="系统暂时不可用，请稍后再试")` 仍在父模块。
  - `background_tasks.add_task(evolution_task, req.user_id)` 仍在父模块。
  - `_do_chat()` 和 KT error path 仍在父模块。

同步更新 chat split module 扫描清单：

- `tests/test_api_group_message_routes_split.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_api_history_log_routes_split.py`
- `tests/test_api_sticker_media_routes_split.py`

相邻回归：

- `tests/test_api_chat_streaming_result_split.py`
- `tests/test_api_chat_push_envelope_split.py`
- `tests/test_api_chat_persistence_split.py`
- `tests/test_chat_response_envelope.py`
- `tests/test_streaming_response_envelope.py`
- `tests/test_api.py::test_proxy_chat`
- `tests/test_api.py::test_proxy_chat_kt_error_does_not_echo_internal_detail`
- `tests/test_api.py::test_private_prompt_v2_audit_failure_is_not_context_chat`
- `tests/test_asyncio_run_policy.py`

## 验收标准

- `api/chat_non_streaming_result.py` 存在，且不导入父模块、FastAPI、push、Bridge 或 guardrail。
- 新模块定向测试通过。
- 四个 chat split module 扫描测试通过。
- `/chat` 非流式成功响应 envelope、reply meta、transport answer、pending 计数和 evolution trigger 语义不变。
- Prompt V2 audit failure 仍对外返回 HTTP 500 通用文案，且内部按占位 answer 持久化。
- KT error path 仍对外返回 502 脱敏错误，不被新 helper 接管。
- `api/routes.py` 行数继续下降。
- 全量测试通过。

## 风险与缓解

- **原始 answer 与 transport answer 混用风险**：测试明确断言 DB persist 写原始 answer，payload 写 transport answer。
- **Prompt V2 audit failure 回归风险**：测试覆盖占位 answer、assistant meta、`assistant_processed=1` 和不构造成功 payload。
- **父模块 patch point 破坏风险**：helper 全部通过 callback 依赖父模块 wrapper，不反向导入 `api.routes`。
- **边界扩张风险**：本阶段不迁移 Bridge 调用、KT error path、SSE、Prompt Runtime 或 persona injection；任何后续迁移另开设计。

# 普通 API Chat 私聊 Pre-Bridge 决策拆分设计

日期：2026-06-24

## 背景

`docs/todo.md` 的 P3 超大文件拆分队列当前只剩普通 `api/routes.py`，文件为 1098 行。前序阶段已把 `/chat` 周边的 request contract、response contract、persistence、runtime facade、guardrail facade、streaming helpers、streaming result、SSE loop、non-streaming result、private buffer、push envelope、persona context、media precache 和 user block rules 拆出。

`proxy_chat()` 中剩余最大连续区块是私聊 pre-bridge 决策编排，位于构建会话记忆之后、Prompt Runtime payload 之前。它负责 private timing 三态分类、casual / no_reply 早返回、private buffer owner / follower 分支、guardrail 检测、合并缓冲消息和 guardrail silent 判断。该区块仍直接写在父路由里，使 `/chat` 主流程继续偏长。

本阶段目标是把「进入 Bridge 前的私聊决策编排」拆成独立 helper。父模块仍负责 DB、HTTP response、Bridge、Prompt Runtime、SSE、落库和 evolution。

## 只读审查结论

本阶段复用两个只读 explorer 的审计结论：

- 非流式 Bridge 成功收尾已经完成拆分，当前更高收益的下一刀是私聊 pre-bridge 决策编排。
- 私聊 pre-bridge 区块行数收益明显，但现有测试依赖 `api.routes.get_guardrail`、`api.routes._private_buffers`、`api.routes.PRIVATE_BUFFER_WINDOW_SECONDS`、`api.routes.asyncio.sleep`、`api.routes._time.time` 等父模块 patch point。
- 因此新模块不能直接拥有全局 buffer、DB、HTTP response 或 runtime payload，只能通过 services / callbacks 接收父模块 wrapper。

## 方案选择

### 方案 A：新增 `api/chat_pre_bridge_decision.py`（推荐）

新模块暴露 `resolve_chat_pre_bridge_decision()`，输入 request、会话类型和 services，输出结构化 outcome。父模块根据 outcome 继续执行早返回落库 / response，或生成 `persist_req`、Prompt Runtime payload 和 Bridge 调用。

优点：

- 行数收益较高，能移走 private timing、buffer owner / follower 和 guardrail 编排的大部分代码。
- 不触碰 Prompt Runtime 输入、persona injection、Bridge、SSE 或 response envelope。
- 父模块 monkeypatch 入口保持不变，通过 services 注入到新模块。
- 可以对新模块做纯 callback / fake service 单测，不需要启动 FastAPI client。

风险：

- 需要谨慎保留 `api.routes.asyncio.sleep` 和 `api.routes._time.time` 这类测试 patch 点，父模块应把它们作为 service 传入。
- early return 不能在新模块内落库或构造 `_chat_response_payload()`，否则会破坏父模块 response contract 和现有测试 patch 点。
- guardrail task 使用 `asyncio.create_task()` 与 `asyncio.to_thread()` 组合创建，新模块可以编排 task，但 `_detect_guardrail` 和 `get_guardrail()` 必须经父模块 callback 注入。

### 方案 B：只拆 private timing 分类

只把 `get_private_gate().classify()`、`no_reply` / casual / `reply_now` 初始决策拆出。

优点是风险低。缺点是行数收益小，private buffer / guardrail 编排仍在父模块，`proxy_chat()` 主体复杂度改善有限。

结论：暂缓，可作为本阶段实现过大时的降级方案。

### 方案 C：迁移完整 `/chat` pre-bridge 到 Prompt Runtime 前

把 persona lookup、memory context、private timing、guardrail、persist req、silent guardrail、persona injection 和 runtime payload 前置逻辑整体迁走。

优点是行数收益最大。缺点是会触碰 `PersonaInjectionService`、`_build_chat_context()`、`release_clean_session_transaction()` 和 Prompt Runtime 输入契约；模板同步风险高。

结论：不采用。

## 目标

- 新增 `api/chat_pre_bridge_decision.py`。
- 提取 `proxy_chat()` 中私聊 pre-bridge 决策编排：
  - private timing 分类。
  - `no_reply` early outcome。
  - casual template / fallback early outcome。
  - `reply_now` 初始 `buffered_query` / `buffered_files`。
  - guardrail 获取和初始 guardrail task 创建。
  - private buffer owner / follower 分支。
  - follower timeout 后 finalize private buffer。
  - owner deadline 等待、snapshot、合并消息与文件。
  - 多消息场景对合并后的 guardrail input 重新检测。
  - guardrail result 存储和 `guardrail_status` 计算。
  - `CancelledError` / exception 时 finalize private buffer 后重抛。
  - 产出 `final_query`、`final_files`、`private_decision`、`private_timing_meta`、`guardrail_status` 和 `classifier_ran`。
- 父模块继续负责：
  - `release_clean_session_transaction(db, label="chat_before_private_decision")`。
  - `_persist_chat_turn()`。
  - `_chat_response_payload()`。
  - `_clone_chat_request()`。
  - guardrail silent 的落库和 response。
  - persona injection 和 Prompt Runtime payload。
  - Bridge 调用、SSE 和非流式结果收尾。

## 非目标

- 不迁移 `/chat` route 或 `proxy_chat()` 本体。
- 不迁移 persona lookup、persona JSON parse 或 `PersonaInjectionService`。
- 不迁移 `_build_chat_context()`、history 注入或 `release_clean_session_transaction()`。
- 不迁移 `_clone_chat_request()` 或 `persist_req` 的生成位置。
- 不迁移 guardrail silent 的 `_persist_chat_turn()` 和 `_chat_response_payload()`。
- 不迁移 Prompt Runtime 输入、`safe_user_input`、`enriched_query`、`bridge_meta` 或模板。
- 不迁移 Bridge、`_do_chat()`、`_stream_chat()`、SSE、message envelope、push envelope 或 evolution。
- 不新增 `asyncio.run()`、`run_awaitable_sync` 或同步函数包装 awaitable。
- 不处理 WebUI / JS。

## 新模块设计

新文件：`api/chat_pre_bridge_decision.py`

### `ChatPreBridgeServices`

职责：承载父模块注入的 patch point、时间源、buffer store 和 guardrail callback。

接口：

```python
@dataclass(frozen=True)
class ChatPreBridgeServices:
    private_buffer_store: Any
    private_buffer_config: Callable[[], Any]
    private_buffer_follower_timeout_seconds: float
    now: Callable[[], float]
    sleep: Callable[[float], Awaitable[None]]
    wait_private_buffer_deadline: Callable[[str], Awaitable[bool]]
    finalize_private_buffer: Callable[[str], Awaitable[None]]
    normalize_files: Callable[[Any], list[str]]
    join_buffered_messages: Callable[[Sequence[str]], str]
    build_guardrail_input: Callable[[str, Any], str]
    get_guardrail: Callable[[], Any]
    detect_guardrail: Callable[[Any, str, bool], dict[str, Any]]
    guardrail_status_from_result: Callable[[dict[str, Any] | None], str]
    is_guardrail_superuser: Callable[[str], bool]
    get_private_gate: Callable[[], Any]
    get_casual_reply: Callable[[str, bool], str]
    private_timing_meta: Callable[[Any | None], dict[str, Any] | None]
    logger: Any
```

`detect_guardrail` 的第三个参数表示 `allow_passthrough`。父模块通过薄 wrapper 把现有 `_detect_guardrail(guardrail, message, allow_passthrough=bool)` 适配成固定三参 callback。`get_casual_reply` 的第二个参数表示 `is_superuser`，同样由父模块 wrapper 负责调用现有模板函数。

### Outcome 类型

职责：把「早返回」和「继续进入 runtime payload」分清楚。

接口：

```python
@dataclass(frozen=True)
class ChatPreBridgeEarlyReturn:
    status: str
    reason: str = ""
    answer: str = ""
    source: str = ""
    intent: str = ""
    guardrail_status: str | None = None
    persist_answer: str | None = None
    persist_guardrail_status: str | None = None
    persist_timing_meta: dict[str, Any] | None = None


@dataclass(frozen=True)
class ChatPreBridgeContinue:
    final_query: str
    final_files: list[str]
    private_decision: Any | None
    private_timing_meta: dict[str, Any] | None
    guardrail_status: str | None
    classifier_ran: bool
```

行为说明：

- `ChatPreBridgeEarlyReturn` 不直接落库，也不构造 response；父模块根据字段调用 `_persist_chat_turn()` 和 `_chat_response_payload()`。
- `persist_answer is None` 表示该 early return 不需要写 assistant answer，例如 private buffer follower / missing。
- guardrail silent 不在新模块内直接落库；新模块返回 `ChatPreBridgeContinue`，其中 `guardrail_status="silent"` 且 `classifier_ran=True`，父模块保留现有 silent 落库和 response 分支。

### `resolve_chat_pre_bridge_decision()`

接口：

```python
async def resolve_chat_pre_bridge_decision(
    req: Any,
    *,
    is_group: bool,
    is_superuser: bool,
    services: ChatPreBridgeServices,
) -> ChatPreBridgeEarlyReturn | ChatPreBridgeContinue:
    raise NotImplementedError
```

上方函数体仅用于展示签名，实际实现会返回 `ChatPreBridgeEarlyReturn` 或 `ChatPreBridgeContinue`。

行为契约：

- group chat 且非 `classification_request`：
  - 不运行 private timing。
  - 不运行 guardrail / private buffer。
  - 返回 `ChatPreBridgeContinue(final_query=req.query, final_files=normalize_files(req.files), classifier_ran=False)`。
- private chat 且非 `classification_request`：
  - 调用 `get_private_gate().classify()`。
  - 优先传入 `is_superuser`；若旧测试桩不支持该参数，仅在 `TypeError` 文本包含 `is_superuser` 时降级重试。
  - `action == "no_reply"` 返回 early no_reply，`persist_answer=""`。
  - `effort == "casual"` 使用 `get_casual_reply()`；有模板则 early ok，无模板则 fallback 为 `你先说事` 或空字符串；均不进入 guardrail / buffer / Bridge。
  - `action == "reply_now"` 时用 `merged_messages` 或 `query` 初始化 `buffered_query` 与 `buffered_files`。
  - private timing 异常只记录 warning，不阻断 guardrail / buffer 后续流程。
- private chat 或 `classification_request`：
  - 设置 `classifier_ran=True`。
  - 通过 `get_guardrail()`、`build_guardrail_input()` 和 `detect_guardrail()` 创建 guardrail task。
  - `allow_passthrough` 使用 `is_guardrail_superuser(req.user_id)`。
  - 使用 `private_buffer_store.begin_or_append()` 进入 owner / follower 分支。
  - follower 等待 owner `done_event`，超时则 finalize private buffer；返回 early silent / `private_buffer_follower`。
  - owner 等待 `wait_private_buffer_deadline(req.user_id)`；missing 时返回 early silent / `private_buffer_missing`。
  - snapshot missing 时返回 early silent / `private_buffer_missing`。
  - 多条 buffered message 时对合并后的 guardrail input 重新运行 `detect_guardrail()`；单条时复用初始 guardrail task。
  - guardrail result 写回 `private_buffer_store.store_guardrail_result()`。
  - 返回 continue outcome，携带合并后的 final query / files 与 guardrail status。
  - `CancelledError` 和普通异常都先 finalize private buffer，再重抛。

## 父模块接入设计

`api/routes.py` 保留父模块 wrapper，并新增薄 wrapper：

```python
def _chat_pre_bridge_services() -> chat_pre_bridge_decision.ChatPreBridgeServices:
    return chat_pre_bridge_decision.ChatPreBridgeServices(
        private_buffer_store=_private_buffer_store,
        private_buffer_config=_private_buffer_config,
        private_buffer_follower_timeout_seconds=PRIVATE_BUFFER_FOLLOWER_TIMEOUT_SECONDS,
        now=_time.time,
        sleep=asyncio.sleep,
        wait_private_buffer_deadline=_wait_private_buffer_deadline,
        finalize_private_buffer=_finalize_private_buffer,
        normalize_files=_normalize_files,
        join_buffered_messages=_join_buffered_messages,
        build_guardrail_input=_build_guardrail_input,
        get_guardrail=get_guardrail,
        detect_guardrail=_detect_guardrail_for_pre_bridge,
        guardrail_status_from_result=chat_guardrail_facade.guardrail_status_from_result,
        is_guardrail_superuser=_is_guardrail_superuser,
        get_private_gate=get_private_gate,
        get_casual_reply=_get_casual_reply_for_pre_bridge,
        private_timing_meta=_private_timing_meta,
        logger=logger,
    )
```

为了让 `get_private_gate` / `get_casual_reply` 保持父模块 patch point，父模块应提供模块级薄 wrapper；避免在新模块内部导入 `core.private_timing` 或 `core.reply_templates`。wrapper 形态如下：

```python
def get_private_gate() -> Any:
    from core.private_timing import get_private_gate as _core_get_private_gate

    return _core_get_private_gate()


def _get_casual_reply_for_pre_bridge(query: str, is_superuser: bool) -> str:
    from core.reply_templates import get_casual_reply as _core_get_casual_reply

    return _core_get_casual_reply(query, is_superuser=is_superuser)


def _detect_guardrail_for_pre_bridge(
    guardrail: Any,
    message: str,
    allow_passthrough: bool,
) -> dict[str, Any]:
    return _detect_guardrail(
        guardrail,
        message,
        allow_passthrough=allow_passthrough,
    )
```

父模块调用形态：

```python
pre_bridge = await _resolve_chat_pre_bridge_decision(
    req,
    is_group=is_group,
    is_superuser=is_superuser,
)

if isinstance(pre_bridge, ChatPreBridgeEarlyReturn):
    if pre_bridge.persist_answer is not None:
        # 父模块使用原始 req、persist_answer、persist_guardrail_status
        # 和 persist_timing_meta 调用 _persist_chat_turn。
        pass
    # 父模块根据 status、reason、answer、source、intent 和
    # guardrail_status 调用 _chat_response_payload。
    return response_payload

final_query = pre_bridge.final_query
final_files = pre_bridge.final_files
_private_decision = pre_bridge.private_decision
private_timing_meta = pre_bridge.private_timing_meta
guardrail_status = pre_bridge.guardrail_status
_classifier_ran = pre_bridge.classifier_ran
```

guardrail silent 分支保持在父模块：

```python
persist_req = _clone_chat_request(req, query=final_query, files=final_files)
if _classifier_ran and guardrail_status == "silent":
    await _finalize_private_buffer(req.user_id)
    # 父模块使用 persist_req 落库「（数据中转，自动静默）」。
    # 父模块返回 status="silent"、reason="guardrail_silent" 的响应。
    return response_payload
```

## 测试设计

新增：`tests/test_api_chat_pre_bridge_decision_split.py`

覆盖：

- 新模块源码约束：
  - 不导入 `api.routes`。
  - 不导入 FastAPI / `StreamingResponse` / `BackgroundTasks`。
  - 不调用 `get_bridge()` / Bridge handle。
  - 不调用 `_persist_chat_turn()`、`ChatLog`、`ConversationTurn` 或 `db.commit()`。
  - 不出现 `build_chat_runtime_payload`、`ChatRuntimeInput` 或 `enriched_query`。
  - 不出现 `asyncio.run` 或 `run_awaitable_sync`。
- private timing `no_reply`：
  - fake gate 返回 no_reply，helper 返回 early outcome，`persist_answer=""`，不调用 guardrail / buffer。
- private timing casual：
  - 有 template 时 early ok / `casual_template`。
  - 无 template 且 query 非空时 answer 为 `你先说事`。
  - 不调用 guardrail / buffer / Bridge。
- private timing `reply_now`：
  - merged messages 优先于 query。
  - files 经 `normalize_files()`。
- private buffer follower：
  - fake store 返回 `PrivateBufferFollowerJoined`，helper 等待 done / timeout 后返回 silent / `private_buffer_follower`。
- owner snapshot 合并：
  - snapshot 多条消息时对合并后的 guardrail input 重新检测。
  - final query 使用 `join_buffered_messages(snapshot.messages)`。
  - final files 使用 snapshot files。
- guardrail status：
  - `guardrail_status_from_result()` 的返回值透传到 continue outcome。
  - superuser passthrough 通过注入的 `is_guardrail_superuser()` 决定。
- 父模块 wrapper 保留：
  - `routes._resolve_chat_pre_bridge_decision.__module__ == "api.routes"`。
  - `routes._private_buffer_config.__module__ == "api.routes"`。
  - `routes._wait_private_buffer_deadline.__module__ == "api.routes"`。
  - `routes._finalize_private_buffer.__module__ == "api.routes"`。

同步更新 chat split module 扫描清单：

- `tests/test_api_group_message_routes_split.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_api_history_log_routes_split.py`
- `tests/test_api_sticker_media_routes_split.py`

相邻回归：

- `tests/test_api_chat_private_buffer_split.py`
- `tests/test_api_chat_guardrail_facade_split.py`
- `tests/test_api.py::test_proxy_chat_persists_private_timing_scoring_meta`
- `tests/test_api.py::test_proxy_chat_no_reply_persists_private_timing_scoring_meta`
- `tests/test_api.py::test_private_buffer_silent_releases_waiters`
- `tests/test_api.py::test_private_buffer_refreshes_window_and_persists_merged_messages`
- `tests/test_api.py::test_private_buffer_merges_files_for_final_bridge_request`
- `tests/test_api.py::test_private_buffer_text_after_files_shrinks_window_to_five_seconds`
- `tests/test_api.py::test_private_buffer_owner_cancel_releases_waiters_and_cleans_buffer`
- `tests/test_api.py::test_private_buffer_bridge_cancel_releases_waiters_and_cleans_buffer`
- `tests/test_asyncio_run_policy.py`

## 验收标准

- `api/chat_pre_bridge_decision.py` 存在，且不导入父模块、FastAPI、DB、Bridge 或 Prompt Runtime。
- 新模块定向测试通过。
- 四个 chat split module 扫描测试通过。
- private timing no_reply / casual / reply_now 行为不变。
- private buffer owner / follower、deadline、snapshot 合并、文件合并和取消清理行为不变。
- guardrail silent 仍由父模块落库并返回 response。
- Prompt Runtime 输入、persona injection、Bridge、SSE 和非流式结果收尾均不受本阶段迁移影响。
- `api/routes.py` 行数明显下降。
- 全量测试通过。

## 风险与缓解

- **patch point 破坏风险**：父模块保留 `_resolve_chat_pre_bridge_decision()` 和 service factory，测试断言 wrapper 仍属于 `api.routes`。
- **guardrail / buffer 时序风险**：相邻回归覆盖 owner cancel、bridge cancel、follower silent、deadline 刷新和文件窗口收缩。
- **Prompt Runtime 契约风险**：本阶段不迁移 `final_query` 之后的 runtime payload 和 persona injection；不需要修改 canonical Prompt Runtime 模板。
- **早返回副作用风险**：新模块只返回 early outcome，父模块继续落库和构造 response，避免 helper 绑定 DB / FastAPI。
- **边界扩张风险**：不迁移完整 `proxy_chat()`；若实现过大，降级方案是先拆 private timing 子 helper。

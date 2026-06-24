# 普通 API Chat Pre-Bridge 路由结果拆分设计

日期：2026-06-24

## 背景

`docs/todo.md` 的 P3 超大文件拆分队列当前只剩普通 `api/routes.py`，文件为 1020 行。前序阶段已把 `/chat` 周边的 request contract、response contract、persistence、runtime facade、guardrail facade、streaming helper、streaming result、SSE loop、non-streaming result、private buffer、push envelope、persona context、persona lookup、media precache、user block rules 和 private pre-bridge decision 拆出。

`proxy_chat()` 中仍保留一段 pre-bridge 决策结果转译逻辑：判断 `ChatPreBridgeEarlyReturn` 并转为落库 / response；对 `ChatPreBridgeContinue` 展开 `final_query`、`final_files`、private timing meta 和 guardrail 状态；在 `guardrail_silent` 分支 finalize private buffer、持久化自动静默结果并返回 silent response。这些逻辑位于 private pre-bridge decision helper 之后、Prompt Runtime payload 之前，职责是 route 层结果转译，而不是决策本身。

本阶段目标是把这段 route result 转译拆到独立 helper，继续缩短 `api/routes.py`，同时保留父模块所有 HTTP、DB、Prompt Runtime、Bridge 和 SSE 边界。

## 只读审查结论

两个只读子 agent 的审计结论一致：

- 下一刀候选包括 pre-bridge 路由结果转译、入口 preflight、流式响应编排。
- 流式响应编排行数收益最大，但会触碰 async generator、断连后台任务、bounded queue drain 和 SSE 错误脱敏，风险明显更高。
- 入口 preflight 收益较小，且包含用户 upsert 与 blocked ChatLog commit 边界。
- pre-bridge route result 与上一刀 `api/chat_pre_bridge_decision.py` 直接相邻，边界清晰，主要通过 callbacks 保留父模块 patch point，适合作为下一刀。

## 方案选择

### 方案 A：新增 `api/chat_pre_bridge_route_result.py`（推荐）

新模块负责把 `ChatPreBridgeEarlyReturn` / `ChatPreBridgeContinue` 转为两类输出：

- `ChatPreBridgeRouteEarlyResponse`：父模块可直接 `return payload`。
- `ChatPreBridgeRouteContinue`：父模块继续进入 persona injection、Prompt Runtime payload 和 Bridge。

优点：

- 与既有 `chat_pre_bridge_decision` 边界自然衔接。
- 不碰 SSE、Bridge、Prompt Runtime 模板或 response envelope 构造细节。
- 可用 fake callbacks 做纯单元测试，验证 early return、guardrail silent 和 continue 字段。
- 预计能从 `api/routes.py` 再移走约 40 行。

风险：

- early return response 字段必须保持一致，尤其是 `status`、`reason`、`source`、`intent`、`guardrail_status` 和 `include_answer_chunks=True`。
- guardrail silent 分支必须继续使用 `persist_req`，不能误用原始 `req`。
- 新模块不能直接调用父模块私有函数，也不能导入 `api.routes`；必须通过 callbacks 注入。

### 方案 B：拆出入口 preflight

迁移用户 upsert、用户名更新、blocked user silent 分支。

优点是风险可控。缺点是涉及 `db.commit()` 与只写 `ChatLog` 的特殊分支，且行数收益小于本阶段推荐方案。

结论：暂缓。

### 方案 C：拆出完整流式响应编排

迁移 `_stream_chat()` async generator。

优点是行数收益最大。缺点是直接触碰流式断连、后台 push、bounded queue drain 和 SSE done 权威语义，调试成本高。

结论：暂缓到更充分的测试设计之后。

## 目标

- 新增 `api/chat_pre_bridge_route_result.py`。
- 新模块只处理 pre-bridge decision outcome 的 route 层转译：
  - early return 需要持久化时，调用注入的 `persist_chat_turn` callback。
  - early return 始终通过注入的 `chat_response_payload` callback 构造 payload。
  - continue outcome 展开为 `final_query`、`final_files`、`private_decision`、`private_timing_meta`、`guardrail_status`、`classifier_ran` 和 `persist_req`。
  - guardrail silent 分支调用注入的 `finalize_private_buffer`、`persist_chat_turn` 和 `chat_response_payload`，返回 early response。
- 父模块继续负责：
  - 调用 `_resolve_chat_pre_bridge_decision()`。
  - 提供 `_clone_chat_request()`、`_persist_chat_turn()`、`_chat_response_payload()` 和 `_finalize_private_buffer()` callback。
  - 继续在 `proxy_chat()` 中保存 `final_query`、`final_files`、`persist_req`、guardrail 状态和 private timing meta 变量。
  - persona injection、Prompt Runtime payload、Bridge、SSE、non-streaming result 和 evolution。

## 非目标

- 不迁移 `/chat` route 或 `proxy_chat()` 本体。
- 不迁移用户 upsert、blocked user silent、图片预缓存或 persona lookup。
- 不迁移 `_build_chat_context()`、history 注入或 `release_clean_session_transaction()`。
- 不迁移 private timing、private buffer 或 guardrail 决策逻辑。
- 不迁移 `PersonaInjectionService`。
- 不迁移 `safe_user_input`、`enriched_query`、`bridge_meta` 或 Prompt Runtime 模板。
- 不迁移 Bridge、`_do_chat()`、`_stream_chat()`、SSE、message envelope、push envelope 或 evolution。
- 不新增 `asyncio.run()`、`run_awaitable_sync` 或同步函数包装 awaitable。
- 不处理 WebUI / JS。

## 新模块设计

新文件：`api/chat_pre_bridge_route_result.py`

### `ChatPreBridgeRouteCallbacks`

职责：承载父模块注入的 route 层 patch point。

字段：

- `clone_chat_request`
- `persist_chat_turn`
- `chat_response_payload`
- `finalize_private_buffer`

### `ChatPreBridgeRouteEarlyResponse`

职责：表示父模块应该直接返回的 payload。

字段：

- `payload: dict[str, Any]`

### `ChatPreBridgeRouteContinue`

职责：表示父模块可以继续进入 runtime payload 的上下文。

字段：

- `final_query: str`
- `final_files: list[str]`
- `private_decision: Any | None`
- `private_timing_meta: dict[str, Any] | None`
- `guardrail_status: str | None`
- `classifier_ran: bool`
- `persist_req: Any`

### `resolve_pre_bridge_route_result()`

接口：

```python
async def resolve_pre_bridge_route_result(
    req: Any,
    pre_bridge: Any,
    *,
    callbacks: ChatPreBridgeRouteCallbacks,
) -> ChatPreBridgeRouteEarlyResponse | ChatPreBridgeRouteContinue:
    raise NotImplementedError
```

行为：

- 若 `pre_bridge` 是 `ChatPreBridgeEarlyReturn`：
  - `persist_answer is not None` 时调用 `persist_chat_turn(req, persist_answer, guardrail_status=pre_bridge.persist_guardrail_status, timing_meta=pre_bridge.persist_timing_meta)`。
  - 返回 `ChatPreBridgeRouteEarlyResponse`，其 payload 由 `chat_response_payload(req, status=pre_bridge.status, reason=pre_bridge.reason, answer=pre_bridge.answer, source=pre_bridge.source, intent=pre_bridge.intent, guardrail_status=pre_bridge.guardrail_status, include_answer_chunks=True)` 生成。
  - payload 字段保持父模块旧逻辑一致。
- 若 `pre_bridge` 是 `ChatPreBridgeContinue`：
  - 使用 `clone_chat_request(req, query=final_query, files=final_files)` 生成 `persist_req`。
  - 若 `classifier_ran and guardrail_status == "silent"`：
    - 调用 `finalize_private_buffer(req.user_id)`。
    - 使用 `persist_req` 持久化 `"（数据中转，自动静默）"`。
    - 返回 silent payload，`reason="guardrail_silent"`。
  - 其他情况返回 `ChatPreBridgeRouteContinue`。

## 父模块接入设计

`api/routes.py` 新增 import：

```python
from api import chat_pre_bridge_route_result
```

新增 callbacks wrapper：

```python
def _chat_pre_bridge_route_callbacks() -> chat_pre_bridge_route_result.ChatPreBridgeRouteCallbacks:
    return chat_pre_bridge_route_result.ChatPreBridgeRouteCallbacks(
        clone_chat_request=_clone_chat_request,
        persist_chat_turn=_persist_chat_turn,
        chat_response_payload=_chat_response_payload,
        finalize_private_buffer=_finalize_private_buffer,
    )
```

新增 async wrapper：

```python
async def _resolve_pre_bridge_route_result(
    req: ChatProxyRequest,
    pre_bridge: Any,
) -> chat_pre_bridge_route_result.ChatPreBridgeRouteEarlyResponse | chat_pre_bridge_route_result.ChatPreBridgeRouteContinue:
    return await chat_pre_bridge_route_result.resolve_pre_bridge_route_result(
        req,
        pre_bridge,
        callbacks=_chat_pre_bridge_route_callbacks(),
    )
```

`proxy_chat()` 中把原有 early / continue / guardrail silent 转译替换为：

```python
pre_bridge_route = await _resolve_pre_bridge_route_result(req, pre_bridge)
if isinstance(pre_bridge_route, chat_pre_bridge_route_result.ChatPreBridgeRouteEarlyResponse):
    return pre_bridge_route.payload

final_query = pre_bridge_route.final_query
final_files = pre_bridge_route.final_files
_private_decision = pre_bridge_route.private_decision
private_timing_meta = pre_bridge_route.private_timing_meta
guardrail_status = pre_bridge_route.guardrail_status
_classifier_ran = pre_bridge_route.classifier_ran
persist_req = pre_bridge_route.persist_req
```

## 测试设计

新增 `tests/test_api_chat_pre_bridge_route_result_split.py`：

- `test_chat_pre_bridge_route_result_module_does_not_import_parent_routes_or_runtime_side_effects`
  - 新模块不导入 `api.routes`、FastAPI、Bridge、Prompt Runtime、DB session、`asyncio.run` 或 `run_awaitable_sync`。
- `test_early_return_persists_when_answer_is_present_and_builds_payload`
  - 用 fake callbacks 验证 early return 持久化参数和 payload 参数。
- `test_early_return_without_persist_only_builds_payload`
  - 覆盖 `persist_answer is None` 的 private buffer follower / missing 类分支。
- `test_continue_outcome_clones_persist_request_and_exposes_fields`
  - 覆盖普通 continue 字段展开。
- `test_guardrail_silent_finalizes_buffer_persists_silent_answer_and_returns_payload`
  - 验证 guardrail silent 使用 `persist_req` 持久化并返回 silent payload。
- `test_parent_pre_bridge_route_result_wrapper_remains_patchable`
  - 父模块 wrapper 的 `__module__ == "api.routes"`，并可 monkeypatch 新模块函数观察调用。

更新四个 chat split module 扫描清单：

- `tests/test_api_group_message_routes_split.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_api_history_log_routes_split.py`
- `tests/test_api_sticker_media_routes_split.py`

把 `api/chat_pre_bridge_route_result.py` 加入 chat split module 边界扫描。

## 验证计划

- 红灯：
  `python -B -m pytest -p no:cacheprovider tests/test_api_chat_pre_bridge_route_result_split.py tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable -v`
- 绿灯定向：
  `python -B -m pytest -p no:cacheprovider tests/test_api_chat_pre_bridge_route_result_split.py -v`
- 相邻回归：
  `python -B -m pytest -p no:cacheprovider tests/test_api_chat_pre_bridge_decision_split.py tests/test_api_chat_non_streaming_result_split.py tests/test_api.py::test_proxy_chat_persists_private_timing_scoring_meta tests/test_api.py::test_proxy_chat_no_reply_persists_private_timing_scoring_meta -v`
- 静态检查：
  `python -m compileall api/routes.py api/chat_pre_bridge_route_result.py -q`
- 文档 / diff：
  `git diff --check -- api/routes.py api/chat_pre_bridge_route_result.py tests/test_api_chat_pre_bridge_route_result_split.py tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py tests/test_api_history_log_routes_split.py tests/test_api_sticker_media_routes_split.py`
- 全量：
  `env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY python -B -m pytest -p no:cacheprovider tests/ -v`

## 风险控制

- 新模块不导入父模块，不持有全局状态。
- 新模块不直接写 DB，只通过父模块 callback 按旧参数调用。
- 新模块不直接构造 FastAPI response，只返回 payload dict。
- `guardrail_silent` 分支继续使用 `persist_req`。
- 父模块保留 `_resolve_chat_pre_bridge_decision()`、`_persist_chat_turn()`、`_chat_response_payload()`、`_clone_chat_request()` 和 `_finalize_private_buffer()` patch point。
- 不改变 Prompt Runtime 输入、conversation 结构、工具输出契约、message envelope、push envelope 或 response envelope。

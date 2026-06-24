# 普通 API Chat Runtime Route Context 拆分设计

日期：2026-06-24

## 背景

`docs/todo.md` 的 P3 超大文件拆分队列当前只剩普通 `api/routes.py`，文件为 1013 行。前序阶段已把 `/chat` 周边的 request contract、response contract、persistence、runtime facade、guardrail facade、streaming helper、streaming result、SSE loop、non-streaming result、private buffer、push envelope、persona context、persona lookup、media precache、user block rules、private pre-bridge decision 和 pre-bridge route result 拆出。

`proxy_chat()` 里仍保留一段 runtime payload route context 组装逻辑：先基于 `final_query` / `final_files` 构造 `safe_user_input`，私聊时调用 `PersonaInjectionService` 生成动态 persona context，然后调用 `chat_runtime_facade.build_chat_runtime_payload()`，展开 `safe_user_input`、`enriched_query`、`bridge_meta`、`platform` 和 `prompt_budget`，最后按 injection / normal 分支记录 Prompt budget 日志。这段逻辑位于 pre-bridge route result 之后、Bridge 获取和 `_do_chat()` 之前，职责是 route 层运行时上下文准备，而不是 Prompt Runtime payload 的纯字段构造。

本阶段目标是把这段 route context 准备拆到独立 helper，继续缩短 `api/routes.py`，同时保留既有 `chat_runtime_facade` 的 payload 合同、父模块 patch point、Prompt Runtime 模板变量和 Bridge / SSE / 落库边界。

## 只读审查结论

- `api/chat_runtime_facade.py` 已负责纯 payload 构造和非流式 Bridge 小包装；它不应重复接收 DB session、logger 或 `PersonaInjectionService`。
- 当前父模块 runtime route context 区段约 60 行，包含动态 persona injection、`_empty_effort_constraint`、`ChatRuntimeInput` 构造、payload 展开和日志分支。
- 只拆 Prompt budget 日志风险最低，但收益太小，不能明显推进 `<800` 行目标。
- 直接拆 Bridge invocation 或 `_stream_chat()` 会触碰 `get_bridge()` patch point、async generator、bounded queue、断连后台 push 和非流式错误路径，风险高于本阶段目标。
- 拆 runtime route context 可以承接前序 runtime facade，行数收益中等，风险集中在 payload 字段和 Prompt Runtime 模板同步核查，适合作为第二十九刀。

## 方案选择

### 方案 A：新增 `api/chat_runtime_route_context.py`（推荐）

新模块负责 route 层 runtime context 准备：

- 先调用注入的 `build_multimodal_user_input_text` 得到 `safe_user_input`。
- 私聊时通过注入的 `build_persona_context` callback 执行动动态 persona injection，并返回更新后的 `persona_text` / `ctx_debug`。
- 调用既有 `chat_runtime_facade.build_chat_runtime_payload()` 构造 payload。
- 展开 `safe_user_input`、`enriched_query`、`bridge_meta`、`platform`、`prompt_budget` 和 `injection_mode`。
- 通过注入的 logger 记录与父模块一致的 Prompt budget 日志。

优点：

- 不重写 `chat_runtime_facade` 的 payload 细节，只移动 route 层编排。
- 不碰 `get_bridge()`、`_do_chat()`、`_stream_chat()`、SSE、non-streaming result、response envelope 或落库。
- 可用 fake callbacks 单元测试 persona injection 成功 / 失败、private / group、injection 日志和父模块 patch point。
- 预计能从 `api/routes.py` 移走约 35-45 行。

风险：

- `safe_user_input` 需要先给 `PersonaInjectionService` 使用，再由 `chat_runtime_facade` 重新计算并成为最终 `runtime_payload.safe_user_input`。如果回调不一致，会改变 persona context 输入或 `bridge_meta["raw_query"]`。
- `_ctx_debug.update(persona_result.debug)` 虽然后续当前不消费，也需要保持副作用语义。
- Prompt budget 日志字段和 `chat_type` 推导必须保持一致。
- 因靠近 Prompt Runtime payload，必须在同阶段核查 `prompts.v2.default/chat/*`、`data/prompts_v2/chat/*`、`core/prompt_v2/variables.py` 和 `core/prompt_v2/template_registry.py`。如果字段名和语义不变，记录无需改模板；如果变更任一字段或标记，必须同步模板。

### 方案 B：只拆 Prompt budget 日志

新增小 helper，只负责 normal / injection 两个日志分支。

优点是风险最低。缺点是行数收益很小，`proxy_chat()` 仍保留动态 persona injection 和 payload 展开，不能明显降低职责密度。

结论：暂缓，除非方案 A 在测试设计中暴露风险过高。

### 方案 C：拆 Bridge invocation context

把 `get_bridge()`、`_do_chat()` 和 stream / non-stream 前置 context 构造一起迁走。

优点是行数收益更大。缺点是会触碰父模块 `get_bridge` monkeypatch、stream queue、runner task、断连后台 push 和非流式错误路径，容易扩大回归面。

结论：暂缓到 route context 和 streaming 边界进一步收敛之后。

## 目标

- 新增 `api/chat_runtime_route_context.py`。
- 新模块定义：
  - `ChatRuntimeRouteServices`
  - `ChatRuntimeRouteInput`
  - `ChatRuntimeRouteContext`
  - `build_chat_runtime_route_context()`
- 父模块新增薄 wrapper：
  - `_chat_runtime_route_services()`
  - `_build_chat_runtime_route_context()`
- 父模块 `proxy_chat()` 用 wrapper 替换内联 runtime route context 组装。
- 保留：
  - `api.routes.proxy_chat.__module__ == "api.routes"`。
  - `/api/v1/chat` route 继续由 `api.routes` 注册。
  - `_build_multimodal_user_input_text()` patch point。
  - `_estimate_tokens()` patch point。
  - `_chat_request_platform()` patch point。
  - `get_effort_constraint()` patch point。
  - `PersonaInjectionService` 的 DB session、`user_id`、`current_user_input` 和 `recent_messages` 入参语义。
  - `release_clean_session_transaction(db, label="chat_before_bridge", logger=logger)` 仍留在父模块，且仍在 runtime route context 构建后、Bridge 调用前执行。
  - `bridge_meta` 字段名和 `<user_input>` 包裹语义。
  - Prompt budget 日志字段和 injection 日志字段。

## 非目标

- 不迁移 `/chat` route 或 `proxy_chat()` 本体。
- 不迁移用户 upsert、blocked user silent、图片预缓存、persona snapshot lookup、history 构建、pre-bridge decision 或 pre-bridge route result。
- 不修改 `api/chat_runtime_facade.py` 的 payload 字段语义。
- 不迁移 `get_bridge()`、`_do_chat()`、`_stream_chat()`、SSE、message envelope、push envelope、non-streaming result、evolution 或错误处理路径。
- 不改变 `enriched_query`、conversation 结构、工具输出契约、Prompt Runtime 模板变量、message envelope、push envelope 或 response envelope。
- 不新增 `asyncio.run()`、`run_awaitable_sync` 或同步函数包装 awaitable。
- 不处理 WebUI / JS。

## 新模块设计

新文件：`api/chat_runtime_route_context.py`

### `ChatRuntimeRouteServices`

职责：承载父模块注入的 route 层 patch point。

字段：

- `build_multimodal_user_input_text`
- `estimate_tokens`
- `get_effort_constraint`
- `chat_request_platform`
- `build_runtime_payload`
- `build_persona_context`
- `logger`

`build_persona_context` 由父模块 wrapper 提供，内部仍调用 `PersonaInjectionService(db).build_context()`，让新模块不直接导入 DB model 或父模块。

### `ChatRuntimeRouteInput`

职责：收拢 `proxy_chat()` 已经计算出的 runtime context 入参。

字段：

- `req`
- `final_query`
- `final_files`
- `persona_text`
- `history_messages`
- `memory_header`
- `ctx_debug`
- `is_group`
- `is_superuser`
- `private_decision`
- `guardrail_status`
- `classifier_ran`

### `ChatRuntimeRouteContext`

职责：返回父模块继续调用 Bridge、SSE 和 result finalizer 所需变量。

字段：

- `safe_user_input`
- `enriched_query`
- `bridge_meta`
- `platform`
- `prompt_budget`
- `persona_text`
- `ctx_debug`
- `injection_mode`

### `build_chat_runtime_route_context()`

接口：

```python
def build_chat_runtime_route_context(
    runtime_input: ChatRuntimeRouteInput,
    *,
    services: ChatRuntimeRouteServices,
) -> ChatRuntimeRouteContext:
    return context
```

行为：

1. 用 `services.build_multimodal_user_input_text()` 生成 `safe_user_input`，供动态 persona injection 使用。
2. 若 `runtime_input.is_group` 为 `False`，调用 `services.build_persona_context()`：
   - 入参是 `req.user_id`、`safe_user_input` 和 `history_messages`。
   - 成功时合并 `ctx_debug`，并在 `context` 非空时覆盖 `persona_text`。
   - 异常时记录与父模块一致的 warning，继续使用原 `persona_text`。
3. 根据 `private_decision` 选择 `get_effort_constraint` 或空约束函数。
4. 调用 `services.build_runtime_payload()`，实际指向 `chat_runtime_facade.build_chat_runtime_payload()`。
5. 读取 payload 的 `safe_user_input`、`enriched_query`、`bridge_meta`、`prompt_budget`、`injection_mode`。
6. 按原逻辑记录 Prompt budget normal / injection 日志。
7. 返回 `ChatRuntimeRouteContext`。

## 父模块接入

`api.routes` 保留：

- `release_clean_session_transaction(db, label="chat_before_bridge", logger=logger)`
- `get_bridge()`
- `_do_chat()`
- `_stream_chat()`
- non-streaming result finalizer
- 所有 SSE、push、落库、evolution 逻辑

新增 `_build_persona_injection_context()` wrapper：

- 调用 `PersonaInjectionService(db).build_context()`。
- `__module__` 保持 `api.routes`，便于测试和 monkeypatch。

新增 `_chat_runtime_route_services(db)`：

- 返回 `ChatRuntimeRouteServices`。
- 通过闭包把当前 `db` 绑定给 `_build_persona_injection_context()`。

新增 `_build_chat_runtime_route_context()`：

- 调用 `api.chat_runtime_route_context.build_chat_runtime_route_context()`。
- 父模块 `proxy_chat()` 从返回对象展开 `safe_user_input`、`enriched_query`、`bridge_meta`、`platform` 和 `prompt_budget`。

## 测试策略

新增 `tests/test_api_chat_runtime_route_context_split.py`。

覆盖：

1. 新模块源码边界：
   - 不导入 `api.routes`。
   - 不导入 FastAPI、`APIRouter`、`StreamingResponse`、`BackgroundTasks` 或 `HTTPException`。
   - 不导入 `SessionLocal`、DB model、Bridge、`get_bridge()` 或 Prompt Runtime 模板注册。
   - 不调用 `asyncio.run` 或 `run_awaitable_sync`。
2. group chat 不调用 persona injection：
   - `is_group=True` 时仍构造 runtime payload。
   - `build_persona_context` 不被调用。
   - `bridge_meta["chat_type"] == "group"`，`platform` 来自 request callback。
3. private chat persona injection 成功：
   - `build_persona_context` 收到 `safe_user_input` 和 `history_messages`。
   - `ctx_debug` 合并 persona debug。
   - 非空 `context` 覆盖 `persona_text` 并进入 `bridge_meta["persona_text"]`。
4. private chat persona injection 异常：
   - 记录 warning。
   - 保留原 `persona_text`。
   - 仍构造 runtime payload。
5. injection guardrail 日志：
   - `classifier_ran=True` 且 `guardrail_status="injection"` 时，`injection_mode=True`。
   - 日志使用原 injection 分支字段。
6. 父模块 wrapper patch point：
   - `_chat_runtime_route_services.__module__ == "api.routes"`。
   - `_build_chat_runtime_route_context.__module__ == "api.routes"`。
   - services 的 callbacks 指向父模块 patch point 或闭包包装后的父模块函数。

修改四个 chat split module 扫描测试，把 `api/chat_runtime_route_context.py` 加入清单：

- `tests/test_api_group_message_routes_split.py`
- `tests/test_api_agent_step_routes_split.py`
- `tests/test_api_history_log_routes_split.py`
- `tests/test_api_sticker_media_routes_split.py`

相邻回归：

- `tests/test_api_chat_runtime_facade_split.py`
- `tests/test_api_chat_pre_bridge_route_result_split.py`
- `tests/test_api.py::test_proxy_chat_passes_history_header_to_bridge`
- `tests/test_api.py::test_proxy_chat_passes_client_platform_to_bridge`
- `tests/test_api.py::test_proxy_chat_releases_db_transaction_before_bridge`

## Prompt Runtime 模板核查

本阶段预期只移动 route 层组装，不改变下列字段和标记：

- `<user_input>` 包裹。
- `bridge_meta["persona_text"]`
- `bridge_meta["raw_query"]`
- `bridge_meta["history_header"]`
- `bridge_meta["history_messages"]`
- `bridge_meta["effort_constraint"]`
- `bridge_meta["runtime_preset"]`
- `bridge_meta["platform"]`
- `bridge_meta["chat_type"]`
- `bridge_meta["stream"]`

实现阶段必须运行：

```bash
rg -n "persona_text|raw_query|history_header|history_messages|effort_constraint|runtime_preset|<user_input>|platform|chat_type|stream" prompts.v2.default/chat data/prompts_v2/chat core/prompt_v2/variables.py core/prompt_v2/template_registry.py nanobot_kt/bridge.py
```

如果核查证明字段名、变量语义和模板标记未改变，则不修改模板，只在计划和 walkthrough 记录结果。如果实现改变任何字段名、变量语义、模板标记、`enriched_query` 包裹方式或 audit 行为，必须在同一阶段同步更新默认模板与 `data/prompts_v2/` 运行时模板，并补测试。

## 验收标准

- `api/routes.py` 行数继续下降。
- `api/chat_runtime_route_context.py` 不导入父模块、FastAPI、Bridge、Prompt Runtime 模板注册或 DB 全局入口。
- 父模块 patch point 保持可 monkeypatch。
- group chat 不触发 dynamic persona injection。
- private chat dynamic persona injection 的入参、debug 合并和 fallback 行为保持不变。
- `bridge_meta` 字段名和 `<user_input>` 包裹语义保持不变。
- Prompt budget normal / injection 日志字段保持不变。
- 带 `label="chat_before_bridge"` 参数的 `release_clean_session_transaction()` 仍在 Bridge 调用前执行。
- 定向测试、相邻回归、静态检查和全量测试通过。
- 不新增 `asyncio.run()`、`run_awaitable_sync` 或同步函数包装 awaitable。

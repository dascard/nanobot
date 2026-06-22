# 普通 API History / Log 路由拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」当前仍只剩 `api/routes.py`。
普通 API 已完成 task、memory、models 和 evolution route-only 四刀拆分，
`api/routes.py` 当前为 2469 行。下一刀需要继续避开 `/chat` 与
`/group/message` 主链路，同时选择行数收益足够明显、职责边界清楚的普通 API
路由集合。

本阶段选择 history / log route-only。它覆盖普通 HTTP 层的历史管理、上下文快照、
原始日志写入和本地日志检索，但明确不迁移 `/chat` 主流程的双表落库 helper。

## 候选方案

### 方案 A：History / Log 路由拆分（推荐）

新增 `api/history_log_routes.py`，迁移：

- `LogRequest`
- `AmbientLogRequest`
- `POST /chat/mark-clear`
- `GET /chat/history-summary`
- `POST /chat/compact-history`
- `GET /context`
- `POST /log`
- `POST /log_ambient`
- `GET /search_logs`

收益约 260-330 行，职责围绕 `ChatLog` / `ConversationTurn` 的外部管理和检索。
主要风险是 `/log` 的 evolution 阈值触发、`mark_clear()` 的 rolling summary
归档、`/log_ambient` 的群 ID 兼容，以及旧 `api.routes` 导入兼容。风险可通过
split 契约测试和现有行为回归锁住。

### 方案 B：Media 路由拆分

新增 `api/media_routes.py`，迁移 sticker 注册 / 搜索 / 禁用与公开图片代理：

- `StickerRegisterRequest`
- `POST /stickers/register`
- `GET /stickers/search`
- `GET /stickers/{sticker_id}/image`
- `GET /generated-images/{image_id}/image`
- `POST /stickers/{sticker_id}/disable`

收益约 140-155 行，风险更低；主要风险是公开图片 query token、`/stickers/search`
静态路由顺序和旧导入兼容。它适合作为后续下一刀，但本阶段优先选择收益更高且仍
可控的 history / log。

### 方案 C：Agent Step / Render 路由拆分

新增 `api/agent_step_routes.py`，迁移 `/chat-step` 和遗留 `/render`。风险最低，
因为核心协议在 `core.agent_step`，路由层只是 SSE 包装和返回转换；但收益约 30 行，
不足以作为当前优先项。

## 目标

新增 `api/history_log_routes.py`，并从 `api/routes.py` 迁移 history / log HTTP 层。
完成后必须满足：

- 路径、方法、鉴权、请求参数、响应结构和数据库写入语义不变。
- `api.history_log_routes.router` 不带 `/api/v1` 前缀，由父 `api.routes.router`
  include。
- `api.routes` 继续 re-export：
  - `LogRequest`
  - `AmbientLogRequest`
  - `mark_clear`
  - `get_history_summary`
  - `compact_history`
  - `get_context`
  - `submit_log`
  - `submit_ambient_log`
  - `search_history_logs`
- `api.history_log_routes` 使用 `api.common_auth.verify_token`，保持
  `api.routes.NANOBOT_API_TOKEN` monkeypatch 兼容。
- `api.history_log_routes` 不导入 `api.routes`，避免循环导入。
- `submit_log()` 继续只写 `ChatLog(processed=0)`，达到 `EVOLUTION_THRESHOLD`
  后通过 `BackgroundTasks.add_task(evolution_task, user_id)` 排队。
- `submit_ambient_log()` 继续写 `ChatLog(role="ambient", processed=1)`，并继续通过
  `normalize_group_session_id()` 处理群 ID。
- `mark_clear()` 继续只删除 `ConversationTurn`，并归档 active rolling summary；
  `ChatLog` 保留不删。
- `compact_history()` 继续只压缩 `ConversationTurn`，不触碰 `ChatLog`。
- `get_context()` 继续查询 `Persona`、`SystemPrompt` 和最近 `ChatLog`，并调用既有
  `run_autocompact_circuit_breaker()`。
- `search_history_logs()` 继续支持 `limit <= 200`、`context_size <= 20`、`all`
  查询、精确 user / session 命中、sender / session name 模糊兜底、LIKE 通配符转义
  和同 session 上下文展开。
- `api.routes` 继续保留：
  - `_persist_chat_turn()`
  - `_safe_meta()`
  - `ChatProxyRequest`
  - `memory`
  - `init_legacy_memory()`
  - `evolution_task`
  - `EVOLUTION_THRESHOLD`
  - `/chat`
  - `/group/message`
  - `/health`
- 不新增 `asyncio.run()`，不新增 `run_awaitable_sync`，不新增同步函数包装 awaitable。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query`、conversation
  结构、message envelope 或工具输出契约。

## 模块边界

### `api/history_log_routes.py`

职责：

- 定义 `router = APIRouter(tags=["history-log"])`。
- 定义 `LogRequest` 和 `AmbientLogRequest`。
- 定义 history / log 路由 endpoint。
- 保留 `search_history_logs()` 的 `_like_contains()` 作为模块私有 helper 或函数内
  局部 helper。

依赖：

- `logging`
- `datetime.datetime`
- `fastapi.APIRouter`
- `fastapi.BackgroundTasks`
- `fastapi.Depends`
- `fastapi.HTTPException`
- `fastapi.Query`
- `pydantic.BaseModel`
- `sqlalchemy.or_`
- `sqlalchemy.orm.Session`
- `api.common_auth.verify_token`
- `config.EVOLUTION_THRESHOLD`
- `core.compaction.run_autocompact_circuit_breaker`
- `core.database.ChatLog`
- `core.database.ConversationTurn`
- `core.database.Persona`
- `core.database.SystemPrompt`
- `core.database.User`
- `core.database.get_db`
- `core.evolution.evolution_task`
- `core.group_runtime.ids.normalize_group_session_id`
- `core.sqlite_retry.run_sqlite_locked_retry`

兼容策略：

- 子模块直接导入 `core.evolution.evolution_task`。拆分后手动 `/log` 的 patch target
  是 `api.history_log_routes.evolution_task`；`api.routes.evolution_task` 继续用于
  `/chat` 自动触发和旧父模块边界。
- `mark_clear()` 内继续懒加载
  `app.session_memory.rolling_summary.archive_active_summaries_for_user`，避免改变导入时副作用。
- `get_context()` 内继续同步调用 `run_autocompact_circuit_breaker()`；这是既有行为，
  本刀不引入 async wrapper。

禁止：

- 不导入 `api.routes`。
- 不迁移 `_persist_chat_turn()`。
- 不迁移 `_safe_meta()`。
- 不迁移 `/chat` 或 `/group/message`。
- 不迁移 legacy `memory` / `init_legacy_memory()`。

### `api/routes.py`

职责：

- 继续作为 `/api/v1` 聚合 router。
- 从 `api.history_log_routes` import `router as history_log_router` 和旧导入兼容符号。
- 删除本地 `LogRequest`、`AmbientLogRequest` 和迁移 endpoint 实现。
- 在尾部普通子路由 include 区加入：

```python
router.include_router(history_log_router)
```

推荐 include 顺序：

```python
router.include_router(history_log_router)
router.include_router(evolution_router)
router.include_router(memory_router)
router.include_router(model_router)
router.include_router(task_router)
```

这样 history / log 路由进入普通子路由区域，`/health` 继续作为父模块尾部健康检查。
这些路径没有动态路由冲突；仍需测试确认没有重复注册。

## 测试策略

新增 `tests/test_api_history_log_routes_split.py`，锁定结构和关键行为契约：

- `test_api_history_log_routes_are_registered_from_split_module`
  - 断言 7 个 endpoint 的 `endpoint.__module__ == "api.history_log_routes"`。
- `test_legacy_api_routes_history_log_imports_still_work`
  - 断言 `api.routes` 与 `api.history_log_routes` re-export 同一对象。
  - 实例化 `LogRequest` 和 `AmbientLogRequest`。
- `test_split_history_log_routes_use_legacy_api_token_monkeypatch`
  - monkeypatch `api.routes.NANOBOT_API_TOKEN`。
  - 正确 Bearer 能访问一个只读 endpoint，错误 Bearer 返回 401。
- `test_api_history_log_routes_are_not_registered_twice`
  - 断言每个 endpoint 只注册一次。
- `test_api_history_log_routes_do_not_import_parent_routes_or_sync_awaitable`
  - 扫描 `api/history_log_routes.py`，断言没有 `from api.routes`、`import api.routes`、
    `asyncio.run`、`run_awaitable_sync`。
- `test_chat_persistence_helpers_stay_in_parent_routes`
  - 断言 `api.routes._persist_chat_turn.__module__ == "api.routes"`。
  - 断言 `api.routes._safe_meta.__module__ == "api.routes"`。
  - 断言 `api.routes.init_legacy_memory.__module__ == "api.routes"`。
- `test_submit_log_keeps_background_evolution_boundary`
  - 直接调用 `api.history_log_routes.submit_log()`，锁定只写 `ChatLog`、返回
    `unprocessed_logs`，达到阈值后向 `BackgroundTasks` 排队
    `api.history_log_routes.evolution_task`。
- `test_ambient_log_keeps_group_session_and_processed_contract`
  - 直接调用 `submit_ambient_log()`，锁定 `group_*` session、`role="ambient"`、
    `processed=1`、`session_name` 写入或更新。

同步调整现有测试：

- `tests/test_api.py`
  - 保留 `/search_logs` 的 422 和 LIKE 转义行为测试。
  - 保留 history 管理 endpoint 不泄漏内部错误的测试，旧导入路径仍从 `api.routes`
    可用。
  - 如果有直接 patch `api.routes.evolution_task` 验证 `/log`，改为 patch
    `api.history_log_routes.evolution_task`。
- `tests/test_tracing_sqlite_retry.py`
  - `/log` retry 覆盖可以直接导入 `api.history_log_routes.LogRequest` 和
    `submit_log()`；另由 split 测试锁定旧 `api.routes` re-export。
- `tests/test_api_memory_routes_split.py`、`tests/test_api_model_routes_split.py`、
  `tests/test_api_evolution_routes_split.py`
  - 如这些测试仍把某些 history / log endpoint 视为父模块 endpoint，同步移除；
    当前父模块尾部列表只应继续保留 `/health`。

定向验证：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_history_log_routes_split.py
python -B -m pytest -q -p no:cacheprovider tests/test_api_history_log_routes_split.py tests/test_api.py::test_search_logs_rejects_limit_above_max tests/test_api.py::test_search_logs_rejects_invalid_context_size tests/test_api.py::test_search_logs_keyword_escapes_like_wildcards tests/test_api.py::test_search_logs_user_id_fuzzy_escapes_like_wildcards tests/test_api.py::test_chat_management_endpoints_do_not_echo_internal_errors tests/test_tracing_sqlite_retry.py::test_submit_log_retries_sqlite_locked_commit
python -B -m pytest -q -p no:cacheprovider tests/test_api_task_routes_split.py tests/test_api_memory_routes_split.py tests/test_api_model_routes_split.py tests/test_api_evolution_routes_split.py tests/test_asyncio_run_policy.py tests/test_audit_fixes.py::TestLazyControllerInit::test_legacy_memory_init_exists
```

静态验证：

```bash
python -B -m compileall api/routes.py api/history_log_routes.py
rg -n "from api\\.routes|import api\\.routes|asyncio\\.run|run_awaitable_sync" api/history_log_routes.py
git diff --check -- api/routes.py api/history_log_routes.py tests/test_api_history_log_routes_split.py tests/test_api.py tests/test_tracing_sqlite_retry.py
```

全量验证：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

## 非目标

- 不拆 `/chat`。
- 不拆 `/group/message`。
- 不拆 group timing。
- 不拆 sticker / generated image media 路由。
- 不拆 `/chat-step` 或 `/render`。
- 不迁移 `_persist_chat_turn()`。
- 不迁移 `_safe_meta()`。
- 不迁移 `ChatProxyRequest`、私聊缓冲、guardrail、bridge 调用或 SSE 流式逻辑。
- 不迁移 `memory`、`SQLiteMemory` 或 `init_legacy_memory()`。
- 不改变 `core.evolution`、`core.compaction` 或 `core.context_builder`。
- 不修改 `server.py` 或 `bootstrap/lifespan.py`。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query`、conversation
  结构、message envelope 或工具输出契约。
- 不做 ruff 批量清理。

## 子 agent 分工约定

主线程负责最终编辑、验证和提交。写入阶段不要让多个 agent 同时修改 `api/routes.py`。

- **Worker A：测试文件。** 只允许创建 `tests/test_api_history_log_routes_split.py`，
  并按需修改 `tests/test_api.py`、`tests/test_tracing_sqlite_retry.py` 中与 history /
  log 拆分直接相关的断言。
- **Worker B：history / log 路由迁移。** 只允许创建 `api/history_log_routes.py`，
  并修改 `api/routes.py` 的 import、include 和旧本地 history / log 区块。
- **Reviewer：验证审查。** 只读检查 diff、route module、重复注册、反向导入、
  鉴权 monkeypatch、`/log` evolution 排队边界、`_persist_chat_turn()` 留父模块、
  行数和测试输出。

接口约定：

- `api.history_log_routes.router` 不带 `/api/v1` 前缀，由父 `api.routes.router`
  include。
- `api.history_log_routes` 使用 `api.common_auth.verify_token`，不导入父模块。
- `api.routes` 必须 re-export 迁移后的 request model 和 endpoint 函数。
- `/log` 手动日志入口 patch target 是 `api.history_log_routes.evolution_task`。
- `/chat` 自动触发 patch target 仍是 `api.routes.evolution_task`。
- `_persist_chat_turn()`、`_safe_meta()`、`init_legacy_memory()` 继续留在 `api.routes`。

## 验收标准

- 新增 split 契约测试先红后绿。
- `tests/test_api_history_log_routes_split.py` 全部通过。
- `/search_logs` limit / context_size / LIKE 转义回归通过。
- `/log` SQLite retry 回归通过。
- chat management endpoint 错误不泄漏内部路径的回归通过。
- 现有普通 API split 测试通过。
- `tests/test_asyncio_run_policy.py` 通过。
- `api/history_log_routes.py` 无 `from api.routes`、无 `import api.routes`、无
  `asyncio.run`、无 `run_awaitable_sync`。
- `api.routes._persist_chat_turn` 仍在父模块。
- `api.routes._safe_meta` 仍在父模块。
- `api.routes.init_legacy_memory` 仍在父模块。
- `/health` 仍留在 `api.routes`。
- `api/routes.py` 行数继续下降，预期进入约 2150-2210 行区间。
- 全量 `tests/` 回归 0 failures。
- 每个阶段性改动都有独立 commit。

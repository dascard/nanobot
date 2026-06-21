# 普通 API Tasks 路由拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」当前只剩
`api/routes.py`。上一阶段已经把 `api/admin_routes.py` 降到 632 行并移出
超大文件队列；普通 API 仍为 2822 行，是后续可维护性收敛的主目标。

普通 API 与管理端拆分的关键差异在鉴权兼容面。现有测试和调用方会通过
`api.routes.verify_token` 做依赖覆盖，也会 monkeypatch
`api.routes.NANOBOT_API_TOKEN` 后直接调用 `routes.verify_token()`。如果新子路由
使用另一个函数对象作为 `Depends()` 依赖，`app.dependency_overrides[routes.verify_token]`
不会覆盖拆分后的端点，测试和旧调用都会失效。

因此普通 API 拆分第一步必须先抽出共享鉴权层，再迁移低耦合路由。两路只读
子 agent 审计均确认 `/tasks*` 是最适合作为第一刀的边界：它只依赖数据库、
`core.daily_digest` 和响应信封，不进入 `/chat`、`/group/message`、KT Bridge、
私聊缓冲或 TimingGate 主链路。

## 现状证据

- `server.py` 只 include `api.routes.router`，普通 API 的聚合入口必须保持不变。
- `tests/conftest.py` 通过 `app.dependency_overrides[routes.verify_token] = lambda: None`
  关闭普通 API 鉴权。
- `tests/test_api.py` 直接 monkeypatch `api.routes.NANOBOT_API_TOKEN` 并调用
  `routes.verify_token()` 校验 503、401 和成功路径。
- `api/routes.py` 中 `/tasks*` 当前位于文件末尾，范围为 `ScheduledTaskCreate`
  和 `create/list/update/toggle/run/delete` 六个端点。
- `run_scheduled_task_now()` 当前是 `async def`，内部 `await _generate_task_message()`
  和 `await push_envelope_to_qq()`，拆分后必须保持协程边界。
- `/tasks` 静态 collection route 与 `/tasks/{task_id}` 动态 route 共用前缀，
  拆分测试需要锁定注册顺序和不重复注册。

## 候选方案

### 方案 A：只抽 `api.common_auth`

新增 `api/common_auth.py`，把普通 API 的 `verify_token()` 下沉到共享模块，
`api.routes` 只 re-export 同一个函数对象。

优点：

- 立即解除普通 API 后续拆分的最大 blocker。
- 风险集中在认证兼容，验证范围小。

风险：

- 行数收益很小，`api/routes.py` 仍接近原始规模。
- P3 拆分进度不明显，还需要下一刀才能减少业务代码。

结论：作为本阶段任务 1，但不能单独作为本阶段终点。

### 方案 B：抽 common auth 后拆 `/tasks*`（采用）

先新增普通 API common auth，再把 `ScheduledTaskCreate` 和 `/tasks*` 端点迁移到
`api/task_routes.py`。父模块 `api.routes` 继续 include 子 router，并 re-export
迁移后的 request model 和 endpoint。

优点：

- 避开 `/chat` 和 `/group/message` 的高耦合 monkeypatch 面。
- `/tasks*` 路由依赖集中，没有私聊 buffer、Bridge、guardrail、`app.state` 或全局
  session 状态。
- `run_scheduled_task_now()` 已有行为测试覆盖 push envelope，可以作为定向回归锚点。
- 形成普通 API 后续拆分模板：common auth、父模块聚合、旧导入兼容、route 顺序测试。

风险：

- 行数收益约百行，`api/routes.py` 仍会超过 800 行，需要后续继续拆 memory/model、
  sticker/media、history/log 等边界。
- 如果子模块反向导入 `api.routes.verify_token`，会制造循环导入；必须从
  `api.common_auth` 导入同一函数对象。

结论：采用。

### 方案 C：拆 evolution / memory / models

范围为 `/evolution/trigger`、`/memory/digests`、`/memory/digests/run`、
`/models/list`、`/models/sync` 和 `/memory/recall`。

优点：

- 行数收益比 `/tasks*` 更高。
- 依赖主要是 registry、memory retrieval service 和 daily digest，整体仍比
  `/chat` 主链路低风险。

风险：

- `/memory/recall` 有日期过滤、AI daily tool log 召回和返回结构兼容要求。
- 需要迁移 `EvolutionTriggerRequest`、`MemoryDigestRunRequest`、`ModelSyncRequest`
  以及记忆 helper，第一刀范围更宽。

结论：作为 `/tasks*` 之后的下一候选。

### 方案 D：拆 sticker / media

范围为 `/stickers/register`、`/stickers/search`、`/stickers/{sticker_id}/image`、
`/generated-images/{image_id}/image` 和 `/stickers/{sticker_id}/disable`。

优点：

- 业务边界清楚，能迁出公开 media 与 sticker HTTP 层。

风险：

- `register_sticker_endpoint()` 涉及 `BackgroundTasks`、自动描述、缓存与群聊贴纸行为。
- `/stickers/search` 必须继续早于 `/stickers/{sticker_id}/image`。

结论：保留为后续候选，不作为第一刀。

## 目标

本阶段交付两个紧密相连的改动：

1. 新增普通 API 鉴权共享模块 `api/common_auth.py`。
2. 新增普通 API task 子路由模块 `api/task_routes.py` 并迁移 `/tasks*`。

完成后必须满足：

- `api.routes.verify_token is api.common_auth.verify_token`。
- `api.common_auth.verify_token()` 先读取 `sys.modules["api.routes"].NANOBOT_API_TOKEN`
  以兼容旧 monkeypatch；没有父模块 patch 时回退到 `config.NANOBOT_API_TOKEN`。
- `api.routes.NANOBOT_API_TOKEN` 仍存在，旧测试和调用方可以继续 monkeypatch。
- `app.dependency_overrides[routes.verify_token]` 继续覆盖拆分后的 `/tasks*` 端点。
- `/api/v1/tasks`、`/api/v1/tasks/{task_id}`、`/api/v1/tasks/{task_id}/toggle`、
  `/api/v1/tasks/{task_id}/run` 的 HTTP path、method、status code 和 response shape
  不变。
- `ScheduledTaskCreate` 和六个 task endpoint 继续可从 `api.routes` 旧路径导入，
  且对象身份等于 `api.task_routes` 中的定义。
- `run_scheduled_task_now()` 继续是 coroutine function，不用同步函数包 awaitable。
- 新模块不反向导入 `api.routes`，不新增 `asyncio.run()`，不新增 `run_awaitable_sync`。
- `server.py` 不改 include 入口，仍只 include `api.routes.router`。

## 模块边界

### `api/common_auth.py`

职责：

- 提供 `_current_api_token()`。
- 提供 `verify_token()`。
- 只依赖 `sys`、`hmac.compare_digest`、`fastapi.Header`、`fastapi.HTTPException` 和
  `config.NANOBOT_API_TOKEN`。

兼容策略：

- `_current_api_token()` 优先检查已加载的 `api.routes` 模块。
- 如果 `api.routes` 存在且有 `NANOBOT_API_TOKEN` 属性，使用该值。
- 否则使用导入时从 `config` 读取的 token。
- `verify_token()` 的 503、401 和成功返回行为保持不变。

### `api/task_routes.py`

职责：

- 定义 `ScheduledTaskCreate`。
- 定义 `/tasks*` 六个 endpoint。
- 持有自己的 `router = APIRouter(tags=["tasks"])`。
- 从 `api.common_auth` 导入 `verify_token`。
- 从 `core.database` 导入 `get_db`，在函数内部继续按需导入 `ScheduledTask`。
- `run_scheduled_task_now()` 继续在函数内部导入 `_generate_task_message`、
  `push_envelope_to_qq` 和 `build_chat_response_envelope`，保持原懒加载行为。

禁止：

- 不导入 `api.routes`。
- 不改 `ScheduledTask` schema。
- 不改 prompt template 净化策略。
- 不改 push envelope 语义。
- 不把 `run_scheduled_task_now()` 改成同步函数。

### `api/routes.py`

职责：

- 继续作为 `/api/v1` 聚合 router。
- 从 `api.common_auth` import `verify_token`，删除本地函数体。
- 从 `api.task_routes` import task router、request model 和 endpoint，用于 include
  和旧导入兼容。
- 在原 `/tasks*` 位置 include `task_router`，让 route 顺序与旧文件一致。
- 保留 `/health` 在父模块，作为本阶段不迁移边界。

## 测试策略

新增 `tests/test_api_task_routes_split.py`，锁定结构契约：

- `test_api_verify_token_is_shared_common_auth_object`
- `test_api_common_auth_uses_legacy_api_routes_token_monkeypatch`
- `test_api_task_routes_are_registered_from_split_module`
- `test_legacy_api_routes_task_imports_still_work`
- `test_split_task_routes_use_legacy_api_token_monkeypatch`
- `test_api_task_routes_are_not_registered_twice`
- `test_api_task_collection_routes_precede_dynamic_task_routes`
- `test_api_task_async_boundaries_remain_coroutines`
- `test_api_task_routes_do_not_import_parent_routes_or_sync_awaitable`
- `test_health_check_stays_in_parent_routes`

定向行为回归：

- `tests/test_api_push_envelope.py::test_run_scheduled_task_now_uses_push_envelope`
- `tests/test_schedule_task_tool.py`
- `tests/test_api.py::test_api_auth_no_token_configured_returns_503`
- `tests/test_api.py::test_api_auth_missing_or_wrong_token_returns_401`
- `tests/test_api.py::test_api_auth_accepts_valid_bearer_token`
- `tests/test_asyncio_run_policy.py`

静态验证：

- `python -B -m compileall api/routes.py api/common_auth.py api/task_routes.py`
- `rg -n "from api\\.routes|import api\\.routes|asyncio\\.run|run_awaitable_sync" api/task_routes.py`
- `git diff --check -- api/routes.py api/common_auth.py api/task_routes.py tests/test_api_task_routes_split.py`

全量验证：

- `python -B -m pytest -p no:cacheprovider tests/ -v`

## 非目标

- 不拆 `/chat`。
- 不拆 `/group/message`。
- 不拆 history、context、log、sticker/media、group timing、search/render、agent step、
  evolution、memory 或 model 路由。
- 不迁移 `/health`。
- 不修改 `server.py`。
- 不改变数据库 schema。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query`、conversation 结构、
  prompt runtime 输入或工具输出契约。
- 不新增 `asyncio.run()`。
- 不新增 `run_awaitable_sync`。
- 不在同步函数中包装 awaitable。

## 后续拆分顺序

完成本阶段后，普通 API 下一刀建议按风险从低到高推进：

1. evolution / memory / models 运维查询边界。
2. sticker / generated image media 边界。
3. history / context / log 边界。
4. group timing legacy / timer 边界。
5. `/group/message`。
6. `/chat` 主链路。

`/chat` 和 `/group/message` 需要单独设计 monkeypatch facade，因为现有测试大量 patch
`api.routes.get_bridge`、`api.routes.get_guardrail`、`api.routes.asyncio.sleep`、
`api.routes._time.time`、`_private_buffers`、`PRIVATE_BUFFER_*` 和群消息 underscore helper。

# 普通 API Evolution 路由拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」当前仍只剩 `api/routes.py`。
普通 API 已完成 task、memory 和 models 三刀拆分，`api/routes.py` 当前为
2484 行。下一刀继续选择低耦合边界，目标是推进聚合路由瘦身，同时继续避开
`/chat` 与 `/group/message` 主链路。

本阶段选择 evolution route-only。两个只读审计结论一致：`/evolution/trigger`
本身只是手动触发 HTTP 层，可以拆出；但 `evolution_task`、`EVOLUTION_THRESHOLD`、
`init_legacy_memory()`、`memory` 和 `_persist_chat_turn()` 仍被父模块的 `/log`、
`/chat` 或启动生命周期直接依赖，不能在同一刀迁移。

## 现状证据

- `server.py` 只 include `api.routes.router`，普通 API 聚合入口不能改变。
- `api.routes.router` 使用 `/api/v1` 前缀；新子 router 不应自带 `/api/v1`。
- `api.common_auth.verify_token` 已兼容 `api.routes.NANOBOT_API_TOKEN`
  monkeypatch，子路由必须继续从 `api.common_auth` 导入鉴权依赖。
- 当前可迁移的手动 evolution HTTP 层只包含：
  - `EvolutionTriggerRequest`
  - `POST /evolution/trigger`
- `trigger_evolution()` 是同步函数，只记录日志、向 `BackgroundTasks` 排队
  `evolution_task(req.user_id)`，并返回：
  `{"status": "ok", "message": f"Evolution task queued for {req.user_id}"}`。
- `/log` 达到 `EVOLUTION_THRESHOLD` 后仍直接调用父模块 `evolution_task`。
- `/chat` 的流式和非流式成功路径仍直接调用父模块 `evolution_task`。
- `bootstrap/lifespan.py` 仍通过 `from api.routes import init_legacy_memory`
  初始化 legacy memory，现有兼容测试也锁定该符号存在。
- `core.evolution.evolution_task()` 自己实例化 `SQLiteMemory()`；`api.routes.memory`
  不是手动 `/evolution/trigger` 的运行依赖。
- `tests/test_api_memory_routes_split.py` 和 `tests/test_api_model_routes_split.py`
  当前仍把 `/evolution/trigger` 视为父模块尾部路由。拆分 evolution 后必须同步调整。

## 目标

新增 `api/evolution_routes.py`，并从 `api/routes.py` 迁移手动 evolution HTTP 层。
完成后必须满足：

- `POST /api/v1/evolution/trigger` 的 path、method、鉴权、请求体、状态码和 response
  shape 不变。
- `/evolution/trigger` 的 `endpoint.__module__` 变为 `api.evolution_routes`。
- `api.routes` 继续 re-export：
  - `EvolutionTriggerRequest`
  - `trigger_evolution`
- `trigger_evolution()` 继续是同步函数，只排队后台任务，不直接执行或 await
  `evolution_task()`。
- 手动 trigger 使用 `api.evolution_routes.evolution_task` 作为 patch target。
- `/log` 和 `/chat` 自动触发继续使用 `api.routes.evolution_task`，避免改变现有
  monkeypatch 与运行边界。
- `api.evolution_routes` 不导入 `api.routes`，避免循环导入。
- `api.routes` 继续保留：
  - `evolution_task`
  - `EVOLUTION_THRESHOLD`
  - `SQLiteMemory`
  - `memory`
  - `init_legacy_memory()`
  - `_persist_chat_turn()`
  - `/health`
- 不新增 `asyncio.run()`，不新增 `run_awaitable_sync`，不新增同步函数包装 awaitable。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query`、conversation
  结构或工具输出契约。

## 模块边界

### `api/evolution_routes.py`

职责：

- 定义 `router = APIRouter(tags=["evolution"])`。
- 定义 `EvolutionTriggerRequest`。
- 定义 `trigger_evolution()`。

依赖：

- `logging`
- `fastapi.APIRouter`
- `fastapi.BackgroundTasks`
- `fastapi.Depends`
- `pydantic.BaseModel`
- `api.common_auth.verify_token`
- `core.evolution.evolution_task`

兼容策略：

- 子模块独立导入 `core.evolution.evolution_task`，不从父模块转发。
- endpoint 保持同步函数，继续通过 `BackgroundTasks.add_task()` 排队。
- 日志名称可以使用 `nanobot.routes.evolution`，避免改变主 logger 的结构化语义。

禁止：

- 不导入 `api.routes`。
- 不迁移 `init_legacy_memory()` 或 `memory`。
- 不迁移 `/log` 或 `/chat` 的自动 evolution 触发逻辑。
- 不迁移 `EVOLUTION_THRESHOLD`。
- 不迁移 `core.evolution` 内部实现。

### `api/routes.py`

职责：

- 继续作为 `/api/v1` 聚合 router。
- 从 `api.evolution_routes` import `router as evolution_router`、
  `EvolutionTriggerRequest` 和 `trigger_evolution`。
- 删除本地 `EvolutionTriggerRequest` 与本地 `trigger_evolution()` 实现。
- 在尾部普通子路由 include 区加入 `router.include_router(evolution_router)`。
- 继续保留 `evolution_task` import，供 `/log` 和 `/chat` 自动触发使用。
- 继续保留 `EVOLUTION_THRESHOLD`、`SQLiteMemory`、`memory` 和
  `init_legacy_memory()`。
- 继续保留 `/health`。

推荐 include 顺序：

```python
router.include_router(evolution_router)
router.include_router(memory_router)
router.include_router(model_router)
router.include_router(task_router)
```

这样手动 evolution route 仍位于尾部普通子路由区域，`/health` 继续作为父模块尾部
健康检查。

## 测试策略

新增 `tests/test_api_evolution_routes_split.py`，锁定结构和行为契约：

- `test_api_evolution_routes_are_registered_from_split_module`
- `test_legacy_api_routes_evolution_imports_still_work`
- `test_split_evolution_routes_use_legacy_api_token_monkeypatch`
- `test_api_evolution_routes_are_not_registered_twice`
- `test_api_evolution_trigger_keeps_sync_background_boundary`
- `test_api_evolution_routes_do_not_import_parent_routes_or_sync_awaitable`
- `test_health_check_stays_in_parent_routes`

同步修改现有 split 测试：

- `tests/test_api_memory_routes_split.py`
  - 从 `_PARENT_ROUTE_SIGNATURES` 中移除 `/api/v1/evolution/trigger`。
  - 只保留 `/api/v1/health` 仍在父模块。
- `tests/test_api_model_routes_split.py`
  - 从 `_PARENT_ROUTE_SIGNATURES` 中移除 `/api/v1/evolution/trigger`。
  - 只保留 `/api/v1/health` 仍在父模块。

补充行为回归：

- `tests/test_audit_fixes.py::TestLazyControllerInit::test_legacy_memory_init_exists`
  或同等现有测试，确认 `api.routes.init_legacy_memory` 仍存在。
- `/log` 自动触发已有覆盖不足时，至少通过静态和相邻测试确保父模块
  `evolution_task` import 未移除；如果新增行为测试，patch target 必须是
  `api.routes.evolution_task`，不是 `api.evolution_routes.evolution_task`。

定向验证：

- `python -B -m pytest -q -p no:cacheprovider tests/test_api_evolution_routes_split.py`
- `python -B -m pytest -q -p no:cacheprovider tests/test_api_evolution_routes_split.py tests/test_api_memory_routes_split.py tests/test_api_model_routes_split.py tests/test_api_task_routes_split.py`
- `python -B -m pytest -q -p no:cacheprovider tests/test_asyncio_run_policy.py tests/test_audit_fixes.py::TestLazyControllerInit::test_legacy_memory_init_exists`

静态验证：

- `python -B -m compileall api/routes.py api/evolution_routes.py`
- `rg -n "from api\\.routes|import api\\.routes|asyncio\\.run|run_awaitable_sync" api/evolution_routes.py`
- `git diff --check -- api/routes.py api/evolution_routes.py tests/test_api_evolution_routes_split.py tests/test_api_memory_routes_split.py tests/test_api_model_routes_split.py`

全量验证：

- `python -B -m pytest -p no:cacheprovider tests/ -v`

## 非目标

- 不拆 `/chat`。
- 不拆 `/group/message`。
- 不拆 `/log`、`/log_ambient`、history、context、sticker/media、group timing、
  search/render 或 agent step。
- 不迁移 `init_legacy_memory()`、`memory` 或 `SQLiteMemory`。
- 不迁移 `/chat` 和 `/log` 的自动 evolution 触发。
- 不改 `core.evolution`、`core.legacy_adapter` 或 legacy memory 初始化流程。
- 不改 `server.py` 或 `bootstrap/lifespan.py`。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query`、conversation
  结构或工具输出契约。
- 不做 ruff 批量清理。

## 子 agent 分工约定

主线程负责最终编辑、验证和提交。写入阶段不要让多个 agent 同时修改 `api/routes.py`。

- **Worker A：测试文件。** 只允许创建 `tests/test_api_evolution_routes_split.py`，
  并修改 `tests/test_api_memory_routes_split.py` 与
  `tests/test_api_model_routes_split.py` 的父模块尾部路由列表。
- **Worker B：evolution 路由迁移。** 只允许创建 `api/evolution_routes.py`，
  并修改 `api/routes.py` 的 evolution import、include 和旧本地手动 trigger 区块。
- **Reviewer：验证审查。** 只读检查 diff、route module、重复注册、反向导入、
  同步排队边界、legacy init 保留、行数和测试输出。

接口约定：

- `api.evolution_routes.router` 不带 `/api/v1` 前缀，由父 `api.routes.router`
  include。
- `api.evolution_routes` 使用 `api.common_auth.verify_token`，不导入父模块。
- `api.routes` 必须 re-export `EvolutionTriggerRequest` 和 `trigger_evolution()`。
- `trigger_evolution()` 保持同步函数。
- `trigger_evolution()` 成功路径只调用 `background_tasks.add_task(evolution_task, user_id)`。
- 手动 trigger patch target 是 `api.evolution_routes.evolution_task`。
- 自动 trigger patch target 仍是 `api.routes.evolution_task`。

## 验收标准

- 新增 split 契约测试先红后绿。
- `tests/test_api_evolution_routes_split.py` 全部通过。
- `tests/test_api_memory_routes_split.py` 和 `tests/test_api_model_routes_split.py`
  同步更新并通过。
- `tests/test_api_task_routes_split.py` 通过。
- `tests/test_asyncio_run_policy.py` 通过。
- `api/evolution_routes.py` 无 `from api.routes`、无 `import api.routes`、无
  `asyncio.run`、无 `run_awaitable_sync`。
- `api.routes.init_legacy_memory` 仍存在且可调用。
- `/log` 与 `/chat` 自动触发仍由父模块 `evolution_task` 提供。
- `api/routes.py` 行数继续下降。
- 全量 `tests/` 回归 0 failures。

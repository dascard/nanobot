# 普通 API Models 路由拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」当前仍只剩 `api/routes.py`。
上一阶段已经完成普通 API memory 路由拆分，`api/routes.py` 从 2712 行降到
2523 行。下一刀继续选择低耦合路由，避免触碰 `/chat`、`/group/message`、
evolution legacy 初始化和 Prompt Runtime 输入契约。

只读子 agent 对 `models`、`evolution` 与 `memory` 的对比结论是：`models`
风险最低，边界最窄，只依赖模型注册表和 `NewAPIClient`；`evolution` endpoint
本身很薄，但父模块仍有 `/chat` 和 `/log` 自动触发 `evolution_task`，拆分时容易
误伤主链路依赖。因此本阶段采用 `models` 路由拆分。

## 现状证据

- `server.py` 只 include `api.routes.router`，普通 API 聚合入口不能改变。
- `api.common_auth.verify_token` 已兼容 `api.routes.NANOBOT_API_TOKEN`
  monkeypatch，子路由必须继续从 `api.common_auth` 导入鉴权依赖。
- 当前 models HTTP 层只包含：
  - `ModelSyncRequest`
  - `GET /models/list`
  - `POST /models/sync`
- `list_models()` 只读取 `clients.model_registry.registry`，支持 `provider` 和
  `tier` 过滤，返回 `status`、`provider`、`count`、`last_updated` 和 `models`。
- `sync_models()` 是 `async def`，函数内部读取 `config.NEW_API_KEY` 与
  `config.NEW_API_BASE_URL`，缺少 key 时返回 HTTP 400；成功时创建
  `NewAPIClient` 并 `await client.sync_models_to_registry(force=req.force)`。
- 现有测试没有专门覆盖 `/api/v1/models/list` 和 `/api/v1/models/sync` 的 HTTP
  行为，需要在拆分契约测试中补齐基本成功 / 错误路径。
- `tests/test_api_memory_routes_split.py` 当前锁定 `/models/list` 与 `/models/sync`
  仍在父模块。拆分 models 时必须同步调整该测试，改为只锁定 evolution 和 health
  留在父模块。

## 目标

本阶段新增 `api/model_routes.py`，并从 `api/routes.py` 迁移普通 models HTTP 层。
完成后必须满足：

- `GET /api/v1/models/list` 和 `POST /api/v1/models/sync` 的 path、method、参数、
  状态码和 response shape 不变。
- 两个 models endpoint 的 `endpoint.__module__` 变为 `api.model_routes`。
- `api.routes` 继续 re-export：
  - `ModelSyncRequest`
  - `list_models`
  - `sync_models`
- `sync_models()` 继续是 coroutine function。
- `api.model_routes` 不导入 `api.routes`，避免循环导入。
- `api.model_routes` 从 `api.common_auth` 导入 `verify_token`。
- `tests/test_api_memory_routes_split.py` 不再要求 models 路由留在父模块。
- `api.routes` 继续保留 `EvolutionTriggerRequest`、`trigger_evolution()`、
  `init_legacy_memory()`、`memory`、`evolution_task` 导入和 `/health`。
- 不新增 `asyncio.run()`，不新增 `run_awaitable_sync`，不新增同步函数包装 awaitable。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query`、conversation
  结构或工具输出契约。

## 模块边界

### `api/model_routes.py`

职责：

- 定义 `router = APIRouter(tags=["models"])`。
- 定义 `ModelSyncRequest`。
- 定义 `list_models()`。
- 定义 `sync_models()`。

依赖：

- `fastapi.APIRouter`、`Depends`、`HTTPException`
- `pydantic.BaseModel`
- `api.common_auth.verify_token`
- `clients.model_registry.registry`
- `clients.new_api_client.NewAPIClient`

兼容策略：

- `sync_models()` 继续在函数内导入 `NEW_API_KEY` 和 `NEW_API_BASE_URL`，保留旧的
  monkeypatch 和懒读取行为。
- 行为测试 mock `api.model_routes.NewAPIClient`，避免真实网络。

禁止：

- 不导入 `api.routes`。
- 不迁移 admin 侧 `api/admin/model_routes.py`。
- 不迁移 `nanobot_kt.bridge` 的模型同步逻辑。
- 不改变 registry 数据结构、模型优先级算法或路由评分。

### `api/routes.py`

职责：

- 继续作为 `/api/v1` 聚合 router。
- 从 `api.model_routes` import model router、request model 和 endpoint。
- 在原 models endpoint 所在位置 include `model_router`，保持尾部路由顺序清晰。
- 删除本地 `ModelSyncRequest`、`list_models()` 和 `sync_models()`。
- 删除父模块仅服务 models 的 import：`registry`、`NewAPIClient`，前提是搜索确认
  父模块其他代码已不使用它们。
- 保留 evolution、memory include、task include 和 health。

## 测试策略

新增 `tests/test_api_model_routes_split.py`，锁定结构和行为契约：

- `test_api_model_routes_are_registered_from_split_module`
- `test_legacy_api_routes_model_imports_still_work`
- `test_split_model_routes_use_legacy_api_token_monkeypatch`
- `test_api_model_routes_are_not_registered_twice`
- `test_api_model_async_boundaries_remain_coroutines`
- `test_api_model_routes_do_not_import_parent_routes_or_sync_awaitable`
- `test_model_list_filters_provider_and_tier`
- `test_model_sync_rejects_missing_api_key`
- `test_model_sync_uses_force_and_returns_updated_count`
- `test_non_model_tail_routes_stay_in_parent_routes`

同步修改 `tests/test_api_memory_routes_split.py`：

- 从 `_PARENT_ROUTE_SIGNATURES` 中移除 `/api/v1/models/list` 和
  `/api/v1/models/sync`。
- 保留 `/api/v1/evolution/trigger` 与 `/api/v1/health` 仍在父模块的约束。

定向验证：

- `python -B -m pytest -q -p no:cacheprovider tests/test_api_model_routes_split.py`
- `python -B -m pytest -q -p no:cacheprovider tests/test_api_memory_routes_split.py`
- `python -B -m pytest -q -p no:cacheprovider tests/test_asyncio_run_policy.py`

静态验证：

- `python -B -m compileall api/routes.py api/model_routes.py`
- `rg -n "from api\\.routes|import api\\.routes|asyncio\\.run|run_awaitable_sync" api/model_routes.py`
- `git diff --check -- api/routes.py api/model_routes.py tests/test_api_model_routes_split.py tests/test_api_memory_routes_split.py`

全量验证：

- `python -B -m pytest -p no:cacheprovider tests/ -v`

## 非目标

- 不拆 `/chat`、`/group/message`、history、context、log、sticker/media、group timing、
  search/render、agent step、evolution route-only、memory、tasks 或 health。
- 不改模型注册表 schema。
- 不改模型优先级排序、熔断器、候选过滤、capability 验证或 NewAPI 请求格式。
- 不改 `nanobot_kt.bridge` 内部模型同步路径。
- 不做 ruff 批量清理。

## 子 agent 分工约定

主线程负责最终编辑、验证和提交。写入阶段不要让多个 agent 同时修改 `api/routes.py`。

- **Worker A：测试文件。** 只允许创建 `tests/test_api_model_routes_split.py`，并修改
  `tests/test_api_memory_routes_split.py` 的父模块尾部路由列表。
- **Worker B：models 路由迁移。** 只允许创建 `api/model_routes.py`，并修改
  `api/routes.py` 的 model import、include 和旧本地 models 区块。
- **Reviewer：验证审查。** 只读检查 diff、route module、重复注册、反向导入、
  async coroutine 边界、行数和测试输出。

接口约定：

- `api.model_routes.router` 不带 `/api/v1` 前缀，由父 `api.routes.router` include。
- `api.model_routes` 使用 `api.common_auth.verify_token`，不导入父模块。
- `api.routes` 必须 re-export `ModelSyncRequest`、`list_models()` 和 `sync_models()`。
- `ModelSyncRequest.force` 默认值保持 `True`。
- `sync_models()` 继续缺少 `NEW_API_KEY` 时返回 HTTP 400。
- `sync_models()` 成功路径继续 await `NewAPIClient.sync_models_to_registry(force=req.force)`。

## 验收标准

- 新增 split 契约测试先红后绿。
- `tests/test_api_model_routes_split.py` 全部通过。
- `tests/test_api_memory_routes_split.py` 全部通过。
- `tests/test_asyncio_run_policy.py` 通过。
- `api/model_routes.py` 无 `from api.routes`、无 `import api.routes`、无
  `asyncio.run`、无 `run_awaitable_sync`。
- `api/routes.py` 行数继续下降。
- 全量 `tests/` 回归 0 failures。

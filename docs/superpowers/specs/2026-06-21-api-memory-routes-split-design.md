# 普通 API Memory 路由拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」当前只剩
`api/routes.py`。上一阶段已经抽出 `api/common_auth.py`，并把 `/tasks*`
迁移到 `api/task_routes.py`，`api.routes` 从 2822 行降到 2712 行。普通 API
拆分模板已经成型：父模块继续作为 `/api/v1` 聚合入口，子模块持有业务 router，
父模块 include 并 re-export 旧导入符号。

下一刀应继续避开 `/chat` 与 `/group/message` 主链路，优先选择低耦合、可测试、
行数收益大于纯微调的路由边界。当前候选集中在 `models`、`evolution` 和
`memory`。

## 现状证据

- `server.py` 只 include `api.routes.router`，普通 API 聚合入口不能改变。
- `api.common_auth.verify_token` 已兼容 `api.routes.NANOBOT_API_TOKEN`
  monkeypatch，子路由应继续从 `api.common_auth` 导入鉴权依赖。
- `api/routes.py` 末尾包含：
  - `POST /evolution/trigger`
  - `GET /memory/digests`
  - `POST /memory/digests/run`
  - `GET /models/list`
  - `POST /models/sync`
  - `GET /memory/recall`
- `tests/test_memory_digest.py` 已覆盖 `/api/v1/memory/recall` 的成功路径和日期
  过滤错误路径。
- `bootstrap/lifespan.py` 通过 `from api.routes import init_legacy_memory` 调用
  legacy memory 初始化；`tests/test_audit_fixes.py` 也锁定
  `api.routes.init_legacy_memory` 存在。
- `api.routes._safe_meta()` 仍被聊天落库逻辑使用，不能作为 memory 路由 helper
  整体迁移。
- `_short_text()` 与 `_build_expand_chain()` 当前在 `api/routes.py` 内没有调用。
  为降低隐藏旧导入风险，本阶段把它们作为 legacy memory helper 一并迁移到新模块，
  并由 `api.routes` re-export；后续若要删除，应另起清理任务。
- 子 agent 审计存在取舍差异：`models/evolution` 审计建议先拆 `models`，因为它的
  风险最低；`memory` 审计确认只做纯路由搬迁时可控。考虑本阶段目标是推进
  `api/routes.py` 超大文件治理，而不是只做最小风险微调，因此采用行数收益更高且
  边界仍清晰的 `memory`。

## 候选方案

### 方案 A：拆 `models` 路由

范围为 `ModelSyncRequest`、`list_models()` 和 `sync_models()`。

优点：

- 边界最小，依赖集中在 `clients.model_registry.registry` 和
  `clients.new_api_client.NewAPIClient`。
- 不触碰聊天、群消息、记忆摘要和 legacy 初始化。

风险：

- 行数收益很小，无法显著推进 `api/routes.py` 超大文件治理。
- 现有测试中没有明显的 `/models/list`、`/models/sync` 端点契约锚点，需要新增
  覆盖才能拆。

结论：适合作为后续小刀，不作为本阶段首选。

### 方案 B：拆 `evolution` 路由

范围为 `EvolutionTriggerRequest` 和 `trigger_evolution()`。

优点：

- HTTP endpoint 本身很小，只向 `BackgroundTasks` 排队 `evolution_task()`。
- 行为简单，容易写拆分契约测试。

风险：

- `api.routes` 仍持有 `memory` 全局和 `init_legacy_memory()`，启动流程与旧兼容测试
  已依赖该符号。
- 如果把 evolution 与 legacy memory 初始化一起下沉，容易扩大范围；如果只迁移
  endpoint，收益过小。

结论：暂缓。待 memory/models 继续拆分后，再单独处理 legacy evolution 边界。

### 方案 C：拆 `memory` 路由（采用）

范围为 `MemoryDigestRunRequest`、`_validate_memory_digest_date_filters()`、
`_calc_recall_confidence()`、`get_memory_digests()`、`run_memory_digests()` 和
`recall_memory()`。

优点：

- 三个 endpoint 共享 `MemoryDigestRetrievalService`、`validate_digest_date`、
  `generate_daily_digest_for_date`、`ChatLog` 和 `MemoryDigest` 相关领域依赖，
  业务边界清楚。
- 行数收益大于 `models` 和 `evolution` 单独拆分。
- 已有 `tests/test_memory_digest.py` 行为回归，新增 split 契约测试后可形成红绿闭环。
- 不进入 `/chat`、`/group/message`、KT Bridge、私聊缓冲、TimingGate 或 Prompt
  Runtime 主链路。

风险：

- `recall_memory()` 额外召回 AI daily SQL tool log，依赖 `ChatLog` 查询和
  `_calc_recall_confidence()`。
- 日期过滤错误需要继续返回 HTTP 400，不能让 `ValueError` 泄漏成 500。
- 父模块旧导入兼容必须保留，否则外部调用 `from api.routes import recall_memory`
  会断。

结论：采用。

## 目标

本阶段新增 `api/memory_routes.py`，并从 `api/routes.py` 迁移 memory HTTP 层。
完成后必须满足：

- `GET /api/v1/memory/digests`、`POST /api/v1/memory/digests/run` 和
  `GET /api/v1/memory/recall` 的 path、method、参数、状态码和 response shape 不变。
- 三个 memory endpoint 的 `endpoint.__module__` 变为 `api.memory_routes`。
- `api.routes` 继续 re-export：
  - `MemoryDigestRunRequest`
  - `_validate_memory_digest_date_filters`
  - `_short_text`
  - `_calc_recall_confidence`
  - `_build_expand_chain`
  - `get_memory_digests`
  - `run_memory_digests`
  - `recall_memory`
- `api.routes._safe_meta` 保留在父模块，继续服务聊天落库逻辑。
- `api.memory_routes` 不导入 `api.routes`，避免循环导入。
- `api.memory_routes` 从 `api.common_auth` 导入 `verify_token`。
- 不新增 `asyncio.run()`，不新增 `run_awaitable_sync`，不新增同步函数包装 awaitable。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query`、conversation
  结构或工具输出契约。

## 模块边界

### `api/memory_routes.py`

职责：

- 定义 `router = APIRouter(tags=["memory"])`。
- 定义 `MemoryDigestRunRequest`。
- 定义 `_validate_memory_digest_date_filters()`。
- 定义 `_short_text()`，仅作为 legacy helper 迁移和 re-export。
- 定义 `_calc_recall_confidence()`。
- 定义 `_build_expand_chain()`，仅作为 legacy helper 迁移和 re-export。
- 定义 `get_memory_digests()`。
- 定义 `run_memory_digests()`。
- 定义 `recall_memory()`。

依赖：

- `datetime`、`timedelta`
- `fastapi.APIRouter`、`Depends`、`HTTPException`
- `pydantic.BaseModel`
- `sqlalchemy.orm.Session`
- `api.common_auth.verify_token`
- `core.database.get_db`、`ChatLog`、`MemoryDigest`
- `core.daily_digest.generate_daily_digest_for_date`
- `app.memory_digest.retrieval_service.MemoryDigestRetrievalService`
- `app.memory_digest.retrieval_service.validate_digest_date`

禁止：

- 不导入 `api.routes`。
- 不迁移 `ChatProxyRequest`、`EvolutionTriggerRequest` 或 `ModelSyncRequest`。
- 不迁移 `_safe_meta`、聊天落库 helper、群消息 helper 或模型路由。
- 不引入后台任务调度新语义。

### `api/routes.py`

职责：

- 继续作为 `/api/v1` 聚合 router。
- 从 `api.memory_routes` import memory router、request model、helper 和 endpoint。
- 在原 memory endpoint 所在位置 include `memory_router`，保持 route 顺序可预测。
- 删除本地 memory endpoint 实现和被迁移 helper 的本地定义。
- 保留 `init_legacy_memory()`、`trigger_evolution()`、`list_models()`、`sync_models()` 和
  `/health`。
- 保留 `_safe_meta()`，因为 `_persist_chat_turn()` 仍调用它。

## 测试策略

新增 `tests/test_api_memory_routes_split.py`，锁定结构契约：

- `test_api_memory_routes_are_registered_from_split_module`
- `test_legacy_api_routes_memory_imports_still_work`
- `test_split_memory_routes_use_legacy_api_token_monkeypatch`
- `test_api_memory_routes_are_not_registered_twice`
- `test_api_memory_routes_do_not_import_parent_routes_or_sync_awaitable`
- `test_non_memory_tail_routes_stay_in_parent_routes`
- `test_safe_meta_stays_in_parent_routes`

定向行为回归：

- `tests/test_memory_digest.py::test_memory_recall_excludes_legacy_by_default`
- `tests/test_memory_digest.py::test_memory_recall_rejects_invalid_date_filters`
- 与 memory digest retrieval 相关的相邻测试：
  `tests/test_memory_digest.py`。

静态验证：

- `python -B -m compileall api/routes.py api/memory_routes.py`
- `rg -n "from api\\.routes|import api\\.routes|asyncio\\.run|run_awaitable_sync" api/memory_routes.py`
- `git diff --check -- api/routes.py api/memory_routes.py tests/test_api_memory_routes_split.py`

全量验证：

- `python -B -m pytest -p no:cacheprovider tests/ -v`

## 非目标

- 不拆 `/chat`、`/group/message`、群 timing、sticker、media、history、log 或 health。
- 不拆 `evolution` legacy 初始化。
- 不拆 `models` registry/sync 路由。
- 不改变 memory digest schema、retrieval ranking、AI daily tool log 召回语义或返回字段。
- 不调整数据库模型或迁移。
- 不做 ruff 批量清理；该任务仍留在 `docs/todo.md` 的低优先级队列。

## 子 agent 分工约定

主线程负责最终编辑、验证和提交。写入阶段不要让多个 agent 同时修改
`api/routes.py`。

- **Explorer A：memory 边界复核。** 只读检查 `api/routes.py` memory helper 和 endpoint
  依赖、现有测试覆盖、旧导入兼容风险。
- **Explorer B：models/evolution 对比。** 只读检查 `models` 与 `evolution` 拆分收益和
  legacy 初始化风险，确认本阶段不选它们。
- **Worker A：测试文件。** 只允许创建或修改 `tests/test_api_memory_routes_split.py`。
- **Worker B：memory 路由迁移。** 只允许创建 `api/memory_routes.py`，并修改
  `api/routes.py` 的 memory import、include、旧本地区块。
- **Reviewer：验证审查。** 只读检查 diff、route module、重复注册、反向导入、
  asyncio 策略、行数和测试输出。

接口约定：

- `api.memory_routes.router` 不带 `/api/v1` 前缀，由父 `api.routes.router` include。
- `api.memory_routes` 使用 `api.common_auth.verify_token`，不导入父模块。
- `api.routes` 必须 re-export memory request model、helper 和 endpoint。
- `MemoryDigestRunRequest` 的字段和默认值保持不变：
  `target_date: Optional[str] = None`、`user_id: Optional[str] = None`、
  `force: bool = False`。
- `run_memory_digests()` 保持同步 endpoint，不把 daily digest 调用包装成 awaitable。
- `recall_memory()` 继续对空 keyword 返回 HTTP 400。
- 日期过滤继续通过 `validate_digest_date()` 校验，并把 `ValueError` 转为 HTTP 400。

## 验收标准

- 新增 split 契约测试先红后绿。
- `tests/test_api_memory_routes_split.py` 全部通过。
- `tests/test_memory_digest.py` 全部通过。
- `tests/test_asyncio_run_policy.py` 通过。
- `api/memory_routes.py` 无 `from api.routes`、无 `import api.routes`、无
  `asyncio.run`、无 `run_awaitable_sync`。
- `api/routes.py` 行数下降，且仍只由 `server.py` include 一次聚合 router。
- 全量 `tests/` 回归 0 failures。

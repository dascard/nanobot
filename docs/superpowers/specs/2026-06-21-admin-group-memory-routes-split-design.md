# Admin Group Memory 路由拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的 P3「超大文件 >800 行拆分」当前仍有两个硬项：
`api/admin_routes.py` 和 `api/routes.py`。上一阶段已经从
`api/admin_routes.py` 拆出 Sticker / Generated Images 管理边界，当前文件仍有
4979 行；`api/routes.py` 仍有 2822 行。

只读调查后，本阶段继续沿管理端拆分，而不是切换到普通 API。原因是管理端已有成熟的
`api/admin/*_routes.py` 模式：子模块暴露无 prefix 的 router，由
`api.admin_routes.router` 统一 include；共享认证与审计走 `api.admin.common`，并兼容
`api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch。普通 API 侧尚未建立
`verify_token` 兼容层，直接拆 `api/routes.py` 的收益较小且鉴权兼容风险更高。

本阶段选择拆 `api/admin_routes.py` 中的 Group Memory 管理边界。它覆盖群体记忆列表、
提取、注入配置、注入预览和治理编辑接口，有独立数据对象 `GroupMemory` 和独立业务服务
`app.group_memory.*`，不会牵连模型路由、工具配置、reply/eval 工作台或 Prompt Runtime。

## 方案比较

### 方案 A：拆 `api/admin/group_memory_routes.py`（推荐）

迁移 Group Memory request model、列表 / 提取 / 注入 / 编辑路由，以及相关序列化 helper。
`api.admin_routes` include 新 router，并 re-export 旧符号。

优点：

- 边界集中，主要围绕 `GroupMemory` 与 `app.group_memory` 服务。
- 不包含全局锁、后台任务状态或缓存状态。
- 现有 `tests/test_admin_api.py::TestObservabilityAPI` 已覆盖主要行为。
- 净减少约 240-250 行，风险低于模型、工具和 reply/eval 大段拆分。

代价：

- 必须保护 `/groups/{group_id:path}/memories` 和
  `/groups/{group_id:path}/memories/extract` 的路由顺序，避免被
  `/groups/{group_id:path}` 吞掉。
- 需要 re-export 旧符号，保持 `api.admin_routes` 旧导入路径兼容。

### 方案 B：拆 trace / observability 只读边界

迁移 `/agent-runs`、`/tool-calls`、`/llm-api-logs` 等只读观测接口。

优点：

- 多数接口是只读查询，事务风险低。
- 也能减少约 260 行。

代价：

- 与 overview、群详情、TimingGate 和 reply trace 语义相邻，后续边界容易扩大。
- 对当前 Group Memory 路由顺序风险没有帮助。

### 方案 C：拆 `api/routes.py` 的公开 media / sticker 端点

迁移普通 API 中的 `/stickers/*` 和 `/generated-images/*/image` 端点。

优点：

- 不触碰 `/chat`、SSE、私聊缓冲和 `/group/message` 主链路。

代价：

- 普通 API 侧缺少 `verify_token` 共享兼容层。现有测试通过
  `app.dependency_overrides[routes.verify_token]` 和
  `api.routes.NANOBOT_API_TOKEN` monkeypatch 覆盖鉴权，拆分前必须先设计 common 层。
- 行数收益约 160 行，不如管理端拆分。

## 目标

1. 新增 `api/admin/group_memory_routes.py`，承载 Group Memory 管理端路由。
2. `api/admin_routes.py` 通过 `router.include_router(group_memory_router)` 继续暴露原
   HTTP 路径。
3. `api.admin_routes` re-export 迁移后的 request model、helper 和 endpoint 函数，保持旧导入路径兼容。
4. 保持所有 HTTP path、method、response shape、状态码、审计 action 和认证语义不变。
5. 不新增 `asyncio.run()`，不新增同步函数包装 awaitable。

## 迁移范围

迁移到 `api/admin/group_memory_routes.py`：

- Request model：
  - `GroupMemoryExtractRequest`
  - `GroupMemoryInjectionConfigRequest`
  - `GroupMemoryInjectionPreviewRequest`
  - `GroupMemoryUpdateRequest`
- Helper：
  - `_group_memory_row_dict()`
  - `_group_memories_payload()`
  - `_extract_group_memories_response()`
- 路由：
  - `GET /groups/{group_id:path}/memories`
  - `GET /group-memories/overview`
  - `GET /group-memories/{group_id:path}/items`
  - `POST /group-memories/{group_id:path}/extract`
  - `PUT /group-memories/{group_id:path}/injection-config`
  - `POST /group-memories/{group_id:path}/injection-preview`
  - `PATCH /group-memories/items/{memory_id}`
  - `POST /groups/{group_id:path}/memories/extract`

保留在 `api/admin_routes.py`：

- `router = APIRouter(prefix="/api/v1/admin")`
- `NANOBOT_ADMIN_TOKEN`
- `verify_admin()`、`_audit()`、`_client_ip()`、`_audit_request()`
- `/overview`、`/groups`、`/groups/{group_id:path}`
- `_raw_group_id()`、`_group_session_id()`、`_group_stream_id()`
- TimingGate、配置、模型、工具、reply/eval、eval 工作台、日志 viewer、settings 等其他子域

不迁移 `_raw_group_id()`、`_group_session_id()`、`_group_stream_id()`。Group Memory 路由自身可通过
`core.group_runtime.ids.normalize_group_session_id()` 和
`app.group_memory.injection_service.group_memory_config_ids()` 完成规范化；这些旧 helper 仍被
`list_groups()`、`group_detail()`、`timing_gate_events()`、配置和工具目标接口使用。

## 兼容策略

### 顶层 router

`server.py` 继续只导入 `api.admin_routes.router`。新模块不被 `server.py` 直接导入。

`api/admin_routes.py` 在本地 `/groups/{group_id:path}` 注册前 include
`group_memory_router`。这样可以确保：

- `/api/v1/admin/groups/{group_id}/memories`
- `/api/v1/admin/groups/{group_id}/memories/extract`

不会被 `/api/v1/admin/groups/{group_id:path}` 抢先匹配。

### 认证与审计

新模块使用：

- `api.admin.common.verify_admin`
- `api.admin.common.audit_request`

`api.admin.common._current_admin_token()` 已兼容读取
`api.admin_routes.NANOBOT_ADMIN_TOKEN`，因此现有测试对旧 token 路径的 monkeypatch 仍会影响新路由。

不额外支持 monkeypatch `api.admin_routes._audit_request` 来改变拆分模块审计行为。现有 split 模块已经以
`api.admin.common.audit_request` 作为统一审计入口，本阶段延续该模式。

### 旧符号导入

`api/admin_routes.py` 从 `api.admin.group_memory_routes` 导入并 re-export：

- `GroupMemoryExtractRequest`
- `GroupMemoryInjectionConfigRequest`
- `GroupMemoryInjectionPreviewRequest`
- `GroupMemoryUpdateRequest`
- `_group_memory_row_dict`
- `_group_memories_payload`
- `_extract_group_memories_response`
- `group_memories_list`
- `group_memories_overview`
- `group_memory_items`
- `group_memory_extract_alias`
- `group_memory_injection_config`
- `group_memory_injection_preview`
- `group_memory_update_item`
- `group_memories_extract`
- `router as group_memory_router`

### 路由顺序

实现必须避免重复注册。迁移后，旧 `api/admin_routes.py` 中不再保留这些路由 decorator。

测试需要显式保护：

- 每个 Group Memory 路由只注册一次。
- endpoint module 为 `api.admin.group_memory_routes`。
- `/groups/{group_id:path}/memories` 系列路由位于 `/groups/{group_id:path}` 之前。

## 测试设计

新增 `tests/test_admin_group_memory_routes_split.py`：

1. `test_admin_group_memory_routes_are_registered_from_split_module`
   - 递归展开 `api.admin_routes.router`。
   - 断言 8 个迁移路由存在。
   - 断言 endpoint module 为 `api.admin.group_memory_routes`。

2. `test_legacy_admin_routes_group_memory_imports_still_work`
   - 断言 `api.admin_routes` 中的 request model、helper 和 endpoint 函数与新模块对象同一。

3. `test_split_group_memory_routes_use_legacy_admin_token_monkeypatch`
   - monkeypatch `api.admin_routes.NANOBOT_ADMIN_TOKEN = "split-token"`。
   - `GET /api/v1/admin/group-memories/overview` 使用 split token 返回 200。
   - 使用旧默认 token 返回 401。

4. `test_admin_group_memory_routes_are_not_registered_twice`
   - 对 8 个迁移路由断言只注册一次。

5. `test_group_memory_routes_are_registered_before_group_detail_catchall`
   - 断言 `/groups/{group_id:path}/memories` 和
     `/groups/{group_id:path}/memories/extract` 在 `/groups/{group_id:path}` 之前注册。

补充 `tests/test_admin_api.py::TestObservabilityAPI`：

- 新增 `GET /api/v1/admin/groups/group_7788/memories` 行为测试，确保 legacy list 路由没有被
  group detail catch-all 吞掉。

复用既有测试：

- `tests/test_admin_api.py::TestObservabilityAPI`
- `tests/test_admin_api.py::TestAuth`

## 验证门禁

实现阶段需要运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_group_memory_routes_split.py -q
```

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_group_memory_routes_split.py \
  tests/test_admin_api.py::TestObservabilityAPI \
  tests/test_admin_api.py::TestAuth \
  -q
```

```bash
python -m compileall api/admin_routes.py api/admin/group_memory_routes.py -q
```

```bash
git diff --check -- \
  api/admin_routes.py \
  api/admin/group_memory_routes.py \
  tests/test_admin_group_memory_routes_split.py \
  tests/test_admin_api.py
```

提交前按项目约束运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

## 不做事项

- 不拆普通 `api/routes.py`。
- 不迁移 `/overview`、`/groups`、`/groups/{group_id:path}`、TimingGate、trace、模型、工具、
  reply/eval、eval 工作台、日志 viewer 或 settings。
- 不改变 DB schema、response shape、状态码、审计 action 或 Group Memory 提取 / 注入业务逻辑。
- 不改 Prompt Runtime 模板、工具 usage 文档、`enriched_query` 组装或 Prompt Runtime 输入。
- 不引入 `asyncio.run()` 或同步函数包装 awaitable。

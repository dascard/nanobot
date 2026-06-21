# Admin DB Browser 拆分设计

日期：2026-06-21

## 背景

`docs/todo.md` 的「超大文件 >800 行拆分」仍包含 `api/admin_routes.py`。
该文件当前同时承担 WebUI 管理 API、Block/Config/DB 管理、调试、日志、评测与运维
入口等多类职责，行数已经超过 5800 行。

只读审计显示，`api/admin_routes.py` 中的 DB Browser 逻辑相对独立，集中包含：

- 请求模型：`DbQuery`
- 表分组与白名单：`DB_TABLE_GROUPS`、`READONLY_TABLES`、`READONLY_TABLE_SET`
- 安全策略：`BLOCKED_DB_TABLES`、全局脱敏列、全局预览列、表级策略
- SQL guard：只允许单条 `SELECT`，禁止系统表、非白名单表和写操作
- 序列化：二进制预览、长文本截断、`cell_meta` 元数据
- 路由：`GET /db/tables`、`GET /db/tables/{table_name}`、`POST /db/query`

这些能力依赖 `get_db`、FastAPI、SQLAlchemy `text()` 和 admin 鉴权，但不依赖
`ChatLog`、`User`、`StickerMemory` 等 `admin_routes.py` 顶部的大量 ORM 类。
因此 DB Browser 是 `api/admin_routes.py` 第一刀拆分的低风险候选。

## 目标

第一阶段做无行为变化的路由拆分：

1. 新增 `api/admin/db_browser_routes.py`，承接只读 DB Browser 能力。
2. `api.admin_routes.router` 继续作为唯一顶层 admin router，由它 include 新子路由。
3. 保持现有 HTTP 路径、请求体、响应字段、错误码和错误文案不变。
4. 保持 `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 兼容。
5. 保持 `api.admin_routes` 中 DB Browser 旧符号的兼容导入。
6. 通过红灯测试证明 DB Browser 路由实现已经迁出 `api.admin_routes`，且没有重复注册。

## 非目标

本阶段不做以下事情：

- 不迁移 `/db/backup`。
- 不迁移 `/db/vacuum`。
- 不改变 DB Browser 前端页面。
- 不调整 DB 表白名单、脱敏策略、默认排序和文本截断阈值。
- 不把 SQL guard 改成 SQLite authorizer。
- 不合并 admin 鉴权实现，不删除 `api.admin_routes.verify_admin`。
- 不拆其他 admin 子域，例如日志、prompt、TimingGate、评测、Block 或 Config。

`/db/backup` 和 `/db/vacuum` 虽然使用 `/db` 前缀，但它们属于数据库运维动作。
其中 backup 搬迁后需要重新处理相对 `DATABASE_URL` 的项目根路径解析，vacuum 会写数据库
并写审计日志。为保持第一刀低风险，本阶段只迁移只读 Browser 路由。

## 方案比较

### 方案 A：只迁移只读 Browser 路由

新增 `api/admin/db_browser_routes.py`，迁移 `/db/tables*` 与 `/db/query` 相关模型、
常量、helper 和路由。`api/admin_routes.py` include 新 router，并 re-export 旧符号。

优点：

- 边界清晰，聚焦只读浏览能力。
- 不触碰 backup 路径解析和 vacuum 审计写入。
- 现有 `tests/test_admin_db_browser.py` 能覆盖主要行为。
- `api/admin_routes.py` 体积下降明显，风险可控。

缺点：

- `/db/*` 路径仍有 backup/vacuum 留在旧文件中。
- 后续如果要完全收敛 DB 管理，还需要第二刀。

### 方案 B：迁移完整 `/db/*` 路由

将 `/db/tables*`、`/db/query`、`/db/backup`、`/db/vacuum` 全部迁移到同一模块。

优点：

- 路径维度更完整，所有 `/db` 路由集中。
- `api/admin_routes.py` 更少残留。

缺点：

- backup 的相对数据库路径在新文件层级下容易算错。
- vacuum 不是只读能力，需要保留审计写入并补新测试。
- 第一刀范围扩大，失败面从 Browser 扩散到 DB 维护。

### 方案 C：先抽 service/helper，不迁移路由

仅把 SQL guard、序列化和表策略拆到 service 模块，路由函数仍留在
`api/admin_routes.py`。

优点：

- HTTP 注册风险最低。
- 可以先降低局部逻辑复杂度。

缺点：

- 不能证明路由职责从大文件迁出。
- `api/admin_routes.py` 仍保留多个 DB Browser endpoint，超大文件拆分收益较低。
- 后续仍要再做一次路由迁移。

推荐采用方案 A。

## 模块边界

新增模块：

- `api/admin/db_browser_routes.py`

该模块负责：

- `router = APIRouter(prefix="/db", tags=["admin-db-browser"])`
- `DbQuery`
- `DB_TABLE_GROUPS`
- `READONLY_TABLES`
- `READONLY_TABLE_SET`
- `BLOCKED_DB_TABLES`
- `GLOBAL_REDACT_COLUMNS`
- `GLOBAL_PREVIEW_ONLY_COLUMNS`
- `DEFAULT_DB_TABLE_POLICY`
- `DB_TABLE_POLICIES`
- `_db_table_policy()`
- `_db_table_meta()`
- `_quote_identifier()`
- `_table_columns()`
- `_safe_serialize_cell()`
- `_serialize_db_rows()`
- `_extract_query_table_names()`
- `_validate_query_tables_allowed()`
- `_validate_readonly_query()`
- `_available_readonly_tables()`
- `_available_db_groups()`
- `list_tables()`
- `query_table()`
- `execute_readonly_query()`

保留模块：

- `api/admin_routes.py`

该模块继续负责：

- 顶层 `router = APIRouter(prefix="/api/v1/admin")`
- `server.py` 的 admin router 入口
- 现有旧鉴权函数和旧 monkeypatch 目标 `NANOBOT_ADMIN_TOKEN`
- 非 DB Browser 的 admin 路由
- `/db/backup`
- `/db/vacuum`
- DB Browser 旧符号的兼容导出

## 路由注册

`api/admin/db_browser_routes.py` 定义相对路径：

- `@router.get("/tables")`
- `@router.get("/tables/{table_name}")`
- `@router.post("/query")`

`api/admin_routes.py` 顶部 include：

```python
from api.admin.db_browser_routes import router as db_browser_router

router.include_router(db_browser_router)
```

完整路径保持不变：

- `GET /api/v1/admin/db/tables`
- `GET /api/v1/admin/db/tables/{table_name}`
- `POST /api/v1/admin/db/query`

新 router 不使用 `/{table_name}` 这种动态根路径，避免未来迁移 `/db/backup`、
`/db/vacuum` 时产生动态路由吞噬风险。

## 鉴权与依赖

新模块从 `api.admin.common` 导入 `verify_admin`，从 `core.database` 导入 `get_db`。
不要从 `api.admin_routes` 导入 `verify_admin`、`router`、`logger` 或 `_audit_request`。

原因：

- `api.admin.common.verify_admin()` 已通过 `sys.modules["api.admin_routes"]`
  读取 `NANOBOT_ADMIN_TOKEN`，兼容现有测试的 monkeypatch。
- 子模块反向导入 `api.admin_routes` 会形成循环导入。
- `get_db` 必须直接使用 `core.database.get_db` 函数对象，保证测试中的
  `app.dependency_overrides[get_db]` 继续生效。

每个 endpoint 沿用现有子模块风格，在函数参数中显式声明：

```python
db: Session = Depends(get_db)
_auth=Depends(verify_admin)
```

不使用 router-level dependency，以避免偏离当前 admin 子模块习惯。

## 兼容导出

`api.admin_routes` 当前暴露了 DB Browser 的模块级对象。现有测试没有直接导入这些符号，
但外部脚本或交互式排查可能使用。

第一阶段在 `api/admin_routes.py` 中保留 re-export：

- `DbQuery`
- `DB_TABLE_GROUPS`
- `READONLY_TABLES`
- `READONLY_TABLE_SET`
- `BLOCKED_DB_TABLES`
- `GLOBAL_REDACT_COLUMNS`
- `GLOBAL_PREVIEW_ONLY_COLUMNS`
- `DEFAULT_DB_TABLE_POLICY`
- `DB_TABLE_POLICIES`
- `_db_table_policy`
- `_db_table_meta`
- `_quote_identifier`
- `_table_columns`
- `_safe_serialize_cell`
- `_serialize_db_rows`
- `_extract_query_table_names`
- `_validate_query_tables_allowed`
- `_validate_readonly_query`
- `_available_readonly_tables`
- `_available_db_groups`
- `list_tables`
- `query_table`
- `execute_readonly_query`

这些 re-export 可以在后续确认没有外部依赖后再收敛；本阶段不做破坏性删除。

## 行为契约

拆分后必须保持以下行为：

- `/db/tables` 返回 `tables`、`groups`、`table_meta`。
- `sensitive_data`、`sqlite_master` 和 `sqlite_*` 表不可访问。
- 非白名单表出现在 `FROM` 或 `JOIN` 中时，在执行前返回 400。
- `updated_at` 不会被误判为 `UPDATE` 写操作。
- SQL 查询只允许单条 `SELECT`，允许末尾一个分号。
- `/db/query` 继续用 `SELECT * FROM (<query>) LIMIT 500` 包裹执行。
- 内部 SQL 错误只返回 `HTTP 500` 和 `内部错误`，不回显列名、SQL 或堆栈。
- 表浏览 `page` 最小为 1，`limit` 钳制在 1 到 200。
- 表浏览隐藏 `hidden_columns`，返回 `cell_meta`。
- SQL 查询结果继续使用 `table_name=""` 序列化，不应用表级 hidden columns。
- 二进制和 `memoryview` 显示为 `<binary N bytes>`。
- 长文本按策略截断，`cell_meta.truncated` 和 `cell_meta.full_length` 保持原语义。
- `_available_readonly_tables()` 保持 `READONLY_TABLES` 的展示顺序。

## 测试计划

先补红灯测试：

- `test_db_browser_routes_are_registered_from_split_module`
  - 遍历 `server.app.routes`。
  - 断言 `/api/v1/admin/db/tables`、`/api/v1/admin/db/tables/{table_name}`、
    `/api/v1/admin/db/query` 的 endpoint module 是 `api.admin.db_browser_routes`。
  - 实现前失败，因为 endpoint 仍来自 `api.admin_routes`。

- `test_legacy_admin_routes_db_browser_imports_still_work`
  - 从 `api.admin_routes` 和 `api.admin.db_browser_routes` 分别导入 DB Browser 公开符号。
  - 断言旧模块对象与新模块对象一致。
  - 实现前失败，因为新模块不存在。

- `test_split_db_browser_uses_legacy_admin_token_monkeypatch`
  - monkeypatch `api.admin_routes.NANOBOT_ADMIN_TOKEN = "split-token"`。
  - `Bearer split-token` 访问 `/db/tables` 返回 200。
  - `Bearer test-token` 返回 401。
  - 防止新模块直接读取 `config.NANOBOT_ADMIN_TOKEN`。

- `test_db_browser_routes_are_not_registered_twice`
  - 遍历 app routes，断言三条 DB Browser 路径每个只注册一次。
  - 防止拆分后旧路由未删又 include 新路由。

定向回归：

- `python -m pytest tests/test_admin_db_browser.py -v`
- `python -m pytest tests/test_admin_api.py::TestAuth -v`
- `python -m pytest tests/test_admin_api.py::TestBlockRule tests/test_admin_api.py::TestPrivateBlockFlow -v`
- `python -m pytest tests/test_admin_web_debug.py::test_db_page_contains_grouped_search_pagination_and_preview_ui -v`

提交生产代码前仍按项目规则运行：

- `python -m pytest tests/ -v`

## 验收标准

实现完成后必须满足：

1. `api/admin/db_browser_routes.py` 存在并承接只读 DB Browser 路由。
2. `api/admin_routes.py` 不再定义 DB Browser 真实路由函数体，只 include 新 router 并保留兼容导出。
3. 三条 Browser HTTP 路径完全不变。
4. 旧 `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 对新路由仍生效。
5. 旧 `api.admin_routes.DbQuery` 等兼容导出可用。
6. DB Browser 路由未重复注册。
7. `tests/test_admin_db_browser.py`、admin auth、private block 联动和 WebUI DB 页面测试通过。
8. 全量测试通过后再提交实现阶段改动。
9. 没有新增除 `main` guard 以外的 `asyncio.run()`。
10. 没有新增同步函数包装 awaitable 的模式。

## 子 agent 分工建议

实现阶段可继续使用子 agent，但生产代码写入范围应避免重叠：

- 主线程或单个 writer 持有 `api/admin_routes.py` 与 `api/admin/db_browser_routes.py`，
  因为这两个文件存在迁移与 re-export 的强耦合。
- 测试 writer 可只修改 `tests/test_admin_db_browser.py`，先补红灯测试。
- 只读 verifier 可并行审计 import 清理、路由注册和测试覆盖，但不得修改生产代码。

若后续继续拆 `/db/backup` 和 `/db/vacuum`，应单独写第二阶段设计，重点处理
`DATABASE_URL` 相对路径解析和 vacuum 审计测试。

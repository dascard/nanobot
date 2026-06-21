# Admin DB Browser 拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `api/admin_routes.py` 中只读 DB Browser 路由拆到 `api/admin/db_browser_routes.py`，保持 HTTP 路径、响应契约、鉴权 monkeypatch 兼容和旧导入路径不变。

**架构：** `api.admin_routes` 继续作为 `/api/v1/admin` 顶层 router，并 include 新的 `api.admin.db_browser_routes.router`。新模块只承接 `GET /db/tables`、`GET /db/tables/{table_name}`、`POST /db/query` 及其模型、常量和 helper；`/db/backup`、`/db/vacuum` 留在旧文件。`api.admin_routes` 通过 re-export 保留 DB Browser 旧符号。

**技术栈：** Python 3.12、FastAPI、Pydantic、SQLAlchemy、pytest、项目既有 admin 子路由模式。

---

## 文件职责

- 创建：`api/admin/db_browser_routes.py`
  - 定义 `router = APIRouter(prefix="/db", tags=["admin-db-browser"])`。
  - 持有 `DbQuery`、DB Browser 表策略常量、SQL guard、结果序列化 helper 和三条只读 Browser 路由。
  - 从 `api.admin.common` 导入 `verify_admin`，从 `core.database` 导入 `get_db`。
- 修改：`api/admin_routes.py`
  - include `db_browser_router`。
  - 删除 DB Browser 真实实现块。
  - re-export `DbQuery`、表策略常量、helper 和三条路由函数。
  - 保留 `/db/backup`、`/db/vacuum` 原实现。
- 修改：`tests/test_admin_db_browser.py`
  - 新增路由迁出红灯测试。
  - 新增旧导入兼容测试。
  - 新增 legacy token monkeypatch 回归测试。
  - 新增路由不重复注册测试。
- 修改：`docs/todo.md`
  - 在「超大文件 >800 行拆分」条目下补充 `api/admin_routes.py` DB Browser 第一刀进展，不勾选整项。
- 修改：`docs/plan_walkthrough.md`
  - 追加本阶段执行记录、提交号和验证结果。

## 任务 1：补 DB Browser 路由拆分红灯测试

**文件：**
- 修改：`tests/test_admin_db_browser.py`

- [x] **步骤 1：新增 route 检查 helper**

在 `tests/test_admin_db_browser.py` 的 `_auth_header()` 后添加：

```python
def _admin_routes_for(path: str):
    from server import app

    return [
        route
        for route in app.routes
        if getattr(route, "path", "") == path
    ]
```

- [x] **步骤 2：新增路由实现模块测试**

在 helper 后添加：

```python
def test_db_browser_routes_are_registered_from_split_module():
    expected = {
        "/api/v1/admin/db/tables",
        "/api/v1/admin/db/tables/{table_name}",
        "/api/v1/admin/db/query",
    }

    for path in expected:
        routes = _admin_routes_for(path)
        assert routes, f"missing route: {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.db_browser_routes"}
```

实现前预期失败：endpoint 仍来自 `api.admin_routes`。

- [x] **步骤 3：新增旧导入兼容测试**

继续添加：

```python
def test_legacy_admin_routes_db_browser_imports_still_work():
    from api import admin_routes
    from api.admin import db_browser_routes

    names = [
        "DbQuery",
        "DB_TABLE_GROUPS",
        "READONLY_TABLES",
        "READONLY_TABLE_SET",
        "BLOCKED_DB_TABLES",
        "GLOBAL_REDACT_COLUMNS",
        "GLOBAL_PREVIEW_ONLY_COLUMNS",
        "DEFAULT_DB_TABLE_POLICY",
        "DB_TABLE_POLICIES",
        "_db_table_policy",
        "_db_table_meta",
        "_quote_identifier",
        "_table_columns",
        "_safe_serialize_cell",
        "_serialize_db_rows",
        "_extract_query_table_names",
        "_validate_query_tables_allowed",
        "_validate_readonly_query",
        "_available_readonly_tables",
        "_available_db_groups",
        "list_tables",
        "query_table",
        "execute_readonly_query",
    ]

    for name in names:
        assert getattr(admin_routes, name) is getattr(db_browser_routes, name)
    assert admin_routes.DbQuery(query="SELECT 1").query == "SELECT 1"
```

实现前预期失败：`api.admin.db_browser_routes` 模块不存在。

- [x] **步骤 4：新增 token monkeypatch 回归测试**

继续添加：

```python
def test_split_db_browser_uses_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/db/tables",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/db/tables",
        headers=_auth_header(),
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401
```

该测试在拆分前可能已通过，但拆分后用于防止新模块直接读取 `config.NANOBOT_ADMIN_TOKEN`。

- [x] **步骤 5：新增路由未重复注册测试**

继续添加：

```python
def test_db_browser_routes_are_not_registered_twice():
    expected = {
        "/api/v1/admin/db/tables",
        "/api/v1/admin/db/tables/{table_name}",
        "/api/v1/admin/db/query",
    }

    for path in expected:
        assert len(_admin_routes_for(path)) == 1
```

该测试在拆分前可能已通过，但拆分后用于防止旧路由未删、新 router 又 include。

- [x] **步骤 6：运行红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest \
  tests/test_admin_db_browser.py::test_db_browser_routes_are_registered_from_split_module \
  tests/test_admin_db_browser.py::test_legacy_admin_routes_db_browser_imports_still_work \
  -v
```

预期：

```text
FAILED tests/test_admin_db_browser.py::test_db_browser_routes_are_registered_from_split_module
FAILED tests/test_admin_db_browser.py::test_legacy_admin_routes_db_browser_imports_still_work
```

如果这两个测试在生产代码迁移前直接通过，需要先检查当前分支是否已经存在新模块。

- [x] **步骤 7：提交红灯测试**

红灯测试可以和生产迁移放在同一最终实现提交中，不单独提交。若需要中途保存，只暂存：

```bash
git add tests/test_admin_db_browser.py
```

## 任务 2：创建 `api.admin.db_browser_routes` 并迁移实现

**文件：**
- 创建：`api/admin/db_browser_routes.py`
- 修改：`api/admin_routes.py`

- [x] **步骤 1：创建新模块头部**

新建 `api/admin/db_browser_routes.py`，头部结构如下：

```python
"""Admin DB Browser 路由。"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.admin.common import verify_admin
from core.database import get_db

logger = logging.getLogger("nanobot.admin")
router = APIRouter(prefix="/db", tags=["admin-db-browser"])
```

不要从 `api.admin_routes` 导入 `verify_admin`、`router`、`logger` 或 `_audit_request`。

- [x] **步骤 2：迁移 `DbQuery`**

从 `api/admin_routes.py` 移出：

```python
class DbQuery(BaseModel):
    query: str
```

放入 `api/admin/db_browser_routes.py`。

- [x] **步骤 3：迁移 DB Browser 常量**

从 `api/admin_routes.py` 移出并原样放入新模块：

- `DB_TABLE_GROUPS`
- `READONLY_TABLES`
- `READONLY_TABLE_SET`
- `BLOCKED_DB_TABLES`
- `GLOBAL_REDACT_COLUMNS`
- `GLOBAL_PREVIEW_ONLY_COLUMNS`
- `DEFAULT_DB_TABLE_POLICY`
- `DB_TABLE_POLICIES`

迁移时保持列表顺序、表名、策略字段和字符串完全不变。

- [x] **步骤 4：迁移 DB Browser helper**

从 `api/admin_routes.py` 移出并原样放入新模块：

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

确认 `_quote_identifier()` 仍使用 `HTTPException(400, ...)`，`_validate_readonly_query()`
仍允许末尾单个分号，禁止词仍用单词边界匹配。

- [x] **步骤 5：迁移三条只读 Browser 路由**

从 `api/admin_routes.py` 移出 `list_tables()`、`query_table()`、
`execute_readonly_query()`，放入新模块并调整装饰器：

```python
@router.get("/tables")
def list_tables(db: Session = Depends(get_db), _auth=Depends(verify_admin)):
    ...


@router.get("/tables/{table_name}")
def query_table(
    table_name: str,
    page: int = 1,
    limit: int = 50,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    ...


@router.post("/query")
def execute_readonly_query(
    body: DbQuery,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    ...
```

函数体保持原语义：

- 内部 SQL 错误继续 `logger.exception(...)` 后 `HTTPException(500, "内部错误")`。
- 表浏览 `limit` 继续钳制到 200。
- SQL 查询继续用 `SELECT * FROM ({q}) LIMIT 500`。

- [x] **步骤 6：在 `api/admin_routes.py` include 新 router**

在现有 admin 子路由导入区加入：

```python
from api.admin.db_browser_routes import router as db_browser_router
```

在 `router.include_router(...)` 区域加入：

```python
router.include_router(db_browser_router)
```

建议放在 `system_router` 之后或现有 include 列表中靠前位置。路径不能变化。

- [x] **步骤 7：在 `api/admin_routes.py` 保留兼容导出**

在新 router import 附近增加兼容导入：

```python
from api.admin.db_browser_routes import (
    BLOCKED_DB_TABLES,
    DB_TABLE_GROUPS,
    DB_TABLE_POLICIES,
    DEFAULT_DB_TABLE_POLICY,
    GLOBAL_PREVIEW_ONLY_COLUMNS,
    GLOBAL_REDACT_COLUMNS,
    READONLY_TABLES,
    READONLY_TABLE_SET,
    DbQuery,
    _available_db_groups,
    _available_readonly_tables,
    _db_table_meta,
    _db_table_policy,
    _extract_query_table_names,
    _quote_identifier,
    _safe_serialize_cell,
    _serialize_db_rows,
    _table_columns,
    _validate_query_tables_allowed,
    _validate_readonly_query,
    execute_readonly_query,
    list_tables,
    query_table,
)
```

如果 lint 对下划线导入有意见，本项目当前没有强制 lint gate；优先保持兼容。

- [x] **步骤 8：不要迁移 `/db/backup` 和 `/db/vacuum`**

确认 `download_backup()` 和 `db_vacuum()` 仍留在 `api/admin_routes.py`，其装饰器仍为：

```python
@router.get("/db/backup")
@router.post("/db/vacuum")
```

不要顺手改路径解析或审计逻辑。

- [x] **步骤 9：运行红灯测试验证变绿**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest \
  tests/test_admin_db_browser.py::test_db_browser_routes_are_registered_from_split_module \
  tests/test_admin_db_browser.py::test_legacy_admin_routes_db_browser_imports_still_work \
  tests/test_admin_db_browser.py::test_split_db_browser_uses_legacy_admin_token_monkeypatch \
  tests/test_admin_db_browser.py::test_db_browser_routes_are_not_registered_twice \
  -v
```

预期：

```text
4 passed
```

## 任务 3：验证 DB Browser 行为兼容

**文件：**
- 创建：`api/admin/db_browser_routes.py`
- 修改：`api/admin_routes.py`
- 修改：`tests/test_admin_db_browser.py`

- [x] **步骤 1：运行 DB Browser 全文件回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/test_admin_db_browser.py -v
```

预期：

```text
14 passed
```

实际数量以新增测试后的 pytest 输出为准，但必须是 0 failures。

- [x] **步骤 2：运行 admin auth 回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/test_admin_api.py::TestAuth -v
```

预期：

```text
5 passed
```

- [x] **步骤 3：运行 private block 与 DB Browser 联动回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest \
  tests/test_admin_api.py::TestBlockRule \
  tests/test_admin_api.py::TestPrivateBlockFlow::test_blocked_user_chat_writes_log_with_files \
  -v
```

预期：

```text
3 passed
```

- [x] **步骤 4：运行 WebUI DB 页面静态契约回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/test_admin_web_debug.py::test_db_page_contains_grouped_search_pagination_and_preview_ui -v
```

预期：

```text
1 passed
```

- [x] **步骤 5：检查 `asyncio.run()` 约束**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/test_asyncio_run_policy.py::test_asyncio_run_only_appears_under_main_guard -v
```

预期：

```text
1 passed
```

- [x] **步骤 6：核对文件行数变化**

运行：

```bash
wc -l api/admin_routes.py api/admin/db_browser_routes.py
```

记录结果。`api/admin_routes.py` 应减少 DB Browser 代码体积；整项「超大文件 >800 行拆分」
仍不能标记完成。

### 已执行验证记录

- 红灯：新增路由迁出和旧导入兼容测试在生产迁移前运行，结果为
  `2 failed, 1 warning`；失败点分别是 DB Browser endpoint 仍来自
  `api.admin_routes`、`api.admin.db_browser_routes` 模块不存在。
- 绿灯首次运行：生产迁移后边界测试结果为 `2 failed, 2 passed, 21 warnings`；
  失败原因是测试 helper 未展开 FastAPI `_IncludedRouter`，不是生产路由缺失。
- 绿灯修正：递归展开 `api.admin_routes.router` 的 included router 后，新增边界
  测试结果为 `4 passed, 21 warnings`。
- DB Browser 回归：`python -m pytest tests/test_admin_db_browser.py -v`
  -> `14 passed, 21 warnings in 4.30s`。
- Admin auth 回归：`python -m pytest tests/test_admin_api.py::TestAuth -v`
  -> `5 passed, 1 warning in 1.36s`。
- Private block 联动：`python -m pytest tests/test_admin_api.py::TestBlockRule
  tests/test_admin_api.py::TestPrivateBlockFlow::test_blocked_user_chat_writes_log_with_files -v`
  -> `3 passed, 1 warning in 1.16s`。
- WebUI DB 页面：`python -m pytest
  tests/test_admin_web_debug.py::test_db_page_contains_grouped_search_pagination_and_preview_ui -v`
  -> `1 passed, 1 warning in 0.72s`。
- `asyncio.run` 约束：`python -m pytest
  tests/test_asyncio_run_policy.py::test_asyncio_run_only_appears_under_main_guard -v`
  -> `1 passed, 1 warning in 1.79s`。
- 行数：`api/admin_routes.py` 5535 行，`api/admin/db_browser_routes.py` 374 行。
- 全量：`python -m pytest tests/ -v`
  -> `1482 passed, 6 skipped, 139 warnings in 105.96s`。

## 任务 4：同步计划状态文档

**文件：**
- 修改：`.Codex/plans/admin-db-browser-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [x] **步骤 1：标记本计划已完成步骤**

在 `.Codex/plans/admin-db-browser-split.md` 中把已经完成的步骤复选框改成 `[x]`。
保持后续 `/db/backup`、`/db/vacuum` 和其他 admin 子域拆分不在本计划内。

- [x] **步骤 2：更新 `docs/todo.md`**

在「超大文件 >800 行拆分」条目下补充状态说明：

```markdown
  - 进展：`api/admin_routes.py` 第一刀已拆出只读 DB Browser 到
    `api/admin/db_browser_routes.py`；`/db/backup`、`/db/vacuum` 及其他 admin 子域仍留在旧文件。
```

不要把该待办项改为 `[x]`。

- [x] **步骤 3：更新 `docs/plan_walkthrough.md`**

追加新小节：

```markdown
## 2026-06-21 Admin DB Browser 第一刀拆分

状态：第一刀实现完成。只读 DB Browser 的三条路由已迁移到
`api/admin/db_browser_routes.py`，`api/admin_routes.py` 保留顶层 include 和旧符号兼容导出。

验证：
- 红灯：记录新增边界测试失败结果
- 绿灯：记录新增边界测试通过结果
- DB Browser 回归：记录 `tests/test_admin_db_browser.py -v` 结果
- Admin auth 回归：记录 `tests/test_admin_api.py::TestAuth -v` 结果
- Private block 联动：记录结果
- WebUI DB 页面：记录结果
- `asyncio.run` 约束：记录结果
- 全量：记录 `python -m pytest tests/ -v` 结果

后续：继续按超大文件拆分排序处理 `news_search/tool.py` 或下一段 admin 子域。
```

## 任务 5：最终验证与提交

**文件：**
- 创建：`api/admin/db_browser_routes.py`
- 修改：`api/admin_routes.py`
- 修改：`tests/test_admin_db_browser.py`
- 修改：`.Codex/plans/admin-db-browser-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [x] **步骤 1：运行全量测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

预期：

```text
0 failed
```

记录完整 summary，例如：

```text
1478 passed, 6 skipped, 139 warnings
```

- [x] **步骤 2：检查 diff 格式**

运行：

```bash
git diff --check -- \
  api/admin_routes.py \
  api/admin/db_browser_routes.py \
  tests/test_admin_db_browser.py \
  .Codex/plans/admin-db-browser-split.md \
  docs/todo.md \
  docs/plan_walkthrough.md
```

预期：无输出，退出码 0。

- [ ] **步骤 3：只暂存本阶段文件**

运行：

```bash
git add \
  api/admin_routes.py \
  api/admin/db_browser_routes.py \
  tests/test_admin_db_browser.py \
  .Codex/plans/admin-db-browser-split.md \
  docs/todo.md \
  docs/plan_walkthrough.md
```

禁止使用 `git add .` 或 `git add -A`。

- [ ] **步骤 4：复查暂存区**

运行：

```bash
git diff --cached --name-status
git diff --cached --check
```

预期暂存区只包含任务 5 步骤 3 的 6 个文件，且 diff check 无输出。

- [ ] **步骤 5：提交**

运行：

```bash
git commit -m "refactor(管理端): 拆分 DB Browser 路由" \
  -m "将只读 DB Browser 路由迁移到 api.admin.db_browser_routes。" \
  -m "api.admin_routes 保留顶层 include 和旧符号兼容导出。" \
  -m "验证：python -m pytest tests/ -v。"
```

预期：生成一个只包含本阶段文件的提交。

## 自检清单

- [x] 规格覆盖：对应 `2026-06-21-admin-db-browser-split-design.md` 的目标、非目标、兼容性、测试和回滚策略。
- [x] TDD：先新增路由迁出和旧导入兼容测试并看到红灯，再写生产代码。
- [x] 路径：`/api/v1/admin/db/tables`、`/api/v1/admin/db/tables/{table_name}`、`/api/v1/admin/db/query` 保持不变。
- [x] 兼容：`api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 对新路由生效。
- [x] 兼容：`api.admin_routes.DbQuery` 和 DB Browser helper 旧导入路径可用。
- [x] 边界：`/db/backup` 和 `/db/vacuum` 本阶段不迁移。
- [x] 约束：没有新增除 `main` guard 外的 `asyncio.run()`。
- [ ] 提交：只用显式路径 `git add`，不暂存无关 pycache、数据库或既有脏项。

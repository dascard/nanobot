# Admin Group Memory 路由拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `api/admin_routes.py` 中 Group Memory 管理端路由拆到 `api/admin/group_memory_routes.py`，保持 HTTP 路径、响应契约、鉴权 monkeypatch、旧导入路径和审计动作不变。

**架构：** `api.admin_routes` 继续作为 `/api/v1/admin` 顶层 router，并 include 新的 `api.admin.group_memory_routes.router`。新模块承接 Group Memory request model、列表 / 提取 / 注入 / 编辑路由和序列化 helper；`api.admin_routes` 通过 re-export 保留旧符号，并保留 `/overview`、`/groups`、`/groups/{group_id:path}` 与 TimingGate 等非本阶段边界。

**技术栈：** Python 3.12、FastAPI、Pydantic、SQLAlchemy、pytest、项目既有 `api.admin.common` 鉴权与审计 helper。

---

## 当前状态（2026-06-21）

- 设计提交：`0388314 docs(管理端): 设计群记忆路由拆分`。
- 设计文档：`docs/superpowers/specs/2026-06-21-admin-group-memory-routes-split-design.md`。
- 上一阶段全量验证：`1511 passed, 6 skipped, 139 warnings in 107.49s`。
- 本阶段选择：继续拆 `api/admin_routes.py`，优先迁移 Group Memory 管理端路由。
- 本阶段不切换到 `api/routes.py`，不拆 chat / stream / group message / public media。

执行结果（2026-06-21）：

- 红灯：split 目标测试 `2 failed, 1 warning in 5.66s`；失败点为 endpoint module
  仍是 `api.admin_routes`，且 `api.admin.group_memory_routes` 尚不存在。
- 绿灯：`tests/test_admin_group_memory_routes_split.py -q` ->
  `5 passed, 21 warnings in 1.31s`。
- 定向回归：Group Memory 行为回归 `14 passed, 21 warnings in 1.71s`；
  鉴权与 asyncio 策略回归 `9 passed, 1 warning in 2.49s`。
- 静态检查：`compileall`、`git diff --check`、facade 同一性检查和 token 级反向导入
  / 旧 helper 扫描均通过。原纯子串扫描会把 `normalize_group_session_id()` 误报为
  `_group_session_id`，实际收口改用 token 级精确扫描。
- 行数：`api/admin_routes.py` 4731 行，`api/admin/group_memory_routes.py` 281 行，
  `tests/test_admin_group_memory_routes_split.py` 111 行。
- 全量测试：`python -m pytest tests/ -v` ->
  `1517 passed, 6 skipped, 139 warnings in 107.94s`。
- 文档收口提交前复跑：`python -m pytest tests/ -v` ->
  `1517 passed, 6 skipped, 139 warnings in 108.71s`。
- 实现提交：`925c110 refactor(管理端): 拆分群记忆路由`。

## 子 agent 分工约定

- **Agent A：测试契约。** 只修改 `tests/test_admin_group_memory_routes_split.py` 和 `tests/test_admin_api.py` 中 `TestObservabilityAPI` 的新增 legacy list 行为测试，不改生产代码。输出红灯测试结果和失败原因。
- **Agent B：新模块草稿。** 只创建 `api/admin/group_memory_routes.py`，不改 `api/admin_routes.py`。按本计划的接口清单迁移实现，内部使用 `api.admin.common.verify_admin` 和 `audit_request`。
- **Agent C：主模块集成。** 只修改 `api/admin_routes.py`。负责 include `group_memory_router`、re-export 旧符号、删除旧 route decorator 实现，并确保 include 顺序早于 `/groups/{group_id:path}`。
- **Agent D：验证审查。** 只读检查 `git diff`、路由注册、行数、`asyncio.run` 策略和测试输出。不得修改代码。

接口约定：

- 新模块必须导出 `router`，且 `router = APIRouter(tags=["admin-group-memory"])`，不要带 `/api/v1/admin` 前缀。
- 新模块必须导出所有迁移 request model、helper 和 endpoint 函数。
- 新模块不得从 `api.admin_routes` 导入 `verify_admin`、`_audit_request`、`router`、`_raw_group_id()`、`_group_session_id()` 或 `_group_stream_id()`，避免循环依赖和边界扩大。
- 新模块使用 `api.admin.common.audit_request`；生产代码中调用名为 `audit_request(...)`。
- 生产代码不得新增 `asyncio.run()`，不得新增同步函数包装 awaitable。

## 文件职责

- 创建：`tests/test_admin_group_memory_routes_split.py`
  - 锁定 8 个 Group Memory 路由的 endpoint module 已迁移到 `api.admin.group_memory_routes`。
  - 锁定 `api.admin_routes` 对 request model、helper 和 endpoint 函数的旧导入兼容。
  - 锁定新模块仍使用旧 token monkeypatch 契约。
  - 锁定关键路由未重复注册。
  - 锁定 `/groups/{group_id:path}/memories` 系列路由先于 `/groups/{group_id:path}` 注册。
- 创建：`api/admin/group_memory_routes.py`
  - 定义 `router = APIRouter(tags=["admin-group-memory"])`。
  - 持有 Group Memory request model。
  - 持有 `_group_memory_row_dict()`、`_group_memories_payload()` 和 `_extract_group_memories_response()`。
  - 持有 8 个迁移路由实现。
  - 从 `api.admin.common` 导入 `verify_admin`、`audit_request`。
- 修改：`api/admin_routes.py`
  - include `group_memory_router`，位置早于本地 `/groups/{group_id:path}` route decorator。
  - 从 `api.admin.group_memory_routes` 导入并 re-export 迁移符号。
  - 删除本地 Group Memory route decorator 实现、request model 和 helper。
  - 保留 `_raw_group_id()`、`_group_session_id()`、`_group_stream_id()` 给其他管理端接口使用。
- 修改：`tests/test_admin_api.py`
  - 在 `TestObservabilityAPI` 增加 legacy list 路由行为测试。
- 修改：`docs/todo.md`
  - 记录 `api/admin_routes.py` Group Memory 管理端拆分进展和拆分后的行数。
- 修改：`docs/plan_walkthrough.md`
  - 追加本阶段执行记录、提交号和验证结果，并修正顶部仍指向 TimingGate proposal 的旧口径。
- 修改：`.Codex/plans/admin-group-memory-routes-split.md`
  - 实现完成后勾选已执行步骤并记录红灯、绿灯、定向回归和全量回归结果。

## 任务 1：补 Group Memory 路由拆分红灯测试

**文件：**
- 创建：`tests/test_admin_group_memory_routes_split.py`
- 修改：`tests/test_admin_api.py`

- [x] **步骤 1：创建 split 路由测试文件**

创建 `tests/test_admin_group_memory_routes_split.py`：

```python
from __future__ import annotations


_GROUP_MEMORY_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/admin/groups/{group_id:path}/memories"),
    ("GET", "/api/v1/admin/group-memories/overview"),
    ("GET", "/api/v1/admin/group-memories/{group_id:path}/items"),
    ("POST", "/api/v1/admin/group-memories/{group_id:path}/extract"),
    ("PUT", "/api/v1/admin/group-memories/{group_id:path}/injection-config"),
    ("POST", "/api/v1/admin/group-memories/{group_id:path}/injection-preview"),
    ("PATCH", "/api/v1/admin/group-memories/items/{memory_id}"),
    ("POST", "/api/v1/admin/groups/{group_id:path}/memories/extract"),
)


_GROUP_MEMORY_ROUTE_EXPORTS = (
    "GroupMemoryExtractRequest",
    "GroupMemoryInjectionConfigRequest",
    "GroupMemoryInjectionPreviewRequest",
    "GroupMemoryUpdateRequest",
    "_group_memory_row_dict",
    "_group_memories_payload",
    "_extract_group_memories_response",
    "group_memories_list",
    "group_memories_overview",
    "group_memory_items",
    "group_memory_extract_alias",
    "group_memory_injection_config",
    "group_memory_injection_preview",
    "group_memory_update_item",
    "group_memories_extract",
)


def _admin_route_entries():
    from server import app

    def _iter_routes(routes, prefix: str = ""):
        for route in routes:
            endpoint = getattr(route, "endpoint", None)
            route_path = getattr(route, "path", None)
            if endpoint is not None and route_path is not None:
                yield prefix + route_path, route
                continue

            original_router = getattr(route, "original_router", None)
            if original_router is None:
                continue
            include_context = getattr(route, "include_context", None)
            include_prefix = getattr(include_context, "prefix", "")
            yield from _iter_routes(original_router.routes, prefix + include_prefix)

    return list(_iter_routes(app.routes))


def _admin_routes_for(path: str, method: str | None = None):
    return [
        route
        for route_path, route in _admin_route_entries()
        if route_path == path and (method is None or method in getattr(route, "methods", set()))
    ]


def test_admin_group_memory_routes_are_registered_from_split_module():
    for method, path in _GROUP_MEMORY_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.group_memory_routes"}


def test_legacy_admin_routes_group_memory_imports_still_work():
    from api import admin_routes
    from api.admin import group_memory_routes

    for name in _GROUP_MEMORY_ROUTE_EXPORTS:
        assert getattr(admin_routes, name) is getattr(group_memory_routes, name)

    assert admin_routes.GroupMemoryExtractRequest(window_hours=1).window_hours == 1


def test_split_group_memory_routes_use_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/group-memories/overview",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/group-memories/overview",
        headers={"Authorization": "Bearer test-token"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_admin_group_memory_routes_are_not_registered_twice():
    for method, path in _GROUP_MEMORY_ROUTE_SIGNATURES:
        assert len(_admin_routes_for(path, method)) == 1, f"{method} {path}"


def test_group_memory_routes_are_registered_before_group_detail_catchall():
    route_paths = [path for path, _route in _admin_route_entries()]

    detail_index = route_paths.index("/api/v1/admin/groups/{group_id:path}")
    list_index = route_paths.index("/api/v1/admin/groups/{group_id:path}/memories")
    extract_index = route_paths.index("/api/v1/admin/groups/{group_id:path}/memories/extract")

    assert list_index < detail_index
    assert extract_index < detail_index
```

- [x] **步骤 2：补 legacy list 行为测试**

在 `tests/test_admin_api.py::TestObservabilityAPI` 的
`test_group_memory_items_endpoint_returns_memories_without_group_route_shadow` 后添加：

```python
    def test_group_memories_legacy_list_endpoint_returns_memories_without_group_route_shadow(self, client, auth_header):
        with next(app.dependency_overrides[get_db]()) as db:
            db.add(GroupMemory(
                group_id="group_7788",
                memory_type="topic",
                content="模型部署: 群里经常讨论本地模型部署",
                content_hash="legacy-list-hash",
                confidence=0.65,
                evidence_count=1,
                evidence_log_ids_json="[1, 2]",
                status="active",
                source="manual_group_memory_extract",
            ))
            db.commit()

        data = _ok(client.get(
            "/api/v1/admin/groups/group_7788/memories",
            headers=auth_header,
        ))

        assert data["group_id"] == "group_7788"
        assert len(data["memories"]) == 1
        assert data["memories"][0]["content"].startswith("模型部署")
```

- [x] **步骤 3：运行红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_group_memory_routes_split.py::test_admin_group_memory_routes_are_registered_from_split_module \
  tests/test_admin_group_memory_routes_split.py::test_legacy_admin_routes_group_memory_imports_still_work \
  -v
```

预期：

```text
FAILED tests/test_admin_group_memory_routes_split.py::test_admin_group_memory_routes_are_registered_from_split_module
FAILED tests/test_admin_group_memory_routes_split.py::test_legacy_admin_routes_group_memory_imports_still_work
```

允许的失败原因：

- endpoint module 仍是 `api.admin_routes`。
- `ModuleNotFoundError: No module named 'api.admin.group_memory_routes'`。

如果生产代码迁移前这两个测试直接通过，先运行 `git status --short` 和 `git log -1 --oneline`，确认当前分支是否已有同名拆分。

## 任务 2：创建 `api.admin.group_memory_routes`

**文件：**
- 创建：`api/admin/group_memory_routes.py`

- [x] **步骤 1：创建新模块头部和 request model**

创建 `api/admin/group_memory_routes.py`，头部结构如下：

```python
"""Admin Group Memory 路由。"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.admin.common import audit_request, verify_admin
from core.database import ChatStreamConfig, get_db

router = APIRouter(tags=["admin-group-memory"])


class GroupMemoryExtractRequest(BaseModel):
    window_hours: int = Field(default=24, ge=0, le=720)
    instructions: str = ""


class GroupMemoryInjectionConfigRequest(BaseModel):
    group_profile_mode: Literal["off", "preview", "on"] = "on"


class GroupMemoryInjectionPreviewRequest(BaseModel):
    user_input: str = ""
    max_items: int = Field(default=10, ge=1, le=30)
    max_chars: int = Field(default=1200, ge=200, le=4000)


class GroupMemoryUpdateRequest(BaseModel):
    content: Optional[str] = None
    status: Optional[Literal["review", "active", "disabled", "archived", "rejected"]] = None
    inject_policy: Optional[Literal["auto", "manual_only", "never"]] = None
    disabled_reason: Optional[str] = None
    rejected_reason: Optional[str] = None
```

从 `api/admin_routes.py` 删除这 4 个 request model。

- [x] **步骤 2：迁移 helper**

从 `api/admin_routes.py` 剪切以下 helper 到新模块，函数体保持现有语义：

- `_group_memory_row_dict(r) -> dict`
- `_group_memories_payload(db: Session, group_id: str, memory_type: str = "") -> dict`
- `_extract_group_memories_response(group_id: str, body: GroupMemoryExtractRequest, request: Request, db: Session) -> dict`

迁移后：

- `_group_memories_payload()` 继续在函数内导入 `GroupMemory` 和 `normalize_group_session_id`。
- `_extract_group_memories_response()` 继续在函数内导入 `app.group_memory.extraction_service`。
- `_extract_group_memories_response()` 内部把 `_audit_request(...)` 改为 `audit_request(...)`。

- [x] **步骤 3：迁移路由实现**

从 `api/admin_routes.py` 剪切以下路由到新模块，保持函数名、路径、参数、docstring、返回结构和异常状态码不变：

- `group_memories_list`
- `group_memories_overview`
- `group_memory_items`
- `group_memory_extract_alias`
- `group_memory_injection_config`
- `group_memory_injection_preview`
- `group_memory_update_item`
- `group_memories_extract`

迁移时做以下机械调整：

```python
_audit_request(
```

改为：

```python
audit_request(
```

不要迁移：

- `_raw_group_id()`
- `_group_session_id()`
- `_group_stream_id()`
- `overview()`
- `list_groups()`
- `group_detail()`
- `timing_gate_events()`

- [x] **步骤 4：检查新模块没有循环依赖和禁止项**

运行：

```bash
python - <<'PY'
from pathlib import Path
text = Path("api/admin/group_memory_routes.py").read_text(encoding="utf-8")
blocked = [
    "from api.admin_routes import",
    "import api.admin_routes",
    "_audit_request",
    "_raw_group_id",
    "_group_session_id",
    "_group_stream_id",
    "asyncio.run(",
    "run_awaitable_sync(",
]
hits = [item for item in blocked if item in text]
if hits:
    raise SystemExit("\n".join(hits))
PY
```

预期：无输出，退出码为 0。

## 任务 3：集成 `api.admin_routes` facade

**文件：**
- 修改：`api/admin_routes.py`

- [x] **步骤 1：导入 group memory router 和 re-export 符号**

在既有 admin 子路由 import 附近添加：

```python
from api.admin.group_memory_routes import (
    GroupMemoryExtractRequest,
    GroupMemoryInjectionConfigRequest,
    GroupMemoryInjectionPreviewRequest,
    GroupMemoryUpdateRequest,
    _extract_group_memories_response,
    _group_memories_payload,
    _group_memory_row_dict,
    group_memories_extract,
    group_memories_list,
    group_memories_overview,
    group_memory_extract_alias,
    group_memory_injection_config,
    group_memory_injection_preview,
    group_memory_items,
    group_memory_update_item,
    router as group_memory_router,
)
```

在已有 include 区域添加：

```python
router.include_router(group_memory_router)
```

该 include 必须位于本地 `@router.get("/groups/{group_id:path}")` decorator 之前。

- [x] **步骤 2：删除旧模块中的迁移实现块**

从 `api/admin_routes.py` 删除以下本地定义：

- `GroupMemoryExtractRequest`
- `GroupMemoryInjectionConfigRequest`
- `GroupMemoryInjectionPreviewRequest`
- `GroupMemoryUpdateRequest`
- `group_memories_list()`
- `_group_memory_row_dict()`
- `_group_memories_payload()`
- `group_memories_overview()`
- `group_memory_items()`
- `group_memory_extract_alias()`
- `group_memory_injection_config()`
- `group_memory_injection_preview()`
- `group_memory_update_item()`
- `_extract_group_memories_response()`
- `group_memories_extract()`

不要删除：

- `verify_admin()`
- `_audit()` / `_audit_request()`
- `_raw_group_id()` / `_group_session_id()` / `_group_stream_id()`
- `_timing_meta()` / `_timing_event_dict()` / `_timing_stats()`
- `overview()`
- `list_groups()`
- `group_detail()`
- `timing_gate_events()`

- [x] **步骤 3：确认 facade helper 和 endpoint 同一性**

运行：

```bash
python - <<'PY'
from api import admin_routes
from api.admin import group_memory_routes

names = [
    "GroupMemoryExtractRequest",
    "GroupMemoryInjectionConfigRequest",
    "GroupMemoryInjectionPreviewRequest",
    "GroupMemoryUpdateRequest",
    "_group_memory_row_dict",
    "_group_memories_payload",
    "_extract_group_memories_response",
    "group_memories_list",
    "group_memories_overview",
    "group_memory_items",
    "group_memory_extract_alias",
    "group_memory_injection_config",
    "group_memory_injection_preview",
    "group_memory_update_item",
    "group_memories_extract",
]
for name in names:
    assert getattr(admin_routes, name) is getattr(group_memory_routes, name), name
assert admin_routes.group_detail.__module__ == "api.admin_routes"
PY
```

预期：无输出，退出码为 0。

## 任务 4：运行红绿测试和定向回归

**文件：**
- 验证：`tests/test_admin_group_memory_routes_split.py`
- 验证：`tests/test_admin_api.py`
- 验证：`tests/test_asyncio_run_policy.py`

- [x] **步骤 1：运行 split 绿灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_group_memory_routes_split.py -q
```

预期：

```text
5 passed
```

- [x] **步骤 2：运行 Group Memory 行为回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_group_memory_routes_split.py \
  tests/test_admin_api.py::TestObservabilityAPI \
  -q
```

预期：

```text
14 passed
```

如果用例数量因新增测试或既有测试调整变化，以 `0 failed` 为硬门禁，并在计划收口时记录实际数量。

- [x] **步骤 3：运行鉴权与 asyncio 策略回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_api.py::TestAuth \
  tests/test_asyncio_run_policy.py \
  -q
```

预期：

```text
9 passed
```

- [x] **步骤 4：运行静态检查**

运行：

```bash
python -m compileall api/admin_routes.py api/admin/group_memory_routes.py -q
```

预期：无输出，退出码为 0。

运行：

```bash
git diff --check -- \
  api/admin_routes.py \
  api/admin/group_memory_routes.py \
  tests/test_admin_group_memory_routes_split.py \
  tests/test_admin_api.py
```

预期：无输出，退出码为 0。

运行：

```bash
python - <<'PY'
from pathlib import Path
text = Path("api/admin/group_memory_routes.py").read_text(encoding="utf-8")
blocked = [
    "from api.admin_routes import",
    "import api.admin_routes",
    "asyncio.run(",
    "run_awaitable_sync(",
]
hits = [item for item in blocked if item in text]
if hits:
    raise SystemExit("\n".join(hits))
PY
```

预期：无输出，退出码为 0。

## 任务 5：同步待办与 walkthrough

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/admin-group-memory-routes-split.md`

- [x] **步骤 1：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」的 `api/admin_routes.py` 进展下追加：

```markdown
  - 进展：`api/admin_routes.py` 第三刀已拆出 Group Memory 管理端路由到
    `api/admin/group_memory_routes.py`；旧 `api.admin_routes` 继续 re-export
    迁移后的 request model、helper 和 endpoint，保留 admin token monkeypatch、
    HTTP 路径和路由顺序兼容。
```

同时更新同段中的 `api/admin_routes.py` 行数为实现后的 `wc -l api/admin_routes.py` 实际结果。

- [x] **步骤 2：追加 `docs/plan_walkthrough.md` 阶段记录**

在文末追加：

```markdown
## 2026-06-21 Admin Group Memory 路由拆分

状态：实现、验证和实现阶段提交准备已完成。`api/admin_routes.py` 已拆出
Group Memory 管理端路由到 `api/admin/group_memory_routes.py`；旧
`api.admin_routes` 继续 include 新 router，并 re-export 迁移符号保持旧导入兼容。

- 设计提交：`0388314 docs(管理端): 设计群记忆路由拆分`。
- 计划提交：`0f43e62 docs(计划): 记录群记忆路由拆分计划`。
- 实现提交：`925c110 refactor(管理端): 拆分群记忆路由`。

已完成：

- [x] 新增 `tests/test_admin_group_memory_routes_split.py`，覆盖 endpoint module、
  legacy import、token monkeypatch、重复注册和 group detail catch-all 顺序。
- [x] 在 `tests/test_admin_api.py::TestObservabilityAPI` 补 legacy list 路由行为回归。
- [x] 新增 `api/admin/group_memory_routes.py`，迁移 Group Memory request model、
  helper 和 8 个路由；新模块使用 `api.admin.common.verify_admin` 和
  `audit_request`，不反向导入 `api.admin_routes`。
- [x] `api/admin_routes.py` include `group_memory_router`，并 re-export 迁移符号。

验证：

- 红灯：split 目标测试 `2 failed, 1 warning in 5.66s`。
- 绿灯：split 测试 `5 passed, 21 warnings in 1.31s`。
- 定向回归：Group Memory 行为回归 `14 passed, 21 warnings in 1.71s`；
  鉴权与 asyncio 策略回归 `9 passed, 1 warning in 2.49s`。
- 静态检查：`compileall`、`git diff --check`、facade 同一性检查和 token 级
  反向导入 / 旧 helper 扫描均通过；行数为 `api/admin_routes.py` 4731 行、
  `api/admin/group_memory_routes.py` 281 行。
- 全量测试：`1517 passed, 6 skipped, 139 warnings in 107.94s`。
- 文档收口提交前复跑：`1517 passed, 6 skipped, 139 warnings in 108.71s`。

下一步：

P3 超大文件队列仍剩 `api/admin_routes.py` 和 `api/routes.py`。继续沿管理端拆分时，
下一刀可考虑 trace / observability 只读边界；切普通 API 前应先设计 `verify_token`
共享兼容层。
```

执行时记录「计划提交」和「实现提交」的实际提交号，不保留泛化占位文本。

- [x] **步骤 3：更新本计划执行状态**

将已完成步骤勾选为 `[x]`，并在顶部「当前状态」后追加执行结果摘要，至少记录：

- 红灯失败摘要。
- 绿灯通过摘要。
- 定向回归结果。
- 静态检查结果。
- 全量测试结果。
- 实现提交号。

## 任务 6：全量验证和实现提交

**文件：**
- 修改：`api/admin_routes.py`
- 创建：`api/admin/group_memory_routes.py`
- 创建：`tests/test_admin_group_memory_routes_split.py`
- 修改：`tests/test_admin_api.py`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/admin-group-memory-routes-split.md`

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

记录完整汇总行，例如：

```text
1512 passed, 6 skipped, 139 warnings in 110.00s
```

- [x] **步骤 2：检查暂存前 diff**

运行：

```bash
git diff --stat -- \
  api/admin_routes.py \
  api/admin/group_memory_routes.py \
  tests/test_admin_group_memory_routes_split.py \
  tests/test_admin_api.py \
  docs/todo.md \
  docs/plan_walkthrough.md \
  .Codex/plans/admin-group-memory-routes-split.md
```

确认 diff 只覆盖本阶段文件。不要暂存 pyc、数据库、快照文件或历史无关脏项。

- [x] **步骤 3：显式暂存目标文件**

运行：

```bash
git add api/admin_routes.py
git add api/admin/group_memory_routes.py
git add tests/test_admin_group_memory_routes_split.py
git add tests/test_admin_api.py
git add docs/todo.md
git add docs/plan_walkthrough.md
git add .Codex/plans/admin-group-memory-routes-split.md
git diff --cached --name-status
git diff --cached --check
```

预期暂存区只包含上述 7 个文件，`git diff --cached --check` 无输出。

- [x] **步骤 4：提交实现阶段**

运行：

```bash
git commit -m "refactor(管理端): 拆分群记忆路由"
```

提交后运行：

```bash
git log -1 --oneline --stat
git status --short
```

确认最新提交只包含本阶段文件。历史无关脏项保留未暂存状态。

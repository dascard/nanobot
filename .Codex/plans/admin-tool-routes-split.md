# Admin Tools 路由拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `api/admin_routes.py` 中 `/tools*` 管理端 HTTP 层拆到 `api/admin/tool_routes.py`，保持路径、响应结构、鉴权 monkeypatch、审计语义、runtime tool 生效语义和旧导入路径不变。

**架构：** `api.admin_routes` 继续作为 `/api/v1/admin` 顶层聚合 router，新建 `api.admin.tool_routes.router` 使用 `prefix="/tools"` 并由父 router include。新模块只承接工具管理 HTTP 编排和小型本地 helper，复杂规则继续由 `core.runtime_tool_service`、`core.tool_schema_preview` 和 `core.tool_registry` 提供；父模块通过 re-export 维持旧符号兼容。

**技术栈：** Python 3.12、FastAPI、Pydantic、SQLAlchemy、pytest、项目既有 `api.admin.common` 鉴权与审计 helper。

---

## 当前状态（2026-06-21）

- [x] 已核对 `docs/todo.md` 剩余硬项：`api/admin_routes.py` 和 `api/routes.py` 仍超过 800 行。
- [x] 已分派只读子 agent 审查 Admin Tools、普通 API 和 Admin Models 三个候选边界。
- [x] 已选择本阶段目标：先拆 Admin Tools，暂不拆普通 API 或 Admin Models。
- [x] 已写设计文档：`docs/superpowers/specs/2026-06-21-admin-tool-routes-split-design.md`。
- [x] 设计提交：`1b5d58b docs(管理端): 设计工具路由拆分`。
- [x] 设计阶段全量验证：`python -m pytest tests/ -v` -> `1523 passed, 6 skipped, 139 warnings in 110.42s`。
- [x] 计划提交：`0871bb9 docs(计划): 记录工具路由拆分计划`。
- [x] TDD 红灯测试验证：`4 failed, 2 passed, 21 warnings in 7.55s`；红灯不单独提交，绿灯后与实现一起提交。
- [x] 实现拆分并提交：`75f0089 refactor(管理端): 拆分工具路由`。
- [x] 更新 `docs/todo.md`、`docs/plan_walkthrough.md` 和本计划执行结果并提交。

执行结果摘要：

- Split 绿灯：`tests/test_admin_tool_routes_split.py -q` ->
  `6 passed, 21 warnings in 2.17s`。
- 工具行为回归：`tests/test_admin_api.py::TestToolAdmin tests/test_tool_plan.py tests/test_tool_schema_config.py tests/test_final_tools.py -q`
  -> `36 passed, 1 warning in 7.09s`。
- 鉴权与 asyncio 策略回归：`tests/test_admin_api.py::TestAuth tests/test_asyncio_run_policy.py -q`
  -> `9 passed, 1 warning in 2.57s`。
- 静态检查：`compileall`、`git diff --check` 和反向导入 / awaitable 扫描均无输出。
- 行数：`api/admin_routes.py` 3761 行，`api/admin/tool_routes.py` 601 行，
  `tests/test_admin_tool_routes_split.py` 136 行。
- 实现阶段全量测试：`python -m pytest tests/ -v` ->
  `1529 passed, 6 skipped, 139 warnings in 109.75s`。

## 子 agent 分工约定

- **Agent A：测试契约。** 只修改 `tests/test_admin_tool_routes_split.py`，不改生产代码。输出红灯测试结果、失败数量和主要失败原因。
- **Agent B：工具路由模块草稿。** 只创建 `api/admin/tool_routes.py`，不改 `api/admin_routes.py`。按本计划迁移 request model、helper 和 `/tools*` endpoint。
- **Agent C：父模块集成。** 只修改 `api/admin_routes.py`。负责 include `tool_router`、re-export 旧符号、删除旧工具区块，并保留其他管理端子域仍使用的 helper。
- **Agent D：验证审查。** 只读检查 `git diff`、路由注册、反向导入、`asyncio.run` 策略、行数和测试输出。不得修改代码。

接口约定：

- `api/admin/tool_routes.py` 导出 `router`，该 router 使用 `prefix="/tools"`，不得带 `/api/v1/admin` 前缀。
- 新模块使用 `api.admin.common.verify_admin`、`audit()` 和 `client_ip()`，不得从 `api.admin_routes` 导入任何符号。
- `api.admin_routes` 必须 re-export 迁移后的 request model、helper 和 endpoint 函数。
- 生产代码不得新增 `asyncio.run()`，不得新增同步函数包装 awaitable。
- 不改变 Prompt Runtime 模板、`enriched_query`、工具 usage 文档或 Prompt Runtime 输入。

## 文件职责

- 创建：`tests/test_admin_tool_routes_split.py`
  - 锁定 10 个 `/api/v1/admin/tools*` route 的 endpoint module 为 `api.admin.tool_routes`。
  - 锁定 `api.admin_routes` 对迁移符号的旧导入兼容。
  - 锁定 `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 对新模块路由仍生效。
  - 锁定迁移路由未重复注册。
  - 锁定 `/tools/targets`、`/tools/effective`、`/tools/decisions` 先于动态 `/{tool_name}` 系列注册。
  - 静态扫描新模块没有反向导入父模块、没有 `asyncio.run`、没有 `run_awaitable_sync`。
- 创建：`api/admin/tool_routes.py`
  - 定义 `router = APIRouter(prefix="/tools", tags=["admin-tools"])`。
  - 持有 `ToolUpdateBody`、`ToolOverrideBody`、`ToolSchemaOverrideBody`。
  - 持有 `_TEMP_TOOL_TARGET_EXACT`、`_TEMP_TOOL_TARGET_PREFIXES`、`_is_temp_tool_target_id()`、`_tool_target_label()`。
  - 本地定义 `_iso()`、`_raw_group_id()`、`_runtime_snapshot()`，避免反向导入父模块。
  - 持有 `list_tools()`、`list_tool_targets()`、`get_tool_schema_override()`、`save_tool_schema_override_api()`、`delete_tool_schema_override_api()`、`update_tool_defaults()`、`set_tool_override()`、`delete_tool_override()`、`get_effective_tools()`、`list_runtime_preset_decisions()`。
- 修改：`api/admin_routes.py`
  - 导入并 include `tool_router`。
  - re-export 迁移符号。
  - 删除本地 `# ── 工具管理 ──` 到 `# ── Model Health Check ──` 前的工具管理实现。
  - 保留仍被 overview、group、settings、model、eval 使用的 `_iso()`、`_raw_group_id()`、`_runtime_snapshot()`、`_audit()`、`_client_ip()` 和相关数据库模型 import。
- 修改：`docs/todo.md`
  - 记录 Admin Tools 拆分进展和 `api/admin_routes.py` 新行数。
- 修改：`docs/plan_walkthrough.md`
  - 追加本阶段设计、计划、实现、验证和提交记录。
- 修改：`.Codex/plans/admin-tool-routes-split.md`
  - 实现完成后勾选已执行步骤并记录红灯、绿灯、定向回归和全量验证结果。

## 任务 1：补 Admin Tools 路由拆分红灯测试

**文件：**
- 创建：`tests/test_admin_tool_routes_split.py`

- [x] **步骤 1：创建 split 路由测试文件**

创建 `tests/test_admin_tool_routes_split.py`：

```python
from __future__ import annotations

from pathlib import Path


_ADMIN_TOOL_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/admin/tools"),
    ("GET", "/api/v1/admin/tools/targets"),
    ("GET", "/api/v1/admin/tools/effective"),
    ("GET", "/api/v1/admin/tools/decisions"),
    ("GET", "/api/v1/admin/tools/{tool_name}/schema"),
    ("PUT", "/api/v1/admin/tools/{tool_name}/schema"),
    ("DELETE", "/api/v1/admin/tools/{tool_name}/schema"),
    ("PUT", "/api/v1/admin/tools/{tool_name}"),
    ("PUT", "/api/v1/admin/tools/{tool_name}/override"),
    ("DELETE", "/api/v1/admin/tools/{tool_name}/override"),
)


_TOOL_ROUTE_EXPORTS = (
    "ToolUpdateBody",
    "ToolOverrideBody",
    "ToolSchemaOverrideBody",
    "_TEMP_TOOL_TARGET_EXACT",
    "_TEMP_TOOL_TARGET_PREFIXES",
    "_is_temp_tool_target_id",
    "_tool_target_label",
    "list_tools",
    "list_tool_targets",
    "get_tool_schema_override",
    "save_tool_schema_override_api",
    "delete_tool_schema_override_api",
    "update_tool_defaults",
    "set_tool_override",
    "delete_tool_override",
    "get_effective_tools",
    "list_runtime_preset_decisions",
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


def test_admin_tool_routes_are_registered_from_split_module():
    for method, path in _ADMIN_TOOL_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.tool_routes"}


def test_legacy_admin_routes_tool_imports_still_work():
    from api import admin_routes
    from api.admin import tool_routes

    for name in _TOOL_ROUTE_EXPORTS:
        assert getattr(admin_routes, name) is getattr(tool_routes, name)

    assert admin_routes.ToolUpdateBody(group_default=True).group_default is True
    assert admin_routes._is_temp_tool_target_id("private_test")
    assert admin_routes._tool_target_label("测试群", "123", "群聊 123") == "测试群 (123)"


def test_split_tool_routes_use_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/tools/effective",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/tools/effective",
        headers={"Authorization": "Bearer test-token"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_admin_tool_routes_are_not_registered_twice():
    for method, path in _ADMIN_TOOL_ROUTE_SIGNATURES:
        assert len(_admin_routes_for(path, method)) == 1, f"{method} {path}"


def test_admin_tool_static_routes_before_dynamic_tool_name_routes():
    route_paths = [path for path, _route in _admin_route_entries()]

    targets_index = route_paths.index("/api/v1/admin/tools/targets")
    effective_index = route_paths.index("/api/v1/admin/tools/effective")
    decisions_index = route_paths.index("/api/v1/admin/tools/decisions")
    dynamic_indices = [
        index
        for index, path in enumerate(route_paths)
        if path.startswith("/api/v1/admin/tools/{tool_name}")
    ]

    assert dynamic_indices
    first_dynamic_index = min(dynamic_indices)
    assert targets_index < first_dynamic_index
    assert effective_index < first_dynamic_index
    assert decisions_index < first_dynamic_index


def test_admin_tool_routes_do_not_import_parent_admin_routes_or_sync_awaitable():
    source = Path("api/admin/tool_routes.py").read_text(encoding="utf-8")

    assert "from api.admin_routes" not in source
    assert "import api.admin_routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
```

- [x] **步骤 2：运行红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_tool_routes_split.py -q
```

预期：失败。失败原因应包含 endpoint module 仍为 `api.admin_routes`、`api.admin.tool_routes` 不存在或静态扫描找不到 `api/admin/tool_routes.py`。

- [x] **步骤 3：记录红灯结果并进入实现**

记录失败数量和主要失败原因。不要提交红灯状态；项目提交门禁要求失败数为 0。红灯测试文件会在实现转绿后与 `api/admin/tool_routes.py` 和 `api/admin_routes.py` 一起提交。

## 任务 2：创建 `api/admin/tool_routes.py`

**文件：**
- 创建：`api/admin/tool_routes.py`
- 参考：`api/admin_routes.py` 中 `# ── 工具管理 ──` 到 `# ── Model Health Check ──` 前的区块

- [x] **步骤 1：创建模块头、router、request model 和 helper**

创建 `api/admin/tool_routes.py`，模块开头使用以下结构：

```python
"""Admin Tools 路由。"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.admin.common import audit, client_ip, verify_admin
from core.database import ChatLog, ChatStreamConfig, ConversationTurn, SystemSetting, User, get_db

logger = logging.getLogger("nanobot.admin")
router = APIRouter(prefix="/tools", tags=["admin-tools"])
```

在该文件内定义：

```python
class ToolUpdateBody(BaseModel):
    private_default: Optional[bool] = None
    private_superuser_default: Optional[bool] = None
    group_default: Optional[bool] = None
    lightweight_default: Optional[bool] = None


class ToolOverrideBody(BaseModel):
    scope_type: str
    scope_id: str
    enabled: bool
    reason: str = ""


class ToolSchemaOverrideBody(BaseModel):
    tool_schema: dict = Field(default_factory=dict, alias="schema")


_TEMP_TOOL_TARGET_EXACT = {
    "admin", "default", "default_session", "local_test", "test",
    "test_session", "test-user", "unknown",
}
_TEMP_TOOL_TARGET_PREFIXES = (
    "fake", "local_", "mock", "pytest", "temp", "tmp", "test",
)


def _iso(v) -> str:
    return v.isoformat(sep=" ", timespec="seconds") if v else ""


def _raw_group_id(group_id: str) -> str:
    raw = str(group_id or "").strip()
    if raw.startswith("group_"):
        return raw.removeprefix("group_")
    if raw.startswith("qq:") and raw.endswith(":group"):
        return raw.removeprefix("qq:").removesuffix(":group")
    return raw


def _runtime_snapshot() -> dict:
    try:
        from core.timing_runtime import get_group_runtime
        runtime = get_group_runtime()
        snapshot = getattr(runtime, "snapshot_states", lambda: {})()
        return snapshot if isinstance(snapshot, dict) else {}
    except Exception:
        return {}


def _is_temp_tool_target_id(value: str) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return True
    lowered = raw.lower()
    if lowered.startswith("private_"):
        return True
    if lowered in _TEMP_TOOL_TARGET_EXACT:
        return True
    if lowered.endswith("_test") or "local_test" in lowered:
        return True
    return any(lowered.startswith(prefix) for prefix in _TEMP_TOOL_TARGET_PREFIXES)


def _tool_target_label(name: str, target_id: str, fallback: str) -> str:
    clean_name = str(name or "").strip()
    clean_id = str(target_id or "").strip()
    if clean_name:
        return f"{clean_name} ({clean_id})" if clean_id else clean_name
    return fallback or clean_id
```

- [x] **步骤 2：迁移 10 个 endpoint 并调整路由前缀**

从 `api/admin_routes.py` 迁移以下函数到新模块，函数体业务逻辑保持不变：

- `list_tools()`
- `list_tool_targets()`
- `get_tool_schema_override()`
- `save_tool_schema_override_api()`
- `delete_tool_schema_override_api()`
- `update_tool_defaults()`
- `set_tool_override()`
- `delete_tool_override()`
- `get_effective_tools()`
- `list_runtime_preset_decisions()`

迁移后 decorator 改为：

```python
@router.get("")
async def list_tools(...):
    ...


@router.get("/targets")
def list_tool_targets(...):
    ...


@router.get("/effective")
def get_effective_tools(...):
    ...


@router.get("/decisions")
def list_runtime_preset_decisions(...):
    ...


@router.get("/{tool_name}/schema")
def get_tool_schema_override(...):
    ...


@router.put("/{tool_name}/schema")
def save_tool_schema_override_api(...):
    ...


@router.delete("/{tool_name}/schema")
def delete_tool_schema_override_api(...):
    ...


@router.put("/{tool_name}")
def update_tool_defaults(...):
    ...


@router.put("/{tool_name}/override")
def set_tool_override(...):
    ...


@router.delete("/{tool_name}/override")
def delete_tool_override(...):
    ...
```

必须保持静态路径 `""`、`"/targets"`、`"/effective"`、`"/decisions"` 在动态 `"/{tool_name}"` 系列之前。

- [x] **步骤 3：替换审计 helper 调用**

迁移时把旧父模块 helper 替换为 `api.admin.common` helper：

```python
_audit(db, "tool_schema_override", "tool", tool_name, {"schema": result["editable_schema"]},
       ip_address=_client_ip(request))
```

改为：

```python
audit(db, "tool_schema_override", "tool", tool_name, {"schema": result["editable_schema"]},
      ip_address=client_ip(request))
```

同样替换以下 action 的审计调用：

- `tool_schema_override`
- `tool_schema_override_delete`
- `tool_default_update`
- `tool_override`
- `tool_override_delete`

- [x] **步骤 4：保持 lazy import 不外提**

确认以下 import 仍在函数体内：

```python
from server import app
from core.tool_registry import TOOL_METADATA
from core.tool_registry import get_tool_def
from core.runtime_tool_service import normalize_tool_platform
from core.runtime_tool_service import normalize_runtime_preset
from core.runtime_tool_service import resolve_effective_tools
from core.tool_schema_preview import build_tool_schema_config
from core.tool_schema_preview import build_effective_tool_schemas
from core.settings_service import settings
```

这些 import 不放到模块顶层，避免导入 `api.admin.tool_routes` 时触发 server、bridge、KT registry 或 settings 副作用。

## 任务 3：集成父 router 并保留旧导入路径

**文件：**
- 修改：`api/admin_routes.py`

- [x] **步骤 1：导入新模块 router 和旧兼容符号**

在现有 `api.admin.*_routes` import 区加入：

```python
from api.admin.tool_routes import (
    ToolOverrideBody,
    ToolSchemaOverrideBody,
    ToolUpdateBody,
    _TEMP_TOOL_TARGET_EXACT,
    _TEMP_TOOL_TARGET_PREFIXES,
    _is_temp_tool_target_id,
    _tool_target_label,
    delete_tool_override,
    delete_tool_schema_override_api,
    get_effective_tools,
    get_tool_schema_override,
    list_runtime_preset_decisions,
    list_tool_targets,
    list_tools,
    router as tool_router,
    save_tool_schema_override_api,
    set_tool_override,
    update_tool_defaults,
)
```

- [x] **步骤 2：include `tool_router`**

在 router include 区加入：

```python
router.include_router(tool_router)
```

推荐放在 `router.include_router(group_memory_router)` 后、`router.include_router(trace_router)` 前，使管理端业务路由和观测路由继续分组清晰。

- [x] **步骤 3：删除父模块工具管理实现**

删除 `api/admin_routes.py` 中从以下注释开始的工具管理区块：

```python
# ── 工具管理 ──
```

一直删到以下注释之前：

```python
# ── Model Health Check ──
```

保留 `# ── Model Health Check ──` 和之后的模型健康检查实现。

- [x] **步骤 4：确认父模块仍保留其他子域依赖**

删除工具区块后，确认 `api/admin_routes.py` 内仍保留这些本地 helper，因为其他端点还在使用：

```python
def _iso(v) -> str:
    ...


def _raw_group_id(group_id: str) -> str:
    ...


def _runtime_snapshot() -> dict:
    ...
```

同时不要删除这些仍被父模块使用的 import：

```python
import asyncio
import json
from datetime import datetime, timedelta
from core.database import ChatLog, ConversationTurn, SystemSetting, User
```

## 任务 4：让 split 测试转绿

**文件：**
- 测试：`tests/test_admin_tool_routes_split.py`
- 生产：`api/admin_routes.py`
- 生产：`api/admin/tool_routes.py`

- [x] **步骤 1：运行 split 测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_tool_routes_split.py -q
```

预期：`6 passed`。

- [x] **步骤 2：如果路由顺序失败，移动 decorator 顺序**

若 `test_admin_tool_static_routes_before_dynamic_tool_name_routes` 失败，只调整 `api/admin/tool_routes.py` 中 endpoint 定义顺序，使 `list_tool_targets()`、`get_effective_tools()`、`list_runtime_preset_decisions()` 位于 `get_tool_schema_override()` 之前。不要改 path、method 或函数名。

## 任务 5：定向回归和静态验证

**文件：**
- 生产：`api/admin_routes.py`
- 生产：`api/admin/tool_routes.py`
- 测试：`tests/test_admin_tool_routes_split.py`

- [x] **步骤 1：运行工具行为回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_api.py::TestToolAdmin \
  tests/test_tool_plan.py \
  tests/test_tool_schema_config.py \
  tests/test_final_tools.py \
  -q
```

预期：全部通过，失败数为 0。

- [x] **步骤 2：运行鉴权与 asyncio 策略回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_api.py::TestAuth \
  tests/test_asyncio_run_policy.py \
  -q
```

预期：全部通过，失败数为 0。

- [x] **步骤 3：运行编译和 diff 检查**

运行：

```bash
python -m compileall api/admin_routes.py api/admin/tool_routes.py -q
git diff --check -- api/admin_routes.py api/admin/tool_routes.py tests/test_admin_tool_routes_split.py
```

预期：两条命令退出码为 0，`git diff --check` 无输出。

- [x] **步骤 4：运行反向导入和 awaitable 扫描**

运行：

```bash
rg -n "from api\.admin_routes|import api\.admin_routes|asyncio\.run|run_awaitable_sync" api/admin/tool_routes.py
```

预期：退出码为 1，无匹配输出。

- [x] **步骤 5：记录行数**

运行：

```bash
wc -l api/admin_routes.py api/admin/tool_routes.py tests/test_admin_tool_routes_split.py
```

预期：`api/admin_routes.py` 行数从 4303 降到约 3760-3770 行；新模块低于 800 行。

## 任务 6：全量验证并提交实现阶段

**文件：**
- 生产：`api/admin_routes.py`
- 生产：`api/admin/tool_routes.py`
- 测试：`tests/test_admin_tool_routes_split.py`

- [x] **步骤 1：运行全量测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

预期：全部通过，失败数为 0。

- [x] **步骤 2：暂存实现文件**

运行：

```bash
git add api/admin_routes.py api/admin/tool_routes.py tests/test_admin_tool_routes_split.py
```

- [x] **步骤 3：提交实现**

运行：

```bash
git commit -m "refactor(管理端): 拆分工具路由"
```

## 任务 7：文档收口

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/admin-tool-routes-split.md`

- [x] **步骤 1：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」下记录：

```markdown
- [x] `api/admin_routes.py` 已继续拆出 Admin Tools 路由到 `api/admin/tool_routes.py`，保留父模块聚合和旧导入兼容。
```

并更新 `api/admin_routes.py` 当前行数。

- [x] **步骤 2：更新 `docs/plan_walkthrough.md`**

追加 2026-06-21 Admin Tools 阶段记录，包含：

```markdown
### 2026-06-21 Admin Tools 路由拆分

- 设计提交：`1b5d58b docs(管理端): 设计工具路由拆分`
- 计划提交：`docs(计划): 记录工具路由拆分计划`
- 实现提交：`refactor(管理端): 拆分工具路由`
- 验证：记录 split 测试、定向回归、静态扫描、行数和全量 pytest 结果。
```

- [x] **步骤 3：更新本计划执行结果**

在「当前状态」中勾选已完成项，并记录实际测试输出、行数和提交 hash。

- [x] **步骤 4：验证文档**

运行：

```bash
git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/admin-tool-routes-split.md
```

预期：无输出。

- [x] **步骤 5：运行全量测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

预期：全部通过，失败数为 0。

- [x] **步骤 6：提交文档收口**

运行：

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/admin-tool-routes-split.md
git commit -m "docs(计划): 收口工具路由拆分"
```

## 验收标准

- `api/admin_routes.py` 行数继续下降，`api/admin/tool_routes.py` 低于 800 行。
- 所有 `/api/v1/admin/tools*` 路径、method、状态码和 response shape 保持不变。
- `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 对拆分后的工具路由仍生效。
- `api.admin_routes` re-export 的迁移符号与 `api.admin.tool_routes` 中对象相同。
- 工具默认值、runtime preset、生效预览、platform override、schema override、决策查询和 audit action/detail 不变。
- 新模块没有 `api.admin_routes` 反向导入。
- 新模块没有新增 `asyncio.run()` 或 `run_awaitable_sync`。
- Split 测试经历红灯到绿灯。
- 定向回归、`tests/test_asyncio_run_policy.py` 和全量测试通过。

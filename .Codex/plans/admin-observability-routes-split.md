# Admin Observability 路由拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `api/admin_routes.py` 中 trace、LLM API 日志、工具调用日志、审计日志和日志查看器路由拆到 `api/admin/trace_routes.py` 与 `api/admin/log_routes.py`，保持 HTTP 路径、响应契约、鉴权 monkeypatch、旧导入路径和日志读取语义不变。

**架构：** `api.admin_routes` 继续作为 `/api/v1/admin` 顶层 router，并 include 新的 `api.admin.trace_routes.router` 与 `api.admin.log_routes.router`。`trace_routes.py` 承接 AgentRun / ToolCall / LLMApiRequestLog 查询接口；`log_routes.py` 承接 AdminAuditLog、日志文件列表、日志读取和前端错误上报接口；`api.admin_routes` 通过 re-export 保留旧符号。

**技术栈：** Python 3.12、FastAPI、Pydantic、SQLAlchemy、pytest、项目既有 `api.admin.common` 鉴权 helper。

---

## 当前状态（2026-06-21）

- [x] 已完成 `docs/todo.md`、`docs/plan_walkthrough.md`、最近提交和路由分布核对。
- [x] 已完成只读子 agent 分析：
  - Observability / log 路由：推荐拆 `trace_routes.py` 与 `log_routes.py`。
  - Tools 路由：边界清晰，但有 runtime tool / bridge 状态副作用。
  - Models 路由：行数收益最大，但配置、Prompt Runtime 间接路径和本地模型加载风险更高。
- [x] 已选择本阶段拆分目标：Admin Observability 路由。
- [x] 已写设计文档：`docs/superpowers/specs/2026-06-21-admin-observability-routes-split-design.md`。
- [x] 设计提交：`a305e28 docs(管理端): 设计观测路由拆分`。
- [x] 设计阶段全量验证：`python -m pytest tests/ -v` -> `1517 passed, 6 skipped, 139 warnings in 108.33s`。
- [ ] 计划提交。
- [ ] TDD 红灯测试提交前验证。
- [ ] 实现拆分并提交。
- [ ] 更新 `docs/todo.md`、`docs/plan_walkthrough.md` 和本计划执行结果并提交。

## 子 agent 分工约定

- **Agent A：测试契约。** 只修改 `tests/test_admin_observability_routes_split.py`，不改生产代码。输出红灯测试结果和失败原因。
- **Agent B：Trace 模块草稿。** 只创建 `api/admin/trace_routes.py`，不改 `api/admin_routes.py`。按本计划迁移 trace / tool / LLM API log 查询路由。
- **Agent C：Log 模块草稿。** 只创建 `api/admin/log_routes.py`，不改 `api/admin_routes.py`。按本计划迁移 audit log、日志查看器和 frontend error 路由。
- **Agent D：主模块集成。** 只修改 `api/admin_routes.py`。负责 include 新 router、re-export 旧符号、删除旧 route decorator 实现，并保留其他 admin 子域仍使用的 import/helper。
- **Agent E：验证审查。** 只读检查 `git diff`、路由注册、行数、`asyncio.run` 策略和测试输出。不得修改代码。

接口约定：

- 两个新模块都导出 `router`，且不要带 `/api/v1/admin` 前缀。
- 新模块使用 `api.admin.common.verify_admin`，不得从 `api.admin_routes` 导入 `verify_admin`、`router` 或 `_audit_request`。
- `api.admin_routes` 必须 re-export 迁移 request model、helper 和 endpoint 函数。
- 生产代码不得新增 `asyncio.run()`，不得新增同步函数包装 awaitable。
- 不改变 Prompt Runtime 模板、`enriched_query`、工具 usage 文档或 prompt runtime 输入。

## 文件职责

- 创建：`tests/test_admin_observability_routes_split.py`
  - 锁定 trace 路由 endpoint module 为 `api.admin.trace_routes`。
  - 锁定 audit / log 路由 endpoint module 为 `api.admin.log_routes`。
  - 锁定 `api.admin_routes` 对迁移符号的旧导入兼容。
  - 锁定旧 token monkeypatch 对新模块路由仍生效。
  - 锁定迁移路由未重复注册。
  - 锁定 `GET /logs`、`POST /logs/frontend-error` 和 `GET /logs/{name}` 的注册顺序。
- 创建：`api/admin/trace_routes.py`
  - 定义 `router = APIRouter(tags=["admin-trace"])`。
  - 持有 `list_agent_runs()`、`get_agent_run()`、`list_tool_calls()`、
    `get_tool_call()`、`list_llm_api_logs()`、`get_llm_api_log()`。
  - 从 `api.admin.common` 导入 `verify_admin`。
  - 从 `core.database` 导入 `get_db`、`AgentRun`、`ToolCall`、`PromptRenderLog`、
    `LLMApiRequestLog`、`ReplyContractCheckLog`。
- 创建：`api/admin/log_routes.py`
  - 定义 `router = APIRouter(tags=["admin-logs"])`。
  - 持有 `FrontendErrorBody`、`_is_allowed_log_name()`、`_log_level_of()`、
    `_group_log_level_events()`、`list_audit_logs()`、`list_log_files()`、
    `read_log()`、`log_frontend_error()`。
  - 从 `api.admin.common` 导入 `verify_admin`。
  - 从 `core.database` 导入 `get_db`、`AdminAuditLog`。
  - 内部定义日志目录解析 helper，兼容 `api.admin_routes.__file__` monkeypatch。
- 修改：`api/admin_routes.py`
  - 导入并 include `trace_router` 与 `log_router`。
  - re-export 迁移符号。
  - 删除本地 trace / audit / log route decorator 实现。
  - 保留其他子域仍使用的 `AdminAuditLog`、`row_to_dict`、`_safe_json()`、
    `_audit_request()`、`_client_ip()` 等符号。
- 修改：`docs/todo.md`
  - 记录 Admin Observability 拆分进展和 `api/admin_routes.py` 新行数。
- 修改：`docs/plan_walkthrough.md`
  - 追加本阶段设计、计划、实现、验证和提交记录。
- 修改：`.Codex/plans/admin-observability-routes-split.md`
  - 实现完成后勾选已执行步骤并记录红灯、绿灯、定向回归和全量验证结果。

## 任务 1：补 Observability 路由拆分红灯测试

**文件：**
- 创建：`tests/test_admin_observability_routes_split.py`

- [ ] **步骤 1：创建 split 路由测试文件**

创建 `tests/test_admin_observability_routes_split.py`：

```python
from __future__ import annotations


_TRACE_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/admin/agent-runs"),
    ("GET", "/api/v1/admin/agent-runs/{run_id}"),
    ("GET", "/api/v1/admin/tool-calls"),
    ("GET", "/api/v1/admin/tool-calls/{tool_call_id}"),
    ("GET", "/api/v1/admin/llm-api-logs"),
    ("GET", "/api/v1/admin/llm-api-logs/{log_id}"),
)


_LOG_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/admin/audit-logs"),
    ("GET", "/api/v1/admin/logs"),
    ("POST", "/api/v1/admin/logs/frontend-error"),
    ("GET", "/api/v1/admin/logs/{name}"),
)


_TRACE_ROUTE_EXPORTS = (
    "list_agent_runs",
    "get_agent_run",
    "list_tool_calls",
    "get_tool_call",
    "list_llm_api_logs",
    "get_llm_api_log",
)


_LOG_ROUTE_EXPORTS = (
    "FrontendErrorBody",
    "_is_allowed_log_name",
    "_log_level_of",
    "_group_log_level_events",
    "list_audit_logs",
    "list_log_files",
    "read_log",
    "log_frontend_error",
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


def test_admin_trace_routes_are_registered_from_split_module():
    for method, path in _TRACE_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.trace_routes"}


def test_admin_log_routes_are_registered_from_split_module():
    for method, path in _LOG_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.log_routes"}


def test_legacy_admin_routes_observability_imports_still_work():
    from api import admin_routes
    from api.admin import log_routes, trace_routes

    for name in _TRACE_ROUTE_EXPORTS:
        assert getattr(admin_routes, name) is getattr(trace_routes, name)

    for name in _LOG_ROUTE_EXPORTS:
        assert getattr(admin_routes, name) is getattr(log_routes, name)

    assert admin_routes.FrontendErrorBody(message="x").message == "x"
    assert admin_routes._is_allowed_log_name("nanobot.log")


def test_split_observability_routes_use_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/agent-runs",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/agent-runs",
        headers={"Authorization": "Bearer test-token"},
    )
    log_ok = client.post(
        "/api/v1/admin/logs/frontend-error",
        json={"message": "split auth smoke"},
        headers={"Authorization": "Bearer split-token"},
    )
    log_wrong = client.post(
        "/api/v1/admin/logs/frontend-error",
        json={"message": "split auth smoke"},
        headers={"Authorization": "Bearer test-token"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401
    assert log_ok.status_code == 200
    assert log_wrong.status_code == 401


def test_admin_observability_routes_are_not_registered_twice():
    for method, path in _TRACE_ROUTE_SIGNATURES + _LOG_ROUTE_SIGNATURES:
        assert len(_admin_routes_for(path, method)) == 1, f"{method} {path}"


def test_admin_log_routes_keep_static_paths_before_dynamic_log_name():
    route_paths = [path for path, _route in _admin_route_entries()]

    logs_index = route_paths.index("/api/v1/admin/logs")
    frontend_error_index = route_paths.index("/api/v1/admin/logs/frontend-error")
    read_log_index = route_paths.index("/api/v1/admin/logs/{name}")

    assert logs_index < read_log_index
    assert frontend_error_index < read_log_index
```

- [ ] **步骤 2：运行红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_observability_routes_split.py -q
```

预期：失败。失败点应为 endpoint module 仍是 `api.admin_routes`，且
`api.admin.trace_routes` / `api.admin.log_routes` 尚不存在。

## 任务 2：拆出 Trace 路由模块

**文件：**
- 创建：`api/admin/trace_routes.py`
- 修改：`api/admin_routes.py`

- [ ] **步骤 1：创建 `api/admin/trace_routes.py`**

创建模块头部和依赖：

```python
"""Admin Trace / LLM API 日志路由。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.admin.common import verify_admin
from core.database import (
    AgentRun,
    LLMApiRequestLog,
    PromptRenderLog,
    ReplyContractCheckLog,
    ToolCall,
    get_db,
)
from core.tracing import row_to_dict

router = APIRouter(tags=["admin-trace"])
```

从 `api/admin_routes.py` 精确迁移以下函数到新模块，函数体保持原语义：

- `list_agent_runs`
- `get_agent_run`
- `list_tool_calls`
- `get_tool_call`
- `list_llm_api_logs`
- `get_llm_api_log`

迁移时只做这些必要调整：

- route decorator 从新模块本地 `router` 绑定。
- `LLMApiRequestLog` 和 `ReplyContractCheckLog` 使用模块顶层 import，不再在函数内局部导入。
- `verify_admin` 使用 `api.admin.common.verify_admin`。
- 不改变 query 参数、排序、分页、统计字段和 404 文案。

- [ ] **步骤 2：集成 trace router**

在 `api/admin_routes.py` 的已拆 router 导入区新增：

```python
from api.admin.trace_routes import (
    get_agent_run,
    get_llm_api_log,
    get_tool_call,
    list_agent_runs,
    list_llm_api_logs,
    list_tool_calls,
    router as trace_router,
)
```

在 include 区新增：

```python
router.include_router(trace_router)
```

删除 `api/admin_routes.py` 中旧 trace route decorator 实现：

- `@router.get("/agent-runs")` 到 `get_agent_run()` 结束。
- `@router.get("/tool-calls")` 到 `get_tool_call()` 结束。
- `@router.get("/llm-api-logs")` 到 `get_llm_api_log()` 结束。

保留 `core.tracing.row_to_dict` 顶层 import，因为 reply / eval 区域仍使用它。

- [ ] **步骤 3：运行 trace 定向测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_observability_routes_split.py::test_admin_trace_routes_are_registered_from_split_module \
  tests/test_prompt_trace_admin.py \
  -q
```

预期：trace module 断言和 trace 行为回归通过；log module 相关 split 测试仍未作为本命令目标。

## 任务 3：拆出 Audit / Log 路由模块

**文件：**
- 创建：`api/admin/log_routes.py`
- 修改：`api/admin_routes.py`
- 修改：`tests/test_admin_logs_viewer.py`（仅当旧 `__file__` monkeypatch 兼容 helper 仍无法满足测试时修改）

- [ ] **步骤 1：创建 `api/admin/log_routes.py`**

创建模块头部和依赖：

```python
"""Admin 审计日志与日志查看器路由。"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import sys
from collections import deque
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.admin.common import verify_admin
from core.database import AdminAuditLog, get_db

logger = logging.getLogger("nanobot.admin")
router = APIRouter(tags=["admin-logs"])
```

新增本模块私有 helper：

```python
def _safe_json(raw):
    try:
        return json.loads(raw or "[]")
    except Exception:
        return []


def _project_root() -> str:
    admin_routes = sys.modules.get("api.admin_routes")
    admin_routes_file = getattr(admin_routes, "__file__", "") if admin_routes else ""
    if admin_routes_file:
        return os.path.dirname(os.path.dirname(os.path.abspath(str(admin_routes_file))))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _data_dir() -> str:
    return os.path.join(_project_root(), "data")
```

从 `api/admin_routes.py` 精确迁移以下符号到新模块：

- `list_audit_logs`
- `_is_allowed_log_name`
- `_LOG_START_RE`
- `_log_level_of`
- `_group_log_level_events`
- `read_log`
- `FrontendErrorBody`
- `log_frontend_error`

迁移时只做这些必要调整：

- `list_log_files()` 的项目根目录使用 `_data_dir()`：

```python
@router.get("/logs")
def list_log_files(_auth=Depends(verify_admin)):
    log_dir = _data_dir()
    files = []
    patterns = ["*.log", "*.log.*", "nanobot.log*"]
    for pat in patterns:
        for p in glob.glob(os.path.join(log_dir, pat)):
            fname = os.path.basename(p)
            if fname not in [f["name"] for f in files]:
                size = os.path.getsize(p)
                files.append({"name": fname, "size": size, "mtime": os.path.getmtime(p)})
    files.sort(key=lambda x: -x["mtime"])
    return {"files": files}
```

- `read_log()` 的 `log_dir` 使用 `_data_dir()`：

```python
log_dir = os.path.abspath(_data_dir())
```

- `POST /logs/frontend-error` 放在 `GET /logs/{name}` 之前定义。
- 不改变 `read_log()` 的返回字段、过滤规则、非法 `lines` 400 和 404 文案。

- [ ] **步骤 2：集成 log router**

在 `api/admin_routes.py` 的已拆 router 导入区新增：

```python
from api.admin.log_routes import (
    FrontendErrorBody,
    _group_log_level_events,
    _is_allowed_log_name,
    _log_level_of,
    list_audit_logs,
    list_log_files,
    log_frontend_error,
    read_log,
    router as log_router,
)
```

在 include 区新增：

```python
router.include_router(log_router)
```

删除 `api/admin_routes.py` 中旧 audit / log route decorator 实现：

- `@router.get("/audit-logs")` 的 `list_audit_logs()`。
- `@router.get("/logs")` 的 `list_log_files()`。
- `_is_allowed_log_name()`、`_LOG_START_RE`、`_log_level_of()`、
  `_group_log_level_events()`。
- `@router.get("/logs/{name}")` 的 `read_log()`。
- `FrontendErrorBody`。
- `@router.post("/logs/frontend-error")` 的 `log_frontend_error()`。

保留 `AdminAuditLog` 顶层 import，因为 timing proposal / eval 路由仍使用它。

- [ ] **步骤 3：运行 log 定向测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_observability_routes_split.py::test_admin_log_routes_are_registered_from_split_module \
  tests/test_admin_observability_routes_split.py::test_admin_log_routes_keep_static_paths_before_dynamic_log_name \
  tests/test_admin_logs_viewer.py \
  tests/test_admin_api.py::TestToolAdmin::test_tools_have_separate_superuser_private_default_template \
  -q
```

预期：log module 断言、日志查看器行为和 audit log 烟测通过。

## 任务 4：完整集成回归

**文件：**
- 修改：`api/admin_routes.py`
- 修改：`api/admin/trace_routes.py`
- 修改：`api/admin/log_routes.py`
- 修改：`tests/test_admin_observability_routes_split.py`

- [ ] **步骤 1：运行 split 全量目标测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_observability_routes_split.py -q
```

预期：所有 split 测试通过。

- [ ] **步骤 2：运行观测行为回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_prompt_trace_admin.py \
  tests/test_admin_logs_viewer.py \
  tests/test_admin_api.py::TestObservabilityAPI \
  -q
```

预期：trace、日志查看器和现有 Observability 行为回归通过。

- [ ] **步骤 3：运行鉴权与 asyncio 策略回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_api.py::TestAuth \
  tests/test_asyncio_run_policy.py \
  -q
```

预期：鉴权 monkeypatch 和 `asyncio.run()` 策略回归通过。

- [ ] **步骤 4：运行静态检查**

运行：

```bash
python -m compileall api/admin_routes.py api/admin/trace_routes.py api/admin/log_routes.py -q
git diff --check -- api/admin_routes.py api/admin/trace_routes.py api/admin/log_routes.py tests/test_admin_observability_routes_split.py
wc -l api/admin_routes.py api/admin/trace_routes.py api/admin/log_routes.py tests/test_admin_observability_routes_split.py
```

预期：`compileall` 和 `git diff --check` 无输出；行数输出记录到本计划和
`docs/plan_walkthrough.md`。

- [ ] **步骤 5：运行反向导入与 awaitable 扫描**

运行：

```bash
rg -n "api\\.admin_routes|asyncio\\.run|run_awaitable_sync" api/admin/trace_routes.py api/admin/log_routes.py
```

预期：无输出。新模块不反向导入 `api.admin_routes`，也不新增
`asyncio.run()` 或同步 awaitable 包装。

- [ ] **步骤 6：运行全量测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

预期：0 failures。

- [ ] **步骤 7：提交实现阶段**

只暂存目标文件：

```bash
git add api/admin_routes.py api/admin/trace_routes.py api/admin/log_routes.py tests/test_admin_observability_routes_split.py
git commit -m "refactor(管理端): 拆分观测路由"
```

## 任务 5：文档收口

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/admin-observability-routes-split.md`

- [ ] **步骤 1：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」下追加进展：

```markdown
  - 进展：`api/admin_routes.py` 第四刀已拆出 Observability 路由到
    `api/admin/trace_routes.py` 与 `api/admin/log_routes.py`；旧
    `api.admin_routes` 继续 re-export 迁移后的 endpoint、request model 和 helper，
    保留 admin token monkeypatch、HTTP 路径、日志读取和 audit log 过滤兼容。
```

同时更新 `admin_routes.py` 行数为实现后的 `wc -l` 结果。

- [ ] **步骤 2：更新 `docs/plan_walkthrough.md`**

追加本阶段记录：

- 设计文档路径和设计提交。
- 计划路径和计划提交。
- 实现提交。
- 红灯、绿灯、定向回归、静态检查和全量验证结果。
- 行数变化。
- 执行约束：不拆普通 API、不迁移模型/工具/eval、不新增 `asyncio.run()`。

- [ ] **步骤 3：更新本计划执行结果**

在「当前状态」中记录：

- 红灯测试结果。
- 绿灯 split 测试结果。
- 定向回归结果。
- 静态检查结果。
- 全量测试结果。
- 实现提交号。

并勾选已完成步骤。

- [ ] **步骤 4：文档提交前验证**

运行：

```bash
git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/admin-observability-routes-split.md
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

预期：`git diff --check` 无输出；全量测试 0 failures。

- [ ] **步骤 5：提交文档收口**

只暂存目标文件：

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/admin-observability-routes-split.md
git commit -m "docs(计划): 收口观测路由拆分"
```

## 验收标准

- `api/admin_routes.py` 行数继续下降，且仍作为 `/api/v1/admin` 聚合 router。
- `api/admin/trace_routes.py` 和 `api/admin/log_routes.py` 不反向导入
  `api.admin_routes`。
- `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 对新模块路由仍生效。
- trace / log / audit HTTP 路径、状态码、分页字段、统计字段和响应 shape 不变。
- `tests/test_admin_observability_routes_split.py` 能证明路由已迁移、旧导入兼容、
  没有重复注册、log 静态路径顺序正确。
- 定向回归、`tests/test_asyncio_run_policy.py` 和全量测试通过。

# 普通 API History / Log 路由拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把普通 `api/routes.py` 中的 history / log HTTP 层迁移到 `api/history_log_routes.py`，保持旧导入、鉴权 monkeypatch、数据库写入语义、`/log` evolution 排队边界和 `/chat` 主链路不变。

**架构：** `api.routes` 继续作为 `/api/v1` 聚合 router，`server.py` 不新增 include 入口。新增 `api.history_log_routes.router` 承载 `/chat/mark-clear`、`/chat/history-summary`、`/chat/compact-history`、`/context`、`/log`、`/log_ambient` 和 `/search_logs`；父模块 include 子 router，并 re-export 迁移后的 request model 和 endpoint。`_persist_chat_turn()`、`_safe_meta()`、`memory`、`init_legacy_memory()`、`/chat`、`/group/message` 和 `/health` 继续留在父模块。

**技术栈：** Python 3.12、FastAPI、Pydantic、SQLAlchemy、pytest、项目既有 `api.common_auth`、`core.database`、`core.sqlite_retry`、`core.evolution`。

---

## 当前状态（2026-06-21）

- [x] 已完成 task、memory、models、evolution route-only 四刀普通 API 拆分，`api/routes.py` 当前 2469 行。
- [x] 已完成下一刀候选审计：`history_log_routes` 收益最大且仍避开 `/chat` 主链路；`media_routes` 风险更低但收益较小；`agent_step_routes` 收益太小。
- [x] 已写设计文档：`docs/superpowers/specs/2026-06-21-api-history-log-routes-split-design.md`。
- [x] 设计提交：`6f93c94 docs(普通API): 设计历史日志路由拆分`。
- [x] 任务 1：补普通 API history / log route split 红灯测试并提交。
- [x] 任务 2：拆出 `api/history_log_routes.py` 并由 `api.routes` include / re-export 后提交。
- [x] 任务 3：更新 `docs/todo.md`、`docs/plan_walkthrough.md` 和本计划执行记录后提交。

## 执行记录（2026-06-21）

- 设计提交：`6f93c94 docs(普通API): 设计历史日志路由拆分`。
- 计划提交：`f321d12 docs(计划): 记录历史日志路由拆分计划`。
- 红灯测试提交：`360b099 test(普通API): 锁定历史日志路由拆分契约`。
- 实现提交：`e6aa5f1 refactor(普通API): 拆分历史日志路由`。
- 文档收口提交：随本阶段 `docs(计划): 收口历史日志路由拆分` 完成。

验证记录：

- 红灯：`python -B -m pytest -q -p no:cacheprovider tests/test_api_history_log_routes_split.py`
  -> `5 failed, 4 passed, 21 warnings in 6.46s`；失败点为 history / log endpoint
  module 仍是 `api.routes`、`api.history_log_routes` 尚不存在，以及
  `api/history_log_routes.py` 文件不存在。
- Split 绿灯：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_history_log_routes_split.py`
  -> `9 passed, 21 warnings in 1.22s`。
- 相邻 split / SQLite retry 回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_history_log_routes_split.py tests/test_api_evolution_routes_split.py tests/test_api_memory_routes_split.py tests/test_api_model_routes_split.py tests/test_api_task_routes_split.py tests/test_tracing_sqlite_retry.py`
  -> `51 passed, 21 warnings in 5.61s`。
- 主 API 行为回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api.py`
  -> `81 passed, 21 warnings in 16.29s`。
- asyncio 策略回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_asyncio_run_policy.py`
  -> `3 passed, 1 warning in 1.70s`。
- 静态检查：`python -B -m py_compile api/routes.py api/history_log_routes.py tests/test_api_history_log_routes_split.py`
  成功；`git diff --check -- api/routes.py api/history_log_routes.py tests/test_api_history_log_routes_split.py`
  无输出；`api/history_log_routes.py` 无 `from api.routes`、`import api.routes`、
  `asyncio.run` 或 `run_awaitable_sync`。
- 行数检查：`api/routes.py` 2134 行，`api/history_log_routes.py` 367 行，
  `tests/test_api_history_log_routes_split.py` 208 行。
- 全量回归：`python -B -m pytest -p no:cacheprovider tests/ -v`
  -> `1610 passed, 6 skipped, 139 warnings in 119.08s`。

## 文件职责

- 创建：`tests/test_api_history_log_routes_split.py`
  - 锁定 7 个 history / log endpoint 的 endpoint module 为 `api.history_log_routes`。
  - 锁定 `api.routes` 旧导入兼容。
  - 锁定 route 未重复注册。
  - 锁定拆分路由继续兼容 `api.routes.NANOBOT_API_TOKEN` monkeypatch。
  - 锁定 `submit_log()` 只写 `ChatLog` 并通过 `BackgroundTasks.add_task()` 排队 evolution。
  - 锁定 `submit_ambient_log()` 的 `group_*` session、`role="ambient"`、`processed=1` 合同。
  - 静态扫描新模块没有反向导入父模块、没有 `asyncio.run`、没有 `run_awaitable_sync`。
  - 锁定 `_persist_chat_turn()`、`_safe_meta()`、`init_legacy_memory()` 和 `/health` 仍留在父模块。
- 创建：`api/history_log_routes.py`
  - 定义 `LogRequest`。
  - 定义 `AmbientLogRequest`。
  - 定义 `mark_clear()`、`get_history_summary()`、`compact_history()`、`get_context()`、`submit_log()`、`submit_ambient_log()`、`search_history_logs()`。
  - 定义 `router = APIRouter(tags=["history-log"])`。
- 修改：`api/routes.py`
  - 从 `api.history_log_routes` import `router as history_log_router`、`LogRequest`、`AmbientLogRequest` 和迁移 endpoint。
  - 删除本地 `LogRequest`、`AmbientLogRequest` 和迁移 endpoint 实现。
  - 在尾部普通子路由区域 include `history_log_router`。
  - 保留父模块 `_persist_chat_turn()`、`_safe_meta()`、`ChatProxyRequest`、`memory`、`init_legacy_memory()`、`evolution_task`、`EVOLUTION_THRESHOLD`、`/chat`、`/group/message` 和 `/health`。
- 收口阶段修改：`docs/todo.md`、`docs/plan_walkthrough.md`、`.Codex/plans/api-history-log-routes-split.md`。

## 任务 1：补普通 API history / log split 红灯测试

**文件：**
- 创建：`tests/test_api_history_log_routes_split.py`

- [x] **步骤 1：创建测试文件**

创建 `tests/test_api_history_log_routes_split.py`：

```python
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from core.database import ChatLog, ConversationTurn, User, get_db


_HISTORY_LOG_ROUTE_SIGNATURES = (
    ("POST", "/api/v1/chat/mark-clear"),
    ("GET", "/api/v1/chat/history-summary"),
    ("POST", "/api/v1/chat/compact-history"),
    ("GET", "/api/v1/context"),
    ("POST", "/api/v1/log"),
    ("POST", "/api/v1/log_ambient"),
    ("GET", "/api/v1/search_logs"),
)

_HISTORY_LOG_ROUTE_EXPORTS = (
    "LogRequest",
    "AmbientLogRequest",
    "mark_clear",
    "get_history_summary",
    "compact_history",
    "get_context",
    "submit_log",
    "submit_ambient_log",
    "search_history_logs",
)


def _api_route_entries():
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


def _api_routes_for(path: str, method: str | None = None):
    return [
        route
        for route_path, route in _api_route_entries()
        if route_path == path and (method is None or method in getattr(route, "methods", set()))
    ]


@pytest.fixture()
def client_with_db(db_session):
    from server import app

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)


def test_api_history_log_routes_are_registered_from_split_module():
    for method, path in _HISTORY_LOG_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.history_log_routes"}


def test_legacy_api_routes_history_log_imports_still_work():
    from api import history_log_routes
    from api import routes

    for name in _HISTORY_LOG_ROUTE_EXPORTS:
        assert getattr(routes, name) is getattr(history_log_routes, name)

    log_body = routes.LogRequest(user_id="u1", role="user", content="hello")
    ambient_body = routes.AmbientLogRequest(group_id="42", sender_name="alice", content="hi")
    assert log_body.user_id == "u1"
    assert ambient_body.group_id == "42"


def test_split_history_log_routes_use_legacy_api_token_monkeypatch(client_with_db, monkeypatch):
    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")

    ok = client_with_db.get(
        "/api/v1/chat/history-summary?user_id=u1",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client_with_db.get(
        "/api/v1/chat/history-summary?user_id=u1",
        headers={"Authorization": "Bearer wrong"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_api_history_log_routes_are_not_registered_twice():
    for method, path in _HISTORY_LOG_ROUTE_SIGNATURES:
        assert len(_api_routes_for(path, method)) == 1, f"{method} {path}"


def test_api_history_log_routes_do_not_import_parent_routes_or_sync_awaitable():
    source = Path("api/history_log_routes.py").read_text(encoding="utf-8")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_chat_persistence_helpers_stay_in_parent_routes():
    from api import routes

    assert routes._persist_chat_turn.__module__ == "api.routes"
    assert routes._safe_meta.__module__ == "api.routes"
    assert routes.init_legacy_memory.__module__ == "api.routes"


def test_submit_log_keeps_background_evolution_boundary(db_session, monkeypatch):
    from api import history_log_routes

    calls = []

    def fake_evolution_task(user_id: str):
        calls.append(user_id)

    monkeypatch.setattr(history_log_routes, "EVOLUTION_THRESHOLD", 1)
    monkeypatch.setattr(history_log_routes, "evolution_task", fake_evolution_task)

    background_tasks = BackgroundTasks()
    response = history_log_routes.submit_log(
        history_log_routes.LogRequest(user_id="u-log", role="user", content="日志"),
        background_tasks,
        db_session,
        _auth=True,
    )

    assert response == {"status": "ok", "unprocessed_logs": 1}
    assert db_session.query(User).filter_by(id="u-log").count() == 1
    rows = db_session.query(ChatLog).filter_by(user_id="u-log").all()
    assert len(rows) == 1
    assert rows[0].content == "日志"
    assert rows[0].processed == 0
    assert db_session.query(ConversationTurn).filter_by(user_id="u-log").count() == 0
    assert len(background_tasks.tasks) == 1
    task = background_tasks.tasks[0]
    assert task.func is history_log_routes.evolution_task
    assert task.args == ("u-log",)
    assert calls == []


def test_ambient_log_keeps_group_session_and_processed_contract(db_session):
    from api import history_log_routes

    response = history_log_routes.submit_ambient_log(
        history_log_routes.AmbientLogRequest(
            group_id="123",
            session_name="测试群",
            sender_name="alice",
            content="环境消息",
            message_id="m1",
        ),
        db_session,
        _auth=True,
    )

    assert response == {"status": "ok", "message": "ambient log saved [deprecated]"}
    user = db_session.query(User).filter_by(id="group_123").one()
    assert user.name == "测试群"
    row = db_session.query(ChatLog).filter_by(user_id="group_123").one()
    assert row.session_id == "group_123"
    assert row.sender_name == "alice"
    assert row.session_name == "测试群"
    assert row.role == "ambient"
    assert row.content == "[alice]: 环境消息"
    assert row.processed == 1
    assert row.message_id == "m1"


def test_health_check_stays_in_parent_routes():
    routes = _api_routes_for("/api/v1/health", "GET")

    assert routes
    assert {route.endpoint.__module__ for route in routes} == {"api.routes"}
```

- [x] **步骤 2：运行测试验证红灯**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_history_log_routes_split.py
```

预期：FAIL。失败点应指向 `api.history_log_routes` 尚不存在、history / log endpoint
module 仍为 `api.routes`、`api/history_log_routes.py` 文件尚不存在。`/health` 留在父模块的断言可以继续通过。

- [x] **步骤 3：提交红灯测试**

运行：

```bash
git add tests/test_api_history_log_routes_split.py
git commit -m "test(普通API): 锁定历史日志路由拆分契约"
```

## 任务 2：拆出 `api.history_log_routes`

**文件：**
- 创建：`api/history_log_routes.py`
- 修改：`api/routes.py`

- [x] **步骤 1：创建 `api/history_log_routes.py`**

创建 `api/history_log_routes.py`，从父模块迁移 history / log 端点。模块骨架如下，函数体直接使用 `api/routes.py` 当前实现：

```python
"""普通 API 历史、上下文与日志路由。"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from api.common_auth import verify_token
from config import EVOLUTION_THRESHOLD
from core.compaction import run_autocompact_circuit_breaker
from core.database import (
    ChatLog,
    ConversationTurn,
    Persona,
    SystemPrompt,
    User,
    get_db,
)
from core.evolution import evolution_task
from core.group_runtime.ids import normalize_group_session_id
from core.sqlite_retry import run_sqlite_locked_retry


logger = logging.getLogger("nanobot.routes.history_log")
router = APIRouter(tags=["history-log"])
```

迁移时保持：

- `mark_clear()` 的 rolling summary 懒加载和异常 fallback。
- `submit_log()` 使用 `run_sqlite_locked_retry()`，达到阈值后只向 `BackgroundTasks` 排队。
- `submit_ambient_log()` 使用 `normalize_group_session_id()`。
- `search_history_logs()` 的 LIKE 转义和同 session 上下文展开。

- [x] **步骤 2：修改 `api/routes.py` import 与 re-export**

在 `api/routes.py` 中新增：

```python
from api.history_log_routes import (
    AmbientLogRequest,
    LogRequest,
    compact_history,
    get_context,
    get_history_summary,
    mark_clear,
    router as history_log_router,
    search_history_logs,
    submit_ambient_log,
    submit_log,
)
```

保留下列父模块符号，不要删除：

```python
from config import EVOLUTION_THRESHOLD
from core.evolution import evolution_task
from core.legacy_adapter import SQLiteMemory
memory = None
def init_legacy_memory():
    ...
def _safe_meta(...):
    ...
def _persist_chat_turn(...):
    ...
```

- [x] **步骤 3：删除父模块本地 history / log 定义并 include 子 router**

删除 `api/routes.py` 中本地：

- `LogRequest`
- `AmbientLogRequest`
- `mark_clear()`
- `get_history_summary()`
- `compact_history()`
- `get_context()`
- `submit_log()`
- `submit_ambient_log()`
- `search_history_logs()`

在尾部 include 区加入：

```python
router.include_router(history_log_router)
```

目标 include 顺序：

```python
router.include_router(history_log_router)
router.include_router(evolution_router)
router.include_router(memory_router)
router.include_router(model_router)
router.include_router(task_router)
```

- [x] **步骤 4：运行 split 定向测试验证绿灯**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_history_log_routes_split.py
```

预期：PASS。

- [x] **步骤 5：运行行为回归与相邻回归**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api.py::test_get_context_default tests/test_api.py::test_get_context_with_auth tests/test_api.py::test_search_logs_rejects_limit_above_max tests/test_api.py::test_search_logs_rejects_invalid_context_size tests/test_api.py::test_search_logs_keyword_escapes_like_wildcards tests/test_api.py::test_search_logs_user_id_fuzzy_escapes_like_wildcards tests/test_api.py::test_submit_log tests/test_api.py::test_deprecated_log_ambient_still_works tests/test_api.py::test_chat_management_endpoints_do_not_echo_internal_errors tests/test_tracing_sqlite_retry.py::test_submit_log_retries_sqlite_locked_commit
python -B -m pytest -q -p no:cacheprovider tests/test_api_task_routes_split.py tests/test_api_memory_routes_split.py tests/test_api_model_routes_split.py tests/test_api_evolution_routes_split.py tests/test_asyncio_run_policy.py tests/test_audit_fixes.py::TestLazyControllerInit::test_legacy_memory_init_exists
```

预期：PASS。

- [x] **步骤 6：运行静态检查**

运行：

```bash
python -B -m compileall api/routes.py api/history_log_routes.py
rg -n "from api\\.routes|import api\\.routes|asyncio\\.run|run_awaitable_sync" api/history_log_routes.py
git diff --check -- api/routes.py api/history_log_routes.py tests/test_api_history_log_routes_split.py
wc -l api/routes.py api/history_log_routes.py tests/test_api_history_log_routes_split.py
```

预期：

- compileall 成功。
- `rg` 无命中，退出码为 1。
- `git diff --check` 无输出。
- `api/routes.py` 行数低于 2469。

- [x] **步骤 7：运行全量测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：0 failures。

- [x] **步骤 8：提交 history / log 路由拆分**

运行：

```bash
git add api/routes.py api/history_log_routes.py
git commit -m "refactor(普通API): 拆分历史日志路由"
```

## 任务 3：文档收口

**文件：**
- 修改：`.Codex/plans/api-history-log-routes-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [x] **步骤 1：更新计划执行记录**

在本计划的「当前状态」中把任务 1 和任务 2 标记为完成，并新增「执行记录」章节，记录：

- 设计提交。
- 红灯测试提交。
- 实现提交。
- 红灯命令、失败数量和失败原因。
- split 绿灯命令和通过数量。
- 行为回归与相邻回归命令和通过数量。
- 静态检查结果。
- `wc -l api/routes.py api/history_log_routes.py tests/test_api_history_log_routes_split.py` 行数。
- 全量回归结果。

- [x] **步骤 2：更新 `docs/todo.md`**

在「超大文件 >800 行拆分」条目下记录：

- `api/routes.py` 已完成 history / log route-only 拆分。
- 新增 `api/history_log_routes.py`。
- `api/routes.py` 最新行数。
- 下一候选为 media 路由或 agent-step / render；继续避开 `/chat` 与 `/group/message` 主链路。

- [x] **步骤 3：更新 `docs/plan_walkthrough.md`**

追加 2026-06-21 的 history / log route-only 拆分执行记录，包含：

- 设计文档提交。
- 计划文档提交。
- 红灯测试提交。
- 实现提交。
- 验证命令和结果。
- 下一步建议。

- [x] **步骤 4：文档格式与状态检查**

运行：

```bash
rg -n "T[O]DO|待[定]|后续实[现]|占[位]|\\x{FFFD}" .Codex/plans/api-history-log-routes-split.md docs/todo.md docs/plan_walkthrough.md
git diff --check -- .Codex/plans/api-history-log-routes-split.md docs/todo.md docs/plan_walkthrough.md
git status --short
```

预期：`rg` 无命中，`git diff --check` 无输出；`git status --short` 中本阶段只包含计划与文档相关改动，以及历史无关脏项。

- [x] **步骤 5：运行最终定向回归**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_history_log_routes_split.py tests/test_api.py::test_search_logs_keyword_escapes_like_wildcards tests/test_tracing_sqlite_retry.py::test_submit_log_retries_sqlite_locked_commit tests/test_asyncio_run_policy.py
```

预期：PASS。

- [x] **步骤 6：提交文档收口**

运行：

```bash
git add .Codex/plans/api-history-log-routes-split.md docs/todo.md docs/plan_walkthrough.md
git commit -m "docs(计划): 收口历史日志路由拆分"
```

## 最终验收清单

- [x] `tests/test_api_history_log_routes_split.py` 经历红灯再绿灯。
- [x] `api.history_log_routes` 不导入 `api.routes`。
- [x] `api.routes` re-export history / log request model 和 endpoint。
- [x] `submit_log()` 保持同步函数，只写 `ChatLog`，不写 `ConversationTurn`。
- [x] `submit_log()` 达到阈值时只向 `BackgroundTasks` 排队 evolution。
- [x] `submit_ambient_log()` 保持 `group_*` session、`ambient` role 和 `processed=1`。
- [x] `api.routes._persist_chat_turn` 继续留在父模块。
- [x] `api.routes._safe_meta` 继续留在父模块。
- [x] `api.routes.init_legacy_memory` 继续留在父模块。
- [x] `/health` 仍留在 `api.routes`。
- [x] `/chat` 与 `/group/message` 主链路未迁移。
- [x] `/search_logs` limit / context_size / LIKE 转义回归通过。
- [x] `tests/test_asyncio_run_policy.py` 通过。
- [x] 全量 `tests/` 回归 0 failures。
- [x] 每个阶段性改动都有独立 commit。

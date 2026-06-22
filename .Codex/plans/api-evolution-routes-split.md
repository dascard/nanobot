# 普通 API Evolution 路由拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把普通 `api/routes.py` 中的手动 `/evolution/trigger` HTTP 层迁移到 `api/evolution_routes.py`，保持旧导入、鉴权 monkeypatch、response shape、同步后台排队边界和 `/chat` / `/log` 自动触发行为不变。

**架构：** `api.routes` 继续作为 `/api/v1` 聚合 router，`server.py` 不新增 include 入口。新增 `api.evolution_routes.router` 承载手动 `/evolution/trigger`；父模块在尾部普通子路由区域 include 子 router，并 re-export `EvolutionTriggerRequest` 和 `trigger_evolution()`。`evolution_task`、`EVOLUTION_THRESHOLD`、`init_legacy_memory()`、`memory` 和 `_persist_chat_turn()` 继续留在父模块。

**技术栈：** Python 3.12、FastAPI、Pydantic、pytest、项目既有 `api.common_auth`、`core.evolution.evolution_task`。

---

## 当前状态（2026-06-21）

- [x] 已完成 task、memory、models 三刀普通 API 拆分，`api/routes.py` 当前 2484 行。
- [x] 已完成 evolution route-only 候选审计，确认只能迁移手动 HTTP 层。
- [x] 已写设计文档：`docs/superpowers/specs/2026-06-21-api-evolution-routes-split-design.md`。
- [x] 设计提交：`a82e342 docs(普通API): 设计进化路由拆分`。
- [x] 设计勘误提交：`99581b7 docs(普通API): 修正进化路由验证引用`。
- [ ] 任务 1：补普通 API evolution route split 红灯测试并提交。
- [ ] 任务 2：拆出 `api/evolution_routes.py` 并由 `api.routes` include / re-export 后提交。
- [ ] 任务 3：更新 `docs/todo.md`、`docs/plan_walkthrough.md` 和本计划执行记录后提交。

## 文件职责

- 创建：`tests/test_api_evolution_routes_split.py`
  - 锁定 `/evolution/trigger` endpoint module 为 `api.evolution_routes`。
  - 锁定 `api.routes` 旧导入兼容。
  - 锁定 route 未重复注册。
  - 锁定拆分路由继续兼容 `api.routes.NANOBOT_API_TOKEN` monkeypatch。
  - 锁定 `trigger_evolution()` 保持同步函数和 `BackgroundTasks.add_task()` 排队边界。
  - 静态扫描新模块没有反向导入父模块、没有 `asyncio.run`、没有 `run_awaitable_sync`。
  - 锁定 `/health` 仍留在父模块。
- 修改：`tests/test_api_memory_routes_split.py`
  - 从父模块尾部路由列表中移除 `/evolution/trigger`。
  - 保留 `/health` 仍在父模块。
- 修改：`tests/test_api_model_routes_split.py`
  - 从父模块尾部路由列表中移除 `/evolution/trigger`。
  - 保留 `/health` 仍在父模块。
- 创建：`api/evolution_routes.py`
  - 定义 `EvolutionTriggerRequest`。
  - 定义 `trigger_evolution()`。
  - 定义 `router = APIRouter(tags=["evolution"])`。
- 修改：`api/routes.py`
  - 从 `api.evolution_routes` import `router as evolution_router`、`EvolutionTriggerRequest`、`trigger_evolution`。
  - 删除本地 `EvolutionTriggerRequest` 与手动 `trigger_evolution()` endpoint 实现。
  - 在尾部普通子路由区域 include `evolution_router`。
  - 保留父模块 `evolution_task`、`EVOLUTION_THRESHOLD`、`SQLiteMemory`、`memory`、`init_legacy_memory()` 和 `_persist_chat_turn()`。
- 收口阶段修改：`docs/todo.md`、`docs/plan_walkthrough.md`、`.Codex/plans/api-evolution-routes-split.md`。

## 任务 1：补普通 API evolution split 红灯测试

**文件：**
- 创建：`tests/test_api_evolution_routes_split.py`
- 修改：`tests/test_api_memory_routes_split.py`
- 修改：`tests/test_api_model_routes_split.py`

- [ ] **步骤 1：创建测试文件**

创建 `tests/test_api_evolution_routes_split.py`：

```python
from __future__ import annotations

import inspect
from pathlib import Path

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient


_EVOLUTION_ROUTE_SIGNATURES = (
    ("POST", "/api/v1/evolution/trigger"),
)

_EVOLUTION_ROUTE_EXPORTS = (
    "EvolutionTriggerRequest",
    "trigger_evolution",
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


def test_api_evolution_routes_are_registered_from_split_module():
    for method, path in _EVOLUTION_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.evolution_routes"}


def test_legacy_api_routes_evolution_imports_still_work():
    from api import evolution_routes
    from api import routes

    for name in _EVOLUTION_ROUTE_EXPORTS:
        assert getattr(routes, name) is getattr(evolution_routes, name)

    body = routes.EvolutionTriggerRequest(user_id="u1")
    assert body.user_id == "u1"


def test_split_evolution_routes_use_legacy_api_token_monkeypatch(monkeypatch):
    from api import evolution_routes
    from server import app

    calls = []

    def fake_evolution_task(user_id: str):
        calls.append(user_id)

    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")
    monkeypatch.setattr(evolution_routes, "evolution_task", fake_evolution_task)
    with TestClient(app) as test_client:
        ok = test_client.post(
            "/api/v1/evolution/trigger",
            json={"user_id": "u1"},
            headers={"Authorization": "Bearer split-token"},
        )
        wrong = test_client.post(
            "/api/v1/evolution/trigger",
            json={"user_id": "u2"},
            headers={"Authorization": "Bearer wrong"},
        )

    assert ok.status_code == 200
    assert ok.json() == {
        "status": "ok",
        "message": "Evolution task queued for u1",
    }
    assert wrong.status_code == 401
    assert calls == ["u1"]


def test_api_evolution_routes_are_not_registered_twice():
    for method, path in _EVOLUTION_ROUTE_SIGNATURES:
        assert len(_api_routes_for(path, method)) == 1, f"{method} {path}"


def test_api_evolution_trigger_keeps_sync_background_boundary():
    from api import evolution_routes
    from api import routes

    assert not inspect.iscoroutinefunction(evolution_routes.trigger_evolution)
    assert not inspect.iscoroutinefunction(routes.trigger_evolution)

    background_tasks = BackgroundTasks()
    body = evolution_routes.EvolutionTriggerRequest(user_id="u1")
    response = evolution_routes.trigger_evolution(body, background_tasks)

    assert response == {
        "status": "ok",
        "message": "Evolution task queued for u1",
    }
    assert len(background_tasks.tasks) == 1
    task = background_tasks.tasks[0]
    assert task.func is evolution_routes.evolution_task
    assert task.args == ("u1",)
    assert task.kwargs == {}


def test_api_evolution_routes_do_not_import_parent_routes_or_sync_awaitable():
    source = Path("api/evolution_routes.py").read_text(encoding="utf-8")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_health_check_stays_in_parent_routes():
    routes = _api_routes_for("/api/v1/health", "GET")

    assert routes
    assert {route.endpoint.__module__ for route in routes} == {"api.routes"}
```

- [ ] **步骤 2：更新 memory split 测试的父模块尾部路由列表**

修改 `tests/test_api_memory_routes_split.py`：

```python
_PARENT_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/health"),
)
```

- [ ] **步骤 3：更新 model split 测试的父模块尾部路由列表**

修改 `tests/test_api_model_routes_split.py`：

```python
_PARENT_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/health"),
)
```

- [ ] **步骤 4：运行测试验证红灯**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_evolution_routes_split.py tests/test_api_memory_routes_split.py tests/test_api_model_routes_split.py
```

预期：FAIL。失败点应指向 `api.evolution_routes` 尚不存在、`/evolution/trigger`
endpoint module 仍为 `api.routes`、`api/evolution_routes.py` 文件尚不存在。
`/health` 仍在父模块的断言可以继续通过。

- [ ] **步骤 5：提交红灯测试**

运行：

```bash
git add tests/test_api_evolution_routes_split.py tests/test_api_memory_routes_split.py tests/test_api_model_routes_split.py
git commit -m "test(普通API): 锁定进化路由拆分契约"
```

## 任务 2：拆出 `api.evolution_routes`

**文件：**
- 创建：`api/evolution_routes.py`
- 修改：`api/routes.py`

- [ ] **步骤 1：创建 `api/evolution_routes.py`**

创建 `api/evolution_routes.py`：

```python
"""普通 API 自进化手动触发路由。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from api.common_auth import verify_token
from core.evolution import evolution_task


logger = logging.getLogger("nanobot.routes.evolution")
router = APIRouter(tags=["evolution"])


class EvolutionTriggerRequest(BaseModel):
    user_id: str


@router.post("/evolution/trigger")
def trigger_evolution(
    req: EvolutionTriggerRequest,
    background_tasks: BackgroundTasks,
    _auth=Depends(verify_token),
):
    """
    手动触发自进化：通过 API 强制开启画像提炼与同步，不再依赖日志计数阈值。
    """
    logger.info("Manual evolution triggered for user [%s]", req.user_id)
    background_tasks.add_task(evolution_task, req.user_id)
    return {"status": "ok", "message": f"Evolution task queued for {req.user_id}"}
```

- [ ] **步骤 2：修改 `api/routes.py` import 与 re-export**

在 `api/routes.py` 中新增：

```python
from api.evolution_routes import (
    EvolutionTriggerRequest,
    router as evolution_router,
    trigger_evolution,
)
```

保留下列父模块 import / 符号，不要删除：

```python
from config import EVOLUTION_THRESHOLD
from core.evolution import evolution_task
from core.legacy_adapter import SQLiteMemory
memory = None
def init_legacy_memory():
    ...
```

- [ ] **步骤 3：删除父模块本地手动 evolution 定义并 include 子 router**

删除 `api/routes.py` 中本地 `EvolutionTriggerRequest` 类。

删除 `api/routes.py` 中本地 `@router.post("/evolution/trigger")` 和
`trigger_evolution()` 函数。

在尾部 include 区加入：

```python
router.include_router(evolution_router)
```

目标 include 顺序：

```python
router.include_router(evolution_router)
router.include_router(memory_router)
router.include_router(model_router)
router.include_router(task_router)
```

- [ ] **步骤 4：运行 split 定向测试验证绿灯**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_evolution_routes_split.py tests/test_api_memory_routes_split.py tests/test_api_model_routes_split.py
```

预期：PASS。

- [ ] **步骤 5：运行相邻回归与静态检查**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_task_routes_split.py tests/test_asyncio_run_policy.py tests/test_audit_fixes.py::TestLazyControllerInit::test_legacy_memory_init_exists
python -B -m compileall api/routes.py api/evolution_routes.py
rg -n "from api\\.routes|import api\\.routes|asyncio\\.run|run_awaitable_sync" api/evolution_routes.py
git diff --check -- api/routes.py api/evolution_routes.py tests/test_api_evolution_routes_split.py tests/test_api_memory_routes_split.py tests/test_api_model_routes_split.py
```

预期：

- pytest PASS。
- compileall 成功。
- `rg` 无命中，退出码为 1。
- `git diff --check` 无输出。

- [ ] **步骤 6：提交 evolution 路由拆分**

运行：

```bash
git add api/routes.py api/evolution_routes.py
git commit -m "refactor(普通API): 拆分进化路由"
```

## 任务 3：文档收口与全量验证

**文件：**
- 修改：`.Codex/plans/api-evolution-routes-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [ ] **步骤 1：运行全量测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：0 failures。

- [ ] **步骤 2：更新计划执行记录**

在本计划的「当前状态」中把任务 1 和任务 2 标记为完成，并新增「执行记录」章节，记录：

- 红灯命令、失败数量和失败原因。
- split 绿灯命令和通过数量。
- 相邻回归命令和通过数量。
- 静态检查结果。
- `wc -l api/routes.py api/evolution_routes.py tests/test_api_evolution_routes_split.py` 行数。
- 全量回归结果。

- [ ] **步骤 3：更新 `docs/todo.md`**

在「超大文件 >800 行拆分」条目下记录：

- `api/routes.py` 已完成 evolution route-only 拆分。
- 新增 `api/evolution_routes.py`。
- `api/routes.py` 最新行数。
- 下一候选为 stickers/media、history/context/log、agent-step/search/render 等更大但低耦合边界。

- [ ] **步骤 4：更新 `docs/plan_walkthrough.md`**

追加 2026-06-21 的 evolution route-only 拆分执行记录，包含：

- 设计文档提交。
- 计划文档提交。
- 红灯测试提交。
- 实现提交。
- 验证命令和结果。
- 下一步建议。

- [ ] **步骤 5：文档格式与状态检查**

运行：

```bash
git diff --check -- .Codex/plans/api-evolution-routes-split.md docs/todo.md docs/plan_walkthrough.md
git status --short
```

预期：`git diff --check` 无输出；`git status --short` 中本阶段只包含计划与文档相关改动，以及历史无关脏项。

- [ ] **步骤 6：提交文档收口**

运行：

```bash
git add .Codex/plans/api-evolution-routes-split.md docs/todo.md docs/plan_walkthrough.md
git commit -m "docs(计划): 收口进化路由拆分"
```

## 最终验收清单

- [ ] `tests/test_api_evolution_routes_split.py` 经历红灯再绿灯。
- [ ] `tests/test_api_memory_routes_split.py` 和 `tests/test_api_model_routes_split.py` 同步更新并通过。
- [ ] `api.evolution_routes` 不导入 `api.routes`。
- [ ] `api.routes` re-export `EvolutionTriggerRequest` 和 `trigger_evolution()`。
- [ ] `trigger_evolution()` 保持同步函数。
- [ ] 手动 `/evolution/trigger` 只向 `BackgroundTasks` 排队，不直接执行 evolution。
- [ ] `api.routes.evolution_task` 继续存在，供 `/log` 和 `/chat` 自动触发使用。
- [ ] `api.routes.init_legacy_memory` 继续存在且可调用。
- [ ] `/health` 仍留在 `api.routes`。
- [ ] `tests/test_asyncio_run_policy.py` 通过。
- [ ] 全量 `tests/` 回归 0 failures。
- [ ] 每个阶段性改动都有独立 commit。

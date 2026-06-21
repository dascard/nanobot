# 普通 API Tasks 路由拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为普通 `api/routes.py` 建立可复用拆分模板：先抽 `verify_token` 共享兼容层，再把 `/tasks*` 定时任务 HTTP 层迁移到 `api/task_routes.py`，保持旧导入、鉴权 monkeypatch、dependency override、route 顺序、响应结构和异步边界不变。

**架构：** `api.routes` 继续作为 `/api/v1` 聚合 router，`server.py` 不新增 include 入口。新增 `api.common_auth` 提供普通 API 鉴权，`api.routes.verify_token` re-export 同一函数对象；新增 `api.task_routes.router` 承载 `/tasks*`，由 `api.routes` 在原路由位置 include。

**技术栈：** Python 3.12、FastAPI、Pydantic、SQLAlchemy、pytest、项目既有 `core.daily_digest` push envelope、`core.database.ScheduledTask`。

---

## 当前状态（2026-06-21）

- [x] 已核对 P3 当前硬项：`api/routes.py` 2822 行，`api/admin_routes.py` 已降至 632 行并移出超大文件队列。
- [x] 已并行分派两个只读子 agent：
  - `api/routes.py` 边界审计：推荐第一刀为 `/tasks*`，备选为 evolution / memory / models。
  - 现有拆分测试模式审计：确认普通 API 先抽 common auth，再拆低耦合子路由。
- [x] 已写设计文档：`docs/superpowers/specs/2026-06-21-api-task-routes-split-design.md`。
- [x] 设计提交：`835ebfd docs(普通API): 设计任务路由拆分`。
- [ ] 任务 1：补普通 API common auth 与 task route split 红灯测试。
- [ ] 任务 2：抽出 `api.common_auth` 并保留 `api.routes.verify_token` 对象身份。
- [ ] 任务 3：拆出 `api/task_routes.py` 并由 `api.routes` include / re-export。
- [ ] 任务 4：文档收口，更新 `docs/todo.md`、`docs/plan_walkthrough.md` 和本计划执行记录。

## 子 agent 分工约定

主线程负责最终编辑、验证和提交。写入阶段不要让多个 agent 同时修改 `api/routes.py`。

- **Explorer A：边界复核。** 只读检查 `api/routes.py` 相关行号、route 顺序、旧导入和 monkeypatch 风险。
- **Explorer B：测试模板复核。** 只读检查 `tests/test_*routes_split*.py`、`tests/conftest.py`、`tests/test_api.py` 中鉴权测试与 route split 模板。
- **Worker A：测试文件。** 只允许创建或修改 `tests/test_api_task_routes_split.py`。
- **Worker B：鉴权实现。** 只允许创建 `api/common_auth.py` 并修改 `api/routes.py` 中 `verify_token` 定义和 import。
- **Worker C：task 路由迁移。** 只允许创建 `api/task_routes.py` 并修改 `api/routes.py` 的 task import、include 和旧本地 task 区块。
- **Reviewer：验证审查。** 只读检查 diff、route order、反向导入、asyncio 策略、行数和测试输出。

接口约定：

- `api/common_auth.py` 不得导入 `api.routes`；只能通过 `sys.modules.get("api.routes")` 读取旧 monkeypatch。
- `api/routes.py` 必须满足 `api.routes.verify_token is api.common_auth.verify_token`。
- `api/task_routes.py` 不得导入 `api.routes`。
- `api.task_routes.router` 不带 `/api/v1` 前缀，由父 `api.routes.router` include。
- `api.routes` 必须 re-export `ScheduledTaskCreate` 和六个 task endpoint。
- `run_scheduled_task_now()` 必须保持 `async def`。
- 生产代码不得新增 `asyncio.run()`，不得新增 `run_awaitable_sync`，不得新增同步函数包装 awaitable。
- 不改变 Prompt Runtime 模板、工具 usage 文档、`enriched_query`、conversation 结构或工具输出契约。

## 文件职责

- 创建：`tests/test_api_task_routes_split.py`
  - 锁定 common auth 对象身份与旧 token monkeypatch。
  - 锁定 `/tasks*` endpoint module 为 `api.task_routes`。
  - 锁定 `api.routes` 旧导入兼容。
  - 锁定 `/tasks` collection routes 先于 `/tasks/{task_id}` 动态 routes。
  - 锁定迁移 route 未重复注册。
  - 锁定 `run_scheduled_task_now()` 仍为 coroutine。
  - 锁定 `/health` 本阶段仍留在父模块。
  - 静态扫描新模块没有反向导入父模块、没有 `asyncio.run`、没有 `run_awaitable_sync`。
- 创建：`api/common_auth.py`
  - 定义 `_current_api_token()`。
  - 定义 `verify_token()`。
  - 兼容 `api.routes.NANOBOT_API_TOKEN` monkeypatch。
- 创建：`api/task_routes.py`
  - 定义 `ScheduledTaskCreate`。
  - 定义 `create_scheduled_task()`、`list_scheduled_tasks()`、`update_scheduled_task()`、
    `toggle_scheduled_task()`、`run_scheduled_task_now()`、`delete_scheduled_task()`。
  - 定义 `router = APIRouter(tags=["tasks"])`。
- 修改：`api/routes.py`
  - 从 `api.common_auth` import `verify_token`。
  - 删除本地 `verify_token()` 函数体和仅用于该函数的 `compare_digest` import。
  - 从 `api.task_routes` import `router as task_router`、`ScheduledTaskCreate` 和六个 endpoint。
  - 在原 `/tasks*` 区块位置 include `task_router`。
  - 删除本地 `ScheduledTaskCreate` 与六个 task endpoint 的实现。
- 收口阶段修改：`docs/todo.md`、`docs/plan_walkthrough.md`、
  `.Codex/plans/api-task-routes-split.md`。

## 任务 1：补普通 API task split 红灯测试

**文件：**
- 创建：`tests/test_api_task_routes_split.py`

- [ ] **步骤 1：创建测试文件**

创建 `tests/test_api_task_routes_split.py`：

```python
from __future__ import annotations

import inspect
from pathlib import Path

from fastapi import HTTPException


_TASK_ROUTE_SIGNATURES = (
    ("POST", "/api/v1/tasks"),
    ("GET", "/api/v1/tasks"),
    ("PUT", "/api/v1/tasks/{task_id}"),
    ("POST", "/api/v1/tasks/{task_id}/toggle"),
    ("POST", "/api/v1/tasks/{task_id}/run"),
    ("DELETE", "/api/v1/tasks/{task_id}"),
)

_TASK_ROUTE_EXPORTS = (
    "ScheduledTaskCreate",
    "create_scheduled_task",
    "list_scheduled_tasks",
    "update_scheduled_task",
    "toggle_scheduled_task",
    "run_scheduled_task_now",
    "delete_scheduled_task",
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


def test_api_verify_token_is_shared_common_auth_object():
    from api import routes
    from api import common_auth

    assert routes.verify_token is common_auth.verify_token


def test_api_common_auth_uses_legacy_api_routes_token_monkeypatch(monkeypatch):
    from api import common_auth

    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")

    assert common_auth.verify_token(authorization="Bearer split-token") is None
    for header in ("", "Bearer wrong"):
        try:
            common_auth.verify_token(authorization=header)
        except HTTPException as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError("expected HTTPException")


def test_api_task_routes_are_registered_from_split_module():
    for method, path in _TASK_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.task_routes"}


def test_legacy_api_routes_task_imports_still_work():
    from api import routes
    from api import task_routes

    for name in _TASK_ROUTE_EXPORTS:
        assert getattr(routes, name) is getattr(task_routes, name)

    body = routes.ScheduledTaskCreate(
        name="测试任务",
        target_id="u1",
        prompt_template="提醒我喝水",
    )
    assert body.cron_expr == "0 9 * * *"
    assert body.target_type == "private"


def test_split_task_routes_use_legacy_api_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/tasks",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/tasks",
        headers={"Authorization": "Bearer wrong"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_api_task_routes_are_not_registered_twice():
    for method, path in _TASK_ROUTE_SIGNATURES:
        assert len(_api_routes_for(path, method)) == 1, f"{method} {path}"


def test_api_task_collection_routes_precede_dynamic_task_routes():
    ordered = [(path, route) for path, route in _api_route_entries()]
    collection_indexes = [
        idx
        for idx, (path, route) in enumerate(ordered)
        if path == "/api/v1/tasks" and {"GET", "POST"} & getattr(route, "methods", set())
    ]
    dynamic_indexes = [
        idx
        for idx, (path, route) in enumerate(ordered)
        if path.startswith("/api/v1/tasks/{task_id}")
    ]

    assert collection_indexes
    assert dynamic_indexes
    assert max(collection_indexes) < min(dynamic_indexes)


def test_api_task_async_boundaries_remain_coroutines():
    from api import routes
    from api import task_routes

    assert inspect.iscoroutinefunction(task_routes.run_scheduled_task_now)
    assert inspect.iscoroutinefunction(routes.run_scheduled_task_now)


def test_api_task_routes_do_not_import_parent_routes_or_sync_awaitable():
    source = Path("api/task_routes.py").read_text(encoding="utf-8")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_health_check_stays_in_parent_routes():
    routes = _api_routes_for("/api/v1/health", "GET")

    assert routes
    assert {route.endpoint.__module__ for route in routes} == {"api.routes"}
```

- [ ] **步骤 2：运行红灯测试**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_task_routes_split.py
```

预期：失败。失败点应至少包含 `api.common_auth` 或 `api.task_routes` 尚不存在、`/tasks*`
endpoint module 仍为 `api.routes`、旧导入对象身份尚未迁移。

- [ ] **步骤 3：提交红灯测试**

运行：

```bash
git add tests/test_api_task_routes_split.py
git commit -m "test(普通API): 锁定任务路由拆分契约"
```

## 任务 2：抽普通 API 鉴权兼容层

**文件：**
- 创建：`api/common_auth.py`
- 修改：`api/routes.py`
- 测试：`tests/test_api_task_routes_split.py`、`tests/test_api.py`

- [ ] **步骤 1：创建 `api/common_auth.py`**

创建文件：

```python
"""普通 API 共享鉴权依赖。"""

from __future__ import annotations

import sys
from hmac import compare_digest

from fastapi import Header, HTTPException

from config import NANOBOT_API_TOKEN as CONFIG_API_TOKEN


def _current_api_token() -> str:
    # 兼容现有测试和调用方对 api.routes.NANOBOT_API_TOKEN 的 monkeypatch。
    routes = sys.modules.get("api.routes")
    if routes is not None and hasattr(routes, "NANOBOT_API_TOKEN"):
        return str(getattr(routes, "NANOBOT_API_TOKEN") or "")
    return str(CONFIG_API_TOKEN or "")


def verify_token(authorization: str = Header(default="")) -> None:
    token_config = _current_api_token()
    if not token_config:
        raise HTTPException(status_code=503, detail="API token not configured")
    token = authorization.replace("Bearer ", "").strip()
    if not token or not compare_digest(token, token_config):
        raise HTTPException(status_code=401, detail="Invalid or missing API token")
```

- [ ] **步骤 2：让 `api/routes.py` re-export 同一函数对象**

修改 `api/routes.py`：

```python
-from hmac import compare_digest
```

新增 import：

```python
from api.common_auth import verify_token
```

删除本地 `verify_token()` 函数体，保留 `config.NANOBOT_API_TOKEN` import 不变，以便旧
monkeypatch 路径仍存在。

- [ ] **步骤 3：运行鉴权定向测试**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api_task_routes_split.py::test_api_verify_token_is_shared_common_auth_object \
  tests/test_api_task_routes_split.py::test_api_common_auth_uses_legacy_api_routes_token_monkeypatch \
  tests/test_api.py::test_api_auth_no_token_configured_returns_503 \
  tests/test_api.py::test_api_auth_missing_or_wrong_token_returns_401 \
  tests/test_api.py::test_api_auth_accepts_valid_bearer_token
```

预期：上述 5 个测试通过；task route module 归属测试仍保持红灯。

- [ ] **步骤 4：静态检查鉴权模块**

运行：

```bash
python -B -m compileall api/routes.py api/common_auth.py
git diff --check -- api/routes.py api/common_auth.py tests/test_api_task_routes_split.py
```

预期：退出码 0，无空白错误。

- [ ] **步骤 5：提交鉴权兼容层**

运行：

```bash
git add api/common_auth.py api/routes.py
git commit -m "refactor(普通API): 抽出鉴权兼容层"
```

## 任务 3：拆出 `/tasks*` 路由模块

**文件：**
- 创建：`api/task_routes.py`
- 修改：`api/routes.py`
- 测试：`tests/test_api_task_routes_split.py`、`tests/test_api_push_envelope.py`、
  `tests/test_schedule_task_tool.py`、`tests/test_asyncio_run_policy.py`

- [ ] **步骤 1：创建 `api/task_routes.py`**

创建文件：

```python
"""普通 API 定时任务路由。"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.common_auth import verify_token
from core.database import get_db


logger = logging.getLogger("nanobot.routes")
router = APIRouter(tags=["tasks"])


class ScheduledTaskCreate(BaseModel):
    name: str
    cron_expr: str = "0 9 * * *"
    target_type: str = "private"
    target_id: str
    prompt_template: str


@router.post("/tasks")
def create_scheduled_task(
    req: ScheduledTaskCreate,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """创建定时任务。例如每天9点推送AI新闻到私聊。"""
    from core.database import ScheduledTask as ST

    task = ST(
        name=req.name,
        cron_expr=req.cron_expr,
        target_type=req.target_type,
        target_id=req.target_id,
        prompt_template=req.prompt_template,
    )
    db.add(task)
    db.commit()
    logger.info("Scheduled task created: %s cron=%s", req.name, req.cron_expr)
    return {"status": "ok", "id": task.id}


@router.get("/tasks")
def list_scheduled_tasks(
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """列出所有定时任务。"""
    from core.database import ScheduledTask as ST

    tasks = db.query(ST).all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "cron": t.cron_expr,
            "target": f"{t.target_type}/{t.target_id}",
            "enabled": t.enabled,
            "last_run": t.last_run_at.isoformat() if t.last_run_at else None,
        }
        for t in tasks
    ]


@router.put("/tasks/{task_id}")
def update_scheduled_task(
    task_id: int,
    req: ScheduledTaskCreate,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """修改定时任务。"""
    from core.database import ScheduledTask as ST

    t = db.query(ST).filter(ST.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    t.name = req.name
    t.cron_expr = req.cron_expr
    t.target_type = req.target_type
    t.target_id = req.target_id
    t.prompt_template = req.prompt_template
    db.commit()
    return {"status": "ok"}


@router.post("/tasks/{task_id}/toggle")
def toggle_scheduled_task(
    task_id: int,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """启用/禁用定时任务。"""
    from core.database import ScheduledTask as ST

    t = db.query(ST).filter(ST.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    t.enabled = 0 if t.enabled else 1
    db.commit()
    return {"status": "ok", "enabled": bool(t.enabled)}


@router.post("/tasks/{task_id}/run")
async def run_scheduled_task_now(
    task_id: int,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """立即执行指定定时任务（生成内容并推送）。"""
    from core.daily_digest import _generate_task_message, push_envelope_to_qq
    from core.database import ScheduledTask as ST
    from core.message_envelope import build_chat_response_envelope

    t = db.query(ST).filter(ST.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")

    logger.info("Manual run: %s", t.name)
    content = await _generate_task_message(t)
    if not content:
        raise HTTPException(status_code=500, detail="LLM returned no content")

    envelope = build_chat_response_envelope(
        status="ok",
        answer=content,
        meta={
            "platform": "qq",
            "chat_type": "scheduled_task",
            "task_id": t.id,
            "task_name": t.name,
            "target_type": t.target_type,
            "target_id": t.target_id,
        },
    )
    ok = await push_envelope_to_qq(t.target_type, t.target_id, envelope)
    if ok:
        t.last_run_at = datetime.now()
        db.commit()
        return {
            "status": "ok",
            "content": content[:200],
            "target": f"{t.target_type}/{t.target_id}",
        }
    raise HTTPException(status_code=502, detail="Push to QQ failed")


@router.delete("/tasks/{task_id}")
def delete_scheduled_task(
    task_id: int,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """删除定时任务。"""
    from core.database import ScheduledTask as ST

    t = db.query(ST).filter(ST.id == task_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    db.delete(t)
    db.commit()
    return {"status": "ok"}
```

- [ ] **步骤 2：修改 `api/routes.py` 聚合和旧导入**

在 import 区加入：

```python
from api.task_routes import (
    ScheduledTaskCreate,
    create_scheduled_task,
    delete_scheduled_task,
    list_scheduled_tasks,
    router as task_router,
    run_scheduled_task_now,
    toggle_scheduled_task,
    update_scheduled_task,
)
```

删除原本本地定义的 `ScheduledTaskCreate` 和六个 task endpoint。

在原 `/tasks*` 区块位置加入：

```python
router.include_router(task_router)
```

保留 `/health` endpoint 在父模块。

- [ ] **步骤 3：运行 split 绿灯测试**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_task_routes_split.py
```

预期：全部通过。

- [ ] **步骤 4：运行行为与相邻回归**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api_push_envelope.py::test_run_scheduled_task_now_uses_push_envelope \
  tests/test_schedule_task_tool.py \
  tests/test_api.py::test_api_auth_no_token_configured_returns_503 \
  tests/test_api.py::test_api_auth_missing_or_wrong_token_returns_401 \
  tests/test_api.py::test_api_auth_accepts_valid_bearer_token \
  tests/test_asyncio_run_policy.py
```

预期：全部通过。

- [ ] **步骤 5：运行静态检查**

运行：

```bash
python -B -m compileall api/routes.py api/common_auth.py api/task_routes.py
rg -n "from api\\.routes|import api\\.routes|asyncio\\.run|run_awaitable_sync" api/task_routes.py
git diff --check -- api/routes.py api/common_auth.py api/task_routes.py tests/test_api_task_routes_split.py
wc -l api/routes.py api/task_routes.py tests/test_api_task_routes_split.py
```

预期：

- `compileall` 退出码 0。
- `rg` 无命中，退出码 1。
- `git diff --check` 无输出。
- `api/routes.py` 行数下降；本阶段不要求低于 800 行。

- [ ] **步骤 6：全量测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：0 failures。

- [ ] **步骤 7：提交 task 路由拆分**

运行：

```bash
git add api/common_auth.py api/task_routes.py api/routes.py tests/test_api_task_routes_split.py
git commit -m "refactor(普通API): 拆分任务路由"
```

如果任务 2 已经单独提交 `api/common_auth.py` 和 `api/routes.py` 的鉴权部分，本步骤只暂存
`api/task_routes.py`、`api/routes.py`、`tests/test_api_task_routes_split.py` 中与 route
拆分相关的差异。

## 任务 4：文档收口

**文件：**
- 修改：`.Codex/plans/api-task-routes-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [ ] **步骤 1：更新本计划执行记录**

在“当前状态”勾选完成任务 1、任务 2、任务 3，并新增“执行记录（2026-06-21）”。
执行记录必须逐条写入真实命令、退出状态和 pytest 汇总，至少包含：

- 红灯测试的失败数量和失败原因。
- common auth 定向绿灯的通过数量。
- split 绿灯的通过数量。
- 行为与相邻回归的通过数量。
- 静态检查结果：`compileall`、反向导入 / awaitable 扫描、`git diff --check`。
- 行数：`api/routes.py`、`api/task_routes.py`、`tests/test_api_task_routes_split.py`。
- 全量测试汇总。

- [ ] **步骤 2：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」的 `api/routes.py` 进展下追加一条：

```markdown
  - 进展：`api/routes.py` 第二刀已先抽出普通 API `verify_token` 共享兼容层到
    `api/common_auth.py`，再拆出 `/tasks*` 定时任务路由到 `api/task_routes.py`；
    `api.routes.verify_token` 与 `api.common_auth.verify_token` 保持同一函数对象，
    旧 `api.routes.NANOBOT_API_TOKEN` monkeypatch、`app.dependency_overrides[routes.verify_token]`、
    `/tasks*` HTTP 契约、push envelope 行为和 `run_scheduled_task_now()` 协程边界保持不变。
```

写入时补充实际行数和验证汇总。

- [ ] **步骤 3：更新 `docs/plan_walkthrough.md`**

追加 `## 2026-06-21 普通 API Tasks 路由拆分`，记录：

- 设计文档与提交。
- 计划文件与提交。
- 红灯测试提交。
- common auth 提交。
- task route 拆分提交。
- 验证记录。
- 下一刀建议：evolution / memory / models。

- [ ] **步骤 4：文档检查**

运行：

```bash
rg -n "未替换标记|坏字符" docs/todo.md docs/plan_walkthrough.md .Codex/plans/api-task-routes-split.md
git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/api-task-routes-split.md
```

预期：未替换标记和坏字符扫描无命中，`git diff --check` 无输出。

- [ ] **步骤 5：最终全量测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：0 failures。

- [ ] **步骤 6：提交文档收口**

运行：

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/api-task-routes-split.md
git commit -m "docs(计划): 收口任务路由拆分"
```

## 风险与回滚边界

- 如果 common auth 对象身份测试失败，不继续拆 `/tasks*`，先修复 `api.routes.verify_token is api.common_auth.verify_token`。
- 如果 `app.dependency_overrides[routes.verify_token]` 对 `/tasks*` 不生效，不继续提交拆分。
- 如果子模块需要访问父模块变量，改为把共享依赖下沉到 `api.common_auth` 或业务模块，不允许从 `api.routes` 反向导入。
- 如果 `/tasks*` 行为回归失败，优先对比原 `api/routes.py` 旧实现，不扩展功能、不改变 response shape。
- 如果全量测试失败且原因不属于本阶段改动，不提交本阶段实现；记录失败测试和可疑外部脏项，等待进一步决策。

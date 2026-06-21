# 普通 API Models 路由拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把普通 `api/routes.py` 中的 `/models/list` 和 `/models/sync` HTTP 层迁移到 `api/model_routes.py`，保持旧导入、鉴权 monkeypatch、response shape、`sync_models()` 协程边界和 NewAPI 同步行为不变，并继续推进 `api/routes.py` 超大文件治理。

**架构：** `api.routes` 继续作为 `/api/v1` 聚合 router，`server.py` 不新增 include 入口。新增 `api.model_routes.router` 承载 `/models/list` 和 `/models/sync`；父模块在原 models 路由位置 include 子 router，并 re-export `ModelSyncRequest`、`list_models()` 和 `sync_models()`。

**技术栈：** Python 3.12、FastAPI、Pydantic、pytest、项目既有 `api.common_auth`、`clients.model_registry.registry`、`clients.new_api_client.NewAPIClient`。

---

## 当前状态（2026-06-21）

- [x] 已完成 memory 路由拆分，`api/routes.py` 当时为 2523 行。
- [x] 已完成 models / evolution 候选审计，确认 `models` 是当前最低风险下一刀。
- [x] 已写设计文档：`docs/superpowers/specs/2026-06-21-api-model-routes-split-design.md`。
- [x] 设计提交：`7887681 docs(普通API): 设计模型路由拆分`。
- [x] 计划提交：`44d344d docs(计划): 记录模型路由拆分计划`。
- [x] 任务 1：补普通 API model route split 红灯测试并提交。
- [x] 红灯测试提交：`6e5291f test(普通API): 锁定模型路由拆分契约`。
- [x] 任务 2：拆出 `api/model_routes.py` 并由 `api.routes` include / re-export 后提交。
- [x] 实现提交：`6e1a2d4 refactor(普通API): 拆分模型路由`。
- [x] 任务 3：更新 `docs/todo.md`、`docs/plan_walkthrough.md` 和本计划执行记录后提交。
- [x] models 拆分后，`api/routes.py` 当前为 2484 行。

## 文件职责

- 创建：`tests/test_api_model_routes_split.py`
  - 锁定 `/models/list` 和 `/models/sync` endpoint module 为 `api.model_routes`。
  - 锁定 `api.routes` 旧导入兼容。
  - 锁定迁移 route 未重复注册。
  - 锁定拆分路由继续兼容 `api.routes.NANOBOT_API_TOKEN` monkeypatch。
  - 锁定 `sync_models()` 仍为 coroutine。
  - 锁定 list 的 provider / tier 过滤和 response shape。
  - 锁定 sync 的缺少 `NEW_API_KEY` 400 和成功路径 `force` 透传。
  - 静态扫描新模块没有反向导入父模块、没有 `asyncio.run`、没有 `run_awaitable_sync`。
- 修改：`tests/test_api_memory_routes_split.py`
  - 从父模块尾部路由列表中移除 `/models/list` 和 `/models/sync`。
  - 保留 `/evolution/trigger` 与 `/health` 仍在父模块。
- 创建：`api/model_routes.py`
  - 定义 `ModelSyncRequest`。
  - 定义 `list_models()`。
  - 定义 `sync_models()`。
  - 定义 `router = APIRouter(tags=["models"])`。
- 修改：`api/routes.py`
  - 从 `api.model_routes` import `router as model_router`、`ModelSyncRequest`、`list_models`、`sync_models`。
  - 删除本地 `ModelSyncRequest` 与两个 models endpoint 实现。
  - 在原 models 路由所在区域 include `model_router`。
  - 删除父模块只服务 models 的 import：`registry`、`NewAPIClient`。
- 收口阶段修改：`docs/todo.md`、`docs/plan_walkthrough.md`、`.Codex/plans/api-model-routes-split.md`。

## 任务 1：补普通 API model split 红灯测试

**文件：**
- 创建：`tests/test_api_model_routes_split.py`
- 修改：`tests/test_api_memory_routes_split.py`

- [x] **步骤 1：创建测试文件**

创建 `tests/test_api_model_routes_split.py`：

```python
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


_MODEL_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/models/list"),
    ("POST", "/api/v1/models/sync"),
)

_PARENT_ROUTE_SIGNATURES = (
    ("POST", "/api/v1/evolution/trigger"),
    ("GET", "/api/v1/health"),
)

_MODEL_ROUTE_EXPORTS = (
    "ModelSyncRequest",
    "list_models",
    "sync_models",
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


def test_api_model_routes_are_registered_from_split_module():
    for method, path in _MODEL_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.model_routes"}


def test_legacy_api_routes_model_imports_still_work():
    from api import model_routes
    from api import routes

    for name in _MODEL_ROUTE_EXPORTS:
        assert getattr(routes, name) is getattr(model_routes, name)

    body = routes.ModelSyncRequest(force=False)
    assert body.force is False


def test_split_model_routes_use_legacy_api_token_monkeypatch(monkeypatch):
    from server import app

    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")
    with TestClient(app) as test_client:
        ok = test_client.get(
            "/api/v1/models/list",
            headers={"Authorization": "Bearer split-token"},
        )
        wrong = test_client.get(
            "/api/v1/models/list",
            headers={"Authorization": "Bearer wrong"},
        )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_api_model_routes_are_not_registered_twice():
    for method, path in _MODEL_ROUTE_SIGNATURES:
        assert len(_api_routes_for(path, method)) == 1, f"{method} {path}"


def test_api_model_async_boundaries_remain_coroutines():
    from api import model_routes
    from api import routes

    assert inspect.iscoroutinefunction(model_routes.sync_models)
    assert inspect.iscoroutinefunction(routes.sync_models)


def test_api_model_routes_do_not_import_parent_routes_or_sync_awaitable():
    source = Path("api/model_routes.py").read_text(encoding="utf-8")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_model_list_filters_provider_and_tier(client, monkeypatch):
    from api import model_routes

    class FakeRegistry:
        data = {"last_updated": "2026-06-21T00:00:00"}

        def get_models_by_provider(self, provider):
            assert provider == "new-api"
            return [
                {"id": "fast-model", "tier": "fast"},
                {"id": "smart-model", "tier": "smart"},
                {"id": "missing-tier"},
            ]

    monkeypatch.setattr(model_routes, "registry", FakeRegistry())

    response = client.get("/api/v1/models/list?provider=new-api&tier=fast")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["provider"] == "new-api"
    assert data["count"] == 1
    assert data["last_updated"] == "2026-06-21T00:00:00"
    assert data["models"] == [{"id": "fast-model", "tier": "fast"}]


def test_model_sync_rejects_missing_api_key(client, monkeypatch):
    monkeypatch.setattr("config.NEW_API_KEY", "")

    response = client.post("/api/v1/models/sync", json={"force": False})

    assert response.status_code == 400
    assert response.json()["detail"] == "NEW_API_KEY is missing"


def test_model_sync_uses_force_and_returns_updated_count(client, monkeypatch):
    from api import model_routes

    calls = []

    class FakeNewAPIClient:
        def __init__(self, *, api_key, base_url):
            calls.append(("init", api_key, base_url))

        async def sync_models_to_registry(self, *, force):
            calls.append(("sync", force))
            return 7

    monkeypatch.setattr("config.NEW_API_KEY", "test-key")
    monkeypatch.setattr("config.NEW_API_BASE_URL", "http://new-api")
    monkeypatch.setattr(model_routes, "NewAPIClient", FakeNewAPIClient)
    monkeypatch.setattr(
        model_routes,
        "registry",
        SimpleNamespace(data={"last_updated": "2026-06-21T01:02:03"}),
    )

    response = client.post("/api/v1/models/sync", json={"force": False})

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "updated": 7,
        "last_updated": "2026-06-21T01:02:03",
    }
    assert calls == [("init", "test-key", "http://new-api"), ("sync", False)]


def test_non_model_tail_routes_stay_in_parent_routes():
    for method, path in _PARENT_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.routes"}
```

- [x] **步骤 2：更新 memory split 测试的父模块尾部路由列表**

修改 `tests/test_api_memory_routes_split.py`：

```python
_PARENT_ROUTE_SIGNATURES = (
    ("POST", "/api/v1/evolution/trigger"),
    ("GET", "/api/v1/health"),
)
```

- [x] **步骤 3：运行测试验证红灯**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_model_routes_split.py tests/test_api_memory_routes_split.py
```

预期：FAIL。失败点应指向 `api.model_routes` 尚不存在、models endpoint module 仍为
`api.routes`、`api/model_routes.py` 文件尚不存在。

- [x] **步骤 4：提交红灯测试**

运行：

```bash
git add tests/test_api_model_routes_split.py tests/test_api_memory_routes_split.py
git commit -m "test(普通API): 锁定模型路由拆分契约"
```

## 任务 2：拆出 `api.model_routes`

**文件：**
- 创建：`api/model_routes.py`
- 修改：`api/routes.py`

- [x] **步骤 1：创建 `api/model_routes.py`**

创建 `api/model_routes.py`：

```python
"""普通 API 模型注册表路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.common_auth import verify_token
from clients.model_registry import registry
from clients.new_api_client import NewAPIClient


router = APIRouter(tags=["models"])


class ModelSyncRequest(BaseModel):
    force: bool = True


@router.get("/models/list")
def list_models(
    provider: str = "new-api",
    tier: str = "",
    _auth=Depends(verify_token),
):
    """查看本地模型注册表中的模型列表。"""
    items = registry.get_models_by_provider(provider)
    if tier:
        items = [m for m in items if (m.get("tier") or "") == tier]
    return {
        "status": "ok",
        "provider": provider,
        "count": len(items),
        "last_updated": registry.data.get("last_updated", "never"),
        "models": items,
    }


@router.post("/models/sync")
async def sync_models(
    req: ModelSyncRequest,
    _auth=Depends(verify_token),
):
    """从 new-api 拉取模型列表并同步至本地 registry。"""
    from config import NEW_API_KEY, NEW_API_BASE_URL

    if not NEW_API_KEY:
        raise HTTPException(status_code=400, detail="NEW_API_KEY is missing")

    client = NewAPIClient(api_key=NEW_API_KEY, base_url=NEW_API_BASE_URL)
    updated = await client.sync_models_to_registry(force=req.force)

    return {
        "status": "ok",
        "updated": updated,
        "last_updated": registry.data.get("last_updated", "never"),
    }
```

- [x] **步骤 2：修改 `api/routes.py` import 与 re-export**

删除：

```python
from clients.model_registry import registry
from clients.new_api_client import NewAPIClient
```

新增：

```python
from api.model_routes import (
    ModelSyncRequest,
    list_models,
    router as model_router,
    sync_models,
)
```

- [x] **步骤 3：删除父模块本地 models 定义并 include 子 router**

删除 `api/routes.py` 中本地 `ModelSyncRequest`、`list_models()` 和 `sync_models()`。

在原 models endpoint 所在区域加入：

```python
router.include_router(model_router)
```

该 include 应位于 `memory_router` 之后、`task_router` 之前。

- [x] **步骤 4：运行 split 定向测试验证绿灯**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_model_routes_split.py tests/test_api_memory_routes_split.py
```

预期：PASS。

- [x] **步骤 5：运行相邻回归与静态检查**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_asyncio_run_policy.py tests/test_api_task_routes_split.py
python -B -m compileall api/routes.py api/model_routes.py
rg -n "from api\\.routes|import api\\.routes|asyncio\\.run|run_awaitable_sync" api/model_routes.py
git diff --check -- api/routes.py api/model_routes.py tests/test_api_model_routes_split.py tests/test_api_memory_routes_split.py
```

预期：

- pytest PASS。
- compileall 成功。
- `rg` 无命中，退出码为 1。
- `git diff --check` 无输出。

- [x] **步骤 6：提交 models 路由拆分**

运行：

```bash
git add api/routes.py api/model_routes.py
git commit -m "refactor(普通API): 拆分模型路由"
```

## 任务 3：文档收口与全量验证

**文件：**
- 修改：`.Codex/plans/api-model-routes-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [x] **步骤 1：更新计划执行记录**

在本计划的「当前状态」中把任务 1 和任务 2 标记为完成，并新增「执行记录」章节，记录：

- 红灯命令、失败数量和失败原因。
- split 绿灯命令和通过数量。
- 静态检查结果。
- `wc -l api/routes.py api/model_routes.py tests/test_api_model_routes_split.py` 行数。
- 全量回归结果。

- [x] **步骤 2：更新 `docs/todo.md`**

在「超大文件 >800 行拆分」条目下记录：

- `api/routes.py` 已完成 models 路由拆分。
- 新增 `api/model_routes.py`。
- `api/routes.py` 最新行数。
- 下一候选为 evolution route-only 或其他更大低耦合边界。

- [x] **步骤 3：更新 `docs/plan_walkthrough.md`**

追加 2026-06-21 的 models 路由拆分执行记录，包含：

- 设计文档提交。
- 计划文档提交。
- 红灯测试提交。
- 实现提交。
- 验证命令和结果。
- 下一步建议。

- [x] **步骤 4：运行全量测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：0 failures。

- [x] **步骤 5：文档格式与状态检查**

运行：

```bash
git diff --check -- .Codex/plans/api-model-routes-split.md docs/todo.md docs/plan_walkthrough.md
git status --short
```

预期：`git diff --check` 无输出；`git status --short` 中本阶段只包含计划与文档相关改动，以及历史无关脏项。

- [x] **步骤 6：提交文档收口**

运行：

```bash
git add .Codex/plans/api-model-routes-split.md docs/todo.md docs/plan_walkthrough.md
git commit -m "docs(计划): 收口模型路由拆分"
```

## 执行记录（2026-06-21）

- 红灯：`python -B -m pytest -q -p no:cacheprovider tests/test_api_model_routes_split.py tests/test_api_memory_routes_split.py`
  -> `6 failed, 11 passed, 21 warnings in 7.79s`。失败点符合预期，分别指向
  `api.model_routes` 尚不存在、models endpoint module 仍为 `api.routes`，以及
  `api/model_routes.py` 文件尚不存在。
- Split 绿灯：`python -B -m pytest -q -p no:cacheprovider tests/test_api_model_routes_split.py tests/test_api_memory_routes_split.py`
  -> `17 passed, 21 warnings in 2.71s`。
- 相邻回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_asyncio_run_policy.py tests/test_api_task_routes_split.py`
  -> `13 passed, 21 warnings in 2.61s`。
- 静态检查：`python -B -m compileall api/routes.py api/model_routes.py` 成功；
  `rg -n "from api\.routes|import api\.routes|asyncio\.run|run_awaitable_sync" api/model_routes.py`
  无命中，退出码为 1；`git diff --check -- api/routes.py api/model_routes.py tests/test_api_model_routes_split.py tests/test_api_memory_routes_split.py`
  无输出。
- 行数检查：`api/routes.py` 2484 行，`api/model_routes.py` 57 行，
  `tests/test_api_model_routes_split.py` 189 行。
- 全量：`python -B -m pytest -p no:cacheprovider tests/ -v` ->
  `1594 passed, 6 skipped, 139 warnings in 114.82s`。

## 最终验收清单

- [x] `tests/test_api_model_routes_split.py` 经历红灯再绿灯。
- [x] `tests/test_api_memory_routes_split.py` 同步更新并通过。
- [x] `api.model_routes` 不导入 `api.routes`。
- [x] `api.routes` re-export `ModelSyncRequest`、`list_models()` 和 `sync_models()`。
- [x] `sync_models()` 保持 coroutine function。
- [x] `/evolution/trigger` 和 `/health` 仍留在 `api.routes`。
- [x] `tests/test_asyncio_run_policy.py` 通过。
- [x] 全量 `tests/` 回归 0 failures。
- [x] 每个阶段性改动都有独立 commit。

# Admin Models 路由拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `api/admin_routes.py` 中模型管理相关 HTTP 层拆到 `api/admin/model_routes.py`，保持模型状态、provider、catalog、route test、本地组件测试、TimingGate 稳定性测试、健康检查、鉴权 monkeypatch、审计语义、前端响应结构和旧导入路径不变。

**架构：** `api.admin_routes` 继续作为 `/api/v1/admin` 顶层聚合 router，新建 `api.admin.model_routes.router` 并由父 router include。新模块只承接模型管理 HTTP 编排、request model、常量和小型 helper；模型路由解析、provider catalog、New API 调用、本地组件加载和 settings cache 继续由既有服务提供。

**技术栈：** Python 3.12、FastAPI、Pydantic、SQLAlchemy、aiohttp、pytest、项目既有 `api.admin.common` 鉴权与审计 helper。

---

## 当前状态（2026-06-21）

- [x] 已核对 `docs/todo.md` 剩余硬项：`api/admin_routes.py` 3761 行、`api/routes.py` 2822 行。
- [x] 已完成只读子 agent 分析：模型管理 Admin API 是下一刀优先边界。
- [x] 已写设计文档：`docs/superpowers/specs/2026-06-21-admin-model-routes-split-design.md`。
- [x] 设计提交：`08be4d6 docs(管理端): 设计模型路由拆分`。
- [x] 设计阶段全量验证：`python -m pytest tests/ -v` ->
  `1529 passed, 6 skipped, 139 warnings in 108.46s`。
- [x] 计划提交：`f5ed550 docs(计划): 记录模型路由拆分计划`。
- [x] 红灯测试：`tests/test_admin_model_routes_split.py -q` ->
  `3 failed, 3 passed, 21 warnings in 6.63s`；失败点为 endpoint module 仍是
  `api.admin_routes`、`api.admin.model_routes` 尚不存在，以及
  `api/admin/model_routes.py` 文件不存在。
- [x] 实现提交：`c2966c7 refactor(管理端): 拆分模型路由`。
- [x] 实现阶段验证：
  - split 绿灯：`6 passed, 21 warnings in 1.24s`。
  - 模型行为回归：`22 passed, 1 warning in 1.95s`。
  - 拆分兼容回归：`41 passed, 21 warnings in 8.18s`。
  - 鉴权与 asyncio 策略回归：`10 passed, 1 warning in 2.63s`。
  - 静态检查：`compileall` / `git diff --check` 无输出；反向导入与
    `asyncio.run` / `run_awaitable_sync` 扫描无命中。
  - 行数：`api/admin_routes.py` 2647 行，`api/admin/model_routes.py` 1178 行，
    `tests/test_admin_model_routes_split.py` 159 行。
  - 全量：`1535 passed, 6 skipped, 139 warnings in 109.68s`。
- [x] 文档收口提交前验证：
  - `git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/admin-model-routes-split.md`
    无输出。
  - `python -m pytest tests/ -v` ->
    `1535 passed, 6 skipped, 139 warnings in 109.35s`。

## 子 agent 分工约定

- **Agent A：测试契约。** 只修改 `tests/test_admin_model_routes_split.py`，不改生产代码。输出红灯测试结果、失败数量和主要失败原因。
- **Agent B：模型路由模块草稿。** 只创建 `api/admin/model_routes.py`，不改 `api/admin_routes.py`。按本计划迁移 request model、常量、helper 和模型管理 endpoint。
- **Agent C：父模块集成。** 只修改 `api/admin_routes.py`。负责 include `model_router`、re-export 旧符号、删除旧模型区块，并保留其他管理端子域仍使用的 helper。
- **Agent D：验证审查。** 只读检查 `git diff`、路由注册、反向导入、`asyncio.run` 策略、行数和测试输出。不得修改代码。

接口约定：

- `api/admin/model_routes.py` 导出 `router`，不得带 `/api/v1/admin` 前缀。
- `router` 不使用 `prefix="/models"`，因为模块同时承载 legacy `/model-catalog` 和 `/model-routes`。
- 新模块使用 `api.admin.common.verify_admin`、`audit()`、`audit_request()` 和 `client_ip()`，不得从 `api.admin_routes` 导入任何符号。
- `api.admin_routes` 必须 re-export 迁移后的 request model、常量、helper 和 endpoint 函数。
- `/model-replies` 保留在 `api.admin_routes`，不迁移到 `api.admin.model_routes`。
- 生产代码不得新增 `asyncio.run()`，不得新增同步函数包装 awaitable。
- 不改变 Prompt Runtime 模板、`enriched_query`、工具 usage 文档或 Prompt Runtime 输入。

## 文件职责

- 创建：`tests/test_admin_model_routes_split.py`
  - 锁定 19 个模型管理 route 的 endpoint module 为 `api.admin.model_routes`。
  - 锁定 `/model-replies` 仍留在 `api.admin_routes`。
  - 锁定 `api.admin_routes` 对迁移符号的旧导入兼容。
  - 锁定 `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 对新模块路由仍生效。
  - 锁定迁移路由未重复注册。
  - 静态扫描新模块没有反向导入父模块、没有 `asyncio.run`、没有 `run_awaitable_sync`。
- 创建：`api/admin/model_routes.py`
  - 定义 `router = APIRouter(tags=["admin-models"])`。
  - 持有 `ChatModelTestRequest`、`ProviderUpdateBody`、`ModelCatalogPatch`、`ModelRoutePatch`、`ModelRouteEditBody`、`TimingGateStabilityRequest`。
  - 持有 `_ALLOWED_TIERS`、`_STAGE_META`、`_ROUTE_SETTING_MAP`、`_CLASSIFIER_ROUTE_KEYS`、`_ROUTE_ALIAS`、`_CHAT_ROUTES`、`_TINY_TEST_PNG`。
  - 持有 `_resolve_route_value()`、`_resolve_route_key()`、`_redact()`、`_test_nli_contradiction()`。
  - 持有 19 个模型管理 endpoint。
- 修改：`api/admin_routes.py`
  - 导入并 include `model_router`。
  - re-export 迁移符号。
  - 删除本地模型管理实现区块，但保留 `/model-replies`。
  - 保留仍被 overview、group、settings、reply/eval、eval 使用的 `_safe_dict()`、`_iso()`、`_audit()`、`_audit_request()`、`_client_ip()`、`ChatLog`、`ConversationTurn`、`User`、`SystemSetting`、`AdminAuditLog` 和 `row_to_dict`。
- 后续文档收口阶段修改：`docs/todo.md`、`docs/plan_walkthrough.md`、`.Codex/plans/admin-model-routes-split.md`。

## 任务 1：补 Admin Models 路由拆分红灯测试

**文件：**
- 创建：`tests/test_admin_model_routes_split.py`

- [x] **步骤 1：创建 split 路由测试文件**

创建 `tests/test_admin_model_routes_split.py`：

```python
from __future__ import annotations

from pathlib import Path


_ADMIN_MODEL_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/admin/models/status"),
    ("POST", "/api/v1/admin/models/chat-test"),
    ("GET", "/api/v1/admin/model-catalog"),
    ("PATCH", "/api/v1/admin/model-catalog/{model_id}"),
    ("GET", "/api/v1/admin/models/providers"),
    ("PUT", "/api/v1/admin/models/providers/{provider_id}"),
    ("GET", "/api/v1/admin/models/catalog"),
    ("GET", "/api/v1/admin/models/route-references"),
    ("POST", "/api/v1/admin/models/catalog/refresh"),
    ("GET", "/api/v1/admin/model-routes"),
    ("PATCH", "/api/v1/admin/model-routes/{stage}"),
    ("PUT", "/api/v1/admin/models/routes/{route_key}"),
    ("POST", "/api/v1/admin/models/routes/{route_key}/test"),
    ("GET", "/api/v1/admin/models/routes/{route_key}/resolved"),
    ("GET", "/api/v1/admin/models/available"),
    ("POST", "/api/v1/admin/models/local/{component}/test"),
    ("POST", "/api/v1/admin/models/local/{component}/warmup"),
    ("POST", "/api/v1/admin/models/timing-gate-stability-test"),
    ("POST", "/api/v1/admin/models/health-check"),
)


_MODEL_ROUTE_EXPORTS = (
    "ChatModelTestRequest",
    "ProviderUpdateBody",
    "ModelCatalogPatch",
    "ModelRoutePatch",
    "ModelRouteEditBody",
    "TimingGateStabilityRequest",
    "_ALLOWED_TIERS",
    "_STAGE_META",
    "_ROUTE_SETTING_MAP",
    "_CLASSIFIER_ROUTE_KEYS",
    "_ROUTE_ALIAS",
    "_CHAT_ROUTES",
    "_TINY_TEST_PNG",
    "_resolve_route_value",
    "_resolve_route_key",
    "_redact",
    "_test_nli_contradiction",
    "models_status",
    "chat_model_test",
    "get_model_catalog",
    "patch_model_catalog",
    "list_model_providers",
    "update_model_provider",
    "get_model_catalog_v2",
    "get_route_references",
    "refresh_model_catalog",
    "get_model_routes",
    "patch_model_route",
    "edit_model_route",
    "test_model_route",
    "get_resolved_route",
    "list_available_models",
    "test_local_component",
    "warmup_local_component",
    "timing_gate_stability_test",
    "model_health_check",
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


def test_admin_model_routes_are_registered_from_split_module():
    for method, path in _ADMIN_MODEL_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.model_routes"}


def test_model_replies_stays_in_parent_admin_routes():
    routes = _admin_routes_for("/api/v1/admin/model-replies", "GET")

    assert routes
    assert {route.endpoint.__module__ for route in routes} == {"api.admin_routes"}


def test_legacy_admin_routes_model_imports_still_work():
    from api import admin_routes
    from api.admin import model_routes

    for name in _MODEL_ROUTE_EXPORTS:
        assert getattr(admin_routes, name) is getattr(model_routes, name)

    assert admin_routes.ChatModelTestRequest(model="x").model == "x"
    assert admin_routes.ProviderUpdateBody(enabled=True).enabled is True
    assert admin_routes._resolve_route_key("vision")[1] == "sticker_describe"
    assert admin_routes._redact({"x.api_key": "secret", "x.model": "m"}) == {
        "x.api_key": "***",
        "x.model": "m",
    }


def test_split_model_routes_use_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/model-catalog",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/model-catalog",
        headers={"Authorization": "Bearer test-token"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_admin_model_routes_are_not_registered_twice():
    for method, path in _ADMIN_MODEL_ROUTE_SIGNATURES:
        assert len(_admin_routes_for(path, method)) == 1, f"{method} {path}"


def test_admin_model_routes_do_not_import_parent_admin_routes_or_sync_awaitable():
    path = Path("api/admin/model_routes.py")

    assert path.exists()
    source = path.read_text(encoding="utf-8")

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
  tests/test_admin_model_routes_split.py -q
```

预期：失败。失败原因应包含 endpoint module 仍为 `api.admin_routes`、`api.admin.model_routes`
不存在、或 `api/admin/model_routes.py` 不存在。

- [x] **步骤 3：记录红灯结果并进入实现**

记录失败数量和主要失败原因。不要提交红灯状态；项目提交门禁要求失败数为 0。红灯测试文件会在实现转绿后与 `api/admin/model_routes.py` 和 `api/admin_routes.py` 一起提交。

## 任务 2：创建 `api/admin/model_routes.py`

**文件：**
- 创建：`api/admin/model_routes.py`
- 参考：`api/admin_routes.py` 中 `# Model status / tests` 到 `/models/timing-gate-stability-test` 结束，以及 `# ── Model Health Check ──` 区块。

- [x] **步骤 1：创建模块头和 router**

创建 `api/admin/model_routes.py`，模块开头使用以下结构：

```python
"""Admin Models 路由。"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.admin.common import audit, audit_request, client_ip, verify_admin
from core.database import SystemSetting, get_db

logger = logging.getLogger("nanobot.admin")
router = APIRouter(tags=["admin-models"])
```

不得导入 `api.admin_routes`。不得使用 `prefix="/models"`。

- [x] **步骤 2：迁移模型状态和连通性测试**

从 `api/admin_routes.py` 迁移以下符号，保持函数体逻辑不变：

- `models_status`
- `ChatModelTestRequest`
- `chat_model_test`

注意：

- `models_status()` 内的 `clients.classifier_client`、`core.route_metadata`、`core.persona_preprocess`、`core.semantic.provider_factory` import 继续留在函数体内。
- `chat_model_test()` 保持 `async def`，继续 `await client.chat_completion(...)`。
- 不改变 `/models/status` 返回字段。

- [x] **步骤 3：迁移 provider、catalog 和 legacy catalog**

迁移以下符号：

- `get_model_catalog`
- `list_model_providers`
- `ProviderUpdateBody`
- `update_model_provider`
- `get_model_catalog_v2`
- `get_route_references`
- `refresh_model_catalog`
- `_ALLOWED_TIERS`
- `ModelCatalogPatch`
- `patch_model_catalog`

替换共享 helper：

- `_audit(...)` -> `audit(...)`
- `_audit_request(...)` -> `audit_request(...)`
- `_client_ip(request)` -> `client_ip(request)`

保持 `refresh_model_catalog()` 内的 `urllib.request`、`datetime`、`list_providers()` 和
`build_provider_catalog()` 行为不变。

- [x] **步骤 4：迁移 legacy stage route 和 canonical route 编辑**

迁移以下符号：

- `_STAGE_META`
- `_resolve_route_value`
- `get_model_routes`
- `ModelRoutePatch`
- `patch_model_route`
- `_ROUTE_SETTING_MAP`
- `_CLASSIFIER_ROUTE_KEYS`
- `_ROUTE_ALIAS`
- `_CHAT_ROUTES`
- `_resolve_route_key`
- `_redact`
- `ModelRouteEditBody`
- `edit_model_route`

替换共享 helper：

- `_audit(...)` -> `audit(...)`
- `_client_ip(request)` -> `client_ip(request)`

保持以下副作用：

- `patch_model_route()` 写入 `SystemSetting` 后 `db.commit()`，再写 audit，再 `settings.invalidate()`。
- `edit_model_route()` 写入 `SystemSetting` 后 `db.commit()`，再写 audit，再 `settings.invalidate()`。
- `db_key == "sticker_describe"` 时继续尝试清理 `_get_image_summary_route._cache`。
- 任何 `api_key` 仍只返回 `api_key_configured`，不暴露原值。

- [x] **步骤 5：迁移 route test、resolved、available 和本地组件**

迁移以下符号：

- `_TINY_TEST_PNG`
- `test_model_route`
- `get_resolved_route`
- `list_available_models`
- `_test_nli_contradiction`
- `test_local_component`
- `warmup_local_component`

约束：

- `test_model_route()` 保持 `async def`。
- chat route 分支继续 `await client.chat_completion(...)`。
- classifier / vision route 分支继续使用 `await asyncio.to_thread(call_model_route, ...)`。
- `list_available_models()` 继续在同步 endpoint 内使用 `urllib.request`，本阶段不做异步化重构。
- 本地组件 import 继续留在函数体内，避免模块 import 阶段加载模型。

- [x] **步骤 6：迁移 TimingGate 稳定性测试和模型健康检查**

迁移以下符号：

- `TimingGateStabilityRequest`
- `timing_gate_stability_test`
- `model_health_check`

约束：

- `timing_gate_stability_test()` 保持当前 response shape：`dry_run`、`cases`、
  `overall_parse_error_count`、`overall_parse_error_ratio`。
- `model_health_check()` 继续使用 `aiohttp.ClientSession()` 探测 `new_api`、`classifier`、
  `image_summary` 三类 endpoint。
- 不新增 `asyncio.run()` 或 `run_awaitable_sync()`。

- [x] **步骤 7：语法检查新模块**

运行：

```bash
python -m compileall api/admin/model_routes.py -q
```

预期：无输出，退出码为 0。

## 任务 3：集成父模块 `api/admin_routes.py`

**文件：**
- 修改：`api/admin_routes.py`

- [x] **步骤 1：导入并 include model router**

在已拆模块 import 区新增：

```python
from api.admin.model_routes import (
    ChatModelTestRequest,
    ModelCatalogPatch,
    ModelRouteEditBody,
    ModelRoutePatch,
    ProviderUpdateBody,
    TimingGateStabilityRequest,
    _ALLOWED_TIERS,
    _CHAT_ROUTES,
    _CLASSIFIER_ROUTE_KEYS,
    _ROUTE_ALIAS,
    _ROUTE_SETTING_MAP,
    _STAGE_META,
    _TINY_TEST_PNG,
    _redact,
    _resolve_route_key,
    _resolve_route_value,
    _test_nli_contradiction,
    chat_model_test,
    edit_model_route,
    get_model_catalog,
    get_model_catalog_v2,
    get_model_routes,
    get_resolved_route,
    get_route_references,
    list_available_models,
    list_model_providers,
    model_health_check,
    models_status,
    patch_model_catalog,
    patch_model_route,
    refresh_model_catalog,
    router as model_router,
    test_local_component,
    test_model_route,
    timing_gate_stability_test,
    update_model_provider,
    warmup_local_component,
)
```

在 include 区新增：

```python
router.include_router(model_router)
```

推荐放在 `tool_router` 之后、`trace_router` 之前。模型路由不依赖 include 顺序，但这样保持当前已拆模块集中。

- [x] **步骤 2：删除父模块本地模型管理区块**

删除 `api/admin_routes.py` 中以下本地实现：

- `# Model status / tests` 标题到 `chat_model_test()` 结束。
- `# ── Model Catalog & Routes ──` 到 `warmup_local_component()` 结束。
- `TimingGateStabilityRequest` 和 `timing_gate_stability_test()`。
- `# ── Model Health Check ──` 到 `model_health_check()` 结束。

不要删除：

- `/model-replies`
- `/db/backup`
- `/db/vacuum`
- `/settings/*`
- `# Reply 手动测试 / A-B 评估` 之后的任何代码。

- [x] **步骤 3：保留父模块其他子域依赖**

确认 `api/admin_routes.py` 中这些符号仍保留：

- `_safe_dict`
- `_iso`
- `_audit`
- `_audit_request`
- `_client_ip`
- `ChatLog`
- `ConversationTurn`
- `User`
- `SystemSetting`
- `AdminAuditLog`
- `row_to_dict`
- `asyncio`

理由：

- `/model-replies` 仍使用 `_safe_dict()`、`_iso()`、`ChatLog`。
- `/settings/*` 仍使用 `SystemSetting`、`_audit()`、`_client_ip()`。
- Reply / Eval 区块仍使用 `row_to_dict`、`AdminAuditLog`、`asyncio`。

- [x] **步骤 4：语法检查父模块和新模块**

运行：

```bash
python -m compileall api/admin_routes.py api/admin/model_routes.py -q
```

预期：无输出，退出码为 0。

## 任务 4：跑绿灯和模型行为回归

**文件：**
- 验证：`tests/test_admin_model_routes_split.py`
- 验证：`tests/test_admin_api.py`

- [x] **步骤 1：运行 split 绿灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_model_routes_split.py -q
```

预期：全部通过。

- [x] **步骤 2：运行模型行为回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_api.py::TestModelCatalog \
  tests/test_admin_api.py::TestModelRoutes \
  tests/test_admin_api.py::TestModelHealthCheck \
  tests/test_admin_api.py::TestModelRouteV2 \
  -q
```

预期：全部通过。若出现真实网络或本地模型加载，先检查是否遗漏已有 monkeypatch，不要通过增加超时解决。

- [x] **步骤 3：运行拆分兼容回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_model_routes_split.py \
  tests/test_admin_tool_routes_split.py \
  tests/test_admin_sticker_routes_split.py \
  tests/test_admin_group_memory_routes_split.py \
  tests/test_admin_observability_routes_split.py \
  tests/test_admin_db_browser.py \
  -q
```

预期：全部通过。

- [x] **步骤 4：运行鉴权与 asyncio 策略回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_api.py::TestAuth \
  tests/test_asyncio_run_policy.py \
  tests/test_admin_model_routes_split.py::test_admin_model_routes_do_not_import_parent_admin_routes_or_sync_awaitable \
  -q
```

预期：全部通过。

## 任务 5：静态检查、行数检查和实现提交

**文件：**
- 检查：`api/admin_routes.py`
- 检查：`api/admin/model_routes.py`
- 检查：`tests/test_admin_model_routes_split.py`

- [x] **步骤 1：运行静态检查**

运行：

```bash
python -m compileall api/admin_routes.py api/admin/model_routes.py -q
git diff --check -- api/admin_routes.py api/admin/model_routes.py tests/test_admin_model_routes_split.py .Codex/plans/admin-model-routes-split.md
rg -n "from api\.admin_routes|import api\.admin_routes|asyncio\.run|run_awaitable_sync" api/admin/model_routes.py
```

预期：

- `compileall` 无输出。
- `git diff --check` 无输出。
- `rg` 无输出，退出码为 1。

- [x] **步骤 2：检查行数**

运行：

```bash
wc -l api/admin_routes.py api/admin/model_routes.py tests/test_admin_model_routes_split.py
```

预期：

- `api/admin_routes.py` 从 3761 行降到约 2650 行。
- `api/admin/model_routes.py` 约 1100-1200 行。
- `tests/test_admin_model_routes_split.py` 约 140 行。

- [x] **步骤 3：运行全量测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

预期：0 failures。

- [x] **步骤 4：暂存实现文件**

运行：

```bash
git add api/admin_routes.py api/admin/model_routes.py tests/test_admin_model_routes_split.py
git diff --cached --name-only
git diff --cached --check -- api/admin_routes.py api/admin/model_routes.py tests/test_admin_model_routes_split.py
```

预期暂存区只包含：

- `api/admin_routes.py`
- `api/admin/model_routes.py`
- `tests/test_admin_model_routes_split.py`

- [x] **步骤 5：提交实现阶段**

运行：

```bash
git commit -m "refactor(管理端): 拆分模型路由"
```

## 任务 6：文档收口

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/admin-model-routes-split.md`

- [x] **步骤 1：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」下更新：

- `api/admin_routes.py` 行数。
- 新增第六刀进展：Admin Models 路由已拆到 `api/admin/model_routes.py`。
- 说明 `/model-replies` 仍保留在父模块，作为后续 Reply / Observability 边界处理。
- 说明旧 `api.admin_routes` 继续 re-export 迁移符号。

- [x] **步骤 2：更新 `docs/plan_walkthrough.md`**

追加 `## 2026-06-21 Admin Models 路由拆分`，记录：

- 设计文档路径。
- 实现计划路径。
- 阶段提交 hash。
- 红灯、绿灯、模型行为回归、拆分兼容回归、asyncio 策略、静态检查、行数和全量测试结果。
- 执行约束：不拆 `/model-replies`、不改 Prompt Runtime 模板、不新增 `asyncio.run()`。

- [x] **步骤 3：更新本计划**

勾选已执行步骤，补充：

- 红灯测试输出。
- 绿灯测试输出。
- 定向回归输出。
- 全量测试输出。
- 实现提交 hash。

- [x] **步骤 4：验证文档收口**

运行：

```bash
git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/admin-model-routes-split.md
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

预期：`git diff --check` 无输出，全量测试 0 failures。

- [x] **步骤 5：暂存并提交文档收口**

运行：

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/admin-model-routes-split.md
git diff --cached --name-only
git diff --cached --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/admin-model-routes-split.md
git commit -m "docs(计划): 收口模型路由拆分"
```

## 完成定义

- [x] `api/admin_routes.py` 中不再定义迁移清单内的 19 个模型管理 endpoint。
- [x] `api/admin/model_routes.py` 不反向导入 `api.admin_routes`。
- [x] `/model-replies` 仍由 `api.admin_routes` 提供。
- [x] `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 仍影响模型管理路由。
- [x] `api.admin_routes` legacy re-export 与 `api.admin.model_routes` 对象 identity 一致。
- [x] 模型行为回归、拆分兼容回归、asyncio 策略回归、静态检查和全量测试均通过。
- [x] 阶段实现已提交，文档收口将在本次提交中归档。

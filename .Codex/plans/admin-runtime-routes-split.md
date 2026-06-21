# Admin Runtime / Overview 路由拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `api/admin_routes.py` 中 Runtime / Overview 管理端 HTTP 层拆到 `api/admin/runtime_routes.py`，保持路径、响应结构、鉴权 monkeypatch、Group Memory 路由顺序、TimingGate 手测协程边界和旧导入路径不变。

**架构：** `api.admin_routes` 继续作为 `/api/v1/admin` 顶层聚合 router，新建 `api.admin.runtime_routes.router` 并由父 router include。新模块只承接 overview、groups、TimingGate events 和 TimingGate 手测 HTTP 编排；数据库模型、TimingRuntime、Prompt 模板目录健康检查和 Sticker helper 继续复用既有模块。

**技术栈：** Python 3.12、FastAPI、Pydantic、SQLAlchemy、pytest、项目既有 `api.admin.common` 鉴权 helper。

---

## 当前状态（2026-06-21）

- [x] 已核对 `docs/todo.md` 剩余硬项：`api/admin_routes.py` 1390 行、`api/routes.py` 2822 行。
- [x] 已读取 Runtime / Overview 源码区块：`api/admin_routes.py:464-840`。
- [x] 已核对现有行为测试：`tests/test_admin_api.py:925-1035`。
- [x] 已核对现有 split 测试模式：`tests/test_admin_group_memory_routes_split.py`、
  `tests/test_admin_tool_routes_split.py`、`tests/test_admin_eval_routes_split.py`。
- [x] 已写设计文档：`docs/superpowers/specs/2026-06-21-admin-runtime-routes-split-design.md`。
- [x] 设计提交：`1c2745c docs(管理端): 设计运行态路由拆分`。
- [x] 计划提交：`0ac3fa7 docs(计划): 记录运行态路由拆分计划`。
- [x] 红灯测试提交：`d394766 test(管理端): 锁定运行态路由拆分契约`。
- [x] 实现提交：`d6a05bf refactor(管理端): 拆分运行态路由`。
- [x] 已验证 split 绿灯：`9 passed, 21 warnings in 1.14s`。
- [x] 已验证管理端行为 / 顺序 / asyncio 策略回归：
  `86 passed, 21 warnings in 8.91s`。
- [x] 已验证全量回归：`1560 passed, 6 skipped, 139 warnings in 111.58s`。
- [x] 已完成文档收口验证：文档关键词扫描无命中，文档 diff 空白检查无输出。
- [x] 行数结果：`api/admin_routes.py` 1009 行，`api/admin/runtime_routes.py` 462 行，
  `tests/test_admin_runtime_routes_split.py` 143 行。

## 子 agent 分工约定

主线程负责最终编辑、验证和提交。可用子 agent 任务必须互不覆盖写入范围：

- **Explorer A：迁移边界。** 只读检查 Runtime / Overview 区块、依赖 import、helper 共享和 route order 风险，输出文件名与行号。
- **Explorer B：测试契约。** 只读检查现有行为测试与 split 测试模式，输出新增测试建议、红灯预期和 pytest 命令。
- **Worker A：测试文件。** 只允许创建或修改 `tests/test_admin_runtime_routes_split.py`。
- **Worker B：生产代码。** 只允许创建 `api/admin/runtime_routes.py` 并修改 `api/admin_routes.py`。必须在红灯测试验证后开始。
- **Reviewer：验证审查。** 只读检查 diff、route order、反向导入、asyncio 策略、行数和测试输出。

接口约定：

- `api/admin/runtime_routes.py` 导出 `router`，不得带 `/api/v1/admin` 前缀。
- `router` 不使用 `prefix="/groups"` 或 `prefix="/timing-gate"`，保持 endpoint path 与旧父模块声明一致。
- 新模块使用 `api.admin.common.verify_admin`，不得从 `api.admin_routes` 导入任何符号。
- `api.admin_routes` 必须 re-export 迁移后的 request model、helper 和 endpoint 函数。
- `group_memory_router` 必须继续早于 `runtime_router` include。
- `timing_gate_test()` 必须保持 `async def` 和 `await asyncio.to_thread(...)`。
- 生产代码不得新增 `asyncio.run()`，不得新增 `run_awaitable_sync`，不得新增同步函数包装 awaitable。
- 不改变 Prompt Runtime 模板、`enriched_query`、工具 usage 文档或 Prompt Runtime 输入。

## 文件职责

- 创建：`tests/test_admin_runtime_routes_split.py`
  - 锁定 5 个 Runtime / Overview route 的 endpoint module 为 `api.admin.runtime_routes`。
  - 锁定 `api.admin_routes` 对迁移 endpoint、request model 和 Runtime 专属 helper 的旧导入兼容。
  - 锁定 `api.admin_routes.NANOBOT_ADMIN_TOKEN` monkeypatch 对新模块路由仍生效。
  - 锁定迁移路由未重复注册。
  - 锁定 `/groups` 静态路由先于 `/groups/{group_id:path}` catch-all。
  - 锁定 Group Memory 子路由先于 `/groups/{group_id:path}` catch-all。
  - 锁定 `timing_gate_test()` 仍是 coroutine function。
  - 锁定 `TimingGateTestRequest.repeats` 在新模块路径下仍限制为 1 到 5。
  - 静态扫描新模块没有反向导入父模块、没有 `asyncio.run`、没有 `run_awaitable_sync`。
- 创建：`api/admin/runtime_routes.py`
  - 定义 `router = APIRouter(tags=["admin-runtime"])`。
  - 持有 `TimingGateTestRequest`。
  - 持有 `_safe_dict()`、`_iso()`、`_age_seconds()`、`_raw_group_id()`、
    `_group_session_id()`、`_group_stream_id()`、`_block_dict()`、
    `_timing_meta()`、`_timing_event_dict()`、`_timing_stats()`、`_runtime_snapshot()`。
  - 持有 `overview()`、`list_groups()`、`group_detail()`、`timing_gate_events()`、
    `timing_gate_test()`。
- 修改：`api/admin_routes.py`
  - 导入并 include `runtime_router`，位置必须晚于 `group_memory_router`。
  - re-export 迁移 endpoint、request model 和 Runtime 专属 helper。
  - 删除本地 `# Observability / Runtime` 区块。
  - 保留父模块后续区块仍使用的 `_safe_dict()`、`_iso()`、`_raw_group_id()`、
    `_group_stream_id()`、`_block_dict()` 等 helper，删除确认不再使用的 import。
- 实现收口阶段修改：`docs/todo.md`、`docs/plan_walkthrough.md`、
  `.Codex/plans/admin-runtime-routes-split.md`。

## 任务 1：补 Runtime / Overview 路由拆分红灯测试

**文件：**
- 创建：`tests/test_admin_runtime_routes_split.py`

- [x] **步骤 1：创建 split 路由测试文件**

创建 `tests/test_admin_runtime_routes_split.py`：

```python
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError


_ADMIN_RUNTIME_ROUTE_SIGNATURES = (
    ("GET", "/api/v1/admin/overview"),
    ("GET", "/api/v1/admin/groups"),
    ("GET", "/api/v1/admin/groups/{group_id:path}"),
    ("GET", "/api/v1/admin/timing-gate/events"),
    ("POST", "/api/v1/admin/timing-gate/test"),
)


_RUNTIME_ROUTE_EXPORTS = (
    "TimingGateTestRequest",
    "_timing_meta",
    "_timing_event_dict",
    "_timing_stats",
    "_runtime_snapshot",
    "overview",
    "list_groups",
    "group_detail",
    "timing_gate_events",
    "timing_gate_test",
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


def test_admin_runtime_routes_are_registered_from_split_module():
    for method, path in _ADMIN_RUNTIME_ROUTE_SIGNATURES:
        routes = _admin_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.runtime_routes"}


def test_legacy_admin_routes_runtime_imports_still_work():
    from api import admin_routes
    from api.admin import runtime_routes

    for name in _RUNTIME_ROUTE_EXPORTS:
        assert getattr(admin_routes, name) is getattr(runtime_routes, name)

    assert admin_routes.TimingGateTestRequest(context="测试", repeats=1).repeats == 1


def test_split_runtime_routes_use_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/overview",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/overview",
        headers={"Authorization": "Bearer test-token"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_admin_runtime_routes_are_not_registered_twice():
    for method, path in _ADMIN_RUNTIME_ROUTE_SIGNATURES:
        assert len(_admin_routes_for(path, method)) == 1, f"{method} {path}"


def test_group_memory_routes_still_precede_group_detail_catchall():
    route_paths = [path for path, _route in _admin_route_entries()]

    detail_index = route_paths.index("/api/v1/admin/groups/{group_id:path}")
    list_index = route_paths.index("/api/v1/admin/groups/{group_id:path}/memories")
    extract_index = route_paths.index("/api/v1/admin/groups/{group_id:path}/memories/extract")

    assert list_index < detail_index
    assert extract_index < detail_index


def test_admin_runtime_static_group_routes_precede_group_detail_catchall():
    route_paths = [path for path, _route in _admin_route_entries()]

    groups_index = route_paths.index("/api/v1/admin/groups")
    detail_index = route_paths.index("/api/v1/admin/groups/{group_id:path}")

    assert groups_index < detail_index


def test_admin_runtime_async_boundaries_remain_coroutines():
    from api.admin import runtime_routes

    assert inspect.iscoroutinefunction(runtime_routes.timing_gate_test)


def test_timing_gate_test_request_repeats_cap_is_preserved_via_split_import():
    from api.admin.runtime_routes import TimingGateTestRequest

    with pytest.raises(ValidationError):
        TimingGateTestRequest(context="测试", repeats=6)


def test_admin_runtime_routes_do_not_import_parent_admin_routes_or_sync_awaitable():
    path = Path("api/admin/runtime_routes.py")

    assert path.exists()
    source = path.read_text(encoding="utf-8")

    assert "from api.admin_routes" not in source
    assert "import api.admin_routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
```

- [x] **步骤 2：运行测试验证红灯**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_admin_runtime_routes_split.py
```

预期：FAIL。失败点应来自 endpoint module 仍是 `api.admin_routes`、`api.admin.runtime_routes`
尚不存在或 `api/admin/runtime_routes.py` 文件尚不存在。

实际结果：`5 failed, 4 passed, 21 warnings in 6.57s`。

- [x] **步骤 3：提交红灯测试**

```bash
git add tests/test_admin_runtime_routes_split.py
git commit -m "test(管理端): 锁定运行态路由拆分契约"
```

实际提交：`d394766 test(管理端): 锁定运行态路由拆分契约`。

## 任务 2：迁移 Runtime / Overview 路由到新模块

**文件：**
- 创建：`api/admin/runtime_routes.py`
- 修改：`api/admin_routes.py`

- [x] **步骤 1：创建 `api/admin/runtime_routes.py`**

从 `api/admin_routes.py` 迁移 Runtime / Overview 区块，模块骨架如下：

```python
"""Admin Runtime / Overview 路由。"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.admin.common import verify_admin
from api.admin.sticker_routes import _sticker_dict
from core.database import (
    ChatLog,
    ChatStreamConfig,
    StickerMemory,
    User,
    UserBlockRule,
    get_db,
)

router = APIRouter(tags=["admin-runtime"])
```

迁移时保持旧函数签名和 response shape。`timing_gate_test()` 的核心循环必须保持：

```python
for idx in range(body.repeats):
    t0 = time.time()
    result = await asyncio.to_thread(gate.judge, context)
    latency_ms = int((time.time() - t0) * 1000)
```

- [x] **步骤 2：修改父模块聚合和 re-export**

在 `api/admin_routes.py` 中导入：

```python
from api.admin.runtime_routes import (
    TimingGateTestRequest,
    _runtime_snapshot,
    _timing_event_dict,
    _timing_meta,
    _timing_stats,
    group_detail,
    list_groups,
    overview,
    router as runtime_router,
    timing_gate_events,
    timing_gate_test,
)
```

include 顺序：

```python
router.include_router(sticker_router)
router.include_router(group_memory_router)
router.include_router(runtime_router)
router.include_router(tool_router)
```

删除父模块本地 `# Observability / Runtime` 区块。若父模块其他区块仍引用同名 helper，
保留父模块本地 helper，不要把 `_safe_dict()`、`_iso()`、`_raw_group_id()`、
`_group_stream_id()` 或 `_block_dict()` 改成 Runtime re-export。

- [x] **步骤 3：运行 split 绿灯**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_admin_runtime_routes_split.py
```

预期：PASS，所有 Runtime / Overview route endpoint module 均为 `api.admin.runtime_routes`。

实际结果：`9 passed, 21 warnings in 1.14s`。

- [x] **步骤 4：运行行为回归**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_admin_api.py \
  tests/test_admin_group_memory_routes_split.py \
  tests/test_asyncio_run_policy.py
```

预期：PASS。

实际结果：`86 passed, 21 warnings in 8.91s`。

- [x] **步骤 5：静态验证**

运行：

```bash
python -B -m compileall api/admin_routes.py api/admin/runtime_routes.py
git diff --check
rg -n "from api\\.admin_routes|import api\\.admin_routes|asyncio\\.run|run_awaitable_sync" api/admin/runtime_routes.py
```

预期：`compileall` 和 `git diff --check` 退出码为 0；`rg` 无命中，退出码为 1。

实际结果：`compileall` 无输出，`git diff --check` 无输出，`rg` 无命中且退出码为 1。

- [x] **步骤 6：提交实现**

```bash
git add api/admin_routes.py api/admin/runtime_routes.py
git commit -m "refactor(管理端): 拆分运行态路由"
```

实际提交：`d6a05bf refactor(管理端): 拆分运行态路由`。

## 任务 3：文档收口与最终验证

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/admin-runtime-routes-split.md`

- [x] **步骤 1：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」下追加本阶段进展：

```markdown
  - 进展：`api/admin_routes.py` 第九刀已拆出 Runtime / Overview 管理端路由到
    `api/admin/runtime_routes.py`；旧 `api.admin_routes` 继续 re-export 迁移后的
    request model、helper 和 endpoint，保留 HTTP 路径、admin token monkeypatch、
    Group Memory 子路由先于 `/groups/{group_id:path}` catch-all 的顺序、overview /
    groups / TimingGate events response shape 和 `timing_gate_test()` 协程边界。
```

- [x] **步骤 2：更新 `docs/plan_walkthrough.md`**

追加 `2026-06-21 Admin Runtime / Overview 路由拆分` 小节，记录设计提交、计划提交、红灯、
实现提交、验证结果、行数变化和执行边界。

- [x] **步骤 3：勾选本计划当前任务状态**

将已经完成的步骤由 `- [ ]` 改为 `- [x]`，并补充实际验证输出和提交 SHA。

- [x] **步骤 4：运行最终验证**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_admin_runtime_routes_split.py
python -B -m pytest -q -p no:cacheprovider \
  tests/test_admin_api.py \
  tests/test_admin_group_memory_routes_split.py \
  tests/test_asyncio_run_policy.py
python -B -m compileall api/admin_routes.py api/admin/runtime_routes.py
git diff --check
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：全部通过。若全量测试失败，先按失败信息修复并重新运行相关测试，不能只更新文档。

实际结果：

- 文档禁用词扫描：无命中，退出码为 1。
- 文档空白检查：
  `git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/admin-runtime-routes-split.md`
  无输出，退出码为 0。
- Runtime split 专项：`9 passed, 21 warnings in 1.15s`。
- 管理端行为 / 顺序 / asyncio 策略回归：`86 passed, 21 warnings in 8.88s`。
- 静态编译：`python -B -m compileall api/admin_routes.py api/admin/runtime_routes.py`
  无输出，退出码为 0。
- 行数检查：`api/admin_routes.py` 1009 行，`api/admin/runtime_routes.py` 462 行，
  `tests/test_admin_runtime_routes_split.py` 143 行。
- 全量回归：`1560 passed, 6 skipped, 139 warnings in 111.58s`。

- [x] **步骤 5：提交文档收口**

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/admin-runtime-routes-split.md
git commit -m "docs(计划): 收口运行态路由拆分"
```

实际提交：本文件随 `docs(计划): 收口运行态路由拆分` 提交。

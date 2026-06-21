# Admin Sticker 路由拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

## 执行结果摘要（2026-06-21）

- 状态：实现、定向验证、文档更新和全量验证已完成。
- 设计提交：`ab17cb4 docs(管理端): 设计贴纸路由拆分`。
- 计划提交：`3162055 docs(计划): 记录贴纸路由拆分计划`。
- 实现提交：`26f6112 refactor(管理端): 拆分贴纸管理路由`。
- 结果：新增 `api/admin/sticker_routes.py`，迁移 Sticker / Generated Images
  管理路由、request model、`_sticker_dict()` 和近重复扫描锁；`api.admin_routes`
  include 新 router，并 re-export 旧符号保持兼容；`api/admin_routes.py` 从
  5535 行降至 4979 行，新模块为 614 行。
- 红灯：新增 split 目标测试 ->
  `2 failed, 1 warning`；失败点为 endpoint module 仍来自 `api.admin_routes`，
  且 `api.admin.sticker_routes` 尚不存在。
- 绿灯：新增 split 测试 -> `4 passed, 21 warnings in 1.30s`。
- sticker 行为回归：新增 split 测试、`TestGeneratedImagesAdmin` 和
  `TestStickerCRUD` -> `19 passed, 21 warnings in 2.00s`。
- 鉴权与 asyncio 策略回归：`TestAuth` 与 `tests/test_asyncio_run_policy.py` ->
  `9 passed, 1 warning in 2.33s`。
- WebUI duplicate 静态回归：`tests/test_webui_admin_redesign.py -k "sticker_duplicate"` ->
  `2 passed, 21 deselected, 1 warning in 0.44s`。
- 静态检查：`compileall`、行数检查、目标文件 `git diff --check`、facade 符号同一性和
  新模块反向导入扫描均通过。
- 全量：`python -m pytest tests/ -v` ->
  `1511 passed, 6 skipped, 139 warnings in 109.17s`。

**目标：** 将 `api/admin_routes.py` 中 Sticker / Generated Images 管理路由拆到 `api/admin/sticker_routes.py`，保持 HTTP 路径、响应契约、鉴权 monkeypatch、旧导入路径和审计动作不变。

**架构：** `api.admin_routes` 继续作为 `/api/v1/admin` 顶层 router，并 include 新的 `api.admin.sticker_routes.router`。新模块承接 Sticker CRUD、生成图片管理、重复贴纸治理、预览重试、批量删除和相关 request model；`api.admin_routes` 通过 re-export 保留旧符号，并继续让 `group_detail()` 调用同名 `_sticker_dict()`。

**技术栈：** Python 3.12、FastAPI、Pydantic、SQLAlchemy、pytest、项目既有 `api.admin.common` 鉴权与审计 helper。

---

## 子 agent 分工约定

- **Agent A：测试契约。** 只修改 `tests/test_admin_sticker_routes_split.py`，不改生产代码。输出红灯测试结果和失败原因。
- **Agent B：新模块草稿。** 只创建 `api/admin/sticker_routes.py`，不改 `api/admin_routes.py`。按本计划的接口清单迁移实现，内部使用 `api.admin.common.verify_admin` 和 `audit_request`。
- **Agent C：主模块集成。** 只修改 `api/admin_routes.py`。负责 include `sticker_router`、re-export 旧符号、删除旧 route 装饰器实现、保留 `_safe_json()` 和 `_iso()` 在旧模块内的其他调用。
- **Agent D：验证审查。** 只读检查 `git diff`、路由注册、行数、`asyncio.run` 策略和测试输出。不得修改代码。

接口约定：

- 新模块必须导出 `router`，且 `router = APIRouter(tags=["admin-sticker"])`，不要带 `/api/v1/admin` 前缀。
- 新模块必须导出所有迁移 request model、endpoint 函数和 `_sticker_dict()`。
- 新模块内部定义自己的 `_safe_json()` 和 `_iso()`。旧模块的 `_safe_json()`、`_iso()` 继续保留给其他 admin 功能使用。
- 新模块不得从 `api.admin_routes` 导入 `verify_admin`、`_audit_request`、`router` 或 `logger`，避免循环依赖。
- 生产代码不得新增 `asyncio.run()`，不得新增同步函数包装 awaitable。

## 文件职责

- 创建：`tests/test_admin_sticker_routes_split.py`
  - 锁定关键路由的 endpoint module 已迁移到 `api.admin.sticker_routes`。
  - 锁定 `api.admin_routes` 对 request model、helper 和 endpoint 函数的旧导入兼容。
  - 锁定新模块仍使用旧 token monkeypatch 契约。
  - 锁定关键路由未重复注册。
- 创建：`api/admin/sticker_routes.py`
  - 定义 `router = APIRouter(tags=["admin-sticker"])`。
  - 持有 Sticker / Generated Images request model。
  - 持有 `_sticker_dict()`、`_NEAR_DUP_SCAN_LOCK` 和迁移路由实现。
  - 从 `api.admin.common` 导入 `verify_admin`、`audit_request`。
- 修改：`api/admin_routes.py`
  - include `sticker_router`。
  - 从 `api.admin.sticker_routes` 导入并 re-export 迁移符号。
  - 删除本地 Sticker / Generated Images route 装饰器实现。
  - 删除本地迁移 request model 和 `_sticker_dict()`，但保留 `_safe_json()` 和 `_iso()`。
- 修改：`docs/todo.md`
  - 记录 `api/admin_routes.py` Sticker 第一刀拆分进展和拆分后的行数。
- 修改：`docs/plan_walkthrough.md`
  - 追加本阶段执行记录、提交号和验证结果。
- 修改：`.Codex/plans/admin-sticker-routes-split.md`
  - 实现完成后勾选已执行步骤并记录红灯、绿灯、定向回归和全量回归结果。

## 任务 1：补 Sticker 路由拆分红灯测试

**文件：**
- 创建：`tests/test_admin_sticker_routes_split.py`

- [x] **步骤 1：创建 split 路由测试文件**

创建 `tests/test_admin_sticker_routes_split.py`：

```python
from __future__ import annotations


def _admin_routes_for(path: str):
    from server import app

    return [
        route
        for route in app.routes
        if getattr(route, "path", "") == path
    ]


def test_admin_sticker_routes_are_registered_from_split_module():
    expected = {
        "/api/v1/admin/stickers",
        "/api/v1/admin/generated-images",
        "/api/v1/admin/generated-images/{image_id}/image",
        "/api/v1/admin/stickers/duplicate-groups",
        "/api/v1/admin/stickers/near-duplicate-candidates",
        "/api/v1/admin/stickers/near-duplicate/scan",
        "/api/v1/admin/stickers/phash/backfill",
        "/api/v1/admin/stickers/{sticker_id:int}",
        "/api/v1/admin/stickers/{sticker_id}/preview/retry",
        "/api/v1/admin/stickers/batch-delete",
    }

    for path in expected:
        routes = _admin_routes_for(path)
        assert routes, f"missing route: {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.admin.sticker_routes"}


def test_legacy_admin_routes_sticker_imports_still_work():
    from api import admin_routes
    from api.admin import sticker_routes

    names = [
        "StickerCreate",
        "StickerUpdate",
        "GeneratedImageCreate",
        "NearDuplicateAction",
        "SetCanonicalBody",
        "MarkDuplicateBody",
        "_sticker_dict",
        "create_sticker",
        "list_stickers",
        "list_generated_images",
        "create_generated_image",
        "generated_image_file",
        "sticker_duplicate_groups",
        "get_sticker",
        "update_sticker",
        "enable_sticker",
        "disable_sticker",
        "preview_sticker",
        "redescribe_sticker",
        "retry_preview",
        "stickers_dedupe_backfill",
        "list_near_duplicate_candidates",
        "scan_near_duplicates_endpoint",
        "backfill_phash_endpoint",
        "update_near_duplicate_candidate",
        "sticker_set_canonical",
        "sticker_mark_duplicate",
        "batch_delete_stickers",
        "delete_sticker",
    ]

    for name in names:
        assert getattr(admin_routes, name) is getattr(sticker_routes, name)

    assert admin_routes.StickerCreate(file_ref="http://example.com/a.png").file_ref == "http://example.com/a.png"


def test_split_sticker_routes_use_legacy_admin_token_monkeypatch(client, monkeypatch):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "split-token")

    ok = client.get(
        "/api/v1/admin/stickers",
        headers={"Authorization": "Bearer split-token"},
    )
    wrong = client.get(
        "/api/v1/admin/stickers",
        headers={"Authorization": "Bearer test-token"},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_admin_sticker_routes_are_not_registered_twice():
    expected = {
        "/api/v1/admin/stickers",
        "/api/v1/admin/generated-images",
        "/api/v1/admin/generated-images/{image_id}/image",
        "/api/v1/admin/stickers/duplicate-groups",
        "/api/v1/admin/stickers/near-duplicate-candidates",
        "/api/v1/admin/stickers/near-duplicate/scan",
        "/api/v1/admin/stickers/phash/backfill",
        "/api/v1/admin/stickers/{sticker_id:int}",
        "/api/v1/admin/stickers/{sticker_id}/preview/retry",
        "/api/v1/admin/stickers/batch-delete",
    }

    for path in expected:
        assert len(_admin_routes_for(path)) == 1, path
```

- [x] **步骤 2：运行红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_sticker_routes_split.py::test_admin_sticker_routes_are_registered_from_split_module \
  tests/test_admin_sticker_routes_split.py::test_legacy_admin_routes_sticker_imports_still_work \
  -v
```

预期：

```text
FAILED tests/test_admin_sticker_routes_split.py::test_admin_sticker_routes_are_registered_from_split_module
FAILED tests/test_admin_sticker_routes_split.py::test_legacy_admin_routes_sticker_imports_still_work
```

允许的失败原因：

- endpoint module 仍是 `api.admin_routes`。
- `ModuleNotFoundError: No module named 'api.admin.sticker_routes'`。

如果生产代码迁移前这两个测试直接通过，先运行 `git status --short` 和 `git log -1 --oneline` 确认当前分支是否已有同名拆分。

## 任务 2：创建 `api.admin.sticker_routes`

**文件：**
- 创建：`api/admin/sticker_routes.py`

- [x] **步骤 1：创建新模块头部和本地 helper**

创建 `api/admin/sticker_routes.py`，头部结构如下：

```python
"""Admin Sticker 与 Generated Images 路由。"""

from __future__ import annotations

import json
import logging
import threading
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.admin.common import audit_request, verify_admin
from core.database import StickerMemory, get_db

logger = logging.getLogger("nanobot.admin")
router = APIRouter(tags=["admin-sticker"])


def _safe_json(raw):
    try:
        return json.loads(raw or "[]")
    except Exception:
        return []


def _iso(v) -> str:
    if not v:
        return ""
    try:
        return v.isoformat()
    except Exception:
        return str(v)
```

- [x] **步骤 2：迁移 request model**

从 `api/admin_routes.py` 剪切到新模块：

```python
class StickerCreate(BaseModel):
    group_id: str = ""
    chat_stream_id: str = ""
    file_ref: str
    sticker_hash: str = ""
    send_code: str = ""
    name: str = ""
    description: str = ""
    tags: list[str] = []
    emotions: list[str] = []
    status: Literal["active", "disabled"] = "active"


class StickerUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    emotions: Optional[list[str]] = None
    status: Optional[Literal["active", "disabled", "deleted"]] = None


class GeneratedImageCreate(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8000)
    size: Literal["1024x1024", "1024x1536", "1536x1024", "auto"] = "1024x1024"
    quality: Literal["low", "medium", "high", "auto"] = "high"
    background: Literal["auto", "transparent", "opaque"] = "auto"
```

从原位置删除这三个 class。保留 `BlockRuleCreate` 及后续非 sticker 模型。

- [x] **步骤 3：迁移 `_sticker_dict()`**

从 `api/admin_routes.py` 剪切 `_sticker_dict(r: StickerMemory) -> dict` 到新模块，函数体保持原样。新函数使用新模块内的 `_safe_json()`。

不要从 `api.admin_routes` 导入 `_safe_json()`，旧模块的 `_safe_json()` 仍留给日志 viewer 使用。

- [x] **步骤 4：迁移 Sticker / Generated Images 路由实现**

从 `api/admin_routes.py` 剪切以下 route block 到新模块，保持函数名、路径、参数和返回结构不变：

- `create_sticker`
- `list_stickers`
- `list_generated_images`
- `create_generated_image`
- `generated_image_file`
- `sticker_duplicate_groups`
- `get_sticker`
- `update_sticker`
- `enable_sticker`
- `disable_sticker`
- `preview_sticker`
- `redescribe_sticker`
- `retry_preview`
- `stickers_dedupe_backfill`
- `list_near_duplicate_candidates`
- `_NEAR_DUP_SCAN_LOCK`
- `scan_near_duplicates_endpoint`
- `backfill_phash_endpoint`
- `NearDuplicateAction`
- `update_near_duplicate_candidate`
- `SetCanonicalBody`
- `sticker_set_canonical`
- `MarkDuplicateBody`
- `sticker_mark_duplicate`
- `batch_delete_stickers`
- `delete_sticker`

迁移时做以下机械替换：

```python
_audit_request(
```

替换为：

```python
audit_request(
```

不要改变审计 action 字符串，例如 `create_sticker`、`sticker.near_duplicate.scan`、`soft_delete_sticker`。

- [x] **步骤 5：检查新模块没有循环依赖**

运行：

```bash
python - <<'PY'
from pathlib import Path
text = Path("api/admin/sticker_routes.py").read_text(encoding="utf-8")
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

## 任务 3：集成 `api.admin_routes` facade

**文件：**
- 修改：`api/admin_routes.py`

- [x] **步骤 1：导入 sticker router 和 re-export 符号**

在既有 admin 子路由 import 附近添加：

```python
from api.admin.sticker_routes import (
    GeneratedImageCreate,
    MarkDuplicateBody,
    NearDuplicateAction,
    SetCanonicalBody,
    StickerCreate,
    StickerUpdate,
    _sticker_dict,
    backfill_phash_endpoint,
    batch_delete_stickers,
    create_generated_image,
    create_sticker,
    delete_sticker,
    disable_sticker,
    enable_sticker,
    generated_image_file,
    get_sticker,
    list_generated_images,
    list_near_duplicate_candidates,
    list_stickers,
    preview_sticker,
    redescribe_sticker,
    retry_preview,
    router as sticker_router,
    scan_near_duplicates_endpoint,
    sticker_duplicate_groups,
    sticker_mark_duplicate,
    sticker_set_canonical,
    stickers_dedupe_backfill,
    update_near_duplicate_candidate,
    update_sticker,
)
```

在 `router.include_router(session_memory_router)` 后添加：

```python
router.include_router(sticker_router)
```

- [x] **步骤 2：删除旧模块中的迁移实现块**

从 `api/admin_routes.py` 删除以下本地定义：

- `StickerCreate`
- `StickerUpdate`
- `GeneratedImageCreate`
- `_sticker_dict()`
- `create_sticker()`
- `list_stickers()`
- `list_generated_images()`
- `create_generated_image()`
- `generated_image_file()`
- `sticker_duplicate_groups()`
- `get_sticker()`
- `update_sticker()`
- `enable_sticker()`
- `disable_sticker()`
- `preview_sticker()`
- `redescribe_sticker()`
- `retry_preview()`
- `stickers_dedupe_backfill()`
- `list_near_duplicate_candidates()`
- `_NEAR_DUP_SCAN_LOCK`
- `scan_near_duplicates_endpoint()`
- `backfill_phash_endpoint()`
- `NearDuplicateAction`
- `update_near_duplicate_candidate()`
- `SetCanonicalBody`
- `sticker_set_canonical()`
- `MarkDuplicateBody`
- `sticker_mark_duplicate()`
- `batch_delete_stickers()`
- `delete_sticker()`

不要删除：

- `verify_admin()`
- `_audit()` / `_audit_request()`
- `_safe_json()`
- `_iso()`
- `group_detail()`
- `BlockRuleCreate` 及后续非 sticker 模型
- `UserBlockRule CRUD` 及后续非 sticker 路由

- [x] **步骤 3：清理已无用 import**

运行：

```bash
python - <<'PY'
from pathlib import Path
text = Path("api/admin_routes.py").read_text(encoding="utf-8")
for name in ["threading"]:
    if f"import {name}" in text and name not in text.replace(f"import {name}", ""):
        print(name)
PY
```

如果输出 `threading`，从 `api/admin_routes.py` 删除 `import threading`。不要做 ruff 批量清理。

- [x] **步骤 4：确认 `group_detail()` 继续使用 facade helper**

运行：

```bash
python - <<'PY'
from api import admin_routes
from api.admin import sticker_routes

assert admin_routes._sticker_dict is sticker_routes._sticker_dict
assert admin_routes.group_detail.__module__ == "api.admin_routes"
PY
```

预期：无输出，退出码为 0。

## 任务 4：运行红绿测试和定向回归

**文件：**
- 验证：`tests/test_admin_sticker_routes_split.py`
- 验证：`tests/test_admin_api.py`
- 验证：`tests/test_webui_admin_redesign.py`
- 验证：`tests/test_asyncio_run_policy.py`

- [x] **步骤 1：运行 split 绿灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_sticker_routes_split.py -q
```

预期：全部通过。

- [x] **步骤 2：运行 admin sticker 行为回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_sticker_routes_split.py \
  tests/test_admin_api.py::TestGeneratedImagesAdmin \
  tests/test_admin_api.py::TestStickerCRUD \
  -q
```

预期：全部通过。

- [x] **步骤 3：运行鉴权、路由 shadow 和 `asyncio.run` 策略回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_admin_api.py::TestAuth \
  tests/test_webui_admin_redesign.py -k "sticker_duplicate" \
  tests/test_asyncio_run_policy.py \
  -q
```

预期：全部通过。

- [x] **步骤 4：运行静态检查**

运行：

```bash
python -m compileall api/admin_routes.py api/admin/sticker_routes.py -q
wc -l api/admin_routes.py api/admin/sticker_routes.py tests/test_admin_sticker_routes_split.py
git diff --check -- api/admin_routes.py api/admin/sticker_routes.py tests/test_admin_sticker_routes_split.py
```

预期：

- `compileall` 无输出，退出码为 0。
- `api/admin_routes.py` 行数较拆分前明显下降。
- `git diff --check` 无输出，退出码为 0。

## 任务 5：更新进度文档并全量验证

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/admin-sticker-routes-split.md`

- [x] **步骤 1：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」条目下记录：

- `api/admin_routes.py` 已拆出 `api/admin/sticker_routes.py`。
- 记录拆分后的 `wc -l api/admin_routes.py api/admin/sticker_routes.py` 实际行数。
- 不勾选整个「超大文件 >800 行拆分」总项，除非 `api/admin_routes.py` 和 `api/routes.py` 都已低于 800 行。

- [x] **步骤 2：更新 `docs/plan_walkthrough.md`**

追加日期为 `2026-06-21` 的执行记录，包含：

- 设计提交：`ab17cb4 docs(管理端): 设计贴纸路由拆分`。
- 计划提交：填入本计划提交号。
- 实现提交：`26f6112 refactor(管理端): 拆分贴纸管理路由`。
- 红灯测试命令和失败摘要。
- 绿灯测试命令和通过摘要。
- 定向回归、静态检查和全量回归结果。

- [x] **步骤 3：更新本计划勾选状态**

把已完成步骤从 `- [ ]` 改为 `- [x]`，并在文件顶部追加执行结果摘要。摘要包含红灯、绿灯、定向回归、全量回归和提交号。

- [x] **步骤 4：运行全量测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

预期：0 failures。

## 任务 6：提交实现阶段

**文件：**
- 暂存：`tests/test_admin_sticker_routes_split.py`
- 暂存：`api/admin/sticker_routes.py`
- 暂存：`api/admin_routes.py`
- 暂存：`docs/todo.md`
- 暂存：`docs/plan_walkthrough.md`
- 暂存：`.Codex/plans/admin-sticker-routes-split.md`

- [x] **步骤 1：检查工作区**

运行：

```bash
git status --short
```

只处理本阶段目标文件。不要暂存历史脏项、`__pycache__`、`nanobot.db`、`.agents/`、`.codex/` 或截图快照文件。

- [x] **步骤 2：显式暂存目标文件**

运行：

```bash
git add tests/test_admin_sticker_routes_split.py
git add api/admin/sticker_routes.py
git add api/admin_routes.py
git add docs/todo.md
git add docs/plan_walkthrough.md
git add .Codex/plans/admin-sticker-routes-split.md
git diff --cached --name-status
git diff --cached --check
```

预期暂存区只包含上述 6 个文件，`git diff --cached --check` 无输出。

- [x] **步骤 3：提交**

运行：

```bash
git commit -m "refactor(管理端): 拆分贴纸管理路由"
```

提交成功后运行：

```bash
git log -1 --oneline
```

预期最新提交标题为：

```text
refactor(管理端): 拆分贴纸管理路由
```

## 完成标准

- `api/admin/sticker_routes.py` 承接迁移范围内所有 endpoint。
- `api.admin_routes.router` 继续暴露原 `/api/v1/admin/*` HTTP 路径。
- `api.admin_routes` 对迁移 request model、helper 和 endpoint 函数保持旧导入兼容。
- `api.admin.common._current_admin_token()` 的旧 token monkeypatch 契约通过测试覆盖。
- `group_detail()` 的 sticker 序列化仍调用 `_sticker_dict()`，且该 helper 与新模块同一对象。
- 关键 sticker 路由只注册一次，静态路径不被 `/{sticker_id:int}` 影响。
- 没有新增 `asyncio.run()` 或同步 awaitable 包装。
- 定向回归、静态检查和全量测试均为 0 failures。

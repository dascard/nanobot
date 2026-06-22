# 普通 API Sticker / Media 路由拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将普通 `api/routes.py` 中 sticker / media HTTP 层拆到 `api/sticker_media_routes.py`，保留旧导入兼容、公开图片 token 语义和聊天主链路边界。

**架构：** 新模块承载 `StickerRegisterRequest` 与 5 个 endpoint，并使用 `api.common_auth.verify_token` 复用普通 API 鉴权兼容层。父模块只负责 include 子 router 和 re-export 旧符号；`/chat`、`/group/message`、聊天图片 helper、群聊 sticker facade、Prompt Runtime 和 message envelope 均不进入本阶段。

**技术栈：** FastAPI `APIRouter`、Pydantic、SQLAlchemy session、pytest、in-memory SQLite、现有 `core.sticker_memory` / `core.sticker_preview` / `core.generated_images`。

---

## 当前状态

- 设计文档：`docs/superpowers/specs/2026-06-22-api-sticker-media-routes-split-design.md`。
- 设计提交：`0b02d3e docs(普通API): 设计贴纸媒体路由拆分`。
- 计划提交：`9493c0c docs(计划): 记录贴纸媒体路由拆分计划`。
- 红灯测试提交：`6ded608 test(普通API): 锁定贴纸媒体路由拆分契约`。
- 实现提交：`8d3acbc refactor(普通API): 拆分贴纸媒体路由`。
- `api/routes.py` 当前 1975 行，拆分前为 2134 行。
- 本阶段迁移：
  - `StickerRegisterRequest`
  - `register_sticker_endpoint`
  - `search_sticker_endpoint`
  - `public_sticker_image`
  - `public_generated_image`
  - `disable_sticker_endpoint`
- 本阶段保留在父模块：
  - `/chat`
  - `/group/message`
  - `ChatProxyRequest`
  - `GroupMessageRequest`
  - `_persist_chat_turn()`
  - `_safe_meta()`
  - `_normalize_files()`
  - `_schedule_image_precache()`
  - `_build_multimodal_user_input_text()`
  - `_build_chatlog_user_content()`
  - `_build_conversation_user_content()`
  - `_group_sticker_payloads`
  - `_register_group_stickers_from_message`
  - `memory`
  - `init_legacy_memory()`
  - `evolution_task`
  - `/health`

## 执行记录

- 红灯：`python -B -m pytest -q -p no:cacheprovider tests/test_api_sticker_media_routes_split.py`
  -> `3 failed, 7 passed, 21 warnings in 7.04s`；失败点为 5 个 sticker / media
  endpoint 仍注册在 `api.routes`、`api.sticker_media_routes` 尚不可导入，以及
  `api/sticker_media_routes.py` 文件不存在。
- Split 绿灯：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_sticker_media_routes_split.py`
  -> `10 passed, 21 warnings in 1.66s`。
- 行为回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api.py::test_sticker_register_search_and_disable_api tests/test_api.py::test_public_sticker_image_returns_cached_file tests/test_api.py::test_sticker_register_auto_describe_adds_background_task tests/test_sticker_memory.py tests/test_sticker_rag.py tests/test_sticker_tool.py tests/test_image_generation_tool.py tests/test_push_envelope.py tests/test_qq_outbound_renderer.py`
  -> `67 passed, 21 warnings in 6.05s`。
- 普通 API split 相邻回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_sticker_media_routes_split.py tests/test_api_history_log_routes_split.py tests/test_api_evolution_routes_split.py tests/test_api_memory_routes_split.py tests/test_api_model_routes_split.py tests/test_api_task_routes_split.py tests/test_asyncio_run_policy.py`
  -> `56 passed, 21 warnings in 7.67s`。
- 静态检查：`python -B -m py_compile api/routes.py api/sticker_media_routes.py tests/test_api_sticker_media_routes_split.py`
  成功；`rg -n "from api\.routes|import api\.routes|asyncio\.run|run_awaitable_sync" api/sticker_media_routes.py`
  无命中，退出码为 1；`git diff --check -- api/routes.py api/sticker_media_routes.py tests/test_api_sticker_media_routes_split.py`
  无输出。
- 行数检查：`api/routes.py` 1975 行，`api/sticker_media_routes.py` 185 行，
  `tests/test_api_sticker_media_routes_split.py` 182 行。
- 全量：`python -B -m pytest -p no:cacheprovider tests/ -v` ->
  `1620 passed, 6 skipped, 139 warnings in 116.71s`。
- 文档收口定向回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_sticker_media_routes_split.py tests/test_api.py::test_sticker_register_search_and_disable_api tests/test_api.py::test_public_sticker_image_returns_cached_file tests/test_api.py::test_sticker_register_auto_describe_adds_background_task tests/test_asyncio_run_policy.py`
  -> `16 passed, 21 warnings in 3.87s`。

## 文件职责

- 创建：`tests/test_api_sticker_media_routes_split.py`
  - 锁定拆分后的 endpoint module、旧导入兼容、普通 API token monkeypatch、公开图片 token 边界、route 顺序和父模块边界。
- 创建：`api/sticker_media_routes.py`
  - 承载 sticker / media request model 与 5 个 endpoint。
- 修改：`api/routes.py`
  - 删除本地 sticker / media request model 与 endpoint 实现。
  - 从 `api.sticker_media_routes` 导入并 re-export 迁移符号。
  - include `sticker_media_router`。
- 修改：`.Codex/plans/api-sticker-media-routes-split.md`
  - 文档收口时勾选执行记录和验收清单。
- 修改：`docs/todo.md`
  - 文档收口时记录 P3 第七刀进展。
- 修改：`docs/plan_walkthrough.md`
  - 文档收口时追加 2026-06-22 阶段记录。

## 任务 1：补普通 API sticker / media route split 红灯测试并提交

**文件：**

- 创建：`tests/test_api_sticker_media_routes_split.py`

- [x] **步骤 1：创建测试文件**

创建 `tests/test_api_sticker_media_routes_split.py`：

```python
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


_STICKER_MEDIA_ROUTE_SIGNATURES = (
    ("POST", "/api/v1/stickers/register"),
    ("GET", "/api/v1/stickers/search"),
    ("GET", "/api/v1/stickers/{sticker_id}/image"),
    ("GET", "/api/v1/generated-images/{image_id}/image"),
    ("POST", "/api/v1/stickers/{sticker_id}/disable"),
)

_STICKER_MEDIA_ROUTE_EXPORTS = (
    "StickerRegisterRequest",
    "register_sticker_endpoint",
    "search_sticker_endpoint",
    "public_sticker_image",
    "public_generated_image",
    "disable_sticker_endpoint",
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


def _route_index(path: str, method: str) -> int:
    for index, (route_path, route) in enumerate(_api_route_entries()):
        if route_path == path and method in getattr(route, "methods", set()):
            return index
    raise AssertionError(f"missing route: {method} {path}")


def test_api_sticker_media_routes_are_registered_from_split_module():
    for method, path in _STICKER_MEDIA_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.sticker_media_routes"}


def test_legacy_api_routes_sticker_media_imports_still_work():
    from api import routes
    from api import sticker_media_routes

    for name in _STICKER_MEDIA_ROUTE_EXPORTS:
        assert getattr(routes, name) is getattr(sticker_media_routes, name)

    body = routes.StickerRegisterRequest(
        chat_stream_id="123",
        file_ref="https://example.com/a.png",
        sticker_hash="hash-a",
    )
    assert body.chat_stream_id == "123"
    assert body.source_type == "manual"
    assert body.status == "active"


def test_split_sticker_media_routes_use_legacy_api_token_monkeypatch(monkeypatch):
    from server import app

    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")

    with TestClient(app) as test_client:
        ok = test_client.get(
            "/api/v1/stickers/search?query=hi",
            headers={"Authorization": "Bearer split-token"},
        )
        wrong = test_client.get(
            "/api/v1/stickers/search?query=hi",
            headers={"Authorization": "Bearer wrong"},
        )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_api_sticker_media_routes_are_not_registered_twice():
    for method, path in _STICKER_MEDIA_ROUTE_SIGNATURES:
        assert len(_api_routes_for(path, method)) == 1, f"{method} {path}"


def test_api_sticker_media_routes_do_not_import_parent_routes_or_sync_awaitable():
    source = Path("api/sticker_media_routes.py").read_text(encoding="utf-8")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_sticker_collection_routes_precede_dynamic_sticker_routes():
    register_index = _route_index("/api/v1/stickers/register", "POST")
    search_index = _route_index("/api/v1/stickers/search", "GET")
    image_index = _route_index("/api/v1/stickers/{sticker_id}/image", "GET")
    disable_index = _route_index("/api/v1/stickers/{sticker_id}/disable", "POST")

    assert register_index < image_index
    assert search_index < image_index
    assert register_index < disable_index
    assert search_index < disable_index


def test_public_sticker_image_keeps_env_token_boundary(monkeypatch):
    from server import app

    monkeypatch.setenv("NANOBOT_STICKER_IMAGE_TOKEN", "image-token")

    with TestClient(app) as test_client:
        wrong = test_client.get("/api/v1/stickers/999999/image?token=wrong")
        ok_missing = test_client.get("/api/v1/stickers/999999/image?token=image-token")

    assert wrong.status_code == 403
    assert ok_missing.status_code == 404


def test_public_generated_image_keeps_env_token_boundary(monkeypatch):
    from server import app

    monkeypatch.setenv("NANOBOT_GENERATED_IMAGE_TOKEN", "image-token")

    with TestClient(app) as test_client:
        wrong = test_client.get("/api/v1/generated-images/not-present/image?token=wrong")
        ok_missing = test_client.get(
            "/api/v1/generated-images/not-present/image?token=image-token"
        )

    assert wrong.status_code == 403
    assert ok_missing.status_code == 404


def test_chat_and_group_boundaries_stay_in_parent_routes():
    from api import routes

    assert routes.proxy_chat.__module__ == "api.routes"
    assert routes.group_message.__module__ == "api.routes"
    assert routes._persist_chat_turn.__module__ == "api.routes"
    assert routes._safe_meta.__module__ == "api.routes"
    assert routes._normalize_files.__module__ == "api.routes"
    assert routes._schedule_image_precache.__module__ == "api.routes"
    assert routes._build_multimodal_user_input_text.__module__ == "api.routes"
    assert routes._build_chatlog_user_content.__module__ == "api.routes"
    assert routes._build_conversation_user_content.__module__ == "api.routes"


def test_group_sticker_helpers_stay_group_ingress_facades():
    from api import routes
    from app.group_ingress import helpers

    assert routes._group_sticker_payloads is helpers.group_sticker_payloads
    assert routes._register_group_stickers_from_message is helpers.register_group_stickers_from_message
```

- [x] **步骤 2：运行测试验证红灯**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_sticker_media_routes_split.py
```

预期：FAIL。失败点应指向 `api.sticker_media_routes` 尚不存在、5 个 endpoint 仍注册在
`api.routes`，以及 `api/sticker_media_routes.py` 文件尚不存在。

- [x] **步骤 3：提交红灯测试**

运行：

```bash
git add tests/test_api_sticker_media_routes_split.py
git diff --cached --check
git commit -m "test(普通API): 锁定贴纸媒体路由拆分契约"
```

## 任务 2：拆出 `api/sticker_media_routes.py` 并提交

**文件：**

- 创建：`api/sticker_media_routes.py`
- 修改：`api/routes.py`

- [x] **步骤 1：创建 `api/sticker_media_routes.py`**

创建 `api/sticker_media_routes.py`，从 `api/routes.py` 当前实现迁移 request model 和
5 个 endpoint。模块骨架如下：

```python
"""普通 API 表情包与公开媒体代理路由。"""
from __future__ import annotations

import logging
import os
from hmac import compare_digest

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.common_auth import verify_token
from core.database import StickerMemory, get_db
from core.generated_images import get_generated_image_path
from core.sticker_memory import (
    auto_describe_sticker,
    disable_sticker,
    register_sticker,
    search_stickers,
)
from core.sticker_preview import (
    cache_sticker_preview,
    media_type_for_path,
    safe_existing_local_path,
)

logger = logging.getLogger("nanobot.routes.sticker_media")
router = APIRouter(tags=["sticker-media"])
```

迁移函数体时保持行为：

- `register_sticker_endpoint()` 捕获 `ValueError` 并返回 400。
- `register_sticker_endpoint()` 在 `auto_describe=True` 且无 description 时只通过
  `background_tasks.add_task(auto_describe_sticker, sticker["id"])` 排队。
- `search_sticker_endpoint()` 返回 `{"status": "ok", "results": ...}`，并将 `limit`
  限制到 1-20。
- `public_sticker_image()` 继续读取 `NANOBOT_STICKER_IMAGE_TOKEN`，使用
  `compare_digest()`，无 bearer 依赖。
- `public_sticker_image()` 继续 duplicate canonical 跳转、active 状态判断、
  cache fallback、`FileResponse` 文件名和 media type。
- `public_generated_image()` 继续读取 `NANOBOT_GENERATED_IMAGE_TOKEN`，无 bearer 依赖，
  缺失图片返回 404。
- `disable_sticker_endpoint()` 捕获 `ValueError` 并返回 404。

- [x] **步骤 2：修改 `api/routes.py` import 与 re-export**

删除父模块中不再需要的 import：

```python
from hmac import compare_digest
```

在普通 API split imports 中新增：

```python
from api.sticker_media_routes import (
    StickerRegisterRequest,
    disable_sticker_endpoint,
    public_generated_image,
    public_sticker_image,
    register_sticker_endpoint,
    router as sticker_media_router,
    search_sticker_endpoint,
)
```

保留 `os`，因为父模块仍在 `_finalize_private_buffer()` 中使用 `os.getenv()`。
保留 `Response`、`StreamingResponse`、`Header` 等父模块仍使用的 import。

- [x] **步骤 3：删除父模块本地 sticker / media 定义并 include 子 router**

删除 `api/routes.py` 中本地：

- `StickerRegisterRequest`
- `register_sticker_endpoint()`
- `search_sticker_endpoint()`
- `public_sticker_image()`
- `public_generated_image()`
- `disable_sticker_endpoint()`

在尾部 include 区加入：

```python
router.include_router(evolution_router)
router.include_router(history_log_router)
router.include_router(memory_router)
router.include_router(model_router)
router.include_router(task_router)
router.include_router(sticker_media_router)
```

- [x] **步骤 4：运行 split 定向测试验证绿灯**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_api_sticker_media_routes_split.py
```

预期：PASS。

- [x] **步骤 5：运行行为回归与相邻回归**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api.py::test_sticker_register_search_and_disable_api \
  tests/test_api.py::test_public_sticker_image_returns_cached_file \
  tests/test_api.py::test_sticker_register_auto_describe_adds_background_task \
  tests/test_sticker_memory.py \
  tests/test_sticker_rag.py \
  tests/test_sticker_tool.py \
  tests/test_image_generation_tool.py \
  tests/test_push_envelope.py \
  tests/test_qq_outbound_renderer.py
```

预期：PASS。

- [x] **步骤 6：运行普通 API split 相邻回归**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api_sticker_media_routes_split.py \
  tests/test_api_history_log_routes_split.py \
  tests/test_api_evolution_routes_split.py \
  tests/test_api_memory_routes_split.py \
  tests/test_api_model_routes_split.py \
  tests/test_api_task_routes_split.py \
  tests/test_asyncio_run_policy.py
```

预期：PASS。

- [x] **步骤 7：运行静态检查**

运行：

```bash
python -B -m py_compile api/routes.py api/sticker_media_routes.py tests/test_api_sticker_media_routes_split.py
rg -n "from api\\.routes|import api\\.routes|asyncio\\.run|run_awaitable_sync" api/sticker_media_routes.py
git diff --check -- api/routes.py api/sticker_media_routes.py tests/test_api_sticker_media_routes_split.py
wc -l api/routes.py api/sticker_media_routes.py tests/test_api_sticker_media_routes_split.py
```

预期：

- `py_compile` 成功。
- `rg` 无命中，退出码为 1。
- `git diff --check` 无输出。
- `api/routes.py` 行数低于 2134。

- [x] **步骤 8：运行全量测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：0 failures。

- [x] **步骤 9：提交 sticker / media 路由拆分**

运行：

```bash
git add api/routes.py api/sticker_media_routes.py
git diff --cached --check
git commit -m "refactor(普通API): 拆分贴纸媒体路由"
```

## 任务 3：文档收口并提交

**文件：**

- 修改：`.Codex/plans/api-sticker-media-routes-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [x] **步骤 1：更新计划执行记录**

在本计划的「当前状态」中把任务 1 和任务 2 标记为完成，并新增「执行记录」章节，
记录：

- 设计提交。
- 计划提交。
- 红灯测试提交。
- 实现提交。
- 红灯测试结果。
- split 绿灯结果。
- 行为回归与相邻回归结果。
- 静态检查结果。
- `wc -l api/routes.py api/sticker_media_routes.py tests/test_api_sticker_media_routes_split.py` 行数。
- 全量回归结果。

- [x] **步骤 2：更新 `docs/todo.md`**

在「超大文件 >800 行拆分」条目下记录：

- `api/routes.py` 第七刀已拆出 sticker / media HTTP 层。
- 新模块 `api/sticker_media_routes.py`。
- 旧导入兼容和公开图片 token 边界。
- `api/routes.py` 最新行数。
- 下一候选为 `chat-step / render` 小刀或继续审计更低风险 route-only 边界。

- [x] **步骤 3：更新 `docs/plan_walkthrough.md`**

追加 2026-06-22 的 sticker / media route-only 拆分执行记录，包含：

- 选择 sticker / media 的原因。
- 设计、计划、红灯、实现和收口提交。
- 计划列表。
- 验证命令和结果。
- 下一步建议。

- [x] **步骤 4：文档格式与状态检查**

运行：

```bash
rg -n "^- \\[ \\]" .Codex/plans/api-sticker-media-routes-split.md
rg -n "T[O]DO|待[定]|后续实[现]|占[位]|\\x{FFFD}" .Codex/plans/api-sticker-media-routes-split.md docs/todo.md docs/plan_walkthrough.md
git diff --check -- .Codex/plans/api-sticker-media-routes-split.md docs/todo.md docs/plan_walkthrough.md
git status --short
```

预期：第一个 `rg` 在收口后无命中；第二个 `rg` 无命中；`git diff --check` 无输出；
`git status --short` 中本阶段只包含计划与文档相关改动，以及历史无关脏项。

- [x] **步骤 5：运行最终定向回归**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api_sticker_media_routes_split.py \
  tests/test_api.py::test_sticker_register_search_and_disable_api \
  tests/test_api.py::test_public_sticker_image_returns_cached_file \
  tests/test_api.py::test_sticker_register_auto_describe_adds_background_task \
  tests/test_asyncio_run_policy.py
```

预期：PASS。

- [x] **步骤 6：提交文档收口**

运行：

```bash
git add .Codex/plans/api-sticker-media-routes-split.md docs/todo.md docs/plan_walkthrough.md
git diff --cached --check
git commit -m "docs(计划): 收口贴纸媒体路由拆分"
```

## 最终验收清单

- [x] `tests/test_api_sticker_media_routes_split.py` 经历红灯再绿灯。
- [x] `api/sticker_media_routes.py` 已创建。
- [x] `api.sticker_media_routes` 不导入 `api.routes`。
- [x] `api.sticker_media_routes` 不包含 `asyncio.run` 或 `run_awaitable_sync`。
- [x] `api.routes` re-export `StickerRegisterRequest` 和 5 个 endpoint。
- [x] 5 个 sticker / media endpoint 注册来源均为 `api.sticker_media_routes`。
- [x] 5 个 sticker / media endpoint 没有重复注册。
- [x] `/stickers/register` 与 `/stickers/search` 早于动态 sticker 路由。
- [x] `/stickers/search` 继续兼容 `api.routes.NANOBOT_API_TOKEN` monkeypatch。
- [x] 公开图片代理端点没有增加 bearer 鉴权。
- [x] `NANOBOT_STICKER_IMAGE_TOKEN` 和 `NANOBOT_GENERATED_IMAGE_TOKEN` 行为不变。
- [x] `/chat` 与 `/group/message` 主链路未迁移。
- [x] `_persist_chat_turn()`、`_safe_meta()`、聊天图片 helper 和群聊 sticker facade
  继续留在父模块。
- [x] 现有 sticker、generated image、push renderer 相关回归通过。
- [x] `tests/test_asyncio_run_policy.py` 通过。
- [x] 全量 `tests/` 回归 0 failures。
- [x] 每个阶段性改动都有独立 commit。

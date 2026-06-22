# 普通 API Group Message 路由拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将普通 `api/routes.py` 中 `/group/message` HTTP 层拆到 `api/group_message_routes.py`，保留旧导入兼容、普通 API 鉴权兼容、`get_bridge` monkeypatch 兼容和群聊入口行为。

**架构：** 新模块承载 `OneBotMessageSegmentPayload`、`GroupMessageRequest` 与 `group_message()`，endpoint 仍只负责依赖注入并把请求交给 `app.group_ingress.service.GroupIngressService`。父模块在原 group ingress helper facade 之后 include 子 router，并 re-export 旧符号；`/chat`、`/health`、Prompt Runtime、message envelope、聊天落库、私聊缓冲和 group ingress helper facade 均不进入本阶段。

**技术栈：** FastAPI `APIRouter`、Pydantic、SQLAlchemy session、pytest、FastAPI `TestClient`、现有 `GroupIngressService`、`core.client_meta` 和普通 API split 测试模板。

---

## 当前状态

- 设计文档：`docs/superpowers/specs/2026-06-22-api-group-message-routes-split-design.md`。
- 设计提交：`1ccbf33 docs(普通API): 设计群消息路由拆分`。
- `api/routes.py` 当前约 1754 行，剩余显式 route 为 `/group/message`、`/chat` 与 `/health`。
- `api/group_utility_routes.py` 已 include 在 `/group/message` 之后；本阶段必须保持 `/group/message` -> `/update_group_name` -> `/group_timing` -> `/group_timing/timer` -> `/render` -> `/chat-step` -> `/chat` 的 route order。
- `tests/test_api_agent_step_routes_split.py` 和 `tests/test_api_sticker_media_routes_split.py` 当前仍断言 `group_message` 留在 `api.routes`，红灯阶段需要同步改为拆分后的 re-export 断言。
- `group_message()` 当前调用父模块 `_normalize_request_client_meta(req, expected_chat_type="group")`，并把 `nanobot_kt.bridge.get_bridge` 直接传给 `GroupIngressService`。新模块不得反向导入 `api.routes`，需要在本地实现 client meta wrapper，并用 `sys.modules["api.routes"].get_bridge` 保留旧 monkeypatch 兼容。
- 本阶段不碰 `/chat` 主链路，不迁移 `ChatProxyRequest`、`proxy_chat()`、`_persist_chat_turn()`、`_safe_meta()`、私聊 multimodal helper、Prompt Runtime 输入组装或 group ingress helper facade。

本阶段迁移：

- `OneBotMessageSegmentPayload`
- `GroupMessageRequest`
- `group_message`
- `POST /group/message`

本阶段保留在父模块：

- `/chat`
- `/health`
- `ChatProxyRequest`
- `proxy_chat()`
- 私聊缓冲、guardrail、流式响应、Prompt Runtime 输入组装和 message envelope
- `_persist_chat_turn()`
- `_safe_meta()`
- `_normalize_files()`
- `_schedule_image_precache()`
- `_build_multimodal_user_input_text()`
- `_build_chatlog_user_content()`
- `_build_conversation_user_content()`
- group ingress helper facade 旧私有名称

## 文件职责

- 创建：`tests/test_api_group_message_routes_split.py`
  - 锁定拆分后的 endpoint module、旧导入兼容、普通 API token monkeypatch、`get_bridge` monkeypatch、client meta 400、route 顺序、async 边界、父模块边界和 helper facade identity。
- 修改：`tests/test_api_agent_step_routes_split.py`
  - 将 `group_message` 的父模块边界断言改为拆分后的 re-export 断言，同时保留 `/chat` 和 group utility 边界。
- 修改：`tests/test_api_sticker_media_routes_split.py`
  - 将 `group_message` 的父模块边界断言改为拆分后的 re-export 断言，同时保留聊天 helper 与群聊 sticker facade 边界。
- 创建：`api/group_message_routes.py`
  - 承载群消息 request model 与 endpoint。
  - 定义 `_current_bridge_provider()`，兼容旧 `api.routes.get_bridge` monkeypatch。
  - 本地定义 `_normalize_request_client_meta()`，避免反向导入父模块。
  - 不导入父模块 `api.routes`。
- 修改：`api/routes.py`
  - 删除本地 `OneBotMessageSegmentPayload`、`GroupMessageRequest` 和 `group_message()`。
  - 从 `api.group_message_routes` 导入并 re-export 迁移符号。
  - 在 group ingress helper facade 之后、`group_utility_router` 之前 include `group_message_router`。
- 修改：`.Codex/plans/api-group-message-routes-split.md`
  - 文档收口时勾选执行记录和验收清单。
- 修改：`docs/todo.md`
  - 文档收口时记录 P3 普通 API 第十刀进展。
- 修改：`docs/plan_walkthrough.md`
  - 文档收口时追加 2026-06-22 阶段记录。

## 任务 1：补普通 API group message route split 红灯测试并提交

**文件：**

- 创建：`tests/test_api_group_message_routes_split.py`
- 修改：`tests/test_api_agent_step_routes_split.py`
- 修改：`tests/test_api_sticker_media_routes_split.py`

- [ ] **步骤 1：创建 group message split 测试文件**

创建 `tests/test_api_group_message_routes_split.py`：

```python
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


_GROUP_MESSAGE_ROUTE_SIGNATURES = (
    ("POST", "/api/v1/group/message"),
)

_GROUP_MESSAGE_ROUTE_EXPORTS = (
    "OneBotMessageSegmentPayload",
    "GroupMessageRequest",
    "group_message",
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


def test_api_group_message_route_is_registered_from_split_module():
    for method, path in _GROUP_MESSAGE_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.group_message_routes"}


def test_legacy_api_routes_group_message_imports_still_work():
    from api import group_message_routes
    from api import routes

    for name in _GROUP_MESSAGE_ROUTE_EXPORTS:
        assert getattr(routes, name) is getattr(group_message_routes, name)

    body = routes.GroupMessageRequest(group_id="123", sender_id="456", message="你好")
    assert body.group_id == "123"
    assert body.sender_id == "456"
    assert body.message == "你好"
    assert body.bot_aliases == []
    assert body.segments == []

    segment = routes.OneBotMessageSegmentPayload(type="text", data={"text": "hi"})
    assert segment.type == "text"
    assert segment.data == {"text": "hi"}


def test_split_group_message_route_uses_legacy_api_token_monkeypatch(monkeypatch):
    from server import app

    class FakeService:
        def __init__(self, *args, **kwargs):
            pass

        async def handle(self, req):
            return {"action": "no_reply", "reason": "fake"}

    monkeypatch.setattr(
        "app.group_ingress.service.GroupIngressService",
        FakeService,
    )
    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")

    with TestClient(app) as test_client:
        ok = test_client.post(
            "/api/v1/group/message",
            json={"group_id": "123", "message": "hi"},
            headers={"Authorization": "Bearer split-token"},
        )
        wrong = test_client.post(
            "/api/v1/group/message",
            json={"group_id": "123", "message": "hi"},
            headers={"Authorization": "Bearer wrong"},
        )

    assert ok.status_code == 200
    assert ok.json() == {"action": "no_reply", "reason": "fake"}
    assert wrong.status_code == 401


def test_api_group_message_route_is_not_registered_twice():
    for method, path in _GROUP_MESSAGE_ROUTE_SIGNATURES:
        assert len(_api_routes_for(path, method)) == 1, f"{method} {path}"


def test_api_group_message_route_does_not_import_parent_routes_or_sync_awaitable():
    source = Path("api/group_message_routes.py").read_text(encoding="utf-8")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_group_message_route_keeps_order_before_group_utility_and_chat():
    group_message_index = _route_index("/api/v1/group/message", "POST")
    update_index = _route_index("/api/v1/update_group_name", "POST")
    group_timing_index = _route_index("/api/v1/group_timing", "POST")
    timer_index = _route_index("/api/v1/group_timing/timer", "POST")
    render_index = _route_index("/api/v1/render", "GET")
    chat_step_index = _route_index("/api/v1/chat-step", "POST")
    chat_index = _route_index("/api/v1/chat", "POST")

    assert group_message_index < update_index
    assert update_index < group_timing_index
    assert group_timing_index < timer_index
    assert timer_index < render_index
    assert render_index < chat_step_index
    assert chat_step_index < chat_index


def test_api_group_message_async_boundary_remains_coroutine():
    from api import group_message_routes
    from api import routes

    assert inspect.iscoroutinefunction(group_message_routes.group_message)
    assert inspect.iscoroutinefunction(routes.group_message)


@pytest.mark.asyncio
async def test_group_message_uses_legacy_routes_get_bridge_monkeypatch(monkeypatch, db_session):
    from api import group_message_routes
    from api import routes

    calls = []

    class FakeService:
        def __init__(self, *, db, background_tasks, bridge_provider):
            self.db = db
            self.background_tasks = background_tasks
            self.bridge_provider = bridge_provider

        async def handle(self, req):
            bridge = self.bridge_provider()
            calls.append(
                {
                    "bridge": bridge,
                    "group_id": req.group_id,
                    "client_meta": req.client_meta,
                }
            )
            return {"action": "no_reply", "reason": "fake"}

    class FakeBridge:
        pass

    monkeypatch.setattr(
        "app.group_ingress.service.GroupIngressService",
        FakeService,
    )
    monkeypatch.setattr(routes, "get_bridge", lambda: FakeBridge())

    result = await group_message_routes.group_message(
        group_message_routes.GroupMessageRequest(
            group_id="123",
            message="你好",
            client_meta={"platform": "web"},
        ),
        db=db_session,
        background_tasks=None,
        _auth=None,
    )

    assert result == {"action": "no_reply", "reason": "fake"}
    assert isinstance(calls[0]["bridge"], FakeBridge)
    assert calls[0]["group_id"] == "123"
    assert calls[0]["client_meta"]["platform"] == "web"
    assert calls[0]["client_meta"]["chat_type"] == "group"


@pytest.mark.asyncio
async def test_group_message_rejects_conflicting_client_meta_before_service(
    monkeypatch,
    db_session,
):
    from api import group_message_routes

    class FakeService:
        def __init__(self, *args, **kwargs):
            raise AssertionError("invalid client_meta must not enter service")

    monkeypatch.setattr(
        "app.group_ingress.service.GroupIngressService",
        FakeService,
    )

    with pytest.raises(HTTPException) as exc:
        await group_message_routes.group_message(
            group_message_routes.GroupMessageRequest(
                group_id="123",
                message="你好",
                client_meta={"chat_type": "private"},
            ),
            db=db_session,
            background_tasks=None,
            _auth=None,
        )

    assert exc.value.status_code == 400
    assert "client_meta" in str(exc.value.detail)


def test_chat_and_health_boundaries_stay_in_parent_routes_after_group_message_split():
    from api import routes

    assert routes.proxy_chat.__module__ == "api.routes"
    assert routes._persist_chat_turn.__module__ == "api.routes"
    assert routes._safe_meta.__module__ == "api.routes"
    assert routes._build_multimodal_user_input_text.__module__ == "api.routes"

    health_routes = _api_routes_for("/api/v1/health", "GET")
    assert health_routes
    assert {route.endpoint.__module__ for route in health_routes} == {"api.routes"}


def test_group_ingress_helper_facades_stay_in_parent_routes():
    from api import routes
    from app.group_ingress import helpers

    assert routes._normalize_onebot_segments is helpers.normalize_onebot_segments
    assert routes._build_group_message_text is helpers.build_group_message_text
    assert routes._persist_group_bridge_reply is helpers.persist_group_bridge_reply
    assert routes._derive_group_trigger_reason is helpers.derive_group_trigger_reason
```

- [ ] **步骤 2：调整 Agent Step split 父模块边界测试**

修改 `tests/test_api_agent_step_routes_split.py` 末尾测试：

```python
def test_chat_boundaries_stay_in_parent_routes_after_group_message_split():
    from api import group_message_routes
    from api import group_utility_routes
    from api import routes

    assert routes.proxy_chat.__module__ == "api.routes"
    assert routes._persist_chat_turn.__module__ == "api.routes"
    assert routes._safe_meta.__module__ == "api.routes"
    assert routes.group_message is group_message_routes.group_message
    assert routes.group_timing_timer is group_utility_routes.group_timing_timer
```

- [ ] **步骤 3：调整 Sticker / Media split 父模块边界测试**

修改 `tests/test_api_sticker_media_routes_split.py` 的 `test_chat_and_group_boundaries_stay_in_parent_routes()`：

```python
def test_chat_and_group_boundaries_stay_in_parent_routes():
    from api import group_message_routes
    from api import routes

    assert routes.proxy_chat.__module__ == "api.routes"
    assert routes.group_message is group_message_routes.group_message
    assert routes._persist_chat_turn.__module__ == "api.routes"
    assert routes._safe_meta.__module__ == "api.routes"
    assert routes._normalize_files.__module__ == "api.routes"
    assert routes._schedule_image_precache.__module__ == "api.routes"
    assert routes._build_multimodal_user_input_text.__module__ == "api.routes"
    assert routes._build_chatlog_user_content.__module__ == "api.routes"
    assert routes._build_conversation_user_content.__module__ == "api.routes"
```

- [ ] **步骤 4：运行红灯测试**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_agent_step_routes_split.py \
  tests/test_api_sticker_media_routes_split.py
```

预期：FAIL。关键失败点应包含 `api.group_message_routes` 不存在、`/group/message` endpoint 仍注册在 `api.routes`、`api/group_message_routes.py` 文件不存在，以及相邻 split 测试已期待 `routes.group_message` re-export 新模块对象。

- [ ] **步骤 5：提交红灯测试**

运行：

```bash
git add tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py tests/test_api_sticker_media_routes_split.py
git diff --cached --check
git commit -m "test(普通API): 锁定群消息路由拆分契约"
```

## 任务 2：实现 group message 路由拆分并提交

**文件：**

- 创建：`api/group_message_routes.py`
- 修改：`api/routes.py`

- [ ] **步骤 1：创建 `api/group_message_routes.py`**

创建文件骨架：

```python
"""普通 API 群聊入口路由。"""
from __future__ import annotations

import sys
from typing import Any, Optional, List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.common_auth import verify_token
from core.client_meta import ClientMetaValidationError, normalize_client_meta
from core.database import get_db
from nanobot_kt.bridge import get_bridge as _default_get_bridge

router = APIRouter(tags=["group-message"])
```

- [ ] **步骤 2：添加旧 `get_bridge` monkeypatch provider**

在 `api/group_message_routes.py` 中加入：

```python
def _current_bridge_provider():
    routes = sys.modules.get("api.routes")
    if routes is not None and hasattr(routes, "get_bridge"):
        return getattr(routes, "get_bridge")
    return _default_get_bridge
```

`group_message()` 中只把 `_current_bridge_provider()` 的返回值传给 `GroupIngressService`，不直接传 `_default_get_bridge`。

- [ ] **步骤 3：添加 client meta wrapper**

在 `api/group_message_routes.py` 中加入：

```python
def _normalize_request_client_meta(req: Any, *, expected_chat_type: str) -> dict[str, Any]:
    try:
        normalized = normalize_client_meta(
            getattr(req, "client_meta", None),
            expected_chat_type=expected_chat_type,
        )
    except ClientMetaValidationError as exc:
        raise HTTPException(400, f"invalid client_meta: {exc}") from exc
    req.client_meta = normalized
    return normalized
```

该函数只服务新模块的 `/group/message`；父模块 `/chat` 继续使用 `api.routes._normalize_request_client_meta()`。

- [ ] **步骤 4：迁移 request model**

从 `api/routes.py` 搬入：

```python
class OneBotMessageSegmentPayload(BaseModel):
    """OneBot/NapCat 消息段——不要和 NoneBot MessageSegment 混淆。"""
    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class GroupMessageRequest(BaseModel):
    group_id: str
    sender_id: str = ""
    sender_name: str = ""
    message: str = ""
    files: Optional[List[str]] = None
    client_meta: dict | None = None
    message_id: str | None = None
    session_name: str | None = None
    is_at_bot: bool = False
    is_reply_to_bot: bool = False
    bot_aliases: list[str] = Field(default_factory=list)
    segments: list[dict] = Field(default_factory=list)
    raw_message: str = ""
    self_id: str = ""
    bot_id: str = ""
    bot_name: str = ""
    sender_is_bot: bool = False
    mentions: list[dict] = Field(default_factory=list)
    reply_to: dict | None = None
    reply_to_message_id: str | None = None
    reply_to_sender_id: str | None = None
    reply_to_sender_name: str | None = None
    reply_to_content: str | None = None
    is_directed_to_other: bool = False
```

- [ ] **步骤 5：迁移 endpoint**

从 `api/routes.py` 搬入并改写 bridge provider：

```python
@router.post("/group/message")
async def group_message(
    req: GroupMessageRequest,
    db: Session = Depends(get_db),
    background_tasks: BackgroundTasks = None,
    _auth=Depends(verify_token),
):
    """统一群聊入口：route 只做依赖注入，业务流程在 GroupIngressService。"""
    from app.group_ingress.service import GroupIngressService

    _normalize_request_client_meta(req, expected_chat_type="group")
    service = GroupIngressService(
        db=db,
        background_tasks=background_tasks,
        bridge_provider=_current_bridge_provider(),
    )
    return await service.handle(req)
```

- [ ] **步骤 6：修改父模块 import 与 re-export**

在 `api/routes.py` 的普通 API split import 区加入：

```python
from api.group_message_routes import (
    GroupMessageRequest,
    OneBotMessageSegmentPayload,
    group_message,
    router as group_message_router,
)
```

删除父模块本地：

- `OneBotMessageSegmentPayload`
- `GroupMessageRequest`
- `group_message()`

父模块 `typing` import 仍可保留 `Optional` 和 `List`，因为 `ChatProxyRequest` 仍使用。

- [ ] **步骤 7：在原位置 include 子 router**

在 `api/routes.py` 中保持顺序：

```python
_derive_group_trigger_reason = group_ingress_helpers.derive_group_trigger_reason


router.include_router(group_message_router)
router.include_router(group_utility_router)
router.include_router(agent_step_router)
```

不要把 `group_message_router` 放到文件尾部 include 区，否则 route order 会变化。

- [ ] **步骤 8：运行 split 绿灯测试**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_agent_step_routes_split.py \
  tests/test_api_sticker_media_routes_split.py \
  tests/test_api_group_utility_routes_split.py
```

预期：PASS，确认 group message、group utility、Agent Step 和 Sticker / Media 边界均符合拆分后契约。

- [ ] **步骤 9：运行群消息行为回归**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api.py::test_group_message_ambient_enters_timing_gate \
  tests/test_api.py::test_group_message_passes_client_platform_to_timing_gate \
  tests/test_api.py::test_group_message_passes_client_platform_to_bridge \
  tests/test_api.py::test_group_message_returns_full_html_reply_without_truncation \
  tests/test_api.py::test_group_message_prompt_v2_audit_failure_is_no_send \
  tests/test_group_response_envelope.py \
  tests/test_api_routes_group_helper_facade.py
```

预期：PASS，确认群消息 TimingGate、Bridge metadata、完整 HTML 回复、Prompt Runtime audit failure、响应信封和父模块 helper facade 均未回退。

- [ ] **步骤 10：运行普通 API split 相邻回归与 async 策略**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_group_utility_routes_split.py \
  tests/test_api_agent_step_routes_split.py \
  tests/test_api_sticker_media_routes_split.py \
  tests/test_api_history_log_routes_split.py \
  tests/test_api_evolution_routes_split.py \
  tests/test_api_memory_routes_split.py \
  tests/test_api_model_routes_split.py \
  tests/test_api_task_routes_split.py \
  tests/test_asyncio_run_policy.py
```

预期：PASS，确认普通 API 拆分边界与 `asyncio.run` 禁止策略保持有效。

- [ ] **步骤 11：运行静态检查**

运行：

```bash
python -B -m py_compile api/routes.py api/group_message_routes.py tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py tests/test_api_sticker_media_routes_split.py
rg -n "from api\\.routes|import api\\.routes|asyncio\\.run|run_awaitable_sync" api/group_message_routes.py
git diff --check -- api/routes.py api/group_message_routes.py tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py tests/test_api_sticker_media_routes_split.py
wc -l api/routes.py api/group_message_routes.py tests/test_api_group_message_routes_split.py
```

预期：

- `py_compile` 退出码为 0。
- `rg` 无命中，退出码为 1。
- `git diff --check` 无输出。
- `api/routes.py` 行数下降。

- [ ] **步骤 12：运行全量回归**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：0 failures。若失败集中在环境或历史脏项，记录完整失败测试名和错误摘要，不提交实现。

- [ ] **步骤 13：提交拆分实现**

运行：

```bash
git add api/group_message_routes.py api/routes.py tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py tests/test_api_sticker_media_routes_split.py
git diff --cached --check
git commit -m "refactor(普通API): 拆分群消息路由"
```

## 任务 3：文档收口并提交

**文件：**

- 修改：`.Codex/plans/api-group-message-routes-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [ ] **步骤 1：更新本计划执行记录**

在「当前状态」之后追加「执行记录」小节，写入红灯、Split 绿灯、群消息行为回归、普通 API split 相邻回归、静态检查、行数检查和全量回归的真实命令与真实输出摘要。每条记录必须包含命令、退出结论、通过或失败统计；红灯记录还要列出关键失败点。

同时将已完成步骤的复选框从 `- [ ]` 改为 `- [x]`。

- [ ] **步骤 2：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」记录中追加本阶段结果：

```markdown
- [x] 第十刀：普通 API group message 路由拆分到 `api/group_message_routes.py`，迁移 `/group/message`、`GroupMessageRequest` 与 `OneBotMessageSegmentPayload`，保留 `/chat`、`/health` 和 group ingress helper facade 在父模块。
```

如果 `docs/todo.md` 已经使用不同编号，按现有编号顺延，不改写无关条目。

- [ ] **步骤 3：更新 `docs/plan_walkthrough.md`**

追加 2026-06-22 记录，格式与文件现有条目一致，内容包含：

```markdown
### 2026-06-22：普通 API group message 路由拆分

- 计划：`.Codex/plans/api-group-message-routes-split.md`
- 设计：`docs/superpowers/specs/2026-06-22-api-group-message-routes-split-design.md`
- 提交：`test(普通API): 锁定群消息路由拆分契约`、`refactor(普通API): 拆分群消息路由`
- 结果：`api/group_message_routes.py` 承载 `/group/message` HTTP shell，`api/routes.py` 继续保留 `/chat`、`/health` 和 group ingress helper facade。
- 验证：记录本阶段实际通过的定向测试、相邻回归和全量回归结果。
```

- [ ] **步骤 4：运行文档检查与定向回归**

运行：

```bash
rg -n "T[O]DO|待[定]|后续实[现]|占[位]|\\x{FFFD}" .Codex/plans/api-group-message-routes-split.md docs/todo.md docs/plan_walkthrough.md
git diff --check -- .Codex/plans/api-group-message-routes-split.md docs/todo.md docs/plan_walkthrough.md
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api_group_message_routes_split.py \
  tests/test_api.py::test_group_message_passes_client_platform_to_bridge \
  tests/test_group_response_envelope.py::test_group_message_rejects_conflicting_client_meta_chat_type \
  tests/test_asyncio_run_policy.py
```

预期：

- `rg` 无命中，退出码为 1。
- `git diff --check` 无输出。
- pytest 0 failures。

- [ ] **步骤 5：提交文档收口**

运行：

```bash
git add .Codex/plans/api-group-message-routes-split.md docs/todo.md docs/plan_walkthrough.md
git diff --cached --check
git commit -m "docs(计划): 收口群消息路由拆分"
```

## 最终验收清单

- [ ] `tests/test_api_group_message_routes_split.py` 经历红灯再绿灯。
- [ ] `api/group_message_routes.py` 存在，并且不包含 `from api.routes`、`import api.routes`、`asyncio.run` 或 `run_awaitable_sync`。
- [ ] `POST /api/v1/group/message` endpoint module 为 `api.group_message_routes`。
- [ ] `POST /api/v1/group/message` 没有重复注册。
- [ ] `api.routes` 继续 re-export `OneBotMessageSegmentPayload`、`GroupMessageRequest` 和 `group_message`，旧导入对象与新模块对象相同。
- [ ] `api.routes.NANOBOT_API_TOKEN` monkeypatch 继续影响拆分后的 endpoint。
- [ ] `api.routes.get_bridge` monkeypatch 继续影响拆分后的 `group_message()`。
- [ ] `client_meta.chat_type` 与群聊入口冲突时继续返回 HTTP 400，且不会进入 `GroupIngressService`。
- [ ] route order 保持 `/group/message` -> `/update_group_name` -> `/group_timing` -> `/group_timing/timer` -> `/render` -> `/chat-step` -> `/chat`。
- [ ] `/chat`、`/health`、`proxy_chat()`、`_persist_chat_turn()`、`_safe_meta()`、`_build_multimodal_user_input_text()` 继续留在 `api.routes`。
- [ ] group ingress helper facade 继续留在 `api.routes`，并与 `app.group_ingress.helpers` 中对象保持 identity alias。
- [ ] 群消息行为回归、普通 API split 相邻回归、`tests/test_asyncio_run_policy.py` 与全量 `tests/` 均为 0 failures。
- [ ] 每个阶段性改动均已独立 commit，且未暂存历史无关脏项。

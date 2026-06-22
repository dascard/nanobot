# 普通 API Group Utility 路由拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将普通 `api/routes.py` 中 `/update_group_name`、`/group_timing` 与 `/group_timing/timer` 拆到 `api/group_utility_routes.py`，保留旧导入兼容、普通 API 鉴权兼容和 legacy timing 行为。

**架构：** 新模块承载 group utility / legacy timing HTTP shell，并直接依赖 `core.context_builder`、`core.group_runtime.ids`、`core.database` 与 `app.group_ingress.helpers`。父模块在原 `/group/message` 之后 include 子 router，并 re-export 旧符号；`/chat`、`/group/message`、Prompt Runtime、message envelope、聊天落库和私聊 multimodal helper 均不进入本阶段。

**技术栈：** FastAPI `APIRouter`、Pydantic、SQLAlchemy session、pytest、FastAPI `TestClient`、现有 `core.timing_runtime` / `core.context_builder` / `app.group_ingress.helpers`。

---

## 当前状态

- 设计文档：`docs/superpowers/specs/2026-06-22-api-group-utility-routes-split-design.md`。
- 设计提交：`d7a68f0 docs(普通API): 设计群工具路由拆分`。
- `api/routes.py` 当前约 1954 行，剩余显式 route 为 `/group/message`、`/update_group_name`、`/group_timing`、`/group_timing/timer`、`/chat` 与 `/health`。
- `api/agent_step_routes.py` 已 include 在 `/group_timing/timer` 之后、`/chat` 之前；本阶段必须保持 `/render`、`/chat-step` 仍在 `/chat` 前。
- `tests/test_api_agent_step_routes_split.py` 当前仍断言 `group_timing_timer` 留在 `api.routes`，本阶段红灯测试需要同步改为拆分后的预期。
- `group_timing_timer()` 当前调用父模块私有 `_build_multimodal_user_input_text()` 和 `MAX_QUERY_CHARS`。由于 timer 路径传入 `files=None`，实现阶段改为在新模块中直接使用 `core.context_builder.sanitize_prompt_text()` 构造 `chat_query`，避免迁移 `/chat` 的 multimodal helper，也避免反向导入 `api.routes`。
- 计划提交：`f8f1ac0 docs(计划): 记录群工具路由拆分计划`。
- 红灯测试提交：`9ae67ea test(普通API): 锁定群工具路由拆分契约`。
- 路由拆分提交：`e6d4be5 refactor(普通API): 拆分群工具路由`。

## 执行记录

- 红灯：`python -B -m pytest -q -p no:cacheprovider tests/test_api_group_utility_routes_split.py tests/test_api_agent_step_routes_split.py`
  -> `7 failed, 13 passed, 21 warnings in 8.33s`；失败点为 group utility endpoint
  仍注册在 `api.routes`、`api.group_utility_routes` 尚不可导入、
  `api/group_utility_routes.py` 文件不存在，以及 Agent Step 边界测试已期待
  `group_timing_timer` re-export 到新模块。
- Split 绿灯：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_group_utility_routes_split.py tests/test_api_agent_step_routes_split.py`
  -> `20 passed, 21 warnings in 2.88s`。
- timing 行为回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_timing_gate.py::TestRouteContext::test_group_timing_context_sanitizes_pending_messages tests/test_api.py::test_group_timer_returns_full_html_reply_without_truncation tests/test_api.py::test_group_message_returns_full_html_reply_without_truncation tests/test_api_routes_group_helper_facade.py`
  -> `5 passed, 1 warning in 1.51s`。
- 普通 API split 相邻回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_group_utility_routes_split.py tests/test_api_agent_step_routes_split.py tests/test_api_sticker_media_routes_split.py tests/test_api_history_log_routes_split.py tests/test_api_evolution_routes_split.py tests/test_api_memory_routes_split.py tests/test_api_model_routes_split.py tests/test_api_task_routes_split.py tests/test_asyncio_run_policy.py`
  -> `76 passed, 21 warnings in 10.01s`。
- 静态检查：`python -B -m py_compile api/routes.py api/group_utility_routes.py tests/test_api_group_utility_routes_split.py tests/test_api_agent_step_routes_split.py`
  成功；`api/group_utility_routes.py` 无 `from api.routes`、`import api.routes`、
  `asyncio.run` 或 `run_awaitable_sync`；`git diff --check -- api/routes.py api/group_utility_routes.py tests/test_api_group_utility_routes_split.py tests/test_api_agent_step_routes_split.py`
  无输出。
- 行数检查：`api/routes.py` 1754 行，`api/group_utility_routes.py` 283 行，
  `tests/test_api_group_utility_routes_split.py` 211 行。
- 全量：`python -B -m pytest -p no:cacheprovider tests/ -v` ->
  `1640 passed, 6 skipped, 139 warnings in 120.42s`。
- 文档收口定向回归：
  `python -B -m pytest -q -p no:cacheprovider tests/test_api_group_utility_routes_split.py tests/test_api.py::test_group_timer_returns_full_html_reply_without_truncation tests/test_asyncio_run_policy.py`
  -> `13 passed, 21 warnings in 2.73s`。

本阶段迁移：

- `UpdateGroupNameRequest`
- `update_group_name`
- `GroupTimingRequest`
- `_build_group_timing_context`
- `GroupTimingTimerRequest`
- `group_timing_deprecated`
- `group_timing_timer`
- `POST /update_group_name`
- `POST /group_timing`
- `POST /group_timing/timer`

本阶段保留在父模块：

- `/chat`
- `/group/message`
- `/health`
- `ChatProxyRequest`
- `GroupMessageRequest`
- OneBot segment model
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

- 创建：`tests/test_api_group_utility_routes_split.py`
  - 锁定拆分后的 endpoint module、旧导入兼容、普通 API token monkeypatch、route 顺序、async 边界、`update_group_name()` DB 行为、`get_bridge` monkeypatch 兼容和父模块边界。
- 修改：`tests/test_api_agent_step_routes_split.py`
  - 将 `group_timing_timer` 的父模块边界断言改为拆分后的 re-export 断言。
- 创建：`api/group_utility_routes.py`
  - 承载 group utility / legacy timing request model 与 3 个 endpoint。
  - 定义 `_current_bridge_provider()`，兼容旧 `api.routes.get_bridge` monkeypatch。
  - 不导入父模块 `api.routes`。
- 修改：`api/routes.py`
  - 删除本地 group utility / legacy timing request model 与 endpoint 实现。
  - 从 `api.group_utility_routes` 导入并 re-export 迁移符号。
  - 在 `group_message()` 之后、`agent_step_router` 之前 include `group_utility_router`。
- 修改：`.Codex/plans/api-group-utility-routes-split.md`
  - 文档收口时勾选执行记录和验收清单。
- 修改：`docs/todo.md`
  - 文档收口时记录 P3 普通 API 第九刀进展。
- 修改：`docs/plan_walkthrough.md`
  - 文档收口时追加 2026-06-22 阶段记录。

## 任务 1：补普通 API group utility route split 红灯测试并提交

**文件：**

- 创建：`tests/test_api_group_utility_routes_split.py`
- 修改：`tests/test_api_agent_step_routes_split.py`

- [x] **步骤 1：创建 group utility split 测试文件**

创建 `tests/test_api_group_utility_routes_split.py`：

```python
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.database import User


_GROUP_UTILITY_ROUTE_SIGNATURES = (
    ("POST", "/api/v1/update_group_name"),
    ("POST", "/api/v1/group_timing"),
    ("POST", "/api/v1/group_timing/timer"),
)

_GROUP_UTILITY_ROUTE_EXPORTS = (
    "UpdateGroupNameRequest",
    "GroupTimingRequest",
    "GroupTimingTimerRequest",
    "_build_group_timing_context",
    "update_group_name",
    "group_timing_deprecated",
    "group_timing_timer",
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


def test_api_group_utility_routes_are_registered_from_split_module():
    for method, path in _GROUP_UTILITY_ROUTE_SIGNATURES:
        routes = _api_routes_for(path, method)

        assert routes, f"missing route: {method} {path}"
        assert {route.endpoint.__module__ for route in routes} == {"api.group_utility_routes"}


def test_legacy_api_routes_group_utility_imports_still_work():
    from api import group_utility_routes
    from api import routes

    for name in _GROUP_UTILITY_ROUTE_EXPORTS:
        assert getattr(routes, name) is getattr(group_utility_routes, name)

    body = routes.GroupTimingTimerRequest(group_id="123", generation=7)
    assert body.group_id == "123"
    assert body.generation == 7
    assert body.timer_fired is True


def test_split_group_utility_routes_use_legacy_api_token_monkeypatch(monkeypatch):
    from server import app

    monkeypatch.setattr("api.routes.NANOBOT_API_TOKEN", "split-token")

    with TestClient(app) as test_client:
        ok = test_client.post(
            "/api/v1/update_group_name",
            json={"group_id": "123", "group_name": "群工具测试"},
            headers={"Authorization": "Bearer split-token"},
        )
        wrong = test_client.post(
            "/api/v1/update_group_name",
            json={"group_id": "123", "group_name": "群工具测试"},
            headers={"Authorization": "Bearer wrong"},
        )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_api_group_utility_routes_are_not_registered_twice():
    for method, path in _GROUP_UTILITY_ROUTE_SIGNATURES:
        assert len(_api_routes_for(path, method)) == 1, f"{method} {path}"


def test_api_group_utility_routes_do_not_import_parent_routes_or_sync_awaitable():
    source = Path("api/group_utility_routes.py").read_text(encoding="utf-8")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_group_utility_routes_keep_order_between_group_message_and_agent_step():
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


def test_group_utility_async_boundaries_remain_explicit():
    from api import group_utility_routes
    from api import routes

    assert not inspect.iscoroutinefunction(group_utility_routes.update_group_name)
    assert inspect.iscoroutinefunction(group_utility_routes.group_timing_deprecated)
    assert inspect.iscoroutinefunction(group_utility_routes.group_timing_timer)
    assert not inspect.iscoroutinefunction(routes.update_group_name)
    assert inspect.iscoroutinefunction(routes.group_timing_deprecated)
    assert inspect.iscoroutinefunction(routes.group_timing_timer)


def test_update_group_name_keeps_group_user_id_normalization(db_session):
    from api import group_utility_routes

    group_utility_routes.update_group_name(
        group_utility_routes.UpdateGroupNameRequest(group_id="123", group_name="旧群名"),
        db=db_session,
        _auth=None,
    )
    created = db_session.query(User).filter(User.id == "group_123").one()
    assert created.name == "旧群名"

    group_utility_routes.update_group_name(
        group_utility_routes.UpdateGroupNameRequest(group_id="123", group_name="新群名"),
        db=db_session,
        _auth=None,
    )
    assert db_session.query(User).filter(User.id == "group_123").one().name == "新群名"

    group_utility_routes.update_group_name(
        group_utility_routes.UpdateGroupNameRequest(group_id="group_123", group_name="带前缀群名"),
        db=db_session,
        _auth=None,
    )
    assert db_session.query(User).filter(User.id == "group_123").one().name == "带前缀群名"
    assert db_session.query(User).filter(User.id == "group_group_123").first() is None


@pytest.mark.asyncio
async def test_group_timing_timer_uses_legacy_routes_get_bridge_monkeypatch(monkeypatch, db_session):
    from api import group_utility_routes
    from api import routes

    class FakeRuntime:
        _states = {}

        async def handle_timer_fired(self, *args, **kwargs):
            return {"action": "continue", "pending_text": "你好"}

        def note_bot_replied(self, group_id):
            raise AssertionError("empty fake bridge reply should not mark bot replied")

    class FakeBridge:
        async def handle_message(self, message, *, session_id, user_id, metadata):
            assert message == "<user_input>\n你好\n</user_input>"
            assert session_id == "group_123"
            assert user_id == "group_123"
            assert metadata["group_id"] == "123"
            return ""

    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: FakeRuntime())
    monkeypatch.setattr(routes, "get_bridge", lambda: FakeBridge())

    result = await group_utility_routes.group_timing_timer(
        group_utility_routes.GroupTimingTimerRequest(group_id="123", generation=1),
        db=db_session,
        _auth=None,
    )

    assert result["action"] == "continue"
    assert result["reply"] == ""
    assert result["reply_meta"] is None
    assert result["group_id"] == "123"
```

- [x] **步骤 2：调整 Agent Step split 父模块边界测试**

修改 `tests/test_api_agent_step_routes_split.py` 末尾测试：

```python
def test_chat_and_group_boundaries_stay_in_parent_routes():
    from api import group_utility_routes
    from api import routes

    assert routes.proxy_chat.__module__ == "api.routes"
    assert routes.group_message.__module__ == "api.routes"
    assert routes._persist_chat_turn.__module__ == "api.routes"
    assert routes._safe_meta.__module__ == "api.routes"
    assert routes.group_timing_timer is group_utility_routes.group_timing_timer
```

- [x] **步骤 3：运行红灯测试**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api_group_utility_routes_split.py \
  tests/test_api_agent_step_routes_split.py
```

预期：FAIL。关键失败点应包含 `api.group_utility_routes` 不存在、group utility endpoint 仍注册在 `api.routes`，或 `api.routes.group_timing_timer` 尚未 re-export 新模块对象。

- [x] **步骤 4：提交红灯测试**

运行：

```bash
git add tests/test_api_group_utility_routes_split.py tests/test_api_agent_step_routes_split.py
git diff --cached --check
git commit -m "test(普通API): 锁定群工具路由拆分契约"
```

## 任务 2：实现 group utility 路由拆分并提交

**文件：**

- 创建：`api/group_utility_routes.py`
- 修改：`api/routes.py`

- [x] **步骤 1：创建 `api/group_utility_routes.py`**

创建文件骨架：

```python
"""普通 API 群工具与 legacy timing 路由。"""
from __future__ import annotations

import logging
import sys
import time as _time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.common_auth import verify_token
from app.group_ingress import helpers as group_ingress_helpers
from core.context_builder import (
    build_chat_context,
    build_timing_recent_context,
    sanitize_prompt_text,
)
from core.database import User, get_db, release_clean_session_transaction
from core.group_runtime.ids import normalize_group_session_id as _normalize_group_session_id
from core.identity import build_identity_vars
from nanobot_kt.bridge import get_bridge as _default_get_bridge

logger = logging.getLogger("nanobot.routes.group_utility")
router = APIRouter(tags=["group-utility"])
MAX_QUERY_CHARS = 2000
```

- [x] **步骤 2：添加旧 `get_bridge` monkeypatch provider**

在 `api/group_utility_routes.py` 中加入：

```python
def _current_bridge_provider():
    routes = sys.modules.get("api.routes")
    if routes is not None and hasattr(routes, "get_bridge"):
        return getattr(routes, "get_bridge")
    return _default_get_bridge
```

`group_timing_timer()` 中只调用 `_current_bridge_provider()()`，不直接调用 `_default_get_bridge()`。

- [x] **步骤 3：迁移 request model 与 `update_group_name()`**

从 `api/routes.py` 搬入：

```python
class UpdateGroupNameRequest(BaseModel):
    group_id: str
    group_name: str


@router.post("/update_group_name")
def update_group_name(
    req: UpdateGroupNameRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    """更新 users 表的 name 字段（群聊也用 users 表，id=group_xxx）。"""
    user_id = f"group_{req.group_id}" if not req.group_id.startswith("group_") else req.group_id
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.name = req.group_name
    else:
        db.add(User(id=user_id, name=req.group_name))
    db.commit()
    return {"status": "ok"}
```

- [x] **步骤 4：迁移 `GroupTimingRequest` 与 `_build_group_timing_context()`**

从 `api/routes.py` 搬入：

```python
class GroupTimingRequest(BaseModel):
    group_id: str
    sender_id: str = ""
    sender_name: str = ""
    message: str
    pending_messages: list[dict] = []
    message_id: str | None = None
    session_name: str | None = None
    is_reply_to_bot: bool = False
    trigger_reason: str = ""
    bot_aliases: list[str] = []


def _build_group_timing_context(
    req: GroupTimingRequest | None = None,
    **kwargs,
) -> str:
    """[DEPRECATED] wrapper——实际逻辑在 core.timing_runtime.GroupRuntime._build_timing_context()。"""
    from core.timing_runtime import PendingMessage as RPM, GroupRuntime

    if req is not None:
        pending = [
            RPM(
                sender_id=p.get("sender_id", ""),
                sender_name=p.get("sender_name", ""),
                message=p.get("message", ""),
                is_reply_to_bot=req.is_reply_to_bot or p.get("is_reply_to_bot", False),
            )
            for p in (req.pending_messages or [])
        ]
        return GroupRuntime._build_timing_context(
            pending=pending,
            session_name=req.session_name or "",
            trigger_reason=req.trigger_reason or "",
            bot_aliases=list(req.bot_aliases or []),
        )
    return GroupRuntime._build_timing_context(**kwargs)
```

- [x] **步骤 5：迁移 `GroupTimingTimerRequest` 与 `group_timing_deprecated()`**

从 `api/routes.py` 搬入：

```python
class GroupTimingTimerRequest(BaseModel):
    """timer_fired 模式——wait 到期后 QQbot 回调。"""

    group_id: str
    generation: int
    timer_fired: bool = True
    trigger_reason: str = ""


@router.post("/group_timing")
async def group_timing_deprecated(req: GroupTimingRequest, _auth=Depends(verify_token)):
    """[DEPRECATED] 使用 /group/message 替代。"""
    logger.warning("[DEPRECATED] /group_timing called by group=%s — migrate to /group/message", req.group_id)
    from core.timing_runtime import get_group_runtime

    runtime = get_group_runtime()
    result = await runtime.process_message(
        req.group_id,
        {
            "sender_id": req.sender_id,
            "sender_name": req.sender_name,
            "message": req.message,
            "message_id": req.message_id or "",
            "is_reply_to_bot": req.is_reply_to_bot,
        },
        session_name=req.session_name or "",
        bot_aliases=list(req.bot_aliases or []),
        trigger_reason=req.trigger_reason or "mentioned",
    )
    return result
```

- [x] **步骤 6：迁移并改写 `group_timing_timer()` 的父模块私有依赖**

从 `api/routes.py` 搬入 `group_timing_timer()`，并执行以下替换：

```python
bridge = _current_bridge_provider()()
memory_header, history_messages, _ctx_debug = build_chat_context(
    db,
    group_user_id,
    user_id=group_user_id,
    is_group=True,
    group_id=req.group_id,
    exclude_message_ids=source_message_ids,
)
chat_query = sanitize_prompt_text(
    str(result.get("pending_text") or ""),
    max_chars=MAX_QUERY_CHARS,
)
if not chat_query.strip():
    chat_query = "timer 触发回复"
result["reply"] = group_ingress_helpers.format_group_reply_for_transport(answer, max_chars=4000)
reply_meta_timer = group_ingress_helpers.pop_bridge_reply_meta(bridge, group_user_id)
duplicate = group_ingress_helpers.find_recent_duplicate_group_reply(db, group_user_id, answer)
group_ingress_helpers.log_group_no_reply(db, group_user_id, chat_query, agent_result, "")
group_ingress_helpers.persist_group_bridge_reply(
    db,
    group_user_id=group_user_id,
    sender_name="",
    session_name="",
    query=chat_query,
    answer=answer,
    bot_name=timer_bot_name or "nanobot",
    source_message_ids=source_message_ids,
    reply_meta=reply_meta_timer,
)
agent_result = group_ingress_helpers.derive_group_agent_result(bridge, group_user_id, reply_meta_timer)
```

保留旧行为：

- `release_clean_session_transaction(db, label="group_timer_before_runtime", logger=logger)`。
- `release_clean_session_transaction(db, label="group_timer_before_bridge", logger=logger)`。
- `build_timing_recent_context(db, group_user_id, limit=5)`。
- runtime `_states` 中的 `bot_id`、`bot_name`、`bot_aliases` 读取。
- `build_identity_vars()` 输出并入 `bridge_meta`。
- 非空且非重复回复后调用 `runtime.note_bot_replied(req.group_id)`。
- bridge 异常时返回 `{"action": "no_reply"}` 语义。

- [x] **步骤 7：修改父模块 import 与 re-export**

在 `api/routes.py` 的 split router import 区加入：

```python
from api.group_utility_routes import (
    GroupTimingRequest,
    GroupTimingTimerRequest,
    UpdateGroupNameRequest,
    _build_group_timing_context,
    group_timing_deprecated,
    group_timing_timer,
    router as group_utility_router,
    update_group_name,
)
```

删除父模块中本地 `UpdateGroupNameRequest` 到 `group_timing_timer()` 的定义块。

- [x] **步骤 8：在原位置 include 子 router**

在 `api/routes.py` 中保持顺序：

```python
router.include_router(group_utility_router)
router.include_router(agent_step_router)


@router.post("/chat")
async def proxy_chat(
```

不要把 `group_utility_router` 放到文件尾部 include 区，否则 route order 会变化。

- [x] **步骤 9：运行拆分绿灯测试**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api_group_utility_routes_split.py \
  tests/test_api_agent_step_routes_split.py
```

预期：PASS，且输出包含 `tests/test_api_group_utility_routes_split.py` 与 `tests/test_api_agent_step_routes_split.py` 全部通过。

- [x] **步骤 10：运行 timing 行为回归**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/test_timing_gate.py::TestRouteContext::test_group_timing_context_sanitizes_pending_messages \
  tests/test_api.py::test_group_timer_returns_full_html_reply_without_truncation \
  tests/test_api.py::test_group_message_returns_full_html_reply_without_truncation \
  tests/test_api_routes_group_helper_facade.py
```

预期：PASS，确认 legacy timing context、timer HTML 完整回复、群消息 HTML 完整回复和父模块 helper facade 均未回退。

- [x] **步骤 11：运行普通 API split 相邻回归与 async 策略**

运行：

```bash
python -B -m pytest -q -p no:cacheprovider \
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

- [x] **步骤 12：运行静态检查**

运行：

```bash
python -B -m py_compile api/routes.py api/group_utility_routes.py tests/test_api_group_utility_routes_split.py tests/test_api_agent_step_routes_split.py
rg -n "from api\.routes|import api\.routes|asyncio\.run|run_awaitable_sync" api/group_utility_routes.py
git diff --check -- api/routes.py api/group_utility_routes.py tests/test_api_group_utility_routes_split.py tests/test_api_agent_step_routes_split.py
wc -l api/routes.py api/group_utility_routes.py tests/test_api_group_utility_routes_split.py
```

预期：

- `py_compile` 退出码为 0。
- `rg` 无命中，退出码为 1。
- `git diff --check` 无输出。
- `api/routes.py` 行数下降。

- [x] **步骤 13：运行全量回归**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：0 failures。若失败集中在环境或历史脏项，记录完整失败测试名和错误摘要，不提交实现。

- [x] **步骤 14：提交拆分实现**

运行：

```bash
git add api/group_utility_routes.py api/routes.py tests/test_api_group_utility_routes_split.py tests/test_api_agent_step_routes_split.py
git diff --cached --check
git commit -m "refactor(普通API): 拆分群工具路由"
```

## 任务 3：文档收口并提交

**文件：**

- 修改：`.Codex/plans/api-group-utility-routes-split.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [x] **步骤 1：更新本计划执行记录**

在「当前状态」之后追加「执行记录」小节，写入红灯、Split 绿灯、timing 行为回归、普通 API split 相邻回归、静态检查、行数检查和全量回归的真实命令与真实输出摘要。每条记录必须包含命令、退出结论、通过或失败统计；红灯记录还要列出关键失败点。

同时将已完成步骤的复选框从 `- [ ]` 改为 `- [x]`。

- [x] **步骤 2：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」记录中追加本阶段结果：

```markdown
- [x] 第九刀：普通 API group utility / legacy timing 路由拆分到 `api/group_utility_routes.py`，迁移 `/update_group_name`、`/group_timing`、`/group_timing/timer`，保留 `/chat`、`/group/message` 和 `/health` 在父模块。
```

如果 `docs/todo.md` 已经使用不同编号，按现有编号顺延，不改写无关条目。

- [x] **步骤 3：更新 `docs/plan_walkthrough.md`**

追加 2026-06-22 记录，格式与文件现有条目一致，内容包含：

```markdown
### 2026-06-22：普通 API group utility / legacy timing 路由拆分

- 计划：`.Codex/plans/api-group-utility-routes-split.md`
- 设计：`docs/superpowers/specs/2026-06-22-api-group-utility-routes-split-design.md`
- 提交：`test(普通API): 锁定群工具路由拆分契约`、`refactor(普通API): 拆分群工具路由`
- 结果：`api/group_utility_routes.py` 承载 3 个 legacy group utility endpoint，`api/routes.py` 继续保留 `/chat`、`/group/message` 与 `/health`。
- 验证：记录本阶段实际通过的定向测试、相邻回归和全量回归结果。
```

- [x] **步骤 4：运行文档检查与定向回归**

运行：

```bash
rg -n "T[O]DO|待[定]|后续实[现]|占[位]|\x{FFFD}" .Codex/plans/api-group-utility-routes-split.md docs/todo.md docs/plan_walkthrough.md
git diff --check -- .Codex/plans/api-group-utility-routes-split.md docs/todo.md docs/plan_walkthrough.md
python -B -m pytest -q -p no:cacheprovider \
  tests/test_api_group_utility_routes_split.py \
  tests/test_api.py::test_group_timer_returns_full_html_reply_without_truncation \
  tests/test_asyncio_run_policy.py
```

预期：

- `rg` 无命中，退出码为 1。
- `git diff --check` 无输出。
- pytest 0 failures。

- [x] **步骤 5：提交文档收口**

运行：

```bash
git add .Codex/plans/api-group-utility-routes-split.md docs/todo.md docs/plan_walkthrough.md
git diff --cached --check
git commit -m "docs(计划): 收口群工具路由拆分"
```

## 最终验收清单

- [x] `api/group_utility_routes.py` 存在，并且不包含 `from api.routes`、`import api.routes`、`asyncio.run` 或 `run_awaitable_sync`。
- [x] `POST /api/v1/update_group_name`、`POST /api/v1/group_timing` 与 `POST /api/v1/group_timing/timer` endpoint module 均为 `api.group_utility_routes`。
- [x] `api.routes` 继续 re-export 迁移符号，旧导入对象与新模块对象相同。
- [x] `api.routes.NANOBOT_API_TOKEN` monkeypatch 继续影响拆分后的 3 个 endpoint。
- [x] `api.routes.get_bridge` monkeypatch 继续影响 `group_timing_timer()`。
- [x] route order 保持 `/group/message` -> `/update_group_name` -> `/group_timing` -> `/group_timing/timer` -> `/render` -> `/chat-step` -> `/chat`。
- [x] `/chat`、`/group/message`、`/health`、`_persist_chat_turn()`、`_safe_meta()`、`_build_multimodal_user_input_text()` 继续留在 `api.routes`。
- [x] `update_group_name()` 继续规范化裸群号为 `group_<id>`，已带 `group_` 前缀时不重复添加。
- [x] timer 行为回归、普通 API split 相邻回归、`tests/test_asyncio_run_policy.py` 与全量 `tests/` 均为 0 failures。
- [x] 每个阶段性改动均已独立 commit，且未暂存历史无关脏项。

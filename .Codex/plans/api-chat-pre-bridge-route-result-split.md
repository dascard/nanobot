# 普通 API Chat Pre-Bridge 路由结果拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把 `api.routes.proxy_chat()` 中的 pre-bridge 决策结果转译拆到 `api/chat_pre_bridge_route_result.py`。

**架构：** 新模块只负责把 `ChatPreBridgeEarlyReturn` / `ChatPreBridgeContinue` 转成 route early response 或 continue context。父模块保留 HTTP route、DB session、callback patch point、persona injection、Prompt Runtime payload、Bridge、SSE、response envelope 和 evolution。

**技术栈：** Python 3.12、dataclass、pytest、pytest-asyncio、源码静态扫描。

---

## 已完成上下文

- [x] 设计文档：`docs/superpowers/specs/2026-06-24-api-chat-pre-bridge-route-result-split-design.md`
- [x] 设计提交：`95e19bc docs(普通API): 设计前置决策结果拆分`
- [x] 计划写入日期：2026-06-24

## 边界与禁止事项

- 保留：`api.routes.proxy_chat.__module__ == "api.routes"`。
- 保留：`/api/v1/chat` route 继续由 `api.routes` 注册。
- 保留：父模块 `_resolve_chat_pre_bridge_decision()` patch point。
- 保留：父模块 `_clone_chat_request()` patch point。
- 保留：父模块 `_persist_chat_turn()` patch point。
- 保留：父模块 `_chat_response_payload()` patch point。
- 保留：父模块 `_finalize_private_buffer()` patch point。
- 保留：guardrail silent 分支继续使用 `persist_req` 持久化自动静默结果。
- 保留：`PersonaInjectionService`、Prompt Runtime payload、Bridge、SSE、non-streaming result 和 evolution 在父模块。
- 禁止：新模块导入 `api.routes`。
- 禁止：新模块导入 FastAPI、`APIRouter`、`StreamingResponse`、`BackgroundTasks` 或 `HTTPException`。
- 禁止：新模块导入 DB model、`SessionLocal`、`UnitOfWork`、Bridge、Prompt Runtime 或 `get_bridge()`。
- 禁止：新模块调用 `db.commit()`、构造 FastAPI response、调用 Bridge 或调用 `build_chat_runtime_payload()`。
- 禁止：改 conversation 结构、历史注入、Prompt Runtime 模板、message envelope、push envelope 或 response envelope。
- 禁止：新增 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。
- 禁止：处理 WebUI / JS。

## 文件职责

- 创建：`api/chat_pre_bridge_route_result.py`
  - 定义 `ChatPreBridgeRouteCallbacks`。
  - 定义 `ChatPreBridgeRouteEarlyResponse`。
  - 定义 `ChatPreBridgeRouteContinue`。
  - 实现 `resolve_pre_bridge_route_result()`。
  - 只通过 callbacks 接收父模块 patch point。
- 修改：`api/routes.py`
  - 导入 `api.chat_pre_bridge_route_result`。
  - 新增 `_chat_pre_bridge_route_callbacks(db)` 薄 wrapper，通过闭包把当前 `db` 绑定到父模块 `_persist_chat_turn()`。
  - 新增 `_resolve_pre_bridge_route_result(db, req, pre_bridge)` 薄 wrapper。
  - 用 wrapper 替换 `proxy_chat()` 中 pre-bridge early / continue / guardrail silent 转译。
- 创建：`tests/test_api_chat_pre_bridge_route_result_split.py`
  - 锁定新模块源码边界。
  - 覆盖 early return 持久化、early return 不持久化、continue 字段展开、guardrail silent、父模块 wrapper patch point。
- 修改：
  - `tests/test_api_group_message_routes_split.py`
  - `tests/test_api_agent_step_routes_split.py`
  - `tests/test_api_history_log_routes_split.py`
  - `tests/test_api_sticker_media_routes_split.py`
  - 将 `api/chat_pre_bridge_route_result.py` 加入 chat split module 扫描清单。
- 修改：`.Codex/plans/api-chat-pre-bridge-route-result-split.md`
  - 随执行更新任务状态、命令输出和提交号。
- 修改：`docs/todo.md`
  - 最终收口时记录 P3 `api/routes.py` pre-bridge route result 拆分进展和行数。
- 修改：`docs/plan_walkthrough.md`
  - 最终收口时追加本阶段提交列表、验证结果和下一步建议。

---

## 任务 1：红灯测试

**文件：**
- 创建：`tests/test_api_chat_pre_bridge_route_result_split.py`
- 修改：`tests/test_api_group_message_routes_split.py`
- 修改：`tests/test_api_agent_step_routes_split.py`
- 修改：`tests/test_api_history_log_routes_split.py`
- 修改：`tests/test_api_sticker_media_routes_split.py`
- 修改：`.Codex/plans/api-chat-pre-bridge-route-result-split.md`

- [x] **步骤 1：创建测试文件基础结构**

创建 `tests/test_api_chat_pre_bridge_route_result_split.py`，写入：

```python
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        user_id="u-route-result",
        session_id="private_u-route-result",
        query="原始问题",
        files=["old.png"],
    )
```

- [x] **步骤 2：新增 fake callbacks helper**

在同一文件追加：

```python
def _callbacks(calls: dict[str, list[Any]]):
    from api.chat_pre_bridge_route_result import ChatPreBridgeRouteCallbacks

    def clone_chat_request(req: Any, **updates: Any) -> Any:
        calls.setdefault("clone", []).append((req, updates))
        data = dict(vars(req))
        data.update(updates)
        return SimpleNamespace(**data)

    def persist_chat_turn(req: Any, answer: str, guardrail_status: str | None = None, **kwargs: Any) -> int:
        calls.setdefault("persist", []).append((req, answer, guardrail_status, kwargs))
        return 7

    def chat_response_payload(req: Any, **kwargs: Any) -> dict[str, Any]:
        calls.setdefault("payload", []).append((req, kwargs))
        return {"payload": kwargs}

    async def finalize_private_buffer(user_id: str, answer: str | None = None, *, clear_window: bool = True) -> None:
        calls.setdefault("finalize", []).append((user_id, answer, clear_window))

    return ChatPreBridgeRouteCallbacks(
        clone_chat_request=clone_chat_request,
        persist_chat_turn=persist_chat_turn,
        chat_response_payload=chat_response_payload,
        finalize_private_buffer=finalize_private_buffer,
    )
```

- [x] **步骤 3：新增源码边界红灯**

追加测试：

```python
def test_chat_pre_bridge_route_result_module_does_not_import_parent_routes_or_runtime_side_effects():
    path = ROOT / "api/chat_pre_bridge_route_result.py"
    assert path.exists()
    source = _source("api/chat_pre_bridge_route_result.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "FastAPI" not in source
    assert "APIRouter" not in source
    assert "StreamingResponse" not in source
    assert "BackgroundTasks" not in source
    assert "HTTPException" not in source
    assert "SessionLocal" not in source
    assert "UnitOfWork" not in source
    assert "ChatLog" not in source
    assert "ConversationTurn" not in source
    assert "db.commit(" not in source
    assert "build_chat_runtime_payload" not in source
    assert "ChatRuntimeInput" not in source
    assert "get_bridge(" not in source
    assert "bridge.handle_message" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
```

- [x] **步骤 4：新增 early return 持久化测试**

追加测试：

```python
@pytest.mark.asyncio
async def test_early_return_persists_when_answer_is_present_and_builds_payload():
    from api.chat_pre_bridge_decision import ChatPreBridgeEarlyReturn
    from api.chat_pre_bridge_route_result import (
        ChatPreBridgeRouteEarlyResponse,
        resolve_pre_bridge_route_result,
    )

    calls: dict[str, list[Any]] = {}
    req = _request()
    pre_bridge = ChatPreBridgeEarlyReturn(
        status="ok",
        reason="casual",
        answer="传输回复",
        source="casual_template",
        intent="寒暄",
        guardrail_status="casual_template",
        persist_answer="原始回复",
        persist_guardrail_status="casual_template",
        persist_timing_meta={"action": "reply_later"},
    )

    result = await resolve_pre_bridge_route_result(
        req,
        pre_bridge,
        callbacks=_callbacks(calls),
    )

    assert isinstance(result, ChatPreBridgeRouteEarlyResponse)
    assert calls["persist"] == [
        (
            req,
            "原始回复",
            "casual_template",
            {"timing_meta": {"action": "reply_later"}},
        )
    ]
    assert calls["payload"] == [
        (
            req,
            {
                "status": "ok",
                "reason": "casual",
                "answer": "传输回复",
                "source": "casual_template",
                "intent": "寒暄",
                "guardrail_status": "casual_template",
                "include_answer_chunks": True,
            },
        )
    ]
    assert result.payload["payload"]["answer"] == "传输回复"
```

- [x] **步骤 5：新增 early return 不持久化测试**

追加测试：

```python
@pytest.mark.asyncio
async def test_early_return_without_persist_only_builds_payload():
    from api.chat_pre_bridge_decision import ChatPreBridgeEarlyReturn
    from api.chat_pre_bridge_route_result import resolve_pre_bridge_route_result

    calls: dict[str, list[Any]] = {}
    req = _request()
    pre_bridge = ChatPreBridgeEarlyReturn(
        status="silent",
        reason="private_buffer_follower",
        persist_answer=None,
    )

    result = await resolve_pre_bridge_route_result(
        req,
        pre_bridge,
        callbacks=_callbacks(calls),
    )

    assert calls.get("persist") is None
    assert calls["payload"][0][1] == {
        "status": "silent",
        "reason": "private_buffer_follower",
        "answer": "",
        "source": "",
        "intent": "",
        "guardrail_status": None,
        "include_answer_chunks": True,
    }
    assert result.payload["payload"]["status"] == "silent"
```

- [x] **步骤 6：新增 continue 字段展开测试**

追加测试：

```python
@pytest.mark.asyncio
async def test_continue_outcome_clones_persist_request_and_exposes_fields():
    from api.chat_pre_bridge_decision import ChatPreBridgeContinue
    from api.chat_pre_bridge_route_result import (
        ChatPreBridgeRouteContinue,
        resolve_pre_bridge_route_result,
    )

    calls: dict[str, list[Any]] = {}
    req = _request()
    decision = SimpleNamespace(action="reply_now")
    pre_bridge = ChatPreBridgeContinue(
        final_query="合并问题",
        final_files=["new.png"],
        private_decision=decision,
        private_timing_meta={"action": "reply_now"},
        guardrail_status="safe",
        classifier_ran=True,
    )

    result = await resolve_pre_bridge_route_result(
        req,
        pre_bridge,
        callbacks=_callbacks(calls),
    )

    assert isinstance(result, ChatPreBridgeRouteContinue)
    assert calls["clone"] == [(req, {"query": "合并问题", "files": ["new.png"]})]
    assert result.final_query == "合并问题"
    assert result.final_files == ["new.png"]
    assert result.private_decision is decision
    assert result.private_timing_meta == {"action": "reply_now"}
    assert result.guardrail_status == "safe"
    assert result.classifier_ran is True
    assert result.persist_req.query == "合并问题"
    assert result.persist_req.files == ["new.png"]
    assert calls.get("persist") is None
    assert calls.get("payload") is None
```

- [x] **步骤 7：新增 guardrail silent 转译测试**

追加测试：

```python
@pytest.mark.asyncio
async def test_guardrail_silent_finalizes_buffer_persists_silent_answer_and_returns_payload():
    from api.chat_pre_bridge_decision import ChatPreBridgeContinue
    from api.chat_pre_bridge_route_result import (
        ChatPreBridgeRouteEarlyResponse,
        resolve_pre_bridge_route_result,
    )

    calls: dict[str, list[Any]] = {}
    req = _request()
    pre_bridge = ChatPreBridgeContinue(
        final_query="合并后问题",
        final_files=["safe.png"],
        private_decision=SimpleNamespace(action="reply_now"),
        private_timing_meta={"action": "reply_now"},
        guardrail_status="silent",
        classifier_ran=True,
    )

    result = await resolve_pre_bridge_route_result(
        req,
        pre_bridge,
        callbacks=_callbacks(calls),
    )

    assert isinstance(result, ChatPreBridgeRouteEarlyResponse)
    persist_req = calls["clone"][0][0]
    cloned_req = calls["persist"][0][0]
    assert persist_req is req
    assert cloned_req.query == "合并后问题"
    assert cloned_req.files == ["safe.png"]
    assert calls["finalize"] == [("u-route-result", None, True)]
    assert calls["persist"] == [
        (
            cloned_req,
            "（数据中转，自动静默）",
            "silent",
            {"timing_meta": {"action": "reply_now"}},
        )
    ]
    assert calls["payload"][0][1] == {
        "status": "silent",
        "reason": "guardrail_silent",
        "guardrail_status": "silent",
        "include_answer_chunks": True,
    }
    assert result.payload["payload"]["reason"] == "guardrail_silent"
```

- [x] **步骤 8：新增父模块 wrapper 测试**

追加测试：

```python
@pytest.mark.asyncio
async def test_parent_pre_bridge_route_result_wrapper_remains_patchable(monkeypatch):
    from api import chat_pre_bridge_route_result
    from api import routes

    calls: list[tuple[Any, Any, Any]] = []

    async def fake_resolver(req: Any, pre_bridge: Any, *, callbacks: Any) -> Any:
        calls.append((req, pre_bridge, callbacks))
        return chat_pre_bridge_route_result.ChatPreBridgeRouteEarlyResponse(
            payload={"status": "patched"}
        )

    monkeypatch.setattr(chat_pre_bridge_route_result, "resolve_pre_bridge_route_result", fake_resolver)
    req = _request()
    pre_bridge = object()

    assert routes._chat_pre_bridge_route_callbacks.__module__ == "api.routes"
    assert routes._resolve_pre_bridge_route_result.__module__ == "api.routes"

    result = await routes._resolve_pre_bridge_route_result(req, pre_bridge)

    assert result.payload == {"status": "patched"}
    assert calls[0][0] is req
    assert calls[0][1] is pre_bridge
    assert calls[0][2].clone_chat_request is routes._clone_chat_request
    assert calls[0][2].persist_chat_turn is routes._persist_chat_turn
    assert calls[0][2].chat_response_payload is routes._chat_response_payload
    assert calls[0][2].finalize_private_buffer is routes._finalize_private_buffer
```

- [x] **步骤 9：更新 chat split 扫描清单**

在以下文件的 `test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable()` 清单中追加 `"api/chat_pre_bridge_route_result.py"`：

```text
tests/test_api_group_message_routes_split.py
tests/test_api_agent_step_routes_split.py
tests/test_api_history_log_routes_split.py
tests/test_api_sticker_media_routes_split.py
```

- [x] **步骤 10：运行红灯测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
tests/test_api_chat_pre_bridge_route_result_split.py \
tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
-v
```

预期：测试失败，失败原因是 `api/chat_pre_bridge_route_result.py` 不存在或父模块 wrapper 不存在。

实际结果（2026-06-24）：`10 failed, 1 warning in 6.84s`。失败原因符合红灯预期：新模块 `api/chat_pre_bridge_route_result.py` 尚不存在，父模块 `_chat_pre_bridge_route_callbacks()` / `_resolve_pre_bridge_route_result()` 尚不存在，四个 chat split 扫描清单因此读取新模块失败。

- [x] **步骤 11：提交红灯测试**

```bash
git add tests/test_api_chat_pre_bridge_route_result_split.py tests/test_api_group_message_routes_split.py tests/test_api_agent_step_routes_split.py tests/test_api_history_log_routes_split.py tests/test_api_sticker_media_routes_split.py .Codex/plans/api-chat-pre-bridge-route-result-split.md
git commit -m "test(普通API): 锁定前置决策结果契约"
```

---

## 任务 2：新增 route result helper

**文件：**
- 创建：`api/chat_pre_bridge_route_result.py`
- 修改：`.Codex/plans/api-chat-pre-bridge-route-result-split.md`

- [x] **步骤 1：新增模块与 dataclass**

创建 `api/chat_pre_bridge_route_result.py`：

```python
"""Chat pre-bridge route result helper。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from api import chat_pre_bridge_decision


@dataclass(frozen=True)
class ChatPreBridgeRouteCallbacks:
    clone_chat_request: Callable
    persist_chat_turn: Callable
    chat_response_payload: Callable
    finalize_private_buffer: Callable


@dataclass(frozen=True)
class ChatPreBridgeRouteEarlyResponse:
    payload: dict[str, Any]


@dataclass(frozen=True)
class ChatPreBridgeRouteContinue:
    final_query: str
    final_files: list[str]
    private_decision: Any | None
    private_timing_meta: dict[str, Any] | None
    guardrail_status: str | None
    classifier_ran: bool
    persist_req: Any
```

- [x] **步骤 2：实现 early return 转译**

追加：

```python
async def _resolve_early_return(
    req: Any,
    pre_bridge: chat_pre_bridge_decision.ChatPreBridgeEarlyReturn,
    *,
    callbacks: ChatPreBridgeRouteCallbacks,
) -> ChatPreBridgeRouteEarlyResponse:
    if pre_bridge.persist_answer is not None:
        callbacks.persist_chat_turn(
            req,
            pre_bridge.persist_answer,
            guardrail_status=pre_bridge.persist_guardrail_status,
            timing_meta=pre_bridge.persist_timing_meta,
        )

    return ChatPreBridgeRouteEarlyResponse(
        payload=callbacks.chat_response_payload(
            req,
            status=pre_bridge.status,
            reason=pre_bridge.reason,
            answer=pre_bridge.answer,
            source=pre_bridge.source,
            intent=pre_bridge.intent,
            guardrail_status=pre_bridge.guardrail_status,
            include_answer_chunks=True,
        )
    )
```

- [x] **步骤 3：实现 continue 与 guardrail silent 转译**

追加：

```python
async def _resolve_continue(
    req: Any,
    pre_bridge: chat_pre_bridge_decision.ChatPreBridgeContinue,
    *,
    callbacks: ChatPreBridgeRouteCallbacks,
) -> ChatPreBridgeRouteEarlyResponse | ChatPreBridgeRouteContinue:
    persist_req = callbacks.clone_chat_request(
        req,
        query=pre_bridge.final_query,
        files=pre_bridge.final_files,
    )

    if pre_bridge.classifier_ran and pre_bridge.guardrail_status == "silent":
        await callbacks.finalize_private_buffer(req.user_id)
        callbacks.persist_chat_turn(
            persist_req,
            "（数据中转，自动静默）",
            pre_bridge.guardrail_status,
            timing_meta=pre_bridge.private_timing_meta,
        )
        return ChatPreBridgeRouteEarlyResponse(
            payload=callbacks.chat_response_payload(
                req,
                status="silent",
                reason="guardrail_silent",
                guardrail_status=pre_bridge.guardrail_status,
                include_answer_chunks=True,
            )
        )

    return ChatPreBridgeRouteContinue(
        final_query=pre_bridge.final_query,
        final_files=pre_bridge.final_files,
        private_decision=pre_bridge.private_decision,
        private_timing_meta=pre_bridge.private_timing_meta,
        guardrail_status=pre_bridge.guardrail_status,
        classifier_ran=pre_bridge.classifier_ran,
        persist_req=persist_req,
    )
```

- [x] **步骤 4：实现公共 resolver**

追加：

```python
async def resolve_pre_bridge_route_result(
    req: Any,
    pre_bridge: Any,
    *,
    callbacks: ChatPreBridgeRouteCallbacks,
) -> ChatPreBridgeRouteEarlyResponse | ChatPreBridgeRouteContinue:
    if isinstance(pre_bridge, chat_pre_bridge_decision.ChatPreBridgeEarlyReturn):
        return await _resolve_early_return(req, pre_bridge, callbacks=callbacks)
    if isinstance(pre_bridge, chat_pre_bridge_decision.ChatPreBridgeContinue):
        return await _resolve_continue(req, pre_bridge, callbacks=callbacks)
    raise TypeError(f"unsupported pre_bridge result: {type(pre_bridge)!r}")
```

- [x] **步骤 5：运行 helper 定向测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_pre_bridge_route_result_split.py -v
```

预期：helper 行为测试通过，父模块 wrapper 测试仍失败。

实际结果（2026-06-24）：`5 passed, 1 failed, 1 warning in 6.49s`。新模块源码边界、early return、continue 和 guardrail silent 行为测试已通过；剩余失败为父模块尚未提供 `_chat_pre_bridge_route_callbacks()`。

- [x] **步骤 6：提交 helper**

```bash
git add api/chat_pre_bridge_route_result.py .Codex/plans/api-chat-pre-bridge-route-result-split.md
git commit -m "refactor(普通API): 增加前置决策结果助手"
```

---

## 任务 3：接入父模块

**文件：**
- 修改：`api/routes.py`
- 修改：`.Codex/plans/api-chat-pre-bridge-route-result-split.md`

- [x] **步骤 1：导入新模块**

在 `from api import (` 列表中加入：

```python
    chat_pre_bridge_route_result,
```

- [x] **步骤 2：新增父模块 callbacks wrapper**

在 `_persist_chat_turn()` 后增加：

```python
def _chat_pre_bridge_route_callbacks(
    db: Session,
) -> chat_pre_bridge_route_result.ChatPreBridgeRouteCallbacks:
    def persist_chat_turn(
        req: ChatProxyRequest,
        answer: str,
        guardrail_status: str | None = None,
        **kwargs: Any,
    ) -> int:
        return _persist_chat_turn(
            db,
            req,
            answer,
            guardrail_status=guardrail_status,
            **kwargs,
        )

    return chat_pre_bridge_route_result.ChatPreBridgeRouteCallbacks(
        clone_chat_request=_clone_chat_request,
        persist_chat_turn=persist_chat_turn,
        chat_response_payload=_chat_response_payload,
        finalize_private_buffer=_finalize_private_buffer,
    )
```

- [x] **步骤 3：新增父模块 resolver wrapper**

追加：

```python
async def _resolve_pre_bridge_route_result(
    db: Session,
    req: ChatProxyRequest,
    pre_bridge: Any,
) -> chat_pre_bridge_route_result.ChatPreBridgeRouteEarlyResponse | chat_pre_bridge_route_result.ChatPreBridgeRouteContinue:
    return await chat_pre_bridge_route_result.resolve_pre_bridge_route_result(
        req,
        pre_bridge,
        callbacks=_chat_pre_bridge_route_callbacks(db),
    )
```

- [x] **步骤 4：替换 `proxy_chat()` 内联转译逻辑**

把 `proxy_chat()` 中 `if isinstance(pre_bridge, ChatPreBridgeEarlyReturn)`、continue 字段展开和 guardrail silent 分支替换为：

```python
    pre_bridge_route = await _resolve_pre_bridge_route_result(db, req, pre_bridge)
    if isinstance(pre_bridge_route, chat_pre_bridge_route_result.ChatPreBridgeRouteEarlyResponse):
        return pre_bridge_route.payload

    final_query = pre_bridge_route.final_query
    final_files = pre_bridge_route.final_files
    _private_decision = pre_bridge_route.private_decision
    private_timing_meta = pre_bridge_route.private_timing_meta
    guardrail_status = pre_bridge_route.guardrail_status
    _classifier_ran = pre_bridge_route.classifier_ran
    persist_req = pre_bridge_route.persist_req
```

- [x] **步骤 5：运行定向测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_pre_bridge_route_result_split.py -v
```

预期：全部通过。

实际结果（2026-06-24）：`6 passed, 1 warning in 0.96s`。父模块 wrapper 已通过 `db` 绑定持久化 callback，仍保留 `_clone_chat_request()`、`_persist_chat_turn()`、`_chat_response_payload()` 和 `_finalize_private_buffer()` 的父模块 patch point。

- [x] **步骤 6：提交父模块接入**

```bash
git add api/routes.py .Codex/plans/api-chat-pre-bridge-route-result-split.md
git commit -m "refactor(普通API): 接入前置决策结果助手"
```

---

## 任务 4：验证和文档收口

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/api-chat-pre-bridge-route-result-split.md`

- [x] **步骤 1：运行 split 扫描和相邻回归**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
-v
```

运行：

```bash
python -B -m pytest -p no:cacheprovider \
tests/test_api_chat_pre_bridge_decision_split.py \
tests/test_api_chat_non_streaming_result_split.py \
tests/test_api.py::test_proxy_chat_persists_private_timing_scoring_meta \
tests/test_api.py::test_proxy_chat_no_reply_persists_private_timing_scoring_meta \
-v
```

预期：全部通过。

实际结果（2026-06-24）：

- chat split 扫描：`4 passed, 1 warning in 1.19s`。
- 相邻回归：`15 passed, 21 warnings in 2.36s`。

- [x] **步骤 2：运行静态检查**

运行：

```bash
python -m compileall api/routes.py api/chat_pre_bridge_route_result.py -q
git diff --check -- \
api/routes.py \
api/chat_pre_bridge_route_result.py \
tests/test_api_chat_pre_bridge_route_result_split.py \
tests/test_api_group_message_routes_split.py \
tests/test_api_agent_step_routes_split.py \
tests/test_api_history_log_routes_split.py \
tests/test_api_sticker_media_routes_split.py \
.Codex/plans/api-chat-pre-bridge-route-result-split.md
wc -l api/routes.py api/chat_pre_bridge_route_result.py tests/test_api_chat_pre_bridge_route_result_split.py
```

预期：compileall 退出码 0；`git diff --check` 无输出；记录行数。

实际结果（2026-06-24）：

- `python -m compileall api/routes.py api/chat_pre_bridge_route_result.py -q` 退出码 0。
- `git diff --check` 无输出。
- `wc -l api/routes.py api/chat_pre_bridge_route_result.py tests/test_api_chat_pre_bridge_route_result_split.py`
  -> `1013 api/routes.py`、`115 api/chat_pre_bridge_route_result.py`、`311 tests/test_api_chat_pre_bridge_route_result_split.py`。
- `rg -n "asyncio\\.run|run_awaitable_sync" api/routes.py api/chat_pre_bridge_route_result.py tests/test_api_chat_pre_bridge_route_result_split.py` 仅命中新测试中的禁止断言字符串，生产代码无命中。
- 验证中发现初版 wrapper 让 `api/routes.py` 从 1020 行增到 1028 行，已随 `4794458 refactor(普通API): 压缩前置结果包装器` 压缩 wrapper；最终为 1013 行。

- [x] **步骤 3：运行全量测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：0 failures。

实际结果（2026-06-24）：`1788 passed, 6 skipped, 139 warnings in 124.64s`。

- [x] **步骤 4：提交验证记录**

把任务 1 到任务 4 的实际命令输出摘要写回本计划，然后提交：

```bash
git add .Codex/plans/api-chat-pre-bridge-route-result-split.md
git commit -m "docs(计划): 记录前置决策结果验证"
```

- [ ] **步骤 5：更新 `docs/todo.md`**

在 P3 超大文件拆分记录中追加本阶段结果：

```markdown
- 进展：`api/routes.py` 第二十八刀已拆出 Chat pre-bridge route result 转译到 `api/chat_pre_bridge_route_result.py`；父模块继续保留 HTTP route、DB callback、persona injection、Prompt Runtime payload、Bridge、SSE、response 和落库边界。
```

- [ ] **步骤 6：更新 `docs/plan_walkthrough.md`**

追加本阶段收口记录，包含：

```markdown
## 2026-06-24 普通 API Chat Pre-Bridge Route Result 拆分

状态：设计、计划、红灯测试、helper 拆分、父模块接入、相邻回归、全量验证和阶段提交均已完成。
```

- [ ] **步骤 7：运行最终文档检查**

运行：

```bash
rg -n "TO""DO|待""定|后续""实现|补充""细节|\\x3c[^>]+\\x3e|\\.\\.\\." \
.Codex/plans/api-chat-pre-bridge-route-result-split.md \
docs/superpowers/specs/2026-06-24-api-chat-pre-bridge-route-result-split-design.md || true
git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/api-chat-pre-bridge-route-result-split.md
```

预期：计划和设计文档没有占位红旗；`git diff --check` 无输出。

- [ ] **步骤 8：提交文档收口**

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/api-chat-pre-bridge-route-result-split.md
git commit -m "docs(计划): 收口前置决策结果拆分"
```

---

## 最终完成标准

- `api/chat_pre_bridge_route_result.py` 不导入父模块、FastAPI、Bridge、Prompt Runtime 或 DB 全局入口。
- `api/routes.py` 只保留 pre-bridge route result wrapper、callback 注入和后续 runtime payload 边界。
- early return response payload 与旧字段一致。
- guardrail silent 分支继续使用 `persist_req` 持久化 `"（数据中转，自动静默）"`。
- 父模块 `_clone_chat_request()`、`_persist_chat_turn()`、`_chat_response_payload()` 和 `_finalize_private_buffer()` patch point 保持可用。
- `tests/test_api_chat_pre_bridge_route_result_split.py`、四个 chat split module 扫描测试、相邻回归和全量测试通过。
- `api/routes.py` 行数继续下降。
- 每个阶段性改动均已按文件精确暂存并 commit。

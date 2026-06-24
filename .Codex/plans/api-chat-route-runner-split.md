# 普通 API Chat Route Runner 拆分实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把 `api.routes.proxy_chat()` 中 Bridge 调用后的 route runner 编排拆到 `api/chat_route_runner.py`，尽量把 `api/routes.py` 压到 800 行以下。

**架构：** 父模块继续保留 `/chat` HTTP route、鉴权、DB session、`get_bridge()` patch point、`StreamingResponse` 和 `HTTPException` 转译。新模块只负责 stream / non-streaming 的 async runner、SSE 事件产出、结果收尾委托和 route 级结果描述，所有副作用通过 callbacks 注入。

**技术栈：** Python 3.12、dataclass、async generator、pytest、pytest-asyncio、FastAPI `StreamingResponse` 边界、源码静态扫描。

---

## 已完成上下文

- [x] 设计文档：`docs/superpowers/specs/2026-06-24-api-chat-route-runner-split-design.md`
- [x] 设计提交：`a6f1e61 docs(普通API): 设计聊天路由执行器拆分`
- [x] 计划写入日期：2026-06-24

## 边界与禁止事项

- 保留：`api.routes.proxy_chat.__module__ == "api.routes"`。
- 保留：`/api/v1/chat` route 继续由 `api.routes` 注册。
- 保留：`bridge = get_bridge()` 在父模块执行。
- 保留：`StreamingResponse(...)` 和 `HTTPException(...)` 在父模块执行。
- 保留：父模块 `_persist_chat_turn()`、`_finalize_private_buffer()`、`_chat_response_payload()`、`_chat_sse_data()`、`_stream_error_event()`、`_build_chat_push_envelope()`、`_expand_chat_transport_answer()` patch point。
- 保留：`CHAT_STREAM_QUEUE_MAXSIZE` 常量在父模块，并作为参数传入新模块。
- 禁止：新模块导入 `api.routes`。
- 禁止：新模块导入 FastAPI、`APIRouter`、`Depends`、`StreamingResponse`、`BackgroundTasks` 或 `HTTPException`。
- 禁止：新模块导入 `SessionLocal`、`UnitOfWork`、`ChatLog`、`ConversationTurn` 或调用 `db.commit()`。
- 禁止：新模块静态调用 `get_bridge()`、`get_guardrail()` 或直接 import `core.daily_digest.push_envelope_to_qq`。
- 禁止：改变 `enriched_query`、`<user_input>` 包裹、`bridge_meta` 字段、history 注入、persona 注入、message envelope、push envelope 或 response envelope。
- 禁止：新增 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。
- 禁止：处理 WebUI / JS。

## 文件职责

- 创建：`api/chat_route_runner.py`
  - 定义 `ChatRouteRunnerCallbacks`。
  - 定义 `ChatRouteRunnerContext`。
  - 定义 `ChatRouteHttpError`。
  - 定义 `ChatRouteNonStreamingResult`。
  - 实现 `iter_streaming_chat_response(context)`。
  - 实现 `run_non_streaming_chat_response(db, context)`。
  - 只通过 callbacks 接收父模块能力和外部服务。
- 修改：`api/routes.py`
  - 导入 `api.chat_route_runner`。
  - 删除父模块内嵌 `_do_chat()` 和 `_stream_chat()`。
  - 新增 `_chat_route_runner_callbacks(db, background_tasks)` 薄 wrapper。
  - 新增 `_chat_route_runner_context(...)` 或等价薄 wrapper。
  - `req.stream` 时返回 `StreamingResponse(chat_route_runner.iter_streaming_chat_response(context), media_type="text/event-stream")`。
  - 非流式时调用 `chat_route_runner.run_non_streaming_chat_response(db, context)`，并在父模块把 `http_error` 转成 `HTTPException`。
- 创建：`tests/test_api_chat_route_runner_split.py`
  - 锁定新模块边界、stream success、stream error、stream audit failed、stream disconnect、non-stream success、non-stream error、non-stream cancellation、non-stream audit failed 和父模块瘦身。
- 修改：`tests/test_api_chat_sse_loop_split.py`
  - 更新父模块结构断言，旧 `_stream_chat()` 哨兵改为 route runner 边界。
- 修改：`tests/test_api_chat_non_streaming_result_split.py`
  - 更新父模块结构断言，旧 `_do_chat()` 哨兵改为 route runner 边界。
- 修改：
  - `tests/test_api_group_message_routes_split.py`
  - `tests/test_api_agent_step_routes_split.py`
  - `tests/test_api_history_log_routes_split.py`
  - `tests/test_api_sticker_media_routes_split.py`
  - 将 `api/chat_route_runner.py` 加入 chat split module 扫描清单。
- 修改：`.Codex/plans/api-chat-route-runner-split.md`
  - 随执行更新任务状态、命令输出和提交号。
- 修改：`docs/todo.md`
  - 最终收口时记录 P3 `api/routes.py` Chat Route Runner 拆分进展、行数和验证结果。
- 修改：`docs/plan_walkthrough.md`
  - 最终收口时追加本阶段提交列表、验证结果和下一步建议。

---

## 任务 1：红灯测试

**文件：**
- 创建：`tests/test_api_chat_route_runner_split.py`
- 修改：`tests/test_api_chat_sse_loop_split.py`
- 修改：`tests/test_api_chat_non_streaming_result_split.py`
- 修改：`tests/test_api_group_message_routes_split.py`
- 修改：`tests/test_api_agent_step_routes_split.py`
- 修改：`tests/test_api_history_log_routes_split.py`
- 修改：`tests/test_api_sticker_media_routes_split.py`
- 修改：`.Codex/plans/api-chat-route-runner-split.md`

- [x] **步骤 1：创建测试文件基础结构**

创建 `tests/test_api_chat_route_runner_split.py`，写入基础 fake：

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]


class FakeDb:
    pass


class FakeBridge:
    def __init__(self, *, answer: str = "最终答案", error: Exception | None = None):
        self.answer = answer
        self.error = error
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def handle_message(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return self.answer


class FakeBackgroundTasks:
    def __init__(self):
        self.tasks: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []

    def add_task(self, func: Any, *args: Any, **kwargs: Any) -> None:
        self.tasks.append((func, args, kwargs))


@dataclass(frozen=True)
class FakePushEnvelope:
    target_type: str
    target_id: str
    envelope: dict[str, Any]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _request(user_id: str = "u-runner", session_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        session_id=session_id or f"private_{user_id}",
        query="路由执行器请求",
        files=[],
        sender_name="发送者",
        session_name="会话",
        message_id="m-runner",
        source_message_ids=[],
        client_meta={"platform": "qq"},
        stream=False,
    )
```

- [x] **步骤 2：创建 callbacks helper**

在测试文件追加：

```python
def _callbacks(
    calls: dict[str, list[Any]],
    *,
    background_tasks: FakeBackgroundTasks | None = None,
    reply_meta: dict[str, Any] | None = None,
    pending: int = 3,
    expand_raises: bool = False,
):
    from api.chat_route_runner import ChatRouteRunnerCallbacks
    from api import chat_non_streaming_result

    background_tasks = background_tasks or FakeBackgroundTasks()

    async def call_bridge_non_streaming(bridge: Any, **kwargs: Any) -> str:
        calls.setdefault("call_non_stream", []).append((bridge, kwargs))
        return await bridge.handle_message(
            kwargs["enriched_query"],
            user_id=kwargs["user_id"],
            session_id=kwargs["session_id"],
            sender_name=kwargs["sender_name"],
            metadata=kwargs["metadata"],
            stream=False,
        )

    async def finalize_private_buffer(user_id: str, answer: str | None = None, *, clear_window: bool = True):
        calls.setdefault("finalize", []).append((user_id, answer, clear_window))

    def persist_chat_turn(db: Any, req: Any, answer: str, guardrail_status: str | None = None, **kwargs: Any) -> int:
        calls.setdefault("persist", []).append((db, req, answer, guardrail_status, kwargs))
        return pending

    def pop_bridge_reply_meta(bridge: Any, session_id: str) -> dict[str, Any]:
        calls.setdefault("pop_meta", []).append((bridge, session_id))
        return reply_meta or {}

    def private_prompt_audit_failure_meta() -> dict[str, Any]:
        calls.setdefault("audit_meta", []).append(())
        return {"kind": "empty_reply", "agent_result": "prompt_v2_audit_failed"}

    def expand_chat_transport_answer(answer: str) -> str:
        calls.setdefault("expand", []).append(answer)
        if expand_raises:
            raise RuntimeError("expand failed")
        return f"expanded:{answer}"

    def build_chat_push_envelope(req: Any, **kwargs: Any) -> FakePushEnvelope:
        calls.setdefault("push_envelope", []).append((req, kwargs))
        return FakePushEnvelope("private", req.user_id, {"answer": kwargs["answer"]})

    async def push_envelope_to_qq(target_type: str, target_id: str, envelope: dict[str, Any]) -> bool:
        calls.setdefault("push", []).append((target_type, target_id, envelope))
        return True

    def chat_response_payload(req: Any, **kwargs: Any) -> dict[str, Any]:
        calls.setdefault("payload", []).append((req, kwargs))
        return {
            "status": kwargs["status"],
            "answer": kwargs.get("answer", ""),
            "reply": kwargs.get("answer", ""),
            "reply_meta": kwargs.get("reply_meta"),
            "unprocessed_logs": kwargs.get("unprocessed_logs"),
            "guardrail_status": kwargs.get("guardrail_status"),
        }

    def chat_sse_data(event: dict[str, Any]) -> str:
        calls.setdefault("sse", []).append(event)
        return f"data: {event}\n\n"

    def stream_error_event() -> dict[str, str]:
        calls.setdefault("stream_error", []).append(())
        return {"status": "error", "message": "系统暂时不可用，请稍后再试"}

    async def drain_stream_queue_until_task_done(stream_queue: asyncio.Queue[Any], runner_task: asyncio.Task[Any]) -> None:
        calls.setdefault("drain", []).append((stream_queue, runner_task))

    return ChatRouteRunnerCallbacks(
        call_bridge_non_streaming=call_bridge_non_streaming,
        finalize_private_buffer=finalize_private_buffer,
        persist_chat_turn=persist_chat_turn,
        pop_bridge_reply_meta=pop_bridge_reply_meta,
        private_prompt_audit_failure_meta=private_prompt_audit_failure_meta,
        expand_chat_transport_answer=expand_chat_transport_answer,
        build_chat_push_envelope=build_chat_push_envelope,
        push_envelope_to_qq=push_envelope_to_qq,
        chat_response_payload=chat_response_payload,
        chat_sse_data=chat_sse_data,
        stream_error_event=stream_error_event,
        drain_stream_queue_until_task_done=drain_stream_queue_until_task_done,
        finalize_non_streaming_chat_result=chat_non_streaming_result.finalize_non_streaming_chat_result,
        add_background_task=background_tasks.add_task,
        evolution_task=lambda user_id: None,
    )
```

- [x] **步骤 3：创建 context helper**

在测试文件追加：

```python
def _context(
    calls: dict[str, list[Any]],
    *,
    req: Any | None = None,
    bridge: Any | None = None,
    background_tasks: FakeBackgroundTasks | None = None,
    reply_meta: dict[str, Any] | None = None,
    pending: int = 3,
):
    from api.chat_route_runner import ChatRouteRunnerContext

    req = req or _request()
    return ChatRouteRunnerContext(
        req=req,
        persist_req=req,
        bridge=bridge or FakeBridge(),
        enriched_query="<user_input>\n问题\n</user_input>",
        bridge_meta={"chat_type": "private", "is_group": False},
        platform="qq",
        guardrail_status="safe",
        private_timing_meta={"private_decision": "ok"},
        queue_maxsize=2,
        empty_assistant_placeholder="（无回复内容）",
        safe_error_message="系统暂时不可用，请稍后再试",
        evolution_threshold=5,
        callbacks=_callbacks(
            calls,
            background_tasks=background_tasks,
            reply_meta=reply_meta,
            pending=pending,
        ),
    )
```

- [x] **步骤 4：新增源码边界红灯**

追加测试：

```python
def test_chat_route_runner_module_does_not_import_parent_routes_or_fastapi_boundaries():
    path = ROOT / "api/chat_route_runner.py"
    assert path.exists()
    source = _source("api/chat_route_runner.py")

    forbidden = [
        "from api.routes",
        "import api.routes",
        "FastAPI",
        "APIRouter",
        "Depends",
        "StreamingResponse",
        "BackgroundTasks",
        "HTTPException",
        "NANOBOT_API_TOKEN",
        "verify_token",
        "router.post",
        "SessionLocal",
        "UnitOfWork",
        "ChatLog",
        "ConversationTurn",
        "db.commit(",
        "get_bridge(",
        "get_guardrail(",
        "from core.daily_digest import push_envelope_to_qq",
        "import core.daily_digest",
        "asyncio.run",
        "run_awaitable_sync",
    ]
    for needle in forbidden:
        assert needle not in source
```

- [x] **步骤 5：新增 stream success 红灯**

追加测试：

```python
@pytest.mark.asyncio
async def test_iter_streaming_chat_response_success_yields_done_payload_and_persists_raw_answer():
    from api.chat_route_runner import iter_streaming_chat_response

    calls: dict[str, list[Any]] = {}
    req = _request(user_id="u-stream-success")
    bridge = FakeBridge(answer="最终答案")
    context = _context(calls, req=req, bridge=bridge, pending=7)
    db = FakeDb()

    events = [event async for event in iter_streaming_chat_response(db, context)]

    assert bridge.calls[0][0] == ("<user_input>\n问题\n</user_input>",)
    assert bridge.calls[0][1]["stream"] is True
    assert bridge.calls[0][1]["stream_queue"].maxsize == 2
    assert calls["finalize"] == [("u-stream-success", "最终答案", True)]
    assert calls["persist"][0] == (
        db,
        req,
        "最终答案",
        "safe",
        {"timing_meta": {"private_decision": "ok"}},
    )
    assert calls["expand"] == ["最终答案"]
    assert calls["payload"][0][1]["status"] == "done"
    assert calls["payload"][0][1]["answer"] == "expanded:最终答案"
    assert calls["payload"][0][1]["unprocessed_logs"] == 7
    assert events[-1].startswith("data: ")
```

- [x] **步骤 6：新增 stream runner error 红灯**

追加测试：

```python
@pytest.mark.asyncio
async def test_iter_streaming_chat_response_runner_error_persists_placeholder_and_yields_safe_error():
    from api.chat_route_runner import iter_streaming_chat_response

    calls: dict[str, list[Any]] = {}
    bridge = FakeBridge(error=RuntimeError("真实内部错误"))
    context = _context(calls, bridge=bridge)
    db = FakeDb()

    events = [event async for event in iter_streaming_chat_response(db, context)]

    assert calls["finalize"] == [("u-runner", "（无回复内容）", True)]
    assert calls["persist"][0][2] == "（无回复内容）"
    assert calls["stream_error"] == [()]
    assert any("系统暂时不可用" in event for event in events)
    assert all("真实内部错误" not in event for event in events)
```

- [x] **步骤 7：新增 stream audit failed 红灯**

追加测试：

```python
@pytest.mark.asyncio
async def test_iter_streaming_chat_response_prompt_audit_failure_persists_audit_placeholder():
    from api.chat_route_runner import iter_streaming_chat_response

    calls: dict[str, list[Any]] = {}
    context = _context(
        calls,
        reply_meta={"_agent_result": "prompt_v2_audit_failed"},
    )
    db = FakeDb()

    events = [event async for event in iter_streaming_chat_response(db, context)]

    persisted = calls["persist"][0]
    assert calls["audit_meta"] == [()]
    assert persisted[2] == "（无回复内容）"
    assert persisted[4]["assistant_meta"] == {
        "kind": "empty_reply",
        "agent_result": "prompt_v2_audit_failed",
    }
    assert persisted[4]["assistant_processed"] == 1
    assert any("系统暂时不可用" in event for event in events)
    assert calls.get("payload") is None
```

- [x] **步骤 8：新增 stream disconnect 红灯**

追加测试：

```python
@pytest.mark.asyncio
async def test_iter_streaming_chat_response_client_disconnect_schedules_background_finish_without_sync_wait():
    from api.chat_route_runner import iter_streaming_chat_response

    started = asyncio.Event()
    release = asyncio.Event()

    class WaitingBridge(FakeBridge):
        async def handle_message(self, *args: Any, **kwargs: Any) -> str:
            self.calls.append((args, kwargs))
            await kwargs["stream_queue"].put({"status": "progress", "text": "处理中"})
            started.set()
            await release.wait()
            return "后台答案"

    calls: dict[str, list[Any]] = {}
    background_tasks = FakeBackgroundTasks()
    bridge = WaitingBridge()
    context = _context(calls, bridge=bridge, background_tasks=background_tasks)
    db = FakeDb()

    iterator = iter_streaming_chat_response(db, context)
    first = await asyncio.wait_for(anext(iterator), timeout=1)
    assert "处理中" in first
    await started.wait()
    await iterator.aclose()

    assert background_tasks.tasks
    _, _, kwargs = background_tasks.tasks[0]
    assert kwargs == {"push": True, "persist_db": None, "drain_stream": True}
    assert calls["finalize"] == [("u-runner", None, True)]
    assert not release.is_set()
    release.set()
```

- [x] **步骤 9：新增 non-streaming success 红灯**

追加测试：

```python
@pytest.mark.asyncio
async def test_run_non_streaming_chat_response_success_delegates_bridge_and_finalize():
    from api.chat_route_runner import run_non_streaming_chat_response

    calls: dict[str, list[Any]] = {}
    req = _request(user_id="u-non-stream")
    bridge = FakeBridge(answer="非流式答案")
    context = _context(calls, req=req, bridge=bridge, pending=8)
    db = FakeDb()

    result = await run_non_streaming_chat_response(db, context)

    assert calls["call_non_stream"][0][0] is bridge
    assert calls["finalize"] == [("u-non-stream", "非流式答案", True)]
    assert calls["persist"][0][2] == "非流式答案"
    assert result.payload["answer"] == "expanded:非流式答案"
    assert result.http_error is None
    assert result.should_trigger_evolution is True
```

- [x] **步骤 10：新增 non-streaming error / cancel / audit 红灯**

追加测试：

```python
@pytest.mark.asyncio
async def test_run_non_streaming_chat_response_bridge_error_returns_502_descriptor():
    from api.chat_route_runner import run_non_streaming_chat_response

    calls: dict[str, list[Any]] = {}
    context = _context(calls, bridge=FakeBridge(error=RuntimeError("内部失败")))
    db = FakeDb()

    result = await run_non_streaming_chat_response(db, context)

    assert result.payload is None
    assert result.http_error.status_code == 502
    assert result.http_error.detail == "系统暂时不可用，请稍后再试"
    assert calls["finalize"] == [("u-runner", "（无回复内容）", True)]
    assert calls["persist"][0][2] == "（无回复内容）"


@pytest.mark.asyncio
async def test_run_non_streaming_chat_response_cancelled_error_finalizes_and_reraises():
    from api.chat_route_runner import run_non_streaming_chat_response

    calls: dict[str, list[Any]] = {}
    context = _context(calls, bridge=FakeBridge(error=asyncio.CancelledError()))

    with pytest.raises(asyncio.CancelledError):
        await run_non_streaming_chat_response(FakeDb(), context)

    assert calls["finalize"] == [("u-runner", "（无回复内容）", True)]
    assert "persist" not in calls


@pytest.mark.asyncio
async def test_run_non_streaming_chat_response_prompt_audit_failure_returns_500_descriptor():
    from api.chat_route_runner import run_non_streaming_chat_response

    calls: dict[str, list[Any]] = {}
    context = _context(calls, reply_meta={"_agent_result": "prompt_v2_audit_failed"})

    result = await run_non_streaming_chat_response(FakeDb(), context)

    assert result.payload is None
    assert result.http_error.status_code == 500
    assert result.http_error.detail == "系统暂时不可用，请稍后再试"
    assert result.prompt_audit_failed is True
```

- [x] **步骤 11：新增父模块瘦身红灯**

追加测试：

```python
def test_parent_chat_route_delegates_bridge_runner_and_keeps_fastapi_boundary():
    source = _source("api/routes.py")

    assert "chat_route_runner" in source
    assert "StreamingResponse(" in source
    assert "chat_route_runner.iter_streaming_chat_response" in source
    assert "chat_route_runner.run_non_streaming_chat_response" in source
    assert "HTTPException(" in source
    assert "bridge = get_bridge()" in source
    assert "async def _stream_chat" not in source
    assert "async def _do_chat" not in source
    assert "async def runner" not in source
    assert "result_holder: dict" not in source
    assert "bridge.handle_message(" not in source
    assert "chat_sse_loop.iter_chat_stream_events(" not in source
    assert "chat_streaming_result.ChatStreamResultCallbacks(" not in source
    assert "chat_non_streaming_result.ChatNonStreamingResultCallbacks(" not in source
```

- [x] **步骤 12：更新旧结构测试和扫描清单**

修改旧测试断言：

```python
# tests/test_api_chat_sse_loop_split.py
assert "chat_route_runner.iter_streaming_chat_response" in source
assert "StreamingResponse(" in source
assert "async def _stream_chat" not in source

# tests/test_api_chat_non_streaming_result_split.py
assert "chat_route_runner.run_non_streaming_chat_response" in source
assert "async def _do_chat" not in source
assert "chat_runtime_facade.call_bridge_non_streaming" not in source
```

四个 scan tuple 追加：

```python
"api/chat_route_runner.py",
```

- [x] **步骤 13：运行红灯测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_route_runner_split.py -v
```

结果：`10 failed, 1 warning in 6.34s`。失败原因符合预期：
`api/chat_route_runner.py` 不存在、`api.chat_route_runner` 无法 import，且
`api.routes` 尚未包含 `chat_route_runner` 委托入口。

- [x] **步骤 14：运行扫描红灯**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  -v
```

结果：`4 failed, 1 warning in 6.83s`。四个失败均为扫描清单读取
`api/chat_route_runner.py` 时文件不存在，符合预期。

- [ ] **步骤 15：提交红灯测试**

运行：

```bash
git add tests/test_api_chat_route_runner_split.py \
  tests/test_api_chat_sse_loop_split.py \
  tests/test_api_chat_non_streaming_result_split.py \
  tests/test_api_group_message_routes_split.py \
  tests/test_api_agent_step_routes_split.py \
  tests/test_api_history_log_routes_split.py \
  tests/test_api_sticker_media_routes_split.py \
  .Codex/plans/api-chat-route-runner-split.md
git commit -m "test(普通API): 锁定聊天路由执行器契约"
```

---

## 任务 2：新增 Chat Route Runner helper

**文件：**
- 创建：`api/chat_route_runner.py`
- 修改：`.Codex/plans/api-chat-route-runner-split.md`

- [ ] **步骤 1：创建新模块类型和 callbacks**

创建 `api/chat_route_runner.py`：

```python
"""聊天 route runner 编排 helper。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any

from api import (
    chat_non_streaming_result,
    chat_sse_loop,
    chat_streaming_helpers,
    chat_streaming_result,
)


logger = logging.getLogger("nanobot.routes")


@dataclass(frozen=True)
class ChatRouteHttpError:
    status_code: int
    detail: str


@dataclass(frozen=True)
class ChatRouteNonStreamingResult:
    payload: dict[str, Any] | None
    http_error: ChatRouteHttpError | None = None
    pending: int | None = None
    should_trigger_evolution: bool = False
    prompt_audit_failed: bool = False


@dataclass(frozen=True)
class ChatRouteRunnerCallbacks:
    call_bridge_non_streaming: Callable[..., Awaitable[Any]]
    finalize_private_buffer: Callable[..., Awaitable[None]]
    persist_chat_turn: Callable[..., int]
    pop_bridge_reply_meta: Callable[[Any, str], dict[str, Any] | None]
    private_prompt_audit_failure_meta: Callable[[], dict[str, Any]]
    expand_chat_transport_answer: Callable[[str], str]
    build_chat_push_envelope: Callable[..., Any]
    push_envelope_to_qq: Callable[[str, str, dict[str, Any]], Awaitable[bool]]
    chat_response_payload: Callable[..., dict[str, Any]]
    chat_sse_data: Callable[[dict[str, Any]], str]
    stream_error_event: Callable[[], dict[str, Any]]
    drain_stream_queue_until_task_done: Callable[[asyncio.Queue[Any], asyncio.Task[Any]], Awaitable[None]]
    finalize_non_streaming_chat_result: Callable[..., Awaitable[Any]]
    add_background_task: Callable[..., None]
    evolution_task: Callable[..., Any]


@dataclass(frozen=True)
class ChatRouteRunnerContext:
    req: Any
    persist_req: Any
    bridge: Any
    enriched_query: str
    bridge_meta: dict[str, Any]
    platform: str
    guardrail_status: str | None
    private_timing_meta: dict[str, Any] | None
    queue_maxsize: int
    empty_assistant_placeholder: str
    safe_error_message: str
    evolution_threshold: int
    callbacks: ChatRouteRunnerCallbacks
```

- [ ] **步骤 2：实现 stream runner helper**

追加：

```python
async def _run_stream_bridge(
    context: ChatRouteRunnerContext,
    result_holder: MutableMapping[str, Any],
    done: asyncio.Event,
    stream_queue: asyncio.Queue[dict[str, Any]],
) -> None:
    req = context.req
    try:
        result_holder["answer"] = await context.bridge.handle_message(
            context.enriched_query,
            user_id=req.user_id,
            session_id=req.session_id,
            sender_name=req.sender_name or "",
            metadata=context.bridge_meta,
            stream_queue=stream_queue,
            stream=True,
        )
    except Exception as exc:
        result_holder["error"] = str(exc)
    finally:
        done.set()
```

- [ ] **步骤 3：实现 stream result context builder**

追加：

```python
def _stream_result_context(
    context: ChatRouteRunnerContext,
    *,
    result_holder: MutableMapping[str, Any],
    runner_task: asyncio.Task[Any],
    stream_queue: asyncio.Queue[Any],
) -> chat_streaming_result.ChatStreamResultContext:
    callbacks = context.callbacks
    return chat_streaming_result.ChatStreamResultContext(
        req=context.req,
        persist_req=context.persist_req,
        bridge=context.bridge,
        result_holder=result_holder,
        runner_task=runner_task,
        stream_queue=stream_queue,
        platform=context.platform,
        bridge_meta=context.bridge_meta,
        guardrail_status=context.guardrail_status,
        private_timing_meta=context.private_timing_meta,
        empty_assistant_placeholder=context.empty_assistant_placeholder,
        callbacks=chat_streaming_result.ChatStreamResultCallbacks(
            drain_stream_queue_until_task_done=callbacks.drain_stream_queue_until_task_done,
            pop_bridge_reply_meta=callbacks.pop_bridge_reply_meta,
            private_prompt_audit_failure_meta=callbacks.private_prompt_audit_failure_meta,
            finalize_private_buffer=callbacks.finalize_private_buffer,
            persist_chat_turn=callbacks.persist_chat_turn,
            expand_chat_transport_answer=callbacks.expand_chat_transport_answer,
            build_chat_push_envelope=callbacks.build_chat_push_envelope,
            push_envelope_to_qq=callbacks.push_envelope_to_qq,
        ),
    )
```

- [ ] **步骤 4：实现 `iter_streaming_chat_response()`**

追加：

```python
async def iter_streaming_chat_response(db: Any, context: ChatRouteRunnerContext):
    callbacks = context.callbacks
    result_holder: dict[str, Any] = {}
    done = asyncio.Event()
    stream_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=context.queue_maxsize)
    persisted = False
    runner_task = asyncio.create_task(_run_stream_bridge(context, result_holder, done, stream_queue))
    stream_result_context = _stream_result_context(
        context,
        result_holder=result_holder,
        runner_task=runner_task,
        stream_queue=stream_queue,
    )

    async def persist_stream_result_after_runner_done(
        *,
        push: bool,
        persist_db: Any | None = None,
        drain_stream: bool = False,
    ) -> None:
        await chat_streaming_result.persist_stream_result_after_runner_done(
            stream_result_context,
            push=push,
            persist_db=persist_db,
            drain_stream=drain_stream,
        )

    try:
        async for event in chat_sse_loop.iter_chat_stream_events(
            stream_queue,
            done,
            heartbeat_interval=5,
            coalescer=chat_streaming_helpers.StreamEventCoalescer(),
            callbacks=chat_sse_loop.ChatSseLoopCallbacks(
                normalize_event=lambda raw: raw if isinstance(raw, dict) else None,
            ),
        ):
            yield callbacks.chat_sse_data(event)

        if "error" in result_holder:
            await callbacks.finalize_private_buffer(
                context.req.user_id,
                context.empty_assistant_placeholder,
            )
            callbacks.persist_chat_turn(
                db,
                context.persist_req,
                context.empty_assistant_placeholder,
                context.guardrail_status,
                timing_meta=context.private_timing_meta,
            )
            persisted = True
            yield callbacks.chat_sse_data(callbacks.stream_error_event())
            return

        answer = result_holder.get("answer", "")
        private_reply_meta = callbacks.pop_bridge_reply_meta(context.bridge, context.req.session_id)
        transport_answer = answer
        try:
            transport_answer = callbacks.expand_chat_transport_answer(answer)
        except Exception:
            logger.warning("[/chat] stream generated image ref expansion failed", exc_info=True)

        if (private_reply_meta or {}).get("_agent_result") == "prompt_v2_audit_failed":
            await callbacks.finalize_private_buffer(
                context.req.user_id,
                context.empty_assistant_placeholder,
            )
            callbacks.persist_chat_turn(
                db,
                context.persist_req,
                context.empty_assistant_placeholder,
                context.guardrail_status,
                assistant_meta=callbacks.private_prompt_audit_failure_meta(),
                assistant_processed=1,
                timing_meta=context.private_timing_meta,
            )
            persisted = True
            yield callbacks.chat_sse_data(callbacks.stream_error_event())
            return

        await callbacks.finalize_private_buffer(context.req.user_id, answer)
        pending = callbacks.persist_chat_turn(
            db,
            context.persist_req,
            answer,
            context.guardrail_status,
            timing_meta=context.private_timing_meta,
        )
        persisted = True
        if pending >= context.evolution_threshold:
            callbacks.add_background_task(callbacks.evolution_task, context.req.user_id)
        done_payload = callbacks.chat_response_payload(
            context.req,
            status="done",
            answer=transport_answer,
            reply_meta=private_reply_meta,
            platform=context.platform,
            chat_type=str(context.bridge_meta.get("chat_type") or ""),
            unprocessed_logs=pending,
            guardrail_status=context.guardrail_status,
        )
        yield callbacks.chat_sse_data(done_payload)
    finally:
        if not persisted:
            if runner_task.done():
                await persist_stream_result_after_runner_done(push=False, persist_db=db)
            else:
                callbacks.add_background_task(
                    persist_stream_result_after_runner_done,
                    push=True,
                    persist_db=None,
                    drain_stream=True,
                )
                await callbacks.finalize_private_buffer(context.req.user_id)
                logger.warning(
                    "[/chat] Stream aborted, running in background: user=%s, session=%s",
                    context.req.user_id,
                    context.req.session_id,
                )
        done.set()
```

- [ ] **步骤 5：实现 non-streaming context builder 和 runner**

追加：

```python
def _non_streaming_context(
    context: ChatRouteRunnerContext,
    *,
    answer: str,
) -> chat_non_streaming_result.ChatNonStreamingResultContext:
    callbacks = context.callbacks
    return chat_non_streaming_result.ChatNonStreamingResultContext(
        req=context.req,
        persist_req=context.persist_req,
        bridge=context.bridge,
        answer=answer,
        platform=context.platform,
        bridge_meta=context.bridge_meta,
        guardrail_status=context.guardrail_status,
        private_timing_meta=context.private_timing_meta,
        empty_assistant_placeholder=context.empty_assistant_placeholder,
        evolution_threshold=context.evolution_threshold,
        callbacks=chat_non_streaming_result.ChatNonStreamingResultCallbacks(
            pop_bridge_reply_meta=callbacks.pop_bridge_reply_meta,
            private_prompt_audit_failure_meta=callbacks.private_prompt_audit_failure_meta,
            finalize_private_buffer=callbacks.finalize_private_buffer,
            persist_chat_turn=callbacks.persist_chat_turn,
            expand_chat_transport_answer=callbacks.expand_chat_transport_answer,
            chat_response_payload=callbacks.chat_response_payload,
        ),
    )


async def run_non_streaming_chat_response(
    db: Any,
    context: ChatRouteRunnerContext,
) -> ChatRouteNonStreamingResult:
    try:
        answer = await context.callbacks.call_bridge_non_streaming(
            context.bridge,
            enriched_query=context.enriched_query,
            user_id=context.req.user_id,
            session_id=context.req.session_id,
            sender_name=context.req.sender_name or "",
            metadata=context.bridge_meta,
        )
    except asyncio.CancelledError:
        await context.callbacks.finalize_private_buffer(
            context.req.user_id,
            context.empty_assistant_placeholder,
        )
        raise
    except Exception:
        await context.callbacks.finalize_private_buffer(
            context.req.user_id,
            context.empty_assistant_placeholder,
        )
        context.callbacks.persist_chat_turn(
            db,
            context.persist_req,
            context.empty_assistant_placeholder,
            context.guardrail_status,
            timing_meta=context.private_timing_meta,
        )
        return ChatRouteNonStreamingResult(
            payload=None,
            http_error=ChatRouteHttpError(502, context.safe_error_message),
        )

    result = await context.callbacks.finalize_non_streaming_chat_result(
        db,
        _non_streaming_context(context, answer=answer),
    )
    if result.prompt_audit_failed:
        return ChatRouteNonStreamingResult(
            payload=None,
            http_error=ChatRouteHttpError(500, context.safe_error_message),
            prompt_audit_failed=True,
        )
    if result.should_trigger_evolution:
        context.callbacks.add_background_task(context.callbacks.evolution_task, context.req.user_id)
    return ChatRouteNonStreamingResult(
        payload=result.payload,
        pending=result.pending,
        should_trigger_evolution=result.should_trigger_evolution,
    )
```

- [ ] **步骤 6：运行 helper 定向测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_route_runner_split.py -k "not parent_chat_route_delegates" -v
```

预期：新模块行为测试通过；父模块瘦身测试仍失败。

- [ ] **步骤 7：静态检查 helper**

运行：

```bash
python -m compileall api/chat_route_runner.py -q
```

预期：退出码 0。

- [ ] **步骤 8：提交 helper**

运行：

```bash
git add api/chat_route_runner.py .Codex/plans/api-chat-route-runner-split.md
git commit -m "refactor(普通API): 增加聊天路由执行器"
```

---

## 任务 3：接入父模块

**文件：**
- 修改：`api/routes.py`
- 修改：`tests/test_api_chat_sse_loop_split.py`
- 修改：`tests/test_api_chat_non_streaming_result_split.py`
- 修改：`.Codex/plans/api-chat-route-runner-split.md`

- [ ] **步骤 1：更新 import**

在 `api/routes.py` 的 `from api import (...)` 中加入：

```python
chat_route_runner,
```

如果父模块接入后不再直接引用这些模块，从 import 中移除：

```python
chat_non_streaming_result,
chat_sse_loop,
chat_streaming_helpers,
chat_streaming_result,
```

- [ ] **步骤 2：新增 callbacks wrapper**

在 `_build_chat_runtime_route_context()` 后或 `/chat` 前新增：

```python
def _chat_route_runner_callbacks(
    db: Session,
    background_tasks: BackgroundTasks,
) -> chat_route_runner.ChatRouteRunnerCallbacks:
    from core.daily_digest import push_envelope_to_qq

    return chat_route_runner.ChatRouteRunnerCallbacks(
        call_bridge_non_streaming=chat_runtime_facade.call_bridge_non_streaming,
        finalize_private_buffer=_finalize_private_buffer,
        persist_chat_turn=_persist_chat_turn,
        pop_bridge_reply_meta=_pop_bridge_reply_meta,
        private_prompt_audit_failure_meta=_private_prompt_audit_failure_meta,
        expand_chat_transport_answer=_expand_chat_transport_answer,
        build_chat_push_envelope=_build_chat_push_envelope,
        push_envelope_to_qq=push_envelope_to_qq,
        chat_response_payload=_chat_response_payload,
        chat_sse_data=_chat_sse_data,
        stream_error_event=_stream_error_event,
        drain_stream_queue_until_task_done=chat_streaming_helpers.drain_stream_queue_until_task_done,
        finalize_non_streaming_chat_result=chat_non_streaming_result.finalize_non_streaming_chat_result,
        add_background_task=background_tasks.add_task,
        evolution_task=evolution_task,
    )
```

如果 `chat_streaming_helpers` 和 `chat_non_streaming_result` 只被 wrapper 使用，可保留父模块 import。若要进一步瘦身，可让 `api/chat_route_runner.py` 直接引用这些已拆 helper，并从父模块 wrapper 中移除对应字段；但必须保持测试禁止项不被触发。

- [ ] **步骤 3：新增 context wrapper**

在 callbacks wrapper 后新增：

```python
def _chat_route_runner_context(
    *,
    req: ChatProxyRequest,
    persist_req: ChatProxyRequest,
    bridge: Any,
    enriched_query: str,
    bridge_meta: dict[str, Any],
    platform: str,
    guardrail_status: str | None,
    private_timing_meta: dict[str, Any] | None,
    background_tasks: BackgroundTasks,
    db: Session,
) -> chat_route_runner.ChatRouteRunnerContext:
    return chat_route_runner.ChatRouteRunnerContext(
        req=req,
        persist_req=persist_req,
        bridge=bridge,
        enriched_query=enriched_query,
        bridge_meta=bridge_meta,
        platform=platform,
        guardrail_status=guardrail_status,
        private_timing_meta=private_timing_meta,
        queue_maxsize=CHAT_STREAM_QUEUE_MAXSIZE,
        empty_assistant_placeholder=EMPTY_ASSISTANT_PLACEHOLDER,
        safe_error_message=SAFE_STREAM_ERROR_MESSAGE,
        evolution_threshold=EVOLUTION_THRESHOLD,
        callbacks=_chat_route_runner_callbacks(db, background_tasks),
    )
```

- [ ] **步骤 4：替换 `proxy_chat()` bridge 后半段**

删除父模块内嵌 `_do_chat()` 和 `_stream_chat()`，替换为：

```python
route_runner_context = _chat_route_runner_context(
    req=req,
    persist_req=persist_req,
    bridge=bridge,
    enriched_query=enriched_query,
    bridge_meta=bridge_meta,
    platform=platform,
    guardrail_status=guardrail_status,
    private_timing_meta=private_timing_meta,
    background_tasks=background_tasks,
    db=db,
)
if req.stream:
    return StreamingResponse(
        chat_route_runner.iter_streaming_chat_response(db, route_runner_context),
        media_type="text/event-stream",
    )

non_streaming_result = await chat_route_runner.run_non_streaming_chat_response(
    db,
    route_runner_context,
)
if non_streaming_result.http_error is not None:
    raise HTTPException(
        status_code=non_streaming_result.http_error.status_code,
        detail=non_streaming_result.http_error.detail,
    )
return non_streaming_result.payload
```

父模块不再直接构造 `chat_streaming_result.ChatStreamResultCallbacks` 或
`chat_non_streaming_result.ChatNonStreamingResultCallbacks`。

- [ ] **步骤 5：同步旧结构测试断言**

确认 `tests/test_api_chat_sse_loop_split.py` 和
`tests/test_api_chat_non_streaming_result_split.py` 的父模块断言与任务 1 一致。

- [ ] **步骤 6：运行父模块接入定向测试**

运行：

```bash
python -B -m pytest -p no:cacheprovider tests/test_api_chat_route_runner_split.py -v
```

预期：全部通过。

- [ ] **步骤 7：运行相邻 chat split 回归**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_chat_route_runner_split.py \
  tests/test_api_chat_sse_loop_split.py \
  tests/test_api_chat_streaming_result_split.py \
  tests/test_api_chat_streaming_helpers_split.py \
  tests/test_api_chat_non_streaming_result_split.py \
  tests/test_api_chat_runtime_facade_split.py \
  tests/test_api_chat_runtime_route_context_split.py \
  -v
```

预期：全部通过。

- [ ] **步骤 8：运行扫描回归**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  -v
```

预期：全部通过。

- [ ] **步骤 9：提交父模块接入**

运行：

```bash
git add api/routes.py \
  tests/test_api_chat_sse_loop_split.py \
  tests/test_api_chat_non_streaming_result_split.py \
  .Codex/plans/api-chat-route-runner-split.md
git commit -m "refactor(普通API): 接入聊天路由执行器"
```

---

## 任务 4：压缩父模块和验证收口

**文件：**
- 修改：`api/routes.py`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/api-chat-route-runner-split.md`

- [ ] **步骤 1：检查行数**

运行：

```bash
wc -l api/routes.py api/chat_route_runner.py tests/test_api_chat_route_runner_split.py
```

预期：`api/routes.py` 显著低于 1005 行。若仍高于 800 行，优先压缩本阶段新增 wrapper 排版和删除未使用 import；不要做无关重构。

- [ ] **步骤 2：静态检查**

运行：

```bash
python -m compileall api/routes.py api/chat_route_runner.py -q
git diff --check
```

预期：退出码 0，`git diff --check` 无输出。

- [ ] **步骤 3：async 策略核对**

运行：

```bash
rg -n "asyncio\\.run|run_awaitable_sync" api/routes.py api/chat_route_runner.py tests/test_api_chat_route_runner_split.py || true
```

预期：生产代码无命中；测试文件只允许命中禁止断言字符串。

- [ ] **步骤 4：Prompt Runtime 核查**

运行：

```bash
rg -n "enriched_query|<user_input>|bridge_meta|history_header|history_messages|raw_query|persona_text|PromptRuntime|prompt_v2" \
  api/routes.py api/chat_route_runner.py prompts.v2.default/chat data/prompts_v2/chat core/prompt_v2/variables.py core/prompt_v2/template_registry.py
```

预期：本阶段仅移动 runner 编排，不改变 Prompt Runtime 字段、模板标记或变量语义。若发现字段变更，必须同步更新模板或回退字段变更。

- [ ] **步骤 5：运行定向和相邻回归**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_chat_route_runner_split.py \
  tests/test_api_chat_sse_loop_split.py \
  tests/test_api_chat_streaming_result_split.py \
  tests/test_api_chat_streaming_helpers_split.py \
  tests/test_api_chat_non_streaming_result_split.py \
  tests/test_api_chat_runtime_facade_split.py \
  tests/test_api_chat_runtime_route_context_split.py \
  -v
```

预期：全部通过。

- [ ] **步骤 6：运行普通 API split 扫描**

运行：

```bash
python -B -m pytest -p no:cacheprovider \
  tests/test_api_group_message_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_agent_step_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_history_log_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  tests/test_api_sticker_media_routes_split.py::test_chat_split_modules_do_not_import_parent_routes_or_sync_awaitable \
  -v
```

预期：全部通过。

- [ ] **步骤 7：运行全量测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
  python -B -m pytest -p no:cacheprovider tests/ -v
```

预期：0 failures。

- [ ] **步骤 8：更新 `docs/todo.md`**

在 P3 `api/routes.py` 进展列表追加本阶段记录，包含：

```markdown
- 进展：`api/routes.py` 第三十刀已拆出 Chat Route Runner 到
  `api/chat_route_runner.py`；旧 `/chat` endpoint、`get_bridge()` patch point、
  `StreamingResponse`、`HTTPException`、DB session、pre-bridge、Prompt Runtime
  payload、message envelope、push envelope 和 response envelope 边界保持在父模块。
  新模块承载 stream / non-streaming bridge runner 编排、SSE 事件产出、断连后台
  收尾登记和 route 级错误描述，不反向导入 `api.routes`，也没有 `asyncio.run` 或
  `run_awaitable_sync`。`api/routes.py` 从 1005 行降至 `<实际行数>` 行，
  `api/chat_route_runner.py` 为 `<实际行数>` 行，拆分测试为 `<实际行数>` 行。
  验证结果：红灯 `<结果>`，helper 阶段 `<结果>`，父模块接入 `<结果>`，
  相邻回归 `<结果>`，全量回归 `<结果>`。
```

如果 `api/routes.py` 已低于 800 行，将该 checkbox 标为完成；否则保留未完成并写明下一刀。

- [ ] **步骤 9：更新 `docs/plan_walkthrough.md`**

追加 `2026-06-24 普通 API Chat Route Runner 拆分` 小节，包含：

- 状态。
- 设计文档路径。
- 实现计划路径。
- 阶段提交列表。
- 计划列表勾选状态。
- 验证记录。
- async 策略核对。
- Prompt Runtime 核查。
- 下一步建议。

- [ ] **步骤 10：提交验证收口**

运行：

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/api-chat-route-runner-split.md
git commit -m "docs(计划): 收口聊天路由执行器拆分"
```

---

## 最终验收标准

- `api/routes.py` 行数低于本阶段开始的 1005 行，目标是低于 800 行。
- `/api/v1/chat` route 仍由 `api.routes` 注册。
- `get_bridge()` patch point 仍在父模块。
- stream / non-streaming route runner 编排已迁出父模块。
- 新模块不导入 `api.routes`，不导入 FastAPI HTTP 边界，不直接持有 DB / UoW 边界。
- 新模块和生产代码没有新增 `asyncio.run`、`run_awaitable_sync` 或同步函数包装 awaitable。
- SSE success、error、audit failed、disconnect、non-stream success、non-stream error、cancel 和 audit failed 均有测试覆盖。
- Prompt Runtime 字段、模板标记和 envelope 语义未改变。
- 定向、扫描、静态检查和全量测试有新鲜通过证据。

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
    def __init__(self, *, answer: str = "最终答案", error: BaseException | None = None):
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


def _callbacks(
    calls: dict[str, list[Any]],
    *,
    background_tasks: FakeBackgroundTasks | None = None,
    reply_meta: dict[str, Any] | None = None,
    pending: int = 3,
    expand_raises: bool = False,
):
    from api import chat_non_streaming_result
    from api.chat_route_runner import ChatRouteRunnerCallbacks

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

    async def finalize_private_buffer(
        user_id: str,
        answer: str | None = None,
        *,
        clear_window: bool = True,
    ):
        calls.setdefault("finalize", []).append((user_id, answer, clear_window))

    def persist_chat_turn(
        db: Any,
        req: Any,
        answer: str,
        guardrail_status: str | None = None,
        **kwargs: Any,
    ) -> int:
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

    async def drain_stream_queue_until_task_done(
        stream_queue: asyncio.Queue[Any],
        runner_task: asyncio.Task[Any],
    ) -> None:
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
    await asyncio.sleep(0)


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

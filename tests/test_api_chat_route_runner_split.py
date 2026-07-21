from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.sqlite_test_utils import install_base_schema


ROOT = Path(__file__).resolve().parents[1]


class FatalRunnerError(BaseException):
    pass


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


async def _wait_for_stream_finalizers(route_runner: Any) -> None:
    async def wait_until_empty() -> None:
        while tasks := tuple(
            getattr(route_runner, "_STREAM_FINALIZER_TASKS", set())
        ):
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(wait_until_empty(), timeout=1)
    except TimeoutError as exc:
        raise AssertionError(
            "stream finalizer registry 未在 1 秒内清空"
        ) from exc


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
    persist_error: BaseException | None = None,
):
    from api import chat_non_streaming_result, chat_streaming_helpers
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
        if persist_error is not None:
            raise persist_error
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
        await chat_streaming_helpers.drain_stream_queue_until_task_done(
            stream_queue,
            runner_task,
            poll_timeout=0.001,
        )

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
    claim_owner: Any | None = None,
    queue_maxsize: int = 2,
    persist_error: BaseException | None = None,
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
        queue_maxsize=queue_maxsize,
        empty_assistant_placeholder="（无回复内容）",
        safe_error_message="系统暂时不可用，请稍后再试",
        evolution_threshold=5,
        callbacks=_callbacks(
            calls,
            background_tasks=background_tasks,
            reply_meta=reply_meta,
            pending=pending,
            persist_error=persist_error,
        ),
        claim_owner=claim_owner,
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


def test_stream_finalizer_registry_strongly_tracks_observes_and_cleans_tasks():
    source = _source("api/chat_route_runner.py")

    assert "_STREAM_FINALIZER_TASKS" in source
    assert ".add_done_callback(" in source
    assert ".exception()" in source
    assert ".discard(" in source


@pytest.mark.parametrize("outcome", ["error", "cancelled"])
@pytest.mark.asyncio
async def test_stream_finalizer_done_callback_logger_base_exception_never_reaches_event_loop(
    outcome,
    monkeypatch,
):
    from api import chat_route_runner

    loop = asyncio.get_running_loop()
    old_handler = loop.get_exception_handler()
    callback_errors: list[dict[str, Any]] = []

    class ExplodingLogger:
        def __getattr__(self, _name):
            raise FatalRunnerError("logger method lookup failed")

    async def finalizer_coroutine():
        if outcome == "error":
            raise RuntimeError("observed finalizer error")
        await asyncio.Event().wait()

    monkeypatch.setattr(chat_route_runner, "logger", ExplodingLogger())
    loop.set_exception_handler(lambda _loop, context: callback_errors.append(context))
    task = chat_route_runner._register_stream_finalizer(
        finalizer_coroutine(),
        name=f"chat-stream-finalizer:callback-{outcome}",
    )
    try:
        if outcome == "cancelled":
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await _wait_for_stream_finalizers(chat_route_runner)

        assert task.done()
        assert not chat_route_runner._STREAM_FINALIZER_TASKS
        assert callback_errors == []
    finally:
        loop.set_exception_handler(old_handler)


@pytest.mark.asyncio
async def test_cold_stream_body_concurrent_pull_and_close_cancels_pull_before_inner_aclose(
    monkeypatch,
):
    from api import chat_route_runner

    calls: dict[str, list[Any]] = {}
    inner_started = asyncio.Event()

    class ClaimOwner:
        def __init__(self):
            self.renewal_task = None

        async def resume(self):
            calls.setdefault("resume", []).append(())
            self.renewal_task = asyncio.create_task(asyncio.Event().wait())

        async def complete(self, completion):
            calls.setdefault("complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("fail", []).append(error)
            if self.renewal_task is not None:
                self.renewal_task.cancel()
                await asyncio.gather(self.renewal_task, return_exceptions=True)
            return True

    owner = ClaimOwner()
    context = _context(calls, claim_owner=owner)

    async def controlled_inner(
        _db,
        _context,
        *,
        lifecycle_started=None,
    ):
        if lifecycle_started is not None:
            lifecycle_started.set()
        try:
            inner_started.set()
            await asyncio.Event().wait()
            yield "不应产生"
        finally:
            calls.setdefault("inner_finally", []).append(())
            await owner.fail(asyncio.CancelledError("cold pull closed"))

    monkeypatch.setattr(
        chat_route_runner,
        "iter_streaming_chat_response",
        controlled_inner,
    )
    body = chat_route_runner.ColdChatStreamingBody(context)
    pull_task = asyncio.create_task(anext(body))
    close_tasks: list[asyncio.Task[Any]] = []
    try:
        await asyncio.wait_for(inner_started.wait(), timeout=1)
        close_tasks = [
            asyncio.create_task(body.aclose()),
            asyncio.create_task(body.aclose()),
        ]
        close_results = await asyncio.gather(*close_tasks, return_exceptions=True)

        assert close_results == [None, None]
        assert pull_task.done()
        assert pull_task.cancelled()
        assert calls["resume"] == [()]
        assert len(calls["inner_finally"]) == 1
        assert len(calls["fail"]) == 1
        assert calls.get("complete") is None
        assert owner.renewal_task is not None and owner.renewal_task.done()
        assert not chat_route_runner._STREAM_FINALIZER_TASKS
    finally:
        if not pull_task.done():
            pull_task.cancel()
        await asyncio.gather(pull_task, *close_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_cold_stream_body_close_queued_during_resume_fails_once_without_entering_inner(
    monkeypatch,
):
    from api import chat_route_runner

    calls: dict[str, list[Any]] = {}
    resume_entered = asyncio.Event()
    release_resume = asyncio.Event()

    class ClaimOwner:
        def __init__(self):
            self.renewal_task = asyncio.create_task(asyncio.sleep(0))

        async def resume(self):
            calls.setdefault("resume", []).append(())
            resume_entered.set()
            await release_resume.wait()
            self.renewal_task = asyncio.create_task(asyncio.Event().wait())
            return self.renewal_task

        async def fail(self, error):
            calls.setdefault("fail", []).append(error)
            if self.renewal_task is not None and not self.renewal_task.done():
                self.renewal_task.cancel()
                await asyncio.gather(self.renewal_task, return_exceptions=True)
            return True

    owner = ClaimOwner()
    await owner.renewal_task
    context = _context(calls, claim_owner=owner)

    async def forbidden_inner(
        _db,
        _context,
        *,
        lifecycle_started=None,
    ):
        if lifecycle_started is not None:
            lifecycle_started.set()
        calls.setdefault("inner_started", []).append(())
        try:
            await asyncio.Event().wait()
            yield "不应进入"
        finally:
            calls.setdefault("inner_finally", []).append(())

    monkeypatch.setattr(
        chat_route_runner,
        "iter_streaming_chat_response",
        forbidden_inner,
    )
    body = chat_route_runner.ColdChatStreamingBody(context)
    pull_task = asyncio.create_task(anext(body))
    close_tasks: list[asyncio.Task[Any]] = []
    try:
        await asyncio.wait_for(resume_entered.wait(), timeout=1)
        close_tasks = [
            asyncio.create_task(body.aclose()),
            asyncio.create_task(body.aclose()),
        ]
        await asyncio.sleep(0)
        release_resume.set()
        close_results = await asyncio.gather(*close_tasks, return_exceptions=True)
        await asyncio.gather(pull_task, return_exceptions=True)

        assert close_results == [None, None]
        assert pull_task.done()
        assert calls["resume"] == [()]
        assert calls.get("inner_started") is None
        assert calls.get("inner_finally") is None
        assert len(calls["fail"]) == 1
        assert owner.renewal_task is None or owner.renewal_task.done()
        assert not chat_route_runner._STREAM_FINALIZER_TASKS
    finally:
        release_resume.set()
        if not pull_task.done():
            pull_task.cancel()
        await asyncio.gather(pull_task, *close_tasks, return_exceptions=True)
        if owner.renewal_task is not None and not owner.renewal_task.done():
            owner.renewal_task.cancel()
            await asyncio.gather(owner.renewal_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cold_stream_body_resume_ownership_lost_never_creates_inner_or_bridge(
    monkeypatch,
):
    from core.inbound_claim_lifecycle import InboundClaimOwnershipLostError
    from api import chat_route_runner

    calls: dict[str, list[Any]] = {}
    ownership_error = InboundClaimOwnershipLostError("cold resume lost")

    class ClaimOwner:
        def __init__(self):
            self.renewal_task = asyncio.create_task(asyncio.sleep(0))

        async def start(self):
            calls.setdefault("start", []).append(())
            return self.renewal_task

        async def resume(self):
            calls.setdefault("resume", []).append(())
            raise ownership_error

        async def fail(self, error):
            calls.setdefault("fail", []).append(error)
            return False

    owner = ClaimOwner()
    await owner.renewal_task
    assert owner.renewal_task.done()
    context = _context(calls, claim_owner=owner)

    async def forbidden_inner(
        _db,
        _context,
        *,
        lifecycle_started=None,
    ):
        if lifecycle_started is not None:
            lifecycle_started.set()
        calls.setdefault("inner", []).append(())
        yield "不应进入"

    monkeypatch.setattr(
        chat_route_runner,
        "iter_streaming_chat_response",
        forbidden_inner,
    )
    body = chat_route_runner.ColdChatStreamingBody(context)

    with pytest.raises(InboundClaimOwnershipLostError) as raised:
        await anext(body)

    assert raised.value is ownership_error
    assert calls["resume"] == [()]
    assert calls.get("start") is None
    assert calls.get("inner") is None
    assert calls["fail"] == [ownership_error]
    assert owner.renewal_task.done()
    assert not chat_route_runner._STREAM_FINALIZER_TASKS


@pytest.mark.asyncio
async def test_run_stream_bridge_preserves_original_exception_object_in_holder():
    from api.chat_route_runner import _run_stream_bridge

    bridge_error = RuntimeError("original bridge failure")
    context = _context({}, bridge=FakeBridge(error=bridge_error))
    result_holder: dict[str, Any] = {}
    done = asyncio.Event()
    stream_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)

    await _run_stream_bridge(context, result_holder, done, stream_queue)

    assert result_holder["error"] is bridge_error
    assert done.is_set()


@pytest.mark.asyncio
async def test_iter_streaming_chat_response_success_yields_done_payload_and_persists_raw_answer(
    monkeypatch,
):
    from api import chat_route_runner

    calls: dict[str, list[Any]] = {}
    req = _request(user_id="u-stream-success")
    bridge = FakeBridge(answer="最终答案")
    background_tasks = FakeBackgroundTasks()
    request_db = FakeDb()
    fresh_db = FakeDb()

    class FakeUnitOfWork:
        def __enter__(self):
            self.db = fresh_db
            calls.setdefault("uow_enter", []).append(self.db)
            return self

        def __exit__(self, exc_type, exc, tb):
            self.db = None
            return False

    monkeypatch.setattr("core.uow.UnitOfWork", FakeUnitOfWork)

    class ClaimOwner:
        async def complete(self, completion):
            assert calls["persist"]
            assert background_tasks.tasks == []
            calls.setdefault("owner_complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return True

    context = _context(
        calls,
        req=req,
        bridge=bridge,
        pending=7,
        background_tasks=background_tasks,
        claim_owner=ClaimOwner(),
    )
    events = [
        event
        async for event in chat_route_runner.iter_streaming_chat_response(
            request_db,
            context,
        )
    ]
    await _wait_for_stream_finalizers(chat_route_runner)

    assert bridge.calls[0][0] == ("<user_input>\n问题\n</user_input>",)
    assert bridge.calls[0][1]["stream"] is True
    assert bridge.calls[0][1]["stream_queue"].maxsize == 2
    assert calls["finalize"] == [("u-stream-success", "最终答案", True)]
    assert calls["persist"][0] == (
        fresh_db,
        req,
        "最终答案",
        "safe",
        {
            "assistant_meta": None,
            "assistant_processed": None,
            "timing_meta": {"private_decision": "ok"},
        },
    )
    assert calls["expand"] == ["最终答案"]
    assert calls["payload"][0][1]["status"] == "done"
    assert calls["payload"][0][1]["answer"] == "expanded:最终答案"
    assert calls["payload"][0][1]["unprocessed_logs"] == 7
    assert len(calls["owner_complete"]) == 1
    assert calls["owner_complete"][0].reply == "最终答案"
    assert calls.get("owner_fail") is None
    assert calls["uow_enter"] == [fresh_db]
    assert len(calls["persist"]) == 1
    assert len(background_tasks.tasks) == 1
    assert [event["status"] for event in calls["sse"]].count("done") == 1
    assert calls.get("push") is None
    assert not chat_route_runner._STREAM_FINALIZER_TASKS
    assert events[-1].startswith("data: ")


@pytest.mark.asyncio
async def test_done_event_handoff_does_not_start_duplicate_delivery(monkeypatch):
    from api import chat_route_runner

    calls: dict[str, list[Any]] = {}

    class FakeUnitOfWork:
        def __enter__(self):
            self.db = FakeDb()
            return self

        def __exit__(self, exc_type, exc, tb):
            self.db = None
            return False

    monkeypatch.setattr("core.uow.UnitOfWork", FakeUnitOfWork)
    iterator = chat_route_runner.iter_streaming_chat_response(
        FakeDb(),
        _context(calls, bridge=FakeBridge(answer="done 期间断连回答")),
    )

    while True:
        event = await asyncio.wait_for(anext(iterator), timeout=1)
        if "'status': 'done'" in event:
            break
    await iterator.aclose()
    await _wait_for_stream_finalizers(chat_route_runner)

    assert calls.get("push_envelope") is None
    assert calls.get("push") is None


@pytest.mark.asyncio
async def test_done_event_handoff_completes_claim_without_duplicate_delivery(
    monkeypatch,
):
    from api import chat_route_runner, chat_streaming_result

    calls: dict[str, list[Any]] = {}
    order = []

    class FakeUnitOfWork:
        def __enter__(self):
            self.db = FakeDb()
            return self

        def __exit__(self, exc_type, exc, tb):
            self.db = None
            return False

    class ClaimOwner:
        async def complete(self, _completion):
            order.append("complete")
            return True

        async def fail(self, _error):
            order.append("fail")
            return True

    async def register_delivery(_context, _result):
        order.append("enqueue")
        return object()

    async def deliver_registered(_context, _result, _registered):
        order.append("push")
        return True

    monkeypatch.setattr("core.uow.UnitOfWork", FakeUnitOfWork)
    monkeypatch.setattr(
        chat_streaming_result,
        "register_stream_finalization_delivery",
        register_delivery,
        raising=False,
    )
    monkeypatch.setattr(
        chat_streaming_result,
        "deliver_registered_stream_finalization",
        deliver_registered,
        raising=False,
    )
    iterator = chat_route_runner.iter_streaming_chat_response(
        FakeDb(),
        _context(
            calls,
            bridge=FakeBridge(answer="顺序验证回答"),
            claim_owner=ClaimOwner(),
        ),
    )

    while True:
        event = await asyncio.wait_for(anext(iterator), timeout=1)
        if "'status': 'done'" in event:
            break
    await iterator.aclose()
    await _wait_for_stream_finalizers(chat_route_runner)

    assert order == ["complete"]


@pytest.mark.asyncio
async def test_done_event_handoff_never_calls_disconnect_registration(monkeypatch):
    from api import chat_route_runner, chat_streaming_result

    calls: dict[str, list[Any]] = {}
    registration_error = RuntimeError("outbox registration failed")
    registration_calls = []

    class FakeUnitOfWork:
        def __enter__(self):
            self.db = FakeDb()
            return self

        def __exit__(self, exc_type, exc, tb):
            self.db = None
            return False

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return True

    async def fail_registration(_context, _result):
        registration_calls.append(True)
        raise registration_error

    monkeypatch.setattr("core.uow.UnitOfWork", FakeUnitOfWork)
    monkeypatch.setattr(
        chat_streaming_result,
        "register_stream_finalization_delivery",
        fail_registration,
    )
    iterator = chat_route_runner.iter_streaming_chat_response(
        FakeDb(),
        _context(
            calls,
            bridge=FakeBridge(answer="登记失败回答"),
            claim_owner=ClaimOwner(),
        ),
    )

    while True:
        event = await asyncio.wait_for(anext(iterator), timeout=1)
        if "'status': 'done'" in event:
            break
    await iterator.aclose()
    await _wait_for_stream_finalizers(chat_route_runner)

    assert len(calls["owner_complete"]) == 1
    assert calls.get("owner_fail") is None
    assert registration_calls == []
    assert calls.get("push") is None


@pytest.mark.asyncio
async def test_stream_cancel_while_claim_completion_pending_hands_off_same_settlement(
    monkeypatch,
):
    from api import chat_route_runner

    calls: dict[str, list[Any]] = {}
    completion_started = asyncio.Event()
    release_completion = asyncio.Event()

    class FakeUnitOfWork:
        def __enter__(self):
            self.db = FakeDb()
            return self

        def __exit__(self, exc_type, exc, tb):
            self.db = None
            return False

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            completion_started.set()
            await release_completion.wait()
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return True

    monkeypatch.setattr("core.uow.UnitOfWork", FakeUnitOfWork)
    iterator = chat_route_runner.iter_streaming_chat_response(
        FakeDb(),
        _context(
            calls,
            bridge=FakeBridge(answer="结算取消窗口回答"),
            claim_owner=ClaimOwner(),
        ),
    )
    consumer_task = asyncio.create_task(anext(iterator))

    await asyncio.wait_for(completion_started.wait(), timeout=1)
    consumer_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer_task
    release_completion.set()
    await _wait_for_stream_finalizers(chat_route_runner)

    assert len(calls["owner_complete"]) == 1
    assert calls.get("owner_fail") is None
    assert calls["push"] == [
        (
            "private",
            "u-runner",
            {"answer": "expanded:结算取消窗口回答"},
        )
    ]


@pytest.mark.parametrize("completion_outcome", ["false", "raise"])
@pytest.mark.asyncio
async def test_stream_cancel_while_claim_completion_pending_never_registers_failed_claim(
    monkeypatch,
    completion_outcome,
):
    from api import chat_route_runner, chat_streaming_result

    calls: dict[str, list[Any]] = {}
    completion_started = asyncio.Event()
    release_completion = asyncio.Event()
    registration_calls = []
    delivery_calls = []

    class FakeUnitOfWork:
        def __enter__(self):
            self.db = FakeDb()
            return self

        def __exit__(self, exc_type, exc, tb):
            self.db = None
            return False

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            completion_started.set()
            await release_completion.wait()
            if completion_outcome == "raise":
                raise RuntimeError("claim completion failed")
            return False

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return True

    async def register_delivery(_context, _result):
        registration_calls.append(True)
        return object()

    async def deliver_registered(_context, _result, _registered):
        delivery_calls.append(True)
        return True

    monkeypatch.setattr("core.uow.UnitOfWork", FakeUnitOfWork)
    monkeypatch.setattr(
        chat_streaming_result,
        "register_stream_finalization_delivery",
        register_delivery,
    )
    monkeypatch.setattr(
        chat_streaming_result,
        "deliver_registered_stream_finalization",
        deliver_registered,
    )
    iterator = chat_route_runner.iter_streaming_chat_response(
        FakeDb(),
        _context(
            calls,
            bridge=FakeBridge(answer="失败结算取消窗口回答"),
            claim_owner=ClaimOwner(),
        ),
    )
    consumer_task = asyncio.create_task(anext(iterator))

    await asyncio.wait_for(completion_started.wait(), timeout=1)
    consumer_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer_task
    release_completion.set()
    await _wait_for_stream_finalizers(chat_route_runner)

    assert len(calls["owner_complete"]) == 1
    assert len(calls["owner_fail"]) == 1
    assert registration_calls == []
    assert delivery_calls == []
    assert calls.get("push") is None


@pytest.mark.asyncio
async def test_stream_consumer_cancel_after_finalizer_started_never_persists_with_request_db(
    monkeypatch,
):
    from api import chat_route_runner

    calls: dict[str, list[Any]] = {}
    finalizer_started = asyncio.Event()
    release_finalizer = asyncio.Event()

    class PoisonableRequestDb:
        poisoned = False

        def close(self):
            self.poisoned = True

    request_db = PoisonableRequestDb()
    fresh_db = FakeDb()

    class ClaimOwner:
        def __init__(self):
            self.renewal_task = asyncio.create_task(asyncio.Event().wait())

        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            self.renewal_task.cancel()
            await asyncio.gather(self.renewal_task, return_exceptions=True)
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return True

    class FakeUnitOfWork:
        def __enter__(self):
            self.db = fresh_db
            calls.setdefault("uow_enter", []).append(self.db)
            return self

        def __exit__(self, exc_type, exc, tb):
            calls.setdefault("uow_exit", []).append((exc_type, exc))
            self.db = None
            return False

    monkeypatch.setattr("core.uow.UnitOfWork", FakeUnitOfWork)
    owner = ClaimOwner()
    context = _context(
        calls,
        bridge=FakeBridge(answer="取消窗口答案"),
        claim_owner=owner,
    )
    original_persist = context.callbacks.persist_chat_turn

    async def controlled_finalize(*args: Any, **kwargs: Any) -> None:
        calls.setdefault("controlled_finalize", []).append((args, kwargs))
        finalizer_started.set()
        await release_finalizer.wait()

    def reject_reclaimed_request_db(db: Any, *args: Any, **kwargs: Any) -> int:
        if db is request_db and request_db.poisoned:
            raise RuntimeError("request db was reclaimed before stream finalizer persist")
        return original_persist(db, *args, **kwargs)

    object.__setattr__(context.callbacks, "finalize_private_buffer", controlled_finalize)
    object.__setattr__(context.callbacks, "persist_chat_turn", reject_reclaimed_request_db)
    iterator = chat_route_runner.iter_streaming_chat_response(request_db, context)
    consumer_task = asyncio.create_task(anext(iterator))

    await asyncio.wait_for(finalizer_started.wait(), timeout=1)
    assert len(chat_route_runner._STREAM_FINALIZER_TASKS) == 1
    finalizer_task = next(iter(chat_route_runner._STREAM_FINALIZER_TASKS))

    consumer_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer_task
    request_db.close()
    release_finalizer.set()
    await _wait_for_stream_finalizers(chat_route_runner)

    assert finalizer_task.done()
    assert not chat_route_runner._STREAM_FINALIZER_TASKS
    assert calls["uow_enter"] == [fresh_db]
    assert calls["persist"][0][0] is fresh_db
    assert len(calls["persist"]) == 1
    assert len(calls["owner_complete"]) == 1
    assert calls.get("owner_fail") is None
    assert len(calls["push"]) == 1
    assert calls["push"][0][2]["answer"] == "expanded:取消窗口答案"
    assert all(event.get("status") != "done" for event in calls.get("sse", []))
    assert owner.renewal_task.done()


@pytest.mark.asyncio
async def test_iter_streaming_chat_response_runner_error_persists_placeholder_and_yields_safe_error():
    from api.chat_route_runner import iter_streaming_chat_response

    calls: dict[str, list[Any]] = {}
    bridge_error = RuntimeError("真实内部错误")

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return True

    bridge = FakeBridge(error=bridge_error)
    context = _context(calls, bridge=bridge, claim_owner=ClaimOwner())
    db = FakeDb()

    events = [event async for event in iter_streaming_chat_response(db, context)]

    assert calls["finalize"] == [("u-runner", "（无回复内容）", True)]
    assert calls["persist"][0][2] == "（无回复内容）"
    assert calls["stream_error"] == [()]
    assert any("系统暂时不可用" in event for event in events)
    assert all("真实内部错误" not in event for event in events)
    assert calls["owner_fail"] == [bridge_error]
    assert calls.get("owner_complete") is None
    assert all("done" not in event for event in events)


@pytest.mark.asyncio
async def test_iter_streaming_bridge_fatal_fails_owner_once_and_stops_renewal():
    from api import chat_route_runner

    calls: dict[str, list[Any]] = {}
    fatal = FatalRunnerError("stream bridge fatal")

    class ClaimOwner:
        def __init__(self):
            self.renewal_task = asyncio.create_task(asyncio.Event().wait())

        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            self.renewal_task.cancel()
            await asyncio.gather(self.renewal_task, return_exceptions=True)
            return True

    owner = ClaimOwner()
    context = _context(
        calls,
        bridge=FakeBridge(error=fatal),
        claim_owner=owner,
    )

    try:
        with pytest.raises(FatalRunnerError) as raised:
            _ = [
                event
                async for event in chat_route_runner.iter_streaming_chat_response(
                    FakeDb(),
                    context,
                )
            ]
        await _wait_for_stream_finalizers(chat_route_runner)

        assert raised.value is fatal
        assert calls["owner_fail"] == [fatal]
        assert calls.get("owner_complete") is None
        assert owner.renewal_task.done()
        assert not chat_route_runner._STREAM_FINALIZER_TASKS
    finally:
        if not owner.renewal_task.done():
            owner.renewal_task.cancel()
            await asyncio.gather(owner.renewal_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_iter_streaming_chat_response_prompt_audit_failure_persists_audit_placeholder():
    from api.chat_route_runner import iter_streaming_chat_response

    calls: dict[str, list[Any]] = {}

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return True

    context = _context(
        calls,
        reply_meta={"_agent_result": "prompt_v2_audit_failed"},
        claim_owner=ClaimOwner(),
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
    assert len(calls["owner_fail"]) == 1
    assert "prompt_v2_audit_failed" in str(calls["owner_fail"][0])
    assert calls.get("owner_complete") is None


@pytest.mark.asyncio
async def test_iter_streaming_persist_error_fails_claim_yields_only_safe_error_and_no_done():
    from api.chat_route_runner import iter_streaming_chat_response

    calls: dict[str, list[Any]] = {}
    persist_error = RuntimeError("database is locked")

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return True

    context = _context(
        calls,
        claim_owner=ClaimOwner(),
        persist_error=persist_error,
    )

    events = [event async for event in iter_streaming_chat_response(FakeDb(), context)]

    assert calls["owner_fail"] == [persist_error]
    assert calls.get("owner_complete") is None
    assert any("系统暂时不可用" in event for event in events)
    assert all("'status': 'done'" not in event for event in events)


@pytest.mark.asyncio
async def test_iter_streaming_complete_error_yields_safe_error_without_done_or_delivery():
    from api import chat_route_runner
    from api.chat_route_runner import iter_streaming_chat_response

    calls: dict[str, list[Any]] = {}
    complete_error = RuntimeError("stream claim owner lost")

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            raise complete_error

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return False

    context = _context(calls, claim_owner=ClaimOwner())

    events = [event async for event in iter_streaming_chat_response(FakeDb(), context)]
    await _wait_for_stream_finalizers(chat_route_runner)

    assert len(calls["owner_complete"]) == 1
    assert calls["owner_fail"] == [complete_error]
    assert all("'status': 'done'" not in event for event in events)
    assert any("系统暂时不可用" in event for event in events)
    assert calls.get("push_envelope") is None
    assert calls.get("push") is None


@pytest.mark.asyncio
async def test_iter_streaming_chat_response_client_disconnect_starts_owned_finalizer_without_background_tasks(
    monkeypatch,
):
    from api import chat_route_runner
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
    prior_task_ran = []

    def failing_prior_background_task():
        prior_task_ran.append(True)
        raise RuntimeError("前置 BackgroundTasks 不应参与 stream finalizer")

    background_tasks.add_task(failing_prior_background_task)

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return True

    class FakeUnitOfWork:
        def __enter__(self):
            self.db = FakeDb()
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("core.uow.UnitOfWork", FakeUnitOfWork)
    bridge = WaitingBridge()
    context = _context(
        calls,
        bridge=bridge,
        background_tasks=background_tasks,
        claim_owner=ClaimOwner(),
    )
    db = FakeDb()

    iterator = iter_streaming_chat_response(db, context)
    first = await asyncio.wait_for(anext(iterator), timeout=1)
    assert "处理中" in first
    await started.wait()
    await iterator.aclose()

    assert len(background_tasks.tasks) == 1
    assert prior_task_ran == []
    assert getattr(chat_route_runner, "_STREAM_FINALIZER_TASKS", set())
    assert calls.get("finalize") is None
    assert not release.is_set()
    release.set()
    await _wait_for_stream_finalizers(chat_route_runner)

    assert calls["finalize"] == [("u-runner", "后台答案", True)]
    assert len(calls["owner_complete"]) == 1
    assert calls.get("owner_fail") is None
    assert calls["push"][0][2]["answer"] == "expanded:后台答案"
    assert calls["drain"][0][1].done()


@pytest.mark.asyncio
async def test_stream_disconnect_finalizer_drains_maxsize_one_queue_and_settles_once(
    monkeypatch,
):
    from api import chat_route_runner

    release = asyncio.Event()

    class BoundedBridge(FakeBridge):
        async def handle_message(self, *args: Any, **kwargs: Any) -> str:
            self.calls.append((args, kwargs))
            queue = kwargs["stream_queue"]
            await queue.put({"status": "progress", "text": "先返回"})
            await release.wait()
            await queue.put({"status": "delta", "text": "A"})
            await queue.put({"status": "delta", "text": "B"})
            return "bounded 后台答案"

    calls: dict[str, list[Any]] = {}

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return True

    class FakeUnitOfWork:
        def __enter__(self):
            self.db = FakeDb()
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("core.uow.UnitOfWork", FakeUnitOfWork)
    context = _context(
        calls,
        bridge=BoundedBridge(),
        queue_maxsize=1,
        claim_owner=ClaimOwner(),
    )
    iterator = chat_route_runner.iter_streaming_chat_response(FakeDb(), context)

    assert "先返回" in await asyncio.wait_for(anext(iterator), timeout=1)
    await iterator.aclose()
    release.set()
    await _wait_for_stream_finalizers(chat_route_runner)

    assert len(calls["drain"]) == 1
    assert calls["drain"][0][1].done()
    assert len(calls["owner_complete"]) == 1
    assert calls.get("owner_fail") is None
    assert calls["persist"][0][2] == "bounded 后台答案"


@pytest.mark.asyncio
async def test_stream_consumer_task_cancel_while_runner_pending_drains_and_real_owner_completes_once(
    tmp_path,
    monkeypatch,
):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from api import chat_route_runner
    from core.database import InboundMessageClaim
    from core.inbound_claim_lifecycle import InboundClaimOwner
    from core.inbound_idempotency import (
        ClaimDecisionKind,
        acquire_inbound_claim,
        normalize_inbound_claim_key,
    )
    from core.uow import UnitOfWork as RealUnitOfWork

    db_path = tmp_path / "stream-cancel-real-owner.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    install_base_schema(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    key = normalize_inbound_claim_key(
        "qq",
        "private",
        "private_real-cancel-owner",
        "real-cancel-message",
    )
    with Session() as claim_db:
        decision = acquire_inbound_claim(claim_db, key)
    assert decision.kind == ClaimDecisionKind.ACQUIRED
    assert decision.handle is not None

    owner = InboundClaimOwner(
        decision.handle,
        session_factory=Session,
        renew_interval_seconds=0.01,
    )
    settlements: list[tuple[str, Any]] = []
    real_complete = owner.complete
    real_fail = owner.fail

    async def tracked_complete(completion):
        settlements.append(("complete", completion))
        return await real_complete(completion)

    async def tracked_fail(error):
        settlements.append(("fail", error))
        return await real_fail(error)

    owner.complete = tracked_complete
    owner.fail = tracked_fail
    await owner.start()

    runner_waiting = asyncio.Event()
    release_runner = asyncio.Event()

    class BoundedWaitingBridge(FakeBridge):
        async def handle_message(self, *args: Any, **kwargs: Any) -> str:
            self.calls.append((args, kwargs))
            queue = kwargs["stream_queue"]
            await queue.put({"status": "progress", "text": "取消前事件"})
            runner_waiting.set()
            await release_runner.wait()
            await queue.put({"status": "delta", "text": "A"})
            await queue.put({"status": "delta", "text": "B"})
            return "真实 owner 后台答案"

    class PoisonableRequestDb:
        poisoned = False

        def close(self):
            self.poisoned = True

    request_db = PoisonableRequestDb()
    calls: dict[str, list[Any]] = {}
    persist_sessions: list[Any] = []
    runner_tasks: list[asyncio.Task[Any]] = []
    drain_tasks: list[asyncio.Task[Any]] = []

    monkeypatch.setattr(
        "core.uow.UnitOfWork",
        lambda: RealUnitOfWork(session_factory=Session),
    )
    context = _context(
        calls,
        req=_request(
            user_id="real-cancel-owner",
            session_id="private_real-cancel-owner",
        ),
        bridge=BoundedWaitingBridge(),
        queue_maxsize=1,
        claim_owner=owner,
    )
    original_persist = context.callbacks.persist_chat_turn
    original_drain = context.callbacks.drain_stream_queue_until_task_done

    def track_fresh_persist(db: Any, *args: Any, **kwargs: Any) -> int:
        if db is request_db or request_db.poisoned and db is request_db:
            raise RuntimeError("request db must not be used after consumer cancellation")
        persist_sessions.append(db)
        return original_persist(db, *args, **kwargs)

    async def track_drain(
        stream_queue: asyncio.Queue[Any],
        runner_task: asyncio.Task[Any],
    ) -> None:
        current = asyncio.current_task()
        assert current is not None
        drain_tasks.append(current)
        runner_tasks.append(runner_task)
        await original_drain(stream_queue, runner_task)

    object.__setattr__(context.callbacks, "persist_chat_turn", track_fresh_persist)
    object.__setattr__(
        context.callbacks,
        "drain_stream_queue_until_task_done",
        track_drain,
    )
    iterator = chat_route_runner.iter_streaming_chat_response(request_db, context)
    received: list[str] = []
    received_first = asyncio.Event()

    async def consume_stream() -> None:
        async for chunk in iterator:
            received.append(chunk)
            received_first.set()

    consumer_task = asyncio.create_task(consume_stream())
    finalizer_task: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(received_first.wait(), timeout=1)
        await asyncio.wait_for(runner_waiting.wait(), timeout=1)
        assert not consumer_task.done()

        consumer_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer_task
        owned_tasks = list(chat_route_runner._STREAM_FINALIZER_TASKS)
        finalizer_tasks = [
            task
            for task in owned_tasks
            if task.get_name().startswith("chat-stream-finalizer:")
        ]
        assert len(finalizer_tasks) == 1
        finalizer_task = finalizer_tasks[0]

        request_db.close()
        release_runner.set()
        await _wait_for_stream_finalizers(chat_route_runner)

        assert received and "取消前事件" in received[0]
        assert len(settlements) == 1
        assert settlements[0][0] == "complete"
        assert persist_sessions and all(db is not request_db for db in persist_sessions)
        assert len(runner_tasks) == 1 and runner_tasks[0].done()
        assert len(drain_tasks) == 1 and drain_tasks[0].done()
        assert finalizer_task.done()
        assert not chat_route_runner._STREAM_FINALIZER_TASKS
        assert owner.renewal_task is not None and owner.renewal_task.done()
        assert calls["push"][0][2]["answer"] == "expanded:真实 owner 后台答案"

        with Session() as verify_db:
            row = verify_db.scalar(select(InboundMessageClaim))
            assert row is not None
            assert row.status == "completed"
            assert row.response_json
    finally:
        if not consumer_task.done():
            consumer_task.cancel()
            await asyncio.gather(consumer_task, return_exceptions=True)
        for task in list(chat_route_runner._STREAM_FINALIZER_TASKS):
            if not task.done():
                task.cancel()
        if chat_route_runner._STREAM_FINALIZER_TASKS:
            await asyncio.gather(
                *list(chat_route_runner._STREAM_FINALIZER_TASKS),
                return_exceptions=True,
            )
        if owner.renewal_task is not None and not owner.renewal_task.done():
            await owner.fail("test cleanup")
        engine.dispose()


@pytest.mark.asyncio
async def test_registry_finalizer_cancel_stops_blocked_runner_drain_and_owner_renewal_once():
    from api import chat_route_runner

    calls: dict[str, list[Any]] = {}
    second_put_blocking = asyncio.Event()
    drain_started = asyncio.Event()
    release_drain = asyncio.Event()
    runner_tasks: list[asyncio.Task[Any]] = []
    drain_tasks: list[asyncio.Task[Any]] = []

    class BoundedBridge(FakeBridge):
        async def handle_message(self, *args: Any, **kwargs: Any) -> str:
            self.calls.append((args, kwargs))
            queue = kwargs["stream_queue"]
            runner = asyncio.current_task()
            assert runner is not None
            runner_tasks.append(runner)
            await queue.put({"status": "progress", "text": "首事件"})
            await queue.put({"status": "delta", "text": "填满队列"})
            second_put_blocking.set()
            await queue.put({"status": "delta", "text": "阻塞 producer"})
            return "不应完成"

    class ClaimOwner:
        def __init__(self):
            self.renewal_task = asyncio.create_task(asyncio.Event().wait())

        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            self.renewal_task.cancel()
            await asyncio.gather(self.renewal_task, return_exceptions=True)
            return True

    owner = ClaimOwner()
    context = _context(
        calls,
        bridge=BoundedBridge(),
        queue_maxsize=1,
        claim_owner=owner,
    )
    original_drain = context.callbacks.drain_stream_queue_until_task_done

    async def controlled_drain(stream_queue, runner_task):
        current = asyncio.current_task()
        assert current is not None
        drain_tasks.append(current)
        drain_started.set()
        await release_drain.wait()
        await original_drain(stream_queue, runner_task)

    object.__setattr__(
        context.callbacks,
        "drain_stream_queue_until_task_done",
        controlled_drain,
    )
    iterator = chat_route_runner.iter_streaming_chat_response(FakeDb(), context)
    finalizer_task: asyncio.Task[Any] | None = None
    try:
        assert "首事件" in await asyncio.wait_for(anext(iterator), timeout=1)
        await asyncio.wait_for(second_put_blocking.wait(), timeout=1)
        await iterator.aclose()
        finalizer_tasks = [
            task
            for task in chat_route_runner._STREAM_FINALIZER_TASKS
            if task.get_name().startswith("chat-stream-finalizer:")
        ]
        assert len(finalizer_tasks) == 1
        finalizer_task = finalizer_tasks[0]
        await asyncio.wait_for(drain_started.wait(), timeout=1)

        finalizer_task.cancel()
        await _wait_for_stream_finalizers(chat_route_runner)
        assert finalizer_task.done()
        await asyncio.gather(finalizer_task, return_exceptions=True)
        assert calls.get("owner_complete") is None
        assert len(calls["owner_fail"]) == 1
        assert len(runner_tasks) == 1 and runner_tasks[0].done()
        assert len(drain_tasks) == 1 and drain_tasks[0].done()
        assert owner.renewal_task.done()
        assert not chat_route_runner._STREAM_FINALIZER_TASKS
    finally:
        release_drain.set()
        if finalizer_task is not None and not finalizer_task.done():
            finalizer_task.cancel()
        if finalizer_task is not None:
            await asyncio.gather(finalizer_task, return_exceptions=True)
        for task in runner_tasks + drain_tasks:
            if not task.done():
                task.cancel()
        if runner_tasks or drain_tasks:
            await asyncio.gather(*runner_tasks, *drain_tasks, return_exceptions=True)
        if not owner.renewal_task.done():
            owner.renewal_task.cancel()
            await asyncio.gather(owner.renewal_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_stream_disconnect_runner_done_race_uses_same_finalizer_and_settles_once(
    monkeypatch,
):
    from api import chat_route_runner

    bridge_done = asyncio.Event()

    class DoneBridge(FakeBridge):
        async def handle_message(self, *args: Any, **kwargs: Any) -> str:
            self.calls.append((args, kwargs))
            await kwargs["stream_queue"].put({"status": "progress", "text": "done-race"})
            bridge_done.set()
            return "runner 已完成答案"

    calls: dict[str, list[Any]] = {}

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return True

    class FakeUnitOfWork:
        def __enter__(self):
            self.db = FakeDb()
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("core.uow.UnitOfWork", FakeUnitOfWork)
    background_tasks = FakeBackgroundTasks()
    context = _context(
        calls,
        bridge=DoneBridge(),
        background_tasks=background_tasks,
        claim_owner=ClaimOwner(),
    )
    iterator = chat_route_runner.iter_streaming_chat_response(FakeDb(), context)

    assert "done-race" in await asyncio.wait_for(anext(iterator), timeout=1)
    await asyncio.wait_for(bridge_done.wait(), timeout=1)
    await iterator.aclose()
    await _wait_for_stream_finalizers(chat_route_runner)

    assert background_tasks.tasks == []
    assert len(calls["owner_complete"]) == 1
    assert calls.get("owner_fail") is None
    assert calls["persist"][0][2] == "runner 已完成答案"


@pytest.mark.asyncio
async def test_stream_disconnect_background_persist_failure_fails_claim_and_registry_cleans(
    monkeypatch,
):
    from api import chat_route_runner

    release = asyncio.Event()
    persist_error = RuntimeError("background database is locked")

    class WaitingBridge(FakeBridge):
        async def handle_message(self, *args: Any, **kwargs: Any) -> str:
            self.calls.append((args, kwargs))
            await kwargs["stream_queue"].put({"status": "progress", "text": "等待失败"})
            await release.wait()
            return "无法持久化"

    calls: dict[str, list[Any]] = {}

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return True

    class FakeUnitOfWork:
        def __enter__(self):
            self.db = FakeDb()
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("core.uow.UnitOfWork", FakeUnitOfWork)
    context = _context(
        calls,
        bridge=WaitingBridge(),
        claim_owner=ClaimOwner(),
        persist_error=persist_error,
    )
    iterator = chat_route_runner.iter_streaming_chat_response(FakeDb(), context)

    assert "等待失败" in await asyncio.wait_for(anext(iterator), timeout=1)
    await iterator.aclose()
    release.set()
    await _wait_for_stream_finalizers(chat_route_runner)

    assert calls["owner_fail"] == [persist_error]
    assert calls.get("owner_complete") is None
    assert calls.get("push") is None
    assert not chat_route_runner._STREAM_FINALIZER_TASKS


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


@pytest.mark.parametrize("stage", ["bridge", "finalize", "persist"])
@pytest.mark.asyncio
async def test_run_non_streaming_fatal_base_exception_fails_owner_once_and_stops_renewal(
    stage,
):
    from api.chat_route_runner import run_non_streaming_chat_response

    calls: dict[str, list[Any]] = {}
    fatal = FatalRunnerError(f"nonstream {stage} fatal")

    class ClaimOwner:
        def __init__(self):
            self.renewal_task = asyncio.create_task(asyncio.Event().wait())

        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            self.renewal_task.cancel()
            await asyncio.gather(self.renewal_task, return_exceptions=True)
            return True

    owner = ClaimOwner()
    context = _context(
        calls,
        bridge=FakeBridge(error=fatal) if stage == "bridge" else FakeBridge(),
        claim_owner=owner,
        persist_error=fatal if stage == "persist" else None,
    )
    if stage == "finalize":
        async def fatal_finalize(*_args, **_kwargs):
            raise fatal

        object.__setattr__(
            context.callbacks,
            "finalize_non_streaming_chat_result",
            fatal_finalize,
        )

    try:
        with pytest.raises(FatalRunnerError) as raised:
            await run_non_streaming_chat_response(FakeDb(), context)

        assert raised.value is fatal
        assert calls["owner_fail"] == [fatal]
        assert calls.get("owner_complete") is None
        assert owner.renewal_task.done()
    finally:
        if not owner.renewal_task.done():
            owner.renewal_task.cancel()
            await asyncio.gather(owner.renewal_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_run_non_streaming_bridge_error_logger_failure_cannot_skip_claim_fail(
    monkeypatch,
):
    from api import chat_route_runner

    calls: dict[str, list[Any]] = {}
    bridge_error = RuntimeError("primary bridge failure")
    log_error = RuntimeError("broken log handler")

    class ClaimOwner:
        def __init__(self):
            self.renewal_task = asyncio.create_task(asyncio.Event().wait())

        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            self.renewal_task.cancel()
            await asyncio.gather(self.renewal_task, return_exceptions=True)
            return True

    def broken_error_log(*_args, **_kwargs):
        raise log_error

    owner = ClaimOwner()
    context = _context(
        calls,
        bridge=FakeBridge(error=bridge_error),
        claim_owner=owner,
    )
    monkeypatch.setattr(chat_route_runner.logger, "error", broken_error_log)

    try:
        result = await chat_route_runner.run_non_streaming_chat_response(FakeDb(), context)

        assert result.http_error is not None
        assert result.http_error.status_code == 502
        assert calls["owner_fail"] == [bridge_error]
        assert owner.renewal_task.done()
    finally:
        if not owner.renewal_task.done():
            owner.renewal_task.cancel()
            await asyncio.gather(owner.renewal_task, return_exceptions=True)


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


@pytest.mark.asyncio
async def test_run_non_streaming_owner_persists_then_completes_before_evolution_and_returns_payload():
    from api.chat_route_runner import run_non_streaming_chat_response

    calls: dict[str, list[Any]] = {}
    background_tasks = FakeBackgroundTasks()

    class ClaimOwner:
        async def complete(self, completion):
            assert calls["persist"][0][2] == "非流式 owner 答案"
            assert background_tasks.tasks == []
            calls.setdefault("owner_complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return True

    bridge = FakeBridge(answer="非流式 owner 答案")
    context = _context(
        calls,
        bridge=bridge,
        background_tasks=background_tasks,
        pending=8,
        claim_owner=ClaimOwner(),
    )

    result = await run_non_streaming_chat_response(FakeDb(), context)

    assert len(bridge.calls) == 1
    assert len(calls["owner_complete"]) == 1
    assert calls["owner_complete"][0].reply == "非流式 owner 答案"
    assert calls["owner_complete"][0].unprocessed_logs == 8
    assert calls.get("owner_fail") is None
    assert len(background_tasks.tasks) == 1
    assert result.payload["answer"] == "expanded:非流式 owner 答案"


@pytest.mark.asyncio
async def test_run_non_streaming_bridge_error_fails_owner_and_keeps_safe_502():
    from api.chat_route_runner import run_non_streaming_chat_response

    calls: dict[str, list[Any]] = {}
    bridge_error = RuntimeError("bridge-internal-secret")

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return True

    context = _context(
        calls,
        bridge=FakeBridge(error=bridge_error),
        claim_owner=ClaimOwner(),
    )

    result = await run_non_streaming_chat_response(FakeDb(), context)

    assert calls["owner_fail"] == [bridge_error]
    assert calls.get("owner_complete") is None
    assert result.payload is None
    assert result.http_error.status_code == 502
    assert result.http_error.detail == "系统暂时不可用，请稍后再试"
    assert "bridge-internal-secret" not in result.http_error.detail


@pytest.mark.asyncio
async def test_run_non_streaming_prompt_audit_failure_fails_owner_and_keeps_safe_500():
    from api.chat_route_runner import run_non_streaming_chat_response

    calls: dict[str, list[Any]] = {}

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return True

    context = _context(
        calls,
        reply_meta={"_agent_result": "prompt_v2_audit_failed"},
        claim_owner=ClaimOwner(),
    )

    result = await run_non_streaming_chat_response(FakeDb(), context)

    assert len(calls["owner_fail"]) == 1
    assert "prompt_v2_audit_failed" in str(calls["owner_fail"][0])
    assert calls.get("owner_complete") is None
    assert result.payload is None
    assert result.http_error.status_code == 500
    assert result.http_error.detail == "系统暂时不可用，请稍后再试"


@pytest.mark.asyncio
async def test_run_non_streaming_persist_error_fails_owner_and_reraises_original():
    from api.chat_route_runner import run_non_streaming_chat_response

    calls: dict[str, list[Any]] = {}
    persist_error = RuntimeError("database is locked")

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return True

    context = _context(calls, claim_owner=ClaimOwner())

    async def fail_during_persist(db, result_context):
        calls.setdefault("persist_attempt", []).append((db, result_context))
        raise persist_error

    object.__setattr__(context.callbacks, "finalize_non_streaming_chat_result", fail_during_persist)

    with pytest.raises(RuntimeError) as raised:
        await run_non_streaming_chat_response(FakeDb(), context)

    assert raised.value is persist_error
    assert len(calls["persist_attempt"]) == 1
    assert calls["owner_fail"] == [persist_error]
    assert calls.get("owner_complete") is None


@pytest.mark.asyncio
async def test_run_non_streaming_completion_dto_error_fails_owner_after_persist(
    monkeypatch,
):
    from api import chat_non_streaming_result
    from api.chat_route_runner import run_non_streaming_chat_response

    calls: dict[str, list[Any]] = {}
    dto_error = ValueError("completion dto rejected")

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return True

    def reject_completion(**_kwargs):
        raise dto_error

    monkeypatch.setattr(
        chat_non_streaming_result,
        "chat_response_contract",
        SimpleNamespace(build_completed_inbound_response=reject_completion),
        raising=False,
    )
    context = _context(calls, claim_owner=ClaimOwner())

    with pytest.raises(ValueError) as raised:
        await run_non_streaming_chat_response(FakeDb(), context)

    assert raised.value is dto_error
    assert calls["persist"]
    assert calls["owner_fail"] == [dto_error]
    assert calls.get("owner_complete") is None


@pytest.mark.parametrize(
    "complete_error",
    [RuntimeError("complete write failed"), RuntimeError("owner lost")],
    ids=["complete-error", "owner-lost"],
)
@pytest.mark.asyncio
async def test_run_non_streaming_complete_error_fails_once_and_never_returns_success(
    complete_error,
):
    from api.chat_route_runner import run_non_streaming_chat_response

    calls: dict[str, list[Any]] = {}
    background_tasks = FakeBackgroundTasks()

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            raise complete_error

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return True

    context = _context(
        calls,
        background_tasks=background_tasks,
        pending=8,
        claim_owner=ClaimOwner(),
    )

    with pytest.raises(RuntimeError) as raised:
        await run_non_streaming_chat_response(FakeDb(), context)

    assert raised.value is complete_error
    assert len(calls["owner_complete"]) == 1
    assert calls["owner_fail"] == [complete_error]
    assert background_tasks.tasks == []


@pytest.mark.asyncio
async def test_run_non_streaming_cancelled_error_best_effort_fails_owner_and_reraises_original():
    from api.chat_route_runner import run_non_streaming_chat_response

    calls: dict[str, list[Any]] = {}
    cancellation = asyncio.CancelledError("client cancelled")

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("owner_complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return True

    context = _context(
        calls,
        bridge=FakeBridge(error=cancellation),
        claim_owner=ClaimOwner(),
    )

    with pytest.raises(asyncio.CancelledError) as raised:
        await run_non_streaming_chat_response(FakeDb(), context)

    assert raised.value is cancellation
    assert calls["owner_fail"] == [cancellation]
    assert calls.get("owner_complete") is None


def test_parent_chat_route_delegates_bridge_runner_and_keeps_fastapi_boundary():
    source = _source("api/routes.py")

    assert "chat_route_runner" in source
    assert "StreamingResponse(" in source
    assert "chat_route_runner.ColdChatStreamingBody" in source
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


@pytest.mark.asyncio
async def test_claimed_nonstream_checkpoint_failure_skips_bridge_and_persistence():
    from dataclasses import replace

    from api.chat_route_runner import run_non_streaming_chat_response

    calls: dict[str, list[Any]] = {}
    checkpoint_error = RuntimeError("owner checkpoint failed")

    class ClaimOwner:
        async def checkpoint(self):
            raise checkpoint_error

        async def wait_unusable(self):
            await asyncio.Event().wait()

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return False

    bridge = FakeBridge()
    context = replace(
        _context(calls, bridge=bridge, claim_owner=ClaimOwner()),
        claim_key=object(),
        request_sha256="a" * 64,
    )

    result = await run_non_streaming_chat_response(FakeDb(), context)

    assert result.http_error is not None
    assert result.http_error.status_code == 502
    assert bridge.calls == []
    assert calls.get("persist") is None
    assert calls["owner_fail"] == [checkpoint_error]


@pytest.mark.asyncio
async def test_claimed_nonstream_owner_unusable_cancels_running_bridge():
    from dataclasses import replace

    from api.chat_route_runner import run_non_streaming_chat_response

    calls: dict[str, list[Any]] = {}
    bridge_started = asyncio.Event()
    bridge_cancelled = asyncio.Event()
    owner_lost = RuntimeError("owner lost while bridge running")

    class BlockingBridge(FakeBridge):
        async def handle_message(self, *args: Any, **kwargs: Any) -> str:
            self.calls.append((args, kwargs))
            bridge_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                bridge_cancelled.set()
                raise

    class ClaimOwner:
        async def checkpoint(self):
            return True

        async def wait_unusable(self):
            await bridge_started.wait()
            raise owner_lost

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return False

    bridge = BlockingBridge()
    context = replace(
        _context(calls, bridge=bridge, claim_owner=ClaimOwner()),
        claim_key=object(),
        request_sha256="a" * 64,
    )

    result = await run_non_streaming_chat_response(FakeDb(), context)

    assert result.http_error is not None
    assert result.http_error.status_code == 502
    assert bridge_cancelled.is_set()
    assert calls.get("persist") is None
    assert calls["owner_fail"] == [owner_lost]


@pytest.mark.asyncio
async def test_claimed_nonstream_post_bridge_checkpoint_failure_skips_persistence():
    from dataclasses import replace

    from api.chat_route_runner import run_non_streaming_chat_response

    calls: dict[str, list[Any]] = {}
    checkpoint_error = RuntimeError("owner lost after bridge")

    class ClaimOwner:
        def __init__(self):
            self.checkpoints = 0

        async def checkpoint(self):
            self.checkpoints += 1
            if self.checkpoints == 2:
                raise checkpoint_error
            return True

        async def wait_unusable(self):
            await asyncio.Event().wait()

        async def fail(self, error):
            calls.setdefault("owner_fail", []).append(error)
            return False

    bridge = FakeBridge()
    context = replace(
        _context(calls, bridge=bridge, claim_owner=ClaimOwner()),
        claim_key=object(),
        request_sha256="a" * 64,
    )

    result = await run_non_streaming_chat_response(FakeDb(), context)

    assert result.http_error is not None
    assert result.http_error.status_code == 502
    assert len(bridge.calls) == 1
    assert calls.get("persist") is None
    assert calls["owner_fail"] == [checkpoint_error]

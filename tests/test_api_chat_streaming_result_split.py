from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]


class FatalStreamFinalizerError(BaseException):
    pass


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


@dataclass
class FakePushEnvelope:
    target_type: str
    target_id: str
    envelope: dict[str, Any]


class FakeDb:
    pass


def _request(user_id: str = "u-stream", session_id: str = "private_u-stream") -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        session_id=session_id,
        query="流式请求",
        files=[],
        sender_name="",
        session_name=None,
        message_id="",
        source_message_ids=[],
        client_meta={"platform": "qq"},
    )


async def _runner_sets(result_holder: dict[str, Any], key: str, value: Any) -> None:
    result_holder[key] = value


async def _runner_raises(error: BaseException) -> None:
    raise error


def _callbacks(
    calls: dict[str, list[Any]],
    *,
    reply_meta: dict[str, Any] | None = None,
    push_ok: bool = True,
    persist_error: BaseException | None = None,
    finalize_error: BaseException | None = None,
    envelope_error: BaseException | None = None,
    push_error: BaseException | None = None,
):
    from api.chat_streaming_result import ChatStreamResultCallbacks

    async def drain_stream_queue_until_task_done(stream_queue, runner_task):
        calls.setdefault("drain", []).append((stream_queue, runner_task))

    def pop_bridge_reply_meta(bridge, session_id: str):
        calls.setdefault("pop_meta", []).append((bridge, session_id))
        return reply_meta or {}

    def private_prompt_audit_failure_meta():
        calls.setdefault("audit_meta", []).append(())
        return {"kind": "empty_reply", "no_context": True, "agent_result": "prompt_v2_audit_failed"}

    async def finalize_private_buffer(user_id: str, answer: str | None = None, *, clear_window: bool = True):
        calls.setdefault("finalize", []).append((user_id, answer, clear_window))
        if finalize_error is not None:
            raise finalize_error

    def persist_chat_turn(db, req, answer, guardrail_status=None, **kwargs):
        calls.setdefault("persist", []).append((db, req, answer, guardrail_status, kwargs))
        if persist_error is not None:
            raise persist_error
        return 3

    def expand_chat_transport_answer(answer: str) -> str:
        calls.setdefault("expand", []).append(answer)
        return f"expanded:{answer}"

    def build_chat_push_envelope(req, **kwargs):
        calls.setdefault("envelope", []).append((req, kwargs))
        if envelope_error is not None:
            raise envelope_error
        return FakePushEnvelope(
            target_type="private",
            target_id=req.user_id,
            envelope={"reply": kwargs["answer"], "meta": {"chat_type": kwargs["chat_type"]}},
        )

    async def push_envelope_to_qq(target_type: str, target_id: str, envelope: dict[str, Any]) -> bool:
        calls.setdefault("push", []).append((target_type, target_id, envelope))
        if push_error is not None:
            raise push_error
        return push_ok

    return ChatStreamResultCallbacks(
        drain_stream_queue_until_task_done=drain_stream_queue_until_task_done,
        pop_bridge_reply_meta=pop_bridge_reply_meta,
        private_prompt_audit_failure_meta=private_prompt_audit_failure_meta,
        finalize_private_buffer=finalize_private_buffer,
        persist_chat_turn=persist_chat_turn,
        expand_chat_transport_answer=expand_chat_transport_answer,
        build_chat_push_envelope=build_chat_push_envelope,
        push_envelope_to_qq=push_envelope_to_qq,
    )


def _context(
    result_holder: dict[str, Any],
    runner_task: asyncio.Task[Any],
    calls: dict[str, list[Any]],
    *,
    req: Any | None = None,
    reply_meta: dict[str, Any] | None = None,
    claim_owner: Any | None = None,
    persist_error: BaseException | None = None,
    finalize_error: BaseException | None = None,
    envelope_error: BaseException | None = None,
    push_error: BaseException | None = None,
    claim_key: Any | None = None,
):
    from api.chat_streaming_result import ChatStreamResultContext

    req = req or _request()
    return ChatStreamResultContext(
        req=req,
        persist_req=req,
        bridge=object(),
        result_holder=result_holder,
        runner_task=runner_task,
        stream_queue=asyncio.Queue(maxsize=1),
        platform="qq",
        bridge_meta={"chat_type": "private", "is_group": False},
        guardrail_status="safe",
        private_timing_meta={"private_decision": "ok"},
        empty_assistant_placeholder="（无回复内容）",
        callbacks=_callbacks(
            calls,
            reply_meta=reply_meta,
            persist_error=persist_error,
            finalize_error=finalize_error,
            envelope_error=envelope_error,
            push_error=push_error,
        ),
        claim_owner=claim_owner,
        claim_key=claim_key,
    )


def test_chat_streaming_result_module_does_not_import_parent_routes_or_sync_awaitable():
    source = _source("api/chat_streaming_result.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source
    assert "get_bridge(" not in source
    assert "get_guardrail(" not in source
    assert "from core.daily_digest import push_envelope_to_qq" not in source
    assert "import core.daily_digest" not in source


@pytest.mark.asyncio
async def test_claimed_stream_push_uses_persistent_outbox_service(monkeypatch):
    from api.chat_streaming_result import (
        ChatStreamFinalizationResult,
        push_stream_finalization_result,
    )
    from core.inbound_idempotency import CompletedInboundResponse, InboundClaimKey

    calls: dict[str, list[Any]] = {}
    key = InboundClaimKey(
        platform="qq",
        chat_type="private",
        session_id="private_u-outbox",
        message_id="m-outbox",
    )
    context = _context(
        {},
        asyncio.create_task(asyncio.sleep(0)),
        calls,
        req=_request(user_id="u-outbox", session_id="private_u-outbox"),
        claim_key=key,
    )
    enqueue_calls = []
    delivery_calls = []

    async def fake_enqueue(**kwargs):
        enqueue_calls.append(kwargs)
        return SimpleNamespace(row_id=7, status="pending")

    async def fake_deliver(**kwargs):
        delivery_calls.append(kwargs)
        return SimpleNamespace(status="delivered")

    monkeypatch.setattr(
        "core.chat_delivery_service.enqueue_chat_response_delivery",
        fake_enqueue,
    )
    monkeypatch.setattr(
        "core.chat_delivery_service.deliver_chat_delivery",
        fake_deliver,
    )
    result = ChatStreamFinalizationResult(
        answer="已持久化回答",
        transport_answer="expanded:已持久化回答",
        reply_meta=None,
        pending=0,
        completion=CompletedInboundResponse(
            outcome="respond",
            reply="已持久化回答",
        ),
    )

    assert await push_stream_finalization_result(context, result) is True
    assert len(enqueue_calls) == 1
    assert enqueue_calls[0]["key"] == key
    assert enqueue_calls[0]["target_type"] == "private"
    assert enqueue_calls[0]["target_id"] == "u-outbox"
    assert enqueue_calls[0]["envelope"]["reply"] == "expanded:已持久化回答"
    assert delivery_calls == [
        {
            "publisher": context.callbacks.push_envelope_to_qq,
            "row_id": 7,
        }
    ]
    assert calls.get("push") is None


@pytest.mark.asyncio
async def test_persist_stream_result_success_uses_result_holder_and_request_db():
    from api.chat_streaming_result import persist_stream_result_after_runner_done

    calls: dict[str, list[Any]] = {}
    result_holder: dict[str, Any] = {}
    runner_task = asyncio.create_task(_runner_sets(result_holder, "answer", "最终答案"))
    req = _request(user_id="u-success")

    class ClaimOwner:
        async def complete(self, completion):
            assert calls["persist"]
            calls.setdefault("complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("fail", []).append(error)
            return True

    context = _context(
        result_holder,
        runner_task,
        calls,
        req=req,
        reply_meta={
            "send_mode": "quote",
            "_agent_result": "must-not-persist",
        },
        claim_owner=ClaimOwner(),
    )
    db = FakeDb()

    result = await persist_stream_result_after_runner_done(
        context,
        push=False,
        persist_db=db,
    )

    assert calls["finalize"] == [("u-success", "最终答案", True)]
    assert calls["persist"] == [
        (
            db,
            req,
            "最终答案",
            "safe",
            {
                "assistant_meta": None,
                "assistant_processed": None,
                "timing_meta": {"private_decision": "ok"},
            },
        )
    ]
    assert len(calls["complete"]) == 1
    assert calls["complete"][0].reply == "最终答案"
    assert calls["complete"][0].reply_meta == {"send_mode": "quote"}
    assert calls["complete"][0].unprocessed_logs == 3
    assert calls.get("fail") is None
    assert result.answer == "最终答案"
    assert result.transport_answer == "expanded:最终答案"
    assert result.pending == 3
    assert result.completion is calls["complete"][0]
    assert calls.get("push") is None


@pytest.mark.asyncio
async def test_persist_stream_result_prompt_audit_failure_uses_meta_and_skips_push():
    from api.chat_streaming_result import persist_stream_result_after_runner_done

    calls: dict[str, list[Any]] = {}
    result_holder: dict[str, Any] = {}
    runner_task = asyncio.create_task(_runner_sets(result_holder, "answer", ""))

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("fail", []).append(error)
            return True

    context = _context(
        result_holder,
        runner_task,
        calls,
        req=_request(user_id="u-audit"),
        reply_meta={"_agent_result": "prompt_v2_audit_failed"},
        claim_owner=ClaimOwner(),
    )
    db = FakeDb()

    with pytest.raises(RuntimeError, match="prompt_v2_audit_failed"):
        await persist_stream_result_after_runner_done(
            context,
            push=True,
            persist_db=db,
        )

    assert calls["audit_meta"] == [()]
    assert calls["finalize"] == [("u-audit", "（无回复内容）", True)]
    persisted = calls["persist"][0]
    assert persisted[0] is db
    assert persisted[2] == "（无回复内容）"
    assert persisted[4]["assistant_meta"] == {
        "kind": "empty_reply",
        "no_context": True,
        "agent_result": "prompt_v2_audit_failed",
    }
    assert persisted[4]["assistant_processed"] == 1
    assert len(calls["fail"]) == 1
    assert calls.get("complete") is None
    assert calls.get("push") is None


@pytest.mark.asyncio
async def test_persist_stream_result_background_push_uses_unit_of_work_and_drain(monkeypatch):
    from api.chat_streaming_result import persist_stream_result_after_runner_done

    calls: dict[str, list[Any]] = {}
    result_holder: dict[str, Any] = {}
    runner_task = asyncio.create_task(_runner_sets(result_holder, "answer", "后台答案"))

    class ClaimOwner:
        async def complete(self, completion):
            assert calls["persist"]
            assert calls.get("push") is None
            calls.setdefault("complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("fail", []).append(error)
            return True

    context = _context(
        result_holder,
        runner_task,
        calls,
        req=_request(user_id="u-bg"),
        claim_owner=ClaimOwner(),
    )
    uow_db = FakeDb()

    class FakeUnitOfWork:
        def __enter__(self):
            self.db = uow_db
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("core.uow.UnitOfWork", FakeUnitOfWork)

    await persist_stream_result_after_runner_done(
        context,
        push=True,
        persist_db=None,
        drain_stream=True,
    )

    assert calls["drain"] == [(context.stream_queue, runner_task)]
    assert calls["persist"][0][0] is uow_db
    assert len(calls["complete"]) == 1
    assert calls.get("fail") is None
    assert calls["expand"] == ["后台答案"]
    assert calls["envelope"][0][1]["answer"] == "expanded:后台答案"
    assert calls["push"] == [
        ("private", "u-bg", {"reply": "expanded:后台答案", "meta": {"chat_type": "private"}})
    ]


@pytest.mark.asyncio
async def test_persist_stream_result_bridge_error_preserves_original_and_fails_owner():
    from api.chat_streaming_result import persist_stream_result_after_runner_done

    calls: dict[str, list[Any]] = {}
    bridge_error = RuntimeError("bridge original error")
    result_holder: dict[str, Any] = {"error": bridge_error}
    runner_task = asyncio.create_task(asyncio.sleep(0))

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("fail", []).append(error)
            return True

    context = _context(
        result_holder,
        runner_task,
        calls,
        claim_owner=ClaimOwner(),
    )

    with pytest.raises(RuntimeError) as raised:
        await persist_stream_result_after_runner_done(
            context,
            push=False,
            persist_db=FakeDb(),
        )

    assert raised.value is bridge_error
    assert calls["fail"] == [bridge_error]
    assert calls.get("complete") is None
    assert calls.get("push") is None


@pytest.mark.parametrize("cleanup_stage", ["finalize", "persist"])
@pytest.mark.asyncio
async def test_stream_bridge_primary_error_wins_over_secondary_cleanup_error(
    cleanup_stage,
):
    from api.chat_streaming_result import persist_stream_result_after_runner_done

    calls: dict[str, list[Any]] = {}
    bridge_error = RuntimeError("primary bridge error")
    cleanup_error = RuntimeError(f"secondary {cleanup_stage} error")
    result_holder: dict[str, Any] = {"error": bridge_error}
    runner_task = asyncio.create_task(asyncio.sleep(0))

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("fail", []).append(error)
            return True

    context = _context(
        result_holder,
        runner_task,
        calls,
        claim_owner=ClaimOwner(),
        finalize_error=cleanup_error if cleanup_stage == "finalize" else None,
        persist_error=cleanup_error if cleanup_stage == "persist" else None,
    )

    with pytest.raises(RuntimeError) as raised:
        await persist_stream_result_after_runner_done(
            context,
            push=False,
            persist_db=FakeDb(),
        )

    assert raised.value is bridge_error
    assert calls["fail"] == [bridge_error]
    assert calls.get("complete") is None


@pytest.mark.asyncio
async def test_stream_audit_primary_error_wins_over_finalize_cleanup_error():
    from api.chat_streaming_result import persist_stream_result_after_runner_done

    calls: dict[str, list[Any]] = {}
    cleanup_error = RuntimeError("secondary audit finalize error")
    result_holder: dict[str, Any] = {}
    runner_task = asyncio.create_task(_runner_sets(result_holder, "answer", ""))

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("fail", []).append(error)
            return True

    context = _context(
        result_holder,
        runner_task,
        calls,
        reply_meta={"_agent_result": "prompt_v2_audit_failed"},
        claim_owner=ClaimOwner(),
        finalize_error=cleanup_error,
    )

    with pytest.raises(RuntimeError, match="prompt_v2_audit_failed") as raised:
        await persist_stream_result_after_runner_done(
            context,
            push=False,
            persist_db=FakeDb(),
        )

    assert raised.value is calls["fail"][0]
    assert raised.value is not cleanup_error
    assert calls.get("complete") is None


@pytest.mark.asyncio
async def test_persist_stream_result_persist_error_fails_owner_and_never_completes():
    from api.chat_streaming_result import persist_stream_result_after_runner_done

    calls: dict[str, list[Any]] = {}
    persist_error = RuntimeError("database is locked")
    result_holder: dict[str, Any] = {}
    runner_task = asyncio.create_task(_runner_sets(result_holder, "answer", "不会完成"))

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("fail", []).append(error)
            return True

    context = _context(
        result_holder,
        runner_task,
        calls,
        claim_owner=ClaimOwner(),
        persist_error=persist_error,
    )

    with pytest.raises(RuntimeError) as raised:
        await persist_stream_result_after_runner_done(
            context,
            push=False,
            persist_db=FakeDb(),
        )

    assert raised.value is persist_error
    assert calls["fail"] == [persist_error]
    assert calls.get("complete") is None


@pytest.mark.parametrize("stage", ["runner", "finalize", "persist"])
@pytest.mark.asyncio
async def test_stream_finalizer_fatal_base_exception_fails_owner_once_and_stops_renewal(
    stage,
):
    from api.chat_streaming_result import persist_stream_result_after_runner_done

    calls: dict[str, list[Any]] = {}
    fatal = FatalStreamFinalizerError(f"stream {stage} fatal")
    result_holder: dict[str, Any] = {}
    runner_task = asyncio.create_task(
        _runner_raises(fatal)
        if stage == "runner"
        else _runner_sets(result_holder, "answer", "不会完成")
    )

    class ClaimOwner:
        def __init__(self):
            self.renewal_task = asyncio.create_task(asyncio.Event().wait())

        async def complete(self, completion):
            calls.setdefault("complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("fail", []).append(error)
            self.renewal_task.cancel()
            await asyncio.gather(self.renewal_task, return_exceptions=True)
            return True

    owner = ClaimOwner()
    context = _context(
        result_holder,
        runner_task,
        calls,
        claim_owner=owner,
        finalize_error=fatal if stage == "finalize" else None,
        persist_error=fatal if stage == "persist" else None,
    )

    try:
        with pytest.raises(FatalStreamFinalizerError) as raised:
            await persist_stream_result_after_runner_done(
                context,
                push=False,
                persist_db=FakeDb(),
            )

        assert raised.value is fatal
        assert calls["fail"] == [fatal]
        assert calls.get("complete") is None
        assert owner.renewal_task.done()
    finally:
        if not owner.renewal_task.done():
            owner.renewal_task.cancel()
            await asyncio.gather(owner.renewal_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_stream_bridge_error_logger_failure_preserves_primary_and_still_fails_owner(
    monkeypatch,
):
    from api import chat_streaming_result

    calls: dict[str, list[Any]] = {}
    bridge_error = RuntimeError("primary stream bridge error")
    log_error = RuntimeError("broken stream log handler")
    result_holder: dict[str, Any] = {"error": bridge_error}
    runner_task = asyncio.create_task(asyncio.sleep(0))

    class ClaimOwner:
        def __init__(self):
            self.renewal_task = asyncio.create_task(asyncio.Event().wait())

        async def complete(self, completion):
            calls.setdefault("complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("fail", []).append(error)
            self.renewal_task.cancel()
            await asyncio.gather(self.renewal_task, return_exceptions=True)
            return True

    def broken_error_log(*_args, **_kwargs):
        raise log_error

    owner = ClaimOwner()
    context = _context(
        result_holder,
        runner_task,
        calls,
        claim_owner=owner,
    )
    monkeypatch.setattr(chat_streaming_result.logger, "error", broken_error_log)

    try:
        with pytest.raises(RuntimeError) as raised:
            await chat_streaming_result.persist_stream_result_after_runner_done(
                context,
                push=False,
                persist_db=FakeDb(),
            )

        assert raised.value is bridge_error
        assert calls["fail"] == [bridge_error]
        assert owner.renewal_task.done()
    finally:
        if not owner.renewal_task.done():
            owner.renewal_task.cancel()
            await asyncio.gather(owner.renewal_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_persist_stream_result_complete_error_attempts_one_fail_and_never_pushes():
    from api.chat_streaming_result import persist_stream_result_after_runner_done

    calls: dict[str, list[Any]] = {}
    complete_error = RuntimeError("claim owner lost")
    result_holder: dict[str, Any] = {}
    runner_task = asyncio.create_task(_runner_sets(result_holder, "answer", "不会推送"))

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("complete", []).append(completion)
            raise complete_error

        async def fail(self, error):
            calls.setdefault("fail", []).append(error)
            return False

    context = _context(
        result_holder,
        runner_task,
        calls,
        claim_owner=ClaimOwner(),
    )

    with pytest.raises(RuntimeError) as raised:
        await persist_stream_result_after_runner_done(
            context,
            push=True,
            persist_db=FakeDb(),
        )

    assert raised.value is complete_error
    assert len(calls["complete"]) == 1
    assert calls["fail"] == [complete_error]
    assert calls.get("push") is None


@pytest.mark.asyncio
async def test_persist_stream_result_push_exception_after_complete_is_only_recorded():
    from api.chat_streaming_result import persist_stream_result_after_runner_done

    calls: dict[str, list[Any]] = {}
    push_error = RuntimeError("qq push unavailable")
    result_holder: dict[str, Any] = {}
    runner_task = asyncio.create_task(_runner_sets(result_holder, "answer", "已完成答案"))

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("fail", []).append(error)
            return True

    context = _context(
        result_holder,
        runner_task,
        calls,
        claim_owner=ClaimOwner(),
        push_error=push_error,
    )

    result = await persist_stream_result_after_runner_done(
        context,
        push=True,
        persist_db=FakeDb(),
    )

    assert result.answer == "已完成答案"
    assert len(calls["complete"]) == 1
    assert calls.get("fail") is None
    assert len(calls["push"]) == 1


@pytest.mark.asyncio
async def test_persist_stream_result_push_envelope_error_after_complete_is_only_recorded():
    from api.chat_streaming_result import persist_stream_result_after_runner_done

    calls: dict[str, list[Any]] = {}
    envelope_error = RuntimeError("push envelope unavailable")
    result_holder: dict[str, Any] = {}
    runner_task = asyncio.create_task(_runner_sets(result_holder, "answer", "已完成答案"))

    class ClaimOwner:
        async def complete(self, completion):
            calls.setdefault("complete", []).append(completion)
            return True

        async def fail(self, error):
            calls.setdefault("fail", []).append(error)
            return True

    context = _context(
        result_holder,
        runner_task,
        calls,
        claim_owner=ClaimOwner(),
        envelope_error=envelope_error,
    )

    result = await persist_stream_result_after_runner_done(
        context,
        push=True,
        persist_db=FakeDb(),
    )

    assert result.answer == "已完成答案"
    assert len(calls["complete"]) == 1
    assert calls.get("fail") is None
    assert len(calls["envelope"]) == 1
    assert calls.get("push") is None

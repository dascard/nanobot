from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from core.database import ChatLog, ConversationTurn


def _handle(*, attempt_count: int):
    from core.inbound_idempotency import InboundClaimHandle, InboundClaimKey

    return InboundClaimHandle(
        key=InboundClaimKey(
            platform="qq",
            chat_type="private",
            session_id="private_recovery-user",
            message_id="recovery-message",
        ),
        owner_token=f"owner-{attempt_count}",
        lease_expires_at=datetime(2026, 7, 11, 18, 0, 0),
        attempt_count=attempt_count,
    )


def _install_chat_route_dependencies(
    monkeypatch,
    routes,
    bridge_calls,
    *,
    bridge=None,
    use_real_non_streaming: bool = False,
):
    from api import chat_pre_bridge_route_result

    async def resolve_pre_bridge_decision(*_args, **_kwargs):
        return object()

    async def resolve_pre_bridge_route_result(_db, req, _pre_bridge, **_kwargs):
        return chat_pre_bridge_route_result.ChatPreBridgeRouteContinue(
            final_query=req.query,
            final_files=list(req.files or []),
            private_decision=None,
            private_timing_meta=None,
            guardrail_status=None,
            classifier_ran=False,
            persist_req=req,
        )

    async def call_bridge_non_streaming(_bridge, **_kwargs):
        bridge_calls.append("bridge")
        return "只应生成一次的回复"

    async def finalize_private_buffer(*_args, **_kwargs):
        return None

    monkeypatch.setattr(routes, "_check_user_blocked", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(routes, "_schedule_image_precache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        routes,
        "_resolve_chat_persona_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(
            persona_obj=None,
            persona_json="",
            persona_data={},
            persona_text="",
            matched_user_id="",
            lookup_user_id="recovery-user",
            candidate_count=1,
        ),
    )
    monkeypatch.setattr(routes, "_build_chat_context", lambda *_args, **_kwargs: ("", [], {}))
    monkeypatch.setattr(
        routes,
        "_resolve_chat_pre_bridge_decision",
        resolve_pre_bridge_decision,
    )
    monkeypatch.setattr(
        routes,
        "_resolve_pre_bridge_route_result",
        resolve_pre_bridge_route_result,
    )
    monkeypatch.setattr(
        routes,
        "_build_chat_runtime_route_context",
        lambda *_args, **_kwargs: SimpleNamespace(
            enriched_query="recovery enriched query",
            bridge_meta={"chat_type": "private", "is_group": False},
            platform="qq",
        ),
    )
    monkeypatch.setattr(
        routes,
        "get_bridge",
        lambda: bridge if bridge is not None else object(),
    )
    if not use_real_non_streaming:
        monkeypatch.setattr(
            routes.chat_runtime_facade,
            "call_bridge_non_streaming",
            call_bridge_non_streaming,
        )
    monkeypatch.setattr(routes, "_finalize_private_buffer", finalize_private_buffer)
    monkeypatch.setattr(routes, "_pop_bridge_reply_meta", lambda *_args, **_kwargs: {})


async def _wait_for_stream_finalizers() -> None:
    from api import chat_route_runner

    for _ in range(100):
        tasks = tuple(chat_route_runner._STREAM_FINALIZER_TASKS)
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)
    raise AssertionError("stream finalizer 未在预期时间内结束")


@pytest.mark.asyncio
async def test_takeover_recovers_persisted_private_reply_without_second_bridge(
    db_session,
    monkeypatch,
):
    from fastapi import BackgroundTasks

    from api import routes
    from core.inbound_idempotency import (
        ClaimDecisionKind,
        InboundClaimDecision,
    )

    decisions = [
        InboundClaimDecision(
            kind=ClaimDecisionKind.ACQUIRED,
            handle=_handle(attempt_count=1),
        ),
        InboundClaimDecision(
            kind=ClaimDecisionKind.ACQUIRED,
            handle=_handle(attempt_count=2),
        ),
        InboundClaimDecision(
            kind=ClaimDecisionKind.ACQUIRED,
            handle=_handle(attempt_count=3),
        ),
    ]
    bridge_calls = []
    completions = []

    class FakeOwner:
        def __init__(self, handle):
            self.handle = handle

        async def start(self):
            return None

        async def complete(self, completion):
            completions.append((self.handle.attempt_count, completion))
            if self.handle.attempt_count < 3:
                raise RuntimeError("complete failed after business commit")
            return True

        async def fail(self, _error):
            return True

    monkeypatch.setattr(routes, "acquire_inbound_claim", lambda *_args: decisions.pop(0))
    monkeypatch.setattr(routes, "InboundClaimOwner", FakeOwner)
    _install_chat_route_dependencies(monkeypatch, routes, bridge_calls)

    class StreamingBridge:
        async def handle_message(self, *_args, **_kwargs):
            bridge_calls.append("bridge")
            return "只应生成一次的回复"

    monkeypatch.setattr(routes, "get_bridge", lambda: StreamingBridge())

    def request():
        return routes.ChatProxyRequest(
            user_id="recovery-user",
            session_id="private_recovery-user",
            query="同一个业务请求",
            message_id="recovery-message",
            client_meta={
                "platform": "qq",
                "trace": {"request_id": "transport-only"},
            },
        )

    with pytest.raises(RuntimeError, match="complete failed after business commit"):
        await routes.proxy_chat(request(), BackgroundTasks(), db_session, None)

    with pytest.raises(RuntimeError, match="complete failed after business commit"):
        await routes.proxy_chat(request(), BackgroundTasks(), db_session, None)

    recovered = await routes.proxy_chat(
        request(),
        BackgroundTasks(),
        db_session,
        None,
    )

    assert recovered["status"] == "ok"
    assert recovered["reply"] == "只应生成一次的回复"
    assert bridge_calls == ["bridge"]
    assert [attempt for attempt, _completion in completions] == [1, 2, 3]
    assert completions[0][1] == completions[1][1] == completions[2][1]
    assert db_session.query(ChatLog).filter_by(user_id="recovery-user").count() == 2
    assert db_session.query(ConversationTurn).filter_by(user_id="recovery-user").count() == 2


@pytest.mark.asyncio
async def test_takeover_recovers_blocked_completion_from_single_request_journal(
    db_session,
    monkeypatch,
):
    from fastapi import BackgroundTasks

    from api import routes
    from core.inbound_idempotency import ClaimDecisionKind, InboundClaimDecision

    decisions = [
        InboundClaimDecision(
            kind=ClaimDecisionKind.ACQUIRED,
            handle=_handle(attempt_count=1),
        ),
        InboundClaimDecision(
            kind=ClaimDecisionKind.ACQUIRED,
            handle=_handle(attempt_count=2),
        ),
    ]
    blocked_checks = []

    class FakeOwner:
        def __init__(self, handle):
            self.handle = handle

        async def start(self):
            return None

        async def complete(self, _completion):
            if self.handle.attempt_count == 1:
                raise RuntimeError("blocked complete failed")
            return True

        async def fail(self, _error):
            return True

    def is_blocked(*_args, **_kwargs):
        blocked_checks.append(True)
        return True

    monkeypatch.setattr(routes, "acquire_inbound_claim", lambda *_args: decisions.pop(0))
    monkeypatch.setattr(routes, "InboundClaimOwner", FakeOwner)
    monkeypatch.setattr(routes, "_check_user_blocked", is_blocked)

    def request():
        return routes.ChatProxyRequest(
            user_id="recovery-user",
            session_id="private_recovery-user",
            query="被屏蔽请求",
            message_id="recovery-message",
            client_meta={"platform": "qq"},
        )

    with pytest.raises(RuntimeError, match="blocked complete failed"):
        await routes.proxy_chat(request(), BackgroundTasks(), db_session, None)

    recovered = await routes.proxy_chat(
        request(),
        BackgroundTasks(),
        db_session,
        None,
    )

    assert recovered["status"] == "silent"
    assert recovered["reason"] == "user_blocked"
    assert blocked_checks == [True]
    logs = db_session.query(ChatLog).filter_by(user_id="recovery-user").all()
    assert len(logs) == 1
    assert logs[0].role == "user"
    assert logs[0].processed == 1


@pytest.mark.asyncio
async def test_nonstream_takeover_recovers_after_stream_complete_failure(
    db_session,
    monkeypatch,
):
    from fastapi import BackgroundTasks

    from api import routes
    from core.database import ChatDeliveryOutbox
    from core.inbound_idempotency import ClaimDecisionKind, InboundClaimDecision

    decisions = [
        InboundClaimDecision(
            kind=ClaimDecisionKind.ACQUIRED,
            handle=_handle(attempt_count=1),
        ),
        InboundClaimDecision(
            kind=ClaimDecisionKind.ACQUIRED,
            handle=_handle(attempt_count=2),
        ),
    ]
    bridge_calls = []
    complete_attempts = []
    pushed: list[tuple[str, str, dict]] = []

    class StreamingBridge:
        async def handle_message(self, *_args, **_kwargs):
            bridge_calls.append("bridge")
            return "只应生成一次的回复"

    class FakeOwner:
        def __init__(self, handle):
            self.handle = handle

        async def start(self):
            return None

        async def pause(self):
            return True

        async def resume(self):
            return True

        async def complete(self, _completion):
            complete_attempts.append(self.handle.attempt_count)
            if self.handle.attempt_count == 1:
                raise RuntimeError("stream complete failed after persist")
            return True

        async def fail(self, _error):
            return True

    monkeypatch.setattr(routes, "acquire_inbound_claim", lambda *_args: decisions.pop(0))
    monkeypatch.setattr(routes, "InboundClaimOwner", FakeOwner)

    async def record_push(target_type: str, target_id: str, envelope: dict) -> bool:
        pushed.append((target_type, target_id, envelope))
        return True

    monkeypatch.setattr("core.daily_digest.push_envelope_to_qq", record_push)
    _install_chat_route_dependencies(
        monkeypatch,
        routes,
        bridge_calls,
        bridge=StreamingBridge(),
    )

    def request(*, stream: bool):
        return routes.ChatProxyRequest(
            user_id="recovery-user",
            session_id="private_recovery-user",
            query="跨 transport 恢复请求",
            message_id="recovery-message",
            stream=stream,
            client_meta={"platform": "qq"},
        )

    first = await routes.proxy_chat(
        request(stream=True),
        BackgroundTasks(),
        db_session,
        None,
    )
    stream_events = [event async for event in first.body_iterator]
    await _wait_for_stream_finalizers()
    recovered = await routes.proxy_chat(
        request(stream=False),
        BackgroundTasks(),
        db_session,
        None,
    )

    decoded_events = [
        json.loads(event.removeprefix("data: ").strip())
        for event in stream_events
    ]
    assert any(event.get("status") == "error" for event in decoded_events)
    assert all(event.get("status") != "done" for event in decoded_events)
    assert recovered["status"] == "ok"
    assert recovered["reply"] == "只应生成一次的回复"
    assert bridge_calls == ["bridge"]
    assert complete_attempts == [1, 2]
    assert pushed == []
    assert db_session.query(ChatDeliveryOutbox).count() == 0
    assert db_session.query(ChatLog).filter_by(user_id="recovery-user").count() == 2
    assert db_session.query(ConversationTurn).filter_by(user_id="recovery-user").count() == 2


@pytest.mark.asyncio
async def test_private_bridge_resolver_failure_returns_502_then_recovers_once(
    db_session,
    monkeypatch,
):
    from fastapi import BackgroundTasks, HTTPException

    from api import chat_recovery, routes
    from core.database import InboundMessageClaim

    calls = {"bridge": 0, "model": 0}

    class ResolverBridge:
        async def handle_message(self, *_args, **_kwargs):
            calls["bridge"] += 1
            if calls["bridge"] == 1:
                raise RuntimeError("session guidance resolver failed")
            calls["model"] += 1
            return "恢复后的私聊回复"

    bridge = ResolverBridge()
    _install_chat_route_dependencies(
        monkeypatch,
        routes,
        [],
        bridge=bridge,
        use_real_non_streaming=True,
    )

    def request():
        return routes.ChatProxyRequest(
            user_id="recovery-user",
            session_id="private_recovery-user",
            query="resolver 恢复请求",
            message_id="recovery-message",
            client_meta={"platform": "qq"},
        )

    with pytest.raises(HTTPException) as raised:
        await routes.proxy_chat(
            request(),
            BackgroundTasks(),
            db_session,
            None,
        )
    assert raised.value.status_code == 502
    assert raised.value.detail == "系统暂时不可用，请稍后再试"

    db_session.expire_all()
    failed_claim = db_session.query(InboundMessageClaim).one()
    assert failed_claim.status == "failed"
    assert failed_claim.response_json == ""
    assert failed_claim.attempt_count == 1
    journal = db_session.query(ChatLog).one()
    assert journal.role == "user"
    assert journal.content == ""
    assert json.loads(journal.meta_json)["kind"] == chat_recovery.REQUEST_JOURNAL_KIND
    assert db_session.query(ConversationTurn).count() == 0
    db_session.rollback()

    recovered = await routes.proxy_chat(
        request(),
        BackgroundTasks(),
        db_session,
        None,
    )

    assert recovered["status"] == "ok"
    assert recovered["reply"] == "恢复后的私聊回复"
    assert calls == {"bridge": 2, "model": 1}
    completed_claim = db_session.query(InboundMessageClaim).one()
    assert completed_claim.status == "completed"
    assert completed_claim.attempt_count == 2
    assert db_session.query(ChatLog).count() == 2
    assert db_session.query(ConversationTurn).count() == 2


@pytest.mark.asyncio
async def test_private_stream_resolver_failure_has_no_empty_push_or_outbox(
    db_session,
    monkeypatch,
):
    from fastapi import BackgroundTasks

    from api import chat_recovery, routes
    from core.database import ChatDeliveryOutbox, InboundMessageClaim

    calls = {"bridge": 0, "model": 0}
    pushed: list[tuple[str, str, dict]] = []

    class ResolverBridge:
        async def handle_message(self, *_args, **_kwargs):
            calls["bridge"] += 1
            if calls["bridge"] == 1:
                raise RuntimeError("session guidance resolver failed")
            calls["model"] += 1
            return "流式失败后的恢复回复"

    async def record_push(target_type, target_id, envelope):
        pushed.append((target_type, target_id, envelope))
        return True

    bridge = ResolverBridge()
    monkeypatch.setattr("core.daily_digest.push_envelope_to_qq", record_push)
    _install_chat_route_dependencies(
        monkeypatch,
        routes,
        [],
        bridge=bridge,
        use_real_non_streaming=True,
    )

    def request(*, stream: bool):
        return routes.ChatProxyRequest(
            user_id="recovery-user",
            session_id="private_recovery-user",
            query="流式 resolver 恢复请求",
            message_id="recovery-message",
            stream=stream,
            client_meta={"platform": "qq"},
        )

    first = await routes.proxy_chat(
        request(stream=True),
        BackgroundTasks(),
        db_session,
        None,
    )
    stream_events = [event async for event in first.body_iterator]
    await _wait_for_stream_finalizers()
    decoded_events = []
    for event in stream_events:
        text = event.decode() if isinstance(event, bytes) else str(event)
        decoded_events.append(json.loads(text.removeprefix("data: ").strip()))

    assert any(event.get("status") == "error" for event in decoded_events)
    assert all(event.get("status") != "done" for event in decoded_events)
    assert pushed == []
    db_session.expire_all()
    failed_claim = db_session.query(InboundMessageClaim).one()
    assert failed_claim.status == "failed"
    assert failed_claim.response_json == ""
    assert failed_claim.attempt_count == 1
    assert db_session.query(ChatDeliveryOutbox).count() == 0
    journal = db_session.query(ChatLog).one()
    assert journal.role == "user"
    assert journal.content == ""
    assert json.loads(journal.meta_json)["kind"] == chat_recovery.REQUEST_JOURNAL_KIND
    assert db_session.query(ConversationTurn).count() == 0
    db_session.rollback()

    recovered = await routes.proxy_chat(
        request(stream=False),
        BackgroundTasks(),
        db_session,
        None,
    )

    assert recovered["status"] == "ok"
    assert recovered["reply"] == "流式失败后的恢复回复"
    assert calls == {"bridge": 2, "model": 1}
    assert pushed == []
    assert db_session.query(ChatDeliveryOutbox).count() == 0
    completed_claim = db_session.query(InboundMessageClaim).one()
    assert completed_claim.status == "completed"
    assert completed_claim.attempt_count == 2


@pytest.mark.asyncio
async def test_blocked_route_rejects_false_claim_completion(
    db_session,
    monkeypatch,
):
    from fastapi import BackgroundTasks

    from api import routes
    from core.inbound_idempotency import ClaimDecisionKind, InboundClaimDecision

    failed = []

    class FakeOwner:
        def __init__(self, _handle):
            pass

        async def start(self):
            return None

        async def complete(self, _completion):
            return False

        async def fail(self, error):
            failed.append(error)
            return True

    monkeypatch.setattr(
        routes,
        "acquire_inbound_claim",
        lambda *_args: InboundClaimDecision(
            kind=ClaimDecisionKind.ACQUIRED,
            handle=_handle(attempt_count=1),
        ),
    )
    monkeypatch.setattr(routes, "InboundClaimOwner", FakeOwner)
    monkeypatch.setattr(routes, "_check_user_blocked", lambda *_args, **_kwargs: True)

    with pytest.raises(RuntimeError, match="blocked claim complete 未成功"):
        await routes.proxy_chat(
            routes.ChatProxyRequest(
                user_id="recovery-user",
                session_id="private_recovery-user",
                query="被屏蔽请求",
                message_id="recovery-message",
                client_meta={"platform": "qq"},
            ),
            BackgroundTasks(),
            db_session,
            None,
        )

    assert len(failed) == 1


@pytest.mark.asyncio
async def test_pre_bridge_route_rejects_false_claim_completion(
    db_session,
    monkeypatch,
):
    from fastapi import BackgroundTasks

    from api import chat_pre_bridge_route_result, routes
    from core.inbound_idempotency import (
        ClaimDecisionKind,
        CompletedInboundResponse,
        InboundClaimDecision,
    )

    failed = []
    completion = CompletedInboundResponse(
        outcome="no_reply",
        reason="timing_gate_no_reply",
    )

    class FakeOwner:
        def __init__(self, _handle):
            pass

        async def start(self):
            return None

        async def complete(self, _completion):
            return False

        async def fail(self, error):
            failed.append(error)
            return True

    async def early_result(*_args, **_kwargs):
        return chat_pre_bridge_route_result.ChatPreBridgeRouteEarlyResponse(
            payload={"status": "no_reply", "reason": "timing_gate_no_reply"},
            completion=completion,
        )

    monkeypatch.setattr(
        routes,
        "acquire_inbound_claim",
        lambda *_args: InboundClaimDecision(
            kind=ClaimDecisionKind.ACQUIRED,
            handle=_handle(attempt_count=1),
        ),
    )
    monkeypatch.setattr(routes, "InboundClaimOwner", FakeOwner)
    _install_chat_route_dependencies(monkeypatch, routes, [])
    monkeypatch.setattr(routes, "_resolve_pre_bridge_route_result", early_result)

    with pytest.raises(RuntimeError, match="pre-bridge claim complete 未成功"):
        await routes.proxy_chat(
            routes.ChatProxyRequest(
                user_id="recovery-user",
                session_id="private_recovery-user",
                query="无需回复",
                message_id="recovery-message",
                client_meta={"platform": "qq"},
            ),
            BackgroundTasks(),
            db_session,
            None,
        )

    assert len(failed) == 1

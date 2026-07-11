from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import BackgroundTasks


async def _wait_for_stream_finalizers(route_runner) -> None:
    async def wait_until_empty() -> None:
        while tasks := tuple(route_runner._STREAM_FINALIZER_TASKS):
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_until_empty(), timeout=3)


@pytest.mark.asyncio
async def test_claimed_stream_disconnect_persists_ambiguous_delivery_outbox(
    db_session,
    monkeypatch,
):
    from api import chat_route_runner, routes
    from core.database import ChatDeliveryOutbox, InboundMessageClaim
    from tests.test_api import _fast_private_reply

    routes._private_buffers.clear()
    _fast_private_reply(monkeypatch)
    release = asyncio.Event()
    push_calls = []

    class FakeBridge:
        async def handle_message(self, *args, stream_queue=None, **kwargs):
            await stream_queue.put({"status": "progress", "message": "thinking"})
            await release.wait()
            return "断连后需要可靠投递"

        def pop_last_reply_meta(self, _session_id):
            return {}

    async def uncertain_push(target_type, target_id, envelope):
        push_calls.append((target_type, target_id, envelope))
        return None

    monkeypatch.setattr(routes, "get_bridge", lambda: FakeBridge())
    monkeypatch.setattr(
        "core.daily_digest.push_envelope_to_qq",
        uncertain_push,
    )
    response = await routes.proxy_chat(
        routes.ChatProxyRequest(
            user_id="u-stream-outbox",
            session_id="private_u-stream-outbox",
            query="验证断连 outbox",
            message_id="m-stream-outbox",
            stream=True,
            client_meta={"platform": "qq"},
        ),
        BackgroundTasks(),
        db_session,
        None,
    )
    iterator = response.body_iterator

    try:
        first_event = await asyncio.wait_for(anext(iterator), timeout=1)
        assert "thinking" in first_event
        await iterator.aclose()
        release.set()
        await _wait_for_stream_finalizers(chat_route_runner)
    finally:
        release.set()
        pending = tuple(chat_route_runner._STREAM_FINALIZER_TASKS)
        for task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    db_session.expire_all()
    claim = db_session.query(InboundMessageClaim).one()
    delivery = db_session.query(ChatDeliveryOutbox).one()
    envelope = json.loads(delivery.envelope_json)

    assert claim.status == "completed"
    assert delivery.message_id == "m-stream-outbox"
    assert delivery.status == "ambiguous"
    assert delivery.attempt_count == 1
    assert delivery.owner_token == ""
    assert delivery.next_attempt_at is not None
    assert envelope["reply"] == "断连后需要可靠投递"
    assert push_calls == [
        (
            "private",
            "u-stream-outbox",
            envelope,
        )
    ]

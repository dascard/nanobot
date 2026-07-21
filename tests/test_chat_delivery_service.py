from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.sqlite_test_utils import install_base_schema


def _key(message_id: str):
    from core.inbound_idempotency import InboundClaimKey

    return InboundClaimKey(
        platform="qq",
        chat_type="private",
        session_id="private_delivery-service-user",
        message_id=message_id,
    )


def _envelope(message: str = "待投递回复") -> dict:
    return {
        "schema_version": 1,
        "kind": "chat_response",
        "reply": message,
        "messages": [{"type": "text", "text": message}],
        "reply_meta": {},
        "meta": {"platform": "qq", "chat_type": "private"},
    }


@pytest.fixture
def delivery_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'chat-delivery-service.db'}",
        connect_args={"timeout": 5},
    )
    install_base_schema(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("publisher_result", "expected_status"),
    [
        (True, "delivered"),
        (False, "failed"),
        (None, "ambiguous"),
    ],
)
@pytest.mark.asyncio
async def test_enqueue_and_deliver_settles_publisher_result(
    delivery_session_factory,
    publisher_result,
    expected_status,
):
    from core.chat_delivery_service import enqueue_and_deliver_chat_response
    from core.database import ChatDeliveryOutbox

    now = datetime(2026, 7, 11, 12, 0, 0)
    calls = []

    async def publisher(target_type, target_id, envelope):
        with delivery_session_factory() as db:
            visible = db.query(ChatDeliveryOutbox).one()
            assert visible.status == "sending"
        calls.append((target_type, target_id, envelope))
        return publisher_result

    result = await enqueue_and_deliver_chat_response(
        key=_key(f"result-{expected_status}"),
        target_type="private",
        target_id="delivery-service-user",
        envelope=_envelope(),
        publisher=publisher,
        session_factory=delivery_session_factory,
        now=now,
        owner_token=f"owner-{expected_status}",
    )

    assert result.status == expected_status
    assert len(calls) == 1
    assert calls[0][0:2] == ("private", "delivery-service-user")
    assert calls[0][2] == _envelope()
    with delivery_session_factory() as db:
        row = db.query(ChatDeliveryOutbox).one()
        assert row.status == expected_status
        assert row.owner_token == ""
        assert row.lease_expires_at is None
        if expected_status == "ambiguous":
            assert row.next_attempt_at == now + timedelta(seconds=30)
        else:
            assert row.next_attempt_at is None


@pytest.mark.asyncio
async def test_enqueue_and_deliver_marks_publisher_exception_ambiguous(
    delivery_session_factory,
):
    from core.chat_delivery_service import enqueue_and_deliver_chat_response
    from core.database import ChatDeliveryOutbox

    async def publisher(_target_type, _target_id, _envelope):
        raise TimeoutError("QQ push timeout")

    result = await enqueue_and_deliver_chat_response(
        key=_key("publisher-exception"),
        target_type="private",
        target_id="delivery-service-user",
        envelope=_envelope(),
        publisher=publisher,
        session_factory=delivery_session_factory,
        now=datetime(2026, 7, 11, 12, 0, 0),
        owner_token="owner-exception",
    )

    assert result.status == "ambiguous"
    assert "QQ push timeout" in result.error
    with delivery_session_factory() as db:
        row = db.query(ChatDeliveryOutbox).one()
        assert row.status == "ambiguous"
        assert "QQ push timeout" in row.last_error


@pytest.mark.asyncio
async def test_delivered_replay_does_not_publish_again(delivery_session_factory):
    from core.chat_delivery_service import enqueue_and_deliver_chat_response

    calls = 0

    async def publisher(_target_type, _target_id, _envelope):
        nonlocal calls
        calls += 1
        return True

    kwargs = {
        "key": _key("delivered-replay"),
        "target_type": "private",
        "target_id": "delivery-service-user",
        "envelope": _envelope(),
        "publisher": publisher,
        "session_factory": delivery_session_factory,
        "now": datetime(2026, 7, 11, 12, 0, 0),
    }
    first = await enqueue_and_deliver_chat_response(
        **kwargs,
        owner_token="owner-first",
    )
    replay = await enqueue_and_deliver_chat_response(
        **kwargs,
        owner_token="owner-replay",
    )

    assert first.status == "delivered"
    assert replay.status == "delivered"
    assert calls == 1


@pytest.mark.asyncio
async def test_delivery_attempt_timeout_becomes_ambiguous_before_lease_expires(
    delivery_session_factory,
):
    from core.chat_delivery_service import enqueue_and_deliver_chat_response
    from core.database import ChatDeliveryOutbox

    publisher_cancelled = False

    async def blocked_publisher(_target_type, _target_id, _envelope):
        nonlocal publisher_cancelled
        try:
            await asyncio.Event().wait()
        finally:
            publisher_cancelled = True

    result = await enqueue_and_deliver_chat_response(
        key=_key("publisher-timeout"),
        target_type="private",
        target_id="delivery-service-user",
        envelope=_envelope(),
        publisher=blocked_publisher,
        session_factory=delivery_session_factory,
        now=datetime(2026, 7, 11, 12, 0, 0),
        owner_token="owner-timeout",
        attempt_timeout_seconds=0.01,
        lease_seconds=1,
    )

    assert result.status == "ambiguous"
    assert publisher_cancelled is True
    with delivery_session_factory() as db:
        row = db.query(ChatDeliveryOutbox).one()
        assert row.status == "ambiguous"
        assert row.lease_expires_at is None


@pytest.mark.asyncio
async def test_delivery_database_sessions_run_outside_event_loop_thread(
    delivery_session_factory,
):
    from core.chat_delivery_service import enqueue_and_deliver_chat_response

    event_loop_thread = threading.get_ident()
    session_threads = []

    def tracking_session_factory():
        session_threads.append(threading.get_ident())
        return delivery_session_factory()

    async def publisher(_target_type, _target_id, _envelope):
        assert threading.get_ident() == event_loop_thread
        return True

    result = await enqueue_and_deliver_chat_response(
        key=_key("database-thread"),
        target_type="private",
        target_id="delivery-service-user",
        envelope=_envelope(),
        publisher=publisher,
        session_factory=tracking_session_factory,
        owner_token="owner-database-thread",
    )

    assert result.status == "delivered"
    assert len(session_threads) >= 3
    assert all(thread_id != event_loop_thread for thread_id in session_threads)

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _key(message_id: str):
    from core.inbound_idempotency import InboundClaimKey

    return InboundClaimKey(
        platform="qq",
        chat_type="private",
        session_id="private_delivery-worker-user",
        message_id=message_id,
    )


def _envelope(message: str = "worker 待投递回复") -> dict:
    return {
        "schema_version": 1,
        "kind": "chat_response",
        "reply": message,
        "messages": [{"type": "text", "text": message}],
        "reply_meta": {},
        "meta": {"platform": "qq", "chat_type": "private"},
    }


@pytest.fixture
def worker_session_factory(tmp_path):
    from core.database import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'chat-delivery-worker.db'}",
        connect_args={"timeout": 5},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_worker_restart_retries_due_ambiguous_delivery(worker_session_factory):
    from core.chat_delivery_outbox import enqueue_chat_delivery
    from core.database import ChatDeliveryOutbox
    from workers.chat_delivery_worker import run_once_async

    now = datetime(2026, 7, 11, 12, 0, 0)
    with worker_session_factory() as db:
        enqueue_chat_delivery(
            db,
            key=_key("worker-restart"),
            target_type="private",
            target_id="delivery-worker-user",
            envelope=_envelope(),
            now=now,
        )
        db.commit()

    first_calls = []

    async def uncertain_publisher(*args):
        first_calls.append(args)
        return None

    first = await run_once_async(
        publisher=uncertain_publisher,
        session_factory=worker_session_factory,
        owner="worker-before-restart",
        now=now,
        limit=5,
    )
    too_early = await run_once_async(
        publisher=uncertain_publisher,
        session_factory=worker_session_factory,
        owner="worker-too-early",
        now=now + timedelta(seconds=29),
        limit=5,
    )
    second_calls = []

    async def successful_publisher(*args):
        second_calls.append(args)
        return True

    after_restart = await run_once_async(
        publisher=successful_publisher,
        session_factory=worker_session_factory,
        owner="worker-after-restart",
        now=now + timedelta(seconds=30),
        limit=5,
    )

    assert first == {
        "processed": 1,
        "delivered": 0,
        "failed": 0,
        "ambiguous": 1,
    }
    assert too_early["processed"] == 0
    assert after_restart == {
        "processed": 1,
        "delivered": 1,
        "failed": 0,
        "ambiguous": 0,
    }
    assert len(first_calls) == 1
    assert len(second_calls) == 1
    with worker_session_factory() as db:
        row = db.query(ChatDeliveryOutbox).one()
        assert row.status == "delivered"
        assert row.attempt_count == 2


@pytest.mark.asyncio
async def test_worker_recovers_stale_sending_before_retry(worker_session_factory):
    from core.chat_delivery_outbox import (
        claim_due_chat_delivery,
        enqueue_chat_delivery,
    )
    from core.database import ChatDeliveryOutbox
    from workers.chat_delivery_worker import run_once_async

    now = datetime(2026, 7, 11, 12, 0, 0)
    with worker_session_factory() as db:
        row = enqueue_chat_delivery(
            db,
            key=_key("worker-stale"),
            target_type="private",
            target_id="delivery-worker-user",
            envelope=_envelope(),
            now=now,
        )
        db.commit()
        claimed = claim_due_chat_delivery(
            db,
            owner_token="crashed-worker",
            now=now,
            lease_seconds=60,
        )
        db.commit()
        assert claimed is not None
        row_id = int(row.id)

    calls = []

    async def publisher(*args):
        calls.append(args)
        return True

    recovered = await run_once_async(
        publisher=publisher,
        session_factory=worker_session_factory,
        owner="recovery-worker",
        now=now + timedelta(seconds=60),
        limit=5,
    )
    retried = await run_once_async(
        publisher=publisher,
        session_factory=worker_session_factory,
        owner="retry-worker",
        now=now + timedelta(seconds=90),
        limit=5,
    )

    assert recovered["processed"] == 0
    assert retried["delivered"] == 1
    assert len(calls) == 1
    with worker_session_factory() as db:
        row = db.get(ChatDeliveryOutbox, row_id)
        assert row is not None
        assert row.status == "delivered"
        assert row.attempt_count == 2


@pytest.mark.asyncio
async def test_worker_uses_unique_fencing_token_for_each_attempt(monkeypatch):
    from workers import chat_delivery_worker

    owner_tokens = []

    async def fake_deliver(**kwargs):
        owner_tokens.append(kwargs["owner_token"])
        if len(owner_tokens) <= 2:
            return SimpleNamespace(
                row_id=len(owner_tokens),
                status="delivered",
            )
        return None

    monkeypatch.setattr(
        chat_delivery_worker,
        "deliver_chat_delivery",
        fake_deliver,
    )
    stats = await chat_delivery_worker.run_once_async(
        publisher=lambda *_args: None,
        session_factory=lambda: None,
        owner="worker-label",
        limit=5,
    )

    assert stats["processed"] == 2
    assert len(owner_tokens) == 3
    assert len(set(owner_tokens)) == 3
    assert all(token.startswith("worker-label:") for token in owner_tokens)
    assert all(len(token) <= 64 for token in owner_tokens)


@pytest.mark.asyncio
async def test_worker_stop_signal_prevents_next_batch_claim(worker_session_factory):
    from core.chat_delivery_outbox import enqueue_chat_delivery
    from core.database import ChatDeliveryOutbox
    from workers.chat_delivery_worker import run_forever_async

    now = datetime.now()
    with worker_session_factory() as db:
        for message_id in ("stop-first", "stop-second"):
            enqueue_chat_delivery(
                db,
                key=_key(message_id),
                target_type="private",
                target_id="delivery-worker-user",
                envelope=_envelope(message_id),
                now=now,
            )
        db.commit()

    stop_event = threading.Event()
    calls = []

    async def publisher(*args):
        calls.append(args)
        stop_event.set()
        return True

    await run_forever_async(
        stop_event,
        publisher=publisher,
        session_factory=worker_session_factory,
        owner="stopping-worker",
        interval=0.05,
        limit=5,
    )

    assert len(calls) == 1
    with worker_session_factory() as db:
        statuses = sorted(
            row.status
            for row in db.query(ChatDeliveryOutbox).order_by(ChatDeliveryOutbox.id).all()
        )
        assert statuses == ["delivered", "pending"]

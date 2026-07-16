from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker


def _key(message_id: str = "delivery-message"):
    from core.inbound_idempotency import InboundClaimKey

    return InboundClaimKey(
        platform="qq",
        chat_type="private",
        session_id="private_delivery-user",
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


def test_chat_delivery_outbox_orm_schema_has_fenced_state_contract(db_session):
    from core.database import ChatDeliveryOutbox

    inspector = inspect(db_session.get_bind())
    columns = inspector.get_columns(ChatDeliveryOutbox.__tablename__)
    assert [column["name"] for column in columns] == [
        "id",
        "delivery_key",
        "platform",
        "chat_type",
        "session_id",
        "message_id",
        "target_type",
        "target_id",
        "envelope_json",
        "status",
        "owner_token",
        "lease_expires_at",
        "attempt_count",
        "next_attempt_at",
        "last_error",
        "created_at",
        "updated_at",
        "delivered_at",
    ]
    checks = {
        item["name"]: item["sqltext"]
        for item in inspector.get_check_constraints(ChatDeliveryOutbox.__tablename__)
    }
    assert "ck_chat_delivery_outbox_status" in checks
    assert "ck_chat_delivery_outbox_attempt_count" in checks
    indexes = {
        item["name"]: (item["unique"], tuple(item["column_names"]))
        for item in inspector.get_indexes(ChatDeliveryOutbox.__tablename__)
    }
    assert indexes == {
        "ix_chat_delivery_outbox_due": (
            0,
            ("status", "next_attempt_at"),
        ),
        "ix_chat_delivery_outbox_status_lease": (
            0,
            ("status", "lease_expires_at"),
        ),
        "uq_chat_delivery_outbox_claim_identity": (
            1,
            ("platform", "chat_type", "session_id", "message_id"),
        ),
        "uq_chat_delivery_outbox_delivery_key": (
            1,
            ("delivery_key",),
        ),
    }


def test_enqueue_chat_delivery_is_idempotent_and_never_resets_existing_state(db_session):
    from core.chat_delivery_outbox import (
        ChatDeliveryConflictError,
        enqueue_chat_delivery,
    )

    first = enqueue_chat_delivery(
        db_session,
        key=_key(),
        target_type="private",
        target_id="delivery-user",
        envelope=_envelope(),
        now=datetime(2026, 7, 11, 12, 0, 0),
    )
    db_session.commit()
    first.status = "delivered"
    first.delivered_at = datetime(2026, 7, 11, 12, 1, 0)
    db_session.commit()

    replay = enqueue_chat_delivery(
        db_session,
        key=_key(),
        target_type="private",
        target_id="delivery-user",
        envelope=_envelope(),
        now=datetime(2026, 7, 11, 12, 2, 0),
    )
    db_session.commit()

    assert replay.id == first.id
    assert replay.status == "delivered"
    assert replay.attempt_count == 0
    with pytest.raises(ChatDeliveryConflictError):
        enqueue_chat_delivery(
            db_session,
            key=_key(),
            target_type="private",
            target_id="delivery-user",
            envelope=_envelope("冲突正文"),
        )


def test_concurrent_workers_enqueue_once_and_only_one_claims_delivery(tmp_path):
    from core.chat_delivery_outbox import (
        claim_due_chat_delivery,
        enqueue_chat_delivery,
    )
    from core.database import Base, ChatDeliveryOutbox

    engine = create_engine(
        f"sqlite:///{tmp_path / 'chat-delivery-concurrency.db'}",
        connect_args={"timeout": 5},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    enqueue_barrier = threading.Barrier(2)
    claim_barrier = threading.Barrier(2)
    now = datetime(2026, 7, 11, 12, 0, 0)

    def enqueue_from_worker() -> int:
        with session_factory() as db:
            enqueue_barrier.wait(timeout=5)
            row = enqueue_chat_delivery(
                db,
                key=_key(),
                target_type="private",
                target_id="delivery-user",
                envelope=_envelope(),
                now=now,
            )
            db.commit()
            return int(row.id)

    def claim_from_worker(owner_token: str) -> tuple[str, int] | None:
        with session_factory() as db:
            claim_barrier.wait(timeout=5)
            row = claim_due_chat_delivery(
                db,
                owner_token=owner_token,
                now=now,
                lease_seconds=60,
            )
            db.commit()
            if row is None:
                return None
            return owner_token, int(row.id)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            enqueue_results = list(executor.map(
                lambda _index: enqueue_from_worker(),
                range(2),
            ))
        assert len(set(enqueue_results)) == 1
        with session_factory() as db:
            assert db.query(ChatDeliveryOutbox).count() == 1

        with ThreadPoolExecutor(max_workers=2) as executor:
            claim_results = list(executor.map(
                claim_from_worker,
                ("owner-a", "owner-b"),
            ))
        claimed = [item for item in claim_results if item is not None]
        assert len(claimed) == 1
        assert claimed[0][1] == enqueue_results[0]
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("target_type", "target_id", "envelope"),
    [
        ("", "user", _envelope()),
        ("private", "", _envelope()),
        ("private", "user", []),
        ("private", "user", {"bad": float("nan")}),
    ],
)
def test_enqueue_chat_delivery_rejects_invalid_target_or_envelope(
    db_session,
    target_type,
    target_id,
    envelope,
):
    from core.chat_delivery_outbox import enqueue_chat_delivery

    with pytest.raises((TypeError, ValueError)):
        enqueue_chat_delivery(
            db_session,
            key=_key(),
            target_type=target_type,
            target_id=target_id,
            envelope=envelope,
        )


def test_chat_delivery_claim_and_settlement_are_owner_fenced(db_session):
    from core.chat_delivery_outbox import (
        claim_due_chat_delivery,
        enqueue_chat_delivery,
        mark_chat_delivery_delivered,
    )

    now = datetime(2026, 7, 11, 12, 0, 0)
    row = enqueue_chat_delivery(
        db_session,
        key=_key(),
        target_type="private",
        target_id="delivery-user",
        envelope=_envelope(),
        now=now,
    )
    db_session.commit()

    claimed = claim_due_chat_delivery(
        db_session,
        owner_token="owner-a",
        now=now,
        lease_seconds=60,
    )
    db_session.commit()
    assert claimed is not None
    assert claimed.id == row.id
    assert claimed.status == "sending"
    assert claimed.attempt_count == 1
    assert claim_due_chat_delivery(
        db_session,
        owner_token="owner-b",
        now=now,
        lease_seconds=60,
    ) is None
    assert mark_chat_delivery_delivered(
        db_session,
        row_id=row.id,
        owner_token="owner-b",
        now=now,
    ) is False
    assert mark_chat_delivery_delivered(
        db_session,
        row_id=row.id,
        owner_token="owner-a",
        now=now,
    ) is True
    db_session.commit()
    db_session.refresh(row)
    assert row.status == "delivered"
    assert row.owner_token == ""
    assert row.lease_expires_at is None
    assert row.delivered_at == now


def test_ambiguous_delivery_uses_exponential_backoff_and_false_is_terminal(db_session):
    from core.chat_delivery_outbox import (
        claim_due_chat_delivery,
        enqueue_chat_delivery,
        mark_chat_delivery_ambiguous,
        mark_chat_delivery_failed,
    )

    now = datetime(2026, 7, 11, 12, 0, 0)
    row = enqueue_chat_delivery(
        db_session,
        key=_key(),
        target_type="private",
        target_id="delivery-user",
        envelope=_envelope(),
        now=now,
    )
    db_session.commit()
    first = claim_due_chat_delivery(
        db_session,
        owner_token="owner-1",
        now=now,
        lease_seconds=60,
    )
    assert first is not None
    assert mark_chat_delivery_ambiguous(
        db_session,
        row_id=row.id,
        owner_token="owner-1",
        error="network timeout",
        now=now,
    ) is True
    db_session.commit()
    db_session.refresh(row)
    assert row.status == "ambiguous"
    assert row.next_attempt_at == now + timedelta(seconds=30)
    assert claim_due_chat_delivery(
        db_session,
        owner_token="too-early",
        now=now + timedelta(seconds=29),
        lease_seconds=60,
    ) is None

    second = claim_due_chat_delivery(
        db_session,
        owner_token="owner-2",
        now=now + timedelta(seconds=30),
        lease_seconds=60,
    )
    assert second is not None
    assert second.attempt_count == 2
    assert mark_chat_delivery_ambiguous(
        db_session,
        row_id=row.id,
        owner_token="owner-2",
        error="network timeout again",
        now=now + timedelta(seconds=30),
    ) is True
    db_session.commit()
    db_session.refresh(row)
    assert row.next_attempt_at == now + timedelta(seconds=90)

    third = claim_due_chat_delivery(
        db_session,
        owner_token="owner-3",
        now=now + timedelta(seconds=90),
        lease_seconds=60,
    )
    assert third is not None
    assert mark_chat_delivery_failed(
        db_session,
        row_id=row.id,
        owner_token="owner-3",
        error="gateway rejected",
        now=now + timedelta(seconds=90),
    ) is True
    db_session.commit()
    db_session.refresh(row)
    assert row.status == "failed"
    assert row.next_attempt_at is None
    assert json.loads(row.envelope_json) == _envelope()


def test_expired_sending_lease_recovers_as_ambiguous_and_fences_old_owner(db_session):
    from core.chat_delivery_outbox import (
        claim_due_chat_delivery,
        enqueue_chat_delivery,
        mark_chat_delivery_delivered,
        recover_stale_chat_deliveries,
    )

    now = datetime(2026, 7, 11, 12, 0, 0)
    row = enqueue_chat_delivery(
        db_session,
        key=_key(),
        target_type="private",
        target_id="delivery-user",
        envelope=_envelope(),
        now=now,
    )
    db_session.commit()
    claimed = claim_due_chat_delivery(
        db_session,
        owner_token="expired-owner",
        now=now,
        lease_seconds=60,
    )
    db_session.commit()
    assert claimed is not None

    assert recover_stale_chat_deliveries(
        db_session,
        now=now + timedelta(seconds=59),
    ) == 0
    assert recover_stale_chat_deliveries(
        db_session,
        now=now + timedelta(seconds=60),
    ) == 1
    db_session.commit()
    db_session.refresh(row)

    assert row.status == "ambiguous"
    assert row.owner_token == ""
    assert row.lease_expires_at is None
    assert row.next_attempt_at == now + timedelta(seconds=90)
    assert "租约过期" in row.last_error
    assert mark_chat_delivery_delivered(
        db_session,
        row_id=row.id,
        owner_token="expired-owner",
        now=now + timedelta(seconds=61),
    ) is False


def _outbox_table_sql(*, include_status_check: bool = True) -> str:
    checks = [
        "CONSTRAINT ck_chat_delivery_outbox_attempt_count "
        "CHECK (attempt_count >= 0)",
    ]
    if include_status_check:
        checks.insert(
            0,
            "CONSTRAINT ck_chat_delivery_outbox_status "
            "CHECK (status IN ('pending', 'sending', 'ambiguous', 'delivered', 'failed'))",
        )
    definitions = [
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT",
        "delivery_key VARCHAR(64) NOT NULL",
        "platform VARCHAR(32) NOT NULL",
        "chat_type VARCHAR(16) NOT NULL",
        "session_id VARCHAR(255) NOT NULL",
        "message_id VARCHAR(255) NOT NULL",
        "target_type VARCHAR(16) NOT NULL",
        "target_id VARCHAR(255) NOT NULL",
        "envelope_json TEXT NOT NULL DEFAULT '{}'",
        "status VARCHAR(16) NOT NULL DEFAULT 'pending'",
        "owner_token VARCHAR(64) NOT NULL DEFAULT ''",
        "lease_expires_at DATETIME",
        "attempt_count INTEGER NOT NULL DEFAULT 0",
        "next_attempt_at DATETIME",
        "last_error TEXT NOT NULL DEFAULT ''",
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "delivered_at DATETIME",
        *checks,
    ]
    return "CREATE TABLE chat_delivery_outbox (" + ", ".join(definitions) + ")"


def test_chat_delivery_outbox_migration_creates_and_accepts_orm_schema():
    from core.chat_delivery_outbox_schema import chat_delivery_outbox_table
    from core.database import Base

    fresh_engine = create_engine("sqlite:///:memory:")
    orm_engine = create_engine("sqlite:///:memory:")
    try:
        with fresh_engine.begin() as conn:
            chat_delivery_outbox_table(conn, fresh_engine, None)
        assert "chat_delivery_outbox" in inspect(fresh_engine).get_table_names()
        migration_checks = {
            item["name"]
            for item in inspect(fresh_engine).get_check_constraints(
                "chat_delivery_outbox"
            )
        }
        assert migration_checks == {
            "ck_chat_delivery_outbox_attempt_count",
            "ck_chat_delivery_outbox_status",
        }

        Base.metadata.create_all(orm_engine)
        with orm_engine.begin() as conn:
            chat_delivery_outbox_table(conn, orm_engine, None)
    finally:
        fresh_engine.dispose()
        orm_engine.dispose()


def test_chat_delivery_outbox_migration_rejects_malformed_existing_table():
    from core.chat_delivery_outbox_schema import chat_delivery_outbox_table
    from core.schema_validation import SchemaMigrationValidationError

    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.execute(text(_outbox_table_sql(include_status_check=False)))
        with pytest.raises(SchemaMigrationValidationError):
            with engine.begin() as conn:
                chat_delivery_outbox_table(conn, engine, None)
    finally:
        engine.dispose()


def test_chat_delivery_outbox_migration_is_registered():
    from core.schema_migrations import MIGRATIONS

    versions = [version for version, _name, _fn in MIGRATIONS]
    assert versions.count("20260711_chat_delivery_outbox") == 1
    assert versions.index("20260710_proactive_outreach_leases") < versions.index(
        "20260711_chat_delivery_outbox"
    )

import asyncio
import json
import math
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, insert, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from tests.sqlite_test_utils import install_base_schema


@pytest.fixture(autouse=True)
def _enable_model_path_for_chat_idempotency_tests(monkeypatch):
    """本模块验证模型与幂等路径，显式关闭默认仅入库策略。"""

    monkeypatch.setattr(
        "api.routes.is_database_only_enabled",
        lambda *_args, **_kwargs: False,
    )


class FatalClaimLifecycleError(BaseException):
    pass


def _use_fast_chat_timers(monkeypatch) -> None:
    """身份重放集成测试不等待生产缓冲和心跳间隔。"""

    from api import chat_sse_loop
    from tests.test_api import _fast_private_reply

    original = chat_sse_loop.iter_chat_stream_events
    _fast_private_reply(monkeypatch)

    async def fast_iter(*args, **kwargs):
        kwargs["heartbeat_interval"] = 0.01
        async for stream_event in original(*args, **kwargs):
            yield stream_event

    monkeypatch.setattr(chat_sse_loop, "iter_chat_stream_events", fast_iter)


def _memory_session():

    engine = create_engine("sqlite:///:memory:")
    install_base_schema(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)()


def _key(message_id: str = "message-1"):
    from core.inbound_idempotency import normalize_inbound_claim_key

    return normalize_inbound_claim_key("qq", "private", "session-1", message_id)


def _response(*, reply: str = "你好，<b>世界</b>", reason: str = "ok"):
    from core.inbound_idempotency import CompletedInboundResponse, GroupReplayFields

    return CompletedInboundResponse(
        outcome="respond",
        reply=reply,
        reply_meta={
            "send_mode": "quote",
            "reply_to_message_id": "source-message-1",
            "mentions": [{"id": "42", "name": "小明"}],
            "quote": "引用内容",
            "at_sender": True,
        },
        reason=reason,
        source="bridge",
        intent="answer",
        guardrail_status="safe",
        unprocessed_logs=3,
        group=GroupReplayFields(
            generation=7,
            delay_seconds=2.5,
            diagnostics={"timing_action": "continue", "agent_result": "safe"},
            duplicate_reply={
                "previous_log_id": 18,
                "similarity": 0.9876,
                "previous_created_at": "2026-07-10T08:00:00",
            },
            hard_rule="",
        ),
    )


def _inject_locked_error_after_first_real_commit(db, monkeypatch):
    real_commit = db.commit
    state = {"commit_calls": 0, "injected": 0}

    def commit_then_raise_once():
        state["commit_calls"] += 1
        real_commit()
        if state["injected"] == 0:
            state["injected"] = 1
            raise OperationalError(
                "COMMIT",
                {},
                sqlite3.OperationalError("database is locked"),
            )

    monkeypatch.setattr(db, "commit", commit_then_raise_once)
    return state


def _inject_precommit_locked_and_failed_first_rollback(db, monkeypatch):
    real_commit = db.commit
    real_rollback = db.rollback
    locked_error = OperationalError(
        "COMMIT",
        {},
        sqlite3.OperationalError("database is locked"),
    )
    rollback_error = RuntimeError("rollback failed before retry")
    state = {"commit_calls": 0, "rollback_calls": 0}

    def commit_before_success():
        state["commit_calls"] += 1
        if state["commit_calls"] == 1:
            raise locked_error
        return real_commit()

    def rollback_fails_once():
        state["rollback_calls"] += 1
        if state["rollback_calls"] == 1:
            raise rollback_error
        return real_rollback()

    monkeypatch.setattr(db, "commit", commit_before_success)
    monkeypatch.setattr(db, "rollback", rollback_fails_once)
    return state, locked_error, rollback_error


_CLAIM_IDENTITY_INDEX = (
    "CREATE UNIQUE INDEX uq_inbound_message_claim_identity "
    "ON inbound_message_claims(platform, chat_type, session_id, message_id)"
)
_CLAIM_LEASE_INDEX = (
    "CREATE INDEX ix_inbound_message_claim_status_lease "
    "ON inbound_message_claims(status, lease_expires_at)"
)

_CLAIM_SCHEMA_IDENTIFIERS = (
    "inbound_message_claims",
    "ck_inbound_message_claim_status",
    "ck_inbound_message_claim_attempt_count",
    "uq_inbound_message_claim_identity",
    "ix_inbound_message_claim_status_lease",
    "id",
    "platform",
    "chat_type",
    "session_id",
    "message_id",
    "status",
    "owner_token",
    "lease_expires_at",
    "response_json",
    "error_summary",
    "attempt_count",
    "created_at",
    "updated_at",
    "completed_at",
)


def _existing_claim_table_sql(
    *,
    include_checks: bool = True,
    include_autoincrement: bool = True,
    omit: set[str] | None = None,
    extra_columns: tuple[str, ...] = (),
    extra_constraints: tuple[str, ...] = (),
) -> str:
    omitted = omit or set()
    id_definition = "id INTEGER NOT NULL PRIMARY KEY"
    if include_autoincrement:
        id_definition += " AUTOINCREMENT"
    columns = [
        ("id", id_definition),
        ("platform", "platform VARCHAR(32) NOT NULL"),
        ("chat_type", "chat_type VARCHAR(16) NOT NULL"),
        ("session_id", "session_id VARCHAR(255) NOT NULL"),
        ("message_id", "message_id VARCHAR(255) NOT NULL"),
        ("status", "status VARCHAR(16) NOT NULL DEFAULT 'processing'"),
        ("owner_token", "owner_token VARCHAR(64) NOT NULL"),
        ("lease_expires_at", "lease_expires_at DATETIME"),
        ("response_json", "response_json TEXT NOT NULL DEFAULT ''"),
        ("error_summary", "error_summary TEXT NOT NULL DEFAULT ''"),
        ("attempt_count", "attempt_count INTEGER NOT NULL DEFAULT 1"),
        ("created_at", "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"),
        ("updated_at", "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"),
        ("completed_at", "completed_at DATETIME"),
    ]
    definitions = [ddl for name, ddl in columns if name not in omitted]
    definitions.extend(extra_columns)
    if include_checks:
        definitions.extend([
            "CONSTRAINT ck_inbound_message_claim_status "
            "CHECK (status IN ('processing', 'completed', 'failed'))",
            "CONSTRAINT ck_inbound_message_claim_attempt_count CHECK (attempt_count >= 1)",
        ])
    definitions.extend(extra_constraints)
    return "CREATE TABLE inbound_message_claims (" + ", ".join(definitions) + ")"


def _claim_schema_with_identifier_transform(transform):
    def transform_sql(sql: str) -> str:
        for identifier in sorted(_CLAIM_SCHEMA_IDENTIFIERS, key=len, reverse=True):
            sql = re.sub(
                rf"\b{re.escape(identifier)}\b",
                transform(identifier),
                sql,
                flags=re.IGNORECASE,
            )
        return sql

    return (
        transform_sql(_existing_claim_table_sql()),
        [transform_sql(_CLAIM_IDENTITY_INDEX), transform_sql(_CLAIM_LEASE_INDEX)],
    )


def _assert_claim_migration_accepts_identifier_transform(transform) -> None:
    from core.schema_migrations import run_schema_migrations

    table_sql, indexes = _claim_schema_with_identifier_transform(transform)
    assert table_sql.count("'processing'") == 2
    assert table_sql.count("'completed'") == 1
    assert table_sql.count("'failed'") == 1

    engine = _engine_with_existing_claim_schema(table_sql, indexes)
    try:
        run_schema_migrations(engine)
        with engine.connect() as conn:
            migration_count = conn.execute(text(
                "SELECT COUNT(*) FROM schema_migrations "
                "WHERE version = '20260710_inbound_message_claims'"
            )).scalar_one()
        assert migration_count == 1
    finally:
        engine.dispose()


def _engine_with_existing_claim_schema(
    table_sql: str,
    index_statements: list[str],
    *,
    prelude_statements: tuple[str, ...] = (),
):
    from core.schema_migrations import MIGRATIONS

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE schema_migrations ("
            "version TEXT PRIMARY KEY, name TEXT NOT NULL, applied_at DATETIME NOT NULL)"
        ))
        prior_versions = [
            {"version": version, "name": name}
            for version, name, _ in MIGRATIONS
            if version != "20260710_inbound_message_claims"
        ]
        conn.execute(text(
            "INSERT INTO schema_migrations(version, name, applied_at) "
            "VALUES (:version, :name, CURRENT_TIMESTAMP)"
        ), prior_versions)
        for statement in prelude_statements:
            conn.execute(text(statement))
        conn.execute(text(table_sql))
        for statement in index_statements:
            conn.execute(text(statement))
    return engine


def _assert_claim_migration_not_recorded(engine) -> None:
    with engine.connect() as conn:
        count = conn.execute(text(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = '20260710_inbound_message_claims'"
        )).scalar_one()
    assert count == 0


def test_inbound_claim_orm_creates_named_identity_and_status_lease_indexes():
    from core.database import InboundMessageClaim

    engine = create_engine("sqlite:///:memory:")
    install_base_schema(engine)

    assert InboundMessageClaim.__tablename__ == "inbound_message_claims"
    indexes = {item["name"]: item for item in inspect(engine).get_indexes("inbound_message_claims")}
    assert indexes["uq_inbound_message_claim_identity"]["unique"] == 1
    assert indexes["uq_inbound_message_claim_identity"]["column_names"] == [
        "platform",
        "chat_type",
        "session_id",
        "message_id",
    ]
    assert indexes["ix_inbound_message_claim_status_lease"]["column_names"] == [
        "status",
        "lease_expires_at",
    ]

    columns = {item["name"]: item for item in inspect(engine).get_columns("inbound_message_claims")}
    assert list(columns) == [
        "id",
        "platform",
        "chat_type",
        "session_id",
        "message_id",
        "status",
        "owner_token",
        "lease_expires_at",
        "response_json",
        "error_summary",
        "attempt_count",
        "created_at",
        "updated_at",
        "completed_at",
    ]
    assert columns["platform"]["type"].length == 32
    assert columns["chat_type"]["type"].length == 16
    assert columns["session_id"]["type"].length == 255
    assert columns["message_id"]["type"].length == 255
    assert columns["owner_token"]["type"].length == 64
    for name in (
        "platform",
        "chat_type",
        "session_id",
        "message_id",
        "status",
        "owner_token",
        "response_json",
        "error_summary",
        "attempt_count",
        "created_at",
        "updated_at",
    ):
        assert columns[name]["nullable"] is False
    checks = {item["name"]: item["sqltext"] for item in inspect(engine).get_check_constraints(
        "inbound_message_claims"
    )}
    assert "processing" in checks["ck_inbound_message_claim_status"]
    assert "attempt_count >= 1" in checks["ck_inbound_message_claim_attempt_count"]
    with engine.connect() as conn:
        table_sql = conn.execute(text(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'inbound_message_claims'"
        )).scalar_one()
    assert "PRIMARY KEY AUTOINCREMENT" in table_sql


def test_inbound_claim_orm_defaults_checks_and_raw_identity_uniqueness():
    from core.database import InboundMessageClaim

    engine = create_engine("sqlite:///:memory:")
    install_base_schema(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    db.add(InboundMessageClaim(
        platform="qq",
        chat_type="private",
        session_id="s1",
        message_id="m1",
        owner_token="a" * 64,
    ))
    db.commit()
    row = db.scalar(select(InboundMessageClaim))
    assert row.status == "processing"
    assert row.response_json == ""
    assert row.error_summary == ""
    assert row.attempt_count == 1
    assert isinstance(row.created_at, datetime)
    assert isinstance(row.updated_at, datetime)
    db.rollback()

    insert_sql = text(
        "INSERT INTO inbound_message_claims "
        "(platform, chat_type, session_id, message_id, owner_token) "
        "VALUES (:platform, :chat_type, :session_id, :message_id, :owner_token)"
    )
    with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(insert_sql, {
                "platform": "qq",
                "chat_type": "private",
                "session_id": "s1",
                "message_id": "m1",
                "owner_token": "b" * 64,
            })
        with pytest.raises(IntegrityError):
            conn.execute(text(
                "INSERT INTO inbound_message_claims "
                "(platform, chat_type, session_id, message_id, status, owner_token) "
                "VALUES ('qq', 'private', 's1', 'bad-status', 'unknown', :token)"
            ), {"token": "c" * 64})
        with pytest.raises(IntegrityError):
            conn.execute(text(
                "INSERT INTO inbound_message_claims "
                "(platform, chat_type, session_id, message_id, owner_token, attempt_count) "
                "VALUES ('qq', 'private', 's1', 'bad-attempt', :token, 0)"
            ), {"token": "d" * 64})


def test_inbound_claim_server_defaults_visible_to_new_raw_connection(tmp_path):

    db_path = tmp_path / "claim-server-defaults.db"
    writer_engine = create_engine(f"sqlite:///{db_path}")
    observer_engine = create_engine(f"sqlite:///{db_path}")
    install_base_schema(writer_engine)

    try:
        with writer_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO inbound_message_claims "
                "(platform, chat_type, session_id, message_id, owner_token) "
                "VALUES ('qq', 'private', 'server-default-session', "
                "'server-default-message', :token)"
            ), {"token": "e" * 64})

        with observer_engine.connect() as conn:
            row = conn.execute(text(
                "SELECT status, response_json, error_summary, attempt_count, "
                "created_at, updated_at, completed_at "
                "FROM inbound_message_claims "
                "WHERE message_id = 'server-default-message'"
            )).mappings().one()

        assert row["status"] == "processing"
        assert row["response_json"] == ""
        assert row["error_summary"] == ""
        assert row["attempt_count"] == 1
        assert row["created_at"] is not None
        assert row["updated_at"] is not None
        assert row["completed_at"] is None
    finally:
        writer_engine.dispose()
        observer_engine.dispose()


def test_inbound_claim_additive_migration_is_idempotent_and_matches_orm_contract():
    from core.schema_migrations import run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    run_schema_migrations(engine)
    run_schema_migrations(engine)

    inspector = inspect(engine)
    assert "inbound_message_claims" in inspector.get_table_names()
    assert {item["name"] for item in inspector.get_columns("inbound_message_claims")} == {
        "id",
        "platform",
        "chat_type",
        "session_id",
        "message_id",
        "status",
        "owner_token",
        "lease_expires_at",
        "response_json",
        "error_summary",
        "attempt_count",
        "created_at",
        "updated_at",
        "completed_at",
    }
    indexes = {item["name"]: item for item in inspector.get_indexes("inbound_message_claims")}
    assert indexes["uq_inbound_message_claim_identity"]["unique"] == 1
    assert indexes["ix_inbound_message_claim_status_lease"]["column_names"] == [
        "status",
        "lease_expires_at",
    ]
    with engine.connect() as conn:
        table_sql = conn.execute(text(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'inbound_message_claims'"
        )).scalar_one()
    assert "ck_inbound_message_claim_status" in table_sql
    assert "ck_inbound_message_claim_attempt_count" in table_sql

    insert_sql = text(
        "INSERT INTO inbound_message_claims "
        "(platform, chat_type, session_id, message_id, owner_token) "
        "VALUES ('qq', 'group', 'g1', 'm1', :token)"
    )
    with engine.begin() as conn:
        conn.execute(insert_sql, {"token": "a" * 64})
        with pytest.raises(IntegrityError):
            conn.execute(insert_sql, {"token": "b" * 64})
        with pytest.raises(IntegrityError):
            conn.execute(text(
                "INSERT INTO inbound_message_claims "
                "(platform, chat_type, session_id, message_id, owner_token, attempt_count) "
                "VALUES ('qq', 'group', 'g1', 'm2', :token, 0)"
            ), {"token": "c" * 64})


def test_proactive_outreach_lease_migration_matches_orm_nullability_and_index():
    from core.schema_migrations import run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    run_schema_migrations(engine)
    run_schema_migrations(engine)

    with engine.begin() as conn:
        columns = {
            str(row["name"]): row
            for row in conn.execute(
                text("PRAGMA table_xinfo(proactive_outreach_leases)")
            ).mappings()
        }
        assert tuple(columns) == (
            "user_id",
            "owner_token",
            "lease_expires_at",
            "created_at",
            "updated_at",
        )
        assert int(columns["user_id"]["notnull"]) == 1
        assert int(columns["user_id"]["pk"]) == 1
        index = conn.execute(text(
            "PRAGMA index_xinfo(ix_proactive_outreach_lease_expires_at)"
        )).mappings().all()
        assert [row["name"] for row in index if int(row["key"]) == 1] == [
            "lease_expires_at"
        ]
        with pytest.raises(IntegrityError):
            conn.execute(text(
                "INSERT INTO proactive_outreach_leases "
                "(user_id, owner_token, lease_expires_at) "
                "VALUES (NULL, 'owner', CURRENT_TIMESTAMP)"
            ))


def test_proactive_outreach_lease_migration_rejects_existing_schema_drift():
    from core.schema_migrations import SchemaMigrationValidationError, run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE proactive_outreach_leases ("
            "user_id VARCHAR(255) PRIMARY KEY, "
            "owner_token INTEGER, "
            "lease_expires_at TEXT, "
            "created_at DATETIME, "
            "updated_at DATETIME"
            ")"
        ))
        conn.execute(text(
            "CREATE INDEX ix_proactive_outreach_lease_expires_at "
            "ON proactive_outreach_leases(owner_token)"
        ))

    with pytest.raises(SchemaMigrationValidationError, match="proactive_outreach_leases"):
        run_schema_migrations(engine)

    with engine.connect() as conn:
        applied = conn.execute(text(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = '20260710_proactive_outreach_leases'"
        )).scalar_one()
    assert applied == 0


@pytest.mark.parametrize(
    "extra_constraint",
    [
        ", CONSTRAINT ck_proactive_lease_owner CHECK (owner_token = 'only-owner')",
        ", UNIQUE(lease_expires_at)",
    ],
    ids=["extra-check", "extra-unique"],
)
def test_proactive_outreach_lease_migration_rejects_extra_write_constraints(
    extra_constraint,
):
    from core.schema_migrations import SchemaMigrationValidationError, run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE proactive_outreach_leases ("
            "user_id VARCHAR(255) NOT NULL PRIMARY KEY, "
            "owner_token VARCHAR(64) NOT NULL, "
            "lease_expires_at DATETIME NOT NULL, "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            f"{extra_constraint}"
            ")"
        ))
        conn.execute(text(
            "CREATE INDEX ix_proactive_outreach_lease_expires_at "
            "ON proactive_outreach_leases(lease_expires_at)"
        ))

    with pytest.raises(SchemaMigrationValidationError, match="proactive_outreach_leases"):
        run_schema_migrations(engine)


def test_proactive_outreach_lease_migration_rejects_extra_expression_index():
    from core.schema_migrations import SchemaMigrationValidationError, run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE proactive_outreach_leases ("
            "user_id VARCHAR(255) NOT NULL PRIMARY KEY, "
            "owner_token VARCHAR(64) NOT NULL, "
            "lease_expires_at DATETIME NOT NULL, "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ")"
        ))
        conn.execute(text(
            "CREATE INDEX ix_proactive_outreach_lease_expires_at "
            "ON proactive_outreach_leases(lease_expires_at)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_proactive_outreach_lease_expression "
            "ON proactive_outreach_leases(json_extract(owner_token, '$'))"
        ))

    with pytest.raises(SchemaMigrationValidationError, match="proactive_outreach_leases"):
        run_schema_migrations(engine)


def test_inbound_claim_migration_accepts_uppercase_identifiers():
    _assert_claim_migration_accepts_identifier_transform(str.upper)


def test_inbound_claim_migration_accepts_mixed_case_identifiers():
    def alternating_case(identifier: str) -> str:
        return "".join(
            character.upper() if index % 2 == 0 else character.lower()
            for index, character in enumerate(identifier)
        )

    _assert_claim_migration_accepts_identifier_transform(alternating_case)


def test_inbound_claim_migration_does_not_hide_malformed_existing_table():
    from core.schema_migrations import run_schema_migrations

    engine = _engine_with_existing_claim_schema(
        "CREATE TABLE inbound_message_claims (id INTEGER PRIMARY KEY)",
        [],
    )

    with pytest.raises(RuntimeError, match="inbound_message_claims"):
        run_schema_migrations(engine)
    _assert_claim_migration_not_recorded(engine)


def test_inbound_claim_migration_rejects_existing_table_missing_non_index_columns():
    from core.schema_migrations import run_schema_migrations

    engine = _engine_with_existing_claim_schema(
        _existing_claim_table_sql(omit={"owner_token", "response_json"}),
        [_CLAIM_IDENTITY_INDEX, _CLAIM_LEASE_INDEX],
    )

    with pytest.raises(RuntimeError, match="inbound_message_claims"):
        run_schema_migrations(engine)
    _assert_claim_migration_not_recorded(engine)


def test_inbound_claim_migration_rejects_existing_table_without_named_checks():
    from core.schema_migrations import run_schema_migrations

    engine = _engine_with_existing_claim_schema(
        _existing_claim_table_sql(include_checks=False),
        [_CLAIM_IDENTITY_INDEX, _CLAIM_LEASE_INDEX],
    )

    with pytest.raises(RuntimeError, match="inbound_message_claims"):
        run_schema_migrations(engine)
    _assert_claim_migration_not_recorded(engine)


@pytest.mark.parametrize(
    "identity_index",
    [
        "CREATE INDEX uq_inbound_message_claim_identity "
        "ON inbound_message_claims(platform, chat_type, session_id, message_id)",
        "CREATE UNIQUE INDEX uq_inbound_message_claim_identity "
        "ON inbound_message_claims(platform, chat_type, session_id, owner_token)",
    ],
    ids=["same_name_non_unique", "same_name_wrong_columns"],
)
def test_inbound_claim_migration_rejects_incorrect_same_name_identity_index(identity_index):
    from core.schema_migrations import run_schema_migrations

    engine = _engine_with_existing_claim_schema(
        _existing_claim_table_sql(),
        [identity_index, _CLAIM_LEASE_INDEX],
    )
    with engine.begin() as conn:
        insert = text(
            "INSERT INTO inbound_message_claims "
            "(platform, chat_type, session_id, message_id, owner_token) "
            "VALUES ('qq', 'private', 's1', 'm1', :owner_token)"
        )
        conn.execute(insert, {"owner_token": "a" * 64})
        conn.execute(insert, {"owner_token": "b" * 64})
        duplicate_count = conn.execute(text(
            "SELECT COUNT(*) FROM inbound_message_claims "
            "WHERE platform='qq' AND chat_type='private' "
            "AND session_id='s1' AND message_id='m1'"
        )).scalar_one()
    assert duplicate_count == 2

    with pytest.raises(RuntimeError, match="uq_inbound_message_claim_identity"):
        run_schema_migrations(engine)
    _assert_claim_migration_not_recorded(engine)


def test_inbound_claim_migration_rejects_wrong_same_name_status_lease_index():
    from core.schema_migrations import run_schema_migrations

    engine = _engine_with_existing_claim_schema(
        _existing_claim_table_sql(),
        [
            _CLAIM_IDENTITY_INDEX,
            "CREATE INDEX ix_inbound_message_claim_status_lease "
            "ON inbound_message_claims(status, completed_at)",
        ],
    )

    with pytest.raises(RuntimeError, match="ix_inbound_message_claim_status_lease"):
        run_schema_migrations(engine)
    _assert_claim_migration_not_recorded(engine)


def test_inbound_claim_migration_rejects_exact_table_without_autoincrement():
    from core.schema_migrations import run_schema_migrations

    engine = _engine_with_existing_claim_schema(
        _existing_claim_table_sql(include_autoincrement=False),
        [_CLAIM_IDENTITY_INDEX, _CLAIM_LEASE_INDEX],
    )

    with pytest.raises(RuntimeError, match="AUTOINCREMENT"):
        run_schema_migrations(engine)
    _assert_claim_migration_not_recorded(engine)


def test_inbound_claim_migration_rejects_checks_present_only_in_comment():
    from core.schema_migrations import SchemaMigrationValidationError, run_schema_migrations

    fake_checks = (
        " /* CONSTRAINT ck_inbound_message_claim_status "
        "CHECK (status IN ('processing', 'completed', 'failed')), "
        "CONSTRAINT ck_inbound_message_claim_attempt_count "
        "CHECK (attempt_count >= 1) */"
    )
    table_without_checks = _existing_claim_table_sql(include_checks=False)
    table_sql = table_without_checks[:-1] + fake_checks + ")"
    engine = _engine_with_existing_claim_schema(
        table_sql,
        [_CLAIM_IDENTITY_INDEX, _CLAIM_LEASE_INDEX],
    )

    try:
        with pytest.raises(SchemaMigrationValidationError, match="CHECK"):
            run_schema_migrations(engine)
        _assert_claim_migration_not_recorded(engine)
    finally:
        engine.dispose()


def test_inbound_claim_migration_rejects_autoincrement_present_only_in_comment():
    from core.schema_migrations import SchemaMigrationValidationError, run_schema_migrations

    fake_autoincrement = " /* id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT */"
    table_without_autoincrement = _existing_claim_table_sql(include_autoincrement=False)
    table_sql = table_without_autoincrement[:-1] + fake_autoincrement + ")"
    engine = _engine_with_existing_claim_schema(
        table_sql,
        [_CLAIM_IDENTITY_INDEX, _CLAIM_LEASE_INDEX],
    )

    try:
        with pytest.raises(SchemaMigrationValidationError, match="AUTOINCREMENT"):
            run_schema_migrations(engine)
        _assert_claim_migration_not_recorded(engine)
    finally:
        engine.dispose()


def test_inbound_claim_migration_rejects_quoted_identifier_with_internal_space():
    from core.schema_migrations import SchemaMigrationValidationError, run_schema_migrations

    table_sql = _existing_claim_table_sql().replace(
        "CHECK (status IN ('processing', 'completed', 'failed'))",
        "CHECK (\"sta tus\" IN ('processing', 'completed', 'failed'))",
    )
    engine = _engine_with_existing_claim_schema(
        table_sql,
        [_CLAIM_IDENTITY_INDEX, _CLAIM_LEASE_INDEX],
    )

    try:
        with pytest.raises(SchemaMigrationValidationError, match="CHECK"):
            run_schema_migrations(engine)
        _assert_claim_migration_not_recorded(engine)
    finally:
        engine.dispose()


def test_inbound_claim_migration_rejects_unicode_spoof_in_status_check():
    from core.schema_migrations import SchemaMigrationValidationError, run_schema_migrations

    table_sql = _existing_claim_table_sql().replace(
        "CHECK (status IN ('processing', 'completed', 'failed'))",
        "CHECK (\"ſtatus\" IN ('processing', 'completed', 'failed'))",
    )
    engine = _engine_with_existing_claim_schema(
        table_sql,
        [_CLAIM_IDENTITY_INDEX, _CLAIM_LEASE_INDEX],
    )

    try:
        with pytest.raises(SchemaMigrationValidationError, match="CHECK"):
            run_schema_migrations(engine)
        _assert_claim_migration_not_recorded(engine)
    finally:
        engine.dispose()


def test_inbound_claim_migration_rejects_unicode_spoof_in_column_and_index_metadata():
    from core.schema_migrations import SchemaMigrationValidationError, run_schema_migrations

    table_sql = _existing_claim_table_sql().replace(
        "status VARCHAR(16) NOT NULL DEFAULT 'processing'",
        "\"ſtatus\" VARCHAR(16) NOT NULL DEFAULT 'processing'",
    ).replace(
        "CHECK (status IN ('processing', 'completed', 'failed'))",
        "CHECK (\"ſtatus\" IN ('processing', 'completed', 'failed'))",
    )
    lease_index = _CLAIM_LEASE_INDEX.replace(
        "ON inbound_message_claims(status, lease_expires_at)",
        "ON inbound_message_claims(\"ſtatus\", lease_expires_at)",
    )
    engine = _engine_with_existing_claim_schema(
        table_sql,
        [_CLAIM_IDENTITY_INDEX, lease_index],
    )

    try:
        with engine.connect() as conn:
            actual_columns = tuple(
                str(row["name"])
                for row in conn.execute(
                    text("PRAGMA table_xinfo(inbound_message_claims)")
                ).mappings()
            )
            lease_index_columns = tuple(
                str(row["name"])
                for row in conn.execute(text(
                    "PRAGMA index_xinfo(ix_inbound_message_claim_status_lease)"
                )).mappings()
                if int(row["key"]) == 1
            )
        assert "ſtatus" in actual_columns
        assert "status" not in actual_columns
        assert lease_index_columns == ("ſtatus", "lease_expires_at")

        with pytest.raises(SchemaMigrationValidationError, match="inbound_message_claims"):
            run_schema_migrations(engine)
        _assert_claim_migration_not_recorded(engine)
    finally:
        engine.dispose()


def test_inbound_claim_migration_rejects_extra_check_that_breaks_processing_insert():
    from core.schema_migrations import run_schema_migrations

    engine = _engine_with_existing_claim_schema(
        _existing_claim_table_sql(extra_constraints=("CHECK (status = 'failed')",)),
        [_CLAIM_IDENTITY_INDEX, _CLAIM_LEASE_INDEX],
    )
    with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(text(
                "INSERT INTO inbound_message_claims "
                "(platform, chat_type, session_id, message_id, owner_token) "
                "VALUES ('qq', 'private', 's1', 'm-processing', :token)"
            ), {"token": "a" * 64})

    with pytest.raises(RuntimeError, match="CHECK"):
        run_schema_migrations(engine)
    _assert_claim_migration_not_recorded(engine)


@pytest.mark.parametrize(
    "table_sql",
    [
        _existing_claim_table_sql().replace(
            "DEFAULT 'processing'",
            "DEFAULT 'PROCESSING'",
        ),
        _existing_claim_table_sql().replace(
            "('processing', 'completed', 'failed')",
            "('PROCESSING', 'COMPLETED', 'FAILED')",
        ),
    ],
    ids=["uppercase_default_literal", "uppercase_check_literals"],
)
def test_inbound_claim_migration_preserves_status_literal_case(table_sql):
    from core.schema_migrations import SchemaMigrationValidationError, run_schema_migrations

    engine = _engine_with_existing_claim_schema(
        table_sql,
        [_CLAIM_IDENTITY_INDEX, _CLAIM_LEASE_INDEX],
    )

    with pytest.raises(SchemaMigrationValidationError, match="status|CHECK|定义"):
        run_schema_migrations(engine)
    _assert_claim_migration_not_recorded(engine)


@pytest.mark.parametrize(
    "modified_index",
    [
        "CREATE UNIQUE INDEX uq_inbound_message_claim_identity "
        "ON inbound_message_claims("
        "platform, chat_type, session_id, message_id COLLATE NOCASE DESC)",
        "CREATE INDEX ix_inbound_message_claim_status_lease "
        "ON inbound_message_claims(status, lease_expires_at DESC)",
    ],
    ids=["identity_nocase_desc", "lease_desc"],
)
def test_inbound_claim_migration_rejects_collation_or_sort_modified_key_index(
    modified_index,
):
    from core.schema_migrations import run_schema_migrations

    indexes = [
        modified_index
        if "uq_inbound_message_claim_identity" in modified_index
        else _CLAIM_IDENTITY_INDEX,
        modified_index
        if "ix_inbound_message_claim_status_lease" in modified_index
        else _CLAIM_LEASE_INDEX,
    ]
    engine = _engine_with_existing_claim_schema(_existing_claim_table_sql(), indexes)

    with pytest.raises(RuntimeError, match="索引"):
        run_schema_migrations(engine)
    _assert_claim_migration_not_recorded(engine)


@pytest.mark.parametrize("extra_unique_kind", ["explicit", "autoindex"])
def test_inbound_claim_migration_rejects_any_extra_unique_index(extra_unique_kind):
    from core.schema_migrations import run_schema_migrations

    extra_constraints: tuple[str, ...] = ()
    indexes = [_CLAIM_IDENTITY_INDEX, _CLAIM_LEASE_INDEX]
    if extra_unique_kind == "explicit":
        indexes.append(
            "CREATE UNIQUE INDEX uq_inbound_message_claim_extra "
            "ON inbound_message_claims(platform, session_id)"
        )
    else:
        extra_constraints = ("UNIQUE(platform, session_id)",)
    engine = _engine_with_existing_claim_schema(
        _existing_claim_table_sql(extra_constraints=extra_constraints),
        indexes,
    )

    with pytest.raises(RuntimeError, match="unique|UNIQUE|唯一"):
        run_schema_migrations(engine)
    _assert_claim_migration_not_recorded(engine)


def test_inbound_claim_migration_rejects_foreign_keys():
    from core.schema_migrations import run_schema_migrations

    engine = _engine_with_existing_claim_schema(
        _existing_claim_table_sql(extra_constraints=(
            "FOREIGN KEY(session_id) REFERENCES inbound_claim_sessions(id)",
        )),
        [_CLAIM_IDENTITY_INDEX, _CLAIM_LEASE_INDEX],
        prelude_statements=(
            "CREATE TABLE inbound_claim_sessions (id VARCHAR(255) PRIMARY KEY)",
        ),
    )

    with pytest.raises(RuntimeError, match="FOREIGN KEY|外键"):
        run_schema_migrations(engine)
    _assert_claim_migration_not_recorded(engine)


def test_inbound_claim_migration_rejects_owner_token_nocase_collation():
    from core.schema_migrations import run_schema_migrations

    table_sql = _existing_claim_table_sql().replace(
        "owner_token VARCHAR(64) NOT NULL",
        "owner_token VARCHAR(64) COLLATE NOCASE NOT NULL",
    )
    engine = _engine_with_existing_claim_schema(
        table_sql,
        [_CLAIM_IDENTITY_INDEX, _CLAIM_LEASE_INDEX],
    )
    lower_token = "abcdef0123456789" * 4
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO inbound_message_claims "
            "(platform, chat_type, session_id, message_id, owner_token) "
            "VALUES ('qq', 'private', 's1', 'm-collation', :owner_token)"
        ), {"owner_token": lower_token})
        uppercase_match_count = conn.execute(text(
            "SELECT COUNT(*) FROM inbound_message_claims "
            "WHERE owner_token = :owner_token"
        ), {"owner_token": lower_token.upper()}).scalar_one()
    assert uppercase_match_count == 1

    with pytest.raises(RuntimeError, match="COLLATE"):
        run_schema_migrations(engine)
    _assert_claim_migration_not_recorded(engine)


def test_inbound_claim_migration_rejects_hidden_generated_columns():
    from core.schema_migrations import run_schema_migrations

    engine = _engine_with_existing_claim_schema(
        _existing_claim_table_sql(extra_columns=(
            "claim_shadow TEXT GENERATED ALWAYS AS (owner_token) VIRTUAL",
        )),
        [_CLAIM_IDENTITY_INDEX, _CLAIM_LEASE_INDEX],
    )
    with engine.connect() as conn:
        table_info_names = tuple(
            row["name"]
            for row in conn.execute(text(
                "PRAGMA table_info(inbound_message_claims)"
            )).mappings()
        )
        table_xinfo_rows = [
            dict(row)
            for row in conn.execute(text(
                "PRAGMA table_xinfo(inbound_message_claims)"
            )).mappings()
        ]
    assert len(table_info_names) == 14
    assert table_xinfo_rows[-1]["name"] == "claim_shadow"
    assert table_xinfo_rows[-1]["hidden"] != 0

    with pytest.raises(RuntimeError, match="hidden|隐藏"):
        run_schema_migrations(engine)
    _assert_claim_migration_not_recorded(engine)


def test_inbound_claim_migration_rejects_existing_triggers():
    from core.schema_migrations import run_schema_migrations

    engine = _engine_with_existing_claim_schema(
        _existing_claim_table_sql(),
        [_CLAIM_IDENTITY_INDEX, _CLAIM_LEASE_INDEX],
    )
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TRIGGER inbound_claim_force_failed "
            "AFTER INSERT ON inbound_message_claims "
            "BEGIN "
            "UPDATE inbound_message_claims SET status = 'failed' WHERE id = NEW.id; "
            "END"
        ))
        conn.execute(text(
            "INSERT INTO inbound_message_claims "
            "(platform, chat_type, session_id, message_id, owner_token) "
            "VALUES ('qq', 'private', 's1', 'm-trigger', :owner_token)"
        ), {"owner_token": "a" * 64})
        stored_status = conn.execute(text(
            "SELECT status FROM inbound_message_claims WHERE message_id = 'm-trigger'"
        )).scalar_one()
    assert stored_status == "failed"

    with pytest.raises(RuntimeError, match="trigger|触发器"):
        run_schema_migrations(engine)
    _assert_claim_migration_not_recorded(engine)


def test_first_inbound_claim_is_acquired_and_commits_short_transaction():
    from core.inbound_idempotency import (
        ClaimDecisionKind,
        acquire_inbound_claim,
        normalize_inbound_claim_key,
    )

    _, db = _memory_session()
    key = normalize_inbound_claim_key(" QQ ", "private", "session-1", "message-1")

    decision = acquire_inbound_claim(
        db,
        key,
        now=datetime(2026, 7, 10, 8, 0, 0),
    )

    assert decision.kind is ClaimDecisionKind.ACQUIRED
    assert decision.handle is not None
    assert decision.handle.key == key
    assert decision.handle.attempt_count == 1
    assert len(decision.handle.owner_token) == 64
    assert not db.in_transaction()


def test_claim_writes_do_not_emit_returning_and_all_transitions_work():
    from core.database import InboundMessageClaim
    from core.inbound_idempotency import (
        ClaimDecisionKind,
        acquire_inbound_claim,
        complete_inbound_claim,
        fail_inbound_claim,
        renew_inbound_claim,
    )

    engine = create_engine("sqlite:///:memory:")
    install_base_schema(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime(2026, 7, 10, 8, 0, 0)

    def reject_returning(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "RETURNING" in statement.upper():
            raise AssertionError(f"claim SQL 不得包含 RETURNING: {statement}")

    event.listen(engine, "before_cursor_execute", reject_returning)
    try:
        with Session() as db:
            first = acquire_inbound_claim(db, _key("no-returning-complete"), now=now)
            assert first.kind is ClaimDecisionKind.ACQUIRED
            assert first.handle.attempt_count == 1
            assert renew_inbound_claim(db, first.handle, now=now + timedelta(seconds=1))
            assert complete_inbound_claim(
                db,
                first.handle,
                _response(),
                now=now + timedelta(seconds=2),
            )

            original = acquire_inbound_claim(
                db,
                _key("no-returning-takeover"),
                now=now,
                lease_seconds=5,
            )
            takeover = acquire_inbound_claim(
                db,
                _key("no-returning-takeover"),
                now=now + timedelta(seconds=6),
            )
            assert takeover.kind is ClaimDecisionKind.ACQUIRED
            assert takeover.handle.owner_token != original.handle.owner_token
            assert takeover.handle.attempt_count == 2

            failed = acquire_inbound_claim(db, _key("no-returning-fail"), now=now)
            assert fail_inbound_claim(
                db,
                failed.handle,
                "expected failure",
                now=now + timedelta(seconds=3),
            )

        with Session() as observer:
            rows = {
                row.message_id: row
                for row in observer.scalars(select(InboundMessageClaim)).all()
            }
            assert rows["no-returning-complete"].status == "completed"
            assert rows["no-returning-takeover"].attempt_count == 2
            assert rows["no-returning-fail"].status == "failed"
    finally:
        event.remove(engine, "before_cursor_execute", reject_returning)
        engine.dispose()


@pytest.mark.parametrize(
    "operation_name",
    ["acquire", "takeover", "renew", "complete", "fail"],
)
def test_claim_commit_ambiguous_retry_changes_state_only_once(
    tmp_path,
    monkeypatch,
    operation_name,
):
    from core.database import InboundMessageClaim
    from core.inbound_idempotency import (
        ClaimDecisionKind,
        acquire_inbound_claim,
        complete_inbound_claim,
        fail_inbound_claim,
        renew_inbound_claim,
    )

    monkeypatch.setenv("SQLITE_LOCK_RETRY_ATTEMPTS", "4")
    monkeypatch.setenv("SQLITE_LOCK_RETRY_BASE_DELAY_SECONDS", "0")
    db_path = tmp_path / f"commit-ambiguous-{operation_name}.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 0.05},
    )
    try:
        install_base_schema(engine)
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        now = datetime(2026, 7, 10, 8, 0, 0)
        key = _key(f"commit-ambiguous-{operation_name}")
        original_handle = None
        result_handle = None

        with Session() as db:
            if operation_name != "acquire":
                original_handle = acquire_inbound_claim(
                    db,
                    key,
                    now=now,
                    lease_seconds=1,
                ).handle

            commit_state = _inject_locked_error_after_first_real_commit(db, monkeypatch)
            if operation_name == "acquire":
                decision = acquire_inbound_claim(db, key, now=now)
                assert decision.kind is ClaimDecisionKind.ACQUIRED
                result_handle = decision.handle
            elif operation_name == "takeover":
                decision = acquire_inbound_claim(db, key, now=now + timedelta(seconds=2))
                assert decision.kind is ClaimDecisionKind.ACQUIRED
                result_handle = decision.handle
            elif operation_name == "renew":
                assert renew_inbound_claim(
                    db,
                    original_handle,
                    now=now + timedelta(seconds=2),
                    lease_seconds=60,
                )
            elif operation_name == "complete":
                assert complete_inbound_claim(
                    db,
                    original_handle,
                    _response(),
                    now=now + timedelta(seconds=2),
                )
            else:
                assert fail_inbound_claim(
                    db,
                    original_handle,
                    "commit ambiguous failure",
                    now=now + timedelta(seconds=2),
                )

            assert commit_state["injected"] == 1
            assert not db.in_transaction()

        with Session() as observer:
            row = observer.scalar(select(InboundMessageClaim).where(
                InboundMessageClaim.message_id == key.message_id
            ))
            assert row is not None
            if operation_name == "acquire":
                assert row.attempt_count == 1
                assert row.owner_token == result_handle.owner_token
            elif operation_name == "takeover":
                assert row.attempt_count == 2
                assert row.owner_token == result_handle.owner_token
                assert row.owner_token != original_handle.owner_token
            elif operation_name == "renew":
                assert row.attempt_count == 1
                assert row.owner_token == original_handle.owner_token
                assert row.lease_expires_at == now + timedelta(seconds=62)
            elif operation_name == "complete":
                assert row.status == "completed"
                assert row.attempt_count == 1
                assert row.completed_at == now + timedelta(seconds=2)
            else:
                assert row.status == "failed"
                assert row.attempt_count == 1
                assert row.error_summary == "commit ambiguous failure"
    finally:
        engine.dispose()


@pytest.mark.parametrize("operation_name", ["acquire", "complete"])
def test_claim_retry_stops_when_precommit_locked_rollback_fails(
    tmp_path,
    monkeypatch,
    operation_name,
):
    from core.database import InboundMessageClaim
    from core.inbound_idempotency import acquire_inbound_claim, complete_inbound_claim

    monkeypatch.setenv("SQLITE_LOCK_RETRY_ATTEMPTS", "4")
    monkeypatch.setenv("SQLITE_LOCK_RETRY_BASE_DELAY_SECONDS", "0")
    db_path = tmp_path / f"rollback-failure-{operation_name}.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 0.05},
    )
    now = datetime(2026, 7, 10, 8, 0, 0)
    key = _key(f"rollback-failure-{operation_name}")

    try:
        install_base_schema(engine)
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as db:
            handle = None
            if operation_name == "complete":
                handle = acquire_inbound_claim(db, key, now=now).handle

            state, locked_error, rollback_error = (
                _inject_precommit_locked_and_failed_first_rollback(db, monkeypatch)
            )
            with pytest.raises(OperationalError) as caught:
                if operation_name == "acquire":
                    acquire_inbound_claim(db, key, now=now)
                else:
                    complete_inbound_claim(
                        db,
                        handle,
                        _response(),
                        now=now + timedelta(seconds=1),
                    )

            assert caught.value is locked_error
            assert caught.value.__cause__ is rollback_error
            assert state == {"commit_calls": 1, "rollback_calls": 2}
            assert not db.in_transaction()

        with Session() as observer:
            row = observer.scalar(select(InboundMessageClaim).where(
                InboundMessageClaim.message_id == key.message_id
            ))
            if operation_name == "acquire":
                assert row is None
            else:
                assert row is not None
                assert row.status == "processing"
                assert row.response_json == ""
                assert row.completed_at is None
    finally:
        engine.dispose()


def test_blank_message_id_bypasses_before_other_validation_and_touches_no_session():
    from core.inbound_idempotency import (
        ClaimDecisionKind,
        acquire_inbound_claim,
        normalize_inbound_claim_key,
    )

    for message_id in (None, "", " \t\r\n "):
        key = normalize_inbound_claim_key(None, "invalid", None, message_id)
        decision = acquire_inbound_claim(object(), key)
        assert decision.kind is ClaimDecisionKind.BYPASS
        assert decision.handle is None
        assert decision.response is None


@pytest.mark.parametrize(
    ("platform", "chat_type", "session_id", "message_id"),
    [
        ("", "private", "s", "m"),
        ("1qq", "private", "s", "m"),
        ("q.q", "private", "s", "m"),
        ("a" * 33, "private", "s", "m"),
        ("qq", "channel", "s", "m"),
        ("qq", "private", " ", "m"),
        ("qq", "private", "s" * 256, "m"),
        ("qq", "private", "s", "m" * 256),
    ],
)
def test_normalize_inbound_claim_key_rejects_invalid_identity(
    platform,
    chat_type,
    session_id,
    message_id,
):
    from core.inbound_idempotency import normalize_inbound_claim_key

    with pytest.raises(ValueError):
        normalize_inbound_claim_key(platform, chat_type, session_id, message_id)


def test_normalize_inbound_claim_key_canonicalizes_only_claim_identity_fields():
    from core.inbound_idempotency import InboundClaimKey, normalize_inbound_claim_key

    assert normalize_inbound_claim_key(" QQ-Bot_2 ", " GROUP ", " group_42 ", " msg-7 ") == (
        InboundClaimKey(
            platform="qq-bot_2",
            chat_type="group",
            session_id="group_42",
            message_id="msg-7",
        )
    )


def test_unexpired_processing_claim_returns_inflight_without_owner_token():
    from core.database import InboundMessageClaim
    from core.inbound_idempotency import ClaimDecisionKind, acquire_inbound_claim

    _, db = _memory_session()
    now = datetime(2026, 7, 10, 8, 0, 0)
    first = acquire_inbound_claim(db, _key(), now=now)
    duplicate = acquire_inbound_claim(db, _key(), now=now + timedelta(seconds=899))

    assert first.kind is ClaimDecisionKind.ACQUIRED
    assert duplicate.kind is ClaimDecisionKind.DUPLICATE_INFLIGHT
    assert duplicate.handle is None
    assert duplicate.response is None
    row = db.scalar(select(InboundMessageClaim))
    assert row.owner_token == first.handle.owner_token
    assert row.attempt_count == 1
    db.rollback()
    assert not db.in_transaction()


@pytest.mark.parametrize("seed_mode", ["failed", "expired", "null_lease"])
def test_failed_expired_and_null_lease_claims_are_taken_over_atomically(seed_mode):
    from core.database import InboundMessageClaim
    from core.inbound_idempotency import ClaimDecisionKind, acquire_inbound_claim

    _, db = _memory_session()
    now = datetime(2026, 7, 10, 8, 0, 0)
    first = acquire_inbound_claim(db, _key(), now=now, lease_seconds=30)
    row = db.scalar(select(InboundMessageClaim))
    if seed_mode == "failed":
        row.status = "failed"
        row.lease_expires_at = now + timedelta(hours=1)
    elif seed_mode == "expired":
        row.lease_expires_at = now - timedelta(seconds=1)
    else:
        row.lease_expires_at = None
    row.response_json = "old terminal payload"
    row.error_summary = "old error"
    row.completed_at = now
    db.commit()

    second = acquire_inbound_claim(db, _key(), now=now)

    assert second.kind is ClaimDecisionKind.ACQUIRED
    assert second.handle.owner_token != first.handle.owner_token
    assert second.handle.attempt_count == 2
    row = db.scalar(select(InboundMessageClaim))
    assert row.status == "processing"
    assert row.response_json == ""
    assert row.error_summary == ""
    assert row.completed_at is None
    assert row.attempt_count == 2
    db.rollback()
    assert not db.in_transaction()


def test_completed_claim_replays_typed_unicode_html_and_defensively_copied_meta():
    from core.inbound_idempotency import (
        ClaimDecisionKind,
        CompletedInboundResponse,
        GroupReplayFields,
        acquire_inbound_claim,
        complete_inbound_claim,
    )

    _, db = _memory_session()
    now = datetime(2026, 7, 10, 8, 0, 0)
    meta = {
        "send_mode": "quote",
        "reply_to_message_id": "source-1",
        "mentions": [{"id": "42"}],
        "quote": "<p>你好</p>",
        "at_sender": True,
    }
    diagnostics = {"timing_action": "continue", "agent_result": "safe"}
    duplicate_reply = {
        "previous_log_id": 88,
        "similarity": 0.975,
        "previous_created_at": "2026-07-10T07:59:00",
    }
    response = CompletedInboundResponse(
        outcome="respond",
        reply="<b>你好，世界</b>",
        reply_meta=meta,
        reason="已完成",
        source="bridge",
        intent="answer",
        guardrail_status="safe",
        unprocessed_logs=2,
        group=GroupReplayFields(
            generation=4,
            delay_seconds=None,
            diagnostics=diagnostics,
            duplicate_reply=duplicate_reply,
            hard_rule="directed",
        ),
    )
    meta["mentions"].append({"id": "external-change"})
    diagnostics["timing_action"] = "wait"
    duplicate_reply["previous_log_id"] = 999
    acquired = acquire_inbound_claim(db, _key(), now=now)

    assert complete_inbound_claim(db, acquired.handle, response, now=now + timedelta(seconds=2))
    replay = acquire_inbound_claim(db, _key(), now=now + timedelta(days=1))

    assert replay.kind is ClaimDecisionKind.REPLAY
    assert replay.handle is None
    assert replay.response == response
    assert replay.response.reply == "<b>你好，世界</b>"
    assert replay.response.reply_meta["mentions"] == [{"id": "42"}]
    assert replay.response.group.diagnostics == {
        "timing_action": "continue",
        "agent_result": "safe",
    }
    assert replay.response.group.duplicate_reply == {
        "previous_log_id": 88,
        "similarity": 0.975,
        "previous_created_at": "2026-07-10T07:59:00",
    }
    assert not db.in_transaction()


def test_owner_fencing_blocks_stale_worker_after_takeover_and_new_owner_completes():
    from core.inbound_idempotency import (
        ClaimDecisionKind,
        acquire_inbound_claim,
        complete_inbound_claim,
        fail_inbound_claim,
        renew_inbound_claim,
    )

    _, db = _memory_session()
    now = datetime(2026, 7, 10, 8, 0, 0)
    owner_a = acquire_inbound_claim(db, _key(), now=now, lease_seconds=10).handle
    owner_b = acquire_inbound_claim(db, _key(), now=now + timedelta(seconds=11)).handle
    assert owner_b.owner_token != owner_a.owner_token

    assert renew_inbound_claim(db, owner_a, now=now + timedelta(seconds=12)) is False
    assert not db.in_transaction()
    assert complete_inbound_claim(db, owner_a, _response(), now=now + timedelta(seconds=12)) is False
    assert not db.in_transaction()
    assert fail_inbound_claim(db, owner_a, "stale", now=now + timedelta(seconds=12)) is False
    assert not db.in_transaction()
    assert complete_inbound_claim(db, owner_b, _response(), now=now + timedelta(seconds=13)) is True
    assert not db.in_transaction()
    replay = acquire_inbound_claim(db, _key(), now=now + timedelta(days=1))
    assert replay.kind is ClaimDecisionKind.REPLAY


def test_file_sqlite_stale_owner_fencing_across_three_connections(tmp_path):
    from core.database import InboundMessageClaim
    from core.inbound_idempotency import (
        acquire_inbound_claim,
        complete_inbound_claim,
        fail_inbound_claim,
        renew_inbound_claim,
    )

    db_path = tmp_path / "claim-cross-connection-fencing.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 0.05},
    )
    install_base_schema(engine)
    Session = sessionmaker(expire_on_commit=False)
    connection_a = engine.connect()
    connection_b = engine.connect()
    connection_c = engine.connect()
    db_a = Session(bind=connection_a)
    db_b = Session(bind=connection_b)
    db_c = Session(bind=connection_c)
    now = datetime(2026, 7, 10, 8, 0, 0)
    key = _key("cross-connection-fencing")

    try:
        dbapi_identities = {
            id(connection_a.connection.dbapi_connection),
            id(connection_b.connection.dbapi_connection),
            id(connection_c.connection.dbapi_connection),
        }
        assert len(dbapi_identities) == 3

        owner_a = acquire_inbound_claim(db_a, key, now=now, lease_seconds=1).handle
        owner_b = acquire_inbound_claim(
            db_b,
            key,
            now=now + timedelta(seconds=2),
        ).handle
        assert owner_b.owner_token != owner_a.owner_token
        assert owner_b.attempt_count == 2

        assert renew_inbound_claim(db_a, owner_a, now=now + timedelta(seconds=3)) is False
        assert complete_inbound_claim(
            db_a,
            owner_a,
            _response(),
            now=now + timedelta(seconds=3),
        ) is False
        assert fail_inbound_claim(
            db_a,
            owner_a,
            "stale owner",
            now=now + timedelta(seconds=3),
        ) is False
        assert not db_a.in_transaction()

        assert complete_inbound_claim(
            db_b,
            owner_b,
            _response(),
            now=now + timedelta(seconds=4),
        )
        assert not db_b.in_transaction()

        row = db_c.scalar(select(InboundMessageClaim).where(
            InboundMessageClaim.message_id == key.message_id
        ))
        assert row.status == "completed"
        assert row.owner_token == owner_b.owner_token
        assert row.attempt_count == 2
        assert row.completed_at == now + timedelta(seconds=4)
    finally:
        db_a.close()
        db_b.close()
        db_c.close()
        connection_a.close()
        connection_b.close()
        connection_c.close()
        engine.dispose()


def test_renew_extends_even_an_expired_lease_when_not_taken_over():
    from core.database import InboundMessageClaim
    from core.inbound_idempotency import acquire_inbound_claim, renew_inbound_claim

    _, db = _memory_session()
    now = datetime(2026, 7, 10, 8, 0, 0)
    handle = acquire_inbound_claim(db, _key(), now=now, lease_seconds=5).handle

    assert renew_inbound_claim(
        db,
        handle,
        now=now + timedelta(seconds=20),
        lease_seconds=60,
    ) is True
    assert not db.in_transaction()
    row = db.scalar(select(InboundMessageClaim))
    assert row.lease_expires_at == now + timedelta(seconds=80)
    db.rollback()


def test_complete_is_idempotent_only_for_same_owner_and_same_response():
    from core.database import InboundMessageClaim
    from core.inbound_idempotency import acquire_inbound_claim, complete_inbound_claim

    _, db = _memory_session()
    now = datetime(2026, 7, 10, 8, 0, 0)
    handle = acquire_inbound_claim(db, _key(), now=now).handle
    response = _response()

    assert complete_inbound_claim(db, handle, response, now=now + timedelta(seconds=1)) is True
    assert complete_inbound_claim(db, handle, response, now=now + timedelta(seconds=2)) is True
    assert complete_inbound_claim(
        db,
        handle,
        _response(reply="different"),
        now=now + timedelta(seconds=3),
    ) is False
    row = db.scalar(select(InboundMessageClaim))
    assert row.status == "completed"
    assert row.lease_expires_at is None
    assert row.error_summary == ""
    assert row.completed_at == now + timedelta(seconds=1)
    db.rollback()


def test_fail_is_idempotent_only_for_same_owner_and_same_single_line_summary():
    from core.database import InboundMessageClaim
    from core.inbound_idempotency import acquire_inbound_claim, fail_inbound_claim

    _, db = _memory_session()
    now = datetime(2026, 7, 10, 8, 0, 0)
    handle = acquire_inbound_claim(db, _key(), now=now).handle
    error = "line 1\r\nline 2\n" + ("长" * 600)

    assert fail_inbound_claim(db, handle, error, now=now + timedelta(seconds=1)) is True
    assert fail_inbound_claim(db, handle, error, now=now + timedelta(seconds=2)) is True
    assert fail_inbound_claim(db, handle, "different", now=now + timedelta(seconds=3)) is False
    row = db.scalar(select(InboundMessageClaim))
    assert row.status == "failed"
    assert row.lease_expires_at is None
    assert row.response_json == ""
    assert row.completed_at is None
    assert "\n" not in row.error_summary
    assert "\r" not in row.error_summary
    assert len(row.error_summary) == 500
    db.rollback()


def test_completed_response_codec_round_trip_is_strict_and_deterministic():
    from core.inbound_idempotency import (
        CompletedInboundResponse,
        decode_completed_inbound_response,
        encode_completed_inbound_response,
    )

    response = _response()
    encoded = encode_completed_inbound_response(response)

    assert encode_completed_inbound_response(response) == encoded
    assert "你好" in encoded
    assert "<b>世界</b>" in encoded
    assert ": " not in encoded
    assert decode_completed_inbound_response(encoded) == response
    assert isinstance(decode_completed_inbound_response(encoded), CompletedInboundResponse)


def test_completed_response_guardrail_none_round_trips_and_other_types_are_rejected():
    from core.inbound_idempotency import (
        CompletedInboundResponse,
        decode_completed_inbound_response,
        encode_completed_inbound_response,
    )

    response = CompletedInboundResponse(outcome="silent")
    assert response.guardrail_status is None
    assert decode_completed_inbound_response(
        encode_completed_inbound_response(response)
    ).guardrail_status is None

    with pytest.raises((TypeError, ValueError)):
        CompletedInboundResponse(outcome="silent", guardrail_status=7)


def test_completed_response_reply_meta_uses_transport_safe_allowlist():
    from core.inbound_idempotency import CompletedInboundResponse, encode_completed_inbound_response

    response = CompletedInboundResponse(
        outcome="respond",
        reply_meta={
            "send_mode": "quote",
            "reply_to_message_id": "m-source",
            "mentions": [{"id": "42"}],
            "quote": "引用",
            "at_sender": True,
            "request_id": "request-secret",
            "user_id": "user-secret",
            "session_id": "session-secret",
            "group_id": "group-secret",
            "sender_id": "sender-secret",
            "answer": "transport-payload",
        },
    )

    body = json.loads(encode_completed_inbound_response(response))
    assert body["reply_meta"] == {
        "send_mode": "quote",
        "reply_to_message_id": "m-source",
        "mentions": [{"id": "42"}],
        "quote": "引用",
        "at_sender": True,
    }
    for forbidden in (
        "request_id",
        "user_id",
        "session_id",
        "group_id",
        "sender_id",
        "answer",
    ):
        assert forbidden not in body["reply_meta"]


def test_encode_revalidates_mutated_internal_reply_meta_mapping():
    from core.inbound_idempotency import CompletedInboundResponse, encode_completed_inbound_response

    response = CompletedInboundResponse(
        outcome="respond",
        reply_meta={"send_mode": "quote"},
    )
    response.reply_meta["request_id"] = "late-injected-request"

    body = json.loads(encode_completed_inbound_response(response))

    assert body["reply_meta"] == {"send_mode": "quote"}
    assert "late-injected-request" not in body


def test_encode_revalidates_mutated_internal_group_diagnostics_mapping():
    from core.inbound_idempotency import (
        CompletedInboundResponse,
        GroupReplayFields,
        encode_completed_inbound_response,
    )

    response = CompletedInboundResponse(
        outcome="no_reply",
        group=GroupReplayFields(diagnostics={"timing_action": "continue"}),
    )
    response.group.diagnostics["request_id"] = "late-injected-request"

    with pytest.raises((TypeError, ValueError)):
        encode_completed_inbound_response(response)


def test_encode_revalidates_mutated_internal_duplicate_reply_mapping():
    from core.inbound_idempotency import (
        CompletedInboundResponse,
        GroupReplayFields,
        encode_completed_inbound_response,
    )

    response = CompletedInboundResponse(
        outcome="no_reply",
        group=GroupReplayFields(duplicate_reply={"similarity": 0.9}),
    )
    response.group.duplicate_reply["similarity"] = True

    with pytest.raises((TypeError, ValueError)):
        encode_completed_inbound_response(response)


@pytest.mark.parametrize(
    "mutated_reply_meta",
    [
        {"send_mode": "quote", "request_id": "stored-request"},
        {"send_mode": "quote", "status": "ok"},
        {"send_mode": "quote", "arbitrary": "stored-extra"},
        {"send_mode": "quote", "quote": None},
    ],
)
def test_decode_rejects_persisted_reply_meta_that_sanitizer_would_change(
    mutated_reply_meta,
):
    from core.inbound_idempotency import (
        CorruptClaimResponse,
        decode_completed_inbound_response,
        encode_completed_inbound_response,
    )

    body = json.loads(encode_completed_inbound_response(_response()))
    body["reply_meta"] = mutated_reply_meta

    with pytest.raises(CorruptClaimResponse):
        decode_completed_inbound_response(
            json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        )


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "request_id",
        "user_id",
        "session_id",
        "group_id",
        "sender_id",
        "status",
        "action",
        "messages",
        "answer",
        "data",
    ],
)
@pytest.mark.parametrize("container_name", ["diagnostics", "duplicate_reply"])
def test_group_replay_mappings_reject_identity_and_transport_payload_fields(
    forbidden_field,
    container_name,
):
    from core.inbound_idempotency import GroupReplayFields

    kwargs = {container_name: {forbidden_field: "forbidden"}}
    with pytest.raises((TypeError, ValueError)):
        GroupReplayFields(**kwargs)


@pytest.mark.parametrize(
    "duplicate_reply",
    [
        True,
        [],
        {"previous_log_id": True},
        {"similarity": True},
        {"similarity": math.nan},
        {"similarity": math.inf},
        {"previous_created_at": 123},
    ],
)
def test_group_replay_duplicate_reply_rejects_non_mapping_and_invalid_field_types(
    duplicate_reply,
):
    from core.inbound_idempotency import GroupReplayFields

    with pytest.raises((TypeError, ValueError)):
        GroupReplayFields(duplicate_reply=duplicate_reply)


@pytest.mark.parametrize(
    "diagnostics",
    [
        {"timing_action": 1},
        {"agent_result": None},
    ],
)
def test_group_replay_diagnostics_require_string_values(diagnostics):
    from core.inbound_idempotency import GroupReplayFields

    with pytest.raises((TypeError, ValueError)):
        GroupReplayFields(diagnostics=diagnostics)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body.update(schema_version=2),
        lambda body: body.update(outcome="retry"),
        lambda body: body.update(extra="forbidden"),
        lambda body: body.update(reply=7),
        lambda body: body.update(reply_meta=[]),
        lambda body: body.update(unprocessed_logs=True),
        lambda body: body.update(group={**body["group"], "extra": 1}),
        lambda body: body.update(group={**body["group"], "generation": True}),
        lambda body: body.update(group={**body["group"], "diagnostics": []}),
        lambda body: body.update(group={**body["group"], "duplicate_reply": True}),
        lambda body: body.update(guardrail_status=7),
    ],
)
def test_completed_response_decode_rejects_unknown_or_wrongly_typed_fields(mutate):
    from core.inbound_idempotency import (
        CorruptClaimResponse,
        decode_completed_inbound_response,
        encode_completed_inbound_response,
    )

    body = json.loads(encode_completed_inbound_response(_response()))
    mutate(body)
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    with pytest.raises(CorruptClaimResponse):
        decode_completed_inbound_response(payload)


@pytest.mark.parametrize("payload", ["[]", "null", "not-json"])
def test_completed_response_decode_rejects_non_object_or_invalid_json(payload):
    from core.inbound_idempotency import CorruptClaimResponse, decode_completed_inbound_response

    with pytest.raises(CorruptClaimResponse):
        decode_completed_inbound_response(payload)


def test_completed_response_codec_rejects_nan_infinity_datetime_set_and_non_mapping():
    from core.inbound_idempotency import (
        CompletedInboundResponse,
        CorruptClaimResponse,
        GroupReplayFields,
        decode_completed_inbound_response,
        encode_completed_inbound_response,
    )

    with pytest.raises((TypeError, ValueError)):
        CompletedInboundResponse(outcome="respond", reply_meta=[])
    with pytest.raises((TypeError, ValueError)):
        GroupReplayFields(diagnostics=[])
    with pytest.raises((TypeError, ValueError)):
        GroupReplayFields(duplicate_reply=[])
    for invalid in (math.nan, math.inf, -math.inf, datetime.now(), {"set"}):
        with pytest.raises((TypeError, ValueError)):
            encode_completed_inbound_response(
                CompletedInboundResponse(outcome="respond", reply_meta={"quote": invalid})
            )

    valid = encode_completed_inbound_response(_response())
    nan_payload = valid.replace('"similarity":0.9876', '"similarity":NaN')
    with pytest.raises(CorruptClaimResponse):
        decode_completed_inbound_response(nan_payload)


def test_corrupt_completed_payload_fails_closed_without_changing_claim_state():
    from core.database import InboundMessageClaim
    from core.inbound_idempotency import CorruptClaimResponse, acquire_inbound_claim

    _, db = _memory_session()
    now = datetime(2026, 7, 10, 8, 0, 0)
    handle = acquire_inbound_claim(db, _key(), now=now).handle
    row = db.scalar(select(InboundMessageClaim))
    row.status = "completed"
    row.response_json = '{"schema_version":99}'
    row.completed_at = now + timedelta(seconds=1)
    row.lease_expires_at = None
    db.commit()

    with pytest.raises(CorruptClaimResponse):
        acquire_inbound_claim(db, _key(), now=now + timedelta(days=1))
    assert not db.in_transaction()
    row = db.scalar(select(InboundMessageClaim))
    assert row.status == "completed"
    assert row.owner_token == handle.owner_token
    assert row.response_json == '{"schema_version":99}'
    assert row.attempt_count == 1
    db.rollback()


def test_aware_and_naive_times_are_stored_as_utc_naive():
    from core.database import InboundMessageClaim
    from core.inbound_idempotency import acquire_inbound_claim

    _, db = _memory_session()
    aware = datetime(2026, 7, 10, 16, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    first = acquire_inbound_claim(db, _key("aware"), now=aware, lease_seconds=30)
    naive = datetime(2026, 7, 10, 9, 0, 0)
    second = acquire_inbound_claim(db, _key("naive"), now=naive, lease_seconds=30)

    assert first.handle.lease_expires_at == datetime(2026, 7, 10, 8, 0, 30)
    assert first.handle.lease_expires_at.tzinfo is None
    assert second.handle.lease_expires_at == datetime(2026, 7, 10, 9, 0, 30)
    rows = db.scalars(select(InboundMessageClaim).order_by(InboundMessageClaim.message_id)).all()
    assert all(row.lease_expires_at.tzinfo is None for row in rows)
    db.rollback()


def test_all_db_operations_reject_dirty_session_without_committing_business_objects():
    from core.database import User
    from core.inbound_idempotency import (
        DirtyClaimSessionError,
        acquire_inbound_claim,
        complete_inbound_claim,
        fail_inbound_claim,
        renew_inbound_claim,
    )

    engine = create_engine("sqlite:///:memory:")
    install_base_schema(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    pending = User(id="pending-acquire")
    db.add(pending)
    with pytest.raises(DirtyClaimSessionError):
        acquire_inbound_claim(db, _key())
    assert pending in db.new
    with Session() as observer:
        assert observer.get(User, "pending-acquire") is None
    db.rollback()

    handle = acquire_inbound_claim(db, _key()).handle
    operations = [
        lambda: renew_inbound_claim(db, handle),
        lambda: complete_inbound_claim(db, handle, _response()),
        lambda: fail_inbound_claim(db, handle, "boom"),
    ]
    for index, operation in enumerate(operations):
        user = User(id=f"pending-{index}")
        db.add(user)
        with pytest.raises(DirtyClaimSessionError):
            operation()
        assert user in db.new
        with Session() as observer:
            assert observer.get(User, user.id) is None
        db.rollback()


@pytest.mark.parametrize("operation_name", ["acquire", "renew", "complete", "fail"])
def test_public_db_operations_reject_existing_clean_read_transaction(operation_name):
    from core.database import User
    from core.inbound_idempotency import (
        DirtyClaimSessionError,
        acquire_inbound_claim,
        complete_inbound_claim,
        fail_inbound_claim,
        renew_inbound_claim,
    )

    _, db = _memory_session()
    handle = acquire_inbound_claim(db, _key(), now=datetime(2026, 7, 10, 8, 0, 0)).handle
    db.execute(select(User)).all()
    assert db.in_transaction()

    with pytest.raises(DirtyClaimSessionError):
        if operation_name == "acquire":
            acquire_inbound_claim(db, _key("existing-read-transaction"))
        elif operation_name == "renew":
            renew_inbound_claim(db, handle)
        elif operation_name == "complete":
            complete_inbound_claim(db, handle, _response())
        else:
            fail_inbound_claim(db, handle, "must-not-run")

    assert db.in_transaction()
    db.rollback()
    assert not db.in_transaction()


@pytest.mark.parametrize("write_mode", ["flush", "core_dml"])
def test_claim_rejects_flushed_or_core_dml_transaction_before_any_sql(
    tmp_path,
    write_mode,
):
    from core.database import User
    from core.inbound_idempotency import DirtyClaimSessionError, acquire_inbound_claim

    db_path = tmp_path / f"dirty-{write_mode}.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    try:
        install_base_schema(engine)
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as writer:
            user_id = f"pending-{write_mode}"
            if write_mode == "flush":
                writer.add(User(id=user_id))
                writer.flush()
            else:
                writer.execute(insert(User).values(id=user_id))

            assert writer.in_transaction()
            assert not writer.new
            assert not writer.dirty
            assert not writer.deleted
            with Session() as observer:
                assert observer.get(User, user_id) is None

            service_sql: list[str] = []

            def record_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
                service_sql.append(statement)

            event.listen(engine, "before_cursor_execute", record_sql)
            try:
                with pytest.raises(DirtyClaimSessionError):
                    acquire_inbound_claim(writer, _key(f"dirty-{write_mode}"))
            finally:
                event.remove(engine, "before_cursor_execute", record_sql)

            assert service_sql == []
            assert writer.in_transaction()
            with Session() as observer:
                assert observer.get(User, user_id) is None
            writer.rollback()
            assert not writer.in_transaction()
    finally:
        engine.dispose()


def test_keyboard_interrupt_after_claim_dml_rolls_back_and_releases_file_lock(
    tmp_path,
    monkeypatch,
):
    from core.database import User
    from core.inbound_idempotency import acquire_inbound_claim

    db_path = tmp_path / "claim-keyboard-interrupt.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 0.05},
    )
    observer_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 0.05},
    )
    install_base_schema(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    claim_dml: list[str] = []
    commit_calls = 0

    def record_claim_dml(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("INSERT INTO INBOUND_MESSAGE_CLAIMS"):
            claim_dml.append(statement)

    def interrupt_commit():
        nonlocal commit_calls
        commit_calls += 1
        raise KeyboardInterrupt("claim commit interrupted")

    event.listen(engine, "after_cursor_execute", record_claim_dml)
    monkeypatch.setattr(db, "commit", interrupt_commit)
    try:
        with pytest.raises(KeyboardInterrupt, match="claim commit interrupted") as caught:
            acquire_inbound_claim(
                db,
                _key("keyboard-interrupt"),
                now=datetime(2026, 7, 10, 8, 0, 0),
            )

        assert str(caught.value) == "claim commit interrupted"
        assert commit_calls == 1
        assert len(claim_dml) == 1
        assert not db.in_transaction()

        with observer_engine.begin() as conn:
            conn.execute(insert(User).values(id="second-writer-after-interrupt"))
            claim_count = conn.execute(text(
                "SELECT COUNT(*) FROM inbound_message_claims "
                "WHERE message_id = 'keyboard-interrupt'"
            )).scalar_one()
        assert claim_count == 0
    finally:
        event.remove(engine, "after_cursor_execute", record_claim_dml)
        db.close()
        engine.dispose()
        observer_engine.dispose()


def test_retry_rollback_failure_preserves_original_base_exception_with_note():
    from core.inbound_idempotency import _run_retry

    original = KeyboardInterrupt("original interrupt")
    rollback_error = RuntimeError("rollback failed")

    class RollbackFails:
        rollback_calls = 0

        def rollback(self):
            self.rollback_calls += 1
            raise rollback_error

    db = RollbackFails()

    def interrupt_operation():
        raise original

    with pytest.raises(KeyboardInterrupt) as caught:
        _run_retry(db, interrupt_operation, label="base exception rollback")

    assert caught.value is original
    assert db.rollback_calls == 1
    if callable(getattr(original, "add_note", None)):
        assert any("rollback failed" in note for note in getattr(original, "__notes__", ()))
    else:
        assert original.__cause__ is rollback_error or original.__context__ is rollback_error


@pytest.mark.parametrize("add_note_mode", ["missing", "raises"])
def test_retry_rollback_failure_uses_exception_chain_when_note_is_unavailable(
    add_note_mode,
):
    from core.inbound_idempotency import _run_retry

    rollback_error = RuntimeError("rollback failed visibly")

    if add_note_mode == "missing":
        class OriginalInterrupt(BaseException):
            add_note = None
    else:
        class OriginalInterrupt(BaseException):
            def add_note(self, _note):
                raise RuntimeError("add_note unavailable")

    original = OriginalInterrupt("original interrupt")

    class RollbackFails:
        def rollback(self):
            raise rollback_error

    def interrupt_operation():
        raise original

    with pytest.raises(OriginalInterrupt) as caught:
        _run_retry(RollbackFails(), interrupt_operation, label="fallback exception chain")

    assert caught.value is original
    assert caught.value.__cause__ is rollback_error or caught.value.__context__ is rollback_error


@pytest.mark.parametrize("operation", ["acquire", "renew", "complete", "fail"])
def test_claim_parameter_error_survives_rollback_error(operation):
    from core import inbound_idempotency as claims

    primary = TypeError(f"{operation} parameter error")
    rollback_error = RuntimeError(f"{operation} rollback error")

    class RollbackFailingSession:
        new = ()
        dirty = ()
        deleted = ()

        @staticmethod
        def in_transaction():
            return False

        @staticmethod
        def rollback():
            raise rollback_error

    db = RollbackFailingSession()
    invalid_calls = {
        "acquire": lambda: claims.acquire_inbound_claim(db, object()),
        "renew": lambda: claims.renew_inbound_claim(db, object()),
        "complete": lambda: claims.complete_inbound_claim(
            db,
            object(),
            object(),
        ),
        "fail": lambda: claims.fail_inbound_claim(db, object(), primary),
    }

    with pytest.raises((TypeError, ValueError)) as raised:
        invalid_calls[operation]()

    assert raised.value is not rollback_error
    chained = {raised.value.__cause__, raised.value.__context__}
    notes = getattr(raised.value, "__notes__", [])
    assert rollback_error in chained or any(
        "rollback error" in note
        for note in notes
    )


def test_completed_replay_decode_error_survives_rollback_error(monkeypatch):
    from core import inbound_idempotency as claims

    primary = ValueError("completed response corrupt")
    rollback_error = RuntimeError("decode rollback error")

    class ReplaySession:
        new = ()
        dirty = ()
        deleted = ()

        def __init__(self):
            self.execute_calls = 0
            self.rollback_calls = 0

        @staticmethod
        def in_transaction():
            return False

        def execute(self, _statement):
            self.execute_calls += 1
            if self.execute_calls <= 2:
                return SimpleNamespace(rowcount=0)
            return SimpleNamespace(
                one_or_none=lambda: SimpleNamespace(
                    status=claims.ClaimStatus.COMPLETED.value,
                    response_json="corrupt",
                )
            )

        def rollback(self):
            self.rollback_calls += 1
            raise rollback_error

    def fail_decode(_payload):
        raise primary

    db = ReplaySession()
    monkeypatch.setattr(
        claims,
        "decode_completed_inbound_response",
        fail_decode,
    )

    with pytest.raises(ValueError) as raised:
        claims.acquire_inbound_claim(
            db,
            _key("corrupt-replay"),
            now=datetime(2026, 7, 11, 8, 0, 0),
        )

    assert raised.value is primary
    assert db.rollback_calls >= 1
    chained = {primary.__cause__, primary.__context__}
    notes = getattr(primary, "__notes__", [])
    assert rollback_error in chained or any(
        "decode rollback error" in note
        for note in notes
    )


def _run_two_claimers(engine, Session, key, now):
    from core.inbound_idempotency import acquire_inbound_claim

    checkout_barrier = threading.Barrier(2)
    insert_barrier = threading.Barrier(2)
    synchronized_threads: set[int] = set()
    synchronized_threads_lock = threading.Lock()

    def synchronize_first_claim_insert(
        _conn,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        if not statement.lstrip().upper().startswith("INSERT INTO INBOUND_MESSAGE_CLAIMS"):
            return
        thread_id = threading.get_ident()
        with synchronized_threads_lock:
            if thread_id in synchronized_threads:
                return
            synchronized_threads.add(thread_id)
        insert_barrier.wait(timeout=5)

    def worker():
        connection = engine.connect()
        db = Session(bind=connection)
        try:
            dbapi_identity = id(connection.connection.dbapi_connection)
            checkout_barrier.wait(timeout=5)
            return acquire_inbound_claim(db, key, now=now), dbapi_identity
        finally:
            db.close()
            connection.close()

    event.listen(engine, "before_cursor_execute", synchronize_first_claim_insert)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker) for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]
    finally:
        event.remove(engine, "before_cursor_execute", synchronize_first_claim_insert)
    return (
        [item[0] for item in results],
        [item[1] for item in results],
        synchronized_threads,
    )


def test_two_file_sqlite_sessions_first_claim_exactly_once(tmp_path, monkeypatch):
    from core.database import InboundMessageClaim
    from core.inbound_idempotency import ClaimDecisionKind

    monkeypatch.setenv("SQLITE_LOCK_RETRY_ATTEMPTS", "6")
    monkeypatch.setenv("SQLITE_LOCK_RETRY_BASE_DELAY_SECONDS", "0.01")
    db_path = tmp_path / "claim-first.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 0.05},
    )
    install_base_schema(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        decisions, dbapi_identities, synchronized_threads = _run_two_claimers(
            engine,
            Session,
            _key(),
            datetime(2026, 7, 10, 8, 0, 0),
        )

        assert len(set(dbapi_identities)) == 2
        assert len(synchronized_threads) == 2
        assert sorted(item.kind.value for item in decisions) == sorted([
            ClaimDecisionKind.ACQUIRED.value,
            ClaimDecisionKind.DUPLICATE_INFLIGHT.value,
        ])
        acquired = next(item for item in decisions if item.kind is ClaimDecisionKind.ACQUIRED)
        inflight = next(item for item in decisions if item.kind is ClaimDecisionKind.DUPLICATE_INFLIGHT)
        assert inflight.handle is None
        with Session() as db:
            row = db.scalar(select(InboundMessageClaim))
            assert row.attempt_count == 1
            assert row.owner_token == acquired.handle.owner_token
    finally:
        engine.dispose()


def test_two_file_sqlite_sessions_failed_claim_is_reacquired_exactly_once(
    tmp_path,
    monkeypatch,
):
    from core.database import InboundMessageClaim
    from core.inbound_idempotency import ClaimDecisionKind, acquire_inbound_claim, fail_inbound_claim

    monkeypatch.setenv("SQLITE_LOCK_RETRY_ATTEMPTS", "6")
    monkeypatch.setenv("SQLITE_LOCK_RETRY_BASE_DELAY_SECONDS", "0.01")
    db_path = tmp_path / "claim-failed.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 0.05},
    )
    install_base_schema(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime(2026, 7, 10, 8, 0, 0)
    try:
        with Session() as db:
            handle = acquire_inbound_claim(db, _key(), now=now).handle
            assert fail_inbound_claim(db, handle, "retryable", now=now + timedelta(seconds=1))

        decisions, dbapi_identities, synchronized_threads = _run_two_claimers(
            engine,
            Session,
            _key(),
            now + timedelta(seconds=2),
        )

        assert len(set(dbapi_identities)) == 2
        assert len(synchronized_threads) == 2
        assert sorted(item.kind.value for item in decisions) == sorted([
            ClaimDecisionKind.ACQUIRED.value,
            ClaimDecisionKind.DUPLICATE_INFLIGHT.value,
        ])
        acquired = next(item for item in decisions if item.kind is ClaimDecisionKind.ACQUIRED)
        with Session() as db:
            row = db.scalar(select(InboundMessageClaim))
            assert row.attempt_count == 2
            assert row.owner_token == acquired.handle.owner_token
    finally:
        engine.dispose()


class _LifecycleSession:
    def __init__(self, label: str):
        self.label = label
        self.close_calls = 0

    @property
    def closed(self) -> bool:
        return self.close_calls > 0

    def close(self) -> None:
        self.close_calls += 1


class _CloseFailingLifecycleSession(_LifecycleSession):
    def __init__(self, label: str, close_error: BaseException):
        super().__init__(label)
        self.close_error = close_error

    def close(self) -> None:
        self.close_calls += 1
        raise self.close_error


class _RecordingLifecycleLogger:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def exception(self, message: str, *_args, **_kwargs) -> None:
        self.calls.append(("exception", message))

    def error(self, message: str, *_args, **_kwargs) -> None:
        self.calls.append(("error", message))

    def warning(self, message: str, *_args, **_kwargs) -> None:
        self.calls.append(("warning", message))


class _ExplodingLifecycleLogger(_RecordingLifecycleLogger):
    def __init__(self, logger_error: BaseException):
        super().__init__()
        self.logger_error = logger_error

    def exception(self, message: str, *_args, **_kwargs) -> None:
        super().exception(message)
        raise self.logger_error

    def error(self, message: str, *_args, **_kwargs) -> None:
        super().error(message)
        raise self.logger_error

    def warning(self, message: str, *_args, **_kwargs) -> None:
        super().warning(message)
        raise self.logger_error


class _ControlledClaimSleep:
    def __init__(self):
        self.calls: list[float] = []
        self._waiters: list[asyncio.Event] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        waiter = asyncio.Event()
        self._waiters.append(waiter)
        await waiter.wait()

    async def wait_for_calls(self, count: int) -> None:
        async def wait_until_ready() -> None:
            while len(self.calls) < count:
                await asyncio.sleep(0)

        await asyncio.wait_for(wait_until_ready(), timeout=1)

    def release(self, index: int) -> None:
        self._waiters[index].set()


class _DelayedCancellationSleep:
    def __init__(self):
        self.started = asyncio.Event()
        self.cancel_started = asyncio.Event()
        self.release_cancel = asyncio.Event()
        self._waiter = asyncio.Event()

    async def __call__(self, _seconds: float) -> None:
        self.started.set()
        try:
            await self._waiter.wait()
        except asyncio.CancelledError:
            self.cancel_started.set()
            await self.release_cancel.wait()
            raise


async def _wait_for_condition(predicate) -> None:
    async def wait_until_true() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_until_true(), timeout=1)


def _lifecycle_handle(message_id: str = "owner-lifecycle"):
    from core.inbound_idempotency import InboundClaimHandle

    return InboundClaimHandle(
        key=_key(message_id),
        owner_token="owner-token-1",
        lease_expires_at=datetime(2026, 7, 10, 8, 15, 0),
        attempt_count=1,
    )


@pytest.mark.asyncio
async def test_claim_owner_checkpoint_creates_uses_and_closes_session_in_worker_thread(
    monkeypatch,
):
    import threading

    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import InboundClaimOwner

    event_loop_thread = threading.get_ident()
    observed: list[tuple[str, int]] = []

    class Session(_LifecycleSession):
        def close(self) -> None:
            observed.append(("close", threading.get_ident()))
            super().close()

    def session_factory():
        observed.append(("create", threading.get_ident()))
        return Session("threaded")

    def renew(*_args, **_kwargs):
        observed.append(("renew", threading.get_ident()))
        return True

    monkeypatch.setattr(lifecycle, "renew_inbound_claim", renew)
    owner = InboundClaimOwner(
        _lifecycle_handle("threaded"),
        session_factory=session_factory,
    )

    assert await owner.checkpoint() is True
    worker_threads = {thread_id for _, thread_id in observed}
    assert len(worker_threads) == 1
    assert event_loop_thread not in worker_threads
    assert [name for name, _ in observed] == ["create", "renew", "close"]


@pytest.mark.asyncio
async def test_claim_owner_blocking_checkpoint_does_not_block_event_loop(monkeypatch):
    import threading

    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import InboundClaimOwner

    entered = threading.Event()
    release = threading.Event()
    heartbeat = asyncio.Event()

    def renew(*_args, **_kwargs):
        entered.set()
        assert release.wait(timeout=2)
        return True

    monkeypatch.setattr(lifecycle, "renew_inbound_claim", renew)
    owner = InboundClaimOwner(
        _lifecycle_handle("heartbeat"),
        session_factory=lambda: _LifecycleSession("heartbeat"),
    )
    task = asyncio.create_task(owner.checkpoint())
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        asyncio.get_running_loop().call_soon(heartbeat.set)
        await asyncio.wait_for(heartbeat.wait(), timeout=0.2)
        release.set()
        assert await task is True
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("renew_mode", ["lost", "error"])
@pytest.mark.asyncio
async def test_claim_owner_renew_failure_completes_unusable_signal(
    monkeypatch,
    renew_mode,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import (
        InboundClaimOwner,
        InboundClaimOwnershipLostError,
    )

    error = RuntimeError("renew failed")
    controlled_sleep = _ControlledClaimSleep()

    def renew(*_args, **_kwargs):
        if renew_mode == "error":
            raise error
        return False

    monkeypatch.setattr(lifecycle, "renew_inbound_claim", renew)
    owner = InboundClaimOwner(
        _lifecycle_handle(f"signal-{renew_mode}"),
        session_factory=lambda: _LifecycleSession(renew_mode),
        renew_interval_seconds=1,
        sleep=controlled_sleep,
    )
    renewal_task = await owner.start()
    waiter: asyncio.Task[None] | None = None
    released = False
    try:
        await controlled_sleep.wait_for_calls(1)
        waiter = asyncio.create_task(owner.wait_unusable())
        controlled_sleep.release(0)
        released = True

        expected = (
            RuntimeError
            if renew_mode == "error"
            else InboundClaimOwnershipLostError
        )
        with pytest.raises(expected) as raised:
            await asyncio.wait_for(waiter, timeout=1)
        if renew_mode == "error":
            assert raised.value is error
    finally:
        if not released:
            controlled_sleep.release(0)
        if waiter is not None and not waiter.done():
            waiter.cancel()
        if waiter is not None:
            await asyncio.gather(waiter, return_exceptions=True)
        await owner.pause()
        await asyncio.gather(renewal_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_claim_owner_renew_tick_and_complete_use_distinct_fresh_closed_sessions(
    monkeypatch,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import InboundClaimOwner

    sessions: list[_LifecycleSession] = []
    renew_calls: list[tuple[_LifecycleSession, object, float]] = []
    complete_calls: list[tuple[_LifecycleSession, object, object]] = []
    controlled_sleep = _ControlledClaimSleep()

    def session_factory():
        session = _LifecycleSession(f"session-{len(sessions)}")
        sessions.append(session)
        return session

    def fake_renew(db, handle, *, lease_seconds):
        renew_calls.append((db, handle, lease_seconds))
        return True

    def fake_complete(db, handle, response):
        complete_calls.append((db, handle, response))
        return True

    monkeypatch.setattr(lifecycle, "renew_inbound_claim", fake_renew)
    monkeypatch.setattr(lifecycle, "complete_inbound_claim", fake_complete)
    owner = InboundClaimOwner(
        _lifecycle_handle(),
        session_factory=session_factory,
        lease_seconds=45,
        renew_interval_seconds=5,
        sleep=controlled_sleep,
    )

    await owner.start()
    await controlled_sleep.wait_for_calls(1)
    controlled_sleep.release(0)
    await _wait_for_condition(lambda: len(renew_calls) == 1)
    await controlled_sleep.wait_for_calls(2)

    assert sessions == [renew_calls[0][0]]
    assert sessions[0].closed
    assert renew_calls[0][1:] == (_lifecycle_handle(), 45)

    assert await owner.complete(_response()) is True

    assert len(sessions) == 2
    assert complete_calls == [(sessions[1], _lifecycle_handle(), _response())]
    assert sessions[1] is not sessions[0]
    assert all(session.close_calls == 1 for session in sessions)
    assert owner.renewal_task is not None
    assert owner.renewal_task.done()


@pytest.mark.asyncio
async def test_claim_owner_pause_stops_renewal_and_resume_fences_before_new_task(
    monkeypatch,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import InboundClaimOwner

    sessions: list[_LifecycleSession] = []
    renew_calls: list[tuple[_LifecycleSession, object, float]] = []
    controlled_sleep = _ControlledClaimSleep()

    def session_factory():
        session = _LifecycleSession(f"pause-resume-{len(sessions)}")
        sessions.append(session)
        return session

    def fake_renew(db, handle, *, lease_seconds):
        renew_calls.append((db, handle, lease_seconds))
        return True

    monkeypatch.setattr(lifecycle, "renew_inbound_claim", fake_renew)
    owner = InboundClaimOwner(
        _lifecycle_handle("pause-resume"),
        session_factory=session_factory,
        lease_seconds=30,
        renew_interval_seconds=5,
        sleep=controlled_sleep,
    )

    await owner.start()
    await controlled_sleep.wait_for_calls(1)
    first_task = owner.renewal_task
    assert first_task is not None and not first_task.done()

    assert await owner.pause() is True
    assert first_task.done()
    assert renew_calls == []

    resumed_task = await owner.resume()
    await controlled_sleep.wait_for_calls(2)

    assert resumed_task is owner.renewal_task
    assert resumed_task is not first_task
    assert not resumed_task.done()
    assert len(renew_calls) == 1
    assert renew_calls[0][1:] == (_lifecycle_handle("pause-resume"), 30)
    assert sessions == [renew_calls[0][0]]
    assert sessions[0].closed

    assert await owner.pause() is True
    assert resumed_task.done()


@pytest.mark.asyncio
async def test_claim_owner_resume_immediate_renew_false_marks_lost_without_new_task(
    monkeypatch,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import (
        InboundClaimOwner,
        InboundClaimOwnershipLostError,
    )

    sessions: list[_LifecycleSession] = []
    renew_calls: list[tuple[_LifecycleSession, object, float]] = []
    controlled_sleep = _ControlledClaimSleep()

    def session_factory():
        session = _LifecycleSession(f"resume-lost-{len(sessions)}")
        sessions.append(session)
        return session

    def lose_ownership(db, handle, *, lease_seconds):
        renew_calls.append((db, handle, lease_seconds))
        return False

    monkeypatch.setattr(lifecycle, "renew_inbound_claim", lose_ownership)
    owner = InboundClaimOwner(
        _lifecycle_handle("resume-lost"),
        session_factory=session_factory,
        lease_seconds=30,
        renew_interval_seconds=5,
        sleep=controlled_sleep,
    )

    await owner.start()
    await controlled_sleep.wait_for_calls(1)
    paused_task = owner.renewal_task
    assert await owner.pause() is True
    assert paused_task is not None and paused_task.done()

    with pytest.raises(InboundClaimOwnershipLostError) as raised:
        await owner.resume()

    assert "resume-lost" in str(raised.value)
    assert owner.ownership_lost is True
    assert owner.renewal_task is paused_task
    assert owner.renewal_task.done()
    assert len(renew_calls) == 1
    assert renew_calls[0][1:] == (_lifecycle_handle("resume-lost"), 30)
    assert sessions == [renew_calls[0][0]]
    assert sessions[0].closed


@pytest.mark.asyncio
async def test_claim_owner_default_session_factory_is_resolved_at_call_time(
    monkeypatch,
):
    import core.database as database
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import InboundClaimOwner

    sessions: list[_LifecycleSession] = []
    controlled_sleep = _ControlledClaimSleep()
    owner = InboundClaimOwner(
        _lifecycle_handle("dynamic-session-factory"),
        renew_interval_seconds=3,
        sleep=controlled_sleep,
    )

    def replacement_session_local():
        session = _LifecycleSession(f"dynamic-{len(sessions)}")
        sessions.append(session)
        return session

    monkeypatch.setattr(database, "SessionLocal", replacement_session_local)
    monkeypatch.setattr(lifecycle, "renew_inbound_claim", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(lifecycle, "complete_inbound_claim", lambda *_args, **_kwargs: True)

    await owner.start()
    await controlled_sleep.wait_for_calls(1)
    controlled_sleep.release(0)
    await _wait_for_condition(lambda: len(sessions) == 1)
    await controlled_sleep.wait_for_calls(2)
    assert await owner.complete(_response()) is True

    assert [session.label for session in sessions] == ["dynamic-0", "dynamic-1"]
    assert all(session.closed for session in sessions)


@pytest.mark.asyncio
async def test_claim_owner_preserves_injected_falsy_callable_session_factory(
    monkeypatch,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import InboundClaimOwner

    class FalsySessionFactory:
        def __init__(self):
            self.calls = 0
            self.sessions: list[_LifecycleSession] = []

        def __bool__(self):
            return False

        def __call__(self):
            self.calls += 1
            session = _LifecycleSession(f"injected-{self.calls}")
            self.sessions.append(session)
            return session

    injected_factory = FalsySessionFactory()
    default_sessions: list[_LifecycleSession] = []

    def default_factory():
        session = _LifecycleSession(f"default-{len(default_sessions)}")
        default_sessions.append(session)
        return session

    monkeypatch.setattr(lifecycle, "_default_session_factory", default_factory)
    monkeypatch.setattr(lifecycle, "complete_inbound_claim", lambda *_args, **_kwargs: True)
    owner = InboundClaimOwner(
        _lifecycle_handle("falsy-session-factory"),
        session_factory=injected_factory,
    )

    assert await owner.complete(_response()) is True
    assert injected_factory.calls == 1
    assert len(injected_factory.sessions) == 1
    assert injected_factory.sessions[0].closed
    assert default_sessions == []


@pytest.mark.asyncio
async def test_claim_owner_complete_success_survives_session_close_error(
    monkeypatch,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import InboundClaimOwner

    close_error = RuntimeError("close failed after complete")
    logger = _RecordingLifecycleLogger()
    sessions: list[_CloseFailingLifecycleSession] = []
    complete_calls = 0
    response = _response(reply="close 后仍完成")

    def session_factory():
        session = _CloseFailingLifecycleSession(
            f"complete-close-{len(sessions)}",
            close_error,
        )
        sessions.append(session)
        return session

    def successful_complete(*_args, **_kwargs):
        nonlocal complete_calls
        complete_calls += 1
        return True

    monkeypatch.setattr(lifecycle, "complete_inbound_claim", successful_complete)
    owner = InboundClaimOwner(
        _lifecycle_handle("complete-close-error"),
        session_factory=session_factory,
        logger=logger,
    )

    assert await owner.complete(response) is True
    assert await owner.complete(_response(reply="close 后仍完成")) is True
    assert complete_calls == 1
    assert len(sessions) == 1
    assert sessions[0].close_calls == 1
    assert logger.calls == [("exception", "关闭 claim Session 失败")]


@pytest.mark.asyncio
async def test_claim_owner_renew_success_survives_close_error_and_continues_loop(
    monkeypatch,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import InboundClaimOwner

    close_error = RuntimeError("close failed after renew")
    logger = _RecordingLifecycleLogger()
    controlled_sleep = _ControlledClaimSleep()
    sessions: list[_LifecycleSession] = []
    renew_calls = 0

    def session_factory():
        if not sessions:
            session = _CloseFailingLifecycleSession("renew-close-0", close_error)
        else:
            session = _LifecycleSession(f"renew-close-{len(sessions)}")
        sessions.append(session)
        return session

    def successful_renew(*_args, **_kwargs):
        nonlocal renew_calls
        renew_calls += 1
        return True

    monkeypatch.setattr(lifecycle, "renew_inbound_claim", successful_renew)
    monkeypatch.setattr(lifecycle, "complete_inbound_claim", lambda *_args, **_kwargs: True)
    owner = InboundClaimOwner(
        _lifecycle_handle("renew-close-error"),
        session_factory=session_factory,
        renew_interval_seconds=2,
        sleep=controlled_sleep,
        logger=logger,
    )

    await owner.start()
    await controlled_sleep.wait_for_calls(1)
    controlled_sleep.release(0)
    await _wait_for_condition(
        lambda: len(controlled_sleep.calls) >= 2 or owner.renewal_task.done()
    )

    assert renew_calls == 1
    assert len(controlled_sleep.calls) == 2
    assert owner.renewal_error is None
    assert sessions[0].close_calls == 1
    assert logger.calls == [("exception", "关闭 claim Session 失败")]
    assert await owner.complete(_response()) is True
    assert owner.renewal_task.done()


@pytest.mark.asyncio
async def test_claim_owner_operation_error_wins_over_session_close_error(
    monkeypatch,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import InboundClaimOwner

    operation_error = RuntimeError("complete operation failed")
    close_error = RuntimeError("close also failed")
    logger = _RecordingLifecycleLogger()
    session = _CloseFailingLifecycleSession("operation-and-close-error", close_error)

    def failing_complete(*_args, **_kwargs):
        raise operation_error

    monkeypatch.setattr(lifecycle, "complete_inbound_claim", failing_complete)
    owner = InboundClaimOwner(
        _lifecycle_handle("operation-and-close-error"),
        session_factory=lambda: session,
        logger=logger,
    )

    with pytest.raises(RuntimeError) as raised:
        await owner.complete(_response())
    with pytest.raises(RuntimeError) as repeated:
        await owner.complete(_response())

    assert raised.value is operation_error
    assert repeated.value is operation_error
    assert session.close_calls == 1
    assert logger.calls == [("exception", "关闭 claim Session 时再次失败")]


@pytest.mark.asyncio
async def test_claim_owner_operation_error_survives_close_and_logger_errors(
    monkeypatch,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import InboundClaimOwner

    operation_error = RuntimeError("original complete error")
    close_error = RuntimeError("secondary close error")
    logger_error = RuntimeError("tertiary logger error")
    logger = _ExplodingLifecycleLogger(logger_error)
    session = _CloseFailingLifecycleSession("triple-error", close_error)

    def failing_complete(*_args, **_kwargs):
        raise operation_error

    monkeypatch.setattr(lifecycle, "complete_inbound_claim", failing_complete)
    owner = InboundClaimOwner(
        _lifecycle_handle("triple-error"),
        session_factory=lambda: session,
        logger=logger,
    )

    with pytest.raises(RuntimeError) as raised:
        await owner.complete(_response())
    with pytest.raises(RuntimeError) as repeated:
        await owner.complete(_response())

    assert raised.value is operation_error
    assert repeated.value is operation_error
    assert session.close_calls == 1
    assert logger.calls == [("exception", "关闭 claim Session 时再次失败")]


@pytest.mark.asyncio
async def test_claim_owner_success_survives_close_and_logger_errors(
    monkeypatch,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import InboundClaimOwner

    close_error = RuntimeError("close error after success")
    logger_error = RuntimeError("logger error after success")
    logger = _ExplodingLifecycleLogger(logger_error)
    session = _CloseFailingLifecycleSession("success-close-log-error", close_error)
    response = _response(reply="日志失败不影响成功")

    monkeypatch.setattr(lifecycle, "complete_inbound_claim", lambda *_args, **_kwargs: True)
    owner = InboundClaimOwner(
        _lifecycle_handle("success-close-log-error"),
        session_factory=lambda: session,
        logger=logger,
    )

    assert await owner.complete(response) is True
    assert await owner.complete(_response(reply="日志失败不影响成功")) is True
    assert session.close_calls == 1
    assert logger.calls == [("exception", "关闭 claim Session 失败")]


@pytest.mark.parametrize("renew_result", ["error", "lost"])
@pytest.mark.asyncio
async def test_claim_owner_renew_logging_error_does_not_escape_task(
    monkeypatch,
    renew_result,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import InboundClaimOwner

    renew_error = RuntimeError("renew operation error")
    logger_error = RuntimeError("renew logger error")
    logger = _ExplodingLifecycleLogger(logger_error)
    controlled_sleep = _ControlledClaimSleep()
    sessions: list[_LifecycleSession] = []

    def session_factory():
        session = _LifecycleSession(f"renew-log-{len(sessions)}")
        sessions.append(session)
        return session

    def renew(*_args, **_kwargs):
        if renew_result == "error":
            raise renew_error
        return False

    monkeypatch.setattr(lifecycle, "renew_inbound_claim", renew)
    owner = InboundClaimOwner(
        _lifecycle_handle(f"renew-log-{renew_result}"),
        session_factory=session_factory,
        renew_interval_seconds=2,
        sleep=controlled_sleep,
        logger=logger,
    )

    task = await owner.start()
    await controlled_sleep.wait_for_calls(1)
    controlled_sleep.release(0)
    await _wait_for_condition(task.done)

    assert task.cancelled() is False
    assert task.exception() is None
    assert sessions[0].closed
    if renew_result == "error":
        assert owner.renewal_error is renew_error
        assert owner.ownership_lost is False
        assert logger.calls[0][0] == "error"
    else:
        assert owner.renewal_error is None
        assert owner.ownership_lost is True
        assert logger.calls[0][0] == "warning"


@pytest.mark.asyncio
async def test_claim_owner_concurrent_and_repeated_settlement_performs_one_real_write(
    monkeypatch,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import InboundClaimOwner

    sessions: list[_LifecycleSession] = []
    calls = {"complete": 0, "fail": 0}
    delayed_sleep = _DelayedCancellationSleep()
    response_a = _response(reply="并发响应 A")
    response_b = _response(reply="并发响应 B")

    def session_factory():
        session = _LifecycleSession(f"settle-{len(sessions)}")
        sessions.append(session)
        return session

    def fake_complete(*_args, **_kwargs):
        calls["complete"] += 1
        return True

    def fake_fail(*_args, **_kwargs):
        calls["fail"] += 1
        return True

    monkeypatch.setattr(lifecycle, "complete_inbound_claim", fake_complete)
    monkeypatch.setattr(lifecycle, "fail_inbound_claim", fake_fail)
    owner = InboundClaimOwner(
        _lifecycle_handle(),
        session_factory=session_factory,
        sleep=delayed_sleep,
    )

    await owner.start()
    await asyncio.wait_for(delayed_sleep.started.wait(), timeout=1)
    first_complete = asyncio.create_task(owner.complete(response_a))
    await asyncio.wait_for(delayed_sleep.cancel_started.wait(), timeout=1)
    second_complete = asyncio.create_task(owner.complete(response_b))
    late_fail = asyncio.create_task(owner.fail("late failure"))
    await asyncio.sleep(0)

    assert not first_complete.done()
    assert not second_complete.done()
    assert not late_fail.done()
    assert calls == {"complete": 0, "fail": 0}

    delayed_sleep.release_cancel.set()
    results = await asyncio.gather(
        first_complete,
        second_complete,
        late_fail,
        return_exceptions=True,
    )

    assert results[0] is True
    assert isinstance(results[1], ValueError)
    assert results[2] is False
    assert await owner.complete(_response(reply="并发响应 A")) is True
    with pytest.raises(ValueError, match="完成结果不一致"):
        await owner.complete(response_b)
    assert await owner.fail("late failure") is False
    assert calls == {"complete": 1, "fail": 0}
    assert len(sessions) == 1
    assert sessions[0].close_calls == 1
    assert owner.renewal_task.done()


@pytest.mark.asyncio
async def test_claim_owner_repeated_complete_requires_equal_persisted_response(
    monkeypatch,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import InboundClaimOwner

    sessions: list[_LifecycleSession] = []
    complete_calls: list[tuple[_LifecycleSession, object]] = []
    response_a = _response(reply="响应 A")
    equal_response_a = _response(reply="响应 A")
    response_b = _response(reply="响应 B")

    def session_factory():
        session = _LifecycleSession(f"complete-consistency-{len(sessions)}")
        sessions.append(session)
        return session

    def successful_complete(db, _handle, response):
        complete_calls.append((db, response))
        return True

    monkeypatch.setattr(lifecycle, "complete_inbound_claim", successful_complete)
    owner = InboundClaimOwner(_lifecycle_handle(), session_factory=session_factory)

    assert await owner.complete(response_a) is True
    assert await owner.complete(equal_response_a) is True
    response_a.reply_meta["quote"] = "持久化成功后被修改"
    with pytest.raises(ValueError, match="完成结果不一致"):
        await owner.complete(response_a)
    with pytest.raises(ValueError, match="完成结果不一致"):
        await owner.complete(response_b)
    with pytest.raises(TypeError, match="CompletedInboundResponse"):
        await owner.complete({"outcome": "respond"})

    assert complete_calls == [(sessions[0], response_a)]
    assert len(sessions) == 1
    assert sessions[0].closed


@pytest.mark.asyncio
async def test_claim_owner_repeated_fail_returns_cached_success_and_blocks_complete(
    monkeypatch,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import (
        InboundClaimOwner,
        InboundClaimOwnershipLostError,
    )

    sessions: list[_LifecycleSession] = []
    calls = {"complete": 0, "fail": 0}

    def session_factory():
        session = _LifecycleSession(f"fail-{len(sessions)}")
        sessions.append(session)
        return session

    def fake_complete(*_args, **_kwargs):
        calls["complete"] += 1
        return True

    def fake_fail(*_args, **_kwargs):
        calls["fail"] += 1
        return True

    monkeypatch.setattr(lifecycle, "complete_inbound_claim", fake_complete)
    monkeypatch.setattr(lifecycle, "fail_inbound_claim", fake_fail)
    owner = InboundClaimOwner(_lifecycle_handle(), session_factory=session_factory)

    assert await owner.fail("boom") is True
    assert await owner.fail("boom again") is True
    with pytest.raises(InboundClaimOwnershipLostError, match="已失败"):
        await owner.complete(_response())

    assert calls == {"complete": 0, "fail": 1}
    assert len(sessions) == 1
    assert sessions[0].closed


@pytest.mark.asyncio
async def test_claim_owner_renew_false_then_complete_false_reports_ownership_lost(
    monkeypatch,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import (
        InboundClaimOwner,
        InboundClaimOwnershipLostError,
    )

    sessions: list[_LifecycleSession] = []
    controlled_sleep = _ControlledClaimSleep()

    def session_factory():
        session = _LifecycleSession(f"lost-{len(sessions)}")
        sessions.append(session)
        return session

    monkeypatch.setattr(lifecycle, "renew_inbound_claim", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(lifecycle, "complete_inbound_claim", lambda *_args, **_kwargs: False)
    owner = InboundClaimOwner(
        _lifecycle_handle(),
        session_factory=session_factory,
        renew_interval_seconds=2,
        sleep=controlled_sleep,
    )

    await owner.start()
    await controlled_sleep.wait_for_calls(1)
    controlled_sleep.release(0)
    await _wait_for_condition(lambda: owner.renewal_task.done())

    assert owner.ownership_lost is True
    with pytest.raises(InboundClaimOwnershipLostError, match="owner-token-1"):
        await owner.complete(_response())

    assert len(sessions) == 2
    assert all(session.closed for session in sessions)
    assert owner.renewal_task.done()


@pytest.mark.asyncio
async def test_claim_owner_renew_exception_is_recorded_but_complete_still_attempts_fenced_write(
    monkeypatch,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import InboundClaimOwner

    sessions: list[_LifecycleSession] = []
    complete_sessions: list[_LifecycleSession] = []
    controlled_sleep = _ControlledClaimSleep()
    renewal_error = RuntimeError("temporary renew failure")

    def session_factory():
        session = _LifecycleSession(f"renew-error-{len(sessions)}")
        sessions.append(session)
        return session

    def fake_renew(*_args, **_kwargs):
        raise renewal_error

    def fake_complete(db, *_args, **_kwargs):
        complete_sessions.append(db)
        return True

    monkeypatch.setattr(lifecycle, "renew_inbound_claim", fake_renew)
    monkeypatch.setattr(lifecycle, "complete_inbound_claim", fake_complete)
    owner = InboundClaimOwner(
        _lifecycle_handle(),
        session_factory=session_factory,
        renew_interval_seconds=2,
        sleep=controlled_sleep,
    )

    await owner.start()
    await controlled_sleep.wait_for_calls(1)
    controlled_sleep.release(0)
    await _wait_for_condition(lambda: owner.renewal_task.done())

    assert owner.renewal_error is renewal_error
    assert sessions[0].closed
    assert await owner.complete(_response()) is True
    assert complete_sessions == [sessions[1]]
    assert sessions[1].closed


@pytest.mark.asyncio
async def test_claim_owner_cancelled_settlement_closes_session_and_does_not_leak_renewal(
    monkeypatch,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import InboundClaimOwner

    sessions: list[_LifecycleSession] = []
    controlled_sleep = _ControlledClaimSleep()

    def session_factory():
        session = _LifecycleSession(f"cancel-{len(sessions)}")
        sessions.append(session)
        return session

    def cancelled_complete(*_args, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(lifecycle, "complete_inbound_claim", cancelled_complete)
    owner = InboundClaimOwner(
        _lifecycle_handle(),
        session_factory=session_factory,
        renew_interval_seconds=2,
        sleep=controlled_sleep,
    )
    await owner.start()
    await controlled_sleep.wait_for_calls(1)

    with pytest.raises(asyncio.CancelledError):
        await owner.complete(_response())

    assert len(sessions) == 1
    assert sessions[0].closed
    assert owner.renewal_task.done()


@pytest.mark.parametrize(
    "complete_error",
    [RuntimeError("complete failed before write"), asyncio.CancelledError()],
    ids=["runtime-error", "cancelled-error"],
)
@pytest.mark.asyncio
async def test_claim_owner_complete_error_allows_one_real_compensating_fail(
    monkeypatch,
    complete_error,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import (
        InboundClaimOwner,
        InboundClaimOwnershipLostError,
    )

    sessions: list[_LifecycleSession] = []
    complete_calls: list[_LifecycleSession] = []
    fail_calls: list[tuple[_LifecycleSession, object]] = []

    def session_factory():
        session = _LifecycleSession(f"complete-error-{len(sessions)}")
        sessions.append(session)
        return session

    def failing_complete(db, *_args, **_kwargs):
        complete_calls.append(db)
        raise complete_error

    def successful_fail(db, _handle, error):
        fail_calls.append((db, error))
        return True

    monkeypatch.setattr(lifecycle, "complete_inbound_claim", failing_complete)
    monkeypatch.setattr(lifecycle, "fail_inbound_claim", successful_fail)
    owner = InboundClaimOwner(_lifecycle_handle(), session_factory=session_factory)

    with pytest.raises(type(complete_error)) as first_error:
        await owner.complete(_response())
    with pytest.raises(type(complete_error)) as repeated_error:
        await owner.complete(_response())

    assert first_error.value is complete_error
    assert repeated_error.value is complete_error
    assert complete_calls == [sessions[0]]
    assert sessions[0].closed

    assert await owner.fail(complete_error) is True
    assert fail_calls == [(sessions[1], complete_error)]
    assert sessions[1].closed
    assert await owner.fail(complete_error) is True
    with pytest.raises(InboundClaimOwnershipLostError, match="已失败"):
        await owner.complete(_response())
    assert len(sessions) == 2


@pytest.mark.asyncio
async def test_claim_owner_ambiguous_complete_then_failed_fencing_preserves_completed_row(
    tmp_path,
    monkeypatch,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.database import InboundMessageClaim
    from core.inbound_claim_lifecycle import (
        InboundClaimOwner,
        InboundClaimOwnershipLostError,
    )
    from core.inbound_idempotency import acquire_inbound_claim

    db_path = tmp_path / "owner-ambiguous-complete.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    install_base_schema(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    response = _response()
    complete_error = RuntimeError("commit acknowledged late")
    real_complete = lifecycle.complete_inbound_claim

    def commit_then_raise(db, handle, completed_response):
        assert real_complete(db, handle, completed_response) is True
        raise complete_error

    try:
        with Session() as db:
            handle = acquire_inbound_claim(db, _key("ambiguous-owner-complete")).handle
        monkeypatch.setattr(lifecycle, "complete_inbound_claim", commit_then_raise)
        owner = InboundClaimOwner(handle, session_factory=Session)

        with pytest.raises(RuntimeError) as raised:
            await owner.complete(response)
        assert raised.value is complete_error
        assert await owner.fail(complete_error) is False
        assert owner.ownership_lost is True
        with pytest.raises(InboundClaimOwnershipLostError):
            await owner.complete(response)

        with Session() as db:
            row = db.scalar(select(InboundMessageClaim))
            assert row.status == "completed"
            assert row.owner_token == handle.owner_token
            assert row.response_json
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_claim_owner_fail_error_is_cached_without_retrying_any_settlement(
    monkeypatch,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import InboundClaimOwner

    sessions: list[_LifecycleSession] = []
    fail_error = RuntimeError("ambiguous fail")
    fail_calls = 0
    complete_calls = 0

    def session_factory():
        session = _LifecycleSession(f"fail-error-{len(sessions)}")
        sessions.append(session)
        return session

    def failing_fail(*_args, **_kwargs):
        nonlocal fail_calls
        fail_calls += 1
        raise fail_error

    def successful_complete(*_args, **_kwargs):
        nonlocal complete_calls
        complete_calls += 1
        return True

    monkeypatch.setattr(lifecycle, "fail_inbound_claim", failing_fail)
    monkeypatch.setattr(lifecycle, "complete_inbound_claim", successful_complete)
    owner = InboundClaimOwner(_lifecycle_handle(), session_factory=session_factory)

    with pytest.raises(RuntimeError) as first_error:
        await owner.fail(fail_error)
    with pytest.raises(RuntimeError) as repeated_error:
        await owner.fail(fail_error)
    with pytest.raises(RuntimeError) as blocked_complete:
        await owner.complete(_response())

    assert first_error.value is fail_error
    assert repeated_error.value is fail_error
    assert blocked_complete.value is fail_error
    assert fail_calls == 1
    assert complete_calls == 0
    assert len(sessions) == 1
    assert sessions[0].closed


@pytest.mark.asyncio
async def test_claim_owner_renew_base_exception_closes_session_and_stops_task(
    monkeypatch,
):
    import core.inbound_claim_lifecycle as lifecycle
    from core.inbound_claim_lifecycle import InboundClaimOwner

    class FatalRenewal(BaseException):
        pass

    sessions: list[_LifecycleSession] = []
    controlled_sleep = _ControlledClaimSleep()
    fatal = FatalRenewal("fatal renewal")

    def session_factory():
        session = _LifecycleSession(f"fatal-{len(sessions)}")
        sessions.append(session)
        return session

    def fatal_renew(*_args, **_kwargs):
        raise fatal

    monkeypatch.setattr(lifecycle, "renew_inbound_claim", fatal_renew)
    owner = InboundClaimOwner(
        _lifecycle_handle(),
        session_factory=session_factory,
        renew_interval_seconds=2,
        sleep=controlled_sleep,
    )

    await owner.start()
    await controlled_sleep.wait_for_calls(1)
    controlled_sleep.release(0)
    await _wait_for_condition(lambda: owner.renewal_task.done())

    assert owner.renewal_error is fatal
    assert sessions[0].closed
    assert owner.renewal_task.done()


def _current_chat_request(*, stream: bool = False):
    return SimpleNamespace(
        user_id="current-user",
        session_id="opaque-current-session",
        stream=stream,
        client_meta={
            "platform": "wechat",
            "chat_type": "private",
            "trace": {"request_id": "current-request-id"},
        },
    )


@pytest.mark.parametrize(
    ("stream", "expected_status", "has_answer_chunks"),
    [(False, "ok", True), (True, "done", False)],
)
def test_completed_chat_response_uses_current_request_identity_and_transport_answer(
    stream,
    expected_status,
    has_answer_chunks,
):
    from api.chat_response_contract import (
        build_completed_inbound_response,
        completed_chat_response_payload,
    )
    from core.inbound_idempotency import GroupReplayFields

    completed = build_completed_inbound_response(
        outcome="respond",
        reply="stored:image-ref",
        reply_meta={"send_mode": "quote", "_agent_result": "must-not-replay"},
        reason="answered",
        source="bridge",
        intent="answer",
        guardrail_status="safe",
        unprocessed_logs=4,
        group=GroupReplayFields(generation=99, hard_rule="must-not-leak"),
    )
    req = _current_chat_request(stream=stream)

    payload = completed_chat_response_payload(
        req,
        completed,
        answer_override="expanded:https://cdn.example/image.png",
    )

    assert payload["status"] == expected_status
    assert payload["user_id"] == "current-user"
    assert payload["reply"] == "expanded:https://cdn.example/image.png"
    assert payload["answer"] == payload["reply"]
    assert payload["messages"] == [
        {"type": "text", "text": "expanded:https://cdn.example/image.png"}
    ]
    assert payload["reply_meta"] == {"send_mode": "quote"}
    assert payload["meta"] == {
        "user_id": "current-user",
        "session_id": "opaque-current-session",
        "platform": "wechat",
        "chat_type": "private",
        "request_id": "current-request-id",
        "unprocessed_logs": 4,
        "reason": "answered",
        "source": "bridge",
        "intent": "answer",
        "guardrail_status": "safe",
    }
    assert completed.reply == "stored:image-ref"
    assert ("answer_chunks" in payload) is has_answer_chunks
    if has_answer_chunks:
        assert payload["answer_chunks"] == ["expanded:https://cdn.example/image.png"]
    assert "generation" not in payload
    assert "group" not in payload
    assert "hard_rule" not in payload["meta"]


@pytest.mark.parametrize(
    ("outcome", "expected_status", "stream"),
    [
        ("no_reply", "no_reply", False),
        ("silent", "silent", True),
        ("wait", "wait", True),
        ("blocked", "silent", False),
    ],
)
def test_completed_chat_response_maps_non_respond_outcomes(
    outcome,
    expected_status,
    stream,
):
    from api.chat_response_contract import completed_chat_response_payload
    from core.inbound_idempotency import CompletedInboundResponse

    completed = CompletedInboundResponse(
        outcome=outcome,
        reply=f"stored-secret-{outcome}",
        reason=f"reason-{outcome}",
        guardrail_status="blocked" if outcome == "blocked" else "safe",
    )

    payload = completed_chat_response_payload(
        _current_chat_request(stream=stream),
        completed,
        answer_override=f"expanded-secret-{outcome}",
    )

    assert payload["status"] == expected_status
    assert payload["reason"] == f"reason-{outcome}"
    assert payload["meta"]["reason"] == f"reason-{outcome}"
    assert payload["meta"]["guardrail_status"] == (
        "blocked" if outcome == "blocked" else "safe"
    )
    assert payload["reply"] == ""
    assert payload["answer"] == ""
    assert payload["messages"] == []
    assert payload["answer_chunks"] == []
    assert completed.reply == f"stored-secret-{outcome}"


def test_duplicate_inflight_chat_payload_is_empty_standard_current_request_envelope():
    from api.chat_response_contract import duplicate_inflight_chat_response_payload

    payload = duplicate_inflight_chat_response_payload(_current_chat_request(stream=True))

    assert payload == {
        "status": "duplicate_inflight",
        "reply": "",
        "messages": [],
        "reply_meta": {},
        "meta": {
            "user_id": "current-user",
            "session_id": "opaque-current-session",
            "platform": "wechat",
            "chat_type": "private",
            "request_id": "current-request-id",
            "reason": "duplicate_inflight",
        },
        "user_id": "current-user",
        "answer": "",
        "reason": "duplicate_inflight",
        "answer_chunks": [],
    }


def test_completed_builder_and_codec_exclude_transport_and_request_identity():
    from api.chat_response_contract import build_completed_inbound_response
    from core.inbound_idempotency import encode_completed_inbound_response

    completed = build_completed_inbound_response(
        outcome="respond",
        reply="raw business reply",
        reply_meta={
            "send_mode": "quote",
            "request_id": "must-not-persist",
            "status": "done",
            "sse": "data: leaked",
        },
        reason="ok",
        source="bridge",
        intent="answer",
        guardrail_status="safe",
        unprocessed_logs=1,
    )
    encoded = encode_completed_inbound_response(completed)
    body = json.loads(encoded)

    assert body["reply"] == "raw business reply"
    assert body["reply_meta"] == {"send_mode": "quote"}
    assert {
        "status",
        "meta",
        "user_id",
        "session_id",
        "stream",
        "answer",
        "answer_chunks",
        "messages",
    }.isdisjoint(body)
    assert "current-user" not in encoded
    assert "opaque-current-session" not in encoded
    assert "data:" not in encoded


class _NoChatRouteSideEffectsDb:
    def query(self, *_args, **_kwargs):
        raise AssertionError("claim 裁决前不得查询业务数据库")


def _route_claim_handle(message_id: str = "route-message"):
    from core.inbound_idempotency import InboundClaimHandle, InboundClaimKey

    return InboundClaimHandle(
        key=InboundClaimKey(
            platform="web",
            chat_type="private",
            session_id="private_shared_session",
            message_id=message_id,
        ),
        owner_token="route-owner-token",
        lease_expires_at=datetime(2026, 7, 10, 12, 15),
        attempt_count=1,
    )


@pytest.mark.asyncio
async def test_proxy_chat_claims_normalized_identity_before_logging_or_side_effects_and_writes_files(
    monkeypatch,
):
    from fastapi import BackgroundTasks

    from api import routes
    from core.inbound_idempotency import (
        ClaimDecisionKind,
        InboundClaimDecision,
        InboundClaimKey,
    )

    events: list[str] = []
    captured_keys: list[tuple[str, str, str, str | None]] = []
    original_normalize_meta = routes._normalize_request_client_meta

    def normalize_meta(req, *, expected_chat_type):
        events.append("normalize_meta")
        return original_normalize_meta(req, expected_chat_type=expected_chat_type)

    def normalize_files(_files):
        events.append("normalize_files")
        return ["https://cdn.example/normalized.png"]

    def normalize_key(platform, chat_type, session_id, message_id):
        events.append("normalize_key")
        captured_keys.append((platform, chat_type, session_id, message_id))
        return InboundClaimKey(
            platform=platform,
            chat_type=chat_type,
            session_id=session_id,
            message_id=str(message_id).strip(),
        )

    def acquire_claim(db, key):
        events.append("acquire")
        assert isinstance(db, _NoChatRouteSideEffectsDb)
        assert key.message_id == "same-message"
        return InboundClaimDecision(kind=ClaimDecisionKind.DUPLICATE_INFLIGHT)

    async def run_fake_session_phase(operation, **_kwargs):
        # 本用例只验证 claim 发生在日志和业务副作用之前。生产实现使用 fresh
        # Session；这里显式注入无业务能力的测试 phase，避免继续把 request
        # Session 当作生产持久化事实源。
        return operation(_NoChatRouteSideEffectsDb())

    monkeypatch.setattr(routes, "_normalize_request_client_meta", normalize_meta)
    monkeypatch.setattr(routes, "_normalize_files", normalize_files)
    monkeypatch.setattr(routes, "normalize_inbound_claim_key", normalize_key, raising=False)
    monkeypatch.setattr(routes, "acquire_inbound_claim", acquire_claim, raising=False)
    monkeypatch.setattr(routes, "run_session_phase_async", run_fake_session_phase)
    monkeypatch.setattr(
        routes,
        "InboundClaimOwner",
        lambda *_args, **_kwargs: pytest.fail("inflight 非 owner 不得创建 owner"),
        raising=False,
    )
    monkeypatch.setattr(routes.logger, "info", lambda *_args, **_kwargs: events.append("log"))

    requests = [
        routes.ChatProxyRequest(
            user_id="first-user",
            session_id="private_shared_session",
            query="第一条正文",
            files=[" raw-first ", ""],
            stream=False,
            message_id=" same-message ",
            client_meta={"platform": "WEB"},
        ),
        routes.ChatProxyRequest(
            user_id="second-user",
            session_id="private_shared_session",
            query="第二条正文",
            files=["raw-second"],
            stream=True,
            message_id=" same-message ",
            client_meta={"platform": "web"},
        ),
    ]

    results = [
        await routes.proxy_chat(req, BackgroundTasks(), _NoChatRouteSideEffectsDb(), None)
        for req in requests
    ]

    assert events == [
        "normalize_meta",
        "normalize_files",
        "normalize_key",
        "acquire",
        "normalize_meta",
        "normalize_files",
        "normalize_key",
        "acquire",
    ]
    assert captured_keys == [
        ("web", "private", "private_shared_session", " same-message "),
        ("web", "private", "private_shared_session", " same-message "),
    ]
    assert all(req.files == ["https://cdn.example/normalized.png"] for req in requests)
    assert [result["status"] for result in results] == [
        "duplicate_inflight",
        "duplicate_inflight",
    ]
    assert results[0]["meta"]["user_id"] == "first-user"
    assert results[1]["meta"]["user_id"] == "second-user"


@pytest.mark.asyncio
async def test_proxy_chat_completed_replay_skips_side_effects_and_uses_current_identity(
    monkeypatch,
):
    from fastapi import BackgroundTasks

    from api import routes
    from core.inbound_idempotency import (
        ClaimDecisionKind,
        CompletedInboundResponse,
        InboundClaimDecision,
    )

    completed = CompletedInboundResponse(
        outcome="respond",
        reply="stored:image-token",
        reply_meta={"send_mode": "quote"},
        reason="answered",
        source="bridge",
        intent="answer",
        guardrail_status="safe",
        unprocessed_logs=6,
    )
    monkeypatch.setattr(
        routes,
        "acquire_inbound_claim",
        lambda _db, _key: InboundClaimDecision(
            kind=ClaimDecisionKind.REPLAY,
            response=completed,
        ),
        raising=False,
    )
    monkeypatch.setattr(
        routes,
        "_expand_chat_transport_answer",
        lambda answer: f"expanded:{answer}",
    )
    monkeypatch.setattr(
        routes.logger,
        "info",
        lambda *_args, **_kwargs: pytest.fail("replay 不得进入请求日志"),
    )

    result = await routes.proxy_chat(
        routes.ChatProxyRequest(
            user_id="replay-current-user",
            session_id="private-replay-session",
            query="不得产生副作用",
            message_id="replay-message",
            client_meta={
                "platform": "web",
                "trace": {"request_id": "replay-current-request"},
            },
        ),
        BackgroundTasks(),
        _NoChatRouteSideEffectsDb(),
        None,
    )

    assert result["status"] == "ok"
    assert result["reply"] == "expanded:stored:image-token"
    assert result["user_id"] == "replay-current-user"
    assert result["meta"]["session_id"] == "private-replay-session"
    assert result["meta"]["request_id"] == "replay-current-request"
    assert result["meta"]["unprocessed_logs"] == 6


@pytest.mark.asyncio
async def test_proxy_chat_stream_completed_respond_replay_yields_exactly_one_done_sse(
    monkeypatch,
):
    from fastapi import BackgroundTasks
    from fastapi.responses import StreamingResponse

    from api import routes
    from core.inbound_idempotency import (
        ClaimDecisionKind,
        CompletedInboundResponse,
        InboundClaimDecision,
    )

    monkeypatch.setattr(
        routes,
        "acquire_inbound_claim",
        lambda _db, _key: InboundClaimDecision(
            kind=ClaimDecisionKind.REPLAY,
            response=CompletedInboundResponse(
                outcome="respond",
                reply="raw-replay-answer",
                reason="answered",
            ),
        ),
        raising=False,
    )
    monkeypatch.setattr(
        routes,
        "_expand_chat_transport_answer",
        lambda answer: f"expanded:{answer}",
    )

    response = await routes.proxy_chat(
        routes.ChatProxyRequest(
            user_id="stream-replay-user",
            session_id="private-stream-replay-user",
            query="流式重放",
            stream=True,
            message_id="stream-replay-message",
            client_meta={"platform": "qq"},
        ),
        BackgroundTasks(),
        _NoChatRouteSideEffectsDb(),
        None,
    )

    assert isinstance(response, StreamingResponse)
    chunks = [chunk async for chunk in response.body_iterator]
    assert len(chunks) == 1
    chunk = chunks[0].decode() if isinstance(chunks[0], bytes) else chunks[0]
    assert chunk.startswith("data: ")
    event = json.loads(chunk.removeprefix("data: ").strip())
    assert event["status"] == "done"
    assert event["reply"] == "expanded:raw-replay-answer"
    assert event["user_id"] == "stream-replay-user"
    assert "delta" not in chunk
    assert "progress" not in chunk
    assert "heartbeat" not in chunk
    assert '"status": "final"' not in chunk


@pytest.mark.asyncio
async def test_proxy_chat_replay_image_expansion_failure_falls_back_to_raw_reply(
    monkeypatch,
):
    from fastapi import BackgroundTasks

    from api import routes
    from core.inbound_idempotency import (
        ClaimDecisionKind,
        CompletedInboundResponse,
        InboundClaimDecision,
    )

    monkeypatch.setattr(
        routes,
        "acquire_inbound_claim",
        lambda _db, _key: InboundClaimDecision(
            kind=ClaimDecisionKind.REPLAY,
            response=CompletedInboundResponse(outcome="respond", reply="raw:image-token"),
        ),
        raising=False,
    )

    def fail_expansion(_answer):
        raise RuntimeError("image expansion failed")

    monkeypatch.setattr(routes, "_expand_chat_transport_answer", fail_expansion)

    result = await routes.proxy_chat(
        routes.ChatProxyRequest(
            user_id="replay-fallback-user",
            session_id="private-replay-fallback-user",
            query="重放回退",
            message_id="replay-fallback-message",
        ),
        BackgroundTasks(),
        _NoChatRouteSideEffectsDb(),
        None,
    )

    assert result["status"] == "ok"
    assert result["reply"] == "raw:image-token"


@pytest.mark.asyncio
async def test_proxy_chat_blank_message_id_bypasses_claim_and_keeps_legacy_blocked_flow(
    db_session,
    monkeypatch,
):
    from fastapi import BackgroundTasks

    from api import routes
    from core.database import InboundMessageClaim, User
    from core.inbound_idempotency import ClaimDecisionKind, InboundClaimDecision

    db_session.add(User(id="blank-message-user", name="旧名称"))
    db_session.commit()
    acquired_keys = []

    def acquire_claim(_db, key):
        acquired_keys.append(key)
        return InboundClaimDecision(kind=ClaimDecisionKind.BYPASS)

    monkeypatch.setattr(routes, "acquire_inbound_claim", acquire_claim, raising=False)
    monkeypatch.setattr(
        routes,
        "InboundClaimOwner",
        lambda *_args, **_kwargs: pytest.fail("BYPASS 不得创建 owner"),
        raising=False,
    )
    monkeypatch.setattr(routes, "_check_user_blocked", lambda *_args, **_kwargs: True)

    result = await routes.proxy_chat(
        routes.ChatProxyRequest(
            user_id="blank-message-user",
            session_id="private-blank-message-user",
            query="空消息 ID 旧流程",
            message_id="   ",
        ),
        BackgroundTasks(),
        db_session,
        None,
    )

    assert acquired_keys == [None]
    assert result["status"] == "silent"
    assert result["reason"] == "user_blocked"
    assert db_session.query(InboundMessageClaim).count() == 0


@pytest.mark.asyncio
async def test_proxy_chat_acquired_stream_cold_body_starts_no_renewal_and_close_fails_once(
    db_session,
    monkeypatch,
):
    from fastapi import BackgroundTasks
    from fastapi.responses import StreamingResponse

    from api import chat_pre_bridge_route_result, chat_route_runner, routes
    from core.database import User
    from core.inbound_idempotency import ClaimDecisionKind, InboundClaimDecision

    db_session.add(User(id="cold-stream-owner", name="冷流用户"))
    db_session.commit()
    handle = _route_claim_handle("cold-stream-message")
    events: list[object] = []

    class FakeOwner:
        renewal_task = None

        def __init__(self, actual_handle, **_kwargs):
            assert actual_handle is handle
            events.append("constructed")

        async def start(self):
            events.append("start")
            self.renewal_task = asyncio.create_task(asyncio.Event().wait())

        async def pause(self):
            events.append("pause")
            if self.renewal_task is not None:
                self.renewal_task.cancel()
                await asyncio.gather(self.renewal_task, return_exceptions=True)
            return True

        async def complete(self, response):
            events.append(("complete", response))
            return True

        async def fail(self, error):
            events.append(("fail", error))
            return True

    class Bridge:
        async def handle_message(self, *_args, **_kwargs):
            events.append("bridge")
            return "不应启动"

    async def resolve_pre_bridge_decision(*_args, **_kwargs):
        return object()

    async def resolve_pre_bridge_route_result(*_args, **_kwargs):
        return chat_pre_bridge_route_result.ChatPreBridgeRouteContinue(
            final_query="冷流查询",
            final_files=[],
            private_decision=None,
            private_timing_meta=None,
            guardrail_status=None,
            classifier_ran=False,
            persist_req=SimpleNamespace(),
        )

    monkeypatch.setattr(
        routes,
        "acquire_inbound_claim",
        lambda _db, _key: InboundClaimDecision(
            kind=ClaimDecisionKind.ACQUIRED,
            handle=handle,
        ),
        raising=False,
    )
    monkeypatch.setattr(routes, "InboundClaimOwner", FakeOwner, raising=False)
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
            lookup_user_id="cold-stream-owner",
            candidate_count=1,
        ),
    )
    monkeypatch.setattr(routes, "_build_chat_context", lambda *_args, **_kwargs: ("", [], {}))
    monkeypatch.setattr(routes, "_resolve_chat_pre_bridge_decision", resolve_pre_bridge_decision)
    monkeypatch.setattr(routes, "_resolve_pre_bridge_route_result", resolve_pre_bridge_route_result)
    monkeypatch.setattr(
        routes,
        "_build_chat_runtime_route_context",
        lambda *_args, **_kwargs: SimpleNamespace(
            enriched_query="冷流 enriched query",
            bridge_meta={"chat_type": "private", "is_group": False},
            platform="qq",
        ),
    )
    monkeypatch.setattr(routes, "get_bridge", Bridge)

    response = await routes.proxy_chat(
        routes.ChatProxyRequest(
            user_id="cold-stream-owner",
            session_id="private_cold-stream-owner",
            query="返回后不消费 body",
            stream=True,
            message_id="cold-stream-message",
        ),
        BackgroundTasks(),
        db_session,
        None,
    )

    assert isinstance(response, StreamingResponse)
    assert events == ["constructed", "start", "pause"]
    assert response.body_iterator._context.claim_owner.renewal_task.done()
    assert not chat_route_runner._STREAM_FINALIZER_TASKS

    await response.body_iterator.aclose()

    assert len(events) == 4
    assert events[0] == "constructed"
    assert events[1:3] == ["start", "pause"]
    assert events[3][0] == "fail"
    assert events[3][1] is not None
    assert "bridge" not in events
    assert not chat_route_runner._STREAM_FINALIZER_TASKS


@pytest.mark.asyncio
async def test_proxy_chat_stream_renews_during_prebridge_then_pauses_until_cold_pull(
    tmp_path,
    monkeypatch,
):
    from fastapi import BackgroundTasks
    from fastapi.responses import StreamingResponse
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from api import chat_pre_bridge_route_result, routes
    from core.inbound_claim_lifecycle import InboundClaimOwner as RealInboundClaimOwner
    from core.inbound_idempotency import (
        ClaimDecisionKind,
        acquire_inbound_claim as real_acquire_inbound_claim,
        normalize_inbound_claim_key,
    )

    db_path = tmp_path / "chat-prebridge-short-lease.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    install_base_schema(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    lease_seconds = 0.15
    prebridge_started = asyncio.Event()
    release_prebridge = asyncio.Event()
    owners: list[RealInboundClaimOwner] = []
    bridge_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def short_acquire(db, key):
        return real_acquire_inbound_claim(
            db,
            key,
            lease_seconds=lease_seconds,
        )

    def owner_factory(handle, **_kwargs):
        owner = RealInboundClaimOwner(
            handle,
            session_factory=Session,
            lease_seconds=lease_seconds,
            renew_interval_seconds=0.03,
        )
        owners.append(owner)
        return owner

    async def run_test_session_phase(operation, **_kwargs):
        from core.database import run_session_phase

        return await asyncio.to_thread(
            run_session_phase,
            operation,
            session_factory=Session,
        )

    class Bridge:
        async def handle_message(self, *args, **kwargs):
            bridge_calls.append((args, kwargs))
            return "不应在 cold body 前调用"

    async def resolve_pre_bridge_decision(*_args, **_kwargs):
        prebridge_started.set()
        await release_prebridge.wait()
        return object()

    async def resolve_pre_bridge_route_result(*_args, **_kwargs):
        return chat_pre_bridge_route_result.ChatPreBridgeRouteContinue(
            final_query="短 lease 查询",
            final_files=[],
            private_decision=None,
            private_timing_meta=None,
            guardrail_status=None,
            classifier_ran=False,
            persist_req=SimpleNamespace(),
        )

    monkeypatch.setattr(routes, "acquire_inbound_claim", short_acquire, raising=False)
    monkeypatch.setattr(routes, "InboundClaimOwner", owner_factory, raising=False)
    monkeypatch.setattr(routes, "run_session_phase_async", run_test_session_phase)
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
            lookup_user_id="prebridge-lease-user",
            candidate_count=1,
        ),
    )
    monkeypatch.setattr(routes, "_build_chat_context", lambda *_args, **_kwargs: ("", [], {}))
    monkeypatch.setattr(routes, "_resolve_chat_pre_bridge_decision", resolve_pre_bridge_decision)
    monkeypatch.setattr(routes, "_resolve_pre_bridge_route_result", resolve_pre_bridge_route_result)
    monkeypatch.setattr(
        routes,
        "_build_chat_runtime_route_context",
        lambda *_args, **_kwargs: SimpleNamespace(
            enriched_query="短 lease enriched query",
            bridge_meta={"chat_type": "private", "is_group": False},
            platform="qq",
        ),
    )
    monkeypatch.setattr(routes, "get_bridge", Bridge)

    request_db = Session()
    response = None
    route_task = asyncio.create_task(
        routes.proxy_chat(
            routes.ChatProxyRequest(
                user_id="prebridge-lease-user",
                session_id="private_prebridge-lease-user",
                query="等待 pre-bridge",
                stream=True,
                message_id="prebridge-lease-message",
                client_meta={"platform": "qq"},
            ),
            BackgroundTasks(),
            request_db,
            None,
        )
    )
    key = normalize_inbound_claim_key(
        "qq",
        "private",
        "private_prebridge-lease-user",
        "prebridge-lease-message",
    )
    try:
        await asyncio.wait_for(prebridge_started.wait(), timeout=1)
        await asyncio.sleep(0.35)
        with Session() as contender_db:
            during_prebridge = real_acquire_inbound_claim(
                contender_db,
                key,
                lease_seconds=lease_seconds,
            )
        assert during_prebridge.kind == ClaimDecisionKind.DUPLICATE_INFLIGHT

        release_prebridge.set()
        response = await asyncio.wait_for(route_task, timeout=1)
        assert isinstance(response, StreamingResponse)
        assert len(owners) == 1
        paused_task = owners[0].renewal_task
        assert paused_task is not None and paused_task.done()
        assert bridge_calls == []

        await asyncio.sleep(0.35)
        with Session() as contender_db:
            after_pause = real_acquire_inbound_claim(
                contender_db,
                key,
                lease_seconds=lease_seconds,
            )
        assert after_pause.kind == ClaimDecisionKind.ACQUIRED

        await response.body_iterator.aclose()
        assert bridge_calls == []
        assert owners[0].renewal_task is paused_task
        assert owners[0].renewal_task.done()
    finally:
        release_prebridge.set()
        if not route_task.done():
            route_task.cancel()
        await asyncio.gather(route_task, return_exceptions=True)
        if response is not None:
            await response.body_iterator.aclose()
        for owner in owners:
            task = owner.renewal_task
            if task is not None and not task.done():
                await owner.pause()
        request_db.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_proxy_chat_blocked_commits_chat_log_before_completing_claim(
    db_session,
    monkeypatch,
):
    from fastapi import BackgroundTasks

    from api import routes
    from core.database import ChatLog, User
    from core.inbound_idempotency import ClaimDecisionKind, InboundClaimDecision

    db_session.add(User(id="blocked-owner-user", name="屏蔽用户"))
    db_session.commit()
    handle = _route_claim_handle("blocked-message")
    events: list[object] = []

    class FakeOwner:
        def __init__(self, actual_handle, **_kwargs):
            assert actual_handle is handle
            self.session_factory = _kwargs["session_factory"]

        async def start(self):
            events.append("start")

        async def complete(self, response):
            with self.session_factory() as verify_db:
                persisted = (
                    verify_db.query(ChatLog)
                    .filter(
                        ChatLog.user_id == "blocked-owner-user",
                        ChatLog.message_id == "blocked-message",
                    )
                    .one()
                )
                assert persisted.processed == 1
            events.append("persisted")
            events.append(("complete", response))
            return True

        async def fail(self, error):
            events.append(("fail", error))
            return True

    monkeypatch.setattr(
        routes,
        "acquire_inbound_claim",
        lambda _db, _key: InboundClaimDecision(
            kind=ClaimDecisionKind.ACQUIRED,
            handle=handle,
        ),
        raising=False,
    )
    monkeypatch.setattr(routes, "InboundClaimOwner", FakeOwner, raising=False)
    monkeypatch.setattr(routes, "_check_user_blocked", lambda *_args, **_kwargs: True)

    result = await routes.proxy_chat(
        routes.ChatProxyRequest(
            user_id="blocked-owner-user",
            session_id="private-blocked-owner-user",
            query="命中屏蔽",
            message_id="blocked-message",
        ),
        BackgroundTasks(),
        db_session,
        None,
    )

    assert result["status"] == "silent"
    assert events[0] == "start"
    complete_index = next(
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and event[0] == "complete"
    )
    assert "persisted" in events[:complete_index]
    _, completion = events[complete_index]
    assert completion.outcome == "blocked"
    assert completion.reason == "user_blocked"
    assert completion.guardrail_status == "silent"
    assert all(event[0] != "fail" for event in events if isinstance(event, tuple))


@pytest.mark.asyncio
async def test_proxy_chat_pre_bridge_persistence_completes_claim_before_return(
    db_session,
    monkeypatch,
):
    from fastapi import BackgroundTasks

    from api import chat_pre_bridge_route_result, routes
    from core.database import User
    from core.inbound_idempotency import (
        ClaimDecisionKind,
        CompletedInboundResponse,
        InboundClaimDecision,
    )

    db_session.add(User(id="early-owner-user", name="早返回用户"))
    db_session.commit()
    handle = _route_claim_handle("early-message")
    completion = CompletedInboundResponse(
        outcome="no_reply",
        reason="timing_gate_no_reply",
    )
    events: list[object] = []

    class FakeOwner:
        def __init__(self, actual_handle, **_kwargs):
            assert actual_handle is handle

        async def start(self):
            events.append("start")

        async def complete(self, response):
            events.append(("complete", response))
            return True

        async def fail(self, error):
            events.append(("fail", error))
            return True

    async def resolve_pre_bridge_route_result(*_args, **_kwargs):
        events.append("persist")
        return chat_pre_bridge_route_result.ChatPreBridgeRouteEarlyResponse(
            payload={"status": "no_reply", "reason": "timing_gate_no_reply"},
            completion=completion,
        )

    monkeypatch.setattr(
        routes,
        "acquire_inbound_claim",
        lambda _db, _key: InboundClaimDecision(
            kind=ClaimDecisionKind.ACQUIRED,
            handle=handle,
        ),
        raising=False,
    )
    monkeypatch.setattr(routes, "InboundClaimOwner", FakeOwner, raising=False)
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
            lookup_user_id="early-owner-user",
            candidate_count=1,
        ),
    )
    monkeypatch.setattr(routes, "_build_chat_context", lambda *_args, **_kwargs: ("", [], {}))
    monkeypatch.setattr(
        routes,
        "_resolve_chat_pre_bridge_decision",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=object()),
    )
    monkeypatch.setattr(routes, "_resolve_pre_bridge_route_result", resolve_pre_bridge_route_result)

    result = await routes.proxy_chat(
        routes.ChatProxyRequest(
            user_id="early-owner-user",
            session_id="private-early-owner-user",
            query="早返回",
            message_id="early-message",
        ),
        BackgroundTasks(),
        db_session,
        None,
    )

    assert result == {"status": "no_reply", "reason": "timing_gate_no_reply"}
    assert events == ["start", "persist", ("complete", completion)]


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("pre-handoff failure"),
        asyncio.CancelledError("pre-handoff cancelled"),
        FatalClaimLifecycleError("pre-handoff fatal"),
    ],
    ids=["technical-error", "cancelled-error", "fatal-base-exception"],
)
@pytest.mark.asyncio
async def test_proxy_chat_owner_handoff_failure_best_effort_fails_and_reraises_original(
    db_session,
    monkeypatch,
    failure,
):
    from fastapi import BackgroundTasks

    from api import routes
    from core.database import User
    from core.inbound_idempotency import ClaimDecisionKind, InboundClaimDecision

    db_session.add(User(id="handoff-failure-user", name="失败用户"))
    db_session.commit()
    handle = _route_claim_handle("handoff-failure-message")
    events: list[object] = []
    owners: list["FakeOwner"] = []

    class FakeOwner:
        def __init__(self, actual_handle, **_kwargs):
            assert actual_handle is handle
            self.renewal_task = None
            owners.append(self)

        async def start(self):
            events.append("start")
            self.renewal_task = asyncio.create_task(asyncio.Event().wait())

        async def complete(self, response):
            events.append(("complete", response))
            return True

        async def fail(self, error):
            events.append(("fail", error))
            if self.renewal_task is not None:
                self.renewal_task.cancel()
                await asyncio.gather(self.renewal_task, return_exceptions=True)
            return True

    def raise_failure(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(
        routes,
        "acquire_inbound_claim",
        lambda _db, _key: InboundClaimDecision(
            kind=ClaimDecisionKind.ACQUIRED,
            handle=handle,
        ),
        raising=False,
    )
    monkeypatch.setattr(routes, "InboundClaimOwner", FakeOwner, raising=False)
    monkeypatch.setattr(routes, "_check_user_blocked", raise_failure)

    try:
        with pytest.raises(type(failure)) as raised:
            await routes.proxy_chat(
                routes.ChatProxyRequest(
                    user_id="handoff-failure-user",
                    session_id="private-handoff-failure-user",
                    query="移交前失败",
                    message_id="handoff-failure-message",
                ),
                BackgroundTasks(),
                db_session,
                None,
            )

        assert raised.value is failure
        assert events == ["start", ("fail", failure)]
        assert owners[0].renewal_task is not None
        assert owners[0].renewal_task.done()
    finally:
        if owners and owners[0].renewal_task is not None and not owners[0].renewal_task.done():
            owners[0].renewal_task.cancel()
            await asyncio.gather(owners[0].renewal_task, return_exceptions=True)


def test_proxy_chat_real_completed_claim_replays_with_reused_request_session_and_calls_bridge_once(
    client,
    db_session,
    monkeypatch,
):
    from unittest.mock import AsyncMock

    from api import routes
    from core.database import InboundMessageClaim, User

    bridge = AsyncMock()
    bridge.handle_message = AsyncMock(return_value="真实幂等回复")
    monkeypatch.setattr(routes, "get_bridge", lambda: bridge)
    monkeypatch.setattr(routes, "_schedule_image_precache", lambda *_args, **_kwargs: None)
    base_payload = {
        "session_id": "group_real-idempotency",
        "query": "真实幂等请求",
        "message_id": "real-idempotency-message",
        "client_meta": {"platform": "qq"},
    }

    first = client.post(
        "/api/v1/chat",
        json={**base_payload, "user_id": "first-real-owner"},
    )
    replay = client.post(
        "/api/v1/chat",
        json={**base_payload, "user_id": "current-replay-user"},
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert first.json()["reply"] == "真实幂等回复"
    assert replay.json()["reply"] == "真实幂等回复"
    assert replay.json()["user_id"] == "current-replay-user"
    assert replay.json()["meta"]["user_id"] == "current-replay-user"
    assert bridge.handle_message.await_count == 1
    assert db_session.query(InboundMessageClaim).one().status == "completed"
    assert db_session.query(User).filter_by(id="current-replay-user").first() is None


def test_proxy_chat_real_stream_owner_replays_to_nonstream_with_current_identity(
    client,
    db_session,
    monkeypatch,
):
    from unittest.mock import AsyncMock

    from api import routes
    from core.database import InboundMessageClaim

    bridge = AsyncMock()
    bridge.handle_message = AsyncMock(return_value="流式 owner 原始回复")
    _use_fast_chat_timers(monkeypatch)
    monkeypatch.setattr(routes, "get_bridge", lambda: bridge)
    monkeypatch.setattr(routes, "_schedule_image_precache", lambda *_args, **_kwargs: None)
    base_payload = {
        "session_id": "private_cross-transport-stream-owner",
        "query": "跨 transport 真实幂等请求",
        "message_id": "cross-transport-stream-owner-message",
    }

    with client.stream(
        "POST",
        "/api/v1/chat",
        json={
            **base_payload,
            "user_id": "stream-owner-user",
            "stream": True,
            "client_meta": {
                "platform": "qq",
                "trace": {"request_id": "stream-owner-request"},
            },
        },
    ) as first:
        first_body = "".join(first.iter_text())

    first_events = [
        json.loads(chunk[6:])
        for chunk in first_body.split("\n\n")
        if chunk.startswith("data: ")
    ]
    first_done_events = [event for event in first_events if event.get("status") == "done"]
    replay = client.post(
        "/api/v1/chat",
        json={
            **base_payload,
            "user_id": "nonstream-replay-user",
            "stream": False,
            "client_meta": {
                "platform": "qq",
                "trace": {"request_id": "nonstream-replay-request"},
            },
        },
    )

    assert first.status_code == 200
    assert len(first_done_events) == 1
    assert first_done_events[0]["reply"] == "流式 owner 原始回复"
    assert first_done_events[0]["user_id"] == "stream-owner-user"
    assert first_done_events[0]["meta"]["request_id"] == "stream-owner-request"
    assert replay.status_code == 200
    assert replay.json()["status"] == "ok"
    assert replay.json()["reply"] == "流式 owner 原始回复"
    assert replay.json()["user_id"] == "nonstream-replay-user"
    assert replay.json()["meta"]["user_id"] == "nonstream-replay-user"
    assert replay.json()["meta"]["request_id"] == "nonstream-replay-request"
    assert bridge.handle_message.await_count == 1
    claim = db_session.query(InboundMessageClaim).one()
    assert claim.status == "completed"
    assert claim.response_json


def test_proxy_chat_real_nonstream_owner_replays_to_stream_as_single_current_done_event(
    client,
    db_session,
    monkeypatch,
):
    from unittest.mock import AsyncMock

    from api import routes
    from core.database import InboundMessageClaim

    bridge = AsyncMock()
    bridge.handle_message = AsyncMock(return_value="非流式 owner 原始回复")
    _use_fast_chat_timers(monkeypatch)
    monkeypatch.setattr(routes, "get_bridge", lambda: bridge)
    monkeypatch.setattr(routes, "_schedule_image_precache", lambda *_args, **_kwargs: None)
    base_payload = {
        "session_id": "private_cross-transport-nonstream-owner",
        "query": "反向跨 transport 真实幂等请求",
        "message_id": "cross-transport-nonstream-owner-message",
    }

    first = client.post(
        "/api/v1/chat",
        json={
            **base_payload,
            "user_id": "nonstream-owner-user",
            "stream": False,
            "client_meta": {
                "platform": "qq",
                "trace": {"request_id": "nonstream-owner-request"},
            },
        },
    )
    with client.stream(
        "POST",
        "/api/v1/chat",
        json={
            **base_payload,
            "user_id": "stream-replay-user",
            "stream": True,
            "client_meta": {
                "platform": "qq",
                "trace": {"request_id": "stream-replay-request"},
            },
        },
    ) as replay:
        replay_body = "".join(replay.iter_text())

    replay_events = [
        json.loads(chunk[6:])
        for chunk in replay_body.split("\n\n")
        if chunk.startswith("data: ")
    ]

    assert first.status_code == 200
    assert first.json()["status"] == "ok"
    assert first.json()["reply"] == "非流式 owner 原始回复"
    assert first.json()["user_id"] == "nonstream-owner-user"
    assert first.json()["meta"]["request_id"] == "nonstream-owner-request"
    assert replay.status_code == 200
    assert len(replay_events) == 1
    assert replay_events[0]["status"] == "done"
    assert replay_events[0]["reply"] == "非流式 owner 原始回复"
    assert replay_events[0]["user_id"] == "stream-replay-user"
    assert replay_events[0]["meta"]["user_id"] == "stream-replay-user"
    assert replay_events[0]["meta"]["request_id"] == "stream-replay-request"
    assert {
        "delta",
        "progress",
        "heartbeat",
        "final",
    }.isdisjoint(event.get("status") for event in replay_events)
    assert bridge.handle_message.await_count == 1
    claim = db_session.query(InboundMessageClaim).one()
    assert claim.status == "completed"
    assert claim.response_json

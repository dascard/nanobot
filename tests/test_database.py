import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError

from core.database import User, Persona, ChatLog, ProactiveOutreachLog

def test_create_user(db_session):
    user = User(id="test_user_1")
    db_session.add(user)
    db_session.commit()
    
    fetched = db_session.query(User).filter_by(id="test_user_1").first()
    assert fetched is not None
    assert fetched.id == "test_user_1"

def test_create_persona(db_session):
    persona = Persona(user_id="user_2", persona_json='{"likes": "apple"}')
    db_session.add(persona)
    db_session.commit()
    
    fetched = db_session.query(Persona).filter_by(user_id="user_2").first()
    assert fetched is not None
    assert fetched.persona_json == '{"likes": "apple"}'

def test_chat_log_processing_flag(db_session):
    log = ChatLog(user_id="user_3", role="user", content="Hello, testing flag")
    db_session.add(log)
    db_session.commit()
    
    fetched = db_session.query(ChatLog).filter_by(user_id="user_3").first()
    assert fetched.processed == 0
    
    fetched.processed = 1
    db_session.commit()
    
    updated = db_session.query(ChatLog).filter_by(user_id="user_3").first()
    assert updated.processed == 1


def test_create_proactive_outreach_log_enforces_idempotency_key(db_session):
    log = ProactiveOutreachLog(
        user_id="superuser",
        idempotency_key="outreach:superuser:20260706T100000",
        grounding_json='{"recent": []}',
        judge_should=True,
        judge_reason="想起之前聊过的项目",
        next_intent="问问项目进展",
        message="刚想起你昨天说的项目，来问问进展。",
        status="pending",
        forced=False,
    )
    db_session.add(log)
    db_session.commit()

    fetched = db_session.query(ProactiveOutreachLog).filter_by(user_id="superuser").one()
    assert fetched.idempotency_key == "outreach:superuser:20260706T100000"
    assert fetched.status == "pending"
    assert fetched.forced is False

    duplicate = ProactiveOutreachLog(
        user_id="superuser",
        idempotency_key="outreach:superuser:20260706T100000",
        grounding_json="{}",
        status="pending",
    )
    db_session.add(duplicate)

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_sqlite_connect_args_include_busy_timeout(monkeypatch):
    from core.database import sqlite_connect_args_for_url

    monkeypatch.setenv("SQLITE_BUSY_TIMEOUT_MS", "45000")

    args = sqlite_connect_args_for_url("sqlite:///./data/test.db")

    assert args["check_same_thread"] is False
    assert args["timeout"] == 45.0


def test_sqlite_connect_args_default_busy_timeout_is_five_seconds(monkeypatch):
    from core.database import sqlite_connect_args_for_url

    monkeypatch.delenv("SQLITE_BUSY_TIMEOUT_MS", raising=False)

    args = sqlite_connect_args_for_url("sqlite:///./data/test.db")

    assert args["timeout"] == 5.0


def test_sqlite_connect_args_invalid_busy_timeout_falls_back_to_five_seconds(monkeypatch):
    from core.database import sqlite_connect_args_for_url

    monkeypatch.setenv("SQLITE_BUSY_TIMEOUT_MS", "not-a-number")

    args = sqlite_connect_args_for_url("sqlite:///./data/test.db")

    assert args["timeout"] == 5.0


def test_release_clean_transaction_refuses_flushed_writes(db_session):
    from core.database import release_clean_session_transaction

    db_session.add(User(id="flushed-write-user"))
    db_session.flush()

    assert list(db_session.new) == []
    assert release_clean_session_transaction(
        db_session,
        label="test_flushed_write_guard",
    ) is False

    db_session.commit()
    assert db_session.get(User, "flushed-write-user") is not None


def test_nested_rollback_keeps_outer_flushed_write_guard(db_session):
    from core.database import release_clean_session_transaction

    db_session.add(User(id="outer-flushed-user"))
    db_session.flush()

    with pytest.raises(RuntimeError, match="rollback nested write"):
        with db_session.begin_nested():
            db_session.add(ChatLog(
                user_id="outer-flushed-user",
                role="user",
                content="只回滚 savepoint 内写入",
            ))
            db_session.flush()
            raise RuntimeError("rollback nested write")

    assert release_clean_session_transaction(
        db_session,
        label="test_nested_rollback_guard",
    ) is False
    db_session.commit()
    assert db_session.get(User, "outer-flushed-user") is not None
    assert db_session.query(ChatLog).count() == 0


def test_release_clean_transaction_refuses_bulk_dml(db_session):
    from core.database import release_clean_session_transaction

    db_session.add(User(id="bulk-dml-user", name="before"))
    db_session.commit()
    db_session.execute(
        update(User)
        .where(User.id == "bulk-dml-user")
        .values(name="after")
    )

    assert list(db_session.new) == []
    assert list(db_session.dirty) == []
    assert release_clean_session_transaction(
        db_session,
        label="test_bulk_dml_guard",
    ) is False
    db_session.commit()
    assert db_session.get(User, "bulk-dml-user").name == "after"


def test_nested_only_rollback_allows_clean_transaction_release(db_session):
    from core.database import release_clean_session_transaction

    db_session.query(User).count()
    with pytest.raises(RuntimeError, match="rollback nested-only write"):
        with db_session.begin_nested():
            db_session.add(ChatLog(
                user_id="nested-only-user",
                role="user",
                content="这条 savepoint 写入会回滚",
            ))
            db_session.flush()
            raise RuntimeError("rollback nested-only write")

    assert db_session.query(ChatLog).count() == 0
    assert release_clean_session_transaction(
        db_session,
        label="test_nested_only_release",
    ) is True
    assert db_session.in_transaction() is False


def test_release_clean_transaction_refuses_commented_text_dml(db_session):
    from core.database import release_clean_session_transaction

    db_session.add(User(id="commented-text-user", name="before"))
    db_session.commit()
    db_session.execute(
        text(
            "-- audit comment\n"
            "UPDATE users SET name = :name WHERE id = :user_id"
        ),
        {"name": "after", "user_id": "commented-text-user"},
    )

    assert release_clean_session_transaction(
        db_session,
        label="test_commented_text_dml_guard",
    ) is False
    db_session.commit()
    assert db_session.get(User, "commented-text-user").name == "after"


@pytest.mark.parametrize(
    "statement",
    (
        "SELECT 1",
        "-- read-only check\nSELECT 1",
        "/* read-only check */\nSELECT 1",
    ),
)
def test_release_clean_transaction_releases_proven_read_only_text(
    db_session,
    statement,
):
    from core.database import release_clean_session_transaction

    assert db_session.execute(text(statement)).scalar() == 1
    assert release_clean_session_transaction(
        db_session,
        label="test_read_only_text_release",
    ) is True
    assert db_session.in_transaction() is False


def test_text_sql_read_only_allowlist_accepts_mixed_leading_comments():
    from core.database import _text_sql_is_proven_read_only

    assert _text_sql_is_proven_read_only(
        "-- first comment\r\n/* second comment */\nSELECT 1"
    ) is True


@pytest.mark.parametrize(
    "statement",
    (
        "SELECT 1;",
        "SELECT 1; UPDATE users SET name = 'unsafe'",
        "WITH item AS (SELECT 1) SELECT * FROM item",
        "PRAGMA foreign_keys",
        "EXPLAIN SELECT 1",
        "",
        "-- comment only",
        "/* unterminated comment",
        "SELECT/* inline comment */ 1",
    ),
)
def test_text_sql_read_only_allowlist_rejects_unknown_forms(statement):
    from core.database import _text_sql_is_proven_read_only

    assert _text_sql_is_proven_read_only(statement) is False

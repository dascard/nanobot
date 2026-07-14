import pytest
from sqlalchemy import create_engine, inspect, text


def _legacy_engine(tmp_path):
    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE chat_stream_configs ("
            "chat_stream_id TEXT PRIMARY KEY, "
            "talk_value FLOAT DEFAULT 0.5, "
            "meta_json TEXT DEFAULT '{}')"
        ))
    return engine


def _insert_configs(engine, rows):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chat_stream_configs(chat_stream_id, talk_value) "
                "VALUES (:chat_stream_id, :talk_value)"
            ),
            [
                {"chat_stream_id": chat_stream_id, "talk_value": talk_value}
                for chat_stream_id, talk_value in rows
            ],
        )


def test_chat_stream_config_model_declares_session_guidance_columns():
    from core.database import ChatStreamConfig

    columns = ChatStreamConfig.__table__.columns

    assert columns["session_guidance"].nullable is False
    assert columns["session_guidance"].default.arg == ""
    assert columns["session_guidance"].server_default is not None
    assert columns["session_guidance_updated_at"].nullable is True

    engine = create_engine("sqlite:///:memory:")
    ChatStreamConfig.__table__.create(engine)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO chat_stream_configs(chat_stream_id) VALUES ('qq:1:private')"
            ))
        with engine.connect() as conn:
            stored = conn.execute(text(
                "SELECT session_guidance, session_guidance_updated_at "
                "FROM chat_stream_configs"
            )).one()
    finally:
        engine.dispose()

    assert stored == ("", None)


def test_session_guidance_columns_migration_is_idempotent(tmp_path):
    from core.schema_migrations import (
        _SESSION_GUIDANCE_COLUMNS_VERSION,
        run_schema_migrations,
    )

    engine = _legacy_engine(tmp_path)
    _insert_configs(engine, [("qq:1:private", 0.5)])
    try:
        run_schema_migrations(engine, db_path=str(tmp_path / "legacy.db"))
        run_schema_migrations(engine, db_path=str(tmp_path / "legacy.db"))

        columns = {
            column["name"]
            for column in inspect(engine).get_columns("chat_stream_configs")
        }
        with engine.connect() as conn:
            version_count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM schema_migrations "
                    "WHERE version = :version"
                ),
                {"version": _SESSION_GUIDANCE_COLUMNS_VERSION},
            ).scalar_one()
            stored = conn.execute(text(
                "SELECT session_guidance, session_guidance_updated_at "
                "FROM chat_stream_configs"
            )).one()
    finally:
        engine.dispose()

    assert {"session_guidance", "session_guidance_updated_at"} <= columns
    assert version_count == 1
    assert stored == ("", None)


def test_identity_migration_renames_uncontested_alias(tmp_path):
    from core.schema_migrations import run_schema_migrations

    engine = _legacy_engine(tmp_path)
    _insert_configs(engine, [("private_456", 0.7)])
    try:
        run_schema_migrations(engine, db_path=str(tmp_path / "legacy.db"))
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT chat_stream_id, talk_value FROM chat_stream_configs"
            )).fetchall()
    finally:
        engine.dispose()

    assert rows == [("qq:456:private", 0.7)]


def test_identity_migration_preserves_alias_when_canonical_conflicts(tmp_path):
    from core.schema_migrations import run_schema_migrations

    engine = _legacy_engine(tmp_path)
    _insert_configs(
        engine,
        [("group_123", 0.2), ("qq:123:group", 0.8)],
    )
    try:
        run_schema_migrations(engine, db_path=str(tmp_path / "legacy.db"))
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT chat_stream_id, talk_value FROM chat_stream_configs "
                "ORDER BY chat_stream_id"
            )).fetchall()
    finally:
        engine.dispose()

    assert rows == [("group_123", 0.2), ("qq:123:group", 0.8)]


@pytest.mark.parametrize(
    "legacy_id",
    ["123", "group_", "private_\x00", "qq:123:group"],
)
def test_identity_migration_preserves_bare_canonical_and_invalid_values(
    tmp_path,
    legacy_id,
):
    from core.schema_migrations import run_schema_migrations

    engine = _legacy_engine(tmp_path)
    _insert_configs(engine, [(legacy_id, 0.4)])
    try:
        run_schema_migrations(engine, db_path=str(tmp_path / "legacy.db"))
        with engine.connect() as conn:
            stored_id = conn.execute(text(
                "SELECT chat_stream_id FROM chat_stream_configs"
            )).scalar_one()
    finally:
        engine.dispose()

    assert stored_id == legacy_id

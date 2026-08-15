from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text


def test_database_only_model_and_migration_default_to_on():
    from core.database import ChatStreamConfig
    from core.schema_migrations import (
        MIGRATIONS,
        _CHAT_STREAM_DATABASE_ONLY_DEFAULT_CONSTRAINT_VERSION,
        _CHAT_STREAM_DATABASE_ONLY_DEFAULT_ENABLED_VERSION,
        _CHAT_STREAM_DATABASE_ONLY_VERSION,
        _chat_stream_database_only_column,
        _chat_stream_database_only_default_constraint,
        _chat_stream_database_only_default_enabled,
    )

    column = ChatStreamConfig.__table__.columns["database_only"]
    assert column.nullable is False
    assert column.default.arg == 1
    assert column.server_default is not None
    assert any(
        version == _CHAT_STREAM_DATABASE_ONLY_VERSION
        for version, _name, _migration in MIGRATIONS
    )
    assert any(
        version == _CHAT_STREAM_DATABASE_ONLY_DEFAULT_ENABLED_VERSION
        for version, _name, _migration in MIGRATIONS
    )
    assert any(
        version == _CHAT_STREAM_DATABASE_ONLY_DEFAULT_CONSTRAINT_VERSION
        for version, _name, _migration in MIGRATIONS
    )

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE chat_stream_configs ("
            "chat_stream_id TEXT PRIMARY KEY)"
        ))
        conn.execute(text(
            "INSERT INTO chat_stream_configs(chat_stream_id) "
            "VALUES ('qq:1:private')"
        ))
        _chat_stream_database_only_column(conn, engine, None)
        _chat_stream_database_only_column(conn, engine, None)
        initial_value = conn.execute(text(
            "SELECT database_only FROM chat_stream_configs"
        )).scalar_one()
        conn.execute(text(
            "UPDATE chat_stream_configs SET database_only = 0"
        ))
        _chat_stream_database_only_default_enabled(conn, engine, None)
        _chat_stream_database_only_default_constraint(conn, engine, None)

    try:
        columns = {
            item["name"]: item
            for item in inspect(engine).get_columns("chat_stream_configs")
        }
        with engine.connect() as conn:
            stored = conn.execute(text(
                "SELECT database_only FROM chat_stream_configs"
            )).scalar_one()
    finally:
        engine.dispose()

    assert "database_only" in columns
    assert str(columns["database_only"]["default"]).strip("()'\" ") == "1"
    assert columns["database_only"]["nullable"] is False
    assert initial_value == 1
    assert stored == 1


def test_database_only_constraint_migration_preserves_explicit_false():
    from core.schema_migrations import (
        _chat_stream_database_only_default_constraint,
    )

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE chat_stream_configs ("
            "chat_stream_id TEXT PRIMARY KEY, "
            "database_only INTEGER NOT NULL DEFAULT 0)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_chat_stream_database_only "
            "ON chat_stream_configs(database_only)"
        ))
        conn.execute(text(
            "INSERT INTO chat_stream_configs(chat_stream_id, database_only) "
            "VALUES ('qq:disabled:private', 0), ('qq:enabled:private', 1)"
        ))
        _chat_stream_database_only_default_constraint(conn, engine, None)
        _chat_stream_database_only_default_constraint(conn, engine, None)
        conn.execute(text(
            "INSERT INTO chat_stream_configs(chat_stream_id) "
            "VALUES ('qq:default:private')"
        ))

    try:
        columns = {
            item["name"]: item
            for item in inspect(engine).get_columns("chat_stream_configs")
        }
        indexes = {
            item["name"]
            for item in inspect(engine).get_indexes("chat_stream_configs")
        }
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT chat_stream_id, database_only "
                "FROM chat_stream_configs ORDER BY chat_stream_id"
            )).fetchall()
    finally:
        engine.dispose()

    assert str(columns["database_only"]["default"]).strip("()'\" ") == "1"
    assert columns["database_only"]["nullable"] is False
    assert "ix_chat_stream_database_only" in indexes
    assert rows == [
        ("qq:default:private", 1),
        ("qq:disabled:private", 0),
        ("qq:enabled:private", 1),
    ]


def test_database_only_runtime_defaults_to_enabled(db_session):
    from app.session_config import is_database_only_enabled

    assert is_database_only_enabled(
        db_session,
        platform="qq",
        chat_type="private",
        session_id="unconfigured-user",
    ) is True


def test_database_only_admin_config_round_trip(
    client,
    db_session,
    monkeypatch,
):
    from core.database import ChatStreamConfig

    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "database-only-token",
    )
    headers = {"Authorization": "Bearer database-only-token"}

    default_response = client.get(
        "/api/v1/admin/configs/qq:db-only:private",
        headers=headers,
    )
    update_response = client.put(
        "/api/v1/admin/configs/qq:db-only:private",
        json={"database_only": False},
        headers=headers,
    )

    assert default_response.status_code == 200
    assert default_response.json()["database_only"] is True
    assert update_response.status_code == 200
    assert update_response.json()["database_only"] is False
    db_session.expire_all()
    assert (
        db_session.get(ChatStreamConfig, "qq:db-only:private").database_only
        == 0
    )


@pytest.mark.database_only_runtime
@pytest.mark.asyncio
async def test_group_database_only_persists_ambient_without_any_model_call(
    db_session,
    monkeypatch,
):
    from api.group_message_routes import GroupMessageRequest
    from app.group_ingress.service import GroupIngressService
    from app.session_memory.windowing import is_context_eligible_chat_log
    from core.database import ChatLog, ChatStreamConfig, ConversationTurn

    db_session.add(ChatStreamConfig(
        chat_stream_id="qq:42:group",
        database_only=1,
    ))
    db_session.commit()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("仅入库会话不应注册模型处理任务或进入 Bridge")

    monkeypatch.setattr(
        "app.group_ingress.helpers.register_group_stickers_from_message",
        forbidden,
    )
    monkeypatch.setattr(
        "core.timing_runtime.get_group_runtime",
        forbidden,
    )
    service = GroupIngressService(
        db=db_session,
        bridge_provider=forbidden,
    )

    payload = await service.handle(GroupMessageRequest(
        group_id="42",
        sender_id="user-1",
        sender_name="测试用户",
        message="这条消息只入库",
        message_id="database-only-group-message",
        client_meta={"platform": "qq", "chat_type": "group"},
    ))

    assert payload["action"] == "no_reply"
    assert payload["reason"] == "database_only"
    assert payload["hard_rule"] == "database_only"
    db_session.expire_all()
    rows = db_session.query(ChatLog).order_by(ChatLog.id).all()
    assert len(rows) == 1
    assert rows[0].role == "ambient"
    assert rows[0].content == "[测试用户]: 这条消息只入库"
    meta = json.loads(rows[0].meta_json)
    assert meta["database_only"] is True
    assert meta["model_invoked"] is False
    assert meta["context_policy"] == "exclude"
    assert meta["no_context_reason"] == "database_only"
    assert meta["timing_gate"]["reason"] == "database_only"
    assert is_context_eligible_chat_log(rows[0]) == (
        False,
        "context_policy_exclude:database_only",
    )
    assert db_session.query(ConversationTurn).count() == 0


@pytest.mark.database_only_runtime
@pytest.mark.asyncio
async def test_group_database_only_cancels_pending_timer_without_model(
    db_session,
    monkeypatch,
):
    from api.group_utility_routes import (
        GroupTimingTimerRequest,
        group_timing_timer,
    )
    from core.database import ChatStreamConfig

    db_session.add(ChatStreamConfig(
        chat_stream_id="qq:timer-only:group",
        database_only=1,
    ))
    db_session.commit()

    def forbidden():
        raise AssertionError("仅入库会话的旧 timer 不应调用 TimingGate")

    monkeypatch.setattr(
        "core.timing_runtime.get_group_runtime",
        forbidden,
    )

    payload = await group_timing_timer(
        GroupTimingTimerRequest(group_id="timer-only", generation=7),
        db_session,
        None,
    )

    assert payload == {
        "action": "no_reply",
        "reason": "database_only",
        "generation": 7,
        "hard_rule": "database_only",
    }


@pytest.mark.database_only_runtime
def test_private_database_only_persists_user_log_without_model_or_turn(
    client,
    db_session,
    monkeypatch,
):
    from api import routes
    from core.database import ChatLog, ChatStreamConfig, ConversationTurn

    db_session.add(ChatStreamConfig(
        chat_stream_id="qq:db-only-user:private",
        database_only=1,
    ))
    db_session.commit()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("仅入库私聊不应构造上下文或调用模型")

    async def forbidden_async(*_args, **_kwargs):
        forbidden()

    monkeypatch.setattr(routes, "_build_structured_chat_context", forbidden)
    monkeypatch.setattr(routes, "_resolve_chat_pre_bridge_decision", forbidden_async)
    monkeypatch.setattr(routes, "get_bridge", forbidden)

    response = client.post(
        "/api/v1/chat",
        json={
            "user_id": "db-only-user",
            "session_id": "private_db-only-user",
            "query": "请只保存这一条",
            "message_id": "database-only-private-message",
            "client_meta": {
                "platform": "qq",
                "chat_type": "private",
            },
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "no_reply"
    assert payload["reason"] == "database_only"
    assert payload["answer"] == ""
    db_session.expire_all()
    user_logs = db_session.query(ChatLog).filter(ChatLog.role == "user").all()
    assert len(user_logs) == 1
    assert user_logs[0].content == "请只保存这一条"
    meta = json.loads(user_logs[0].meta_json)
    assert meta["database_only"] is True
    assert meta["model_invoked"] is False
    assert meta["context_policy"] == "exclude"
    assert meta["no_context_reason"] == "database_only"
    assert db_session.query(ChatLog).filter(ChatLog.role == "assistant").count() == 0
    assert db_session.query(ConversationTurn).count() == 0


def test_session_config_page_exposes_database_only_switch():
    source = Path(
        "webui/src/features/session-config/SessionConfigsPage.jsx"
    ).read_text(encoding="utf-8")

    assert 'id="session-config-database-only"' in source
    assert "仅入库（不调用任何模型）" in source
    assert "database_only: Boolean(form.database_only)" in source
    assert "database_only: config?.database_only ?? true" in source

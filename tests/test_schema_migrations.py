import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError


def test_sqlite_path_from_database_url_respects_configured_database_path(tmp_path):
    from core.database import sqlite_path_from_database_url

    db_path = tmp_path / "custom" / "nanobot.db"

    assert sqlite_path_from_database_url(f"sqlite:///{db_path}") == str(db_path)
    assert sqlite_path_from_database_url("sqlite:///:memory:") is None
    assert sqlite_path_from_database_url("postgresql://example/db") is None


def test_schema_migrations_records_applied_versions():
    from core.schema_migrations import MIGRATIONS, run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE chat_logs (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE conversation_turns (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE users (id TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE agent_runs (id TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE prompt_render_logs (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE llm_api_request_logs (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE reply_contract_check_logs (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE reply_eval_results (id INTEGER PRIMARY KEY)"))
        conn.execute(text(
            "CREATE TABLE group_memories ("
            "id INTEGER PRIMARY KEY, "
            "group_id TEXT, memory_type TEXT, content TEXT, content_hash TEXT, "
            "confidence REAL, evidence_count INTEGER, evidence_log_ids_json TEXT, "
            "decay_score REAL, status TEXT)"
        ))

    run_schema_migrations(engine)
    run_schema_migrations(engine)

    inspector = inspect(engine)
    assert "schema_migrations" in inspector.get_table_names()
    assert "chat_type" in [col["name"] for col in inspector.get_columns("agent_runs")]
    assert "prompt_template_resolutions_json" in [
        col["name"] for col in inspector.get_columns("agent_runs")
    ]
    assert "prompt_template_resolutions_json" in [
        col["name"] for col in inspector.get_columns("prompt_render_logs")
    ]
    assert "response_json" in [col["name"] for col in inspector.get_columns("llm_api_request_logs")]
    reply_contract_columns = [col["name"] for col in inspector.get_columns("reply_contract_check_logs")]
    assert "reply_tool_call_count" in reply_contract_columns
    assert "total_final_action_count" in reply_contract_columns
    assert "rolling_session_summaries" in inspector.get_table_names()
    assert "web_search_provider_usage" in inspector.get_table_names()
    rss_columns = [col["name"] for col in inspector.get_columns("rolling_session_summaries")]
    assert "covered_until_turn_id" in rss_columns
    assert "raw_window_start_turn_id" in rss_columns
    group_memory_columns = [col["name"] for col in inspector.get_columns("group_memories")]
    assert "inject_policy" in group_memory_columns
    assert "last_injected_at" in group_memory_columns
    assert "injected_count" in group_memory_columns
    usage_columns = [col["name"] for col in inspector.get_columns("web_search_provider_usage")]
    assert "total_calls" in usage_columns
    assert "last_error_code" in usage_columns

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version FROM schema_migrations ORDER BY version")).fetchall()

    assert [row[0] for row in rows] == sorted(version for version, _, _ in MIGRATIONS)


def test_prompt_template_resolution_columns_apply_after_legacy_trace_migration():
    from core.schema_migrations import run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE schema_migrations ("
            "version TEXT PRIMARY KEY, name TEXT, "
            "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        conn.execute(text(
            "INSERT INTO schema_migrations(version, name) VALUES "
            "('20260523_agent_prompt_trace_columns', 'legacy trace columns')"
        ))
        conn.execute(text(
            "CREATE TABLE agent_runs ("
            "run_id TEXT PRIMARY KEY, prompt_source TEXT DEFAULT '', "
            "prompt_runtime_path TEXT DEFAULT '', prompt_default_path TEXT DEFAULT '', "
            "prompt_sha256 TEXT DEFAULT '')"
        ))
        conn.execute(text(
            "CREATE TABLE prompt_render_logs ("
            "id INTEGER PRIMARY KEY, prompt_source TEXT DEFAULT '', "
            "prompt_runtime_path TEXT DEFAULT '', prompt_default_path TEXT DEFAULT '', "
            "prompt_sha256 TEXT DEFAULT '')"
        ))
        conn.execute(text(
            "INSERT INTO agent_runs(run_id) VALUES ('legacy-run')"
        ))
        conn.execute(text(
            "INSERT INTO prompt_render_logs(id) VALUES (1)"
        ))

    run_schema_migrations(engine)
    run_schema_migrations(engine)

    inspector = inspect(engine)
    agent_columns = {
        column["name"]: column for column in inspector.get_columns("agent_runs")
    }
    render_columns = {
        column["name"]: column for column in inspector.get_columns("prompt_render_logs")
    }
    assert agent_columns["prompt_template_resolutions_json"]["nullable"] is False
    assert render_columns["prompt_template_resolutions_json"]["nullable"] is False
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT prompt_template_resolutions_json FROM agent_runs "
            "WHERE run_id = 'legacy-run'"
        )).scalar_one() == "{}"
        assert conn.execute(text(
            "SELECT prompt_template_resolutions_json FROM prompt_render_logs WHERE id = 1"
        )).scalar_one() == "{}"
        assert conn.execute(text(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = '20260714_prompt_template_resolution_columns'"
        )).scalar_one() == 1


def test_fresh_prompt_trace_resolution_columns_support_legacy_writers():
    from core.database import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    agent_columns = {
        column["name"]: column for column in inspector.get_columns("agent_runs")
    }
    render_columns = {
        column["name"]: column for column in inspector.get_columns("prompt_render_logs")
    }
    assert agent_columns["prompt_template_resolutions_json"]["nullable"] is False
    assert agent_columns["prompt_template_resolutions_json"]["default"] is not None
    assert render_columns["prompt_template_resolutions_json"]["nullable"] is False
    assert render_columns["prompt_template_resolutions_json"]["default"] is not None

    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO agent_runs(run_id) VALUES ('fresh-legacy-run')"
        ))
        conn.execute(text("INSERT INTO prompt_render_logs DEFAULT VALUES"))
        assert conn.execute(text(
            "SELECT prompt_template_resolutions_json FROM agent_runs "
            "WHERE run_id = 'fresh-legacy-run'"
        )).scalar_one() == "{}"
        assert conn.execute(text(
            "SELECT prompt_template_resolutions_json FROM prompt_render_logs"
        )).scalar_one() == "{}"


def test_session_guidance_migrations_are_registered_in_dependency_order():
    from core.schema_migrations import (
        MIGRATIONS,
        _CHAT_STREAM_IDENTITY_VERSION,
        _SESSION_GUIDANCE_COLUMNS_VERSION,
    )

    versions = [version for version, _, _ in MIGRATIONS]

    assert versions.count(_SESSION_GUIDANCE_COLUMNS_VERSION) == 1
    assert versions.count(_CHAT_STREAM_IDENTITY_VERSION) == 1
    assert versions.index(_SESSION_GUIDANCE_COLUMNS_VERSION) < versions.index(
        _CHAT_STREAM_IDENTITY_VERSION
    )


def test_non_sqlite_identity_migration_does_not_require_sqlite_snapshot(
    monkeypatch,
):
    from core import schema_migrations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE chat_stream_configs ("
            "chat_stream_id TEXT PRIMARY KEY, talk_value FLOAT DEFAULT 0.5)"
        ))
        conn.execute(text(
            "INSERT INTO chat_stream_configs(chat_stream_id) VALUES ('group_123')"
        ))
    engine.url = make_url("postgresql://user:password@example.invalid/nanobot")
    monkeypatch.setattr(
        schema_migrations,
        "create_sqlite_snapshot",
        lambda *_args, **_kwargs: pytest.fail("非 SQLite 不应创建文件快照"),
    )

    try:
        schema_migrations.run_schema_migrations(engine)
        with engine.connect() as conn:
            stored_id = conn.execute(text(
                "SELECT chat_stream_id FROM chat_stream_configs"
            )).scalar_one()
    finally:
        engine.dispose()

    assert stored_id == "qq:123:group"


def test_proactive_outreach_log_table_is_created_by_migration():
    from core.schema_migrations import run_schema_migrations

    engine = create_engine("sqlite:///:memory:")

    run_schema_migrations(engine)

    inspector = inspect(engine)
    assert "proactive_outreach_log" in inspector.get_table_names()
    columns = {col["name"] for col in inspector.get_columns("proactive_outreach_log")}
    assert {
        "id",
        "user_id",
        "idempotency_key",
        "grounding_json",
        "judge_should",
        "judge_reason",
        "next_check_at",
        "next_intent",
        "message",
        "status",
        "forced",
        "created_at",
    } <= columns

    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO proactive_outreach_log "
            "(user_id, idempotency_key, grounding_json, status) "
            "VALUES ('superuser', 'outreach:once', '{}', 'pending')"
        ))
        with pytest.raises(IntegrityError):
            conn.execute(text(
                "INSERT INTO proactive_outreach_log "
                "(user_id, idempotency_key, grounding_json, status) "
                "VALUES ('superuser', 'outreach:once', '{}', 'pending')"
            ))


def test_super_user_config_cleanup_removes_setting_and_redacts_audit():
    from core.schema_migrations import run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE system_settings ("
            "key TEXT PRIMARY KEY, value TEXT, description TEXT, updated_at DATETIME)"
        ))
        conn.execute(text(
            "CREATE TABLE admin_audit_logs ("
            "id INTEGER PRIMARY KEY, action TEXT NOT NULL, target_type TEXT, "
            "target_id TEXT, detail_json TEXT, ip_address TEXT, created_at DATETIME)"
        ))
        conn.execute(text(
            "INSERT INTO system_settings(key, value, description) "
            "VALUES ('bot.super_user_ids', '0000000000', 'legacy target')"
        ))
        conn.execute(text(
            "INSERT INTO admin_audit_logs"
            "(action, target_type, target_id, detail_json) VALUES "
            "('update_proactive_outreach_setting', 'setting', "
            "'bot.super_user_ids', '{\"value\":\"0000000000\"}')"
        ))
        conn.execute(text(
            "INSERT INTO admin_audit_logs"
            "(action, target_type, target_id, detail_json) VALUES "
            "('other', 'setting', 'other.key', '{\"value\":\"keep\"}')"
        ))

    run_schema_migrations(engine)
    run_schema_migrations(engine)

    with engine.connect() as conn:
        setting_count = conn.execute(text(
            "SELECT COUNT(*) FROM system_settings "
            "WHERE key = 'bot.super_user_ids'"
        )).scalar_one()
        redacted = conn.execute(text(
            "SELECT detail_json FROM admin_audit_logs "
            "WHERE target_id = 'bot.super_user_ids'"
        )).scalar_one()
        untouched = conn.execute(text(
            "SELECT detail_json FROM admin_audit_logs WHERE target_id = 'other.key'"
        )).scalar_one()
        version_count = conn.execute(text(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = '20260712_super_user_config_cleanup'"
        )).scalar_one()

    assert setting_count == 0
    assert redacted == '{"changed":true,"redacted":true}'
    assert untouched == '{"value":"keep"}'
    assert version_count == 1


def test_group_memory_governance_columns_apply_when_old_group_memory_migration_already_recorded():
    from core.schema_migrations import run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE schema_migrations ("
            "version TEXT PRIMARY KEY, name TEXT, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        conn.execute(text(
            "INSERT INTO schema_migrations(version, name) "
            "VALUES ('20260523_group_memory_columns', 'group memory columns')"
        ))
        conn.execute(text(
            "CREATE TABLE group_memories ("
            "id INTEGER PRIMARY KEY, "
            "group_id TEXT, memory_type TEXT, content TEXT, content_hash TEXT, "
            "confidence REAL, evidence_count INTEGER, evidence_log_ids_json TEXT, "
            "decay_score REAL, status TEXT)"
        ))

    run_schema_migrations(engine)

    columns = [col["name"] for col in inspect(engine).get_columns("group_memories")]
    assert "inject_policy" in columns
    assert "last_injected_at" in columns
    assert "injected_count" in columns


def test_session_summary_llm_columns_apply_when_old_rolling_migration_already_recorded():
    from core.schema_migrations import run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE schema_migrations ("
            "version TEXT PRIMARY KEY, name TEXT, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        conn.execute(text(
            "INSERT INTO schema_migrations(version, name) "
            "VALUES ('20260525_rolling_session_summaries', 'rolling session summaries')"
        ))
        conn.execute(text(
            "CREATE TABLE rolling_session_summaries ("
            "id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, user_id TEXT, chat_type TEXT, "
            "status TEXT, summary_text TEXT, summary_json TEXT, covered_from_turn_id INTEGER, "
            "covered_until_turn_id INTEGER, source_turn_ids_json TEXT, source_turn_count INTEGER, "
            "source_token_estimate INTEGER, source_char_count INTEGER, raw_window_start_turn_id INTEGER, "
            "quality_score REAL, issues_json TEXT, model TEXT, prompt_sha256 TEXT, meta_json TEXT, "
            "created_at DATETIME, updated_at DATETIME)"
        ))

    run_schema_migrations(engine)

    columns = [col["name"] for col in inspect(engine).get_columns("rolling_session_summaries")]
    assert "summary_kind" in columns
    assert "llm_status" in columns
    assert "retry_count" in columns
    assert "stable_hash" in columns


def test_session_summary_jobs_table_is_created_by_migration():
    from core.schema_migrations import run_schema_migrations

    engine = create_engine("sqlite:///:memory:")

    run_schema_migrations(engine)

    inspector = inspect(engine)
    assert "session_summary_jobs" in inspector.get_table_names()
    columns = [col["name"] for col in inspector.get_columns("session_summary_jobs")]
    assert "session_id" in columns
    assert "fallback_summary_id" in columns
    assert "result_summary_id" in columns
    assert "next_retry_at" in columns


def test_runtime_tool_decision_platform_column_added_to_existing_table(tmp_path):
    from core.schema_migrations import run_schema_migrations

    db_path = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE runtime_tool_decisions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id TEXT, "
            "message_id TEXT, "
            "chat_type TEXT, "
            "group_id TEXT, "
            "user_id TEXT, "
            "runtime_preset TEXT, "
            "enabled_tools_json TEXT, "
            "disabled_tools_json TEXT, "
            "disabled_reasons_json TEXT, "
            "effective_tools_json TEXT, "
            "created_at DATETIME"
            ")"
        ))

    run_schema_migrations(engine, db_path=str(db_path))

    columns = {col["name"] for col in inspect(engine).get_columns("runtime_tool_decisions")}
    assert "platform" in columns

    run_schema_migrations(engine, db_path=str(db_path))
    columns_again = {col["name"] for col in inspect(engine).get_columns("runtime_tool_decisions")}
    assert "platform" in columns_again


def test_persona_fact_governance_columns_are_added_to_existing_table():
    from core.schema_migrations import run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE persona_facts ("
            "id INTEGER PRIMARY KEY, user_id TEXT, domain_primary TEXT, content TEXT, "
            "evidence_count INTEGER, source_log_ids TEXT, confidence TEXT, fact_type TEXT, "
            "first_seen TIMESTAMP, last_seen TIMESTAMP)"
        ))
        conn.execute(text(
            "INSERT INTO persona_facts "
            "(id, user_id, domain_primary, content, evidence_count, source_log_ids, confidence, fact_type) "
            "VALUES "
            "(1, 'u1', 'general', '用户喜欢简洁回复', 3, '[\"用户说喜欢简洁\"]', '确认', 'preference'), "
            "(2, 'u1', 'general', '用户偶尔问天气', 1, '[\"文本证据\"]', '可能', 'preference')"
        ))

    run_schema_migrations(engine)

    columns = [col["name"] for col in inspect(engine).get_columns("persona_facts")]
    assert "status" in columns
    assert "inject_policy" in columns
    assert "memory_type" in columns
    assert "content_hash" in columns
    assert "evidence_log_ids_json" in columns
    assert "candidate_meta_json" in columns
    assert "last_injected_at" in columns
    assert "injected_count" in columns

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, status, inject_policy, memory_type, content_hash, evidence_log_ids_json "
            "FROM persona_facts ORDER BY id"
        )).fetchall()

    assert rows[0].status == "active"
    assert rows[0].inject_policy == "auto"
    assert rows[0].memory_type == "stable_preference"
    assert rows[0].content_hash
    assert rows[0].evidence_log_ids_json == "[]"
    assert rows[1].status == "review"
    assert rows[1].inject_policy == "manual_only"

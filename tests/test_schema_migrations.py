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


def test_summary_model_safe_defaults_migration_is_exact_and_one_time():
    from core.schema_migrations import run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE system_settings ("
            '"key" TEXT PRIMARY KEY, value TEXT, description TEXT, updated_at DATETIME)'
        ))
        conn.execute(text(
            "INSERT INTO system_settings(\"key\", value) VALUES "
            "('model.route.session_summary.temperature', '1.0'),"
            "('model.route.session_summary.max_tokens', '65535'),"
            "('model.route.session_summary.enable_thinking', 'true'),"
            "('model.route.memory_digest.temperature', '1.0'),"
            "('model.route.memory_digest.max_tokens', '65535'),"
            "('model.route.memory_digest.enable_thinking', 'true'),"
            "('model.route.reply.temperature', '0.7')"
        ))

    run_schema_migrations(engine)

    with engine.begin() as conn:
        rows = dict(conn.execute(text(
            "SELECT \"key\", value FROM system_settings "
            "WHERE \"key\" LIKE 'model.route.%'"
        )).fetchall())
        conn.execute(text(
            "UPDATE system_settings SET value = '0.2' "
            "WHERE \"key\" = 'model.route.session_summary.temperature'"
        ))

    assert rows["model.route.session_summary.temperature"] == "0.1"
    assert rows["model.route.session_summary.max_tokens"] == "1200"
    assert rows["model.route.session_summary.enable_thinking"] == "false"
    assert rows["model.route.memory_digest.temperature"] == "0.1"
    assert rows["model.route.memory_digest.max_tokens"] == "1800"
    assert rows["model.route.memory_digest.enable_thinking"] == "false"
    assert rows["model.route.reply.temperature"] == "0.7"

    run_schema_migrations(engine)
    with engine.connect() as conn:
        value = conn.execute(text(
            "SELECT value FROM system_settings "
            "WHERE \"key\" = 'model.route.session_summary.temperature'"
        )).scalar_one()
    assert value == "0.2"


def test_memory_digest_jobs_migration_is_idempotent_and_enforces_source_key():
    from core.schema_migrations import run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE memory_digests ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "digest_date TEXT, content TEXT, meta_json TEXT)"
        ))
        conn.execute(text(
            "INSERT INTO memory_digests(id, digest_date, content, meta_json) "
            "VALUES (7, '2026-07-17', '历史摘要正文', '{\"status\":\"active\"}')"
        ))

    run_schema_migrations(engine)
    run_schema_migrations(engine)

    inspector = inspect(engine)
    assert "memory_digest_jobs" in inspector.get_table_names()
    columns = {
        column["name"]
        for column in inspector.get_columns("memory_digest_jobs")
    }
    assert {
        "session_id",
        "digest_date",
        "source_revision",
        "status",
        "lease_token",
        "lease_expires_at",
        "attempt_count",
        "retry_count",
        "error_type",
        "error_summary",
        "result_digest_count",
        "result_source_id",
        "result_root_digest_id",
        "result_semantic_job_id",
    } <= columns
    indexes = {
        row["name"]: tuple(row["column_names"])
        for row in inspector.get_indexes("memory_digest_jobs")
    }
    assert indexes["idx_memory_digest_job_claim"] == (
        "status",
        "lease_expires_at",
        "id",
    )
    digest_columns = {
        column["name"]
        for column in inspector.get_columns("memory_digests")
    }
    assert "generation_job_id" in digest_columns
    digest_indexes = {
        row["name"]: tuple(row["column_names"])
        for row in inspector.get_indexes("memory_digests")
    }
    assert digest_indexes["ix_memory_digests_generation_job_id"] == (
        "generation_job_id",
    )
    with engine.connect() as conn:
        historical = conn.execute(text(
            "SELECT id, digest_date, content, meta_json, generation_job_id "
            "FROM memory_digests WHERE id = 7"
        )).mappings().one()
    assert dict(historical) == {
        "id": 7,
        "digest_date": "2026-07-17",
        "content": "历史摘要正文",
        "meta_json": '{"status":"active"}',
        "generation_job_id": None,
    }

    with engine.begin() as conn:
        values = {
            "session_id": "session-1",
            "digest_date": "2026-07-18",
        }
        conn.execute(
            text(
                "INSERT INTO memory_digest_jobs(session_id, digest_date) "
                "VALUES (:session_id, :digest_date)"
            ),
            values,
        )
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO memory_digest_jobs(session_id, digest_date) "
                    "VALUES (:session_id, :digest_date)"
                ),
                values,
            )


def test_semantic_index_reconcile_v2_migrates_legacy_jobs_idempotently():
    from core.schema_migrations import run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE semantic_index_items ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source_type TEXT NOT NULL DEFAULT '', "
            "source_id TEXT NOT NULL DEFAULT '', "
            "source_sub_id TEXT NOT NULL DEFAULT '', "
            "index_version TEXT DEFAULT '', "
            "visibility TEXT DEFAULT 'recall', "
            "embedding_status TEXT DEFAULT 'pending', "
            "status TEXT DEFAULT 'active')"
        ))
        conn.execute(text(
            "CREATE TABLE semantic_index_jobs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source_type TEXT NOT NULL DEFAULT '', "
            "source_id TEXT NOT NULL DEFAULT '', "
            "source_sub_id TEXT DEFAULT '', "
            "job_type TEXT DEFAULT 'upsert', "
            "index_version TEXT DEFAULT '', "
            "status TEXT DEFAULT 'pending', "
            "retry_count INTEGER DEFAULT 0, "
            "max_retry INTEGER DEFAULT 3, "
            "next_retry_at DATETIME, "
            "locked_by TEXT DEFAULT '', "
            "locked_at DATETIME, "
            "error TEXT DEFAULT '', "
            "created_at DATETIME, "
            "updated_at DATETIME, "
            "finished_at DATETIME)"
        ))
        conn.execute(text(
            "INSERT INTO semantic_index_jobs "
            "(id, source_type, source_id, status, retry_count, max_retry, "
            "locked_by, locked_at, error, next_retry_at, finished_at) VALUES "
            "(1, 'memory_digest', '11', 'running', 1, 3, "
            "'legacy-worker', '2026-07-17 01:00:00', 'legacy-running', NULL, NULL), "
            "(2, 'session_summary', 's1', 'failed', 2, 3, '', NULL, "
            "'temporary-provider-error', '2026-07-17 02:00:00', NULL), "
            "(3, 'session_summary', 'terminal', 'failed', 2, 3, '', NULL, "
            "'terminal-provider-error', '2026-07-17 02:30:00', "
            "'2026-07-17 02:40:00')"
        ))

    run_schema_migrations(engine)
    run_schema_migrations(engine)

    inspector = inspect(engine)
    job_columns = {
        column["name"]
        for column in inspector.get_columns("semantic_index_jobs")
    }
    item_columns = {
        column["name"]
        for column in inspector.get_columns("semantic_index_items")
    }
    assert {
        "lease_token",
        "lease_expires_at",
        "attempt_count",
        "manual_retry_count",
        "source_revision",
        "meta_json",
    } <= job_columns
    assert "source_revision" in item_columns

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, status, retry_count, locked_by, locked_at, error, "
            "lease_token, lease_expires_at, next_retry_at, finished_at "
            "FROM semantic_index_jobs ORDER BY id"
        )).mappings().all()
        migration_count = conn.execute(text(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = '20260717_semantic_index_reconcile_v2'"
        )).scalar_one()
        index_names = {
            row[0]
            for row in conn.execute(text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' AND name LIKE 'idx_semantic_%_v2'"
            )).fetchall()
        }

    assert rows[0]["status"] == "pending"
    assert rows[0]["retry_count"] == 1
    assert rows[0]["locked_by"] == ""
    assert rows[0]["locked_at"] is None
    assert rows[0]["lease_token"] == ""
    assert rows[0]["lease_expires_at"] is None
    assert rows[0]["error"] == "migration_requeued_legacy_running"
    assert rows[1]["status"] == "pending"
    assert rows[1]["retry_count"] == 2
    assert rows[1]["next_retry_at"] is not None
    assert rows[2]["status"] == "failed"
    assert rows[2]["error"] == "terminal-provider-error"
    assert rows[2]["finished_at"] is not None
    assert migration_count == 1
    assert index_names == {
        "idx_semantic_job_claim_v2",
        "idx_semantic_job_lease_v2",
        "idx_semantic_job_source_revision_v2",
        "idx_semantic_item_source_revision_v2",
    }

    from core.database import SemanticIndexItem, SemanticIndexJob

    orm_indexes = {
        index.name: tuple(column.name for column in index.columns)
        for table in (SemanticIndexJob.__table__, SemanticIndexItem.__table__)
        for index in table.indexes
        if index.name and index.name.endswith("_v2")
    }
    assert orm_indexes == {
        "idx_semantic_job_claim_v2": ("status", "next_retry_at", "id"),
        "idx_semantic_job_lease_v2": ("status", "lease_expires_at", "id"),
        "idx_semantic_job_source_revision_v2": (
            "source_type",
            "source_id",
            "index_version",
            "source_revision",
            "status",
        ),
        "idx_semantic_item_source_revision_v2": (
            "source_type",
            "source_id",
            "source_revision",
            "status",
        ),
    }
    assert "ix_semantic_index_jobs_source_revision" not in {
        index.name for index in SemanticIndexJob.__table__.indexes
    }
    assert "ix_semantic_index_items_source_revision" not in {
        index.name for index in SemanticIndexItem.__table__.indexes
    }


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


def test_memory_cleanup_governance_adds_archive_state_and_run_ledger():
    from core.schema_migrations import _memory_cleanup_governance

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE personas ("
            "user_id TEXT PRIMARY KEY, persona_json TEXT, updated_at DATETIME)"
        ))
        conn.execute(text(
            "CREATE TABLE persona_behaviors ("
            "id INTEGER PRIMARY KEY, user_id TEXT, pattern TEXT)"
        ))
        _memory_cleanup_governance(conn, engine, None)
        _memory_cleanup_governance(conn, engine, None)

    inspector = inspect(engine)
    persona_columns = {item["name"] for item in inspector.get_columns("personas")}
    behavior_columns = {
        item["name"] for item in inspector.get_columns("persona_behaviors")
    }
    assert {"status", "archive_meta_json"}.issubset(persona_columns)
    assert {"status", "archive_meta_json"}.issubset(behavior_columns)
    assert "memory_cleanup_runs" in inspector.get_table_names()
    indexes = {item["name"] for item in inspector.get_indexes("memory_cleanup_runs")}
    assert "idx_memory_cleanup_run_status" in indexes

    with engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO personas(user_id, persona_json) VALUES ('u1', '{}')"
        ))
        row = conn.execute(text(
            "SELECT status, archive_meta_json FROM personas WHERE user_id='u1'"
        )).one()
    assert row.status == "active"
    assert row.archive_meta_json == "{}"

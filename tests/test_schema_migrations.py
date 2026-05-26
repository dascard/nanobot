from sqlalchemy import create_engine, inspect, text


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
    assert "response_json" in [col["name"] for col in inspector.get_columns("llm_api_request_logs")]
    assert "rolling_session_summaries" in inspector.get_table_names()
    rss_columns = [col["name"] for col in inspector.get_columns("rolling_session_summaries")]
    assert "covered_until_turn_id" in rss_columns
    assert "raw_window_start_turn_id" in rss_columns
    group_memory_columns = [col["name"] for col in inspector.get_columns("group_memories")]
    assert "inject_policy" in group_memory_columns
    assert "last_injected_at" in group_memory_columns
    assert "injected_count" in group_memory_columns

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version FROM schema_migrations ORDER BY version")).fetchall()

    assert [row[0] for row in rows] == sorted(version for version, _, _ in MIGRATIONS)


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

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
    group_memory_columns = [col["name"] for col in inspector.get_columns("group_memories")]
    assert "inject_policy" in group_memory_columns
    assert "last_injected_at" in group_memory_columns
    assert "injected_count" in group_memory_columns

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version FROM schema_migrations ORDER BY version")).fetchall()

    assert [row[0] for row in rows] == sorted(version for version, _, _ in MIGRATIONS)

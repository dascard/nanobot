from __future__ import annotations

from sqlalchemy import create_engine, inspect, text


def test_selfcheck_schema_migration_is_idempotent_and_indexed():
    from core.schema_migrations import (
        MIGRATIONS,
        _SELFCHECK_RUNTIME_V1_VERSION,
        _selfcheck_runtime_v1,
    )

    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.execute(text("PRAGMA foreign_keys=ON"))
            _selfcheck_runtime_v1(conn, engine, None)
            _selfcheck_runtime_v1(conn, engine, None)

        schema = inspect(engine)
        assert {
            "selfcheck_runs",
            "selfcheck_results",
            "worker_heartbeats",
        } <= set(schema.get_table_names())
        assert {
            "ix_selfcheck_result_check_time",
            "ix_selfcheck_result_status_time",
        } <= {item["name"] for item in schema.get_indexes("selfcheck_results")}
        assert "ix_worker_heartbeat_seen" in {
            item["name"] for item in schema.get_indexes("worker_heartbeats")
        }
        assert MIGRATIONS[-1][0] == _SELFCHECK_RUNTIME_V1_VERSION
    finally:
        engine.dispose()


def test_selfcheck_result_rows_cascade_with_run():
    from core.schema_migrations import _selfcheck_runtime_v1

    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.execute(text("PRAGMA foreign_keys=ON"))
            _selfcheck_runtime_v1(conn, engine, None)
            conn.execute(text(
                "INSERT INTO selfcheck_runs ("
                "run_id, trigger, environment, capability_registry_sha256, "
                "probe_registry_sha256) VALUES ("
                "'sc_test', 'manual', 'ci', :sha, :sha)"
            ), {"sha": "0" * 64})
            conn.execute(text(
                "INSERT INTO selfcheck_results ("
                "run_id, check_id, category, status, severity, duration_ms, "
                "detail_code, started_at, completed_at) VALUES ("
                "'sc_test', 'database.connectivity', 'database', 'passed', "
                "'critical', 0, 'database_connectivity_ok', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ))
            conn.execute(text("DELETE FROM selfcheck_runs WHERE run_id = 'sc_test'"))
            remaining = int(conn.execute(text(
                "SELECT COUNT(*) FROM selfcheck_results"
            )).scalar_one())
        assert remaining == 0
    finally:
        engine.dispose()

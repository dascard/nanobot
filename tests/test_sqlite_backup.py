from __future__ import annotations

import os
import shutil
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime as RealDateTime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import OperationalError

from tests.http_test_utils import open_test_client_without_lifespan


def _create_database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, content TEXT NOT NULL)")
        conn.execute("INSERT INTO messages(content) VALUES ('snapshot row')")
        conn.commit()


def _create_legacy_chat_database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE chat_logs (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE conversation_turns (id INTEGER PRIMARY KEY)")
        conn.commit()


def _create_legacy_outbound_database(path: Path, *, include_chat_tables: bool = False) -> None:
    with closing(sqlite3.connect(path)) as conn:
        if include_chat_tables:
            conn.execute("CREATE TABLE chat_logs (id INTEGER PRIMARY KEY)")
            conn.execute("CREATE TABLE conversation_turns (id INTEGER PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE scheduled_tasks ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR, cron_expr VARCHAR, "
            "target_type VARCHAR DEFAULT 'private', target_id VARCHAR, "
            "prompt_template TEXT, enabled INTEGER DEFAULT 1, "
            "last_run_at DATETIME, created_at DATETIME)"
        )
        conn.execute(
            "INSERT INTO scheduled_tasks (name, last_run_at) "
            "VALUES ('legacy', '2026-07-13 08:00:00')"
        )
        conn.execute(
            "CREATE TABLE proactive_outreach_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id VARCHAR, "
            "idempotency_key VARCHAR UNIQUE, grounding_json TEXT DEFAULT '{}', "
            "judge_should BOOLEAN DEFAULT 0, judge_reason TEXT DEFAULT '', "
            "next_check_at DATETIME, next_intent TEXT DEFAULT '', "
            "message TEXT DEFAULT '', status VARCHAR DEFAULT 'pending', "
            "forced BOOLEAN DEFAULT 0, created_at DATETIME)"
        )
        conn.execute(
            "INSERT INTO proactive_outreach_log "
            "(user_id, idempotency_key, status) "
            "VALUES ('opaque-user', 'legacy-outreach', 'sending')"
        )
        conn.commit()


def _create_legacy_chat_stream_database(
    path: Path,
    rows: list[tuple[str, float]],
) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            "CREATE TABLE chat_stream_configs ("
            "chat_stream_id TEXT PRIMARY KEY, "
            "talk_value FLOAT DEFAULT 0.5, "
            "meta_json TEXT DEFAULT '{}')"
        )
        conn.executemany(
            "INSERT INTO chat_stream_configs(chat_stream_id, talk_value) "
            "VALUES (?, ?)",
            rows,
        )
        conn.commit()


def _database_schema(path: Path) -> list[tuple[str, str]]:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()


def test_snapshot_contains_committed_wal_rows_and_passes_integrity_check(tmp_path):
    from core.sqlite_backup import create_sqlite_snapshot

    source_path = tmp_path / "wal-source.db"
    bare_copy_path = tmp_path / "bare-main.db"
    source_conn = sqlite3.connect(source_path)
    snapshot_path: Path | None = None
    try:
        assert source_conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        source_conn.execute("PRAGMA wal_autocheckpoint=0")
        source_conn.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, content TEXT NOT NULL)"
        )
        source_conn.commit()
        source_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        source_conn.execute("INSERT INTO messages(content) VALUES ('wal-only row')")
        source_conn.commit()

        wal_path = Path(f"{source_path}-wal")
        assert wal_path.exists() and wal_path.stat().st_size > 0

        # 对照组只复制主文件，证明未 checkpoint 的已提交行仍只在 WAL 中。
        shutil.copyfile(source_path, bare_copy_path)
        with closing(sqlite3.connect(bare_copy_path)) as bare_conn:
            assert bare_conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0

        snapshot_path = create_sqlite_snapshot(source_path, temp_dir=tmp_path)
    finally:
        source_conn.close()

    assert snapshot_path is not None
    try:
        assert snapshot_path.parent == tmp_path
        assert snapshot_path != source_path
        with closing(sqlite3.connect(snapshot_path)) as snapshot_conn:
            assert snapshot_conn.execute("SELECT content FROM messages").fetchall() == [
                ("wal-only row",)
            ]
            assert snapshot_conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        snapshot_path.unlink(missing_ok=True)


def test_snapshot_supports_explicit_target(tmp_path):
    from core.sqlite_backup import create_sqlite_snapshot

    source_path = tmp_path / "source.db"
    target_path = tmp_path / "explicit-backup.db"
    _create_database(source_path)

    result = create_sqlite_snapshot(source_path, target_path)

    assert result == target_path
    with closing(sqlite3.connect(target_path)) as conn:
        assert conn.execute("SELECT content FROM messages").fetchone()[0] == "snapshot row"


def test_snapshot_rejects_same_source_and_target_or_existing_target(tmp_path):
    from core.sqlite_backup import create_sqlite_snapshot

    source_path = tmp_path / "source.db"
    existing_target = tmp_path / "existing.db"
    _create_database(source_path)
    existing_target.write_bytes(b"must stay unchanged")

    with pytest.raises(ValueError, match="different"):
        create_sqlite_snapshot(source_path, source_path.resolve())
    with pytest.raises(FileExistsError):
        create_sqlite_snapshot(source_path, existing_target)

    assert existing_target.read_bytes() == b"must stay unchanged"


def test_snapshot_rejects_missing_source_without_creating_target(tmp_path):
    from core.sqlite_backup import create_sqlite_snapshot

    target_path = tmp_path / "unused.db"

    with pytest.raises(FileNotFoundError):
        create_sqlite_snapshot(tmp_path / "missing.db", target_path)

    assert not target_path.exists()


def test_snapshot_failure_removes_explicit_and_temporary_partial_files(tmp_path):
    from core.sqlite_backup import create_sqlite_snapshot

    invalid_source = tmp_path / "invalid.db"
    explicit_target = tmp_path / "partial.db"
    invalid_source.write_bytes(b"this is not a sqlite database")

    with pytest.raises(sqlite3.Error):
        create_sqlite_snapshot(invalid_source, explicit_target)
    assert not explicit_target.exists()

    before = set(tmp_path.iterdir())
    with pytest.raises(sqlite3.Error):
        create_sqlite_snapshot(invalid_source, temp_dir=tmp_path)
    assert set(tmp_path.iterdir()) == before


def test_temporary_snapshot_paths_are_unique_across_consecutive_calls(tmp_path):
    from core.sqlite_backup import create_sqlite_snapshot

    source_path = tmp_path / "source.db"
    _create_database(source_path)
    snapshots: list[Path] = []
    try:
        snapshots.append(create_sqlite_snapshot(source_path, temp_dir=tmp_path))
        snapshots.append(create_sqlite_snapshot(source_path, temp_dir=tmp_path))

        assert snapshots[0] != snapshots[1]
        assert all(path.exists() for path in snapshots)
    finally:
        for path in snapshots:
            path.unlink(missing_ok=True)


def test_snapshot_source_connection_uses_read_only_uri_without_immutable(
    tmp_path,
    monkeypatch,
):
    from core import sqlite_backup

    source_path = tmp_path / "source.db"
    target_path = tmp_path / "target.db"
    _create_database(source_path)
    real_connect = sqlite3.connect
    calls: list[tuple[object, tuple, dict]] = []

    def recording_connect(database, *args, **kwargs):
        calls.append((database, args, dict(kwargs)))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite_backup.sqlite3, "connect", recording_connect)

    sqlite_backup.create_sqlite_snapshot(source_path, target_path)

    source_database, source_args, source_kwargs = calls[0]
    expected_uri = f"{source_path.resolve().as_uri()}?mode=ro"
    assert source_database == expected_uri
    assert source_args == ()
    assert source_kwargs == {"uri": True}
    assert "immutable" not in str(source_database)
    with closing(real_connect(target_path)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_snapshot_backup_uses_bounded_pages_and_sleep(tmp_path, monkeypatch):
    from core import sqlite_backup

    source_path = tmp_path / "source.db"
    target_path = tmp_path / "target.db"
    _create_database(source_path)
    real_connect = sqlite3.connect
    backup_calls: list[tuple[tuple, dict]] = []

    class RecordingSourceConnection:
        def __init__(self, connection):
            self.connection = connection

        def backup(self, target, *args, **kwargs):
            backup_calls.append((args, dict(kwargs)))
            return self.connection.backup(target, *args, **kwargs)

        def close(self):
            self.connection.close()

    def recording_connect(database, *args, **kwargs):
        connection = real_connect(database, *args, **kwargs)
        if kwargs.get("uri") is True:
            return RecordingSourceConnection(connection)
        return connection

    monkeypatch.setattr(sqlite_backup.sqlite3, "connect", recording_connect)

    sqlite_backup.create_sqlite_snapshot(source_path, target_path)

    assert backup_calls == [((), {"pages": 256, "sleep": 0.05})]
    with closing(real_connect(target_path)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_snapshot_cleanup_preserves_original_error_and_attempts_every_sidecar(
    tmp_path,
    monkeypatch,
):
    from core import sqlite_backup

    source_path = tmp_path / "source.db"
    target_path = tmp_path / "partial.db"
    _create_database(source_path)
    real_connect = sqlite3.connect
    real_unlink = Path.unlink
    cleanup_paths = [
        target_path,
        Path(f"{target_path}-journal"),
        Path(f"{target_path}-wal"),
        Path(f"{target_path}-shm"),
    ]
    attempted_paths: list[Path] = []

    class FailingSourceConnection:
        def __init__(self, connection):
            self.connection = connection

        def backup(self, _target, *args, **kwargs):
            for sidecar_path in cleanup_paths[1:]:
                sidecar_path.write_bytes(b"partial sidecar")
            raise sqlite3.OperationalError("original backup failure")

        def close(self):
            self.connection.close()

    def failing_connect(database, *args, **kwargs):
        connection = real_connect(database, *args, **kwargs)
        if kwargs.get("uri") is True:
            return FailingSourceConnection(connection)
        return connection

    def recording_unlink(path, *args, **kwargs):
        attempted_paths.append(path)
        if path == target_path:
            raise OSError("first cleanup failed")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(sqlite_backup.sqlite3, "connect", failing_connect)
    monkeypatch.setattr(Path, "unlink", recording_unlink)

    try:
        with pytest.raises(sqlite3.OperationalError, match="original backup failure"):
            sqlite_backup.create_sqlite_snapshot(source_path, target_path)

        assert attempted_paths == cleanup_paths
        assert target_path.exists()
        assert all(not path.exists() for path in cleanup_paths[1:])
    finally:
        real_unlink(target_path, missing_ok=True)


def test_admin_backup_returns_snapshot_and_removes_temporary_file(
    tmp_path,
    monkeypatch,
):
    import config
    from api import admin_routes
    from core.sqlite_backup import create_sqlite_snapshot
    from server import app

    source_path = tmp_path / "admin-source.db"
    downloaded_path = tmp_path / "downloaded.db"
    _create_database(source_path)
    created_snapshots: list[Path] = []

    def tracking_snapshot(*args, **kwargs):
        snapshot_path = create_sqlite_snapshot(*args, **kwargs)
        created_snapshots.append(snapshot_path)
        return snapshot_path

    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{source_path}")
    monkeypatch.setattr(admin_routes, "NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(admin_routes, "create_sqlite_snapshot", tracking_snapshot)

    with open_test_client_without_lifespan(app) as client:
        response = client.get(
            "/api/v1/admin/db/backup",
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/octet-stream"
    assert created_snapshots and all(not path.exists() for path in created_snapshots)
    downloaded_path.write_bytes(response.content)
    with closing(sqlite3.connect(downloaded_path)) as conn:
        assert conn.execute("SELECT content FROM messages").fetchone()[0] == "snapshot row"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.parametrize(
    "database_url",
    ["sqlite:///:memory:", "postgresql://example.invalid/nanobot"],
)
def test_admin_backup_rejects_non_file_sqlite_database_urls(
    database_url,
    monkeypatch,
):
    import config
    from api import admin_routes
    from server import app

    monkeypatch.setattr(config, "DATABASE_URL", database_url)
    monkeypatch.setattr(admin_routes, "NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(
        admin_routes,
        "create_sqlite_snapshot",
        lambda *_args, **_kwargs: pytest.fail("不应尝试创建快照"),
    )

    with open_test_client_without_lifespan(app) as client:
        response = client.get(
            "/api/v1/admin/db/backup",
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 400


def test_admin_backup_returns_404_for_missing_source(tmp_path, monkeypatch):
    import config
    from api import admin_routes
    from server import app

    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path / 'missing.db'}")
    monkeypatch.setattr(admin_routes, "NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(
        admin_routes,
        "create_sqlite_snapshot",
        lambda *_args, **_kwargs: pytest.fail("不应尝试创建快照"),
    )

    with open_test_client_without_lifespan(app) as client:
        response = client.get(
            "/api/v1/admin/db/backup",
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 404


def test_admin_backup_snapshot_failure_returns_generic_500_without_raw_fallback(
    tmp_path,
    monkeypatch,
    caplog,
):
    import config
    from api import admin_routes
    from server import app

    source_path = tmp_path / "admin-source.db"
    _create_database(source_path)
    calls: list[Path] = []

    def fail_snapshot(source_path_arg, *_args, **_kwargs):
        calls.append(Path(source_path_arg))
        raise sqlite3.OperationalError("internal snapshot detail")

    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{source_path}")
    monkeypatch.setattr(admin_routes, "NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(admin_routes, "create_sqlite_snapshot", fail_snapshot)

    with caplog.at_level("ERROR", logger="nanobot.admin"):
        with open_test_client_without_lifespan(app) as client:
            response = client.get(
                "/api/v1/admin/db/backup",
                headers={"Authorization": "Bearer test-token"},
            )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database backup failed"}
    assert calls == [source_path.resolve()]
    assert response.content != source_path.read_bytes()
    assert "internal snapshot detail" in caplog.text


def test_admin_backup_cleans_snapshot_when_response_construction_fails(
    tmp_path,
    monkeypatch,
):
    import config
    from api import admin_routes
    from server import app

    source_path = tmp_path / "admin-source.db"
    snapshot_path = tmp_path / "admin-partial-response.db"
    _create_database(source_path)

    def fake_snapshot(*_args, **_kwargs):
        snapshot_path.write_bytes(b"temporary snapshot")
        return snapshot_path

    def fail_response(*_args, **_kwargs):
        raise RuntimeError("response construction failed")

    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{source_path}")
    monkeypatch.setattr(admin_routes, "NANOBOT_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(admin_routes, "create_sqlite_snapshot", fake_snapshot)
    monkeypatch.setattr(admin_routes, "FileResponse", fail_response)

    with open_test_client_without_lifespan(app) as client:
        response = client.get(
            "/api/v1/admin/db/backup",
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database backup failed"}
    assert not snapshot_path.exists()


def test_schema_migration_uses_snapshot_service_before_altering_schema(
    tmp_path,
    monkeypatch,
):
    from core import schema_migrations
    from core.sqlite_backup import create_sqlite_snapshot

    db_path = tmp_path / "legacy.db"
    _create_legacy_chat_database(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    calls: list[tuple[Path, Path]] = []

    def tracking_snapshot(source_path, target_path, **kwargs):
        result = create_sqlite_snapshot(source_path, target_path, **kwargs)
        calls.append((Path(source_path), Path(target_path)))
        return result

    monkeypatch.setattr(schema_migrations, "create_sqlite_snapshot", tracking_snapshot)

    try:
        schema_migrations.run_schema_migrations(engine, db_path=str(db_path))
    finally:
        engine.dispose()

    assert len(calls) == 1
    backup_source, backup_target = calls[0]
    assert backup_source == db_path
    assert backup_target.parent == db_path.parent
    assert backup_target.name.startswith(f"{db_path.name}.bak.")
    assert backup_target.exists()
    with closing(sqlite3.connect(backup_target)) as backup_conn:
        backup_columns = {
            row[1] for row in backup_conn.execute("PRAGMA table_info(chat_logs)").fetchall()
        }
    assert backup_columns == {"id"}

    verification_engine = create_engine(f"sqlite:///{db_path}")
    try:
        migrated_columns = {
            col["name"] for col in inspect(verification_engine).get_columns("chat_logs")
        }
    finally:
        verification_engine.dispose()
    assert "session_id" in migrated_columns


def test_schema_migration_snapshot_failure_keeps_schema_and_versions_unchanged(
    tmp_path,
    monkeypatch,
):
    from core import schema_migrations

    db_path = tmp_path / "legacy.db"
    _create_legacy_chat_database(db_path)
    schema_before = _database_schema(db_path)
    engine = create_engine(f"sqlite:///{db_path}")

    def fail_snapshot(*_args, **_kwargs):
        raise OSError("snapshot unavailable")

    monkeypatch.setattr(schema_migrations, "create_sqlite_snapshot", fail_snapshot)

    try:
        with pytest.raises(OSError, match="snapshot unavailable"):
            schema_migrations.run_schema_migrations(engine, db_path=str(db_path))
    finally:
        engine.dispose()

    assert _database_schema(db_path) == schema_before
    with closing(sqlite3.connect(db_path)) as conn:
        version_table = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()[0]
    assert version_table == 0


def test_outbound_migration_snapshot_failure_keeps_legacy_sources_unchanged(
    tmp_path,
    monkeypatch,
):
    from core import schema_migrations

    db_path = tmp_path / "legacy-outbound-snapshot-failure.db"
    _create_legacy_outbound_database(db_path)
    schema_before = _database_schema(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        rows_before = conn.execute(
            "SELECT idempotency_key, status FROM proactive_outreach_log"
        ).fetchall()
    engine = create_engine(f"sqlite:///{db_path}")

    def fail_snapshot(*_args, **_kwargs):
        raise OSError("outbound snapshot unavailable")

    monkeypatch.setattr(
        schema_migrations,
        "create_sqlite_snapshot",
        fail_snapshot,
    )

    try:
        with pytest.raises(OSError, match="outbound snapshot unavailable"):
            schema_migrations.run_schema_migrations(engine, db_path=str(db_path))
    finally:
        engine.dispose()

    assert _database_schema(db_path) == schema_before
    with closing(sqlite3.connect(db_path)) as conn:
        rows_after = conn.execute(
            "SELECT idempotency_key, status FROM proactive_outreach_log"
        ).fetchall()
        version_table = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()[0]
        outbound_tables = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'table' AND name LIKE 'outbound_%'"
        ).fetchone()[0]
    assert rows_after == rows_before
    assert version_table == 0
    assert outbound_tables == 0


def test_outbound_and_chat_migrations_share_one_prechange_snapshot(
    tmp_path,
    monkeypatch,
):
    from core import schema_migrations
    from core.sqlite_backup import create_sqlite_snapshot

    db_path = tmp_path / "legacy-chat-and-outbound.db"
    _create_legacy_outbound_database(db_path, include_chat_tables=True)
    engine = create_engine(f"sqlite:///{db_path}")
    calls: list[tuple[Path, Path]] = []

    def tracking_snapshot(source_path, target_path, **kwargs):
        result = create_sqlite_snapshot(source_path, target_path, **kwargs)
        calls.append((Path(source_path), Path(target_path)))
        return result

    monkeypatch.setattr(schema_migrations, "create_sqlite_snapshot", tracking_snapshot)

    try:
        schema_migrations.run_schema_migrations(engine, db_path=str(db_path))
        schema_migrations.run_schema_migrations(engine, db_path=str(db_path))
    finally:
        engine.dispose()

    assert len(calls) == 1
    _source_path, backup_path = calls[0]
    with closing(sqlite3.connect(backup_path)) as backup_conn:
        scheduled_columns = {
            row[1]
            for row in backup_conn.execute("PRAGMA table_info(scheduled_tasks)")
        }
        proactive_status = backup_conn.execute(
            "SELECT status FROM proactive_outreach_log"
        ).fetchone()[0]
        chat_columns = {
            row[1] for row in backup_conn.execute("PRAGMA table_info(chat_logs)")
        }
    assert "last_attempt_at" not in scheduled_columns
    assert proactive_status == "sending"
    assert chat_columns == {"id"}


def test_chat_stream_identity_migration_snapshot_is_recoverable(
    tmp_path,
    monkeypatch,
):
    from core import schema_migrations
    from core.sqlite_backup import create_sqlite_snapshot

    db_path = tmp_path / "legacy-stream.db"
    restored_path = tmp_path / "restored-stream.db"
    _create_legacy_chat_stream_database(db_path, [("private_456", 0.7)])
    engine = create_engine(f"sqlite:///{db_path}")
    calls: list[tuple[Path, Path]] = []

    def tracking_snapshot(source_path, target_path, **kwargs):
        result = create_sqlite_snapshot(source_path, target_path, **kwargs)
        calls.append((Path(source_path), Path(target_path)))
        return result

    monkeypatch.setattr(schema_migrations, "create_sqlite_snapshot", tracking_snapshot)

    try:
        schema_migrations.run_schema_migrations(engine, db_path=str(db_path))
        schema_migrations.run_schema_migrations(engine, db_path=str(db_path))
        with engine.connect() as conn:
            live_rows = conn.execute(text(
                "SELECT chat_stream_id, talk_value FROM chat_stream_configs"
            )).fetchall()
    finally:
        engine.dispose()

    assert len(calls) == 1
    backup_source, backup_path = calls[0]
    assert backup_source == db_path
    assert backup_path.exists()
    with closing(sqlite3.connect(backup_path)) as backup_conn:
        backup_rows = backup_conn.execute(
            "SELECT chat_stream_id, talk_value FROM chat_stream_configs"
        ).fetchall()
    assert backup_rows == [("private_456", 0.7)]
    assert live_rows == [("qq:456:private", 0.7)]

    shutil.copyfile(backup_path, restored_path)
    restored_engine = create_engine(f"sqlite:///{restored_path}")
    try:
        with restored_engine.connect() as conn:
            restored_rows = conn.execute(text(
                "SELECT chat_stream_id, talk_value FROM chat_stream_configs"
            )).fetchall()
    finally:
        restored_engine.dispose()
    assert restored_rows == [("private_456", 0.7)]


def test_chat_stream_identity_snapshot_failure_keeps_alias_and_schema_unchanged(
    tmp_path,
    monkeypatch,
):
    from core import schema_migrations

    db_path = tmp_path / "identity-snapshot-failure.db"
    _create_legacy_chat_stream_database(db_path, [("group_123", 0.5)])
    schema_before = _database_schema(db_path)
    engine = create_engine(f"sqlite:///{db_path}")

    def fail_snapshot(*_args, **_kwargs):
        raise OSError("identity snapshot unavailable")

    monkeypatch.setattr(schema_migrations, "create_sqlite_snapshot", fail_snapshot)

    try:
        with pytest.raises(OSError, match="identity snapshot unavailable"):
            schema_migrations.run_schema_migrations(engine, db_path=str(db_path))
        with engine.connect() as conn:
            stored_id = conn.execute(text(
                "SELECT chat_stream_id FROM chat_stream_configs"
            )).scalar_one()
    finally:
        engine.dispose()

    assert stored_id == "group_123"
    assert _database_schema(db_path) == schema_before


def test_in_memory_chat_stream_identity_migration_skips_file_snapshot(monkeypatch):
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

    monkeypatch.setattr(
        schema_migrations,
        "create_sqlite_snapshot",
        lambda *_args, **_kwargs: pytest.fail("内存数据库不应创建文件快照"),
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


def test_chat_metadata_and_identity_migrations_share_one_snapshot(
    tmp_path,
    monkeypatch,
):
    from core import schema_migrations
    from core.sqlite_backup import create_sqlite_snapshot

    db_path = tmp_path / "combined-legacy.db"
    _create_legacy_chat_stream_database(db_path, [("group_123", 0.5)])
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("CREATE TABLE chat_logs (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE conversation_turns (id INTEGER PRIMARY KEY)")
        conn.commit()
    engine = create_engine(f"sqlite:///{db_path}")
    calls: list[tuple[Path, Path]] = []

    def tracking_snapshot(source_path, target_path, **kwargs):
        result = create_sqlite_snapshot(source_path, target_path, **kwargs)
        calls.append((Path(source_path), Path(target_path)))
        return result

    monkeypatch.setattr(schema_migrations, "create_sqlite_snapshot", tracking_snapshot)

    try:
        schema_migrations.run_schema_migrations(engine, db_path=str(db_path))
    finally:
        engine.dispose()

    assert len(calls) == 1


def test_conflicting_chat_stream_alias_does_not_create_snapshot(
    tmp_path,
    monkeypatch,
):
    from core import schema_migrations

    db_path = tmp_path / "conflicting-stream.db"
    _create_legacy_chat_stream_database(
        db_path,
        [("group_123", 0.4), ("qq:123:group", 0.8)],
    )
    engine = create_engine(f"sqlite:///{db_path}")
    backup_calls: list[tuple] = []
    monkeypatch.setattr(
        schema_migrations,
        "create_sqlite_snapshot",
        lambda *args, **kwargs: backup_calls.append((args, kwargs)),
    )

    try:
        schema_migrations.run_schema_migrations(engine, db_path=str(db_path))
        with engine.connect() as conn:
            stored_ids = {
                row[0]
                for row in conn.execute(text(
                    "SELECT chat_stream_id FROM chat_stream_configs"
                )).fetchall()
            }
    finally:
        engine.dispose()

    assert backup_calls == []
    assert stored_ids == {"group_123", "qq:123:group"}


def test_identity_migration_does_not_rename_alias_inserted_after_backup_check(
    tmp_path,
    monkeypatch,
):
    from core import schema_migrations

    db_path = tmp_path / "identity-preflight-race.db"
    _create_legacy_chat_stream_database(db_path, [])
    engine = create_engine(f"sqlite:///{db_path}")
    original_applied_versions = schema_migrations._applied_versions
    writer_attempt_finished = threading.Event()
    migration_finished = threading.Event()
    writer_errors: list[BaseException] = []
    writer_was_locked: list[bool] = []
    backup_calls: list[tuple] = []
    hook_used = False

    def coordinated_applied_versions(conn):
        nonlocal hook_used
        if not hook_used:
            hook_used = True
            allow_writer.set()
            assert writer_attempt_finished.wait(timeout=2)
        return original_applied_versions(conn)

    def insert_alias():
        allow_writer.wait(timeout=2)
        writer_engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"timeout": 0.0},
        )
        try:
            with writer_engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO chat_stream_configs(chat_stream_id) "
                    "VALUES ('group_123')"
                ))
        except OperationalError as exc:
            if "locked" not in str(exc).lower():
                writer_errors.append(exc)
            else:
                writer_was_locked.append(True)
        except BaseException as exc:  # pragma: no cover - 失败由主线程断言
            writer_errors.append(exc)
        else:
            writer_was_locked.append(False)
        finally:
            writer_engine.dispose()
            writer_attempt_finished.set()

        if writer_was_locked == [True]:
            if not migration_finished.wait(timeout=5):
                writer_errors.append(TimeoutError("迁移完成事件超时"))
                return
            retry_engine = create_engine(f"sqlite:///{db_path}")
            try:
                with retry_engine.begin() as conn:
                    conn.execute(text(
                        "INSERT INTO chat_stream_configs(chat_stream_id) "
                        "VALUES ('group_123')"
                    ))
            except BaseException as exc:  # pragma: no cover - 失败由主线程断言
                writer_errors.append(exc)
            finally:
                retry_engine.dispose()

    allow_writer = threading.Event()
    monkeypatch.setattr(
        schema_migrations,
        "_applied_versions",
        coordinated_applied_versions,
    )
    monkeypatch.setattr(
        schema_migrations,
        "create_sqlite_snapshot",
        lambda *args, **kwargs: backup_calls.append((args, kwargs)),
    )
    writer = threading.Thread(target=insert_alias)
    writer.start()

    try:
        schema_migrations.run_schema_migrations(engine, db_path=str(db_path))
        migration_finished.set()
        writer.join(timeout=5)
        assert not writer.is_alive()
        with engine.connect() as conn:
            stored_ids = {
                row[0]
                for row in conn.execute(text(
                    "SELECT chat_stream_id FROM chat_stream_configs"
                )).fetchall()
            }
    finally:
        migration_finished.set()
        engine.dispose()

    assert writer_errors == []
    assert writer_was_locked == [True]
    assert backup_calls == []
    assert stored_ids == {"group_123"}


def test_concurrent_identity_migration_runners_create_one_snapshot(
    tmp_path,
    monkeypatch,
):
    from core import schema_migrations
    from core.sqlite_backup import create_sqlite_snapshot

    db_path = tmp_path / "concurrent-identity.db"
    _create_legacy_chat_stream_database(db_path, [("group_123", 0.5)])
    first_snapshot_started = threading.Event()
    release_first_snapshot = threading.Event()
    second_begin_attempted = threading.Event()
    calls_lock = threading.Lock()
    calls: list[tuple[Path, Path]] = []
    errors: list[BaseException] = []

    def tracking_snapshot(source_path, target_path, **kwargs):
        with calls_lock:
            calls.append((Path(source_path), Path(target_path)))
            call_index = len(calls)
        if call_index == 1:
            first_snapshot_started.set()
            assert release_first_snapshot.wait(timeout=3)
        return create_sqlite_snapshot(source_path, target_path, **kwargs)

    def run_migration(runner_engine):
        try:
            schema_migrations.run_schema_migrations(
                runner_engine,
                db_path=str(db_path),
            )
        except BaseException as exc:  # pragma: no cover - 失败由主线程断言
            errors.append(exc)

    def observe_second_begin(
        _conn,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        if statement.strip().upper() == "BEGIN IMMEDIATE":
            second_begin_attempted.set()

    monkeypatch.setattr(schema_migrations, "create_sqlite_snapshot", tracking_snapshot)
    runner_engines = [
        create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 0.05},
        )
        for _ in range(2)
    ]
    event.listen(runner_engines[1], "before_cursor_execute", observe_second_begin)
    runners = [
        threading.Thread(target=run_migration, args=(runner_engine,))
        for runner_engine in runner_engines
    ]
    runners[0].start()
    assert first_snapshot_started.wait(timeout=3)
    runners[1].start()
    assert second_begin_attempted.wait(timeout=3)
    time.sleep(0.1)
    release_first_snapshot.set()
    for runner in runners:
        runner.join(timeout=5)
        assert not runner.is_alive()
    for runner_engine in runner_engines:
        runner_engine.dispose()

    verification_engine = create_engine(f"sqlite:///{db_path}")
    try:
        with verification_engine.connect() as conn:
            stored_ids = {
                row[0]
                for row in conn.execute(text(
                    "SELECT chat_stream_id FROM chat_stream_configs"
                )).fetchall()
            }
    finally:
        verification_engine.dispose()

    assert errors == []
    assert len(calls) == 1
    assert stored_ids == {"qq:123:group"}


def test_chat_stream_identity_migration_failure_rolls_back_rows_and_version(
    tmp_path,
    monkeypatch,
):
    from core import schema_migrations

    db_path = tmp_path / "rollback-stream.db"
    original_ids = {"group_123", "private_456"}
    _create_legacy_chat_stream_database(
        db_path,
        [("group_123", 0.2), ("private_456", 0.7)],
    )
    engine = create_engine(f"sqlite:///{db_path}")
    real_rename = schema_migrations._rename_chat_stream_identity_alias
    call_count = 0

    def fail_after_second_rename(conn, alias, canonical):
        nonlocal call_count
        call_count += 1
        real_rename(conn, alias, canonical)
        if call_count == 2:
            raise RuntimeError("simulated second identity conversion failure")

    monkeypatch.setattr(
        schema_migrations,
        "_rename_chat_stream_identity_alias",
        fail_after_second_rename,
    )

    try:
        with pytest.raises(
            RuntimeError,
            match="simulated second identity conversion failure",
        ):
            schema_migrations.run_schema_migrations(engine, db_path=str(db_path))
        with engine.connect() as conn:
            stored_ids = {
                row[0]
                for row in conn.execute(text(
                    "SELECT chat_stream_id FROM chat_stream_configs"
                )).fetchall()
            }
            if "schema_migrations" in inspect(conn).get_table_names():
                versions = {
                    row[0]
                    for row in conn.execute(text(
                        "SELECT version FROM schema_migrations"
                    )).fetchall()
                }
            else:
                versions = set()

        monkeypatch.setattr(
            schema_migrations,
            "_rename_chat_stream_identity_alias",
            real_rename,
        )
        schema_migrations.run_schema_migrations(engine, db_path=str(db_path))
        with engine.connect() as conn:
            recovered_ids = {
                row[0]
                for row in conn.execute(text(
                    "SELECT chat_stream_id FROM chat_stream_configs"
                )).fetchall()
            }
            recovered_version_rows = [
                row[0]
                for row in conn.execute(text(
                    "SELECT version FROM schema_migrations"
                )).fetchall()
            ]
            recovered_versions = set(recovered_version_rows)
    finally:
        engine.dispose()

    assert call_count == 2
    assert stored_ids == original_ids
    assert schema_migrations._SESSION_GUIDANCE_COLUMNS_VERSION not in versions
    assert schema_migrations._CHAT_STREAM_IDENTITY_VERSION not in versions
    assert recovered_ids == {"qq:123:group", "qq:456:private"}
    assert recovered_version_rows.count(
        schema_migrations._SESSION_GUIDANCE_COLUMNS_VERSION
    ) == 1
    assert recovered_version_rows.count(
        schema_migrations._CHAT_STREAM_IDENTITY_VERSION
    ) == 1
    assert schema_migrations._CHAT_STREAM_IDENTITY_VERSION in recovered_versions


def test_file_schema_migration_derives_backup_path_from_engine_url(tmp_path):
    from core import schema_migrations

    db_path = tmp_path / "derived-path.db"
    _create_legacy_chat_database(db_path)
    engine = create_engine(f"sqlite:///{db_path}")

    try:
        schema_migrations.run_schema_migrations(engine)
    finally:
        engine.dispose()

    backups = sorted(tmp_path.glob(f"{db_path.name}.bak.*"))
    assert len(backups) == 1
    with closing(sqlite3.connect(backups[0])) as backup_conn:
        backup_columns = {
            row[1] for row in backup_conn.execute("PRAGMA table_info(chat_logs)").fetchall()
        }
    assert backup_columns == {"id"}

    verification_engine = create_engine(f"sqlite:///{db_path}")
    try:
        migrated_columns = {
            col["name"] for col in inspect(verification_engine).get_columns("chat_logs")
        }
    finally:
        verification_engine.dispose()
    assert "session_id" in migrated_columns


def test_file_schema_migration_rejects_missing_explicit_backup_path_before_ddl(
    tmp_path,
):
    from core import schema_migrations

    db_path = tmp_path / "legacy.db"
    missing_path = tmp_path / "wrong-missing.db"
    _create_legacy_chat_database(db_path)
    schema_before = _database_schema(db_path)
    engine = create_engine(f"sqlite:///{db_path}")

    try:
        with pytest.raises(FileNotFoundError):
            schema_migrations.run_schema_migrations(engine, db_path=str(missing_path))
    finally:
        engine.dispose()

    assert _database_schema(db_path) == schema_before
    assert not list(tmp_path.glob(f"{missing_path.name}.bak.*"))


def test_file_schema_migration_rejects_existing_different_backup_path_before_ddl(
    tmp_path,
):
    from core import schema_migrations

    db_path = tmp_path / "legacy.db"
    wrong_path = tmp_path / "different.db"
    _create_legacy_chat_database(db_path)
    _create_database(wrong_path)
    schema_before = _database_schema(db_path)
    engine = create_engine(f"sqlite:///{db_path}")

    try:
        with pytest.raises(FileNotFoundError):
            schema_migrations.run_schema_migrations(engine, db_path=str(wrong_path))
    finally:
        engine.dispose()

    assert _database_schema(db_path) == schema_before
    assert not list(tmp_path.glob(f"{wrong_path.name}.bak.*"))


def test_file_schema_migration_rejects_explicit_directory_before_ddl(tmp_path):
    from core import schema_migrations

    db_path = tmp_path / "legacy.db"
    directory_path = tmp_path / "backup-directory"
    _create_legacy_chat_database(db_path)
    directory_path.mkdir()
    schema_before = _database_schema(db_path)
    engine = create_engine(f"sqlite:///{db_path}")

    try:
        with pytest.raises(FileNotFoundError):
            schema_migrations.run_schema_migrations(engine, db_path=str(directory_path))
    finally:
        engine.dispose()

    assert _database_schema(db_path) == schema_before


def test_in_memory_schema_migration_rejects_explicit_file_before_ddl(tmp_path):
    from core import schema_migrations

    explicit_path = tmp_path / "unrelated.db"
    _create_database(explicit_path)
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE chat_logs (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE conversation_turns (id INTEGER PRIMARY KEY)")
    with engine.connect() as conn:
        schema_before = conn.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()

    try:
        with pytest.raises(ValueError, match="in-memory"):
            schema_migrations.run_schema_migrations(engine, db_path=str(explicit_path))
        with engine.connect() as conn:
            schema_after = conn.exec_driver_sql(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            ).fetchall()
    finally:
        engine.dispose()

    assert schema_after == schema_before
    assert not list(tmp_path.glob(f"{explicit_path.name}.bak.*"))


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:///:memory:",
        "sqlite+pysqlite:///file::memory:?cache=shared&uri=true",
        "sqlite+pysqlite:///file:schema_migration_memory"
        "?mode=memory&cache=shared&uri=true",
    ],
    ids=["memory", "file-memory-uri", "mode-memory-uri"],
)
def test_in_memory_schema_migration_without_db_path_skips_file_snapshot(
    database_url,
    monkeypatch,
):
    from core import schema_migrations

    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE chat_logs (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE conversation_turns (id INTEGER PRIMARY KEY)")

    monkeypatch.setattr(
        schema_migrations,
        "create_sqlite_snapshot",
        lambda *_args, **_kwargs: pytest.fail("内存数据库不应创建文件快照"),
    )

    try:
        schema_migrations.run_schema_migrations(engine)
        columns = {col["name"] for col in inspect(engine).get_columns("chat_logs")}
        with engine.connect() as conn:
            versions = {
                row[0]
                for row in conn.exec_driver_sql(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
            }
    finally:
        engine.dispose()

    assert "session_id" in columns
    assert schema_migrations._CHAT_LOG_METADATA_VERSION in versions


def test_migration_backup_retention_keeps_only_five_newest_real_snapshots(tmp_path):
    from core import schema_migrations

    db_path = tmp_path / "retention.db"
    _create_database(db_path)
    created_paths: list[Path] = []

    for _ in range(7):
        before = set(tmp_path.glob(f"{db_path.name}.bak.*"))
        schema_migrations._backup_sqlite_db(str(db_path))
        after = set(tmp_path.glob(f"{db_path.name}.bak.*"))
        new_paths = after - before
        assert len(new_paths) == 1
        created_paths.extend(new_paths)

    retained = set(tmp_path.glob(f"{db_path.name}.bak.*"))
    assert len(retained) == 5
    assert retained == set(created_paths[-5:])
    assert all(not path.exists() for path in created_paths[:-5])
    for backup_path in retained:
        with closing(sqlite3.connect(backup_path)) as conn:
            assert conn.execute("SELECT content FROM messages").fetchone()[0] == "snapshot row"
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_migration_backup_retention_uses_creation_order_not_uuid_order(
    tmp_path,
    monkeypatch,
):
    from core import schema_migrations

    db_path = tmp_path / "deterministic-retention.db"
    _create_database(db_path)

    class FrozenDateTime:
        @classmethod
        def now(cls):
            return RealDateTime(2026, 7, 10, 12, 34, 56, 123456)

    class FixedUUID:
        def __init__(self, value):
            self.hex = value

    uuid_values = [char * 32 for char in ("f", "e", "d", "c", "b", "a", "0")]
    uuid_iterator = iter(uuid_values)
    monkeypatch.setattr(schema_migrations, "datetime", FrozenDateTime)
    monkeypatch.setattr(
        schema_migrations,
        "uuid4",
        lambda: FixedUUID(next(uuid_iterator)),
    )

    created_paths: list[Path] = []
    for creation_index, uuid_value in enumerate(uuid_values, start=1):
        schema_migrations._backup_sqlite_db(str(db_path))
        backup_path = tmp_path / (
            f"{db_path.name}.bak.20260710_123456_123456_{uuid_value}"
        )
        assert backup_path.exists()
        os.utime(backup_path, ns=(creation_index, creation_index))
        created_paths.append(backup_path)

    retained = set(tmp_path.glob(f"{db_path.name}.bak.*"))
    assert retained == set(created_paths[-5:])


def test_migration_backup_retention_ignores_sidecars_directories_and_manual_files(
    tmp_path,
):
    from core import schema_migrations

    db_path = tmp_path / "filtered-retention.db"
    _create_database(db_path)
    for _ in range(5):
        schema_migrations._backup_sqlite_db(str(db_path))

    manual_file = tmp_path / f"{db_path.name}.bak.manual"
    sidecar_file = tmp_path / (
        f"{db_path.name}.bak.00000000_000000_000000_{'1' * 32}-wal"
    )
    formal_name_directory = tmp_path / (
        f"{db_path.name}.bak.00000000_000000_000000_{'0' * 32}"
    )
    manual_file.write_bytes(b"manual backup marker")
    sidecar_file.write_bytes(b"sidecar marker")
    formal_name_directory.mkdir()

    schema_migrations._backup_sqlite_db(str(db_path))

    assert manual_file.read_bytes() == b"manual backup marker"
    assert sidecar_file.read_bytes() == b"sidecar marker"
    assert formal_name_directory.is_dir()
    formal_backups = [
        path
        for path in tmp_path.iterdir()
        if path.is_file()
        and path.name.startswith(f"{db_path.name}.bak.20")
        and not path.name.endswith(("-wal", "-shm", "-journal"))
    ]
    assert len(formal_backups) == 5


def test_migration_backups_do_not_overwrite_when_clock_is_frozen(tmp_path, monkeypatch):
    from core import schema_migrations

    db_path = tmp_path / "same-second.db"
    _create_database(db_path)

    class FrozenDateTime:
        @classmethod
        def now(cls):
            return RealDateTime(2026, 7, 10, 12, 34, 56, 123456)

    monkeypatch.setattr(schema_migrations, "datetime", FrozenDateTime)

    schema_migrations._backup_sqlite_db(str(db_path))
    schema_migrations._backup_sqlite_db(str(db_path))

    backups = sorted(tmp_path.glob(f"{db_path.name}.bak.*"))
    assert len(backups) == 2
    assert backups[0] != backups[1]
    assert all("20260710_123456_123456" in path.name for path in backups)


def test_migration_backup_cleanup_failure_warns_and_does_not_block_migration(
    tmp_path,
    monkeypatch,
    caplog,
):
    from core import schema_migrations

    db_path = tmp_path / "cleanup-warning.db"
    _create_legacy_chat_database(db_path)
    for _ in range(5):
        schema_migrations._backup_sqlite_db(str(db_path))

    existing_backups = set(tmp_path.glob(f"{db_path.name}.bak.*"))
    assert len(existing_backups) == 5
    oldest_backup = sorted(existing_backups, reverse=True)[-1]
    real_unlink = Path.unlink

    def fail_old_backup_unlink(path, *args, **kwargs):
        if path == oldest_backup:
            raise OSError("simulated retention cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_old_backup_unlink)
    engine = create_engine(f"sqlite:///{db_path}")

    try:
        with caplog.at_level("WARNING", logger="nanobot"):
            schema_migrations.run_schema_migrations(engine, db_path=str(db_path))
        migrated_columns = {col["name"] for col in inspect(engine).get_columns("chat_logs")}
    finally:
        engine.dispose()

    backups_after = set(tmp_path.glob(f"{db_path.name}.bak.*"))
    new_backups = backups_after - existing_backups
    assert len(new_backups) == 1
    assert next(iter(new_backups)).exists()
    assert oldest_backup.exists()
    assert "session_id" in migrated_columns
    assert "simulated retention cleanup failure" in caplog.text

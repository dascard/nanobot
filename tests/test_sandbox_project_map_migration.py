from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest


SCRIPT_PATH = Path("scripts/migrate-sandbox-project-map.py").resolve()
SPEC = importlib.util.spec_from_file_location(
    "migrate_sandbox_project_map",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MIGRATION
SPEC.loader.exec_module(MIGRATION)

WORKSPACE_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
WORKSPACE_B = "22222222-2222-4222-8222-222222222222"
MIB = 1024 * 1024


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO schema_migrations(version, name) VALUES
                ('20260722_sandbox_control_plane_tables', 'control'),
                ('20260722_sandbox_project_sequence_seed', 'seed');
            CREATE TABLE workspaces (
                id TEXT PRIMARY KEY,
                quota_bytes INTEGER NOT NULL,
                used_bytes INTEGER NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE workspace_quota_bindings (
                workspace_id TEXT PRIMARY KEY,
                project_id INTEGER NOT NULL UNIQUE,
                desired_quota_bytes INTEGER NOT NULL,
                applied_quota_bytes INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                generation INTEGER NOT NULL DEFAULT 1,
                last_error_code TEXT NOT NULL DEFAULT '',
                last_error_summary TEXT NOT NULL DEFAULT '',
                last_applied_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
            );
            CREATE TABLE sandbox_project_sequences (
                name TEXT PRIMARY KEY,
                next_value INTEGER NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO sandbox_project_sequences(name, next_value)
                VALUES ('workspace', 10000);
            """
        )
        connection.executemany(
            "INSERT INTO workspaces(id, quota_bytes, used_bytes, status) "
            "VALUES (?, ?, ?, 'active')",
            [
                (WORKSPACE_A, 64 * MIB, 1),
                (WORKSPACE_B, 96 * MIB, 2),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def _bindings(raw: str):
    return MIGRATION.parse_project_map(raw.encode("utf-8"))


def test_project_map_imports_pending_bindings_without_creating_grants(tmp_path):
    database = tmp_path / "nanobot.db"
    _database(database)
    bindings = _bindings(
        f"legacy-a\t{WORKSPACE_A}\t10000\t{64 * MIB}\n"
        f"legacy-b\t{WORKSPACE_B}\t10001\t{96 * MIB}\n"
    )

    assert MIGRATION.migrate_database(database, bindings, apply=False) == (2, 0)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM workspace_quota_bindings"
        ).fetchone()[0] == 0

    assert MIGRATION.migrate_database(database, bindings, apply=True) == (2, 0)
    assert MIGRATION.migrate_database(database, bindings, apply=True) == (0, 2)
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT workspace_id, project_id, desired_quota_bytes, "
            "applied_quota_bytes, status, generation "
            "FROM workspace_quota_bindings ORDER BY project_id"
        ).fetchall()
        sequence = connection.execute(
            "SELECT next_value FROM sandbox_project_sequences "
            "WHERE name = 'workspace'"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert rows == [
        (WORKSPACE_A, 10000, 64 * MIB, 0, "pending", 1),
        (WORKSPACE_B, 10001, 96 * MIB, 0, "pending", 1),
    ]
    assert sequence == 10002
    assert "sandbox_access_grants" not in tables


def test_project_map_conflict_rolls_back_the_entire_batch(tmp_path):
    database = tmp_path / "nanobot.db"
    _database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO workspace_quota_bindings("
            "workspace_id, project_id, desired_quota_bytes"
            ") VALUES (?, 10001, ?)",
            (WORKSPACE_B, 96 * MIB),
        )
        connection.commit()
    bindings = _bindings(
        f"legacy-a\t{WORKSPACE_A}\t10000\t{64 * MIB}\n"
        f"legacy-b\t{WORKSPACE_B}\t10002\t{96 * MIB}\n"
    )

    with pytest.raises(MIGRATION.MigrationError, match="冲突"):
        MIGRATION.migrate_database(database, bindings, apply=True)

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT workspace_id, project_id FROM workspace_quota_bindings"
        ).fetchall()
    assert rows == [(WORKSPACE_B, 10001)]


def test_project_map_rejects_duplicate_project_and_noncanonical_workspace():
    with pytest.raises(MIGRATION.MigrationError, match="多个 Workspace"):
        _bindings(
            f"legacy-a\t{WORKSPACE_A}\t10000\t{64 * MIB}\n"
            f"legacy-b\t{WORKSPACE_B}\t10000\t{96 * MIB}\n"
        )
    with pytest.raises(MIGRATION.MigrationError, match="规范 UUID"):
        _bindings(f"legacy-a\t{WORKSPACE_A.upper()}\t10000\t{64 * MIB}\n")


def test_host_validation_fails_closed_before_database_import(tmp_path):
    bindings = _bindings(
        f"legacy-a\t{WORKSPACE_A}\t10000\t{64 * MIB}\n"
    )

    with pytest.raises(MIGRATION.MigrationError, match="不一致"):
        MIGRATION.validate_host_bindings(
            tmp_path,
            bindings,
            inspector=lambda _root, _binding: 10001,
        )

"""SQL 分析链路的只读安全契约测试。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from creatures.nanobot.prompts.skills.sql_analysis.tool import SQLAnalysisTool
from sandbox import AnalysisSandbox
from tests.async_helpers import run_async


@pytest.fixture
def analysis_db(tmp_path: Path) -> Path:
    """创建仅供本测试使用的 SQLite 数据库。"""
    db_path = tmp_path / "analysis.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE parents (
                id INTEGER PRIMARY KEY,
                label TEXT NOT NULL
            );
            CREATE TABLE records (
                id INTEGER PRIMARY KEY,
                parent_id INTEGER NOT NULL REFERENCES parents(id),
                value TEXT NOT NULL
            );
            CREATE INDEX idx_records_value ON records(value);
            INSERT INTO parents (id, label) VALUES (1, 'parent');
            INSERT INTO records (id, parent_id, value) VALUES
                (1, 1, 'alpha'),
                (2, 1, 'beta'),
                (3, 1, 'gamma');
            """
        )
    return db_path


def _database_snapshot(db_path: Path) -> tuple[tuple[tuple[object, ...], ...], tuple[str, ...], int]:
    with sqlite3.connect(db_path) as conn:
        rows = tuple(
            conn.execute(
                "SELECT id, parent_id, value FROM records ORDER BY id"
            ).fetchall()
        )
        tables = tuple(
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' ORDER BY name"
            ).fetchall()
        )
        user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    return rows, tables, user_version


def _assert_sandbox_rejected(output: str, db_path: Path) -> None:
    assert output.startswith("SQL Error:"), output
    assert str(db_path.resolve()) not in output


def _run_tool(db_path: Path, sql: str):
    tool = SQLAnalysisTool()
    tool._sandbox = AnalysisSandbox(db_path=str(db_path))
    return run_async(tool.execute({"sql": sql}))


BOUNDED_READ_QUERIES = [
    pytest.param(
        "SELECT id, value FROM records ORDER BY id LIMIT 2",
        id="select",
    ),
    pytest.param(
        "WITH recent AS ("
        "SELECT id, value FROM records ORDER BY id DESC LIMIT 2"
        ") SELECT id, value FROM recent ORDER BY id LIMIT 2",
        id="cte",
    ),
]


SAFE_PRAGMAS = [
    pytest.param("PRAGMA table_info(records)", id="table-info"),
    pytest.param("PRAGMA table_xinfo(records)", id="table-xinfo"),
    pytest.param("PRAGMA index_list(records)", id="index-list"),
    pytest.param("PRAGMA index_info(idx_records_value)", id="index-info"),
    pytest.param("PRAGMA foreign_key_list(records)", id="foreign-key-list"),
]


UNSAFE_SQL = [
    pytest.param(
        "WITH selected AS (SELECT id FROM records LIMIT 1) "
        "DELETE FROM records WHERE id IN (SELECT id FROM selected)",
        id="write-cte",
    ),
    pytest.param(
        "/* leading comment */ INSERT INTO records (id, parent_id, value) "
        "VALUES (4, 1, 'delta')",
        id="insert",
    ),
    pytest.param(
        "UPDATE records SET value = 'changed' WHERE id = 1",
        id="update",
    ),
    pytest.param("DELETE FROM records WHERE id = 1", id="delete"),
    pytest.param(
        "REPLACE INTO records (id, parent_id, value) VALUES (1, 1, 'changed')",
        id="replace",
    ),
    pytest.param("CREATE TABLE injected (id INTEGER)", id="create-table"),
    pytest.param("DROP TABLE records", id="drop-table"),
    pytest.param(
        "ALTER TABLE records ADD COLUMN injected TEXT",
        id="alter-table",
    ),
    pytest.param(
        "SELECT id FROM records LIMIT 1; SELECT id FROM records LIMIT 1",
        id="multiple-statements",
    ),
    pytest.param(
        "SELECT id FROM records LIMIT 1; /* split */ DELETE FROM records",
        id="multiple-statements-with-comment",
    ),
    pytest.param("ATTACH DATABASE ':memory:' AS extra", id="attach"),
    pytest.param("DETACH DATABASE main", id="detach"),
    pytest.param("VACUUM", id="vacuum"),
    pytest.param("REINDEX idx_records_value", id="reindex"),
    pytest.param("PRAGMA database_list", id="database-list"),
    pytest.param("PRAGMA user_version = 7", id="pragma-assignment"),
    pytest.param("PRAGMA query_only = OFF", id="disable-query-only"),
    pytest.param(
        "SELECT load_extension('not-present') LIMIT 1",
        id="load-extension",
    ),
    pytest.param("SELECT * FROM records LIMIT 1", id="select-star"),
    pytest.param("SELECT id FROM records LIMIT 1001", id="limit-too-large"),
]


@pytest.mark.parametrize("sql", BOUNDED_READ_QUERIES)
def test_analysis_sandbox_allows_bounded_select_and_cte(
    analysis_db: Path,
    sql: str,
) -> None:
    output = AnalysisSandbox(db_path=str(analysis_db)).run_query(sql)

    assert not output.startswith("SQL Error:"), output
    assert "alpha" in output or "beta" in output or "gamma" in output


@pytest.mark.parametrize("sql", SAFE_PRAGMAS)
def test_analysis_sandbox_allows_safe_pragmas(
    analysis_db: Path,
    sql: str,
) -> None:
    output = AnalysisSandbox(db_path=str(analysis_db)).run_query(sql)

    assert not output.startswith("SQL Error:"), output


@pytest.mark.parametrize("sql", UNSAFE_SQL)
def test_analysis_sandbox_rejects_unsafe_sql_without_mutation_or_path_leak(
    analysis_db: Path,
    sql: str,
) -> None:
    before = _database_snapshot(analysis_db)

    output = AnalysisSandbox(db_path=str(analysis_db)).run_query(sql)

    assert _database_snapshot(analysis_db) == before
    _assert_sandbox_rejected(output, analysis_db)


def test_analysis_sandbox_database_list_does_not_disclose_database_path(
    analysis_db: Path,
) -> None:
    output = AnalysisSandbox(db_path=str(analysis_db)).run_query("PRAGMA database_list")

    _assert_sandbox_rejected(output, analysis_db)


@pytest.mark.parametrize("sql", BOUNDED_READ_QUERIES)
def test_sql_analysis_tool_allows_bounded_select_and_cte(
    analysis_db: Path,
    sql: str,
) -> None:
    result = _run_tool(analysis_db, sql)

    assert result.success, result.error
    assert "alpha" in result.output or "beta" in result.output or "gamma" in result.output


@pytest.mark.parametrize("sql", SAFE_PRAGMAS)
def test_sql_analysis_tool_allows_safe_pragmas(
    analysis_db: Path,
    sql: str,
) -> None:
    result = _run_tool(analysis_db, sql)

    assert result.success, result.error


@pytest.mark.parametrize("sql", UNSAFE_SQL)
def test_sql_analysis_tool_rejects_unsafe_sql_without_path_leak(
    analysis_db: Path,
    sql: str,
) -> None:
    before = _database_snapshot(analysis_db)

    result = _run_tool(analysis_db, sql)

    assert _database_snapshot(analysis_db) == before
    assert not result.success
    assert result.error
    combined_output = f"{result.error}\n{result.get_text_output()}"
    assert str(analysis_db.resolve()) not in combined_output


def test_sql_analysis_tool_database_list_does_not_disclose_database_path(
    analysis_db: Path,
) -> None:
    result = _run_tool(analysis_db, "PRAGMA database_list")

    assert not result.success
    combined_output = f"{result.error or ''}\n{result.get_text_output()}"
    assert str(analysis_db.resolve()) not in combined_output

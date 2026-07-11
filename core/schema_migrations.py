"""轻量级 schema migration runner。

用于替代继续在 `core.database.init_db()` 里追加热迁移逻辑。
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import inspect, text

from core.chat_delivery_outbox_schema import chat_delivery_outbox_table
from core.proactive_outreach_schema import proactive_outreach_leases_table
from core.schema_validation import (
    SchemaMigrationValidationError as SchemaMigrationValidationError,
)
from core.sqlite_backup import create_sqlite_snapshot
from core.time_utils import db_now_naive


MigrationFn = Callable[[Any, Any, str | None], None]
_CHAT_LOG_METADATA_VERSION = "20260523_chat_log_metadata_columns"


def _ensure_table(conn: Any) -> None:
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version TEXT PRIMARY KEY, "
        "name TEXT NOT NULL, "
        "applied_at DATETIME NOT NULL"
        ")"
    ))


def _applied_versions(conn: Any) -> set[str]:
    _ensure_table(conn)
    rows = conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
    return {str(row[0]) for row in rows}


def _record(conn: Any, version: str, name: str) -> None:
    conn.execute(
        text("INSERT INTO schema_migrations(version, name, applied_at) VALUES (:v, :n, :t)"),
        {"v": version, "n": name, "t": db_now_naive()},
    )


def _table_names(bind: Any) -> set[str]:
    return {str(name) for name in inspect(bind).get_table_names()}


def _columns(bind: Any, table: str) -> set[str]:
    inspector = inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {str(col["name"]) for col in inspector.get_columns(table)}


def _add_missing_columns(conn: Any, table: str, columns: dict[str, str]) -> list[str]:
    existing = _columns(conn, table)
    if not existing:
        return []

    added: list[str] = []
    for col_name, col_type in columns.items():
        if col_name not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
            added.append(col_name)
    return added


def _create_indexes(conn: Any, statements: list[str]) -> None:
    for stmt in statements:
        conn.execute(text(stmt))


def _backup_sqlite_db(db_path: str | Path) -> None:
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = path.with_name(f"{path.name}.bak.{timestamp}_{uuid4().hex}")
    create_sqlite_snapshot(path, backup_path)

    try:
        backup_name_pattern = re.compile(
            rf"{re.escape(path.name)}\.bak\."
            r"\d{8}_\d{6}_\d{6}_[0-9a-f]{32}"
        )
        backups = [
            candidate
            for candidate in path.parent.iterdir()
            if backup_name_pattern.fullmatch(candidate.name)
            and candidate.is_file()
            and not candidate.is_symlink()
        ]
        backups.sort(
            key=lambda candidate: (candidate.stat().st_mtime_ns, candidate.name),
            reverse=True,
        )
    except Exception as exc:  # pragma: no cover - 保留扫描失败不应阻断迁移
        logging.getLogger("nanobot").warning(
            "DB backup cleanup scan failed (migration continues): %s",
            exc,
        )
        return

    for old in backups[5:]:
        try:
            old.unlink(missing_ok=True)
        except Exception as exc:  # pragma: no cover - 保留清理失败不应阻断迁移
            logging.getLogger("nanobot").warning(
                "DB backup cleanup failed (migration continues): %s",
                exc,
            )


def _sqlite_engine_database_path(engine: Any) -> Path | None:
    """解析文件型 SQLite engine 路径；内存数据库明确返回 None。"""
    url = getattr(engine, "url", None)
    drivername = str(getattr(url, "drivername", ""))
    if not drivername.startswith("sqlite"):
        raise ValueError("Schema migration backup requires a SQLite engine")

    database = getattr(url, "database", None)
    if database in (None, "", ":memory:"):
        return None

    database_text = str(database)
    query = dict(getattr(url, "query", {}) or {})
    uri_enabled = str(query.get("uri", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if uri_enabled and (
        database_text == "file::memory:"
        or str(query.get("mode", "")).strip().lower() == "memory"
    ):
        return None
    if database_text.startswith("file:"):
        raise ValueError("SQLite URI file paths are not supported for migration backup")
    return Path(database_text).resolve()


def _migration_backup_path(engine: Any, db_path: str | None) -> Path | None:
    engine_path = _sqlite_engine_database_path(engine)
    if engine_path is None:
        if db_path is not None:
            raise ValueError("Explicit db_path conflicts with in-memory SQLite engine")
        return None

    if db_path is None:
        selected_path = engine_path
    else:
        selected_path = Path(db_path).resolve()
        if not selected_path.is_file():
            raise FileNotFoundError(selected_path)
        if selected_path != engine_path:
            raise FileNotFoundError(
                f"Migration backup path does not match engine database: {selected_path}"
            )

    if not selected_path.is_file():
        raise FileNotFoundError(selected_path)
    return selected_path


def _chat_log_metadata_needs_backup(bind: Any) -> bool:
    chat_columns = _columns(bind, "chat_logs")
    conv_columns = _columns(bind, "conversation_turns")
    return bool(
        (chat_columns and any(col not in chat_columns for col in (
            "session_id",
            "sender_name",
            "session_name",
            "message_id",
            "source_message_ids_json",
            "meta_json",
        )))
        or (conv_columns and any(col not in conv_columns for col in (
            "source_message_ids_json",
            "meta_json",
        )))
    )


def _chat_log_metadata_columns(conn: Any, engine: Any, db_path: str | None) -> None:
    chat_missing = {
        "session_id": "TEXT",
        "sender_name": "TEXT",
        "session_name": "TEXT",
        "message_id": "TEXT",
        "source_message_ids_json": "TEXT",
        "meta_json": "TEXT",
    }
    conv_missing = {
        "source_message_ids_json": "TEXT",
        "meta_json": "TEXT",
    }

    _add_missing_columns(conn, "chat_logs", chat_missing)
    _add_missing_columns(conn, "conversation_turns", conv_missing)


def _sticker_memory_columns(conn: Any, engine: Any, db_path: str | None) -> None:
    _add_missing_columns(conn, "sticker_memories", {
        "chat_stream_id": "TEXT",
        "sticker_hash": "TEXT",
        "file_ref": "TEXT",
        "send_code": "TEXT",
        "name": "TEXT",
        "description": "TEXT",
        "tags_json": "TEXT",
        "emotions_json": "TEXT",
        "source_type": "TEXT",
        "source_count": "INTEGER DEFAULT 1",
        "status": "TEXT DEFAULT 'active'",
        "usage_count": "INTEGER DEFAULT 0",
        "first_seen": "TIMESTAMP",
        "last_seen": "TIMESTAMP",
        "last_used": "TIMESTAMP",
        "meta_json": "TEXT",
        "local_path": "TEXT",
        "preview_status": "TEXT DEFAULT 'pending'",
        "content_hash": "TEXT",
        "byte_size": "INTEGER DEFAULT 0",
        "width": "INTEGER DEFAULT 0",
        "height": "INTEGER DEFAULT 0",
        "phash": "TEXT DEFAULT ''",
        "dhash": "TEXT DEFAULT ''",
        "ahash": "TEXT DEFAULT ''",
        "duplicate_of_id": "INTEGER",
        "dedupe_status": "TEXT DEFAULT 'unique'",
        "describe_status": "TEXT DEFAULT 'pending'",
        "describe_attempts": "INTEGER DEFAULT 0",
        "describe_last_error": "TEXT",
        "described_at": "TIMESTAMP",
        "created_at": "TIMESTAMP",
    })
    if "sticker_memories" in _table_names(conn):
        _create_indexes(conn, [
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_sticker_stream_hash "
            "ON sticker_memories(chat_stream_id, sticker_hash)"
        ])


def _group_memory_columns(conn: Any, engine: Any, db_path: str | None) -> None:
    if "group_memories" not in _table_names(conn):
        return

    _add_missing_columns(conn, "group_memories", {
        "content_hash": "TEXT DEFAULT ''",
        "cluster_key": "TEXT",
        "updated_at": "TIMESTAMP",
        "source": "TEXT DEFAULT 'group_analysis'",
        "inject_policy": "TEXT DEFAULT 'auto'",
        "disabled_reason": "TEXT DEFAULT ''",
        "rejected_reason": "TEXT DEFAULT ''",
        "merged_into_id": "INTEGER",
        "last_injected_at": "TIMESTAMP",
        "injected_count": "INTEGER DEFAULT 0",
    })

    rows = conn.execute(text(
        "SELECT id, content FROM group_memories WHERE content_hash IS NULL OR content_hash = ''"
    )).fetchall()
    for row in rows:
        norm = re.sub(r"\s+", " ", (row.content or "").strip().lower()).rstrip("。.!！?？")
        content_hash = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]
        conn.execute(
            text("UPDATE group_memories SET content_hash = :h WHERE id = :id"),
            {"h": content_hash, "id": row.id},
        )

    dup_rows = conn.execute(text(
        "SELECT group_id, memory_type, content_hash, COUNT(*) AS n "
        "FROM group_memories "
        "GROUP BY group_id, memory_type, content_hash "
        "HAVING n > 1"
    )).fetchall()
    for drow in dup_rows:
        dup_ids = conn.execute(text(
            "SELECT id FROM group_memories "
            "WHERE group_id = :g AND memory_type = :t AND content_hash = :h "
            "ORDER BY confidence DESC, evidence_count DESC, id ASC"
        ), {"g": drow.group_id, "t": drow.memory_type, "h": drow.content_hash}).fetchall()
        if not dup_ids:
            continue
        canonical_id = dup_ids[0][0]
        for (dup_id,) in dup_ids[1:]:
            conn.execute(text(
                "UPDATE group_memories SET status = 'archived', "
                "content_hash = content_hash || ':archived:' || CAST(id AS TEXT), "
                "cluster_key = (SELECT cluster_key FROM group_memories WHERE id = :c) "
                "WHERE id = :d"
            ), {"c": canonical_id, "d": dup_id})

    _create_indexes(conn, [
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_group_memory_hash "
        "ON group_memories(group_id, memory_type, content_hash)"
    ])


def _group_memory_governance_columns(conn: Any, engine: Any, db_path: str | None) -> None:
    if "group_memories" not in _table_names(conn):
        return

    _add_missing_columns(conn, "group_memories", {
        "inject_policy": "TEXT DEFAULT 'auto'",
        "disabled_reason": "TEXT DEFAULT ''",
        "rejected_reason": "TEXT DEFAULT ''",
        "merged_into_id": "INTEGER",
        "last_injected_at": "TIMESTAMP",
        "injected_count": "INTEGER DEFAULT 0",
    })


def _chat_stream_config_group_profile_mode(conn: Any, engine: Any, db_path: str | None) -> None:
    cfg_columns = _columns(conn, "chat_stream_configs")
    if not cfg_columns or "group_profile_mode" in cfg_columns:
        return

    has_old = "enable_group_profile" in cfg_columns
    default_val = "on" if has_old else "off"
    conn.execute(text(
        f"ALTER TABLE chat_stream_configs ADD COLUMN group_profile_mode TEXT DEFAULT '{default_val}'"
    ))
    if has_old:
        conn.execute(text(
            "UPDATE chat_stream_configs SET group_profile_mode = "
            "CASE WHEN enable_group_profile != 0 THEN 'on' ELSE 'off' END"
        ))


def _chat_log_session_message_index(conn: Any, engine: Any, db_path: str | None) -> None:
    if "chat_logs" in _table_names(conn):
        _create_indexes(conn, [
            "CREATE INDEX IF NOT EXISTS idx_cl_session_msg ON chat_logs(session_id, message_id)"
        ])


def _user_profile_columns(conn: Any, engine: Any, db_path: str | None) -> None:
    _add_missing_columns(conn, "users", {
        "history_clear_at": "TIMESTAMP",
        "name": "TEXT",
    })


def _persona_fact_governance_columns(conn: Any, engine: Any, db_path: str | None) -> None:
    if "persona_facts" not in _table_names(conn):
        return

    existing_before = _columns(conn, "persona_facts")
    _add_missing_columns(conn, "persona_facts", {
        "status": "TEXT DEFAULT 'review'",
        "inject_policy": "TEXT DEFAULT 'manual_only'",
        "memory_type": "TEXT DEFAULT 'stable_preference'",
        "content_hash": "TEXT DEFAULT ''",
        "disabled_reason": "TEXT DEFAULT ''",
        "rejected_reason": "TEXT DEFAULT ''",
        "evidence_log_ids_json": "TEXT DEFAULT '[]'",
        "candidate_meta_json": "TEXT DEFAULT '{}'",
        "last_injected_at": "TIMESTAMP",
        "injected_count": "INTEGER DEFAULT 0",
    })

    rows = conn.execute(text(
        "SELECT id, content, fact_type, confidence, evidence_count "
        "FROM persona_facts"
    )).fetchall()
    for row in rows:
        norm = re.sub(r"\s+", " ", (row.content or "").strip().lower()).rstrip("。.!！?？")
        content_hash = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32] if norm else ""
        fact_type = str(row.fact_type or "").strip().lower()
        memory_type = {
            "preference": "stable_preference",
            "behavior": "interaction_style",
            "trait": "stable_background",
        }.get(fact_type, "stable_preference")
        confidence = str(row.confidence or "")
        evidence_count = int(row.evidence_count or 0)
        if confidence == "确认" or (confidence == "可能" and evidence_count >= 3):
            status = "active"
            inject_policy = "auto"
        else:
            status = "review"
            inject_policy = "manual_only"
        status_expr = ":s" if "status" not in existing_before else (
            "CASE WHEN status IS NULL OR status = '' THEN :s ELSE status END"
        )
        inject_expr = ":p" if "inject_policy" not in existing_before else (
            "CASE WHEN inject_policy IS NULL OR inject_policy = '' THEN :p ELSE inject_policy END"
        )
        memory_expr = ":m" if "memory_type" not in existing_before else (
            "CASE WHEN memory_type IS NULL OR memory_type = '' THEN :m ELSE memory_type END"
        )
        conn.execute(text(
            "UPDATE persona_facts SET "
            "content_hash = CASE WHEN content_hash IS NULL OR content_hash = '' THEN :h ELSE content_hash END, "
            f"memory_type = {memory_expr}, "
            f"status = {status_expr}, "
            f"inject_policy = {inject_expr}, "
            "evidence_log_ids_json = CASE WHEN evidence_log_ids_json IS NULL OR evidence_log_ids_json = '' THEN '[]' ELSE evidence_log_ids_json END, "
            "candidate_meta_json = CASE WHEN candidate_meta_json IS NULL OR candidate_meta_json = '' THEN '{}' ELSE candidate_meta_json END "
            "WHERE id = :id"
        ), {
            "h": content_hash,
            "m": memory_type,
            "s": status,
            "p": inject_policy,
            "id": row.id,
        })


def _expression_jargon_unique_indexes(conn: Any, engine: Any, db_path: str | None) -> None:
    tables = _table_names(conn)
    if "expression_memories" in tables:
        _create_indexes(conn, [
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_expr_stream_expr "
            "ON expression_memories(chat_stream_id, expression)"
        ])
    if "jargon_memories" in tables:
        _create_indexes(conn, [
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_jargon_stream_term "
            "ON jargon_memories(chat_stream_id, term)"
        ])


def _agent_prompt_trace_columns(conn: Any, engine: Any, db_path: str | None) -> None:
    _add_missing_columns(conn, "agent_runs", {
        "chat_type": "TEXT DEFAULT ''",
        "group_id": "TEXT DEFAULT ''",
        "prompt_source": "TEXT DEFAULT ''",
        "prompt_runtime_path": "TEXT DEFAULT ''",
        "prompt_default_path": "TEXT DEFAULT ''",
        "prompt_sha256": "TEXT DEFAULT ''",
    })
    if "agent_runs" in _table_names(conn):
        _create_indexes(conn, [
            "CREATE INDEX IF NOT EXISTS idx_agent_runs_chat_type ON agent_runs(chat_type)",
            "CREATE INDEX IF NOT EXISTS idx_agent_runs_group_id ON agent_runs(group_id)",
            "CREATE INDEX IF NOT EXISTS idx_agent_runs_prompt_source ON agent_runs(prompt_source)",
            "CREATE INDEX IF NOT EXISTS idx_agent_runs_prompt_sha256 ON agent_runs(prompt_sha256)",
        ])

    _add_missing_columns(conn, "prompt_render_logs", {
        "prompt_source": "TEXT DEFAULT ''",
        "prompt_runtime_path": "TEXT DEFAULT ''",
        "prompt_default_path": "TEXT DEFAULT ''",
        "prompt_sha256": "TEXT DEFAULT ''",
    })
    if "prompt_render_logs" in _table_names(conn):
        _create_indexes(conn, [
            "CREATE INDEX IF NOT EXISTS idx_prompt_render_source ON prompt_render_logs(prompt_source)",
            "CREATE INDEX IF NOT EXISTS idx_prompt_render_sha256 ON prompt_render_logs(prompt_sha256)",
        ])


def _llm_request_log_columns(conn: Any, engine: Any, db_path: str | None) -> None:
    _add_missing_columns(conn, "llm_api_request_logs", {
        "response_json": "TEXT DEFAULT '{}'",
        "response_preview": "TEXT DEFAULT ''",
        "latency_ms": "INTEGER DEFAULT 0",
        "finished_at": "DATETIME",
        "message_sources_json": "TEXT DEFAULT '[]'",
        "request_lint_json": "TEXT DEFAULT '{}'",
        "actual_sent_tools_json": "TEXT DEFAULT '[]'",
        "runtime_enabled_tools_json": "TEXT DEFAULT '[]'",
        "runtime_disabled_tools_json": "TEXT DEFAULT '[]'",
        "framework_injected_tools_json": "TEXT DEFAULT '[]'",
    })


def _reply_contract_check_logs(conn: Any, engine: Any, db_path: str | None) -> None:
    _add_missing_columns(conn, "reply_contract_check_logs", {
        "trace_id": "TEXT DEFAULT ''",
        "run_id": "TEXT DEFAULT ''",
        "session_id": "TEXT DEFAULT ''",
        "attempt": "INTEGER DEFAULT 0",
        "raw_output_preview": "TEXT DEFAULT ''",
        "has_reply_tool": "INTEGER DEFAULT 0",
        "has_no_reply_tool": "INTEGER DEFAULT 0",
        "has_structured_fallback": "INTEGER DEFAULT 0",
        "result": "TEXT DEFAULT ''",
        "created_at": "DATETIME",
    })
    if "reply_contract_check_logs" in _table_names(conn):
        _create_indexes(conn, [
            "CREATE INDEX IF NOT EXISTS idx_reply_contract_run_id ON reply_contract_check_logs(run_id)",
            "CREATE INDEX IF NOT EXISTS idx_reply_contract_trace_id ON reply_contract_check_logs(trace_id)",
            "CREATE INDEX IF NOT EXISTS idx_reply_contract_session_id ON reply_contract_check_logs(session_id)",
        ])


def _reply_contract_check_count_columns(conn: Any, engine: Any, db_path: str | None) -> None:
    _add_missing_columns(conn, "reply_contract_check_logs", {
        "reply_tool_call_count": "INTEGER DEFAULT 0",
        "no_reply_tool_call_count": "INTEGER DEFAULT 0",
        "structured_fallback_count": "INTEGER DEFAULT 0",
        "total_final_action_count": "INTEGER DEFAULT 0",
    })


def _reply_eval_trace_columns(conn: Any, engine: Any, db_path: str | None) -> None:
    _add_missing_columns(conn, "reply_eval_results", {
        "agent_run_id": "TEXT DEFAULT ''",
        "trace_id": "TEXT DEFAULT ''",
        "prompt_sha256": "TEXT DEFAULT ''",
    })
    if "reply_eval_results" in _table_names(conn):
        _create_indexes(conn, [
            "CREATE INDEX IF NOT EXISTS idx_reply_eval_results_agent_run_id ON reply_eval_results(agent_run_id)",
            "CREATE INDEX IF NOT EXISTS idx_reply_eval_results_trace_id ON reply_eval_results(trace_id)",
            "CREATE INDEX IF NOT EXISTS idx_reply_eval_results_prompt_sha256 ON reply_eval_results(prompt_sha256)",
        ])


def _rolling_session_summaries(conn: Any, engine: Any, db_path: str | None) -> None:
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS rolling_session_summaries ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "session_id TEXT NOT NULL, "
        "user_id TEXT DEFAULT '', "
        "chat_type TEXT DEFAULT 'private', "
        "status TEXT DEFAULT 'active', "
        "summary_kind TEXT DEFAULT 'deterministic_fallback', "
        "summary_text TEXT DEFAULT '', "
        "summary_json TEXT DEFAULT '{}', "
        "covered_from_turn_id INTEGER DEFAULT 0, "
        "covered_until_turn_id INTEGER DEFAULT 0, "
        "source_turn_ids_json TEXT DEFAULT '[]', "
        "source_turn_count INTEGER DEFAULT 0, "
        "source_token_estimate INTEGER DEFAULT 0, "
        "source_char_count INTEGER DEFAULT 0, "
        "raw_window_start_turn_id INTEGER DEFAULT 0, "
        "quality_score REAL DEFAULT 0.0, "
        "issues_json TEXT DEFAULT '[]', "
        "model TEXT DEFAULT '', "
        "prompt_sha256 TEXT DEFAULT '', "
        "llm_status TEXT DEFAULT '', "
        "llm_model TEXT DEFAULT '', "
        "llm_request_log_id INTEGER, "
        "llm_error TEXT DEFAULT '', "
        "retry_count INTEGER DEFAULT 0, "
        "next_retry_at DATETIME, "
        "supersedes_summary_id INTEGER, "
        "stable_hash TEXT DEFAULT '', "
        "meta_json TEXT DEFAULT '{}', "
        "created_at DATETIME, "
        "updated_at DATETIME"
        ")"
    ))
    _create_indexes(conn, [
        "CREATE INDEX IF NOT EXISTS idx_rss_session_status ON rolling_session_summaries(session_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_rss_session_covered ON rolling_session_summaries(session_id, covered_until_turn_id)",
        "CREATE INDEX IF NOT EXISTS idx_rss_user_session ON rolling_session_summaries(user_id, session_id)",
        "CREATE INDEX IF NOT EXISTS idx_rss_summary_kind ON rolling_session_summaries(summary_kind)",
        "CREATE INDEX IF NOT EXISTS idx_rss_llm_status ON rolling_session_summaries(llm_status)",
        "CREATE INDEX IF NOT EXISTS idx_rss_stable_hash ON rolling_session_summaries(stable_hash)",
    ])


def _session_summary_llm_columns(conn: Any, engine: Any, db_path: str | None) -> None:
    _add_missing_columns(conn, "rolling_session_summaries", {
        "summary_kind": "TEXT DEFAULT 'deterministic_fallback'",
        "llm_status": "TEXT DEFAULT ''",
        "llm_model": "TEXT DEFAULT ''",
        "llm_request_log_id": "INTEGER",
        "llm_error": "TEXT DEFAULT ''",
        "retry_count": "INTEGER DEFAULT 0",
        "next_retry_at": "DATETIME",
        "supersedes_summary_id": "INTEGER",
        "stable_hash": "TEXT DEFAULT ''",
    })
    _create_indexes(conn, [
        "CREATE INDEX IF NOT EXISTS idx_rss_summary_kind ON rolling_session_summaries(summary_kind)",
        "CREATE INDEX IF NOT EXISTS idx_rss_llm_status ON rolling_session_summaries(llm_status)",
        "CREATE INDEX IF NOT EXISTS idx_rss_stable_hash ON rolling_session_summaries(stable_hash)",
    ])


def _session_summary_jobs(conn: Any, engine: Any, db_path: str | None) -> None:
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS session_summary_jobs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "session_id TEXT NOT NULL, "
        "user_id TEXT DEFAULT '', "
        "chat_type TEXT DEFAULT 'private', "
        "covered_from_turn_id INTEGER DEFAULT 0, "
        "covered_until_turn_id INTEGER DEFAULT 0, "
        "source_turn_ids_json TEXT DEFAULT '[]', "
        "previous_summary_id INTEGER, "
        "fallback_summary_id INTEGER, "
        "result_summary_id INTEGER, "
        "status TEXT DEFAULT 'pending', "
        "retry_count INTEGER DEFAULT 0, "
        "max_retry INTEGER DEFAULT 3, "
        "next_retry_at DATETIME, "
        "locked_by TEXT DEFAULT '', "
        "locked_at DATETIME, "
        "error TEXT DEFAULT '', "
        "stable_hash TEXT DEFAULT '', "
        "meta_json TEXT DEFAULT '{}', "
        "created_at DATETIME, "
        "updated_at DATETIME"
        ")"
    ))
    _create_indexes(conn, [
        "CREATE INDEX IF NOT EXISTS idx_ssj_session_status ON session_summary_jobs(session_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_ssj_status_retry ON session_summary_jobs(status, next_retry_at)",
        "CREATE INDEX IF NOT EXISTS idx_ssj_session_range ON session_summary_jobs(session_id, covered_from_turn_id, covered_until_turn_id)",
        "CREATE INDEX IF NOT EXISTS idx_ssj_stable_hash ON session_summary_jobs(stable_hash)",
    ])


def _semantic_rag_tables(conn: Any, engine: Any, db_path: str | None) -> None:
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS semantic_index_items ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "source_type TEXT NOT NULL DEFAULT '', "
        "source_id TEXT NOT NULL DEFAULT '', "
        "source_sub_id TEXT NOT NULL DEFAULT '', "
        "document_id TEXT DEFAULT '', "
        "chunk_id TEXT DEFAULT '', "
        "user_id TEXT DEFAULT '', "
        "session_id TEXT DEFAULT '', "
        "group_id TEXT DEFAULT '', "
        "chat_stream_id TEXT DEFAULT '', "
        "visibility TEXT DEFAULT 'recall', "
        "status TEXT DEFAULT 'active', "
        "title TEXT DEFAULT '', "
        "text TEXT DEFAULT '', "
        "lexical_text TEXT DEFAULT '', "
        "embedding_text TEXT DEFAULT '', "
        "text_hash TEXT DEFAULT '', "
        "source_hash TEXT DEFAULT '', "
        "source_updated_at DATETIME, "
        "embedding BLOB, "
        "embedding_dim INTEGER DEFAULT 0, "
        "embedding_model TEXT DEFAULT '', "
        "embedding_status TEXT DEFAULT 'pending', "
        "index_version TEXT DEFAULT '', "
        "quality_score REAL DEFAULT 0.0, "
        "trust_level TEXT DEFAULT 'medium', "
        "source_prior REAL DEFAULT 0.5, "
        "meta_json TEXT DEFAULT '{}', "
        "indexed_at DATETIME, "
        "updated_at DATETIME, "
        "deleted_at DATETIME, "
        "UNIQUE(source_type, source_id, source_sub_id, index_version)"
        ")"
    ))
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS semantic_index_jobs ("
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
        "finished_at DATETIME"
        ")"
    ))
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS rag_debug_runs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "trace_id TEXT DEFAULT '', "
        "source_type TEXT DEFAULT '', "
        "query TEXT DEFAULT '', "
        "request_json TEXT DEFAULT '{}', "
        "response_json TEXT DEFAULT '{}', "
        "degraded INTEGER DEFAULT 0, "
        "fallback_reason TEXT DEFAULT '', "
        "latency_ms INTEGER DEFAULT 0, "
        "created_at DATETIME"
        ")"
    ))
    conn.execute(text(
        "CREATE VIRTUAL TABLE IF NOT EXISTS semantic_index_fts USING fts5("
        "title, "
        "text, "
        "lexical_text, "
        "source_type UNINDEXED, "
        "source_id UNINDEXED, "
        "source_sub_id UNINDEXED, "
        "tokenize = 'trigram'"
        ")"
    ))
    _create_indexes(conn, [
        "CREATE INDEX IF NOT EXISTS idx_semantic_item_source ON semantic_index_items(source_type, source_id)",
        "CREATE INDEX IF NOT EXISTS idx_semantic_item_status_visibility ON semantic_index_items(status, visibility)",
        "CREATE INDEX IF NOT EXISTS idx_semantic_item_embedding_status ON semantic_index_items(embedding_status)",
        "CREATE INDEX IF NOT EXISTS idx_semantic_job_status_retry ON semantic_index_jobs(status, next_retry_at)",
        "CREATE INDEX IF NOT EXISTS idx_rag_debug_trace ON rag_debug_runs(trace_id)",
        "CREATE INDEX IF NOT EXISTS idx_rag_debug_source_created ON rag_debug_runs(source_type, created_at)",
    ])


def _knowledge_library_tables(conn: Any, engine: Any, db_path: str | None) -> None:
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS knowledge_sources ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "source_key TEXT NOT NULL UNIQUE, "
        "name TEXT DEFAULT '', "
        "source_type TEXT DEFAULT 'manual', "
        "domain TEXT DEFAULT '', "
        "base_url TEXT DEFAULT '', "
        "status TEXT DEFAULT 'active', "
        "trust_level TEXT DEFAULT 'medium', "
        "meta_json TEXT DEFAULT '{}', "
        "created_at DATETIME, "
        "updated_at DATETIME"
        ")"
    ))
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS knowledge_documents ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "source_id INTEGER, "
        "document_kind TEXT DEFAULT 'manual_file', "
        "title TEXT DEFAULT '', "
        "url TEXT DEFAULT '', "
        "domain TEXT DEFAULT '', "
        "author TEXT DEFAULT '', "
        "published_at TEXT DEFAULT '', "
        "summary TEXT DEFAULT '', "
        "status TEXT DEFAULT 'active', "
        "trust_level TEXT DEFAULT 'medium', "
        "created_by TEXT DEFAULT '', "
        "updated_by TEXT DEFAULT '', "
        "disabled_reason TEXT DEFAULT '', "
        "disabled_by TEXT DEFAULT '', "
        "disabled_at DATETIME, "
        "latest_seen DATETIME, "
        "meta_json TEXT DEFAULT '{}', "
        "created_at DATETIME, "
        "updated_at DATETIME"
        ")"
    ))
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS knowledge_chunks ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "document_id INTEGER NOT NULL, "
        "chunk_id TEXT NOT NULL, "
        "order_index INTEGER DEFAULT 0, "
        "title TEXT DEFAULT '', "
        "text TEXT DEFAULT '', "
        "citation_json TEXT DEFAULT '{}', "
        "status TEXT DEFAULT 'active', "
        "trust_level TEXT DEFAULT 'medium', "
        "meta_json TEXT DEFAULT '{}', "
        "created_at DATETIME, "
        "updated_at DATETIME, "
        "UNIQUE(document_id, chunk_id)"
        ")"
    ))
    _add_missing_columns(conn, "knowledge_documents", {
        "created_by": "TEXT DEFAULT ''",
        "updated_by": "TEXT DEFAULT ''",
        "disabled_reason": "TEXT DEFAULT ''",
        "disabled_by": "TEXT DEFAULT ''",
        "disabled_at": "DATETIME",
    })
    _create_indexes(conn, [
        "CREATE INDEX IF NOT EXISTS idx_knowledge_sources_status ON knowledge_sources(status)",
        "CREATE INDEX IF NOT EXISTS idx_knowledge_documents_status ON knowledge_documents(status)",
        "CREATE INDEX IF NOT EXISTS idx_knowledge_documents_trust_date ON knowledge_documents(trust_level, published_at)",
        "CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_doc ON knowledge_chunks(document_id, order_index)",
        "CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_status ON knowledge_chunks(status)",
    ])


def _runtime_tool_decision_platform_column(conn: Any, engine: Any, db_path: str | None) -> None:
    _add_missing_columns(conn, "runtime_tool_decisions", {
        "platform": "TEXT DEFAULT ''",
    })


def _web_search_provider_usage_table(conn: Any, engine: Any, db_path: str | None) -> None:
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS web_search_provider_usage ("
        "provider_id TEXT PRIMARY KEY, "
        "total_calls INTEGER DEFAULT 0, "
        "success_calls INTEGER DEFAULT 0, "
        "failure_calls INTEGER DEFAULT 0, "
        "last_called_at DATETIME, "
        "last_success_at DATETIME, "
        "last_error_at DATETIME, "
        "last_error_code TEXT DEFAULT '', "
        "last_duration_ms INTEGER DEFAULT 0, "
        "updated_at DATETIME"
        ")"
    ))


def _proactive_outreach_log_table(conn: Any, engine: Any, db_path: str | None) -> None:
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS proactive_outreach_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id TEXT, "
        "idempotency_key TEXT UNIQUE, "
        "grounding_json TEXT DEFAULT '{}', "
        "judge_should BOOLEAN DEFAULT 0, "
        "judge_reason TEXT DEFAULT '', "
        "next_check_at DATETIME, "
        "next_intent TEXT DEFAULT '', "
        "message TEXT DEFAULT '', "
        "status TEXT DEFAULT 'pending', "
        "forced BOOLEAN DEFAULT 0, "
        "created_at DATETIME"
        ")"
    ))
    _create_indexes(conn, [
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_proactive_outreach_log_idempotency_key "
        "ON proactive_outreach_log(idempotency_key)",
        "CREATE INDEX IF NOT EXISTS ix_proactive_outreach_log_user_id "
        "ON proactive_outreach_log(user_id)",
        "CREATE INDEX IF NOT EXISTS ix_proactive_outreach_log_status "
        "ON proactive_outreach_log(status)",
        "CREATE INDEX IF NOT EXISTS ix_proactive_outreach_log_created_at "
        "ON proactive_outreach_log(created_at)",
    ])


_INBOUND_CLAIM_COLUMNS = (
    ("id", "INTEGER", 1, None, 1),
    ("platform", "VARCHAR(32)", 1, None, 0),
    ("chat_type", "VARCHAR(16)", 1, None, 0),
    ("session_id", "VARCHAR(255)", 1, None, 0),
    ("message_id", "VARCHAR(255)", 1, None, 0),
    ("status", "VARCHAR(16)", 1, "'processing'", 0),
    ("owner_token", "VARCHAR(64)", 1, None, 0),
    ("lease_expires_at", "DATETIME", 0, None, 0),
    ("response_json", "TEXT", 1, "''", 0),
    ("error_summary", "TEXT", 1, "''", 0),
    ("attempt_count", "INTEGER", 1, "1", 0),
    ("created_at", "DATETIME", 1, "current_timestamp", 0),
    ("updated_at", "DATETIME", 1, "current_timestamp", 0),
    ("completed_at", "DATETIME", 0, None, 0),
)

_PROACTIVE_OUTREACH_LEASE_COLUMNS = (
    ("user_id", "VARCHAR(255)", 1, None, 1),
    ("owner_token", "VARCHAR(64)", 1, None, 0),
    ("lease_expires_at", "DATETIME", 1, None, 0),
    ("created_at", "DATETIME", 1, "current_timestamp", 0),
    ("updated_at", "DATETIME", 1, "current_timestamp", 0),
)


_SQL_KEYWORDS = {
    "autoincrement",
    "check",
    "collate",
    "constraint",
    "current_date",
    "current_time",
    "current_timestamp",
    "in",
    "integer",
    "key",
    "not",
    "null",
    "primary",
}
_SQLToken = tuple[str, str]
_SQLITE_ASCII_IDENTIFIER_CASE_MAP = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "abcdefghijklmnopqrstuvwxyz",
)


def _canonicalize_sqlite_identifier(value: str) -> str:
    """按 SQLite 默认规则仅折叠标识符中的 ASCII 大写字母。"""
    return value.translate(_SQLITE_ASCII_IDENTIFIER_CASE_MAP)


def _tokenize_sql(value: str) -> tuple[_SQLToken, ...]:
    """切分 SQLite DDL，并保留 literal 与 quoted identifier 的语义边界。"""
    tokens: list[_SQLToken] = []
    index = 0
    length = len(value)
    while index < length:
        char = value[index]
        if char.isspace():
            index += 1
            continue
        if value.startswith("--", index):
            newline = value.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if value.startswith("/*", index):
            comment_end = value.find("*/", index + 2)
            if comment_end < 0:
                tokens.append(("invalid", value[index:]))
                break
            index = comment_end + 2
            continue
        if char == "'":
            start = index
            index += 1
            closed = False
            while index < length:
                if value[index] != "'":
                    index += 1
                    continue
                if index + 1 < length and value[index + 1] == "'":
                    index += 2
                    continue
                index += 1
                closed = True
                break
            if not closed:
                tokens.append(("invalid", value[start:]))
                break
            tokens.append(("literal", value[start:index]))
            continue
        if char in ('"', "`"):
            delimiter = char
            identifier: list[str] = []
            index += 1
            closed = False
            while index < length:
                if value[index] != delimiter:
                    identifier.append(value[index])
                    index += 1
                    continue
                if index + 1 < length and value[index + 1] == delimiter:
                    identifier.append(delimiter)
                    index += 2
                    continue
                index += 1
                closed = True
                break
            if not closed:
                tokens.append(("invalid", "".join(identifier)))
                break
            tokens.append((
                "identifier",
                _canonicalize_sqlite_identifier("".join(identifier)),
            ))
            continue
        if char == "[":
            closing = value.find("]", index + 1)
            if closing < 0:
                tokens.append(("invalid", value[index:]))
                break
            tokens.append((
                "identifier",
                _canonicalize_sqlite_identifier(value[index + 1:closing]),
            ))
            index = closing + 1
            continue
        if char.isalpha() or char == "_" or ord(char) >= 128:
            start = index
            index += 1
            while index < length:
                current = value[index]
                if not (
                    current.isalnum()
                    or current in "_$"
                    or ord(current) >= 128
                ):
                    break
                index += 1
            word = _canonicalize_sqlite_identifier(value[start:index])
            kind = "keyword" if word in _SQL_KEYWORDS else "identifier"
            tokens.append((kind, word))
            continue
        if char.isdigit():
            start = index
            index += 1
            while index < length and (value[index].isalnum() or value[index] in "._"):
                index += 1
            tokens.append(("number", value[start:index].casefold()))
            continue
        tokens.append(("symbol", char))
        index += 1
    return tuple(tokens)


def _tokens_have_wrapping_parentheses(tokens: tuple[_SQLToken, ...]) -> bool:
    if len(tokens) < 2 or tokens[0] != ("symbol", "(") or tokens[-1] != ("symbol", ")"):
        return False
    depth = 0
    for index, token in enumerate(tokens):
        if token == ("symbol", "("):
            depth += 1
        elif token == ("symbol", ")"):
            depth -= 1
            if depth == 0 and index != len(tokens) - 1:
                return False
            if depth < 0:
                return False
    return depth == 0


def _normalize_schema_default(value: Any) -> tuple[_SQLToken, ...] | None:
    if value is None:
        return None
    tokens = _tokenize_sql(str(value))
    while _tokens_have_wrapping_parentheses(tokens):
        tokens = tokens[1:-1]
    return tokens


def _count_token_sequence(
    tokens: tuple[_SQLToken, ...],
    expected: tuple[_SQLToken, ...],
) -> int:
    if not expected or len(expected) > len(tokens):
        return 0
    return sum(
        tokens[index:index + len(expected)] == expected
        for index in range(len(tokens) - len(expected) + 1)
    )


def _validate_inbound_claim_table(conn: Any) -> None:
    rows = conn.execute(text("PRAGMA table_xinfo(inbound_message_claims)")).mappings().all()
    hidden_columns = [
        (str(row["name"]), int(row["hidden"]))
        for row in rows
        if int(row["hidden"]) != 0
    ]
    if hidden_columns:
        raise SchemaMigrationValidationError(
            "inbound_message_claims 不允许 hidden 隐藏或生成列: "
            f"{hidden_columns!r}"
        )
    raw_actual_names = tuple(str(row["name"]) for row in rows)
    actual_names = tuple(
        _canonicalize_sqlite_identifier(name) for name in raw_actual_names
    )
    expected_names = tuple(
        _canonicalize_sqlite_identifier(item[0])
        for item in _INBOUND_CLAIM_COLUMNS
    )
    if actual_names != expected_names:
        raise SchemaMigrationValidationError(
            "inbound_message_claims 列集合或顺序不符合契约: "
            f"expected={expected_names!r} actual={raw_actual_names!r}"
        )

    for row, expected in zip(rows, _INBOUND_CLAIM_COLUMNS, strict=True):
        name, column_type, not_null, default, primary_key = expected
        actual = (
            str(row["type"]).upper().replace(" ", ""),
            int(row["notnull"]),
            _normalize_schema_default(row["dflt_value"]),
            int(row["pk"]),
        )
        required = (
            column_type,
            not_null,
            _normalize_schema_default(default),
            primary_key,
        )
        if actual != required:
            raise SchemaMigrationValidationError(
                f"inbound_message_claims.{name} 定义不符合契约: "
                f"expected={required!r} actual={actual!r}"
            )

    table_sql = conn.execute(text(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND lower(name) = 'inbound_message_claims'"
    )).scalar_one_or_none()
    if not isinstance(table_sql, str):
        raise SchemaMigrationValidationError("inbound_message_claims 缺少建表 DDL")
    table_tokens = _tokenize_sql(table_sql)
    if any(kind == "invalid" for kind, _value in table_tokens):
        raise SchemaMigrationValidationError(
            "inbound_message_claims 建表 DDL 包含未闭合的注释或引号"
        )
    if ("keyword", "collate") in table_tokens:
        raise SchemaMigrationValidationError(
            "inbound_message_claims 不允许 COLLATE 声明，所有列必须使用默认 BINARY"
        )
    triggers = conn.execute(text(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'trigger' AND lower(tbl_name) = 'inbound_message_claims'"
    )).scalars().all()
    if triggers:
        raise SchemaMigrationValidationError(
            "inbound_message_claims 不允许 trigger 触发器: "
            f"{sorted(str(name) for name in triggers)!r}"
        )
    autoincrement_tokens = _tokenize_sql(
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT"
    )
    if _count_token_sequence(table_tokens, autoincrement_tokens) != 1:
        raise SchemaMigrationValidationError(
            "inbound_message_claims.id 必须显式使用 "
            "INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT"
        )
    required_checks = (
        "CONSTRAINT ck_inbound_message_claim_status "
        "CHECK (status IN ('processing', 'completed', 'failed'))",
        "CONSTRAINT ck_inbound_message_claim_attempt_count "
        "CHECK (attempt_count >= 1)",
    )
    for check_sql in required_checks:
        if _count_token_sequence(table_tokens, _tokenize_sql(check_sql)) != 1:
            raise SchemaMigrationValidationError(
                "inbound_message_claims 缺少或错误的具名 CHECK: " + check_sql
            )

    check_count = sum(
        table_tokens[index] == ("keyword", "check")
        and table_tokens[index + 1] == ("symbol", "(")
        for index in range(len(table_tokens) - 1)
    )
    check_names: set[str] = set()
    for index in range(len(table_tokens) - 3):
        if (
            table_tokens[index] == ("keyword", "constraint")
            and table_tokens[index + 1][0] == "identifier"
            and table_tokens[index + 2] == ("keyword", "check")
            and table_tokens[index + 3] == ("symbol", "(")
        ):
            check_names.add(table_tokens[index + 1][1])
    expected_check_names = {
        "ck_inbound_message_claim_status",
        "ck_inbound_message_claim_attempt_count",
    }
    if check_count != 2 or check_names != expected_check_names:
        raise SchemaMigrationValidationError(
            "inbound_message_claims CHECK 集合不符合契约: "
            f"count={check_count} names={sorted(check_names)!r}"
        )

    foreign_keys = conn.execute(text(
        "PRAGMA foreign_key_list(inbound_message_claims)"
    )).mappings().all()
    if foreign_keys:
        raise SchemaMigrationValidationError(
            "inbound_message_claims 不允许 FOREIGN KEY 外键约束"
        )


def _validate_inbound_claim_indexes(conn: Any) -> None:
    index_rows = conn.execute(text(
        "PRAGMA index_list(inbound_message_claims)"
    )).mappings().all()
    indexes: dict[str, tuple[str, Any]] = {}
    duplicate_index_names: list[str] = []
    for row in index_rows:
        original_name = str(row["name"])
        normalized_name = _canonicalize_sqlite_identifier(original_name)
        if normalized_name in indexes:
            duplicate_index_names.append(original_name)
            continue
        indexes[normalized_name] = (original_name, row)
    if duplicate_index_names:
        raise SchemaMigrationValidationError(
            "inbound_message_claims 存在大小写等价的重复索引名: "
            f"{sorted(duplicate_index_names)!r}"
        )
    extra_unique_indexes = sorted(
        original_name
        for normalized_name, (original_name, row) in indexes.items()
        if int(row["unique"]) == 1
        and normalized_name
        != _canonicalize_sqlite_identifier("uq_inbound_message_claim_identity")
    )
    if extra_unique_indexes:
        raise SchemaMigrationValidationError(
            "inbound_message_claims 不允许额外唯一 unique 索引: "
            f"{extra_unique_indexes!r}"
        )
    expected_indexes = {
        "uq_inbound_message_claim_identity": (
            1,
            ("platform", "chat_type", "session_id", "message_id"),
        ),
        "ix_inbound_message_claim_status_lease": (
            0,
            ("status", "lease_expires_at"),
        ),
    }
    for index_name, (unique, columns) in expected_indexes.items():
        index_entry = indexes.get(_canonicalize_sqlite_identifier(index_name))
        if index_entry is None:
            raise SchemaMigrationValidationError(
                f"inbound_message_claims 缺少索引 {index_name}"
            )
        original_index_name, row = index_entry
        actual_unique = int(row["unique"])
        actual_origin = str(row["origin"])
        actual_partial = int(row["partial"])
        escaped_index_name = original_index_name.replace("'", "''")
        xinfo_rows = conn.execute(text(
            f"PRAGMA index_xinfo('{escaped_index_name}')"
        )).mappings().all()
        key_rows = sorted(
            (item for item in xinfo_rows if int(item["key"]) == 1),
            key=lambda item: int(item["seqno"]),
        )
        auxiliary_rows = [item for item in xinfo_rows if int(item["key"]) == 0]
        actual_columns = tuple(
            None
            if item["name"] is None
            else _canonicalize_sqlite_identifier(str(item["name"]))
            for item in key_rows
        )
        expected_columns = tuple(
            _canonicalize_sqlite_identifier(column) for column in columns
        )
        key_semantics_valid = all(
            item["name"] is not None
            and int(item["cid"]) >= 0
            and _canonicalize_sqlite_identifier(str(item["coll"])) == "binary"
            and int(item["desc"]) == 0
            and int(item["key"]) == 1
            for item in key_rows
        )
        auxiliary_rows_valid = (
            len(auxiliary_rows) == 1
            and int(auxiliary_rows[0]["cid"]) == -1
            and auxiliary_rows[0]["name"] is None
            and _canonicalize_sqlite_identifier(
                str(auxiliary_rows[0]["coll"])
            ) == "binary"
            and int(auxiliary_rows[0]["desc"]) == 0
            and int(auxiliary_rows[0]["key"]) == 0
        )
        if (
            actual_unique != unique
            or actual_origin != "c"
            or actual_partial != 0
            or actual_columns != expected_columns
            or not key_semantics_valid
            or not auxiliary_rows_valid
        ):
            raise SchemaMigrationValidationError(
                f"inbound_message_claims 索引 {index_name} 不符合契约: "
                f"unique={actual_unique} origin={actual_origin!r} "
                f"partial={actual_partial} columns={actual_columns!r} "
                f"key_semantics_valid={key_semantics_valid} "
                f"auxiliary_rows_valid={auxiliary_rows_valid}"
            )


def _inbound_message_claims_table(conn: Any, engine: Any, db_path: str | None) -> None:
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS inbound_message_claims ("
        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
        "platform VARCHAR(32) NOT NULL, "
        "chat_type VARCHAR(16) NOT NULL, "
        "session_id VARCHAR(255) NOT NULL, "
        "message_id VARCHAR(255) NOT NULL, "
        "status VARCHAR(16) NOT NULL DEFAULT 'processing', "
        "owner_token VARCHAR(64) NOT NULL, "
        "lease_expires_at DATETIME, "
        "response_json TEXT NOT NULL DEFAULT '', "
        "error_summary TEXT NOT NULL DEFAULT '', "
        "attempt_count INTEGER NOT NULL DEFAULT 1, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "completed_at DATETIME, "
        "CONSTRAINT ck_inbound_message_claim_status "
        "CHECK (status IN ('processing', 'completed', 'failed')), "
        "CONSTRAINT ck_inbound_message_claim_attempt_count "
        "CHECK (attempt_count >= 1)"
        ")"
    ))
    _validate_inbound_claim_table(conn)
    _create_indexes(conn, [
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_inbound_message_claim_identity "
        "ON inbound_message_claims(platform, chat_type, session_id, message_id)",
        "CREATE INDEX IF NOT EXISTS ix_inbound_message_claim_status_lease "
        "ON inbound_message_claims(status, lease_expires_at)",
    ])
    _validate_inbound_claim_indexes(conn)


def _validate_proactive_outreach_lease_table(conn: Any) -> None:
    rows = conn.execute(
        text("PRAGMA table_xinfo(proactive_outreach_leases)")
    ).mappings().all()
    hidden_columns = [
        (str(row["name"]), int(row["hidden"]))
        for row in rows
        if int(row["hidden"]) != 0
    ]
    if hidden_columns:
        raise SchemaMigrationValidationError(
            "proactive_outreach_leases 不允许 hidden 隐藏或生成列: "
            f"{hidden_columns!r}"
        )

    raw_actual_names = tuple(str(row["name"]) for row in rows)
    actual_names = tuple(name.casefold() for name in raw_actual_names)
    expected_names = tuple(
        item[0].casefold() for item in _PROACTIVE_OUTREACH_LEASE_COLUMNS
    )
    if actual_names != expected_names:
        raise SchemaMigrationValidationError(
            "proactive_outreach_leases 列集合或顺序不符合契约: "
            f"expected={expected_names!r} actual={raw_actual_names!r}"
        )

    for row, expected in zip(
        rows,
        _PROACTIVE_OUTREACH_LEASE_COLUMNS,
        strict=True,
    ):
        name, column_type, not_null, default, primary_key = expected
        actual = (
            str(row["type"]).upper().replace(" ", ""),
            int(row["notnull"]),
            _normalize_schema_default(row["dflt_value"]),
            int(row["pk"]),
        )
        required = (
            column_type,
            not_null,
            _normalize_schema_default(default),
            primary_key,
        )
        if actual != required:
            raise SchemaMigrationValidationError(
                f"proactive_outreach_leases.{name} 定义不符合契约: "
                f"expected={required!r} actual={actual!r}"
            )

    table_sql = conn.execute(text(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND lower(name) = 'proactive_outreach_leases'"
    )).scalar_one_or_none()
    if not isinstance(table_sql, str):
        raise SchemaMigrationValidationError(
            "proactive_outreach_leases 缺少建表 DDL"
        )
    table_tokens = _tokenize_sql(table_sql)
    if any(kind == "invalid" for kind, _value in table_tokens):
        raise SchemaMigrationValidationError(
            "proactive_outreach_leases 建表 DDL 包含未闭合的注释或引号"
        )
    if ("keyword", "collate") in table_tokens:
        raise SchemaMigrationValidationError(
            "proactive_outreach_leases 不允许 COLLATE 声明"
        )
    if ("keyword", "check") in table_tokens:
        raise SchemaMigrationValidationError(
            "proactive_outreach_leases 不允许 CHECK 约束"
        )
    triggers = conn.execute(text(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'trigger' AND lower(tbl_name) = 'proactive_outreach_leases'"
    )).scalars().all()
    if triggers:
        raise SchemaMigrationValidationError(
            "proactive_outreach_leases 不允许 trigger 触发器: "
            f"{sorted(str(name) for name in triggers)!r}"
        )
    foreign_keys = conn.execute(
        text("PRAGMA foreign_key_list(proactive_outreach_leases)")
    ).mappings().all()
    if foreign_keys:
        raise SchemaMigrationValidationError(
            "proactive_outreach_leases 不允许 FOREIGN KEY 外键约束"
        )


def _validate_proactive_outreach_lease_indexes(conn: Any) -> None:
    index_rows = conn.execute(
        text("PRAGMA index_list(proactive_outreach_leases)")
    ).mappings().all()
    indexes = {
        str(row["name"]).casefold(): (str(row["name"]), row)
        for row in index_rows
    }
    index_name = "ix_proactive_outreach_lease_expires_at"
    unexpected_indexes = sorted(
        str(row["name"])
        for row in index_rows
        if str(row["origin"]) != "pk"
        and str(row["name"]).casefold() != index_name.casefold()
    )
    if unexpected_indexes:
        raise SchemaMigrationValidationError(
            "proactive_outreach_leases 不允许额外索引: "
            f"{unexpected_indexes!r}"
        )
    extra_unique_indexes = sorted(
        original_name
        for original_name, row in (
            (str(item["name"]), item) for item in index_rows
        )
        if int(row["unique"]) == 1 and str(row["origin"]) != "pk"
    )
    if extra_unique_indexes:
        raise SchemaMigrationValidationError(
            "proactive_outreach_leases 不允许额外唯一 unique 索引: "
            f"{extra_unique_indexes!r}"
        )
    index_entry = indexes.get(index_name.casefold())
    if index_entry is None:
        raise SchemaMigrationValidationError(
            f"proactive_outreach_leases 缺少索引 {index_name}"
        )

    original_name, row = index_entry
    escaped_name = original_name.replace("'", "''")
    xinfo_rows = conn.execute(
        text(f"PRAGMA index_xinfo('{escaped_name}')")
    ).mappings().all()
    key_rows = sorted(
        (item for item in xinfo_rows if int(item["key"]) == 1),
        key=lambda item: int(item["seqno"]),
    )
    auxiliary_rows = [item for item in xinfo_rows if int(item["key"]) == 0]
    actual_columns = tuple(
        None if item["name"] is None else str(item["name"]).casefold()
        for item in key_rows
    )
    key_semantics_valid = all(
        item["name"] is not None
        and int(item["cid"]) >= 0
        and str(item["coll"]).upper() == "BINARY"
        and int(item["desc"]) == 0
        for item in key_rows
    )
    auxiliary_rows_valid = (
        len(auxiliary_rows) == 1
        and int(auxiliary_rows[0]["cid"]) == -1
        and auxiliary_rows[0]["name"] is None
        and str(auxiliary_rows[0]["coll"]).upper() == "BINARY"
        and int(auxiliary_rows[0]["desc"]) == 0
    )
    if (
        int(row["unique"]) != 0
        or str(row["origin"]) != "c"
        or int(row["partial"]) != 0
        or actual_columns != ("lease_expires_at",)
        or not key_semantics_valid
        or not auxiliary_rows_valid
    ):
        raise SchemaMigrationValidationError(
            f"proactive_outreach_leases 索引 {index_name} 不符合契约: "
            f"unique={int(row['unique'])} origin={str(row['origin'])!r} "
            f"partial={int(row['partial'])} columns={actual_columns!r}"
        )


def _proactive_outreach_leases_table(conn: Any, engine: Any, db_path: str | None) -> None:
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS proactive_outreach_leases ("
        "user_id VARCHAR(255) NOT NULL PRIMARY KEY, "
        "owner_token VARCHAR(64) NOT NULL, "
        "lease_expires_at DATETIME NOT NULL, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    ))
    _validate_proactive_outreach_lease_table(conn)
    _create_indexes(conn, [
        "CREATE INDEX IF NOT EXISTS ix_proactive_outreach_lease_expires_at "
        "ON proactive_outreach_leases(lease_expires_at)",
    ])
    _validate_proactive_outreach_lease_indexes(conn)


MIGRATIONS: list[tuple[str, str, MigrationFn]] = [
    (_CHAT_LOG_METADATA_VERSION, "chat log metadata columns", _chat_log_metadata_columns),
    ("20260523_sticker_memory_columns", "sticker memory columns", _sticker_memory_columns),
    ("20260523_group_memory_columns", "group memory columns", _group_memory_columns),
    ("20260524_group_memory_governance_columns", "group memory governance columns", _group_memory_governance_columns),
    ("20260523_chat_stream_config_group_profile_mode", "chat stream group profile mode", _chat_stream_config_group_profile_mode),
    ("20260523_chat_log_session_message_index", "chat log session/message index", _chat_log_session_message_index),
    ("20260523_user_profile_columns", "user profile columns", _user_profile_columns),
    ("20260524_persona_fact_governance_columns", "persona fact governance columns", _persona_fact_governance_columns),
    ("20260523_expression_jargon_unique_indexes", "expression and jargon unique indexes", _expression_jargon_unique_indexes),
    ("20260523_agent_prompt_trace_columns", "agent/prompt trace columns", _agent_prompt_trace_columns),
    ("20260523_llm_request_log_columns", "llm api request log columns", _llm_request_log_columns),
    ("20260523_reply_contract_check_logs", "reply contract check log columns", _reply_contract_check_logs),
    ("20260528_reply_contract_check_count_columns", "reply contract check count columns", _reply_contract_check_count_columns),
    ("20260523_reply_eval_trace_columns", "reply eval trace columns", _reply_eval_trace_columns),
    ("20260525_rolling_session_summaries", "rolling session summaries", _rolling_session_summaries),
    ("20260526_session_summary_llm_columns", "session summary llm columns", _session_summary_llm_columns),
    ("20260526_session_summary_jobs", "session summary jobs", _session_summary_jobs),
    ("20260526_semantic_rag_tables", "semantic rag tables", _semantic_rag_tables),
    ("20260526_knowledge_library_tables", "knowledge library tables", _knowledge_library_tables),
    ("20260618_runtime_tool_decision_platform", "runtime tool decision platform column", _runtime_tool_decision_platform_column),
    ("20260706_web_search_provider_usage", "web search provider usage table", _web_search_provider_usage_table),
    ("20260706_proactive_outreach_log", "proactive outreach log table", _proactive_outreach_log_table),
    ("20260710_inbound_message_claims", "inbound message claims table", _inbound_message_claims_table),
    (
        "20260710_proactive_outreach_leases",
        "proactive outreach leases table",
        proactive_outreach_leases_table,
    ),
    (
        "20260711_chat_delivery_outbox",
        "chat delivery outbox table",
        chat_delivery_outbox_table,
    ),
]


def run_schema_migrations(engine: Any, *, db_path: str | None = None) -> None:
    with engine.connect() as conn:
        if "schema_migrations" in _table_names(conn):
            applied_before_transaction = {
                str(row[0])
                for row in conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
            }
        else:
            applied_before_transaction = set()

    if (
        _CHAT_LOG_METADATA_VERSION not in applied_before_transaction
        and _chat_log_metadata_needs_backup(engine)
    ):
        backup_path = _migration_backup_path(engine, db_path)
        if backup_path is not None:
            _backup_sqlite_db(backup_path)

    with engine.begin() as conn:
        applied = _applied_versions(conn)
        for version, name, fn in MIGRATIONS:
            if version in applied:
                continue
            fn(conn, engine, db_path)
            _record(conn, version, name)

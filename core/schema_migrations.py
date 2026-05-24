"""轻量级 schema migration runner。

用于替代继续在 `core.database.init_db()` 里追加热迁移逻辑。
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text


MigrationFn = Callable[[Any, Any, str | None], None]


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
        {"v": version, "n": name, "t": datetime.now()},
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


def _backup_sqlite_db(db_path: str | None) -> None:
    if not db_path:
        return

    path = Path(db_path)
    if not path.exists():
        return

    backup_path = path.with_name(f"{path.name}.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    try:
        shutil.copy2(path, backup_path)
        backups = sorted(
            [p for p in path.parent.iterdir() if p.name.startswith(f"{path.name}.bak.")],
            reverse=True,
        )
        for old in backups[5:]:
            old.unlink(missing_ok=True)
    except Exception as exc:  # pragma: no cover - 备份失败不应阻断迁移
        logging.getLogger("nanobot").warning("DB backup/cleanup failed (migration continues): %s", exc)


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

    needs_backup = False
    chat_columns = _columns(conn, "chat_logs")
    conv_columns = _columns(conn, "conversation_turns")
    if chat_columns:
        needs_backup = any(col not in chat_columns for col in chat_missing)
    if conv_columns:
        needs_backup = needs_backup or any(col not in conv_columns for col in conv_missing)
    if needs_backup:
        _backup_sqlite_db(db_path)

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


MIGRATIONS: list[tuple[str, str, MigrationFn]] = [
    ("20260523_chat_log_metadata_columns", "chat log metadata columns", _chat_log_metadata_columns),
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
    ("20260523_reply_eval_trace_columns", "reply eval trace columns", _reply_eval_trace_columns),
]


def run_schema_migrations(engine: Any, *, db_path: str | None = None) -> None:
    with engine.begin() as conn:
        applied = _applied_versions(conn)
        for version, name, fn in MIGRATIONS:
            if version in applied:
                continue
            fn(conn, engine, db_path)
            _record(conn, version, name)

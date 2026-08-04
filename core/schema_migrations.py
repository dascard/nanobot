"""轻量级 schema migration runner。

用于替代继续在 `core.database.init_db()` 里追加热迁移逻辑。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import inspect, text

from core.chat_delivery_outbox_schema import chat_delivery_outbox_table
from core.chat_stream_identity import (
    ChatStreamIdentity,
    ChatStreamIdentityError,
    canonicalize_legacy_chat_stream_id,
    parse_canonical_chat_stream_id,
    resolve_chat_stream_identity,
)
from core.outbound_delivery_schema import (
    OUTBOUND_DELIVERY_SCHEMA_VERSION,
    create_outbound_delivery_schema,
    outbound_delivery_schema_needs_backup,
)
from core.proactive_outreach_schema import proactive_outreach_leases_table
from core.schema_validation import (
    SchemaMigrationValidationError as SchemaMigrationValidationError,
)
from core.sqlite_backup import create_sqlite_snapshot
from core.sqlite_retry import run_sqlite_locked_retry
from core.time_utils import db_now_naive


MigrationFn = Callable[[Any, Any, str | None], None]
_CHAT_LOG_METADATA_VERSION = "20260523_chat_log_metadata_columns"
_SESSION_GUIDANCE_COLUMNS_VERSION = "20260712_chat_stream_session_guidance_columns"
_CHAT_STREAM_IDENTITY_VERSION = "20260712_chat_stream_identity_normalization"
_GROUP_MEMORY_CANONICAL_IDENTITY_VERSION = (
    "20260723_group_memory_canonical_identity"
)
_GROUP_LEARNING_STAGE7A_SCHEMA_VERSION = (
    "20260723_group_learning_stage7a_schema"
)
_GROUP_LEARNING_STAGE7B_REVIEW_VERSION = (
    "20260723_group_learning_stage7b_review_fields"
)
_GROUP_LEARNING_STAGE7C_SCHEDULE_VERSION = (
    "20260724_group_learning_stage7c_schedule_fencing"
)
_GROUP_LEARNING_STAGE7D_LEGACY_READ_ONLY_VERSION = (
    "20260724_group_learning_stage7d_legacy_read_only"
)
_ADMIN_IDEMPOTENCY_RECORDS_VERSION = (
    "20260724_admin_idempotency_records"
)
_SANDBOX_EXECUTION_PROFILES_AND_LEASES_VERSION = (
    "20260725_sandbox_execution_profiles_and_leases"
)
_SANDBOX_RUNTIME_PROJECT_QUOTAS_VERSION = (
    "20260725_sandbox_runtime_project_quotas"
)
_SANDBOX_LEASE_CONTROLLER_STATE_VERSION = (
    "20260725_sandbox_lease_controller_state"
)
_SANDBOX_WORKSPACE_QUOTA_MAINTENANCE_VERSION = (
    "20260725_sandbox_workspace_quota_maintenance"
)
_BLOCK_SESSION_MEMORY_VERSION = "20260726_block_session_memory_schema"
_SCHEDULED_TASK_SCHEDULE_COLUMNS_VERSION = (
    "20260726_scheduled_task_schedule_columns"
)
_SCHEDULED_TASK_OWNER_IDENTITY_VERSION = (
    "20260729_scheduled_task_owner_identity"
)
_SCHEDULED_TASK_WORKFLOW_EXECUTION_VERSION = (
    "20260729_scheduled_task_workflow_execution"
)
_LLM_REQUEST_EXECUTION_PHASE_VERSION = (
    "20260730_llm_request_execution_phase"
)
_GROUP_ROLLING_CHATLOG_SOURCE_VERSION = (
    "20260731_group_rolling_chatlog_source"
)
_CHAT_LOG_SESSION_ID_INDEX_VERSION = (
    "20260801_chat_log_session_id_index"
)
_LLM_CACHE_OBSERVABILITY_VERSION = (
    "20260802_llm_cache_observability"
)
_LLM_CACHE_DIAGNOSTICS_V2_VERSION = (
    "20260803_llm_cache_miss_and_shape"
)
_RUN_LEDGER_V1_VERSION = "20260804_run_event_ledger_v1"
_RUN_EVIDENCE_GOVERNANCE_V1_VERSION = "20260804_run_evidence_governance_v1"
_RUN_RECOVERY_V1_VERSION = "20260804_run_checkpoint_recovery_v1"
_RUN_DURABLE_TASK_V1_VERSION = "20260804_run_durable_task_v1"
_ARTIFACT_LIFECYCLE_V1_VERSION = "20260804_artifact_lifecycle_v1"
_LLM_PROVIDER_CACHE_PERFORMANCE_VERSION = (
    "20260804_llm_provider_cache_performance"
)
_SESSION_GOAL_PLAN_MODE_V1_VERSION = (
    "20260804_session_goal_plan_mode_v1"
)
_AGENT_SKILLS_LIFECYCLE_V1_VERSION = (
    "20260804_agent_skills_lifecycle_v1"
)
_AGENT_SKILLS_GOVERNANCE_V2_VERSION = (
    "20260804_agent_skills_governance_v2"
)
_MCP_CONTROL_PLANE_V1_VERSION = "20260804_mcp_control_plane_v1"
_RUNTIME_PERMISSION_GOVERNANCE_V1_VERSION = (
    "20260804_runtime_permission_governance_v1"
)
_SCHEMA_MIGRATION_LOCK_ATTEMPTS = 8
_SCHEMA_MIGRATION_LOCK_RETRY_DELAY_SECONDS = 0.05


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


def _existing_applied_versions(conn: Any) -> set[str]:
    """读取已存在的迁移版本，且不在备份前创建版本表。"""
    if "schema_migrations" not in _table_names(conn):
        return set()
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


def _parse_group_memory_identity(
    value: object,
    *,
    platform: str | None = None,
) -> ChatStreamIdentity:
    raw = str(value or "").strip()
    parts = raw.split(":")
    try:
        if len(parts) == 3 and parts[2] in {"group", "private"}:
            identity = parse_canonical_chat_stream_id(raw)
        else:
            identity = resolve_chat_stream_identity(
                platform=platform or "qq",
                chat_type="group",
                session_id=raw,
            )
    except ChatStreamIdentityError as exc:
        raise SchemaMigrationValidationError(
            "group_memories 包含无法规范化的群会话身份"
        ) from exc
    if identity.chat_type != "group":
        raise SchemaMigrationValidationError(
            "group_memories 包含非群聊 canonical 身份"
        )
    if platform is not None and identity.platform != platform:
        raise SchemaMigrationValidationError(
            "group_memories canonical 与兼容身份投影不一致"
        )
    return identity


def _validate_group_memory_canonical_index(conn: Any) -> None:
    expected_name = "uq_group_memory_canonical_hash"
    expected_columns = [
        "chat_stream_id",
        "memory_type",
        "content_hash",
    ]
    for index in inspect(conn).get_indexes("group_memories"):
        if index["name"] != expected_name:
            continue
        if not index["unique"] or index["column_names"] != expected_columns:
            raise SchemaMigrationValidationError(
                "group_memories canonical 唯一索引定义不符合契约"
            )
        return
    raise SchemaMigrationValidationError(
        "group_memories 缺少 canonical 唯一索引"
    )


def _group_memory_canonical_identity(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    """回填 GroupMemory canonical 身份并固定兼容投影。"""

    columns = _columns(conn, "group_memories")
    if not columns:
        return
    required = {"id", "group_id", "memory_type", "content_hash"}
    if not required <= columns:
        raise SchemaMigrationValidationError(
            "group_memories 缺少 canonical 身份迁移所需字段"
        )

    canonical_select = (
        "chat_stream_id"
        if "chat_stream_id" in columns
        else "NULL AS chat_stream_id"
    )
    rows = conn.execute(text(
        "SELECT id, group_id, memory_type, content_hash, "
        f"{canonical_select} FROM group_memories ORDER BY id"
    )).mappings().all()

    updates: list[dict[str, object]] = []
    canonical_keys: set[tuple[str, str, str]] = set()
    for row in rows:
        existing_canonical = str(row["chat_stream_id"] or "").strip()
        if existing_canonical:
            try:
                identity = parse_canonical_chat_stream_id(
                    existing_canonical
                )
            except ChatStreamIdentityError as exc:
                raise SchemaMigrationValidationError(
                    "group_memories 包含非法 canonical 身份"
                ) from exc
            if identity.chat_type != "group":
                raise SchemaMigrationValidationError(
                    "group_memories 包含非群聊 canonical 身份"
                )
            projected = _parse_group_memory_identity(
                row["group_id"],
                platform=identity.platform,
            )
            if projected.chat_stream_id != identity.chat_stream_id:
                raise SchemaMigrationValidationError(
                    "group_memories canonical 与兼容身份投影不一致"
                )
        else:
            identity = _parse_group_memory_identity(row["group_id"])

        key = (
            identity.chat_stream_id,
            str(row["memory_type"] or ""),
            str(row["content_hash"] or ""),
        )
        if key in canonical_keys:
            raise SchemaMigrationValidationError(
                "group_memories canonical 身份冲突，拒绝自动合并"
            )
        canonical_keys.add(key)
        updates.append({
            "id": int(row["id"]),
            "chat_stream_id": identity.chat_stream_id,
            "group_id": (
                identity.legacy_runtime_session_id
                if identity.platform == "qq"
                else identity.chat_stream_id
            ),
        })

    _add_missing_columns(
        conn,
        "group_memories",
        {"chat_stream_id": "TEXT"},
    )
    for update in updates:
        result = conn.execute(
            text(
                "UPDATE group_memories "
                "SET chat_stream_id = :chat_stream_id, "
                "group_id = :group_id WHERE id = :id"
            ),
            update,
        )
        if result.rowcount != 1:
            raise SchemaMigrationValidationError(
                "group_memories canonical 身份回填行数异常"
            )
    _create_indexes(conn, [
        "CREATE INDEX IF NOT EXISTS ix_group_memories_chat_stream_id "
        "ON group_memories(chat_stream_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_group_memory_canonical_hash "
        "ON group_memories(chat_stream_id, memory_type, content_hash)",
    ])
    _validate_group_memory_canonical_index(conn)


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


def _chat_stream_session_guidance_columns(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    _add_missing_columns(conn, "chat_stream_configs", {
        "session_guidance": "TEXT NOT NULL DEFAULT ''",
        "session_guidance_updated_at": "TIMESTAMP",
    })


def _chat_stream_identity_rename_candidates(conn: Any) -> list[tuple[str, str]]:
    if "chat_stream_configs" not in _table_names(conn):
        return []

    existing_ids = {
        str(row[0])
        for row in conn.execute(text(
            "SELECT chat_stream_id FROM chat_stream_configs"
        )).fetchall()
        if row[0] is not None
    }
    aliases_by_target: dict[str, list[str]] = {}
    for alias in sorted(existing_ids):
        canonical = canonicalize_legacy_chat_stream_id(alias)
        if canonical is None or canonical in existing_ids:
            continue
        aliases_by_target.setdefault(canonical, []).append(alias)

    return [
        (aliases[0], canonical)
        for canonical, aliases in sorted(aliases_by_target.items())
        if len(aliases) == 1
    ]


def _rename_chat_stream_identity_alias(
    conn: Any,
    alias: str,
    canonical: str,
) -> None:
    result = conn.execute(
        text(
            "UPDATE chat_stream_configs SET chat_stream_id = :canonical "
            "WHERE chat_stream_id = :alias"
        ),
        {"alias": alias, "canonical": canonical},
    )
    if result.rowcount != 1:
        raise SchemaMigrationValidationError(
            f"chat_stream_configs identity 更新行数异常: {alias!r} -> {canonical!r}"
        )


def _chat_stream_identity_normalization(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    for alias, canonical in _chat_stream_identity_rename_candidates(conn):
        _rename_chat_stream_identity_alias(conn, alias, canonical)


def _chat_stream_identity_needs_backup(conn: Any) -> bool:
    return bool(_chat_stream_identity_rename_candidates(conn))


def _chat_log_session_message_index(conn: Any, engine: Any, db_path: str | None) -> None:
    if "chat_logs" in _table_names(conn):
        _create_indexes(conn, [
            "CREATE INDEX IF NOT EXISTS idx_cl_session_msg ON chat_logs(session_id, message_id)"
        ])


def _chat_log_session_id_index(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    if "chat_logs" in _table_names(conn):
        _create_indexes(conn, [
            "CREATE INDEX IF NOT EXISTS "
            "idx_cl_session_id ON chat_logs(session_id, id)"
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


def _prompt_template_resolution_columns(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    columns = {
        "prompt_template_resolutions_json": "TEXT NOT NULL DEFAULT '{}'",
    }
    _add_missing_columns(conn, "agent_runs", columns)
    _add_missing_columns(conn, "prompt_render_logs", columns)


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


def _llm_request_execution_phase(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    _add_missing_columns(conn, "llm_api_request_logs", {
        "phase": "TEXT DEFAULT ''",
        "round_index": "INTEGER DEFAULT 0",
        "route_attempt_index": "INTEGER DEFAULT 0",
    })
    if "llm_api_request_logs" in _table_names(conn):
        _create_indexes(conn, [
            "CREATE INDEX IF NOT EXISTS idx_llm_api_request_phase "
            "ON llm_api_request_logs(phase)",
        ])


def _llm_cache_observability(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    """增加模型缓存命中字段，并从历史响应中尽可能回填。"""

    from foundation.llm.cache_usage import (
        CACHE_STATUS_PENDING,
        normalize_llm_cache_usage,
    )

    _add_missing_columns(conn, "llm_api_request_logs", {
        "cache_status": "TEXT NOT NULL DEFAULT 'pending'",
        "cache_hit": "BOOLEAN",
        "cache_hit_tokens": "INTEGER NOT NULL DEFAULT 0",
        "cache_miss_tokens": "INTEGER NOT NULL DEFAULT 0",
        "cache_write_tokens": "INTEGER NOT NULL DEFAULT 0",
        "cache_details_json": "TEXT NOT NULL DEFAULT '{}'",
    })
    if "llm_api_request_logs" not in _table_names(conn):
        return

    _create_indexes(conn, [
        "CREATE INDEX IF NOT EXISTS idx_llm_api_request_cache_status "
        "ON llm_api_request_logs(cache_status)",
    ])
    required_columns = {"id", "status", "response_json"}
    if not required_columns <= _columns(conn, "llm_api_request_logs"):
        return

    update_statement = text(
        "UPDATE llm_api_request_logs SET "
        "cache_status = :cache_status, cache_hit = :cache_hit, "
        "cache_hit_tokens = :cache_hit_tokens, "
        "cache_miss_tokens = :cache_miss_tokens, "
        "cache_write_tokens = :cache_write_tokens, "
        "cache_details_json = :cache_details_json WHERE id = :id"
    )
    last_id = -(2**63)
    while True:
        rows = conn.execute(text(
            "SELECT id, status, response_json FROM llm_api_request_logs "
            "WHERE id > :last_id ORDER BY id LIMIT 500"
        ), {"last_id": last_id}).mappings().all()
        if not rows:
            break

        updates: list[dict[str, Any]] = []
        for row in rows:
            call_status = str(row.get("status") or "")
            if call_status in {"created", "stream_created", ""}:
                updates.append({
                    "id": row["id"],
                    "cache_status": CACHE_STATUS_PENDING,
                    "cache_hit": None,
                    "cache_hit_tokens": 0,
                    "cache_miss_tokens": 0,
                    "cache_write_tokens": 0,
                    "cache_details_json": "{}",
                })
                continue
            try:
                response = json.loads(str(row.get("response_json") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                response = {}
            cache_usage = normalize_llm_cache_usage(
                response,
                successful=call_status in {"success", "stream_success"},
            )
            updates.append({
                "id": row["id"],
                "cache_status": cache_usage.status,
                "cache_hit": cache_usage.hit,
                "cache_hit_tokens": cache_usage.hit_tokens,
                "cache_miss_tokens": cache_usage.miss_tokens,
                "cache_write_tokens": cache_usage.write_tokens,
                "cache_details_json": json.dumps(
                    cache_usage.details,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            })
        conn.execute(update_statement, updates)
        last_id = int(rows[-1]["id"])


def _llm_cache_diagnostics_v2(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    """补齐 DeepSeek miss tokens，并回填无正文缓存形状。"""

    from foundation.llm.cache_shape import build_llm_cache_shape
    from foundation.llm.cache_usage import normalize_llm_cache_usage

    _add_missing_columns(conn, "llm_api_request_logs", {
        "cache_miss_tokens": "INTEGER NOT NULL DEFAULT 0",
    })
    if "llm_api_request_logs" not in _table_names(conn):
        return
    required = {
        "id",
        "status",
        "source",
        "model",
        "request_json",
        "response_json",
        "cache_details_json",
    }
    if not required <= _columns(conn, "llm_api_request_logs"):
        return

    update_statement = text(
        "UPDATE llm_api_request_logs SET "
        "cache_miss_tokens = :cache_miss_tokens, "
        "cache_details_json = :cache_details_json WHERE id = :id"
    )
    last_id = -(2**63)
    while True:
        rows = conn.execute(text(
            "SELECT id, status, source, model, request_json, response_json, "
            "cache_details_json FROM llm_api_request_logs "
            "WHERE id > :last_id ORDER BY id LIMIT 500"
        ), {"last_id": last_id}).mappings().all()
        if not rows:
            break

        updates: list[dict[str, Any]] = []
        for row in rows:
            try:
                request_payload = json.loads(str(row.get("request_json") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                request_payload = {}
            try:
                response_payload = json.loads(str(row.get("response_json") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                response_payload = {}
            try:
                details = json.loads(str(row.get("cache_details_json") or "{}"))
                if not isinstance(details, dict):
                    details = {}
            except (TypeError, ValueError, json.JSONDecodeError):
                details = {}
            call_status = str(row.get("status") or "")
            cache_usage = normalize_llm_cache_usage(
                response_payload,
                successful=call_status in {"success", "stream_success"},
            )
            details["cache_shape"] = build_llm_cache_shape(
                request_payload,
                cache_context={
                    "scope_key": (
                        f"{row.get('source') or 'unknown'}:"
                        f"{row.get('model') or ''}"
                    ),
                },
            )
            updates.append({
                "id": row["id"],
                "cache_miss_tokens": cache_usage.miss_tokens,
                "cache_details_json": json.dumps(
                    details,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            })
        conn.execute(update_statement, updates)
        last_id = int(rows[-1]["id"])


def _llm_provider_cache_performance(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    """补齐 Provider 级 token、首 token 延迟和成本字段。"""

    from foundation.llm.cost_usage import normalize_llm_cost_usage

    _add_missing_columns(conn, "llm_api_request_logs", {
        "input_tokens": "INTEGER NOT NULL DEFAULT 0",
        "output_tokens": "INTEGER NOT NULL DEFAULT 0",
        "first_token_latency_ms": "INTEGER NOT NULL DEFAULT 0",
        "cost_microusd": "INTEGER NOT NULL DEFAULT 0",
        "cost_source": "TEXT NOT NULL DEFAULT 'not_available'",
    })
    if "llm_api_request_logs" not in _table_names(conn):
        return
    required = {"id", "status", "response_json"}
    if not required <= _columns(conn, "llm_api_request_logs"):
        return

    def metric(usage: dict[str, Any], *keys: str) -> int:
        for key in keys:
            value = usage.get(key)
            if type(value) is int and value >= 0:
                return value
        return 0

    statement = text(
        "UPDATE llm_api_request_logs SET input_tokens=:input_tokens, "
        "output_tokens=:output_tokens, "
        "first_token_latency_ms=:first_token_latency_ms, "
        "cost_microusd=:cost_microusd, cost_source=:cost_source "
        "WHERE id=:id"
    )
    last_id = -(2**63)
    while True:
        rows = conn.execute(text(
            "SELECT id, status, response_json FROM llm_api_request_logs "
            "WHERE id > :last_id ORDER BY id LIMIT 500"
        ), {"last_id": last_id}).mappings().all()
        if not rows:
            break
        updates: list[dict[str, Any]] = []
        for row in rows:
            try:
                response = json.loads(str(row.get("response_json") or "{}"))
                if not isinstance(response, dict):
                    response = {}
            except (TypeError, ValueError, json.JSONDecodeError):
                response = {}
            raw_usage = response.get("usage")
            usage = raw_usage if isinstance(raw_usage, dict) else {}
            input_tokens = metric(usage, "prompt_tokens", "input_tokens")
            output_tokens = metric(
                usage, "completion_tokens", "output_tokens"
            )
            raw_stream = response.get("stream_metrics")
            stream = raw_stream if isinstance(raw_stream, dict) else {}
            first_token = metric(
                stream,
                "first_content_ms",
                "first_reasoning_ms",
                "first_chunk_ms",
            )
            successful = str(row.get("status") or "") in {
                "success",
                "stream_success",
            }
            cost = normalize_llm_cost_usage(
                response,
                successful=successful,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            updates.append({
                "id": row["id"],
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "first_token_latency_ms": first_token,
                "cost_microusd": cost.cost_microusd,
                "cost_source": cost.source,
            })
        conn.execute(statement, updates)
        last_id = int(rows[-1]["id"])


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
        "lease_token TEXT NOT NULL DEFAULT '', "
        "lease_expires_at DATETIME, "
        "generation INTEGER NOT NULL DEFAULT 0, "
        "attempt_count INTEGER NOT NULL DEFAULT 0, "
        "error TEXT DEFAULT '', "
        "finished_at DATETIME, "
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


def _session_summary_job_fencing(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    """为会话摘要任务补充不可复用的租约 fencing 身份。"""

    _add_missing_columns(conn, "session_summary_jobs", {
        "lease_token": "TEXT NOT NULL DEFAULT ''",
        "lease_expires_at": "DATETIME",
        "generation": "INTEGER NOT NULL DEFAULT 0",
        "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        "finished_at": "DATETIME",
    })
    if "session_summary_jobs" not in _table_names(conn):
        return

    now = db_now_naive()
    conn.execute(
        text(
            "UPDATE session_summary_jobs SET "
            "status = 'pending', "
            "locked_by = '', "
            "locked_at = NULL, "
            "lease_token = '', "
            "lease_expires_at = NULL, "
            "next_retry_at = COALESCE(next_retry_at, :now), "
            "error = 'migration_requeued_legacy_running', "
            "finished_at = NULL, "
            "updated_at = :now "
            "WHERE status = 'running'"
        ),
        {"now": now},
    )
    _create_indexes(conn, [
        "CREATE INDEX IF NOT EXISTS idx_ssj_claim_fencing "
        "ON session_summary_jobs(status, next_retry_at, id)",
        "CREATE INDEX IF NOT EXISTS idx_ssj_lease_fencing "
        "ON session_summary_jobs(status, lease_expires_at, id)",
    ])


def _block_session_memory_schema(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    """块式会话记忆:conversation_blocks 表 + rolling_session_summaries.block_id 列。

    见 docs/superpowers/specs/2026-07-26-block-session-memory-design.md。新表由
    create_all/本迁移幂等建立;block_id 为 rolling_session_summaries 上的新列,
    P1 只加列、P2 起才读(严格加列先于读列)。
    """

    _add_missing_columns(conn, "rolling_session_summaries", {
        "block_id": "INTEGER",
    })
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS conversation_blocks ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "session_id TEXT NOT NULL, "
        "user_id TEXT DEFAULT '', "
        "chat_type TEXT DEFAULT 'private', "
        "block_seq INTEGER DEFAULT 1, "
        "status TEXT DEFAULT 'open', "
        "open_key TEXT, "
        "first_turn_id INTEGER DEFAULT 0, "
        "last_turn_id INTEGER DEFAULT 0, "
        "started_at DATETIME, "
        "last_turn_at DATETIME, "
        "closed_at DATETIME, "
        "turn_count INTEGER DEFAULT 0, "
        "token_estimate INTEGER DEFAULT 0, "
        "closed_reason TEXT DEFAULT '', "
        "rolling_summary_id INTEGER, "
        "episode_id INTEGER, "
        "meta_json TEXT DEFAULT '{}', "
        "created_at DATETIME, "
        "updated_at DATETIME"
        ")"
    ))
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS conversation_block_episodes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "block_id INTEGER NOT NULL, "
        "block_seq INTEGER DEFAULT 0, "
        "session_id TEXT NOT NULL, "
        "user_id TEXT DEFAULT '', "
        "chat_type TEXT DEFAULT 'private', "
        "status TEXT DEFAULT 'active', "
        "summary_kind TEXT DEFAULT 'deterministic_fallback', "
        "llm_status TEXT DEFAULT '', "
        "summary_text TEXT DEFAULT '', "
        "summary_json TEXT DEFAULT '{}', "
        "covered_first_turn_id INTEGER DEFAULT 0, "
        "covered_last_turn_id INTEGER DEFAULT 0, "
        "source_turn_ids_json TEXT DEFAULT '[]', "
        "source_turn_count INTEGER DEFAULT 0, "
        "seed_summary_id INTEGER, "
        "quality_score REAL DEFAULT 0.0, "
        "issues_json TEXT DEFAULT '[]', "
        "model TEXT DEFAULT '', "
        "prompt_sha256 TEXT DEFAULT '', "
        "stable_hash TEXT DEFAULT '', "
        "source_revision TEXT DEFAULT '', "
        "created_at DATETIME, "
        "sealed_at DATETIME, "
        "refined_at DATETIME, "
        "updated_at DATETIME, "
        "meta_json TEXT DEFAULT '{}'"
        ")"
    ))
    _create_indexes(conn, [
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_block_open_key "
        "ON conversation_blocks(open_key)",
        "CREATE INDEX IF NOT EXISTS idx_conv_block_session_status "
        "ON conversation_blocks(session_id, status, id)",
        "CREATE INDEX IF NOT EXISTS idx_conv_block_session_seq "
        "ON conversation_blocks(session_id, block_seq)",
        "CREATE INDEX IF NOT EXISTS idx_conv_block_status_last_turn "
        "ON conversation_blocks(status, last_turn_at)",
        "CREATE INDEX IF NOT EXISTS idx_rss_block_id "
        "ON rolling_session_summaries(block_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_block_episode_block_id "
        "ON conversation_block_episodes(block_id)",
        "CREATE INDEX IF NOT EXISTS idx_block_episode_session_status "
        "ON conversation_block_episodes(session_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_block_episode_user_session "
        "ON conversation_block_episodes(user_id, session_id)",
    ])


def _group_rolling_chatlog_source(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    """为群聊 Rolling Summary 增加不与 ConversationTurn 混用的来源游标。"""

    _add_missing_columns(conn, "rolling_session_summaries", {
        "source_type": "TEXT NOT NULL DEFAULT 'conversation_turn'",
        "covered_from_source_id": "INTEGER NOT NULL DEFAULT 0",
        "covered_until_source_id": "INTEGER NOT NULL DEFAULT 0",
        "source_ids_json": "TEXT NOT NULL DEFAULT '[]'",
        "raw_window_start_source_id": "INTEGER NOT NULL DEFAULT 0",
    })
    _add_missing_columns(conn, "session_summary_jobs", {
        "source_type": "TEXT NOT NULL DEFAULT 'conversation_turn'",
        "covered_from_source_id": "INTEGER NOT NULL DEFAULT 0",
        "covered_until_source_id": "INTEGER NOT NULL DEFAULT 0",
        "source_ids_json": "TEXT NOT NULL DEFAULT '[]'",
    })
    if "rolling_session_summaries" in _table_names(conn):
        conn.execute(text(
            "UPDATE rolling_session_summaries SET "
            "source_type = 'conversation_turn', "
            "covered_from_source_id = covered_from_turn_id, "
            "covered_until_source_id = covered_until_turn_id, "
            "source_ids_json = source_turn_ids_json, "
            "raw_window_start_source_id = raw_window_start_turn_id "
            "WHERE source_type IS NULL OR source_type = '' "
            "OR covered_until_source_id = 0"
        ))
        # 旧群聊摘要只覆盖 bot 参与的 ConversationTurn，不能再作为完整群现场
        # 注入。只归档派生摘要，ChatLog 原始档案不做任何删除。
        conn.execute(
            text(
                "UPDATE rolling_session_summaries SET "
                "status = 'archived', updated_at = :now "
                "WHERE chat_type = 'group' AND status = 'active' "
                "AND source_type != 'chat_log'"
            ),
            {"now": db_now_naive()},
        )
    if "session_summary_jobs" in _table_names(conn):
        conn.execute(text(
            "UPDATE session_summary_jobs SET "
            "source_type = 'conversation_turn', "
            "covered_from_source_id = covered_from_turn_id, "
            "covered_until_source_id = covered_until_turn_id, "
            "source_ids_json = source_turn_ids_json "
            "WHERE source_type IS NULL OR source_type = '' "
            "OR covered_until_source_id = 0"
        ))
        conn.execute(
            text(
                "UPDATE session_summary_jobs SET "
                "status = 'obsolete', "
                "error = '', next_retry_at = NULL, "
                "locked_by = '', locked_at = NULL, "
                "lease_token = '', lease_expires_at = NULL, "
                "finished_at = :now, updated_at = :now "
                "WHERE chat_type = 'group' "
                "AND status IN ('pending', 'running', 'failed') "
                "AND source_type != 'chat_log'"
            ),
            {"now": db_now_naive()},
        )
    _create_indexes(conn, [
        "CREATE INDEX IF NOT EXISTS idx_rss_source_coverage "
        "ON rolling_session_summaries(session_id, source_type, covered_until_source_id)",
        "CREATE INDEX IF NOT EXISTS idx_ssj_source_coverage "
        "ON session_summary_jobs(session_id, source_type, covered_from_source_id, covered_until_source_id)",
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


def _semantic_index_reconcile_v2(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    """为语义索引任务增加可 fencing 的租约和逻辑源 revision。"""

    _add_missing_columns(conn, "semantic_index_items", {
        "source_revision": "TEXT NOT NULL DEFAULT ''",
    })
    _add_missing_columns(conn, "semantic_index_jobs", {
        "source_revision": "TEXT NOT NULL DEFAULT ''",
        "lease_token": "TEXT NOT NULL DEFAULT ''",
        "lease_expires_at": "DATETIME",
        "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        "manual_retry_count": "INTEGER NOT NULL DEFAULT 0",
        "meta_json": "TEXT NOT NULL DEFAULT '{}'",
    })

    tables = _table_names(conn)
    if "semantic_index_jobs" in tables:
        now = db_now_naive()
        conn.execute(text(
            "UPDATE semantic_index_jobs SET "
            "status = 'pending', locked_by = '', locked_at = NULL, "
            "lease_token = '', lease_expires_at = NULL, "
            "next_retry_at = COALESCE(next_retry_at, :now), "
            "error = 'migration_requeued_legacy_running', "
            "finished_at = NULL, updated_at = :now "
            "WHERE status = 'running'"
        ), {"now": now})
        conn.execute(text(
            "UPDATE semantic_index_jobs SET "
            "status = 'pending', locked_by = '', locked_at = NULL, "
            "lease_token = '', lease_expires_at = NULL, "
            "finished_at = NULL, updated_at = :now "
            "WHERE status = 'failed' AND next_retry_at IS NOT NULL "
            "AND finished_at IS NULL AND retry_count < max_retry"
        ), {"now": now})

    _create_indexes(conn, [
        "CREATE INDEX IF NOT EXISTS idx_semantic_job_claim_v2 "
        "ON semantic_index_jobs(status, next_retry_at, id)",
        "CREATE INDEX IF NOT EXISTS idx_semantic_job_lease_v2 "
        "ON semantic_index_jobs(status, lease_expires_at, id)",
        "CREATE INDEX IF NOT EXISTS idx_semantic_job_source_revision_v2 "
        "ON semantic_index_jobs(source_type, source_id, index_version, source_revision, status)",
        "CREATE INDEX IF NOT EXISTS idx_semantic_item_source_revision_v2 "
        "ON semantic_index_items(source_type, source_id, source_revision, status)",
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


def _super_user_config_cleanup(conn: Any, engine: Any, db_path: str | None) -> None:
    tables = _table_names(conn)
    if "system_settings" in tables:
        conn.execute(text(
            "DELETE FROM system_settings WHERE key = 'bot.super_user_ids'"
        ))
    if "admin_audit_logs" in tables:
        conn.execute(
            text(
                "UPDATE admin_audit_logs "
                "SET detail_json = :detail_json "
                "WHERE target_id = 'bot.super_user_ids'"
            ),
            {"detail_json": '{"changed":true,"redacted":true}'},
        )


def _summary_model_safe_defaults(conn: Any, engine: Any, db_path: str | None) -> None:
    """一次性收敛摘要路由的高风险历史参数。"""

    if "system_settings" not in _table_names(conn):
        return
    columns = _columns(conn, "system_settings")
    values = {
        "model.route.session_summary.temperature": "0.1",
        "model.route.session_summary.max_tokens": "1200",
        "model.route.session_summary.enable_thinking": "false",
        "model.route.memory_digest.temperature": "0.1",
        "model.route.memory_digest.max_tokens": "1800",
        "model.route.memory_digest.enable_thinking": "false",
    }
    now = db_now_naive()
    for key, value in values.items():
        assignments = ["value = :value"]
        params: dict[str, Any] = {"key": key, "value": value}
        if "description" in columns:
            assignments.append("description = :description")
            params["description"] = "摘要模型安全参数（2026-07-16 审计修复）"
        if "updated_at" in columns:
            assignments.append("updated_at = :updated_at")
            params["updated_at"] = now
        updated = conn.execute(
            text(
                f'UPDATE system_settings SET {", ".join(assignments)} '
                'WHERE "key" = :key'
            ),
            params,
        )
        if updated.rowcount:
            continue
        insert_columns = ["key", "value"]
        insert_params = [":key", ":value"]
        if "description" in columns:
            insert_columns.append("description")
            insert_params.append(":description")
        if "updated_at" in columns:
            insert_columns.append("updated_at")
            insert_params.append(":updated_at")
        conn.execute(
            text(
                f'INSERT INTO system_settings ({", ".join(insert_columns)}) '
                f'VALUES ({", ".join(insert_params)})'
            ),
            params,
        )


def _summary_output_contract_defaults(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    """把旧摘要输出上限迁移到能够容纳当前 JSON 合同的默认值。"""

    if "system_settings" not in _table_names(conn):
        return
    columns = _columns(conn, "system_settings")
    settings = (
        (
            "model.route.session_summary.max_tokens",
            "4096",
            {"1200", "65535"},
        ),
        (
            "model.route.memory_digest.max_tokens",
            "8192",
            {"1800", "65535"},
        ),
    )
    now = db_now_naive()
    for key, value, legacy_values in settings:
        row = conn.execute(
            text('SELECT value FROM system_settings WHERE "key" = :key'),
            {"key": key},
        ).first()
        if row is not None and str(row[0] or "").strip() not in legacy_values:
            continue

        params: dict[str, Any] = {"key": key, "value": value}
        if "description" in columns:
            params["description"] = "摘要 JSON 输出合同容量（2026-07-18 修复）"
        if "updated_at" in columns:
            params["updated_at"] = now
        if row is not None:
            assignments = ["value = :value"]
            if "description" in columns:
                assignments.append("description = :description")
            if "updated_at" in columns:
                assignments.append("updated_at = :updated_at")
            conn.execute(
                text(
                    f'UPDATE system_settings SET {", ".join(assignments)} '
                    'WHERE "key" = :key'
                ),
                params,
            )
            continue

        insert_columns = ["key", "value"]
        insert_values = [":key", ":value"]
        if "description" in columns:
            insert_columns.append("description")
            insert_values.append(":description")
        if "updated_at" in columns:
            insert_columns.append("updated_at")
            insert_values.append(":updated_at")
        conn.execute(
            text(
                f'INSERT INTO system_settings ({", ".join(insert_columns)}) '
                f'VALUES ({", ".join(insert_values)})'
            ),
            params,
        )


def _memory_digest_jobs(conn: Any, engine: Any, db_path: str | None) -> None:
    """创建 MemoryDigest 生成、租约和重试账本。"""

    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS memory_digest_jobs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "session_id TEXT NOT NULL, "
        "digest_date TEXT NOT NULL, "
        "user_id TEXT NOT NULL DEFAULT '', "
        "source_start_log_id INTEGER NOT NULL DEFAULT 0, "
        "source_end_log_id INTEGER NOT NULL DEFAULT 0, "
        "source_log_count INTEGER NOT NULL DEFAULT 0, "
        "source_revision TEXT NOT NULL DEFAULT '', "
        "status TEXT NOT NULL DEFAULT 'pending', "
        "locked_by TEXT NOT NULL DEFAULT '', "
        "lease_token TEXT NOT NULL DEFAULT '', "
        "lease_expires_at DATETIME, "
        "attempt_count INTEGER NOT NULL DEFAULT 0, "
        "retry_count INTEGER NOT NULL DEFAULT 0, "
        "max_retry INTEGER NOT NULL DEFAULT 3, "
        "next_retry_at DATETIME, "
        "result_digest_count INTEGER NOT NULL DEFAULT 0, "
        "result_source_id TEXT NOT NULL DEFAULT '', "
        "result_root_digest_id INTEGER, "
        "result_semantic_job_id INTEGER, "
        "error_type TEXT NOT NULL DEFAULT '', "
        "error_summary TEXT NOT NULL DEFAULT '', "
        "meta_json TEXT NOT NULL DEFAULT '{}', "
        "finished_at DATETIME, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "CONSTRAINT uq_memory_digest_job_source "
        "UNIQUE(session_id, digest_date)"
        ")"
    ))
    _create_indexes(conn, [
        "CREATE INDEX IF NOT EXISTS idx_memory_digest_job_claim "
        "ON memory_digest_jobs(status, lease_expires_at, id)",
        "CREATE INDEX IF NOT EXISTS idx_memory_digest_job_retry "
        "ON memory_digest_jobs(status, next_retry_at, id)",
    ])
    _add_missing_columns(conn, "memory_digests", {
        "generation_job_id": "INTEGER",
    })
    if "memory_digests" in _table_names(conn):
        _create_indexes(conn, [
            "CREATE INDEX IF NOT EXISTS ix_memory_digests_generation_job_id "
            "ON memory_digests(generation_job_id)",
        ])


def _memory_cleanup_governance(conn: Any, engine: Any, db_path: str | None) -> None:
    """为历史记忆归档提供显式状态和幂等执行账本。"""

    _add_missing_columns(conn, "personas", {
        "status": "TEXT NOT NULL DEFAULT 'active'",
        "archive_meta_json": "TEXT NOT NULL DEFAULT '{}'",
    })
    _add_missing_columns(conn, "persona_behaviors", {
        "status": "TEXT NOT NULL DEFAULT 'active'",
        "archive_meta_json": "TEXT NOT NULL DEFAULT '{}'",
    })
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS memory_cleanup_runs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "cleanup_version VARCHAR(64) NOT NULL DEFAULT '', "
        "bundle_sha256 VARCHAR(64) NOT NULL, "
        "status VARCHAR(24) NOT NULL DEFAULT 'applying', "
        "actor VARCHAR(255) NOT NULL DEFAULT 'cli', "
        "audit_log_id INTEGER, "
        "target_counts_json TEXT NOT NULL DEFAULT '{}', "
        "result_json TEXT NOT NULL DEFAULT '{}', "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "applied_at DATETIME, "
        "CONSTRAINT uq_memory_cleanup_run_bundle_sha256 UNIQUE(bundle_sha256)"
        ")"
    ))
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_memory_cleanup_run_status "
        "ON memory_cleanup_runs(status, id)",
    ]
    tables = _table_names(conn)
    if "personas" in tables:
        indexes.append(
            "CREATE INDEX IF NOT EXISTS ix_personas_status ON personas(status)"
        )
    if "persona_behaviors" in tables:
        indexes.append(
            "CREATE INDEX IF NOT EXISTS ix_persona_behaviors_status "
            "ON persona_behaviors(status)"
        )
    _create_indexes(conn, indexes)


def _memory_governance_repairs(conn: Any, engine: Any, db_path: str | None) -> None:
    """修复摘要审计发现的遗留运行态、孤儿索引和单发言者群记忆。"""

    tables = _table_names(conn)
    now = db_now_naive()

    if "memory_digest_jobs" in tables:
        columns = _columns(conn, "memory_digest_jobs")
        required = {
            "status", "locked_by", "lease_token", "lease_expires_at",
            "retry_count", "max_retry", "next_retry_at", "error_type",
            "error_summary", "finished_at", "updated_at",
        }
        if required <= columns:
            conn.execute(
                text(
                    "UPDATE memory_digest_jobs SET "
                    "status = 'failed', locked_by = '', lease_token = '', "
                    "lease_expires_at = NULL, "
                    "next_retry_at = CASE "
                    "WHEN COALESCE(retry_count, 0) < COALESCE(max_retry, 0) "
                    "THEN :now ELSE NULL END, "
                    "error_type = 'lease_expired_recovered', "
                    "error_summary = 'memory_digest_failed:lease_expired_recovered', "
                    "finished_at = :now, updated_at = :now "
                    "WHERE status = 'running' "
                    "AND (lease_expires_at IS NULL OR lease_expires_at <= :now)"
                ),
                {"now": now},
            )

    fallback_item_ids: list[int] = []
    if "semantic_index_items" in tables:
        summary_kinds: dict[int, str] = {}
        if (
            "rolling_session_summaries" in tables
            and {"id", "summary_kind"} <= _columns(conn, "rolling_session_summaries")
        ):
            summary_kinds = {
                int(row[0]): str(row[1] or "")
                for row in conn.execute(text(
                    "SELECT id, summary_kind FROM rolling_session_summaries"
                )).all()
            }
        item_columns = _columns(conn, "semantic_index_items")
        if {"id", "source_type", "document_id", "status", "meta_json"} <= item_columns:
            rows = conn.execute(text(
                "SELECT id, document_id, meta_json FROM semantic_index_items "
                "WHERE source_type = 'session_summary' AND status = 'active'"
            )).all()
            for item_id, document_id, raw_meta in rows:
                try:
                    meta = json.loads(str(raw_meta or "{}"))
                except (TypeError, json.JSONDecodeError):
                    meta = {}
                try:
                    summary_id = int(str(document_id or "0"))
                except ValueError:
                    summary_id = 0
                if (
                    str(meta.get("summary_kind") or "") == "deterministic_fallback"
                    or summary_kinds.get(summary_id) == "deterministic_fallback"
                ):
                    fallback_item_ids.append(int(item_id))
            for item_id in fallback_item_ids:
                if "semantic_index_fts" in tables:
                    conn.execute(
                        text("DELETE FROM semantic_index_fts WHERE rowid = :item_id"),
                        {"item_id": item_id},
                    )
                assignments = ["status = 'deleted'"]
                if "deleted_at" in item_columns:
                    assignments.append("deleted_at = :now")
                if "updated_at" in item_columns:
                    assignments.append("updated_at = :now")
                conn.execute(
                    text(
                        f"UPDATE semantic_index_items SET {', '.join(assignments)} "
                        "WHERE id = :item_id AND status = 'active'"
                    ),
                    {"item_id": item_id, "now": now},
                )

    group_required = {
        "id", "memory_type", "status", "source", "evidence_log_ids_json",
        "inject_policy", "meta_json", "updated_at",
    }
    chat_log_columns = _columns(conn, "chat_logs") if "chat_logs" in tables else set()
    if (
        "group_memories" not in tables
        or "chat_logs" not in tables
        or not group_required <= _columns(conn, "group_memories")
        or not {"id", "user_id"} <= chat_log_columns
    ):
        return
    memories = conn.execute(text(
        "SELECT id, evidence_log_ids_json, meta_json FROM group_memories "
        "WHERE memory_type = 'topic' AND status = 'active' "
        "AND source IN ('group_analysis', 'manual_group_memory_extract')"
    )).all()
    for memory_id, raw_evidence, raw_meta in memories:
        try:
            evidence = json.loads(str(raw_evidence or "[]"))
        except (TypeError, json.JSONDecodeError):
            evidence = []
        evidence_ids = sorted({
            int(item)
            for item in evidence
            if str(item).isdigit() and int(item) > 0
        })
        speakers: list[str] = []
        if evidence_ids:
            placeholders = ",".join(f":evidence_{index}" for index in range(len(evidence_ids)))
            params = {
                f"evidence_{index}": evidence_id
                for index, evidence_id in enumerate(evidence_ids)
            }
            selected_columns = ["user_id"] + [
                column
                for column in ("session_id", "sender_name", "role", "meta_json")
                if column in chat_log_columns
            ]
            speaker_set: set[str] = set()
            chat_rows = conn.execute(
                text(
                    f"SELECT {', '.join(selected_columns)} FROM chat_logs "
                    f"WHERE id IN ({placeholders})"
                ),
                params,
            ).mappings().all()
            for chat_row in chat_rows:
                role = str(chat_row.get("role") or "ambient").strip()
                if role not in {"ambient", "user"}:
                    continue
                try:
                    chat_meta = json.loads(str(chat_row.get("meta_json") or "{}"))
                except (TypeError, json.JSONDecodeError):
                    chat_meta = {}
                if not isinstance(chat_meta, dict):
                    chat_meta = {}
                sender_meta = (
                    chat_meta.get("sender")
                    if isinstance(chat_meta.get("sender"), dict)
                    else {}
                )
                moderation = (
                    chat_meta.get("moderation")
                    if isinstance(chat_meta.get("moderation"), dict)
                    else {}
                )
                if (
                    bool(sender_meta.get("is_bot"))
                    or bool(chat_meta.get("is_bot"))
                    or bool(chat_meta.get("sender_is_bot"))
                    or bool(chat_meta.get("external_bot"))
                    or bool(moderation.get("no_learn"))
                ):
                    continue
                user_id = str(chat_row.get("user_id") or "").strip()
                session_id = str(chat_row.get("session_id") or "").strip()
                speaker = str(
                    sender_meta.get("id") or chat_meta.get("sender_id") or ""
                ).strip()
                if not speaker and user_id and user_id != session_id:
                    speaker = user_id
                if not speaker:
                    speaker = str(chat_row.get("sender_name") or user_id).strip()
                if speaker:
                    speaker_set.add(speaker)
            speakers = sorted(speaker_set)
        try:
            meta = json.loads(str(raw_meta or "{}"))
        except (TypeError, json.JSONDecodeError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta.update({
            "generator": str(meta.get("generator") or "legacy_untracked"),
            "consensus_gate": "multi_speaker",
            "evidence_speakers": speakers,
            "evidence_speaker_count": len(speakers),
        })
        if len(speakers) < 2:
            issues = meta.get("governance_issues")
            issues = list(issues) if isinstance(issues, list) else []
            if "single_speaker_evidence" not in issues:
                issues.append("single_speaker_evidence")
            meta["governance_issues"] = issues
        conn.execute(
            text(
                "UPDATE group_memories SET status = :status, inject_policy = 'auto', "
                "meta_json = :meta_json, updated_at = :now WHERE id = :memory_id"
            ),
            {
                "status": "active" if len(speakers) >= 2 else "review",
                "meta_json": json.dumps(meta, ensure_ascii=False, sort_keys=True),
                "now": now,
                "memory_id": int(memory_id),
            },
        )


def _sandbox_storage_tables(conn: Any, engine: Any, db_path: str | None) -> None:
    """创建 owner-only Workspace、Asset 授权和 Sandbox 运行账本。"""

    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS workspaces ("
        "id VARCHAR(36) PRIMARY KEY, "
        "platform VARCHAR(32) NOT NULL, "
        "owner_type VARCHAR(16) NOT NULL, "
        "owner_id VARCHAR(255) NOT NULL, "
        "name VARCHAR(64) NOT NULL DEFAULT 'default', "
        "status VARCHAR(16) NOT NULL DEFAULT 'active', "
        "quota_bytes BIGINT NOT NULL, "
        "used_bytes BIGINT NOT NULL DEFAULT 0, "
        "last_accessed_at DATETIME, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "CONSTRAINT ck_workspace_owner_type "
        "CHECK (owner_type IN ('user', 'group', 'project')), "
        "CONSTRAINT ck_workspace_status "
        "CHECK (status IN ('active', 'disabled', 'archived')), "
        "CONSTRAINT ck_workspace_quota_positive CHECK (quota_bytes > 0), "
        "CONSTRAINT ck_workspace_used_nonnegative CHECK (used_bytes >= 0), "
        "CONSTRAINT uq_workspace_owner_name "
        "UNIQUE (platform, owner_type, owner_id, name)"
        ")"
    ))
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS assets ("
        "sha256 VARCHAR(64) PRIMARY KEY, "
        "size_bytes BIGINT NOT NULL, "
        "media_type VARCHAR(255) NOT NULL DEFAULT 'application/octet-stream', "
        "storage_key VARCHAR(255) NOT NULL UNIQUE, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "CONSTRAINT ck_asset_size_nonnegative CHECK (size_bytes >= 0)"
        ")"
    ))
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS workspace_assets ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "workspace_id VARCHAR(36) NOT NULL, "
        "asset_sha256 VARCHAR(64) NOT NULL, "
        "logical_name VARCHAR(512) NOT NULL, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "CONSTRAINT fk_workspace_asset_workspace "
        "FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE, "
        "CONSTRAINT fk_workspace_asset_asset "
        "FOREIGN KEY (asset_sha256) REFERENCES assets(sha256) ON DELETE RESTRICT, "
        "CONSTRAINT uq_workspace_asset_logical_name "
        "UNIQUE (workspace_id, logical_name), "
        "CONSTRAINT uq_workspace_asset_link "
        "UNIQUE (workspace_id, asset_sha256, logical_name)"
        ")"
    ))
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS sandbox_runs ("
        "run_id VARCHAR(64) PRIMARY KEY, "
        "request_id VARCHAR(64) NOT NULL UNIQUE, "
        "workspace_id VARCHAR(36) NOT NULL, "
        "lease_id VARCHAR(64), "
        "profile_id VARCHAR(32) NOT NULL DEFAULT 'restricted', "
        "execution_mode VARCHAR(16) NOT NULL DEFAULT 'oneshot', "
        "process_state VARCHAR(16) NOT NULL DEFAULT 'not_applicable', "
        "trace_id VARCHAR(64) NOT NULL DEFAULT '', "
        "agent_run_id VARCHAR(64) NOT NULL DEFAULT '', "
        "tool_call_id VARCHAR(64) NOT NULL DEFAULT '', "
        "image_digest VARCHAR(255) NOT NULL, "
        "status VARCHAR(16) NOT NULL DEFAULT 'pending', "
        "exit_code INTEGER, "
        "termination_reason VARCHAR(64) NOT NULL DEFAULT '', "
        "cpu_time_ms INTEGER NOT NULL DEFAULT 0, "
        "peak_memory_bytes BIGINT NOT NULL DEFAULT 0, "
        "stdout_bytes BIGINT NOT NULL DEFAULT 0, "
        "stderr_bytes BIGINT NOT NULL DEFAULT 0, "
        "stdout_truncated BOOLEAN NOT NULL DEFAULT 0, "
        "stderr_truncated BOOLEAN NOT NULL DEFAULT 0, "
        "started_at DATETIME, "
        "finished_at DATETIME, "
        "last_seen_at DATETIME, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "CONSTRAINT fk_sandbox_run_workspace "
        "FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT, "
        "CONSTRAINT ck_sandbox_run_status "
        "CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')), "
        "CONSTRAINT ck_sandbox_run_profile "
        "CHECK (profile_id IN ('restricted', 'developer', 'trusted_developer')), "
        "CONSTRAINT ck_sandbox_run_execution_mode "
        "CHECK (execution_mode IN ('oneshot', 'lease')), "
        "CONSTRAINT ck_sandbox_run_process_state "
        "CHECK (process_state IN "
        "('not_applicable', 'starting', 'running', 'exited', 'lost')), "
        "CONSTRAINT ck_sandbox_run_cpu_nonnegative CHECK (cpu_time_ms >= 0), "
        "CONSTRAINT ck_sandbox_run_memory_nonnegative CHECK (peak_memory_bytes >= 0), "
        "CONSTRAINT ck_sandbox_run_output_nonnegative "
        "CHECK (stdout_bytes >= 0 AND stderr_bytes >= 0)"
        ")"
    ))
    _create_indexes(conn, [
        "CREATE INDEX IF NOT EXISTS ix_workspace_owner "
        "ON workspaces(platform, owner_type, owner_id)",
        "CREATE INDEX IF NOT EXISTS ix_workspace_assets_workspace_id "
        "ON workspace_assets(workspace_id)",
        "CREATE INDEX IF NOT EXISTS ix_workspace_asset_sha256 "
        "ON workspace_assets(asset_sha256)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_sandbox_runs_request_id "
        "ON sandbox_runs(request_id)",
        "CREATE INDEX IF NOT EXISTS ix_sandbox_runs_trace_id "
        "ON sandbox_runs(trace_id)",
        "CREATE INDEX IF NOT EXISTS ix_sandbox_runs_agent_run_id "
        "ON sandbox_runs(agent_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_sandbox_runs_tool_call_id "
        "ON sandbox_runs(tool_call_id)",
        "CREATE INDEX IF NOT EXISTS ix_sandbox_runs_lease_id "
        "ON sandbox_runs(lease_id)",
        "CREATE INDEX IF NOT EXISTS ix_sandbox_run_workspace_created "
        "ON sandbox_runs(workspace_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_sandbox_run_status_created "
        "ON sandbox_runs(status, created_at)",
    ])


def _sandbox_control_plane_tables(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    """创建按会话授权、硬配额绑定与可恢复管理操作表。"""

    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS sandbox_access_grants ("
        "id VARCHAR(36) PRIMARY KEY, "
        "chat_stream_id VARCHAR(512) NOT NULL UNIQUE, "
        "platform VARCHAR(32) NOT NULL, "
        "chat_type VARCHAR(16) NOT NULL, "
        "external_session_id VARCHAR(255) NOT NULL, "
        "workspace_id VARCHAR(36), "
        "capability_level VARCHAR(16) NOT NULL DEFAULT 'off', "
        "execution_profile VARCHAR(32) NOT NULL DEFAULT 'restricted', "
        "status VARCHAR(16) NOT NULL DEFAULT 'disabled', "
        "version INTEGER NOT NULL DEFAULT 1, "
        "reason TEXT NOT NULL DEFAULT '', "
        "created_by VARCHAR(128) NOT NULL DEFAULT '', "
        "updated_by VARCHAR(128) NOT NULL DEFAULT '', "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "CONSTRAINT fk_sandbox_access_grant_workspace "
        "FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT, "
        "CONSTRAINT ck_sandbox_access_grant_chat_type "
        "CHECK (chat_type IN ('private', 'group')), "
        "CONSTRAINT ck_sandbox_access_grant_capability "
        "CHECK (capability_level IN ('off', 'workspace', 'assets', 'exec')), "
        "CONSTRAINT ck_sandbox_access_grant_execution_profile "
        "CHECK (execution_profile IN "
        "('restricted', 'developer', 'trusted_developer')), "
        "CONSTRAINT ck_sandbox_access_grant_status "
        "CHECK (status IN ('provisioning', 'active', 'disabled', 'error')), "
        "CONSTRAINT ck_sandbox_access_grant_version CHECK (version >= 1), "
        "CONSTRAINT uq_sandbox_access_grant_external_session "
        "UNIQUE (platform, chat_type, external_session_id)"
        ")"
    ))
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS workspace_quota_bindings ("
        "workspace_id VARCHAR(36) PRIMARY KEY, "
        "project_id INTEGER NOT NULL UNIQUE, "
        "desired_quota_bytes BIGINT NOT NULL, "
        "applied_quota_bytes BIGINT NOT NULL DEFAULT 0, "
        "status VARCHAR(16) NOT NULL DEFAULT 'pending', "
        "generation INTEGER NOT NULL DEFAULT 1, "
        "last_error_code VARCHAR(64) NOT NULL DEFAULT '', "
        "last_error_summary VARCHAR(255) NOT NULL DEFAULT '', "
        "last_applied_at DATETIME, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "CONSTRAINT fk_workspace_quota_binding_workspace "
        "FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE, "
        "CONSTRAINT ck_workspace_quota_project_id CHECK (project_id >= 10000), "
        "CONSTRAINT ck_workspace_quota_desired_positive "
        "CHECK (desired_quota_bytes > 0), "
        "CONSTRAINT ck_workspace_quota_applied_nonnegative "
        "CHECK (applied_quota_bytes >= 0), "
        "CONSTRAINT ck_workspace_quota_status "
        "CHECK (status IN ('pending', 'applying', 'applied', 'error')), "
        "CONSTRAINT ck_workspace_quota_generation CHECK (generation >= 1)"
        ")"
    ))
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS sandbox_admin_operations ("
        "operation_id VARCHAR(64) PRIMARY KEY, "
        "request_id VARCHAR(64) NOT NULL UNIQUE, "
        "operation_type VARCHAR(32) NOT NULL, "
        "chat_stream_id VARCHAR(512) NOT NULL DEFAULT '', "
        "workspace_id VARCHAR(36), "
        "desired_capability VARCHAR(16) NOT NULL DEFAULT '', "
        "previous_capability VARCHAR(16) NOT NULL DEFAULT '', "
        "desired_quota_bytes BIGINT NOT NULL DEFAULT 0, "
        "expected_grant_version INTEGER, "
        "expected_quota_generation INTEGER, "
        "status VARCHAR(16) NOT NULL DEFAULT 'pending', "
        "step VARCHAR(64) NOT NULL DEFAULT 'queued', "
        "attempt_count INTEGER NOT NULL DEFAULT 0, "
        "max_attempts INTEGER NOT NULL DEFAULT 5, "
        "locked_by VARCHAR(128) NOT NULL DEFAULT '', "
        "lease_token VARCHAR(64) NOT NULL DEFAULT '', "
        "lease_expires_at DATETIME, "
        "next_attempt_at DATETIME, "
        "error_code VARCHAR(64) NOT NULL DEFAULT '', "
        "error_summary VARCHAR(255) NOT NULL DEFAULT '', "
        "reason VARCHAR(255) NOT NULL DEFAULT '', "
        "created_by VARCHAR(128) NOT NULL DEFAULT '', "
        "started_at DATETIME, "
        "finished_at DATETIME, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "CONSTRAINT fk_sandbox_admin_operation_workspace "
        "FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT, "
        "CONSTRAINT ck_sandbox_admin_operation_type "
        "CHECK (operation_type IN "
        "('set_access', 'set_quota', 'bind_workspace', 'import_quota', "
        "'lease_stop', 'lease_destroy', 'lease_recreate', 'kill_switch')), "
        "CONSTRAINT ck_sandbox_admin_operation_status "
        "CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')), "
        "CONSTRAINT ck_sandbox_admin_desired_capability "
        "CHECK (desired_capability IN ('', 'off', 'workspace', 'assets', 'exec')), "
        "CONSTRAINT ck_sandbox_admin_previous_capability "
        "CHECK (previous_capability IN ('', 'off', 'workspace', 'assets', 'exec')), "
        "CONSTRAINT ck_sandbox_admin_desired_quota "
        "CHECK (desired_quota_bytes >= 0), "
        "CONSTRAINT ck_sandbox_admin_attempt_count CHECK (attempt_count >= 0), "
        "CONSTRAINT ck_sandbox_admin_max_attempts CHECK (max_attempts >= 1)"
        ")"
    ))
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS sandbox_project_sequences ("
        "name VARCHAR(32) PRIMARY KEY, "
        "next_value INTEGER NOT NULL DEFAULT 10000, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "CONSTRAINT ck_sandbox_project_sequence_value CHECK (next_value >= 10000)"
        ")"
    ))
    _create_indexes(conn, [
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_sandbox_access_grants_chat_stream_id "
        "ON sandbox_access_grants(chat_stream_id)",
        "CREATE INDEX IF NOT EXISTS ix_sandbox_access_grants_workspace_id "
        "ON sandbox_access_grants(workspace_id)",
        "CREATE INDEX IF NOT EXISTS ix_sandbox_access_grant_platform_type "
        "ON sandbox_access_grants(platform, chat_type)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_workspace_quota_bindings_project_id "
        "ON workspace_quota_bindings(project_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_sandbox_admin_operations_request_id "
        "ON sandbox_admin_operations(request_id)",
        "CREATE INDEX IF NOT EXISTS ix_sandbox_admin_operations_workspace_id "
        "ON sandbox_admin_operations(workspace_id)",
        "CREATE INDEX IF NOT EXISTS ix_sandbox_admin_operation_status_retry "
        "ON sandbox_admin_operations(status, next_attempt_at)",
        "CREATE INDEX IF NOT EXISTS ix_sandbox_admin_operation_session_created "
        "ON sandbox_admin_operations(chat_stream_id, created_at)",
    ])


def _sandbox_project_sequence_seed(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    """以独立数据迁移初始化 project ID 序列。"""

    conn.execute(
        text(
            "INSERT INTO sandbox_project_sequences(name, next_value) "
            "SELECT :name, :next_value "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM sandbox_project_sequences WHERE name = :name"
            ")"
        ),
        {"name": "workspace", "next_value": 10000},
    )


_SANDBOX_ADMIN_OPERATION_COLUMNS = (
    "operation_id",
    "request_id",
    "operation_type",
    "chat_stream_id",
    "workspace_id",
    "desired_capability",
    "previous_capability",
    "desired_quota_bytes",
    "expected_grant_version",
    "expected_quota_generation",
    "status",
    "step",
    "attempt_count",
    "max_attempts",
    "locked_by",
    "lease_token",
    "lease_expires_at",
    "next_attempt_at",
    "error_code",
    "error_summary",
    "reason",
    "created_by",
    "started_at",
    "finished_at",
    "created_at",
    "updated_at",
)
_SANDBOX_ADMIN_OPERATION_TYPES = (
    "set_access",
    "set_quota",
    "bind_workspace",
    "import_quota",
    "lease_stop",
    "lease_destroy",
    "lease_recreate",
    "kill_switch",
)


def _sandbox_admin_operations_support_lease_types(conn: Any) -> bool:
    if "sandbox_admin_operations" not in _table_names(conn):
        return False
    if str(getattr(conn.dialect, "name", "")) == "sqlite":
        ddl = conn.execute(text(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'sandbox_admin_operations'"
        )).scalar_one_or_none()
        definitions = [ddl] if isinstance(ddl, str) else []
    else:
        definitions = [
            str(constraint.get("sqltext") or "")
            for constraint in inspect(conn).get_check_constraints(
                "sandbox_admin_operations"
            )
        ]
    normalized = " ".join(definitions).casefold()
    return all(
        f"'{operation_type}'" in normalized
        for operation_type in _SANDBOX_ADMIN_OPERATION_TYPES
    )


def _rebuild_sandbox_admin_operations(conn: Any) -> None:
    """重建旧管理操作表，使升级库与新库拥有同一 CHECK。"""

    if _sandbox_admin_operations_support_lease_types(conn):
        return
    required_columns = set(_SANDBOX_ADMIN_OPERATION_COLUMNS)
    actual_columns = _columns(conn, "sandbox_admin_operations")
    missing_columns = sorted(required_columns - actual_columns)
    if missing_columns:
        raise SchemaMigrationValidationError(
            "sandbox_admin_operations 缺少重建所需列："
            f"{missing_columns}"
        )
    temporary_table = "sandbox_admin_operations__lease_migration"
    if temporary_table in _table_names(conn):
        raise SchemaMigrationValidationError(
            f"迁移临时表已存在：{temporary_table}"
        )

    selected_columns = ", ".join(_SANDBOX_ADMIN_OPERATION_COLUMNS)
    source_rows = [
        tuple(row)
        for row in conn.execute(text(
            f"SELECT {selected_columns} FROM sandbox_admin_operations "
            "ORDER BY operation_id"
        )).fetchall()
    ]
    conn.execute(text(
        f"CREATE TABLE {temporary_table} ("
        "operation_id VARCHAR(64) PRIMARY KEY, "
        "request_id VARCHAR(64) NOT NULL UNIQUE, "
        "operation_type VARCHAR(32) NOT NULL, "
        "chat_stream_id VARCHAR(512) NOT NULL DEFAULT '', "
        "workspace_id VARCHAR(36), "
        "desired_capability VARCHAR(16) NOT NULL DEFAULT '', "
        "previous_capability VARCHAR(16) NOT NULL DEFAULT '', "
        "desired_quota_bytes BIGINT NOT NULL DEFAULT 0, "
        "expected_grant_version INTEGER, "
        "expected_quota_generation INTEGER, "
        "status VARCHAR(16) NOT NULL DEFAULT 'pending', "
        "step VARCHAR(64) NOT NULL DEFAULT 'queued', "
        "attempt_count INTEGER NOT NULL DEFAULT 0, "
        "max_attempts INTEGER NOT NULL DEFAULT 5, "
        "locked_by VARCHAR(128) NOT NULL DEFAULT '', "
        "lease_token VARCHAR(64) NOT NULL DEFAULT '', "
        "lease_expires_at DATETIME, "
        "next_attempt_at DATETIME, "
        "error_code VARCHAR(64) NOT NULL DEFAULT '', "
        "error_summary VARCHAR(255) NOT NULL DEFAULT '', "
        "reason VARCHAR(255) NOT NULL DEFAULT '', "
        "created_by VARCHAR(128) NOT NULL DEFAULT '', "
        "started_at DATETIME, "
        "finished_at DATETIME, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "CONSTRAINT fk_sandbox_admin_operation_workspace "
        "FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT, "
        "CONSTRAINT ck_sandbox_admin_operation_type "
        "CHECK (operation_type IN "
        "('set_access', 'set_quota', 'bind_workspace', 'import_quota', "
        "'lease_stop', 'lease_destroy', 'lease_recreate', 'kill_switch')), "
        "CONSTRAINT ck_sandbox_admin_operation_status "
        "CHECK (status IN "
        "('pending', 'running', 'succeeded', 'failed', 'cancelled')), "
        "CONSTRAINT ck_sandbox_admin_desired_capability "
        "CHECK (desired_capability IN "
        "('', 'off', 'workspace', 'assets', 'exec')), "
        "CONSTRAINT ck_sandbox_admin_previous_capability "
        "CHECK (previous_capability IN "
        "('', 'off', 'workspace', 'assets', 'exec')), "
        "CONSTRAINT ck_sandbox_admin_desired_quota "
        "CHECK (desired_quota_bytes >= 0), "
        "CONSTRAINT ck_sandbox_admin_attempt_count "
        "CHECK (attempt_count >= 0), "
        "CONSTRAINT ck_sandbox_admin_max_attempts CHECK (max_attempts >= 1)"
        ")"
    ))
    conn.execute(text(
        f"INSERT INTO {temporary_table} ({selected_columns}) "
        f"SELECT {selected_columns} FROM sandbox_admin_operations"
    ))
    copied_rows = [
        tuple(row)
        for row in conn.execute(text(
            f"SELECT {selected_columns} FROM {temporary_table} "
            "ORDER BY operation_id"
        )).fetchall()
    ]
    if copied_rows != source_rows:
        raise SchemaMigrationValidationError(
            "sandbox_admin_operations 重建前后行数或关键字段不一致"
        )

    conn.execute(text("DROP TABLE sandbox_admin_operations"))
    conn.execute(text(
        f"ALTER TABLE {temporary_table} RENAME TO sandbox_admin_operations"
    ))
    _create_indexes(conn, [
        "CREATE UNIQUE INDEX ix_sandbox_admin_operations_request_id "
        "ON sandbox_admin_operations(request_id)",
        "CREATE INDEX ix_sandbox_admin_operations_workspace_id "
        "ON sandbox_admin_operations(workspace_id)",
        "CREATE INDEX ix_sandbox_admin_operation_status_retry "
        "ON sandbox_admin_operations(status, next_attempt_at)",
        "CREATE INDEX ix_sandbox_admin_operation_session_created "
        "ON sandbox_admin_operations(chat_stream_id, created_at)",
    ])
    if str(getattr(conn.dialect, "name", "")) == "sqlite":
        violations = conn.execute(text(
            "PRAGMA foreign_key_check(sandbox_admin_operations)"
        )).fetchall()
        if violations:
            raise SchemaMigrationValidationError(
                "sandbox_admin_operations 重建后外键校验失败"
            )


def _sandbox_execution_profiles_and_leases(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    """增加执行 Profile、Lease 与可续接进程账本字段。"""

    del engine, db_path
    _add_missing_columns(
        conn,
        "sandbox_access_grants",
        {
            "execution_profile": (
                "VARCHAR(32) NOT NULL DEFAULT 'restricted' "
                "CHECK (execution_profile IN "
                "('restricted', 'developer', 'trusted_developer'))"
            ),
        },
    )
    _add_missing_columns(
        conn,
        "sandbox_runs",
        {
            "lease_id": "VARCHAR(64)",
            "profile_id": (
                "VARCHAR(32) NOT NULL DEFAULT 'restricted' "
                "CHECK (profile_id IN "
                "('restricted', 'developer', 'trusted_developer'))"
            ),
            "execution_mode": (
                "VARCHAR(16) NOT NULL DEFAULT 'oneshot' "
                "CHECK (execution_mode IN ('oneshot', 'lease'))"
            ),
            "process_state": (
                "VARCHAR(16) NOT NULL DEFAULT 'not_applicable' "
                "CHECK (process_state IN "
                "('not_applicable', 'starting', 'running', 'exited', 'lost'))"
            ),
            "last_seen_at": "DATETIME",
        },
    )
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS sandbox_leases ("
        "lease_id VARCHAR(64) PRIMARY KEY, "
        "lease_key VARCHAR(64) NOT NULL, "
        "grant_id VARCHAR(36) NOT NULL, "
        "chat_stream_id VARCHAR(512) NOT NULL, "
        "workspace_id VARCHAR(36) NOT NULL, "
        "profile_id VARCHAR(32) NOT NULL, "
        "catalog_generation VARCHAR(64) NOT NULL, "
        "policy_sha256 VARCHAR(64) NOT NULL, "
        "status VARCHAR(16) NOT NULL DEFAULT 'provisioning', "
        "image_digest VARCHAR(255) NOT NULL DEFAULT '', "
        "controller_epoch VARCHAR(64) NOT NULL DEFAULT '', "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "last_active_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "idle_expires_at DATETIME, "
        "max_expires_at DATETIME, "
        "stopped_at DATETIME, "
        "reconciled_at DATETIME, "
        "last_error_code VARCHAR(64) NOT NULL DEFAULT '', "
        "last_error_summary VARCHAR(255) NOT NULL DEFAULT '', "
        "CONSTRAINT fk_sandbox_lease_grant "
        "FOREIGN KEY (grant_id) "
        "REFERENCES sandbox_access_grants(id) ON DELETE RESTRICT, "
        "CONSTRAINT fk_sandbox_lease_workspace "
        "FOREIGN KEY (workspace_id) "
        "REFERENCES workspaces(id) ON DELETE RESTRICT, "
        "CONSTRAINT ck_sandbox_lease_status "
        "CHECK (status IN "
        "('provisioning', 'active', 'idle', 'stopping', "
        "'stopped', 'expired', 'destroyed', 'failed'))"
        ")"
    ))
    _create_indexes(conn, [
        "CREATE INDEX IF NOT EXISTS ix_sandbox_runs_lease_id "
        "ON sandbox_runs(lease_id)",
        "CREATE INDEX IF NOT EXISTS ix_sandbox_lease_grant_id "
        "ON sandbox_leases(grant_id)",
        "CREATE INDEX IF NOT EXISTS ix_sandbox_lease_workspace_status "
        "ON sandbox_leases(workspace_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_sandbox_lease_status_expiry "
        "ON sandbox_leases(status, idle_expires_at, max_expires_at)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_sandbox_lease_key_current "
        "ON sandbox_leases(lease_key) "
        "WHERE status IN ('provisioning', 'active', 'idle', 'stopping')",
    ])
    _rebuild_sandbox_admin_operations(conn)


def _sandbox_execution_profiles_and_leases_needs_backup(conn: Any) -> bool:
    """只有旧管理表确实需要重建时才要求文件 SQLite 快照。"""

    return (
        "sandbox_admin_operations" in _table_names(conn)
        and not _sandbox_admin_operations_support_lease_types(conn)
    )


def _sandbox_runtime_project_quotas(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    """为每个 Workspace 建立独立的 Runtime project quota 绑定。"""

    del engine, db_path
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS workspace_runtime_quota_bindings ("
        "workspace_id VARCHAR(36) PRIMARY KEY, "
        "project_id INTEGER NOT NULL UNIQUE, "
        "desired_quota_bytes BIGINT NOT NULL, "
        "applied_quota_bytes BIGINT NOT NULL DEFAULT 0, "
        "status VARCHAR(16) NOT NULL DEFAULT 'pending', "
        "generation INTEGER NOT NULL DEFAULT 1, "
        "last_error_code VARCHAR(64) NOT NULL DEFAULT '', "
        "last_error_summary VARCHAR(255) NOT NULL DEFAULT '', "
        "last_applied_at DATETIME, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "CONSTRAINT fk_workspace_runtime_quota_binding_workspace "
        "FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE, "
        "CONSTRAINT ck_workspace_runtime_quota_project_id "
        "CHECK (project_id >= 10000), "
        "CONSTRAINT ck_workspace_runtime_quota_desired_positive "
        "CHECK (desired_quota_bytes > 0), "
        "CONSTRAINT ck_workspace_runtime_quota_applied_nonnegative "
        "CHECK (applied_quota_bytes >= 0), "
        "CONSTRAINT ck_workspace_runtime_quota_status "
        "CHECK (status IN ('pending', 'applying', 'applied', 'error')), "
        "CONSTRAINT ck_workspace_runtime_quota_generation "
        "CHECK (generation >= 1)"
        ")"
    ))
    _create_indexes(conn, [
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "ix_workspace_runtime_quota_bindings_project_id "
        "ON workspace_runtime_quota_bindings(project_id)",
    ])

    existing_workspace_projects = {
        int(row[0])
        for row in conn.execute(text(
            "SELECT project_id FROM workspace_quota_bindings"
        )).fetchall()
    }
    existing_runtime_projects = {
        int(row[0])
        for row in conn.execute(text(
            "SELECT project_id FROM workspace_runtime_quota_bindings"
        )).fetchall()
    }
    collisions = existing_workspace_projects & existing_runtime_projects
    if collisions:
        raise SchemaMigrationValidationError(
            "Workspace 与 Runtime project ID 发生冲突："
            f"{sorted(collisions)[:10]}"
        )

    sequence_value = conn.execute(text(
        "SELECT next_value FROM sandbox_project_sequences "
        "WHERE name = 'workspace'"
    )).scalar_one_or_none()
    next_project_id = max(
        10000,
        int(sequence_value or 10000),
        max(existing_workspace_projects | existing_runtime_projects, default=9999) + 1,
    )
    profile_runtime_quotas = {
        "restricted": 512 * 1024 * 1024,
        "developer": 10 * 1024 * 1024 * 1024,
        "trusted_developer": 10 * 1024 * 1024 * 1024,
    }
    profile_rows = conn.execute(text(
        "SELECT workspace_id, execution_profile "
        "FROM sandbox_access_grants "
        "WHERE workspace_id IS NOT NULL"
    )).fetchall()
    profiles_by_workspace: dict[str, str] = {}
    for workspace_id, profile_id in profile_rows:
        normalized_workspace = str(workspace_id)
        normalized_profile = str(profile_id or "restricted")
        previous = profiles_by_workspace.get(normalized_workspace)
        if (
            previous is None
            or profile_runtime_quotas.get(normalized_profile, 0)
            > profile_runtime_quotas.get(previous, 0)
        ):
            profiles_by_workspace[normalized_workspace] = normalized_profile

    bindings = conn.execute(text(
        "SELECT workspace_id, generation "
        "FROM workspace_quota_bindings "
        "WHERE workspace_id NOT IN ("
        "SELECT workspace_id FROM workspace_runtime_quota_bindings"
        ") "
        "ORDER BY workspace_id"
    )).fetchall()
    for workspace_id, generation in bindings:
        if next_project_id > 2_147_483_647:
            raise SchemaMigrationValidationError(
                "Runtime project ID 已耗尽"
            )
        normalized_workspace = str(workspace_id)
        profile_id = profiles_by_workspace.get(
            normalized_workspace,
            "restricted",
        )
        runtime_quota = profile_runtime_quotas.get(
            profile_id,
            profile_runtime_quotas["restricted"],
        )
        conn.execute(
            text(
                "INSERT INTO workspace_runtime_quota_bindings("
                "workspace_id, project_id, desired_quota_bytes, "
                "applied_quota_bytes, status, generation"
                ") VALUES ("
                ":workspace_id, :project_id, :desired_quota_bytes, "
                "0, 'pending', :generation"
                ")"
            ),
            {
                "workspace_id": normalized_workspace,
                "project_id": next_project_id,
                "desired_quota_bytes": runtime_quota,
                "generation": max(1, int(generation or 1)),
            },
        )
        next_project_id += 1

    conn.execute(
        text(
            "UPDATE sandbox_project_sequences "
            "SET next_value = :next_value, updated_at = :updated_at "
            "WHERE name = 'workspace' AND next_value < :next_value"
        ),
        {
            "next_value": next_project_id,
            "updated_at": db_now_naive(),
        },
    )


def _sandbox_lease_controller_state(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    """增加 sandboxd epoch 与 Server reconciler leader fencing 状态。"""

    del engine, db_path
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS sandbox_controller_states ("
        "state_key VARCHAR(32) PRIMARY KEY DEFAULT 'sandboxd', "
        "controller_epoch VARCHAR(64) NOT NULL DEFAULT '', "
        "leader_owner VARCHAR(128) NOT NULL DEFAULT '', "
        "leader_token VARCHAR(64) NOT NULL DEFAULT '', "
        "leader_expires_at DATETIME, "
        "reconciled_at DATETIME, "
        "last_error_code VARCHAR(64) NOT NULL DEFAULT '', "
        "last_error_summary VARCHAR(255) NOT NULL DEFAULT '', "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "CONSTRAINT ck_sandbox_controller_state_key "
        "CHECK (state_key = 'sandboxd')"
        ")"
    ))
    conn.execute(text(
        "INSERT INTO sandbox_controller_states(state_key) "
        "SELECT 'sandboxd' "
        "WHERE NOT EXISTS ("
        "SELECT 1 FROM sandbox_controller_states "
        "WHERE state_key = 'sandboxd'"
        ")"
    ))


def _sandbox_workspace_quota_maintenance(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    """增加 Workspace 定向配额维护门禁与已应用 generation。"""

    del engine, db_path
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS workspace_maintenance_states ("
        "workspace_id VARCHAR(36) PRIMARY KEY, "
        "status VARCHAR(16) NOT NULL DEFAULT 'quiescing', "
        "generation INTEGER NOT NULL DEFAULT 1, "
        "applied_quota_generation INTEGER NOT NULL DEFAULT 0, "
        "locked_by VARCHAR(128) NOT NULL DEFAULT '', "
        "fencing_token VARCHAR(64) NOT NULL DEFAULT '', "
        "lease_expires_at DATETIME, "
        "last_error_code VARCHAR(64) NOT NULL DEFAULT '', "
        "last_error_summary VARCHAR(255) NOT NULL DEFAULT '', "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "CONSTRAINT fk_workspace_maintenance_state_workspace "
        "FOREIGN KEY (workspace_id) "
        "REFERENCES workspaces(id) ON DELETE CASCADE, "
        "CONSTRAINT ck_workspace_maintenance_status "
        "CHECK (status IN ('ready', 'quiescing', 'error')), "
        "CONSTRAINT ck_workspace_maintenance_generation "
        "CHECK (generation >= 1), "
        "CONSTRAINT ck_workspace_maintenance_applied_generation "
        "CHECK (applied_quota_generation >= 0)"
        ")"
    ))
    # 回填只依据 Workspace 硬配额自身的一致性：存量 Workspace 的 Runtime
    # binding 由本批迁移新建、恒为 pending，若计入条件则所有存量 Workspace
    # 都落为 error，WORKSPACE 级能力在管理员逐个重发配额前全部被拒。
    # EXEC 对 Runtime quota 的 fail-closed 门禁由 access_policy 独立承担。
    conn.execute(text(
        "INSERT INTO workspace_maintenance_states("
        "workspace_id, status, generation, applied_quota_generation, "
        "last_error_code, last_error_summary"
        ") "
        "SELECT workspace_binding.workspace_id, "
        "CASE WHEN "
        "workspace_binding.status = 'applied' "
        "AND workspace_binding.desired_quota_bytes = "
        "workspace_binding.applied_quota_bytes "
        "THEN 'ready' ELSE 'error' END, "
        "workspace_binding.generation, "
        "CASE WHEN "
        "workspace_binding.status = 'applied' "
        "AND workspace_binding.desired_quota_bytes = "
        "workspace_binding.applied_quota_bytes "
        "THEN workspace_binding.generation ELSE 0 END, "
        "CASE WHEN "
        "workspace_binding.status = 'applied' "
        "AND workspace_binding.desired_quota_bytes = "
        "workspace_binding.applied_quota_bytes "
        "THEN '' ELSE 'quota_maintenance_required' END, "
        "CASE WHEN "
        "workspace_binding.status = 'applied' "
        "AND workspace_binding.desired_quota_bytes = "
        "workspace_binding.applied_quota_bytes "
        "THEN '' ELSE 'Workspace 硬配额需要重新验证' END "
        "FROM workspace_quota_bindings AS workspace_binding "
        "WHERE NOT EXISTS ("
        "SELECT 1 FROM workspace_maintenance_states AS maintenance "
        "WHERE maintenance.workspace_id = workspace_binding.workspace_id"
        ")"
    ))


def _sandbox_tool_overrides_retired(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    """删除旧 Sandbox ToolOverride，授权只由会话 grant 决定。"""

    if "tool_overrides" not in _table_names(conn):
        return
    sandbox_tools = (
        "workspace_list",
        "workspace_read",
        "workspace_search",
        "workspace_write",
        "asset_import",
        "asset_publish",
        "sandbox_exec",
    )
    placeholders = ", ".join(
        f":sandbox_tool_{index}" for index in range(len(sandbox_tools))
    )
    conn.execute(
        text(f"DELETE FROM tool_overrides WHERE tool_name IN ({placeholders})"),
        {
            f"sandbox_tool_{index}": tool_name
            for index, tool_name in enumerate(sandbox_tools)
        },
    )


def _runtime_telemetry_events(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """创建无正文 RuntimeEvent 持久化账本。"""

    from core.db.models.observability import RuntimeTelemetryEvent

    RuntimeTelemetryEvent.__table__.create(
        bind=conn,
        checkfirst=True,
    )


def _run_event_ledger_v1(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """创建版本化 Run Ledger，并在 SQLite 层拒绝事实更新和删除。"""

    from core.db.models.run_ledger import (
        RunLedgerEventRow,
        RunLedgerStreamHead,
    )

    RunLedgerStreamHead.__table__.create(bind=conn, checkfirst=True)
    RunLedgerEventRow.__table__.create(bind=conn, checkfirst=True)
    if str(getattr(conn.dialect, "name", "")) != "sqlite":
        return
    conn.execute(text(
        "CREATE TRIGGER IF NOT EXISTS "
        "trg_run_ledger_events_no_update "
        "BEFORE UPDATE ON run_ledger_events "
        "BEGIN "
        "SELECT RAISE(ABORT, 'run_ledger_events_append_only'); "
        "END"
    ))
    conn.execute(text(
        "CREATE TRIGGER IF NOT EXISTS "
        "trg_run_ledger_events_no_delete "
        "BEFORE DELETE ON run_ledger_events "
        "BEGIN "
        "SELECT RAISE(ABORT, 'run_ledger_events_append_only'); "
        "END"
    ))


def _run_evidence_governance_v1(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """创建证据治理表，并把事实删除收敛为短事务授权路径。"""

    from core.db.models.run_ledger import (
        RunLedgerErasureAuthorization,
        RunLedgerErasureReceipt,
        RunLedgerLegalHold,
    )

    for model in (
        RunLedgerLegalHold,
        RunLedgerErasureAuthorization,
        RunLedgerErasureReceipt,
    ):
        model.__table__.create(bind=conn, checkfirst=True)
    if str(getattr(conn.dialect, "name", "")) != "sqlite":
        return

    conn.execute(text("DROP TRIGGER IF EXISTS trg_run_ledger_events_no_delete"))
    conn.execute(text(
        "CREATE TRIGGER trg_run_ledger_events_no_delete "
        "BEFORE DELETE ON run_ledger_events "
        "WHEN NOT EXISTS ("
        "SELECT 1 FROM run_ledger_erasure_authorizations AS authorization "
        "WHERE authorization.run_id = OLD.run_id "
        "AND datetime(authorization.expires_at) > CURRENT_TIMESTAMP"
        ") "
        "BEGIN "
        "SELECT RAISE(ABORT, 'run_ledger_events_append_only'); "
        "END"
    ))
    conn.execute(text(
        "CREATE TRIGGER IF NOT EXISTS "
        "trg_run_ledger_stream_heads_no_delete "
        "BEFORE DELETE ON run_ledger_stream_heads "
        "WHEN NOT EXISTS ("
        "SELECT 1 FROM run_ledger_erasure_authorizations AS authorization "
        "WHERE authorization.run_id = OLD.run_id "
        "AND datetime(authorization.expires_at) > CURRENT_TIMESTAMP"
        ") "
        "BEGIN "
        "SELECT RAISE(ABORT, 'run_ledger_stream_heads_erasure_guard'); "
        "END"
    ))


def _run_checkpoint_recovery_v1(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """创建生产 Checkpoint、恢复 lineage 与副作用回执表。"""

    from core.db.models.run_recovery import (
        RunCheckpointRow,
        RunRecoveryOperation,
        RunSideEffectReceipt,
    )

    for model in (
        RunCheckpointRow,
        RunSideEffectReceipt,
        RunRecoveryOperation,
    ):
        model.__table__.create(bind=conn, checkfirst=True)
    if str(getattr(conn.dialect, "name", "")) != "sqlite":
        return
    conn.execute(text(
        "CREATE TRIGGER IF NOT EXISTS trg_run_checkpoints_no_update "
        "BEFORE UPDATE ON run_checkpoints BEGIN "
        "SELECT RAISE(ABORT, 'run_checkpoints_immutable'); END"
    ))
    for table_name in (
        "run_checkpoints",
        "run_side_effect_receipts",
        "run_recovery_operations",
    ):
        conn.execute(text(
            f"CREATE TRIGGER IF NOT EXISTS trg_{table_name}_erasure_guard "
            f"BEFORE DELETE ON {table_name} "
            "WHEN NOT EXISTS ("
            "SELECT 1 FROM run_ledger_erasure_authorizations AS authorization "
            f"WHERE authorization.run_id = OLD.run_id "
            "AND datetime(authorization.expires_at) > CURRENT_TIMESTAMP"
            ") BEGIN "
            "SELECT RAISE(ABORT, 'run_recovery_erasure_guard'); END"
        ))


def _run_durable_task_v1(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """创建所有 Agent Run 共用的执行租约与只读任务投影。"""

    from core.db.models.durable_task import RunTaskControl

    _add_missing_columns(
        conn,
        "scheduled_task_executions",
        {
            "lease_generation": "INTEGER NOT NULL DEFAULT 0",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    _add_missing_columns(
        conn,
        "outbound_runs",
        {
            "claim_generation": "INTEGER NOT NULL DEFAULT 0",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    RunTaskControl.__table__.create(bind=conn, checkfirst=True)


def _session_goal_plan_mode_v1(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """创建 Session Goal 投影、不可变计划版本和 append-only 控制事件。"""

    from core.db.models.session_goal import (
        SessionGoalEventRow,
        SessionGoalRow,
        SessionPlanAssetRow,
    )

    for model in (
        SessionGoalRow,
        SessionPlanAssetRow,
        SessionGoalEventRow,
    ):
        model.__table__.create(bind=conn, checkfirst=True)
    if str(getattr(conn.dialect, "name", "")) != "sqlite":
        return
    for table_name in ("session_plan_assets", "session_goal_events"):
        conn.execute(text(
            f"CREATE TRIGGER IF NOT EXISTS trg_{table_name}_no_update "
            f"BEFORE UPDATE ON {table_name} BEGIN "
            f"SELECT RAISE(ABORT, '{table_name}_append_only'); END"
        ))
        conn.execute(text(
            f"CREATE TRIGGER IF NOT EXISTS trg_{table_name}_no_delete "
            f"BEFORE DELETE ON {table_name} BEGIN "
            f"SELECT RAISE(ABORT, '{table_name}_append_only'); END"
        ))


def _agent_skills_lifecycle_v1(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """创建不可变 Skill 版本、资源、作用域绑定和生命周期事件。"""

    from core.db.models.skill import (
        SkillBindingRow,
        SkillLifecycleEventRow,
        SkillPackageFileRow,
        SkillPackageRow,
    )

    for model in (
        SkillPackageRow,
        SkillPackageFileRow,
        SkillBindingRow,
        SkillLifecycleEventRow,
    ):
        model.__table__.create(bind=conn, checkfirst=True)
    if str(getattr(conn.dialect, "name", "")) != "sqlite":
        return
    for table_name in (
        "skill_packages",
        "skill_package_files",
        "skill_lifecycle_events",
    ):
        conn.execute(text(
            f"CREATE TRIGGER IF NOT EXISTS trg_{table_name}_no_update "
            f"BEFORE UPDATE ON {table_name} BEGIN "
            f"SELECT RAISE(ABORT, '{table_name}_immutable'); END"
        ))
        conn.execute(text(
            f"CREATE TRIGGER IF NOT EXISTS trg_{table_name}_no_delete "
            f"BEFORE DELETE ON {table_name} BEGIN "
            f"SELECT RAISE(ABORT, '{table_name}_immutable'); END"
        ))


def _agent_skills_governance_v2(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """创建版本级 Skill 调用成本和追加式评测事实。"""

    from core.db.models.skill import SkillEvaluationRow, SkillInvocationRow

    for model in (SkillInvocationRow, SkillEvaluationRow):
        model.__table__.create(bind=conn, checkfirst=True)
    if str(getattr(conn.dialect, "name", "")) != "sqlite":
        return
    for table_name in ("skill_invocations", "skill_evaluations"):
        conn.execute(text(
            f"CREATE TRIGGER IF NOT EXISTS trg_{table_name}_no_update "
            f"BEFORE UPDATE ON {table_name} BEGIN "
            f"SELECT RAISE(ABORT, '{table_name}_append_only'); END"
        ))
        conn.execute(text(
            f"CREATE TRIGGER IF NOT EXISTS trg_{table_name}_no_delete "
            f"BEFORE DELETE ON {table_name} BEGIN "
            f"SELECT RAISE(ABORT, '{table_name}_append_only'); END"
        ))


def _mcp_control_plane_v1(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """创建 MCP 原子配置、加密秘密和追加式安全诊断。"""

    from core.db.models.mcp import (
        McpConfigurationStateRow,
        McpDiagnosticRow,
        McpSecretRow,
        McpServerRow,
    )

    for model in (
        McpConfigurationStateRow,
        McpServerRow,
        McpSecretRow,
        McpDiagnosticRow,
    ):
        model.__table__.create(bind=conn, checkfirst=True)
    if str(getattr(conn.dialect, "name", "")) != "sqlite":
        return
    conn.execute(text(
        "CREATE TRIGGER IF NOT EXISTS trg_mcp_diagnostics_no_update "
        "BEFORE UPDATE ON mcp_diagnostics BEGIN "
        "SELECT RAISE(ABORT, 'mcp_diagnostics_append_only'); END"
    ))
    conn.execute(text(
        "CREATE TRIGGER IF NOT EXISTS trg_mcp_diagnostics_no_delete "
        "BEFORE DELETE ON mcp_diagnostics BEGIN "
        "SELECT RAISE(ABORT, 'mcp_diagnostics_append_only'); END"
    ))


def _runtime_permission_governance_v1(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """创建 owner/session 精确绑定且可撤销的统一权限 grant。"""

    from core.db.models.permission import PermissionSessionGrantRow

    PermissionSessionGrantRow.__table__.create(bind=conn, checkfirst=True)


def _group_learning_stage7a_schema(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """创建群学习只读基础设施，不迁移或激活任何旧记忆。"""

    from core.db.models.group_learning import (
        GroupLearningCandidate,
        GroupLearningEvidence,
        GroupLearningRun,
        GroupLearningSchedule,
        GroupLearningStreamState,
    )

    for model in (
        GroupLearningSchedule,
        GroupLearningStreamState,
        GroupLearningCandidate,
        GroupLearningEvidence,
        GroupLearningRun,
    ):
        model.__table__.create(bind=conn, checkfirst=True)

    if "group_memories" not in _table_names(conn):
        return
    _add_missing_columns(
        conn,
        "group_memories",
        {
            "approval_source": "VARCHAR(16)",
            "governance_mode": "VARCHAR(24)",
            "approved_content_hash": "VARCHAR(64)",
            "model_review_run_id": "VARCHAR(64)",
            "model_contract_version": "VARCHAR(64)",
            "human_reviewer_id": "VARCHAR(128)",
            "human_reviewed_at": "DATETIME",
            "human_action": "VARCHAR(32)",
            "conflict_group_id": "VARCHAR(64)",
            "version": "INTEGER NOT NULL DEFAULT 1",
        },
    )
    _create_indexes(
        conn,
        [
            "CREATE INDEX IF NOT EXISTS "
            "ix_group_memories_conflict_group_id "
            "ON group_memories(conflict_group_id)",
        ],
    )


def _group_learning_stage7a_needs_backup(conn: Any) -> bool:
    """只有文件库需要 ALTER 旧 GroupMemory 时才请求在线快照。"""

    if "group_memories" not in _table_names(conn):
        return False
    required = {
        "approval_source",
        "governance_mode",
        "approved_content_hash",
        "model_review_run_id",
        "model_contract_version",
        "human_reviewer_id",
        "human_reviewed_at",
        "human_action",
        "conflict_group_id",
        "version",
    }
    return not required <= _columns(conn, "group_memories")


def _group_learning_stage7b_review_fields(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """补充 candidate-only 模型观察和人工审核账本字段。"""

    _add_missing_columns(
        conn,
        "group_learning_candidates",
        {
            "model_observed_at": "DATETIME",
            "observation_reason_hash": (
                "VARCHAR(64) NOT NULL DEFAULT ''"
            ),
            "reviewed_content": "TEXT",
            "reviewed_meaning": "TEXT",
            "reviewed_content_hash": "VARCHAR(64)",
            "human_reviewer_id": "VARCHAR(128)",
            "human_reviewed_at": "DATETIME",
            "human_action": "VARCHAR(32)",
        },
    )
    _add_missing_columns(
        conn,
        "group_learning_runs",
        {
            "mode": (
                "VARCHAR(24) NOT NULL DEFAULT 'candidate_only'"
            ),
            "task_run_id": "VARCHAR(64) NOT NULL DEFAULT ''",
            "input_chars": "INTEGER NOT NULL DEFAULT 0",
            "input_tokens": "INTEGER NOT NULL DEFAULT 0",
            "output_tokens": "INTEGER NOT NULL DEFAULT 0",
            "total_tokens": "INTEGER NOT NULL DEFAULT 0",
            "cost_microusd": "INTEGER",
            "latency_ms": "INTEGER NOT NULL DEFAULT 0",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "raw_output_bytes": "INTEGER NOT NULL DEFAULT 0",
            "raw_output_sha256": (
                "VARCHAR(64) NOT NULL DEFAULT ''"
            ),
        },
    )


def _group_learning_stage7b_needs_backup(conn: Any) -> bool:
    candidate_required = {
        "model_observed_at",
        "observation_reason_hash",
        "reviewed_content",
        "reviewed_meaning",
        "reviewed_content_hash",
        "human_reviewer_id",
        "human_reviewed_at",
        "human_action",
    }
    run_required = {
        "mode",
        "task_run_id",
        "input_chars",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cost_microusd",
        "latency_ms",
        "attempt_count",
        "raw_output_bytes",
        "raw_output_sha256",
    }
    candidate_missing = (
        "group_learning_candidates" in _table_names(conn)
        and not candidate_required
        <= _columns(conn, "group_learning_candidates")
    )
    run_missing = (
        "group_learning_runs" in _table_names(conn)
        and not run_required <= _columns(conn, "group_learning_runs")
    )
    return candidate_missing or run_missing


def _group_learning_stage7c_schedule_fencing(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """给群学习白名单调度补充独立租约 generation 和 attempt。"""

    _add_missing_columns(
        conn,
        "group_learning_schedules",
        {
            "lease_generation": "INTEGER NOT NULL DEFAULT 0",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        },
    )


def _group_learning_stage7c_schedule_needs_backup(
    conn: Any,
) -> bool:
    return (
        "group_learning_schedules" in _table_names(conn)
        and not {"lease_generation", "attempt_count"}
        <= _columns(conn, "group_learning_schedules")
    )


_GROUP_LEARNING_LEGACY_READ_ONLY_TRIGGERS = {
    (
        "expression_memories",
        "INSERT",
    ): "trg_group_learning_legacy_expression_insert_read_only",
    (
        "expression_memories",
        "UPDATE",
    ): "trg_group_learning_legacy_expression_update_read_only",
    (
        "expression_memories",
        "DELETE",
    ): "trg_group_learning_legacy_expression_delete_read_only",
    (
        "jargon_memories",
        "INSERT",
    ): "trg_group_learning_legacy_jargon_insert_read_only",
    (
        "jargon_memories",
        "UPDATE",
    ): "trg_group_learning_legacy_jargon_update_read_only",
    (
        "jargon_memories",
        "DELETE",
    ): "trg_group_learning_legacy_jargon_delete_read_only",
}


def _sqlite_trigger_names(conn: Any) -> set[str]:
    if str(getattr(conn.dialect, "name", "")) != "sqlite":
        return set()
    rows = conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type = 'trigger'"
    )).fetchall()
    return {str(row[0]) for row in rows}


def _group_learning_stage7d_legacy_read_only(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """保留旧表达和黑话读面，并在数据库层拒绝所有新写。"""

    if str(getattr(conn.dialect, "name", "")) != "sqlite":
        raise RuntimeError("旧群学习表只读迁移仅支持 SQLite")
    tables = _table_names(conn)
    for (
        table_name,
        operation,
    ), trigger_name in (
        _GROUP_LEARNING_LEGACY_READ_ONLY_TRIGGERS.items()
    ):
        if table_name not in tables:
            continue
        conn.execute(text(
            f"CREATE TRIGGER IF NOT EXISTS {trigger_name} "
            f"BEFORE {operation} ON {table_name} "
            "BEGIN "
            f"SELECT RAISE(ABORT, '{table_name}_read_only'); "
            "END"
        ))


def _group_learning_stage7d_legacy_read_only_needs_backup(
    conn: Any,
) -> bool:
    if str(getattr(conn.dialect, "name", "")) != "sqlite":
        return False
    tables = _table_names(conn)
    expected = {
        trigger_name
        for (table_name, _operation), trigger_name
        in _GROUP_LEARNING_LEGACY_READ_ONLY_TRIGGERS.items()
        if table_name in tables
    }
    return bool(expected - _sqlite_trigger_names(conn))


def _admin_idempotency_records(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """建立 Admin 写操作的数据库唯一 at-most-once 账本。"""

    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS admin_idempotency_records ("
        "request_id VARCHAR(64) PRIMARY KEY, "
        "action VARCHAR(128) NOT NULL, "
        "target_id VARCHAR(255) NOT NULL DEFAULT '', "
        "request_sha256 VARCHAR(64) NOT NULL, "
        "status VARCHAR(16) NOT NULL DEFAULT 'running' "
        "CHECK (status IN ('running','succeeded','failed')), "
        "result_json TEXT NOT NULL DEFAULT '{}', "
        "error_code VARCHAR(128) NOT NULL DEFAULT '', "
        "created_at DATETIME NOT NULL, "
        "updated_at DATETIME NOT NULL"
        ")"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS "
        "idx_admin_idempotency_records_action "
        "ON admin_idempotency_records(action)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS "
        "idx_admin_idempotency_action_target "
        "ON admin_idempotency_records(action, target_id)"
    ))


def _scheduled_task_schedule_columns(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """定时任务 schedule 规格列、到期索引与存量 cron 行回填。"""

    if "scheduled_tasks" not in _table_names(conn):
        return
    _add_missing_columns(conn, "scheduled_tasks", {
        "schedule_kind": "VARCHAR(16) NOT NULL DEFAULT 'cron'",
        "schedule_spec": "TEXT NOT NULL DEFAULT ''",
        "next_fire_at": "DATETIME",
    })
    # 合成/退化 legacy 表可能缺基础列;仅在列齐备时建索引与回填。
    columns = _columns(conn, "scheduled_tasks")
    if {"enabled", "next_fire_at"} <= columns:
        _create_indexes(conn, [
            "CREATE INDEX IF NOT EXISTS "
            "ix_scheduled_tasks_enabled_next_fire_at "
            "ON scheduled_tasks(enabled, next_fire_at)",
        ])
    if not ({"cron_expr", "schedule_kind", "schedule_spec"} <= columns):
        return

    from core.schedule_spec import schedule_fields, spec_from_fields

    rows = conn.execute(text(
        "SELECT id, cron_expr FROM scheduled_tasks "
        "WHERE schedule_spec = '' OR schedule_spec IS NULL"
    )).fetchall()
    for row_id, cron_expr in rows:
        spec = spec_from_fields("", "", cron_expr)
        if spec is None:
            # 无效表达式保持空 spec:调度器会跳过并告警,
            # 与旧匹配器"永不触发"的行为一致。
            continue
        _kind, spec_json, _expr = schedule_fields(spec)
        conn.execute(
            text(
                "UPDATE scheduled_tasks SET schedule_kind = 'cron', "
                "schedule_spec = :spec_json WHERE id = :row_id"
            ),
            {"spec_json": spec_json, "row_id": row_id},
        )


def _scheduled_task_owner_identity(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """补齐任务 owner 并让无法证明归属的旧任务失败关闭。"""

    if "scheduled_tasks" not in _table_names(conn):
        return
    _add_missing_columns(
        conn,
        "scheduled_tasks",
        {
            "owner_chat_stream_id": (
                "VARCHAR(512) NOT NULL DEFAULT ''"
            ),
            "owner_platform": "VARCHAR(32) NOT NULL DEFAULT ''",
            "owner_chat_type": "VARCHAR(16) NOT NULL DEFAULT ''",
            "owner_session_id": "VARCHAR(512) NOT NULL DEFAULT ''",
            "created_by_actor_id": (
                "VARCHAR(255) NOT NULL DEFAULT ''"
            ),
            "owner_migration_required": (
                "INTEGER NOT NULL DEFAULT 1"
            ),
            "definition_version": "INTEGER NOT NULL DEFAULT 1",
            # SQLite 的 ALTER TABLE 不允许添加 CURRENT_TIMESTAMP
            # 这类非常量默认值；旧库先加可空列并在本迁移内回填。
            "updated_at": "DATETIME",
        },
    )
    columns = _columns(conn, "scheduled_tasks")
    required = {
        "id",
        "target_type",
        "target_id",
        "enabled",
        "owner_chat_stream_id",
        "owner_platform",
        "owner_chat_type",
        "owner_session_id",
        "created_by_actor_id",
        "owner_migration_required",
        "definition_version",
        "updated_at",
    }
    if not required <= columns:
        return

    _create_indexes(
        conn,
        [
            "CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_owner "
            "ON scheduled_tasks(owner_chat_stream_id, id)",
            "CREATE INDEX IF NOT EXISTS "
            "ix_scheduled_tasks_owner_enabled "
            "ON scheduled_tasks(owner_chat_stream_id, enabled)",
        ],
    )

    rows = conn.execute(text(
        "SELECT id, target_type, target_id, created_by_actor_id "
        "FROM scheduled_tasks "
        "WHERE owner_chat_stream_id = '' "
        "OR owner_chat_stream_id IS NULL "
        "OR owner_migration_required != 0"
    )).fetchall()
    migrated: list[dict[str, object]] = []
    blocked_ids: list[int] = []
    from core.scheduled_task_contract import (
        ScheduledTaskContractError,
        scheduled_task_owner_from_target,
    )

    for row_id, target_type, target_id, actor_id in rows:
        try:
            owner = scheduled_task_owner_from_target(
                target_type=target_type,
                target_id=target_id,
                platform="qq",
                created_by_actor_id=(
                    actor_id
                    or (
                        target_id
                        if str(target_type or "").strip().lower()
                        == "private"
                        else ""
                    )
                ),
            )
        except ScheduledTaskContractError:
            conn.execute(
                text(
                    "UPDATE scheduled_tasks SET enabled = 0, "
                    "owner_migration_required = 1, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = :task_id"
                ),
                {"task_id": row_id},
            )
            blocked_ids.append(int(row_id))
            continue
        conn.execute(
            text(
                "UPDATE scheduled_tasks SET "
                "owner_chat_stream_id = :chat_stream_id, "
                "owner_platform = :platform, "
                "owner_chat_type = :chat_type, "
                "owner_session_id = :session_id, "
                "created_by_actor_id = :actor_id, "
                "owner_migration_required = 0, "
                "definition_version = CASE "
                "WHEN definition_version IS NULL "
                "OR definition_version < 1 THEN 1 "
                "ELSE definition_version END, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE id = :task_id"
            ),
            {
                "chat_stream_id": owner.chat_stream_id,
                "platform": owner.platform,
                "chat_type": owner.chat_type,
                "session_id": owner.session_id,
                "actor_id": owner.created_by_actor_id,
                "task_id": row_id,
            },
        )
        migrated.append(
            {
                "id": int(row_id),
                "owner": owner.chat_stream_id,
            }
        )

    report = {
        "scanned": len(rows),
        "migrated": len(migrated),
        "blocked": len(blocked_ids),
        "blocked_ids": blocked_ids,
        "owners": migrated,
    }
    report_sha256 = hashlib.sha256(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    logging.getLogger("nanobot").info(
        "Scheduled task owner migration scanned=%d migrated=%d "
        "blocked=%d report_sha256=%s blocked_ids=%s",
        len(rows),
        len(migrated),
        len(blocked_ids),
        report_sha256,
        blocked_ids[:50],
    )


def _scheduled_task_owner_identity_needs_backup(conn: Any) -> bool:
    if "scheduled_tasks" not in _table_names(conn):
        return False
    required = {
        "owner_chat_stream_id",
        "owner_platform",
        "owner_chat_type",
        "owner_session_id",
        "created_by_actor_id",
        "owner_migration_required",
        "definition_version",
        "updated_at",
    }
    columns = _columns(conn, "scheduled_tasks")
    if not required <= columns:
        return True
    row = conn.execute(text(
        "SELECT 1 FROM scheduled_tasks "
        "WHERE owner_chat_stream_id = '' "
        "OR owner_chat_stream_id IS NULL "
        "OR owner_migration_required != 0 "
        "LIMIT 1"
    )).first()
    return row is not None


def _scheduled_task_workflow_execution(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """建立统一任务 program、执行实例、步骤尝试和租约账本。"""

    if "scheduled_tasks" in _table_names(conn):
        _add_missing_columns(
            conn,
            "scheduled_tasks",
            {
                "program_json": "TEXT NOT NULL DEFAULT ''",
                "program_sha256": "VARCHAR(64) NOT NULL DEFAULT ''",
            },
        )
        columns = _columns(conn, "scheduled_tasks")
        if {"id", "name", "prompt_template", "program_json"} <= columns:
            rows = conn.execute(text(
                "SELECT id, name, prompt_template, program_json "
                "FROM scheduled_tasks "
                "WHERE program_json = '' OR program_json IS NULL "
                "ORDER BY id"
            )).mappings().all()
            from core.scheduled_task_contract import (
                ScheduledTaskContractError,
                normalize_scheduled_task_definition,
            )

            blocked_ids: list[int] = []
            migrated = 0
            for row in rows:
                try:
                    (
                        _name,
                        _prompt,
                        _program,
                        program_json,
                        program_sha256,
                    ) = normalize_scheduled_task_definition(
                        name=row["name"],
                        prompt_template=row["prompt_template"],
                    )
                except ScheduledTaskContractError:
                    assignments = [
                        "program_json = ''",
                        "program_sha256 = ''",
                    ]
                    if "enabled" in columns:
                        assignments.append("enabled = 0")
                    # delivery_status / last_error_summary 是既有投递结果投影，
                    # 不能被定义迁移复用。空 program + enabled=0 表达失败关闭，
                    # 详细阻断清单写入迁移日志。
                    conn.execute(
                        text(
                            "UPDATE scheduled_tasks SET "
                            + ", ".join(assignments)
                            + " WHERE id = :task_id"
                        ),
                        {"task_id": int(row["id"])},
                    )
                    blocked_ids.append(int(row["id"]))
                    continue
                conn.execute(
                    text(
                        "UPDATE scheduled_tasks SET "
                        "program_json = :program_json, "
                        "program_sha256 = :program_sha256 "
                        "WHERE id = :task_id"
                    ),
                    {
                        "program_json": program_json,
                        "program_sha256": program_sha256,
                        "task_id": int(row["id"]),
                    },
                )
                migrated += 1
            logging.getLogger("nanobot").info(
                "Scheduled task workflow migration scanned=%d migrated=%d "
                "blocked=%d blocked_ids=%s",
                len(rows),
                migrated,
                len(blocked_ids),
                blocked_ids[:50],
            )

    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS scheduled_task_executions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "task_id INTEGER NOT NULL, "
        "task_version INTEGER NOT NULL, "
        "owner_chat_stream_id VARCHAR(512) NOT NULL, "
        "occurrence_key VARCHAR(255) NOT NULL, "
        "trigger_type VARCHAR(32) NOT NULL, "
        "scheduled_for DATETIME NOT NULL, "
        "task_snapshot_json TEXT NOT NULL, "
        "task_snapshot_sha256 VARCHAR(64) NOT NULL, "
        "owner_snapshot_json TEXT NOT NULL, "
        "trigger_snapshot_json TEXT NOT NULL, "
        "program_snapshot_json TEXT NOT NULL, "
        "program_snapshot_sha256 VARCHAR(64) NOT NULL, "
        "state_json TEXT NOT NULL DEFAULT '{}', "
        "current_step_id VARCHAR(255) NOT NULL DEFAULT '', "
        "status VARCHAR(24) NOT NULL DEFAULT 'pending' "
        "CHECK (status IN ("
        "'pending','running','waiting','succeeded','failed',"
        "'blocked','ambiguous')), "
        "lease_owner VARCHAR(128), "
        "lease_token VARCHAR(64), "
        "lease_expires_at DATETIME, "
        "lease_generation INTEGER NOT NULL DEFAULT 0, "
        "attempt_count INTEGER NOT NULL DEFAULT 0, "
        "wake_at DATETIME, "
        "agent_trace_id VARCHAR(128) NOT NULL DEFAULT '', "
        "agent_run_id VARCHAR(128) NOT NULL DEFAULT '', "
        "outbound_run_id INTEGER, "
        "last_error_code VARCHAR(128) NOT NULL DEFAULT '', "
        "last_error_summary TEXT NOT NULL DEFAULT '', "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "started_at DATETIME, "
        "finished_at DATETIME"
        ")"
    ))
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_scheduled_task_execution_occurrence "
        "ON scheduled_task_executions(task_id, occurrence_key)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_scheduled_task_execution_claim "
        "ON scheduled_task_executions("
        "status, wake_at, lease_expires_at)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_scheduled_task_execution_owner "
        "ON scheduled_task_executions(owner_chat_stream_id, status)"
    ))

    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS scheduled_task_owner_leases ("
        "owner_chat_stream_id VARCHAR(512) PRIMARY KEY, "
        "execution_id INTEGER NOT NULL, "
        "lease_owner VARCHAR(128) NOT NULL, "
        "lease_token VARCHAR(64) NOT NULL, "
        "lease_expires_at DATETIME NOT NULL, "
        "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS "
        "ix_scheduled_task_owner_lease_expiry "
        "ON scheduled_task_owner_leases(lease_expires_at)"
    ))

    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS scheduled_task_step_attempts ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "execution_id INTEGER NOT NULL "
        "REFERENCES scheduled_task_executions(id), "
        "step_id VARCHAR(255) NOT NULL, "
        "attempt_no INTEGER NOT NULL, "
        "idempotency_key VARCHAR(255) NOT NULL, "
        "operation VARCHAR(16) NOT NULL "
        "CHECK (operation IN ("
        "'set','tool','model','branch','loop','wait','emit')), "
        "status VARCHAR(24) NOT NULL DEFAULT 'started' "
        "CHECK (status IN ("
        "'started','succeeded','failed','blocked','ambiguous')), "
        "input_sha256 VARCHAR(64) NOT NULL DEFAULT '', "
        "output_sha256 VARCHAR(64) NOT NULL DEFAULT '', "
        "tool_call_id VARCHAR(128) NOT NULL DEFAULT '', "
        "model_trace_id VARCHAR(128) NOT NULL DEFAULT '', "
        "checkpoint_json TEXT NOT NULL DEFAULT '', "
        "error_type VARCHAR(128) NOT NULL DEFAULT '', "
        "error_summary TEXT NOT NULL DEFAULT '', "
        "started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "completed_at DATETIME"
        ")"
    ))
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_scheduled_task_step_attempt "
        "ON scheduled_task_step_attempts("
        "execution_id, step_id, attempt_no)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS "
        "ix_scheduled_task_step_idempotency "
        "ON scheduled_task_step_attempts(idempotency_key)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS "
        "ix_scheduled_task_step_execution_status "
        "ON scheduled_task_step_attempts(execution_id, status)"
    ))


def _scheduled_task_workflow_execution_needs_backup(conn: Any) -> bool:
    if "scheduled_tasks" not in _table_names(conn):
        return False
    columns = _columns(conn, "scheduled_tasks")
    if not {"program_json", "program_sha256"} <= columns:
        return True
    return conn.execute(text(
        "SELECT 1 FROM scheduled_tasks "
        "WHERE program_json = '' OR program_json IS NULL LIMIT 1"
    )).first() is not None


_WORKSPACE_ARTIFACT_COLUMNS = frozenset({
    "id",
    "workspace_id",
    "artifact_id",
    "asset_sha256",
    "logical_name",
    "version",
    "source_run_id",
    "source_kind",
    "acl_platform",
    "acl_owner_type",
    "acl_owner_id",
    "acl_sha256",
    "created_at",
})


def _workspace_artifact_schema_current(conn: Any) -> bool:
    if "workspace_assets" not in _table_names(conn):
        return False
    if not _WORKSPACE_ARTIFACT_COLUMNS <= _columns(conn, "workspace_assets"):
        return False
    unique_columns = {
        tuple(str(column) for column in constraint.get("column_names") or ())
        for constraint in inspect(conn).get_unique_constraints("workspace_assets")
    }
    return (
        ("workspace_id", "logical_name", "version") in unique_columns
        and ("workspace_id", "logical_name") not in unique_columns
    )


def _artifact_acl_sha256(platform: str, owner_type: str, owner_id: str) -> str:
    payload = json.dumps(
        {
            "platform": str(platform),
            "owner_type": str(owner_type),
            "owner_id": str(owner_id),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _migrated_artifact_id(
    workspace_id: str,
    logical_name: str,
    version: int,
    asset_sha256: str,
) -> str:
    payload = json.dumps(
        [workspace_id, logical_name, int(version), asset_sha256],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"art_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:48]}"


def _legacy_workspace_artifact_rows(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(text(
        "SELECT wa.id, wa.workspace_id, wa.asset_sha256, wa.logical_name, "
        "wa.created_at, w.platform, w.owner_type, w.owner_id "
        "FROM workspace_assets wa "
        "JOIN workspaces w ON w.id = wa.workspace_id "
        "ORDER BY wa.id"
    )).mappings().all()
    return [
        {
            **dict(row),
            "artifact_id": _migrated_artifact_id(
                str(row["workspace_id"]),
                str(row["logical_name"]),
                1,
                str(row["asset_sha256"]),
            ),
            "version": 1,
            "source_run_id": "",
            "source_kind": "legacy",
            "acl_platform": str(row["platform"]),
            "acl_owner_type": str(row["owner_type"]),
            "acl_owner_id": str(row["owner_id"]),
            "acl_sha256": _artifact_acl_sha256(
                str(row["platform"]),
                str(row["owner_type"]),
                str(row["owner_id"]),
            ),
        }
        for row in rows
    ]


def _create_workspace_artifact_indexes(conn: Any) -> None:
    _create_indexes(conn, [
        "CREATE INDEX IF NOT EXISTS ix_workspace_assets_workspace_id "
        "ON workspace_assets(workspace_id)",
        "CREATE INDEX IF NOT EXISTS ix_workspace_asset_sha256 "
        "ON workspace_assets(asset_sha256)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_workspace_asset_artifact_id "
        "ON workspace_assets(artifact_id)",
        "CREATE INDEX IF NOT EXISTS ix_workspace_asset_source_run_id "
        "ON workspace_assets(source_run_id)",
    ])


def _artifact_lifecycle_v1(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """把 Workspace 授权链接升级为 owner-scoped 不可变 Artifact 版本。"""

    if "workspace_assets" not in _table_names(conn):
        return
    if _workspace_artifact_schema_current(conn):
        _create_workspace_artifact_indexes(conn)
        return
    required_legacy = {
        "id",
        "workspace_id",
        "asset_sha256",
        "logical_name",
        "created_at",
    }
    missing = sorted(required_legacy - _columns(conn, "workspace_assets"))
    if missing:
        raise SchemaMigrationValidationError(
            f"workspace_assets 缺少 Artifact 迁移所需列：{missing}"
        )
    rows = _legacy_workspace_artifact_rows(conn)
    dialect = str(getattr(conn.dialect, "name", ""))
    if dialect != "sqlite":
        _add_missing_columns(
            conn,
            "workspace_assets",
            {
                "artifact_id": "VARCHAR(64)",
                "version": "INTEGER NOT NULL DEFAULT 1",
                "source_run_id": "VARCHAR(64) NOT NULL DEFAULT ''",
                "source_kind": "VARCHAR(16) NOT NULL DEFAULT 'legacy'",
                "acl_platform": "VARCHAR(32)",
                "acl_owner_type": "VARCHAR(16)",
                "acl_owner_id": "VARCHAR(255)",
                "acl_sha256": "VARCHAR(64)",
            },
        )
        for row in rows:
            conn.execute(
                text(
                    "UPDATE workspace_assets SET artifact_id=:artifact_id, "
                    "version=:version, source_run_id=:source_run_id, "
                    "source_kind=:source_kind, acl_platform=:acl_platform, "
                    "acl_owner_type=:acl_owner_type, acl_owner_id=:acl_owner_id, "
                    "acl_sha256=:acl_sha256 WHERE id=:id"
                ),
                row,
            )
        for column in (
            "artifact_id",
            "acl_platform",
            "acl_owner_type",
            "acl_owner_id",
            "acl_sha256",
        ):
            conn.execute(text(
                f"ALTER TABLE workspace_assets ALTER COLUMN {column} SET NOT NULL"
            ))
        conn.execute(text(
            "ALTER TABLE workspace_assets DROP CONSTRAINT IF EXISTS "
            "uq_workspace_asset_logical_name"
        ))
        conn.execute(text(
            "ALTER TABLE workspace_assets ADD CONSTRAINT "
            "uq_workspace_asset_logical_version "
            "UNIQUE (workspace_id, logical_name, version)"
        ))
        conn.execute(text(
            "ALTER TABLE workspace_assets ADD CONSTRAINT "
            "ck_workspace_asset_version_positive CHECK (version >= 1)"
        ))
        conn.execute(text(
            "ALTER TABLE workspace_assets ADD CONSTRAINT "
            "ck_workspace_asset_source_kind CHECK (source_kind IN "
            "('legacy', 'upload', 'import', 'tool', 'model', 'runtime'))"
        ))
        conn.execute(text(
            "ALTER TABLE workspace_assets ADD CONSTRAINT "
            "ck_workspace_asset_acl_owner_type CHECK (acl_owner_type IN "
            "('user', 'group', 'project'))"
        ))
        _create_workspace_artifact_indexes(conn)
        return

    temporary_table = "workspace_assets__artifact_migration"
    if temporary_table in _table_names(conn):
        raise SchemaMigrationValidationError(
            f"迁移临时表已存在：{temporary_table}"
        )
    conn.execute(text(
        f"CREATE TABLE {temporary_table} ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "workspace_id VARCHAR(36) NOT NULL, "
        "artifact_id VARCHAR(64) NOT NULL, "
        "asset_sha256 VARCHAR(64) NOT NULL, "
        "logical_name VARCHAR(512) NOT NULL, "
        "version INTEGER NOT NULL DEFAULT 1, "
        "source_run_id VARCHAR(64) NOT NULL DEFAULT '', "
        "source_kind VARCHAR(16) NOT NULL DEFAULT 'legacy', "
        "acl_platform VARCHAR(32) NOT NULL, "
        "acl_owner_type VARCHAR(16) NOT NULL, "
        "acl_owner_id VARCHAR(255) NOT NULL, "
        "acl_sha256 VARCHAR(64) NOT NULL, "
        "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "CONSTRAINT fk_workspace_asset_workspace "
        "FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE, "
        "CONSTRAINT fk_workspace_asset_asset "
        "FOREIGN KEY (asset_sha256) REFERENCES assets(sha256) ON DELETE RESTRICT, "
        "CONSTRAINT uq_workspace_asset_logical_version "
        "UNIQUE (workspace_id, logical_name, version), "
        "CONSTRAINT uq_workspace_asset_link "
        "UNIQUE (workspace_id, asset_sha256, logical_name), "
        "CONSTRAINT ck_workspace_asset_version_positive CHECK (version >= 1), "
        "CONSTRAINT ck_workspace_asset_source_kind CHECK (source_kind IN "
        "('legacy', 'upload', 'import', 'tool', 'model', 'runtime')), "
        "CONSTRAINT ck_workspace_asset_acl_owner_type CHECK (acl_owner_type IN "
        "('user', 'group', 'project'))"
        ")"
    ))
    insert_sql = text(
        f"INSERT INTO {temporary_table} ("
        "id, workspace_id, artifact_id, asset_sha256, logical_name, version, "
        "source_run_id, source_kind, acl_platform, acl_owner_type, acl_owner_id, "
        "acl_sha256, created_at) VALUES ("
        ":id, :workspace_id, :artifact_id, :asset_sha256, :logical_name, :version, "
        ":source_run_id, :source_kind, :acl_platform, :acl_owner_type, :acl_owner_id, "
        ":acl_sha256, :created_at)"
    )
    for row in rows:
        conn.execute(insert_sql, row)
    copied = conn.execute(text(
        f"SELECT COUNT(*) FROM {temporary_table}"
    )).scalar_one()
    if int(copied) != len(rows):
        raise SchemaMigrationValidationError(
            "workspace_assets Artifact 迁移前后行数不一致"
        )
    conn.execute(text("DROP TABLE workspace_assets"))
    conn.execute(text(
        f"ALTER TABLE {temporary_table} RENAME TO workspace_assets"
    ))
    _create_workspace_artifact_indexes(conn)
    violations = conn.execute(text(
        "PRAGMA foreign_key_check(workspace_assets)"
    )).fetchall()
    if violations:
        raise SchemaMigrationValidationError(
            "workspace_assets Artifact 迁移后外键校验失败"
        )


def _artifact_lifecycle_v1_needs_backup(conn: Any) -> bool:
    return (
        "workspace_assets" in _table_names(conn)
        and not _workspace_artifact_schema_current(conn)
    )


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
    (
        "20260723_session_summary_job_fencing",
        "session summary job fencing columns",
        _session_summary_job_fencing,
    ),
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
    (
        "20260712_super_user_config_cleanup",
        "remove persisted super user configuration and redact audit details",
        _super_user_config_cleanup,
    ),
    (
        _SESSION_GUIDANCE_COLUMNS_VERSION,
        "chat stream session guidance columns",
        _chat_stream_session_guidance_columns,
    ),
    (
        _CHAT_STREAM_IDENTITY_VERSION,
        "chat stream identity normalization",
        _chat_stream_identity_normalization,
    ),
    (
        "20260714_prompt_template_resolution_columns",
        "prompt template resolution trace columns",
        _prompt_template_resolution_columns,
    ),
    (
        OUTBOUND_DELIVERY_SCHEMA_VERSION,
        "outbound delivery ledger and source projections",
        create_outbound_delivery_schema,
    ),
    (
        "20260716_summary_model_safe_defaults",
        "normalize session summary and memory digest model settings",
        _summary_model_safe_defaults,
    ),
    (
        "20260718_summary_output_contract_defaults",
        "increase summary model output limits for JSON contracts",
        _summary_output_contract_defaults,
    ),
    (
        "20260717_semantic_index_reconcile_v2",
        "semantic index reconcile lease and source revision",
        _semantic_index_reconcile_v2,
    ),
    (
        "20260718_memory_digest_jobs",
        "memory digest generation lease and retry ledger",
        _memory_digest_jobs,
    ),
    (
        "20260718_memory_cleanup_governance",
        "memory cleanup archive state and execution ledger",
        _memory_cleanup_governance,
    ),
    (
        "20260718_memory_governance_repairs",
        "recover stale digest jobs and repair legacy memory governance",
        _memory_governance_repairs,
    ),
    (
        "20260720_sandbox_storage_tables",
        "sandbox workspace asset and run tables",
        _sandbox_storage_tables,
    ),
    (
        "20260722_sandbox_control_plane_tables",
        "sandbox session grants quota bindings and admin operations",
        _sandbox_control_plane_tables,
    ),
    (
        "20260722_sandbox_project_sequence_seed",
        "initialize sandbox project id sequence",
        _sandbox_project_sequence_seed,
    ),
    (
        "20260722_sandbox_tool_overrides_retired",
        "retire sandbox tool overrides",
        _sandbox_tool_overrides_retired,
    ),
    (
        "20260723_runtime_telemetry_events",
        "runtime telemetry event ledger",
        _runtime_telemetry_events,
    ),
    (
        _GROUP_MEMORY_CANONICAL_IDENTITY_VERSION,
        "group memory canonical chat stream identity",
        _group_memory_canonical_identity,
    ),
    (
        _GROUP_LEARNING_STAGE7A_SCHEMA_VERSION,
        "group learning stage 7a schema",
        _group_learning_stage7a_schema,
    ),
    (
        _GROUP_LEARNING_STAGE7B_REVIEW_VERSION,
        "group learning stage 7b review fields",
        _group_learning_stage7b_review_fields,
    ),
    (
        _GROUP_LEARNING_STAGE7C_SCHEDULE_VERSION,
        "group learning stage 7c schedule fencing",
        _group_learning_stage7c_schedule_fencing,
    ),
    (
        _GROUP_LEARNING_STAGE7D_LEGACY_READ_ONLY_VERSION,
        "group learning stage 7d legacy tables read only",
        _group_learning_stage7d_legacy_read_only,
    ),
    (
        _ADMIN_IDEMPOTENCY_RECORDS_VERSION,
        "admin mutation idempotency records",
        _admin_idempotency_records,
    ),
    (
        _SANDBOX_EXECUTION_PROFILES_AND_LEASES_VERSION,
        "sandbox execution profiles and lease ledger",
        _sandbox_execution_profiles_and_leases,
    ),
    (
        _SANDBOX_RUNTIME_PROJECT_QUOTAS_VERSION,
        "sandbox runtime project quota bindings",
        _sandbox_runtime_project_quotas,
    ),
    (
        _SANDBOX_LEASE_CONTROLLER_STATE_VERSION,
        "sandbox lease controller epoch and reconciler fencing",
        _sandbox_lease_controller_state,
    ),
    (
        _SANDBOX_WORKSPACE_QUOTA_MAINTENANCE_VERSION,
        "sandbox workspace quota maintenance fencing",
        _sandbox_workspace_quota_maintenance,
    ),
    (
        _BLOCK_SESSION_MEMORY_VERSION,
        "block session memory: conversation_blocks table and rolling summary block_id",
        _block_session_memory_schema,
    ),
    (
        _SCHEDULED_TASK_SCHEDULE_COLUMNS_VERSION,
        "scheduled task schedule spec and next fire columns",
        _scheduled_task_schedule_columns,
    ),
    (
        _SCHEDULED_TASK_OWNER_IDENTITY_VERSION,
        "scheduled task owner identity and definition version",
        _scheduled_task_owner_identity,
    ),
    (
        _SCHEDULED_TASK_WORKFLOW_EXECUTION_VERSION,
        "scheduled task unified program and workflow execution",
        _scheduled_task_workflow_execution,
    ),
    (
        _LLM_REQUEST_EXECUTION_PHASE_VERSION,
        "llm request execution phase and round indexes",
        _llm_request_execution_phase,
    ),
    (
        _GROUP_ROLLING_CHATLOG_SOURCE_VERSION,
        "group rolling summary ChatLog source cursors",
        _group_rolling_chatlog_source,
    ),
    (
        _CHAT_LOG_SESSION_ID_INDEX_VERSION,
        "chat log session/id index",
        _chat_log_session_id_index,
    ),
    (
        _LLM_CACHE_OBSERVABILITY_VERSION,
        "llm cache hit observability",
        _llm_cache_observability,
    ),
    (
        _LLM_CACHE_DIAGNOSTICS_V2_VERSION,
        "llm cache miss tokens and prefix shape",
        _llm_cache_diagnostics_v2,
    ),
    (
        _RUN_LEDGER_V1_VERSION,
        "versioned append-only run event ledger",
        _run_event_ledger_v1,
    ),
    (
        _RUN_EVIDENCE_GOVERNANCE_V1_VERSION,
        "run evidence retention access export and erasure governance",
        _run_evidence_governance_v1,
    ),
    (
        _RUN_RECOVERY_V1_VERSION,
        "run checkpoint recovery lineage and side effect receipts",
        _run_checkpoint_recovery_v1,
    ),
    (
        _RUN_DURABLE_TASK_V1_VERSION,
        "run durable task lease heartbeat fencing and reconciliation",
        _run_durable_task_v1,
    ),
    (
        _ARTIFACT_LIFECYCLE_V1_VERSION,
        "owner scoped immutable artifact versions",
        _artifact_lifecycle_v1,
    ),
    (
        _LLM_PROVIDER_CACHE_PERFORMANCE_VERSION,
        "llm provider cache token latency and cost metrics",
        _llm_provider_cache_performance,
    ),
    (
        _SESSION_GOAL_PLAN_MODE_V1_VERSION,
        "session goal plan mode and immutable plan assets",
        _session_goal_plan_mode_v1,
    ),
    (
        _AGENT_SKILLS_LIFECYCLE_V1_VERSION,
        "agent skills immutable versions scoped bindings and lifecycle",
        _agent_skills_lifecycle_v1,
    ),
    (
        _AGENT_SKILLS_GOVERNANCE_V2_VERSION,
        "agent skills registry retrieval usage cost and evaluations",
        _agent_skills_governance_v2,
    ),
    (
        _MCP_CONTROL_PLANE_V1_VERSION,
        "mcp atomic configuration encrypted secrets transports and diagnostics",
        _mcp_control_plane_v1,
    ),
    (
        _RUNTIME_PERMISSION_GOVERNANCE_V1_VERSION,
        "runtime permission session grants and revocation",
        _runtime_permission_governance_v1,
    ),
]


def _prepare_schema_migration_backup(
    conn: Any,
    engine: Any,
    db_path: str | None,
) -> None:
    applied_before_transaction = _existing_applied_versions(conn)
    chat_log_backup_needed = (
        _CHAT_LOG_METADATA_VERSION not in applied_before_transaction
        and _chat_log_metadata_needs_backup(conn)
    )
    identity_backup_needed = (
        _CHAT_STREAM_IDENTITY_VERSION not in applied_before_transaction
        and _chat_stream_identity_needs_backup(conn)
    )
    outbound_backup_needed = (
        OUTBOUND_DELIVERY_SCHEMA_VERSION not in applied_before_transaction
        and outbound_delivery_schema_needs_backup(conn)
    )
    group_learning_backup_needed = (
        _GROUP_LEARNING_STAGE7A_SCHEMA_VERSION
        not in applied_before_transaction
        and _group_learning_stage7a_needs_backup(conn)
    )
    group_learning_stage7b_backup_needed = (
        _GROUP_LEARNING_STAGE7B_REVIEW_VERSION
        not in applied_before_transaction
        and _group_learning_stage7b_needs_backup(conn)
    )
    group_learning_stage7c_backup_needed = (
        _GROUP_LEARNING_STAGE7C_SCHEDULE_VERSION
        not in applied_before_transaction
        and _group_learning_stage7c_schedule_needs_backup(conn)
    )
    group_learning_stage7d_backup_needed = (
        _GROUP_LEARNING_STAGE7D_LEGACY_READ_ONLY_VERSION
        not in applied_before_transaction
        and _group_learning_stage7d_legacy_read_only_needs_backup(
            conn
        )
    )
    sandbox_lease_backup_needed = (
        _SANDBOX_EXECUTION_PROFILES_AND_LEASES_VERSION
        not in applied_before_transaction
        and _sandbox_execution_profiles_and_leases_needs_backup(conn)
    )
    artifact_lifecycle_backup_needed = (
        _ARTIFACT_LIFECYCLE_V1_VERSION
        not in applied_before_transaction
        and _artifact_lifecycle_v1_needs_backup(conn)
    )
    scheduled_task_owner_backup_needed = (
        _SCHEDULED_TASK_OWNER_IDENTITY_VERSION
        not in applied_before_transaction
        and _scheduled_task_owner_identity_needs_backup(conn)
    )
    scheduled_task_workflow_backup_needed = (
        _SCHEDULED_TASK_WORKFLOW_EXECUTION_VERSION
        not in applied_before_transaction
        and _scheduled_task_workflow_execution_needs_backup(conn)
    )
    drivername = str(getattr(getattr(engine, "url", None), "drivername", ""))
    if drivername.startswith("sqlite") and (
        chat_log_backup_needed
        or identity_backup_needed
        or outbound_backup_needed
        or group_learning_backup_needed
        or group_learning_stage7b_backup_needed
        or group_learning_stage7c_backup_needed
        or group_learning_stage7d_backup_needed
        or sandbox_lease_backup_needed
        or scheduled_task_owner_backup_needed
        or scheduled_task_workflow_backup_needed
        or artifact_lifecycle_backup_needed
    ):
        backup_path = _migration_backup_path(engine, db_path)
        if backup_path is not None:
            _backup_sqlite_db(backup_path)


def _apply_pending_schema_migrations(
    conn: Any,
    engine: Any,
    db_path: str | None,
    applied: set[str],
) -> None:
    for version, name, fn in MIGRATIONS:
        if version in applied:
            continue
        fn(conn, engine, db_path)
        _record(conn, version, name)


def run_schema_migrations(engine: Any, *, db_path: str | None = None) -> None:
    drivername = str(getattr(getattr(engine, "url", None), "drivername", ""))
    if drivername.startswith("sqlite"):
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            run_sqlite_locked_retry(
                lambda: conn.exec_driver_sql("BEGIN IMMEDIATE"),
                rollback=conn.rollback,
                label="schema_migration_lock",
                logger=logging.getLogger("nanobot"),
                attempts=_SCHEMA_MIGRATION_LOCK_ATTEMPTS,
                base_delay_seconds=_SCHEMA_MIGRATION_LOCK_RETRY_DELAY_SECONDS,
            )
            version_table_initialized = False
            try:
                _prepare_schema_migration_backup(conn, engine, db_path)
                applied = _applied_versions(conn)
                version_table_initialized = True
                _apply_pending_schema_migrations(conn, engine, db_path, applied)
            except BaseException as exc:
                conn.rollback()
                if version_table_initialized:
                    try:
                        _ensure_table(conn)
                        conn.commit()
                    except BaseException as recovery_exc:
                        conn.rollback()
                        add_note = getattr(exc, "add_note", None)
                        if callable(add_note):
                            add_note(
                                "迁移回滚后恢复 schema_migrations 表失败: "
                                f"{type(recovery_exc).__name__}: {recovery_exc}"
                            )
                raise
            else:
                conn.commit()
        return

    with engine.begin() as conn:
        _prepare_schema_migration_backup(conn, engine, db_path)
        applied = _applied_versions(conn)
        _apply_pending_schema_migrations(conn, engine, db_path, applied)

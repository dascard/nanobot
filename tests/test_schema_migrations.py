import json

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError


_GROUP_MEMORY_CANONICAL_IDENTITY_VERSION = (
    "20260723_group_memory_canonical_identity"
)


def _legacy_group_memory_engine(
    rows: list[dict],
    *,
    include_canonical_column: bool = False,
):
    from core.schema_migrations import MIGRATIONS

    engine = create_engine("sqlite:///:memory:")
    canonical_column = (
        ", chat_stream_id TEXT"
        if include_canonical_column
        else ""
    )
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE schema_migrations ("
            "version TEXT PRIMARY KEY, name TEXT NOT NULL, "
            "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))
        conn.execute(
            text(
                "INSERT INTO schema_migrations(version, name) "
                "VALUES (:version, :name)"
            ),
            [
                {"version": version, "name": name}
                for version, name, _migration in MIGRATIONS
                if version != _GROUP_MEMORY_CANONICAL_IDENTITY_VERSION
            ],
        )
        conn.execute(text(
            "CREATE TABLE group_memories ("
            "id INTEGER PRIMARY KEY, group_id TEXT NOT NULL, "
            "memory_type TEXT NOT NULL, content_hash TEXT NOT NULL"
            f"{canonical_column})"
        ))
        if rows:
            column_names = [
                "id",
                "group_id",
                "memory_type",
                "content_hash",
            ]
            if include_canonical_column:
                column_names.append("chat_stream_id")
            placeholders = ", ".join(
                f":{column_name}" for column_name in column_names
            )
            conn.execute(
                text(
                    "INSERT INTO group_memories("
                    + ", ".join(column_names)
                    + f") VALUES ({placeholders})"
                ),
                rows,
            )
    return engine


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
    llm_log_columns = {
        col["name"]
        for col in inspector.get_columns("llm_api_request_logs")
    }
    assert {
        "response_json",
        "phase",
        "round_index",
        "route_attempt_index",
        "cache_status",
        "cache_hit",
        "cache_hit_tokens",
        "cache_miss_tokens",
        "cache_write_tokens",
        "cache_details_json",
    } <= llm_log_columns
    reply_contract_columns = [col["name"] for col in inspector.get_columns("reply_contract_check_logs")]
    assert "reply_tool_call_count" in reply_contract_columns
    assert "total_final_action_count" in reply_contract_columns
    assert "rolling_session_summaries" in inspector.get_table_names()
    assert "web_search_provider_usage" in inspector.get_table_names()
    assert {
        "workspaces",
        "assets",
        "workspace_assets",
        "sandbox_runs",
        "sandbox_leases",
        "sandbox_access_grants",
        "workspace_quota_bindings",
        "sandbox_admin_operations",
        "sandbox_project_sequences",
        "workspace_runtime_quota_bindings",
        "workspace_maintenance_states",
        "admin_idempotency_records",
    } <= set(inspector.get_table_names())
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
    workspace_indexes = {
        row["name"]
        for row in inspector.get_indexes("workspaces")
    }
    assert "ix_workspace_owner" in workspace_indexes
    sandbox_run_columns = {
        column["name"]
        for column in inspector.get_columns("sandbox_runs")
    }
    assert {
        "request_id",
        "trace_id",
        "agent_run_id",
        "tool_call_id",
        "image_digest",
        "lease_id",
        "profile_id",
        "execution_mode",
        "process_state",
        "last_seen_at",
        "termination_reason",
        "peak_memory_bytes",
        "stdout_bytes",
        "stderr_bytes",
    } <= sandbox_run_columns
    grant_columns = {
        column["name"]
        for column in inspector.get_columns("sandbox_access_grants")
    }
    assert {
        "chat_stream_id",
        "platform",
        "chat_type",
        "external_session_id",
        "workspace_id",
        "capability_level",
        "execution_profile",
        "status",
        "version",
    } <= grant_columns
    lease_columns = {
        column["name"]
        for column in inspector.get_columns("sandbox_leases")
    }
    assert {
        "lease_id",
        "lease_key",
        "grant_id",
        "chat_stream_id",
        "workspace_id",
        "profile_id",
        "catalog_generation",
        "policy_sha256",
        "status",
        "image_digest",
        "controller_epoch",
        "created_at",
        "last_active_at",
        "idle_expires_at",
        "max_expires_at",
        "stopped_at",
        "reconciled_at",
        "last_error_code",
        "last_error_summary",
    } == lease_columns
    quota_columns = {
        column["name"]
        for column in inspector.get_columns("workspace_quota_bindings")
    }
    assert {
        "workspace_id",
        "project_id",
        "desired_quota_bytes",
        "applied_quota_bytes",
        "status",
        "generation",
        "last_error_code",
    } <= quota_columns
    runtime_quota_columns = {
        column["name"]
        for column in inspector.get_columns(
            "workspace_runtime_quota_bindings"
        )
    }
    assert {
        "workspace_id",
        "project_id",
        "desired_quota_bytes",
        "applied_quota_bytes",
        "status",
        "generation",
        "last_error_code",
    } <= runtime_quota_columns
    maintenance_columns = {
        column["name"]
        for column in inspector.get_columns(
            "workspace_maintenance_states"
        )
    }
    assert {
        "workspace_id",
        "status",
        "generation",
        "applied_quota_generation",
        "locked_by",
        "fencing_token",
        "lease_expires_at",
        "last_error_code",
    } <= maintenance_columns
    operation_columns = {
        column["name"]
        for column in inspector.get_columns("sandbox_admin_operations")
    }
    assert {
        "operation_id",
        "request_id",
        "operation_type",
        "chat_stream_id",
        "workspace_id",
        "desired_capability",
        "expected_quota_generation",
        "status",
        "step",
        "lease_token",
        "lease_expires_at",
        "next_attempt_at",
    } <= operation_columns
    admin_idempotency_columns = {
        column["name"]
        for column in inspector.get_columns(
            "admin_idempotency_records"
        )
    }
    assert {
        "request_id",
        "action",
        "target_id",
        "request_sha256",
        "status",
        "result_json",
        "error_code",
        "created_at",
        "updated_at",
    } <= admin_idempotency_columns

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version FROM schema_migrations ORDER BY version")).fetchall()
        project_sequence = conn.execute(text(
            "SELECT next_value FROM sandbox_project_sequences WHERE name = 'workspace'"
        )).scalar_one()

    assert [row[0] for row in rows] == sorted(version for version, _, _ in MIGRATIONS)
    assert project_sequence == 10000


def test_scheduled_task_owner_migration_scopes_valid_rows_and_blocks_unknown():
    from core.schema_migrations import (
        MIGRATIONS,
        run_schema_migrations,
    )

    owner_version = "20260729_scheduled_task_owner_identity"
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE schema_migrations ("
            "version TEXT PRIMARY KEY, name TEXT NOT NULL, "
            "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))
        conn.execute(
            text(
                "INSERT INTO schema_migrations(version, name) "
                "VALUES (:version, :name)"
            ),
            [
                {"version": version, "name": name}
                for version, name, _migration in MIGRATIONS
                if version != owner_version
            ],
        )
        conn.execute(text(
            "CREATE TABLE scheduled_tasks ("
            "id INTEGER PRIMARY KEY, "
            "target_type TEXT, target_id TEXT, enabled INTEGER)"
        ))
        conn.execute(
            text(
                "INSERT INTO scheduled_tasks("
                "id, target_type, target_id, enabled"
                ") VALUES (:id, :target_type, :target_id, 1)"
            ),
            [
                {
                    "id": 1,
                    "target_type": "group",
                    "target_id": "10001",
                },
                {
                    "id": 2,
                    "target_type": "private",
                    "target_id": "u2",
                },
                {
                    "id": 3,
                    "target_type": "unknown",
                    "target_id": "",
                },
            ],
        )

    run_schema_migrations(engine)
    run_schema_migrations(engine)

    inspector = inspect(engine)
    columns = {
        column["name"]
        for column in inspector.get_columns("scheduled_tasks")
    }
    assert {
        "owner_chat_stream_id",
        "owner_platform",
        "owner_chat_type",
        "owner_session_id",
        "created_by_actor_id",
        "owner_migration_required",
        "definition_version",
        "updated_at",
    } <= columns
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, owner_chat_stream_id, owner_session_id, "
            "created_by_actor_id, owner_migration_required, enabled "
            "FROM scheduled_tasks ORDER BY id"
        )).mappings().all()

    assert dict(rows[0]) == {
        "id": 1,
        "owner_chat_stream_id": "qq:10001:group",
        "owner_session_id": "group_10001",
        "created_by_actor_id": "",
        "owner_migration_required": 0,
        "enabled": 1,
    }
    assert dict(rows[1]) == {
        "id": 2,
        "owner_chat_stream_id": "qq:u2:private",
        "owner_session_id": "u2",
        "created_by_actor_id": "u2",
        "owner_migration_required": 0,
        "enabled": 1,
    }
    assert rows[2]["owner_chat_stream_id"] == ""
    assert rows[2]["owner_migration_required"] == 1
    assert rows[2]["enabled"] == 0


def test_llm_cache_observability_migration_backfills_existing_logs():
    from core.schema_migrations import _llm_cache_observability

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE llm_api_request_logs ("
            "id INTEGER PRIMARY KEY, status TEXT, response_json TEXT)"
        ))
        conn.execute(
            text(
                "INSERT INTO llm_api_request_logs(id, status, response_json) "
                "VALUES (:id, :status, :response_json)"
            ),
            [
                {
                    "id": 1,
                    "status": "success",
                    "response_json": json.dumps({
                        "usage": {
                            "prompt_tokens_details": {"cached_tokens": 12},
                        },
                    }),
                },
                {
                    "id": 2,
                    "status": "success",
                    "response_json": json.dumps({"usage": {"prompt_tokens": 4}}),
                },
                {
                    "id": 3,
                    "status": "error",
                    "response_json": "{}",
                },
                {
                    "id": 4,
                    "status": "created",
                    "response_json": "{}",
                },
            ],
        )

        _llm_cache_observability(conn, engine, None)
        rows = conn.execute(text(
            "SELECT id, cache_status, cache_hit, cache_hit_tokens, "
            "cache_miss_tokens FROM llm_api_request_logs ORDER BY id"
        )).mappings().all()

    assert [row["cache_status"] for row in rows] == [
        "hit",
        "not_reported",
        "error",
        "pending",
    ]
    assert rows[0]["cache_hit"] == 1
    assert rows[0]["cache_hit_tokens"] == 12
    assert rows[0]["cache_miss_tokens"] == 0
    assert rows[1]["cache_hit"] is None
    assert rows[2]["cache_hit"] is None
    assert rows[3]["cache_hit"] is None


def test_llm_cache_diagnostics_v2_backfills_deepseek_miss_and_shape():
    from core.schema_migrations import _llm_cache_diagnostics_v2

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE llm_api_request_logs ("
            "id INTEGER PRIMARY KEY, status TEXT, source TEXT, model TEXT, "
            "request_json TEXT, response_json TEXT, cache_details_json TEXT)"
        ))
        conn.execute(text(
            "INSERT INTO llm_api_request_logs("
            "id, status, source, model, request_json, response_json, "
            "cache_details_json) VALUES (1, 'success', 'replyer.group_chat', "
            "'deepseek-chat', :request_json, :response_json, '{}')"
        ), {
            "request_json": json.dumps({
                "messages": [
                    {"role": "system", "content": "固定前缀"},
                    {"role": "user", "content": "当前消息"},
                ],
                "tools": [],
            }, ensure_ascii=False),
            "response_json": json.dumps({
                "usage": {
                    "prompt_cache_hit_tokens": 20,
                    "prompt_cache_miss_tokens": 80,
                },
            }),
        })

        _llm_cache_diagnostics_v2(conn, engine, None)
        row = conn.execute(text(
            "SELECT cache_miss_tokens, cache_details_json "
            "FROM llm_api_request_logs WHERE id = 1"
        )).mappings().one()

    assert row["cache_miss_tokens"] == 80
    details = json.loads(row["cache_details_json"])
    assert details["cache_shape"]["leading_system_sha256"]
    assert details["cache_shape"]["scope_sha256"]


def test_block_session_memory_migration_adds_table_and_column():
    """块式会话记忆迁移:conversation_blocks 表 + rolling_session_summaries.block_id。"""

    from core.schema_migrations import run_schema_migrations

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
    # rolling_session_summaries 由迁移链的 _rolling_session_summaries 建全表(无
    # block_id),随后 _block_session_memory_schema 追加 block_id,验证加列路径。

    # 幂等:连续运行两次不应报错。
    run_schema_migrations(engine)
    run_schema_migrations(engine)

    inspector = inspect(engine)
    assert "conversation_blocks" in inspector.get_table_names()

    block_columns = {col["name"] for col in inspector.get_columns("conversation_blocks")}
    for expected in {
        "session_id", "user_id", "chat_type", "block_seq", "status", "open_key",
        "first_turn_id", "last_turn_id", "last_turn_at", "closed_at",
        "turn_count", "token_estimate", "closed_reason",
        "rolling_summary_id", "episode_id",
    }:
        assert expected in block_columns, expected

    rss_columns = {col["name"] for col in inspector.get_columns("rolling_session_summaries")}
    assert "block_id" in rss_columns

    index_names = {idx["name"] for idx in inspector.get_indexes("conversation_blocks")}
    assert "uq_conversation_block_open_key" in index_names

    # 唯一 open 块:同一 session 只允许一个非 NULL open_key,多个 NULL 互异。
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO conversation_blocks(session_id, open_key, status) "
            "VALUES ('s1', 's1', 'open')"
        ))
        conn.execute(text(
            "INSERT INTO conversation_blocks(session_id, open_key, status) "
            "VALUES ('s1', NULL, 'closed')"
        ))
        conn.execute(text(
            "INSERT INTO conversation_blocks(session_id, open_key, status) "
            "VALUES ('s1', NULL, 'closed')"
        ))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO conversation_blocks(session_id, open_key, status) "
                "VALUES ('s1', 's1', 'open')"
            ))


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
    assert rows["model.route.session_summary.max_tokens"] == "4096"
    assert rows["model.route.session_summary.enable_thinking"] == "false"
    assert rows["model.route.memory_digest.temperature"] == "0.1"
    assert rows["model.route.memory_digest.max_tokens"] == "8192"
    assert rows["model.route.memory_digest.enable_thinking"] == "false"
    assert rows["model.route.reply.temperature"] == "0.7"

    run_schema_migrations(engine)
    with engine.connect() as conn:
        value = conn.execute(text(
            "SELECT value FROM system_settings "
            "WHERE \"key\" = 'model.route.session_summary.temperature'"
        )).scalar_one()
    assert value == "0.2"


def test_memory_governance_repair_cleans_stale_jobs_indexes_and_single_speaker_topics():
    from core.schema_migrations import _memory_governance_repairs

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE memory_digest_jobs ("
            "id INTEGER PRIMARY KEY, status TEXT, locked_by TEXT, lease_token TEXT, "
            "lease_expires_at DATETIME, retry_count INTEGER, max_retry INTEGER, "
            "next_retry_at DATETIME, error_type TEXT, error_summary TEXT, "
            "meta_json TEXT, finished_at DATETIME, updated_at DATETIME)"
        ))
        conn.execute(
            text(
                "INSERT INTO memory_digest_jobs VALUES ("
                "15, 'running', 'dead-worker', 'token', '2020-01-01 00:00:00', "
                "1, 3, NULL, '', '', :meta_json, NULL, NULL)"
            ),
            {"meta_json": '{"batch_checkpoint":{"schema_version":1}}'},
        )
        conn.execute(text(
            "CREATE TABLE rolling_session_summaries ("
            "id INTEGER PRIMARY KEY, summary_kind TEXT)"
        ))
        conn.execute(text(
            "INSERT INTO rolling_session_summaries VALUES "
            "(7, 'deterministic_fallback'), (8, 'llm_episode')"
        ))
        conn.execute(text(
            "CREATE TABLE semantic_index_items ("
            "id INTEGER PRIMARY KEY, source_type TEXT, document_id TEXT, status TEXT, "
            "meta_json TEXT, deleted_at DATETIME, updated_at DATETIME)"
        ))
        conn.execute(text(
            "INSERT INTO semantic_index_items VALUES "
            "(1, 'session_summary', '7', 'active', '{\"summary_kind\":\"deterministic_fallback\"}', NULL, NULL), "
            "(2, 'session_summary', '8', 'active', '{\"summary_kind\":\"llm_episode\"}', NULL, NULL)"
        ))
        conn.execute(text("CREATE TABLE semantic_index_fts (title TEXT, text TEXT)"))
        conn.execute(text(
            "INSERT INTO semantic_index_fts(rowid, title, text) VALUES "
            "(1, '旧兜底', '不应召回'), (2, 'LLM', '应保留')"
        ))
        conn.execute(text(
            "CREATE TABLE chat_logs ("
            "id INTEGER PRIMARY KEY, user_id TEXT, session_id TEXT, sender_name TEXT, "
            "role TEXT, meta_json TEXT)"
        ))
        conn.execute(text(
            "INSERT INTO chat_logs VALUES "
            "(11, 'group_42', 'group_42', '同名成员', 'ambient', "
            "'{\"sender\":{\"id\":\"speaker-a\",\"name\":\"同名成员\"}}'), "
            "(12, 'group_42', 'group_42', '同名成员', 'ambient', "
            "'{\"sender\":{\"id\":\"speaker-a\",\"name\":\"同名成员\"}}'), "
            "(13, 'group_42', 'group_42', '同名成员', 'ambient', "
            "'{\"sender\":{\"id\":\"speaker-a\",\"name\":\"同名成员\"}}'), "
            "(14, 'group_42', 'group_42', '同名成员', 'ambient', "
            "'{\"sender\":{\"id\":\"speaker-b\",\"name\":\"同名成员\"}}')"
        ))
        conn.execute(text(
            "CREATE TABLE group_memories ("
            "id INTEGER PRIMARY KEY, memory_type TEXT, status TEXT, source TEXT, "
            "evidence_log_ids_json TEXT, inject_policy TEXT, meta_json TEXT, updated_at DATETIME)"
        ))
        conn.execute(text(
            "INSERT INTO group_memories VALUES "
            "(1, 'topic', 'active', 'group_analysis', '[11,12]', 'auto', '{}', NULL), "
            "(2, 'topic', 'active', 'group_analysis', '[13,14]', 'auto', '{}', NULL)"
        ))

        _memory_governance_repairs(conn, engine, None)

        job = conn.execute(text(
            "SELECT status, error_type, next_retry_at, meta_json "
            "FROM memory_digest_jobs WHERE id = 15"
        )).one()
        semantic_rows = conn.execute(text(
            "SELECT id, status FROM semantic_index_items ORDER BY id"
        )).all()
        fts_ids = [row[0] for row in conn.execute(text(
            "SELECT rowid FROM semantic_index_fts ORDER BY rowid"
        )).all()]
        group_rows = conn.execute(text(
            "SELECT id, status, meta_json FROM group_memories ORDER BY id"
        )).all()

    assert job[0] == "failed"
    assert job[1] == "lease_expired_recovered"
    assert job[2] is not None
    assert json.loads(job[3])["batch_checkpoint"]["schema_version"] == 1
    assert semantic_rows == [(1, "deleted"), (2, "active")]
    assert fts_ids == [2]
    assert group_rows[0][1] == "review"
    first_meta = json.loads(group_rows[0][2])
    assert first_meta["evidence_speaker_count"] == 1
    assert first_meta["evidence_speakers"] == ["speaker-a"]
    assert group_rows[1][1] == "active"
    second_meta = json.loads(group_rows[1][2])
    assert second_meta["evidence_speaker_count"] == 2
    assert second_meta["evidence_speakers"] == ["speaker-a", "speaker-b"]


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


def test_session_summary_fencing_migration_requeues_legacy_running_once():
    from core.schema_migrations import run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE session_summary_jobs ("
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
            "updated_at DATETIME)"
        ))
        conn.execute(text(
            "INSERT INTO session_summary_jobs("
            "id, session_id, status, retry_count, max_retry, "
            "locked_by, locked_at, error, updated_at"
            ") VALUES ("
            "1, 'legacy-running', 'running', 1, 3, "
            "'legacy-worker', '2026-07-23 01:00:00', "
            "'legacy-running', '2026-07-23 01:00:00'"
            ")"
        ))

    run_schema_migrations(engine)
    run_schema_migrations(engine)

    inspector = inspect(engine)
    columns = {
        column["name"]
        for column in inspector.get_columns("session_summary_jobs")
    }
    assert {
        "lease_token",
        "lease_expires_at",
        "generation",
        "attempt_count",
        "finished_at",
    } <= columns

    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT status, retry_count, locked_by, locked_at, "
            "lease_token, lease_expires_at, generation, attempt_count, "
            "error, next_retry_at, finished_at "
            "FROM session_summary_jobs WHERE id = 1"
        )).mappings().one()
        migration_count = conn.execute(text(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = '20260723_session_summary_job_fencing'"
        )).scalar_one()

    assert row["status"] == "pending"
    assert row["retry_count"] == 1
    assert row["locked_by"] == ""
    assert row["locked_at"] is None
    assert row["lease_token"] == ""
    assert row["lease_expires_at"] is None
    assert row["generation"] == 0
    assert row["attempt_count"] == 0
    assert row["error"] == "migration_requeued_legacy_running"
    assert row["next_retry_at"] is not None
    assert row["finished_at"] is None
    assert migration_count == 1


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


def test_sandbox_tool_override_migration_removes_only_sandbox_tools():
    from core.schema_migrations import run_schema_migrations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE tool_overrides ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "tool_name TEXT NOT NULL, "
            "scope_type TEXT NOT NULL, "
            "scope_id TEXT NOT NULL, "
            "enabled BOOLEAN NOT NULL)"
        ))
        conn.execute(text(
            "INSERT INTO tool_overrides(tool_name, scope_type, scope_id, enabled) "
            "VALUES "
            "('workspace_read', 'user', 'u1', 1), "
            "('sandbox_exec', 'chat_type', 'private_superuser', 1), "
            "('memory_query', 'user', 'u1', 1)"
        ))

    run_schema_migrations(engine)
    run_schema_migrations(engine)

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT tool_name FROM tool_overrides ORDER BY tool_name"
        )).scalars().all()
        version_count = conn.execute(text(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = '20260722_sandbox_tool_overrides_retired'"
        )).scalar_one()

    assert rows == ["memory_query"]
    assert version_count == 1


def test_runtime_telemetry_event_migration_is_idempotent_and_indexed():
    from core.schema_migrations import run_schema_migrations

    engine = create_engine("sqlite:///:memory:")

    run_schema_migrations(engine)
    run_schema_migrations(engine)

    inspector = inspect(engine)
    assert "runtime_telemetry_events" in inspector.get_table_names()
    columns = {
        column["name"]
        for column in inspector.get_columns("runtime_telemetry_events")
    }
    assert {
        "event_id",
        "name",
        "domain",
        "phase",
        "occurred_at",
        "request_id",
        "session_id",
        "turn_id",
        "trace_id",
        "run_id",
        "task_id",
        "task_run_id",
        "job_id",
        "tool_call_id",
        "delivery_id",
        "parent_job_id",
        "registry_generation",
        "registry_sha256",
        "module_id",
        "module_version",
        "artifact_revision",
        "failure_code",
        "attributes_json",
        "dropped_attribute_count",
    } <= columns
    indexes = {
        index["name"]
        for index in inspector.get_indexes("runtime_telemetry_events")
    }
    assert {
        "ix_runtime_telemetry_name_time",
        "ix_runtime_telemetry_job_time",
        "ix_runtime_telemetry_task_time",
    } <= indexes
    with engine.connect() as conn:
        version_count = conn.execute(text(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = '20260723_runtime_telemetry_events'"
        )).scalar_one()
    assert version_count == 1


def test_group_memory_canonical_identity_migration_backfills_and_is_idempotent():
    from core.schema_migrations import run_schema_migrations

    engine = _legacy_group_memory_engine([
        {
            "id": 1,
            "group_id": "group_42",
            "memory_type": "topic",
            "content_hash": "hash-1",
        },
        {
            "id": 2,
            "group_id": "qq:43:group",
            "memory_type": "style",
            "content_hash": "hash-2",
        },
        {
            "id": 3,
            "group_id": "研发:一组",
            "memory_type": "event",
            "content_hash": "hash-3",
        },
    ])

    run_schema_migrations(engine)
    run_schema_migrations(engine)

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, chat_stream_id, group_id "
            "FROM group_memories ORDER BY id"
        )).fetchall()
        version_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM schema_migrations "
                "WHERE version = :version"
            ),
            {"version": _GROUP_MEMORY_CANONICAL_IDENTITY_VERSION},
        ).scalar_one()

    assert rows == [
        (1, "qq:42:group", "group_42"),
        (2, "qq:43:group", "group_43"),
        (
            3,
            "qq:%E7%A0%94%E5%8F%91%3A%E4%B8%80%E7%BB%84:group",
            "group_研发:一组",
        ),
    ]
    assert version_count == 1
    indexes = {
        index["name"]: index
        for index in inspect(engine).get_indexes("group_memories")
    }
    canonical_index = indexes["uq_group_memory_canonical_hash"]
    assert canonical_index["unique"] == 1
    assert canonical_index["column_names"] == [
        "chat_stream_id",
        "memory_type",
        "content_hash",
    ]


def test_group_memory_canonical_identity_migration_rejects_alias_collision_atomically():
    from core.schema_migrations import (
        SchemaMigrationValidationError,
        run_schema_migrations,
    )

    engine = _legacy_group_memory_engine([
        {
            "id": 1,
            "group_id": "group_sensitive-room",
            "memory_type": "topic",
            "content_hash": "same-hash",
        },
        {
            "id": 2,
            "group_id": "sensitive-room",
            "memory_type": "topic",
            "content_hash": "same-hash",
        },
    ])

    with pytest.raises(
        SchemaMigrationValidationError,
        match="canonical 身份冲突",
    ) as caught:
        run_schema_migrations(engine)

    assert "sensitive-room" not in str(caught.value)
    assert "chat_stream_id" not in {
        column["name"]
        for column in inspect(engine).get_columns("group_memories")
    }
    with engine.connect() as conn:
        version_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM schema_migrations "
                "WHERE version = :version"
            ),
            {"version": _GROUP_MEMORY_CANONICAL_IDENTITY_VERSION},
        ).scalar_one()
        rows = conn.execute(text(
            "SELECT id, group_id FROM group_memories ORDER BY id"
        )).fetchall()
    assert version_count == 0
    assert rows == [
        (1, "group_sensitive-room"),
        (2, "sensitive-room"),
    ]


def test_group_memory_canonical_identity_migration_rejects_existing_projection_conflict():
    from core.schema_migrations import (
        SchemaMigrationValidationError,
        run_schema_migrations,
    )

    engine = _legacy_group_memory_engine(
        [{
            "id": 1,
            "group_id": "group_sensitive-room",
            "memory_type": "topic",
            "content_hash": "hash-1",
            "chat_stream_id": "web:other-room:group",
        }],
        include_canonical_column=True,
    )

    with pytest.raises(
        SchemaMigrationValidationError,
        match="身份投影不一致",
    ) as caught:
        run_schema_migrations(engine)

    assert "sensitive-room" not in str(caught.value)
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT chat_stream_id, group_id FROM group_memories"
        )).one()
    assert row == (
        "web:other-room:group",
        "group_sensitive-room",
    )


def _legacy_sandbox_profile_engine(database_url: str = "sqlite:///:memory:"):
    from core.schema_migrations import (
        MIGRATIONS,
        _SANDBOX_EXECUTION_PROFILES_AND_LEASES_VERSION,
        _SANDBOX_RUNTIME_PROJECT_QUOTAS_VERSION,
        _SANDBOX_WORKSPACE_QUOTA_MAINTENANCE_VERSION,
    )

    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE schema_migrations ("
            "version TEXT PRIMARY KEY, "
            "name TEXT NOT NULL, "
            "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))
        conn.execute(
            text(
                "INSERT INTO schema_migrations(version, name) "
                "VALUES (:version, :name)"
            ),
            [
                {"version": version, "name": name}
                for version, name, _migration in MIGRATIONS
                if version not in {
                    _SANDBOX_EXECUTION_PROFILES_AND_LEASES_VERSION,
                    _SANDBOX_RUNTIME_PROJECT_QUOTAS_VERSION,
                    _SANDBOX_WORKSPACE_QUOTA_MAINTENANCE_VERSION,
                }
            ],
        )
        conn.execute(text(
            "CREATE TABLE workspaces ("
            "id VARCHAR(36) PRIMARY KEY, "
            "quota_bytes INTEGER NOT NULL DEFAULT 1, "
            "used_bytes INTEGER NOT NULL DEFAULT 0)"
        ))
        conn.execute(text(
            "INSERT INTO workspaces(id) VALUES ('workspace-legacy')"
        ))
        conn.execute(text(
            "CREATE TABLE workspace_quota_bindings ("
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
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ")"
        ))
        conn.execute(text(
            "INSERT INTO workspace_quota_bindings("
            "workspace_id, project_id, desired_quota_bytes, "
            "applied_quota_bytes, status, generation"
            ") VALUES ('workspace-legacy', 10000, 4294967296, "
            "4294967296, 'applied', 7)"
        ))
        conn.execute(text(
            "CREATE TABLE sandbox_project_sequences ("
            "name VARCHAR(32) PRIMARY KEY, "
            "next_value INTEGER NOT NULL, "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ")"
        ))
        conn.execute(text(
            "INSERT INTO sandbox_project_sequences(name, next_value) "
            "VALUES ('workspace', 10001)"
        ))
        conn.execute(text(
            "CREATE TABLE sandbox_access_grants ("
            "id VARCHAR(36) PRIMARY KEY, "
            "chat_stream_id VARCHAR(512) NOT NULL UNIQUE, "
            "platform VARCHAR(32) NOT NULL, "
            "chat_type VARCHAR(16) NOT NULL, "
            "external_session_id VARCHAR(255) NOT NULL, "
            "workspace_id VARCHAR(36), "
            "capability_level VARCHAR(16) NOT NULL DEFAULT 'off', "
            "status VARCHAR(16) NOT NULL DEFAULT 'disabled', "
            "version INTEGER NOT NULL DEFAULT 1, "
            "reason TEXT NOT NULL DEFAULT '', "
            "created_by VARCHAR(128) NOT NULL DEFAULT '', "
            "updated_by VARCHAR(128) NOT NULL DEFAULT '', "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "FOREIGN KEY (workspace_id) REFERENCES workspaces(id)"
            ")"
        ))
        conn.execute(text(
            "INSERT INTO sandbox_access_grants("
            "id, chat_stream_id, platform, chat_type, external_session_id, "
            "workspace_id, capability_level, status"
            ") VALUES ("
            "'grant-legacy', 'qq:legacy:private', 'qq', 'private', 'legacy', "
            "'workspace-legacy', 'exec', 'active'"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE sandbox_runs ("
            "run_id VARCHAR(64) PRIMARY KEY, "
            "request_id VARCHAR(64) NOT NULL UNIQUE, "
            "workspace_id VARCHAR(36) NOT NULL, "
            "trace_id VARCHAR(64) NOT NULL DEFAULT '', "
            "agent_run_id VARCHAR(64) NOT NULL DEFAULT '', "
            "tool_call_id VARCHAR(64) NOT NULL DEFAULT '', "
            "image_digest VARCHAR(255) NOT NULL, "
            "status VARCHAR(16) NOT NULL DEFAULT 'pending', "
            "exit_code INTEGER, "
            "termination_reason VARCHAR(64) NOT NULL DEFAULT '', "
            "cpu_time_ms INTEGER NOT NULL DEFAULT 0, "
            "peak_memory_bytes INTEGER NOT NULL DEFAULT 0, "
            "stdout_bytes INTEGER NOT NULL DEFAULT 0, "
            "stderr_bytes INTEGER NOT NULL DEFAULT 0, "
            "stdout_truncated BOOLEAN NOT NULL DEFAULT 0, "
            "stderr_truncated BOOLEAN NOT NULL DEFAULT 0, "
            "started_at DATETIME, "
            "finished_at DATETIME, "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "FOREIGN KEY (workspace_id) REFERENCES workspaces(id)"
            ")"
        ))
        conn.execute(text(
            "INSERT INTO sandbox_runs("
            "run_id, request_id, workspace_id, image_digest, status"
            ") VALUES ("
            "'run-legacy', 'request-legacy', 'workspace-legacy', "
            "'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
            "'completed'"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE sandbox_admin_operations ("
            "operation_id VARCHAR(64) PRIMARY KEY, "
            "request_id VARCHAR(64) NOT NULL UNIQUE, "
            "operation_type VARCHAR(32) NOT NULL, "
            "chat_stream_id VARCHAR(512) NOT NULL DEFAULT '', "
            "workspace_id VARCHAR(36), "
            "desired_capability VARCHAR(16) NOT NULL DEFAULT '', "
            "previous_capability VARCHAR(16) NOT NULL DEFAULT '', "
            "desired_quota_bytes INTEGER NOT NULL DEFAULT 0, "
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
            "FOREIGN KEY (workspace_id) REFERENCES workspaces(id), "
            "CONSTRAINT ck_sandbox_admin_operation_type "
            "CHECK (operation_type IN "
            "('set_access', 'set_quota', 'bind_workspace', 'import_quota')), "
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
            "INSERT INTO sandbox_admin_operations("
            "operation_id, request_id, operation_type, chat_stream_id, "
            "workspace_id, desired_quota_bytes, expected_quota_generation, "
            "status, step, reason, created_by, created_at, updated_at"
            ") VALUES ("
            "'operation-legacy', 'request-operation-legacy', 'set_quota', "
            "'qq:legacy:private', 'workspace-legacy', 4294967296, 7, "
            "'pending', 'queued', '保留旧行', 'tester', "
            "'2026-07-25 10:00:00', '2026-07-25 10:00:01'"
            ")"
        ))
    return engine


def test_sandbox_profile_and_lease_migration_upgrades_current_master_schema():
    from core.schema_migrations import (
        _SANDBOX_EXECUTION_PROFILES_AND_LEASES_VERSION,
        _SANDBOX_RUNTIME_PROJECT_QUOTAS_VERSION,
        run_schema_migrations,
    )

    engine = _legacy_sandbox_profile_engine()
    run_schema_migrations(engine)

    inspector = inspect(engine)
    assert "sandbox_leases" in inspector.get_table_names()
    assert "execution_profile" in {
        column["name"]
        for column in inspector.get_columns("sandbox_access_grants")
    }
    assert {
        "lease_id",
        "profile_id",
        "execution_mode",
        "process_state",
        "last_seen_at",
    } <= {
        column["name"]
        for column in inspector.get_columns("sandbox_runs")
    }
    with engine.begin() as conn:
        assert conn.execute(text(
            "SELECT execution_profile FROM sandbox_access_grants "
            "WHERE id = 'grant-legacy'"
        )).scalar_one() == "restricted"
        assert conn.execute(text(
            "SELECT profile_id, execution_mode, process_state "
            "FROM sandbox_runs WHERE run_id = 'run-legacy'"
        )).one() == ("restricted", "oneshot", "not_applicable")
        assert conn.execute(text(
            "SELECT project_id, desired_quota_bytes, applied_quota_bytes, "
            "status, generation "
            "FROM workspace_runtime_quota_bindings "
            "WHERE workspace_id = 'workspace-legacy'"
        )).one() == (
            10001,
            512 * 1024 * 1024,
            0,
            "pending",
            7,
        )
        assert conn.execute(text(
            "SELECT next_value FROM sandbox_project_sequences "
            "WHERE name = 'workspace'"
        )).scalar_one() == 10002
        assert conn.execute(text(
            "SELECT status, generation, applied_quota_generation, "
            "last_error_code "
            "FROM workspace_maintenance_states "
            "WHERE workspace_id = 'workspace-legacy'"
        )).one() == ("ready", 7, 7, "")
        assert conn.execute(text(
            "SELECT operation_type, desired_quota_bytes, "
            "expected_quota_generation, reason, created_by "
            "FROM sandbox_admin_operations "
            "WHERE operation_id = 'operation-legacy'"
        )).one() == (
            "set_quota",
            4294967296,
            7,
            "保留旧行",
            "tester",
        )
        conn.execute(text(
            "INSERT INTO sandbox_admin_operations("
            "operation_id, request_id, operation_type"
            ") VALUES "
            "('operation-stop', 'request-operation-stop', 'lease_stop'), "
            "('operation-kill', 'request-operation-kill', 'kill_switch')"
        ))
        assert conn.execute(text(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = :version"
        ), {
            "version": _SANDBOX_EXECUTION_PROFILES_AND_LEASES_VERSION,
        }).scalar_one() == 1
        assert conn.execute(text(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = :version"
        ), {
            "version": _SANDBOX_RUNTIME_PROJECT_QUOTAS_VERSION,
        }).scalar_one() == 1


def test_quota_maintenance_backfill_marks_inconsistent_binding_error():
    from core.schema_migrations import run_schema_migrations

    engine = _legacy_sandbox_profile_engine()
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO workspaces(id) VALUES ('workspace-drift')"
        ))
        conn.execute(text(
            "INSERT INTO workspace_quota_bindings("
            "workspace_id, project_id, desired_quota_bytes, "
            "applied_quota_bytes, status, generation"
            ") VALUES ('workspace-drift', 10005, 4294967296, 1024, "
            "'applied', 3)"
        ))

    run_schema_migrations(engine)

    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT status, generation, applied_quota_generation, "
            "last_error_code "
            "FROM workspace_maintenance_states "
            "WHERE workspace_id = 'workspace-legacy'"
        )).one() == ("ready", 7, 7, "")
        assert conn.execute(text(
            "SELECT status, applied_quota_generation, last_error_code "
            "FROM workspace_maintenance_states "
            "WHERE workspace_id = 'workspace-drift'"
        )).one() == ("error", 0, "quota_maintenance_required")


def test_upgraded_lease_partial_unique_index_preserves_terminal_history():
    from core.schema_migrations import run_schema_migrations

    engine = _legacy_sandbox_profile_engine()
    run_schema_migrations(engine)
    lease_values = {
        "grant_id": "grant-legacy",
        "chat_stream_id": "qq:legacy:private",
        "workspace_id": "workspace-legacy",
        "profile_id": "developer",
        "catalog_generation": "20260725.1",
        "policy_sha256": "a" * 64,
    }
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO sandbox_leases("
            "lease_id, lease_key, grant_id, chat_stream_id, workspace_id, "
            "profile_id, catalog_generation, policy_sha256, status"
            ") VALUES ("
            "'lease-current-a', 'same-key', :grant_id, :chat_stream_id, "
            ":workspace_id, :profile_id, :catalog_generation, "
            ":policy_sha256, 'active'"
            ")"
        ), lease_values)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO sandbox_leases("
                "lease_id, lease_key, grant_id, chat_stream_id, workspace_id, "
                "profile_id, catalog_generation, policy_sha256, status"
                ") VALUES ("
                "'lease-current-b', 'same-key', :grant_id, :chat_stream_id, "
                ":workspace_id, :profile_id, :catalog_generation, "
                ":policy_sha256, 'idle'"
                ")"
            ), lease_values)
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE sandbox_leases SET status = 'stopped' "
            "WHERE lease_id = 'lease-current-a'"
        ))
        conn.execute(text(
            "INSERT INTO sandbox_leases("
            "lease_id, lease_key, grant_id, chat_stream_id, workspace_id, "
            "profile_id, catalog_generation, policy_sha256, status"
            ") VALUES ("
            "'lease-current-b', 'same-key', :grant_id, :chat_stream_id, "
            ":workspace_id, :profile_id, :catalog_generation, "
            ":policy_sha256, 'active'"
            ")"
        ), lease_values)
        conn.execute(text(
            "INSERT INTO sandbox_leases("
            "lease_id, lease_key, grant_id, chat_stream_id, workspace_id, "
            "profile_id, catalog_generation, policy_sha256, status"
            ") VALUES ("
            "'lease-history', 'same-key', :grant_id, :chat_stream_id, "
            ":workspace_id, :profile_id, :catalog_generation, "
            ":policy_sha256, 'failed'"
            ")"
        ), lease_values)
        assert conn.execute(text(
            "SELECT COUNT(*) FROM sandbox_leases WHERE lease_key = 'same-key'"
        )).scalar_one() == 3


def test_file_sqlite_sandbox_admin_rebuild_creates_pre_migration_backup(
    tmp_path,
):
    from core.schema_migrations import run_schema_migrations

    db_path = tmp_path / "legacy-sandbox.db"
    engine = _legacy_sandbox_profile_engine(f"sqlite:///{db_path}")
    engine.dispose()
    engine = create_engine(f"sqlite:///{db_path}")

    run_schema_migrations(engine, db_path=str(db_path))

    backups = sorted(tmp_path.glob("legacy-sandbox.db.bak.*"))
    assert len(backups) == 1
    backup_engine = create_engine(f"sqlite:///{backups[0]}")
    try:
        assert "execution_profile" not in {
            column["name"]
            for column in inspect(backup_engine).get_columns(
                "sandbox_access_grants"
            )
        }
        with backup_engine.connect() as conn:
            assert conn.execute(text(
                "SELECT operation_type FROM sandbox_admin_operations "
                "WHERE operation_id = 'operation-legacy'"
            )).scalar_one() == "set_quota"
    finally:
        backup_engine.dispose()


def test_scheduled_task_schedule_columns_backfills_cron_spec():
    from core.schema_migrations import MIGRATIONS, run_schema_migrations

    version = "20260726_scheduled_task_schedule_columns"
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE schema_migrations ("
            "version TEXT PRIMARY KEY, name TEXT NOT NULL, "
            "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))
        conn.execute(
            text(
                "INSERT INTO schema_migrations(version, name) "
                "VALUES (:version, :name)"
            ),
            [
                {"version": migration_version, "name": name}
                for migration_version, name, _migration in MIGRATIONS
                if migration_version != version
            ],
        )
        conn.execute(text(
            "CREATE TABLE scheduled_tasks ("
            "id INTEGER PRIMARY KEY, name TEXT, cron_expr TEXT, "
            "target_type TEXT, target_id TEXT, prompt_template TEXT, "
            "enabled INTEGER)"
        ))
        conn.execute(text(
            "INSERT INTO scheduled_tasks"
            "(id, name, cron_expr, target_type, target_id, "
            "prompt_template, enabled) VALUES "
            "(1, '早报', '0 9 * * *', 'private', 'u1', '生成早报', 1), "
            "(2, '坏表达式', 'not a cron', 'private', 'u1', '生成', 1)"
        ))

    run_schema_migrations(engine)
    # 幂等:重复执行不报错
    run_schema_migrations(engine)

    with engine.connect() as conn:
        columns = {
            str(col["name"])
            for col in inspect(conn).get_columns("scheduled_tasks")
        }
        assert {"schedule_kind", "schedule_spec", "next_fire_at"} <= columns
        indexes = {
            str(index["name"])
            for index in inspect(conn).get_indexes("scheduled_tasks")
        }
        assert "ix_scheduled_tasks_enabled_next_fire_at" in indexes
        good = conn.execute(text(
            "SELECT schedule_kind, schedule_spec, next_fire_at "
            "FROM scheduled_tasks WHERE id = 1"
        )).one()
        assert good[0] == "cron"
        assert json.loads(good[1]) == {
            "kind": "cron",
            "expr": "0 9 * * *",
            "display": "0 9 * * *",
        }
        assert good[2] is None
        bad = conn.execute(text(
            "SELECT schedule_spec FROM scheduled_tasks WHERE id = 2"
        )).one()
        assert bad[0] == ""


def test_chat_log_session_id_index_avoids_group_rollup_temp_sort():
    from core.db.models.chat import ChatLog
    from core.schema_migrations import (
        MIGRATIONS,
        _CHAT_LOG_SESSION_ID_INDEX_VERSION,
        run_schema_migrations,
    )

    model_index = next(
        index
        for index in ChatLog.__table__.indexes
        if index.name == "idx_cl_session_id"
    )
    assert [column.name for column in model_index.columns] == [
        "session_id",
        "id",
    ]

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE schema_migrations ("
            "version TEXT PRIMARY KEY, name TEXT NOT NULL, "
            "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))
        conn.execute(
            text(
                "INSERT INTO schema_migrations(version, name) "
                "VALUES (:version, :name)"
            ),
            [
                {"version": version, "name": name}
                for version, name, _migration in MIGRATIONS
                if version != _CHAT_LOG_SESSION_ID_INDEX_VERSION
            ],
        )
        conn.execute(text(
            "CREATE TABLE chat_logs ("
            "id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
            "content TEXT, meta_json TEXT)"
        ))

    run_schema_migrations(engine)
    run_schema_migrations(engine)

    indexes = {
        index["name"]: index["column_names"]
        for index in inspect(engine).get_indexes("chat_logs")
    }
    assert indexes["idx_cl_session_id"] == ["session_id", "id"]
    with engine.connect() as conn:
        plan = conn.execute(text(
            "EXPLAIN QUERY PLAN "
            "SELECT * FROM chat_logs "
            "WHERE session_id = 'group_1' AND id > 0 "
            "AND role IN ('ambient', 'user', 'assistant') "
            "ORDER BY id DESC"
        )).fetchall()
        applied_count = conn.execute(text(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = :version"
        ), {
            "version": _CHAT_LOG_SESSION_ID_INDEX_VERSION,
        }).scalar_one()

    assert not any(
        "USE TEMP B-TREE FOR ORDER BY" in str(row[-1])
        for row in plan
    )
    assert applied_count == 1

from __future__ import annotations

import sqlite3
import re
from contextlib import closing
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


OUTBOUND_TABLES = (
    "outbound_runs",
    "outbound_generation_attempts",
    "outbound_delivery_outbox",
    "outbound_delivery_attempts",
    "outbound_delivery_circuits",
    "outbound_delivery_controls",
)

FOREIGN_KEY_TABLES = {
    "outbound_generation_attempts",
    "outbound_delivery_outbox",
    "outbound_delivery_attempts",
    "outbound_delivery_circuits",
}

EXPECTED_COLUMNS = {
    "outbound_runs": (
        "id", "source_type", "source_id", "occurrence_key", "source_revision",
        "source_snapshot_json", "source_snapshot_sha256", "delivery_contract_json",
        "delivery_contract_sha256", "writer_owner", "writer_token",
        "writer_protocol_version", "task_kind", "scheduled_for", "trigger_type",
        "status", "claim_owner", "claim_token",
        "claim_expires_at", "claim_generation", "attempt_count", "attempted_at",
        "generated_at", "succeeded_at",
        "failure_type", "failure_summary", "active_outbox_id",
        "has_ambiguous_ancestor", "delivery_mode", "cutover_epoch", "created_at",
        "updated_at",
    ),
    "outbound_generation_attempts": (
        "id", "run_id", "attempt_no", "owner", "fencing_token", "status",
        "started_at", "completed_at", "model_trace_id", "content_sha256",
        "error_type", "error_summary", "created_at",
    ),
    "outbound_delivery_outbox": (
        "id", "run_id", "idempotency_key", "destination_snapshot_json",
        "destination_fingerprint", "target_type", "endpoint_key", "payload_json",
        "payload_sha256", "status", "lease_owner", "lease_token",
        "lease_expires_at", "next_attempt_at", "allocated_attempt_count",
        "request_started_count", "max_attempts", "retry_deadline_at",
        "last_error_type", "last_error_summary", "delivered_at", "cancelled_at",
        "cancel_reason_type", "replay_of_outbox_id", "replay_sequence",
        "replay_request_sha256",
        "cutover_epoch", "endpoint_config_revision", "payload_contract_fingerprint",
        "created_at", "updated_at",
    ),
    "outbound_delivery_attempts": (
        "id", "outbox_id", "attempt_no", "worker_owner", "lease_token", "status",
        "transport_phase", "request_started", "endpoint_config_revision",
        "http_status", "result_category", "error_type", "safe_summary",
        "duration_ms", "settlement_retry_at", "settlement_circuit_scope_type",
        "settlement_request_sha256", "started_at", "request_started_at",
        "completed_at", "created_at",
    ),
    "outbound_delivery_circuits": (
        "id", "scope_type", "scope_fingerprint", "config_revision", "status",
        "reason_type", "opened_at", "opened_by_attempt_id", "created_at",
        "updated_at",
    ),
    "outbound_delivery_controls": (
        "source_type", "mode", "cutover_epoch", "effective_from",
        "protocol_version", "writer_version", "writer_owner", "writer_token",
        "writer_lease_expires_at", "created_at", "updated_at",
    ),
}

EXPECTED_INDEXES = {
    "outbound_runs": {
        "uq_outbound_run_occurrence",
        "ix_outbound_run_source",
        "ix_outbound_run_claim_lease",
    },
    "outbound_generation_attempts": {"uq_outbound_generation_attempt"},
    "outbound_delivery_outbox": {
        "uq_outbound_delivery_idempotency_key",
        "uq_outbound_delivery_replay_leaf",
        "ix_outbound_delivery_due",
        "ix_outbound_delivery_lease",
        "ix_outbound_delivery_run_status",
        "ix_outbound_delivery_replay_parent",
    },
    "outbound_delivery_attempts": {
        "uq_outbound_delivery_attempt",
        "ix_outbound_delivery_attempt_status_started",
    },
    "outbound_delivery_circuits": {
        "uq_outbound_delivery_circuit_scope",
        "ix_outbound_delivery_circuit_status",
    },
    "outbound_delivery_controls": {
        "ix_outbound_delivery_control_mode_effective",
    },
}


def _run_migrations(engine):
    from core.schema_migrations import run_schema_migrations

    run_schema_migrations(engine)


def _contract(table_name):
    from core.outbound_delivery_schema import OUTBOUND_TABLE_CONTRACTS

    return next(item for item in OUTBOUND_TABLE_CONTRACTS if item.name == table_name)


def _create_contract_table(conn, contract, *, create_sql=None, schema_prefix=""):
    conn.execute(text(create_sql or contract.create_sql))
    for index in contract.indexes:
        unique = "UNIQUE " if index.unique else ""
        columns = ", ".join(index.columns)
        qualified_name = f"{schema_prefix}{index.name}"
        conn.execute(text(
            f"CREATE {unique}INDEX {qualified_name} "
            f"ON {contract.name}({columns})"
        ))


def _normalized_default(value):
    if value is None:
        return None
    return str(value).strip().strip("()").replace('"', "'").casefold()


def _table_contract(conn, table_name):
    columns = tuple(
        (
            str(row["name"]),
            str(row["type"]).upper().replace(" ", ""),
            int(row["notnull"]),
            _normalized_default(row["dflt_value"]),
            int(row["pk"]),
        )
        for row in conn.execute(
            text(f"PRAGMA table_xinfo('{table_name}')")
        ).mappings()
    )
    indexes = []
    for row in conn.execute(
        text(f"PRAGMA index_list('{table_name}')")
    ).mappings():
        if str(row["origin"]) == "pk":
            continue
        name = str(row["name"])
        escaped_name = name.replace("'", "''")
        index_columns = tuple(
            str(item["name"])
            for item in conn.execute(
                text(f"PRAGMA index_xinfo('{escaped_name}')")
            ).mappings()
            if int(item["key"]) == 1
        )
        indexes.append(
            (
                name,
                int(row["unique"]),
                int(row["partial"]),
                index_columns,
            )
        )
    table_sql = conn.execute(text(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name"
    ), {"name": table_name}).scalar_one()
    checks = tuple(sorted(re.findall(
        r"\bCONSTRAINT\s+([A-Za-z_][A-Za-z0-9_]*)\s+CHECK\s*\(",
        str(table_sql),
        flags=re.IGNORECASE,
    )))
    foreign_keys = tuple(sorted(
        (
            str(row["from"]),
            str(row["table"]),
            str(row["to"]),
            str(row["on_update"]),
            str(row["on_delete"]),
        )
        for row in conn.execute(
            text(f"PRAGMA foreign_key_list('{table_name}')")
        ).mappings()
    ))
    return columns, tuple(sorted(indexes)), checks, foreign_keys


def _insert_run(conn, *, occurrence_key="slot-1"):
    result = conn.execute(
        text(
            "INSERT INTO outbound_runs ("
            "source_type, source_id, occurrence_key, source_revision, task_kind, "
            "source_snapshot_json, source_snapshot_sha256, delivery_contract_json, "
            "delivery_contract_sha256, writer_owner, writer_token, "
            "writer_protocol_version, scheduled_for, "
            "trigger_type, status, claim_owner, claim_token, "
            "claim_expires_at, delivery_mode, cutover_epoch"
            ") VALUES ("
            "'scheduled_task', 'task-1', :occurrence_key, 'rev-1', 'ai_digest', "
            "'{}', 'source-hash', '{\"target\":\"opaque\"}', "
            "'delivery-contract-hash', 'producer-a', 'writer-a', 2, "
            "CURRENT_TIMESTAMP, 'cron', 'claimed', "
            "'producer-a', 'token-a', "
            "datetime('now', '+5 minutes'), 'outbox', 1"
            ")"
        ),
        {"occurrence_key": occurrence_key},
    )
    return int(result.lastrowid)


def _insert_outbox(
    conn,
    run_id,
    *,
    idempotency_key="delivery-1",
    status="pending",
    replay_sequence=0,
    lease_owner=None,
    lease_token=None,
    lease_expires_at=None,
    next_attempt_at=None,
    delivered_at=None,
    cancelled_at=None,
    cancel_reason_type=None,
    destination_fingerprint=None,
):
    destination_fingerprint = destination_fingerprint or f"destination-{idempotency_key}"
    result = conn.execute(
        text(
            "INSERT INTO outbound_delivery_outbox ("
            "run_id, idempotency_key, destination_snapshot_json, "
            "destination_fingerprint, target_type, endpoint_key, payload_json, "
            "payload_sha256, status, lease_owner, lease_token, lease_expires_at, "
            "next_attempt_at, allocated_attempt_count, request_started_count, "
            "max_attempts, retry_deadline_at, delivered_at, cancelled_at, "
            "cancel_reason_type, replay_sequence, cutover_epoch, "
            "endpoint_config_revision, payload_contract_fingerprint"
            ") VALUES ("
            ":run_id, :idempotency_key, '{\"target\":\"opaque\"}', "
            ":destination_fingerprint, 'private', 'qq_push', "
            "'{\"content\":\"hello\"}', "
            "'payload-hash', :status, :lease_owner, :lease_token, "
            ":lease_expires_at, :next_attempt_at, 0, 0, 3, "
            "datetime('now', '+1 day'), :delivered_at, :cancelled_at, "
            ":cancel_reason_type, :replay_sequence, 1, 'qq-revision-1', "
            "'qq-envelope-v1'"
            ")"
        ),
        {
            "run_id": run_id,
            "idempotency_key": idempotency_key,
            "status": status,
            "lease_owner": lease_owner,
            "lease_token": lease_token,
            "lease_expires_at": lease_expires_at,
            "next_attempt_at": next_attempt_at,
            "delivered_at": delivered_at,
            "cancelled_at": cancelled_at,
            "cancel_reason_type": cancel_reason_type,
            "replay_sequence": replay_sequence,
            "destination_fingerprint": destination_fingerprint,
        },
    )
    return int(result.lastrowid)


def test_outbound_models_declare_all_six_persistent_record_types():
    from core.database import (
        OutboundDeliveryAttempt,
        OutboundDeliveryCircuit,
        OutboundDeliveryControl,
        OutboundDeliveryOutbox,
        OutboundGenerationAttempt,
        OutboundRun,
    )

    assert tuple(
        model.__tablename__
        for model in (
            OutboundRun,
            OutboundGenerationAttempt,
            OutboundDeliveryOutbox,
            OutboundDeliveryAttempt,
            OutboundDeliveryCircuit,
            OutboundDeliveryControl,
        )
    ) == OUTBOUND_TABLES

    from core.database import ProactiveOutreachLog, ScheduledTask

    assert {
        "last_attempt_at",
        "last_success_at",
        "delivery_status",
        "last_run_id",
        "last_error_summary",
    } <= set(ScheduledTask.__table__.columns.keys())
    assert "outbound_run_id" in ProactiveOutreachLog.__table__.columns


def test_outbound_migration_is_registered_once_after_prompt_trace_migration():
    from core.outbound_delivery_schema import OUTBOUND_DELIVERY_SCHEMA_VERSION
    from core.schema_migrations import MIGRATIONS

    versions = [version for version, _name, _function in MIGRATIONS]
    assert versions.count(OUTBOUND_DELIVERY_SCHEMA_VERSION) == 1
    assert versions.index(OUTBOUND_DELIVERY_SCHEMA_VERSION) > versions.index(
        "20260714_prompt_template_resolution_columns"
    )


def test_outbound_migration_matches_orm_columns_indexes_and_checks_exactly():
    from core.outbound_delivery_schema import validate_outbound_delivery_schema
    from core.database import (
        OutboundDeliveryAttempt,
        OutboundDeliveryCircuit,
        OutboundDeliveryControl,
        OutboundDeliveryOutbox,
        OutboundGenerationAttempt,
        OutboundRun,
    )

    models = (
        OutboundRun,
        OutboundGenerationAttempt,
        OutboundDeliveryOutbox,
        OutboundDeliveryAttempt,
        OutboundDeliveryCircuit,
        OutboundDeliveryControl,
    )
    orm_engine = create_engine("sqlite:///:memory:")
    migration_engine = create_engine("sqlite:///:memory:")
    try:
        models[0].metadata.create_all(
            orm_engine,
            tables=[model.__table__ for model in models],
        )
        _run_migrations(migration_engine)
        _run_migrations(migration_engine)
        with orm_engine.connect() as orm_conn, migration_engine.connect() as migrated_conn:
            validate_outbound_delivery_schema(orm_conn)
            validate_outbound_delivery_schema(migrated_conn)
            assert set(OUTBOUND_TABLES) <= set(inspect(migrated_conn).get_table_names())
            for table_name in OUTBOUND_TABLES:
                migrated_contract = _table_contract(migrated_conn, table_name)
                expected_check_names = {
                    name for name, _expression in _contract(table_name).checks
                }
                orm_check_names = {
                    item["name"]
                    for item in inspect(orm_conn).get_check_constraints(table_name)
                }
                migrated_check_names = {
                    item["name"]
                    for item in inspect(migrated_conn).get_check_constraints(table_name)
                }
                assert orm_check_names == expected_check_names
                assert migrated_check_names == expected_check_names
                assert tuple(row[0] for row in migrated_contract[0]) == (
                    EXPECTED_COLUMNS[table_name]
                )
                assert {row[0] for row in migrated_contract[1]} == (
                    EXPECTED_INDEXES[table_name]
                )
                assert migrated_contract[2]
                assert _table_contract(migrated_conn, table_name) == _table_contract(
                    orm_conn,
                    table_name,
                )
                assert bool(migrated_contract[3]) == (
                    table_name in FOREIGN_KEY_TABLES
                )
            assert migrated_conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    finally:
        orm_engine.dispose()
        migration_engine.dispose()


def test_run_occurrence_and_attempt_numbers_are_unique_and_positive():
    engine = create_engine("sqlite:///:memory:")
    _run_migrations(engine)

    with engine.begin() as conn:
        run_id = _insert_run(conn)
        with pytest.raises(IntegrityError):
            _insert_run(conn)

        conn.execute(text(
            "INSERT INTO outbound_generation_attempts ("
            "run_id, attempt_no, owner, fencing_token, status, started_at"
            ") VALUES (:run_id, 1, 'producer-a', 'token-a', 'started', CURRENT_TIMESTAMP)"
        ), {"run_id": run_id})
        with pytest.raises(IntegrityError):
            conn.execute(text(
                "INSERT INTO outbound_generation_attempts ("
                "run_id, attempt_no, owner, fencing_token, status, started_at"
                ") VALUES (:run_id, 1, 'producer-b', 'token-b', 'started', CURRENT_TIMESTAMP)"
            ), {"run_id": run_id})
        with pytest.raises(IntegrityError):
            conn.execute(text(
                "INSERT INTO outbound_generation_attempts ("
                "run_id, attempt_no, owner, fencing_token, status, started_at"
                ") VALUES (:run_id, 0, 'producer-b', 'token-b', 'started', CURRENT_TIMESTAMP)"
            ), {"run_id": run_id})


def test_outbound_foreign_keys_reject_orphan_audit_records():
    engine = create_engine("sqlite:///:memory:")
    _run_migrations(engine)

    with engine.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(text(
            "INSERT INTO outbound_generation_attempts ("
            "run_id, attempt_no, owner, fencing_token, status, started_at"
            ") VALUES (999, 1, 'producer-a', 'token-a', 'started', CURRENT_TIMESTAMP)"
        ))


def test_outbox_enforces_lease_terminal_delivery_and_cancellation_contracts():
    engine = create_engine("sqlite:///:memory:")
    _run_migrations(engine)

    with engine.begin() as conn:
        run_id = _insert_run(conn)

        with pytest.raises(IntegrityError):
            _insert_outbox(
                conn,
                run_id,
                status="leased",
                lease_owner="worker-a",
            )
        with pytest.raises(IntegrityError):
            _insert_outbox(
                conn,
                run_id,
                status="pending",
                lease_owner="worker-a",
                lease_token="lease-a",
                lease_expires_at=datetime.now() + timedelta(minutes=1),
            )
        with pytest.raises(IntegrityError):
            _insert_outbox(
                conn,
                run_id,
                status="failed",
                next_attempt_at=datetime.now(),
            )
        with pytest.raises(IntegrityError):
            _insert_outbox(conn, run_id, status="delivered")
        with pytest.raises(IntegrityError):
            _insert_outbox(conn, run_id, status="cancelled")

        _insert_outbox(
            conn,
            run_id,
            status="leased",
            lease_owner="worker-a",
            lease_token="lease-a",
            lease_expires_at=datetime.now() + timedelta(minutes=1),
        )
        _insert_outbox(
            conn,
            run_id,
            idempotency_key="delivery-2",
            status="delivered",
            delivered_at=datetime.now(),
        )
        _insert_outbox(
            conn,
            run_id,
            idempotency_key="delivery-3",
            status="cancelled",
            cancelled_at=datetime.now(),
            cancel_reason_type="source_disabled",
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE outbound_runs SET source_type = '' WHERE id = :run_id",
        "UPDATE outbound_runs SET source_id = '' WHERE id = :run_id",
        "UPDATE outbound_runs SET occurrence_key = '' WHERE id = :run_id",
        "UPDATE outbound_runs SET source_revision = '' WHERE id = :run_id",
        "UPDATE outbound_runs SET task_kind = '' WHERE id = :run_id",
        "UPDATE outbound_runs SET trigger_type = '' WHERE id = :run_id",
        "UPDATE outbound_runs SET claim_owner = '' WHERE id = :run_id",
        "UPDATE outbound_runs SET claim_token = '' WHERE id = :run_id",
        (
            "INSERT INTO outbound_generation_attempts ("
            "run_id, attempt_no, owner, fencing_token, status, started_at"
            ") VALUES (:run_id, 1, '', 'generation-token', 'started', "
            "CURRENT_TIMESTAMP)"
        ),
        (
            "INSERT INTO outbound_generation_attempts ("
            "run_id, attempt_no, owner, fencing_token, status, started_at"
            ") VALUES (:run_id, 1, 'producer-a', '', 'started', "
            "CURRENT_TIMESTAMP)"
        ),
        (
            "UPDATE outbound_delivery_outbox SET status = 'leased', "
            "lease_owner = '', lease_token = 'delivery-token', "
            "lease_expires_at = CURRENT_TIMESTAMP WHERE id = :outbox_id"
        ),
        (
            "UPDATE outbound_delivery_outbox SET status = 'leased', "
            "lease_owner = 'worker-a', lease_token = '', "
            "lease_expires_at = CURRENT_TIMESTAMP WHERE id = :outbox_id"
        ),
        (
            "UPDATE outbound_delivery_outbox SET idempotency_key = '' "
            "WHERE id = :outbox_id"
        ),
        (
            "UPDATE outbound_delivery_outbox SET target_type = '' "
            "WHERE id = :outbox_id"
        ),
        (
            "UPDATE outbound_delivery_outbox SET endpoint_key = '' "
            "WHERE id = :outbox_id"
        ),
        (
            "UPDATE outbound_delivery_outbox "
            "SET payload_contract_fingerprint = '' "
            "WHERE id = :outbox_id"
        ),
        (
            "INSERT INTO outbound_delivery_attempts ("
            "outbox_id, attempt_no, worker_owner, lease_token, status, "
            "transport_phase, request_started, endpoint_config_revision, started_at"
            ") VALUES (:outbox_id, 1, '', 'delivery-token', 'started', "
            "'allocated', 0, 'qq-revision-1', CURRENT_TIMESTAMP)"
        ),
        (
            "INSERT INTO outbound_delivery_attempts ("
            "outbox_id, attempt_no, worker_owner, lease_token, status, "
            "transport_phase, request_started, endpoint_config_revision, started_at"
            ") VALUES (:outbox_id, 1, 'worker-a', '', 'started', "
            "'allocated', 0, 'qq-revision-1', CURRENT_TIMESTAMP)"
        ),
        (
            "INSERT INTO outbound_delivery_attempts ("
            "outbox_id, attempt_no, worker_owner, lease_token, status, "
            "transport_phase, request_started, endpoint_config_revision, started_at"
            ") VALUES (:outbox_id, 1, 'worker-a', 'delivery-token', 'started', "
            "'allocated', 0, '', CURRENT_TIMESTAMP)"
        ),
        (
            "UPDATE outbound_delivery_controls SET writer_owner = '', "
            "writer_token = 'writer-token', "
            "writer_lease_expires_at = CURRENT_TIMESTAMP "
            "WHERE source_type = 'scheduled_task'"
        ),
        (
            "UPDATE outbound_delivery_controls SET writer_owner = 'writer-a', "
            "writer_token = '', writer_lease_expires_at = CURRENT_TIMESTAMP "
            "WHERE source_type = 'scheduled_task'"
        ),
        (
            "UPDATE outbound_delivery_controls SET source_type = '' "
            "WHERE source_type = 'scheduled_task'"
        ),
        (
            "INSERT INTO outbound_delivery_circuits ("
            "scope_type, scope_fingerprint, config_revision, status"
            ") VALUES ('endpoint', '', 'qq-revision-1', 'closed')"
        ),
        (
            "INSERT INTO outbound_delivery_circuits ("
            "scope_type, scope_fingerprint, config_revision, status"
            ") VALUES ('endpoint', 'qq-endpoint', '', 'closed')"
        ),
    ],
)
def test_fencing_and_config_identity_fields_reject_empty_strings(statement):
    engine = create_engine("sqlite:///:memory:")
    _run_migrations(engine)

    with engine.begin() as conn:
        run_id = _insert_run(conn)
        outbox_id = _insert_outbox(conn, run_id)
        with pytest.raises(IntegrityError):
            conn.execute(
                text(statement),
                {"run_id": run_id, "outbox_id": outbox_id},
            )


def test_retry_wait_requires_an_explicit_next_attempt_time():
    engine = create_engine("sqlite:///:memory:")
    _run_migrations(engine)

    with engine.begin() as conn:
        run_id = _insert_run(conn)
        with pytest.raises(IntegrityError):
            _insert_outbox(
                conn,
                run_id,
                status="retry_wait",
            )
        _insert_outbox(
            conn,
            run_id,
            idempotency_key="delivery-retry",
            status="retry_wait",
            next_attempt_at=datetime.now() + timedelta(minutes=1),
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE outbound_runs SET claim_expires_at = '' WHERE id = :run_id",
        "UPDATE outbound_runs SET claim_expires_at = 'now' WHERE id = :run_id",
        (
            "UPDATE outbound_runs SET status = 'succeeded', claim_owner = NULL, "
            "claim_token = NULL, claim_expires_at = NULL, "
            "succeeded_at = 'not-a-time' WHERE id = :run_id"
        ),
        (
            "INSERT INTO outbound_generation_attempts ("
            "run_id, attempt_no, owner, fencing_token, status, started_at, "
            "completed_at, error_type"
            ") VALUES (:run_id, 1, 'producer-a', 'generation-token', "
            "'failed', CURRENT_TIMESTAMP, '', 'model_error')"
        ),
        (
            "UPDATE outbound_delivery_outbox SET status = 'leased', "
            "lease_owner = 'worker-a', lease_token = 'delivery-token', "
            "lease_expires_at = '' WHERE id = :outbox_id"
        ),
        (
            "UPDATE outbound_delivery_outbox SET status = 'retry_wait', "
            "next_attempt_at = 'not-a-time' WHERE id = :outbox_id"
        ),
        (
            "UPDATE outbound_delivery_outbox SET status = 'retry_wait', "
            "next_attempt_at = '0' WHERE id = :outbox_id"
        ),
        (
            "UPDATE outbound_delivery_outbox SET retry_deadline_at = '' "
            "WHERE id = :outbox_id"
        ),
        (
            "UPDATE outbound_delivery_outbox SET status = 'delivered', "
            "delivered_at = '' WHERE id = :outbox_id"
        ),
        (
            "UPDATE outbound_delivery_outbox SET status = 'cancelled', "
            "cancelled_at = '', cancel_reason_type = 'source_disabled' "
            "WHERE id = :outbox_id"
        ),
        (
            "INSERT INTO outbound_delivery_attempts ("
            "outbox_id, attempt_no, worker_owner, lease_token, status, "
            "transport_phase, request_started, endpoint_config_revision, "
            "started_at, completed_at"
            ") VALUES (:outbox_id, 1, 'worker-a', 'delivery-token', "
            "'transient_failure', 'allocated', 0, 'qq-revision-1', "
            "CURRENT_TIMESTAMP, '')"
        ),
        (
            "INSERT INTO outbound_delivery_circuits ("
            "scope_type, scope_fingerprint, config_revision, status, "
            "reason_type, opened_at"
            ") VALUES ('endpoint', 'qq-endpoint', 'qq-revision-1', "
            "'open', 'unauthorized', '')"
        ),
        (
            "UPDATE outbound_delivery_controls SET writer_owner = 'writer-a', "
            "writer_token = 'writer-token', writer_lease_expires_at = '' "
            "WHERE source_type = 'scheduled_task'"
        ),
        (
            "UPDATE outbound_delivery_controls SET effective_from = '' "
            "WHERE source_type = 'scheduled_task'"
        ),
        (
            "UPDATE outbound_delivery_controls "
            "SET effective_from = '2026-02-30 00:00:00' "
            "WHERE source_type = 'scheduled_task'"
        ),
        (
            "UPDATE outbound_delivery_controls "
            "SET effective_from = '0000-01-01 00:00:00' "
            "WHERE source_type = 'scheduled_task'"
        ),
        (
            "UPDATE outbound_delivery_controls "
            "SET effective_from = '2026-00-14 23:59:59' "
            "WHERE source_type = 'scheduled_task'"
        ),
        (
            "UPDATE outbound_delivery_controls "
            "SET effective_from = '2026-13-14 23:59:59' "
            "WHERE source_type = 'scheduled_task'"
        ),
        (
            "UPDATE outbound_delivery_controls "
            "SET effective_from = '2026-07-00 23:59:59' "
            "WHERE source_type = 'scheduled_task'"
        ),
        (
            "UPDATE outbound_delivery_controls "
            "SET effective_from = '2026-07-14 25:00:00' "
            "WHERE source_type = 'scheduled_task'"
        ),
        (
            "UPDATE outbound_delivery_controls "
            "SET effective_from = '2026-07-14 23:60:00' "
            "WHERE source_type = 'scheduled_task'"
        ),
        (
            "UPDATE outbound_delivery_controls "
            "SET effective_from = '2026-07-14 23:59:60' "
            "WHERE source_type = 'scheduled_task'"
        ),
        (
            "UPDATE outbound_delivery_controls "
            "SET effective_from = '2026-07-14 99:99:99' "
            "WHERE source_type = 'scheduled_task'"
        ),
        (
            "UPDATE outbound_delivery_controls SET effective_from = "
            "'2026-07-14 23:59:59' || char(0) || 'hidden' "
            "WHERE source_type = 'scheduled_task'"
        ),
        (
            "UPDATE outbound_delivery_controls SET effective_from = "
            "'2026-07-14 23:59:59.123456' || char(0) || 'hidden' "
            "WHERE source_type = 'scheduled_task'"
        ),
    ],
)
def test_outbound_datetime_facts_reject_unparseable_text(statement):
    engine = create_engine("sqlite:///:memory:")
    _run_migrations(engine)

    with engine.begin() as conn:
        run_id = _insert_run(conn)
        outbox_id = _insert_outbox(conn, run_id)
        with pytest.raises(IntegrityError):
            conn.execute(
                text(statement),
                {"run_id": run_id, "outbox_id": outbox_id},
            )


def test_outbound_datetime_contract_accepts_python_supported_bounds():
    from sqlalchemy.orm import Session

    from core.database import OutboundDeliveryControl

    engine = create_engine("sqlite:///:memory:")
    _run_migrations(engine)

    for boundary in (
        datetime.min,
        datetime(2026, 7, 14, 23, 59, 59, 999500),
        datetime(2026, 7, 14, 23, 59, 59, 999999),
        datetime.max,
    ):
        with Session(engine) as session:
            control = session.get(OutboundDeliveryControl, "scheduled_task")
            assert control is not None
            control.effective_from = boundary
            session.commit()
            session.expire_all()
            reloaded = session.get(OutboundDeliveryControl, "scheduled_task")
            assert reloaded is not None
            assert reloaded.effective_from == boundary


@pytest.mark.parametrize("schema_path", ["orm", "migration"])
def test_scheduled_task_error_summary_is_bounded_in_every_schema_path(schema_path):
    from core.chat_delivery_outbox_schema import _tokenize_sql
    from core.database import ScheduledTask
    from core.outbound_delivery_schema import SCHEDULED_TASK_ERROR_SUMMARY_CHECK

    engine = create_engine("sqlite:///:memory:")
    if schema_path == "orm":
        ScheduledTask.__table__.create(engine)
    else:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE scheduled_tasks ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR, "
                "last_run_at DATETIME)"
            ))
        _run_migrations(engine)

    if schema_path == "orm":
        check_names = {
            item["name"]
            for item in inspect(engine).get_check_constraints("scheduled_tasks")
        }
        assert SCHEDULED_TASK_ERROR_SUMMARY_CHECK[0] in check_names
    else:
        with engine.connect() as conn:
            table_sql = conn.execute(text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'scheduled_tasks'"
            )).scalar_one()
        tokens = _tokenize_sql(table_sql)
        expected = _tokenize_sql(
            "CONSTRAINT "
            + SCHEDULED_TASK_ERROR_SUMMARY_CHECK[0]
            + " CHECK ("
            + SCHEDULED_TASK_ERROR_SUMMARY_CHECK[1]
            + ")"
        )
        assert any(
            tokens[index:index + len(expected)] == expected
            for index in range(len(tokens) - len(expected) + 1)
        )

    with engine.begin() as conn:
        task_id = conn.execute(
            text(
                "INSERT INTO scheduled_tasks (name, last_error_summary) "
                "VALUES ('bounded-summary', :summary)"
            ),
            {"summary": "x" * 1000},
        ).lastrowid
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "UPDATE scheduled_tasks SET last_error_summary = :summary "
                    "WHERE id = :task_id"
                ),
                {"summary": "x" * 1001, "task_id": task_id},
            )


def test_delivery_attempt_separates_allocated_sequence_from_request_budget():
    engine = create_engine("sqlite:///:memory:")
    _run_migrations(engine)

    with engine.begin() as conn:
        run_id = _insert_run(conn)
        outbox_id = _insert_outbox(conn, run_id)
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "UPDATE outbound_delivery_outbox "
                    "SET request_started_count = 1 WHERE id = :outbox_id"
                ),
                {"outbox_id": outbox_id},
            )


@pytest.mark.parametrize(
    ("status", "request_started", "transport_phase", "http_status"),
    [
        ("transient_failure", 0, "allocated", None),
        ("abandoned_before_send", 0, "allocated", None),
        ("cancelled_before_send", 0, "allocated", None),
        ("transient_failure", 1, "response_received", 503),
        ("permanent_failure", 1, "response_received", 400),
        ("ambiguous", 1, "read", None),
        ("ambiguous", 1, "read", 200),
        ("transient_failure", 1, "read", 503),
        ("succeeded", 1, "response_received", 204),
        ("succeeded", 1, "settled", 200),
    ],
)
def test_delivery_attempt_accepts_consistent_transport_facts(
    status,
    request_started,
    transport_phase,
    http_status,
):
    engine = create_engine("sqlite:///:memory:")
    _run_migrations(engine)

    with engine.begin() as conn:
        run_id = _insert_run(conn)
        outbox_id = _insert_outbox(conn, run_id)
        conn.execute(
            text(
                "INSERT INTO outbound_delivery_attempts ("
                "outbox_id, attempt_no, worker_owner, lease_token, status, "
                "transport_phase, request_started, endpoint_config_revision, "
                "http_status, settlement_request_sha256, started_at, "
                "request_started_at, completed_at"
                ") VALUES ("
                ":outbox_id, 1, 'worker-a', 'lease-a', :status, "
                ":transport_phase, :request_started, 'qq-revision-1', "
                ":http_status, :settlement_request_sha256, CURRENT_TIMESTAMP, "
                ":request_started_at, CURRENT_TIMESTAMP"
                ")"
            ),
            {
                "outbox_id": outbox_id,
                "status": status,
                "transport_phase": transport_phase,
                "request_started": request_started,
                "http_status": http_status,
                "settlement_request_sha256": (
                    ""
                    if status in {
                        "abandoned_before_send",
                        "cancelled_before_send",
                    }
                    else "a" * 64
                ),
                "request_started_at": (
                    datetime.now() if request_started else None
                ),
            },
        )


@pytest.mark.parametrize("invalid_pointer", [0, -1])
def test_run_active_outbox_pointer_must_be_positive(invalid_pointer):
    engine = create_engine("sqlite:///:memory:")
    _run_migrations(engine)

    with engine.begin() as conn:
        run_id = _insert_run(conn)
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "UPDATE outbound_runs SET active_outbox_id = :pointer "
                    "WHERE id = :run_id"
                ),
                {"pointer": invalid_pointer, "run_id": run_id},
            )


@pytest.mark.parametrize(
    ("status", "request_started", "transport_phase", "http_status"),
    [
        ("succeeded", 0, "allocated", 200),
        ("ambiguous", 0, "allocated", None),
        ("abandoned_before_send", 0, "allocated", 200),
        ("cancelled_before_send", 0, "allocated", 204),
        ("succeeded", 1, "response_received", None),
        ("succeeded", 1, "response_received", 500),
        ("succeeded", 1, "request_started", 200),
        ("succeeded", 1, "read", 200),
    ],
    ids=[
        "success-before-request",
        "ambiguous-before-request",
        "abandoned-with-http",
        "cancelled-with-http",
        "success-without-http",
        "success-with-non-2xx",
        "http-before-response",
        "success-before-response-complete",
    ],
)
def test_delivery_attempt_rejects_impossible_transport_facts(
    status,
    request_started,
    transport_phase,
    http_status,
):
    engine = create_engine("sqlite:///:memory:")
    _run_migrations(engine)

    with engine.begin() as conn:
        run_id = _insert_run(conn)
        outbox_id = _insert_outbox(conn, run_id)
        request_started_at = datetime.now() if request_started else None
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO outbound_delivery_attempts ("
                    "outbox_id, attempt_no, worker_owner, lease_token, status, "
                    "transport_phase, request_started, endpoint_config_revision, "
                    "http_status, settlement_request_sha256, started_at, "
                    "request_started_at, completed_at"
                    ") VALUES ("
                    ":outbox_id, 1, 'worker-a', 'lease-a', :status, "
                    ":transport_phase, :request_started, 'qq-revision-1', "
                    ":http_status, :settlement_request_sha256, CURRENT_TIMESTAMP, "
                    ":request_started_at, CURRENT_TIMESTAMP"
                    ")"
                ),
                {
                    "outbox_id": outbox_id,
                    "status": status,
                    "transport_phase": transport_phase,
                    "request_started": request_started,
                    "http_status": http_status,
                    "settlement_request_sha256": (
                        ""
                        if status in {
                            "abandoned_before_send",
                            "cancelled_before_send",
                        }
                        else "a" * 64
                    ),
                    "request_started_at": request_started_at,
                },
            )
        conn.execute(
            text(
                "INSERT INTO outbound_delivery_attempts ("
                "outbox_id, attempt_no, worker_owner, lease_token, status, "
                "transport_phase, request_started, endpoint_config_revision, started_at"
                ") VALUES ("
                ":outbox_id, 1, 'worker-a', 'lease-a', 'started', "
                "'allocated', 0, 'qq-revision-1', CURRENT_TIMESTAMP"
                ")"
            ),
            {"outbox_id": outbox_id},
        )
        row = conn.execute(text(
            "SELECT allocated_attempt_count, request_started_count "
            "FROM outbound_delivery_outbox WHERE id = :outbox_id"
        ), {"outbox_id": outbox_id}).one()
        assert tuple(row) == (0, 0)

        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO outbound_delivery_attempts ("
                    "outbox_id, attempt_no, worker_owner, lease_token, status, "
                    "transport_phase, request_started, endpoint_config_revision, started_at"
                    ") VALUES ("
                    ":outbox_id, 0, 'worker-b', 'lease-b', 'started', "
                    "'allocated', 0, 'qq-revision-1', CURRENT_TIMESTAMP"
                    ")"
                ),
                {"outbox_id": outbox_id},
            )


@pytest.mark.parametrize(
    ("statement", "parameters"),
    [
        (
            "UPDATE outbound_runs SET cutover_epoch = 0.5 WHERE id = :run_id",
            {"target": "run"},
        ),
        (
            "UPDATE outbound_runs SET has_ambiguous_ancestor = 0.5 "
            "WHERE id = :run_id",
            {"target": "run"},
        ),
        (
            "INSERT INTO outbound_generation_attempts ("
            "run_id, attempt_no, owner, fencing_token, status, started_at"
            ") VALUES (:run_id, 1.5, 'producer-a', 'token-a', 'started', "
            "CURRENT_TIMESTAMP)",
            {"target": "run"},
        ),
        (
            "UPDATE outbound_delivery_outbox SET allocated_attempt_count = 0.5 "
            "WHERE id = :outbox_id",
            {"target": "outbox"},
        ),
        (
            "UPDATE outbound_delivery_outbox SET allocated_attempt_count = 1, "
            "request_started_count = 0.5 "
            "WHERE id = :outbox_id",
            {"target": "outbox"},
        ),
        (
            "UPDATE outbound_delivery_outbox SET max_attempts = 1.5 "
            "WHERE id = :outbox_id",
            {"target": "outbox"},
        ),
        (
            "UPDATE outbound_delivery_outbox SET replay_sequence = 0.5, "
            "replay_of_outbox_id = id "
            "WHERE id = :outbox_id",
            {"target": "outbox"},
        ),
        (
            "UPDATE outbound_delivery_outbox SET cutover_epoch = 0.5 "
            "WHERE id = :outbox_id",
            {"target": "outbox"},
        ),
        (
            "INSERT INTO outbound_delivery_attempts ("
            "outbox_id, attempt_no, worker_owner, lease_token, status, "
            "transport_phase, request_started, endpoint_config_revision, "
            "started_at"
            ") VALUES (:outbox_id, 1.5, 'worker-a', 'lease-a', 'started', "
            "'allocated', 0, 'qq-revision-1', CURRENT_TIMESTAMP)",
            {"target": "outbox"},
        ),
        (
            "INSERT INTO outbound_delivery_attempts ("
            "outbox_id, attempt_no, worker_owner, lease_token, status, "
            "transport_phase, request_started, endpoint_config_revision, "
            "started_at, request_started_at"
            ") VALUES (:outbox_id, 1, 'worker-a', 'lease-a', 'started', "
            "'request_started', 0.5, 'qq-revision-1', CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP)",
            {"target": "outbox"},
        ),
        (
            "INSERT INTO outbound_delivery_attempts ("
            "outbox_id, attempt_no, worker_owner, lease_token, status, "
            "transport_phase, request_started, endpoint_config_revision, "
            "http_status, started_at, request_started_at"
            ") VALUES (:outbox_id, 1, 'worker-a', 'lease-a', 'started', "
            "'response_received', 1, 'qq-revision-1', 200.5, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            {"target": "outbox"},
        ),
        (
            "INSERT INTO outbound_delivery_attempts ("
            "outbox_id, attempt_no, worker_owner, lease_token, status, "
            "transport_phase, request_started, endpoint_config_revision, "
            "duration_ms, started_at"
            ") VALUES (:outbox_id, 1, 'worker-a', 'lease-a', 'started', "
            "'allocated', 0, 'qq-revision-1', 1.5, CURRENT_TIMESTAMP)",
            {"target": "outbox"},
        ),
        (
            "UPDATE outbound_delivery_controls SET cutover_epoch = 0.5 "
            "WHERE source_type = 'scheduled_task'",
            {"target": "control"},
        ),
        (
            "UPDATE outbound_delivery_controls SET protocol_version = 1.5 "
            "WHERE source_type = 'scheduled_task'",
            {"target": "control"},
        ),
        (
            "UPDATE outbound_delivery_controls SET writer_version = 0.5 "
            "WHERE source_type = 'scheduled_task'",
            {"target": "control"},
        ),
    ],
    ids=[
        "run-epoch",
        "run-ambiguous-flag",
        "generation-attempt-no",
        "allocated-count",
        "request-started-count",
        "max-attempts",
        "replay-sequence",
        "outbox-epoch",
        "delivery-attempt-no",
        "request-started-flag",
        "http-status",
        "duration-ms",
        "control-epoch",
        "protocol-version",
        "writer-version",
    ],
)
def test_state_machine_integer_facts_reject_real_values(statement, parameters):
    engine = create_engine("sqlite:///:memory:")
    _run_migrations(engine)

    with engine.begin() as conn:
        run_id = _insert_run(conn)
        outbox_id = _insert_outbox(conn, run_id)
        bound = {"run_id": run_id, "outbox_id": outbox_id}
        assert parameters["target"] in {"run", "outbox", "control"}
        with pytest.raises(IntegrityError):
            conn.execute(text(statement), bound)


def test_circuit_scope_revision_and_control_source_are_unique():
    engine = create_engine("sqlite:///:memory:")
    _run_migrations(engine)

    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO outbound_delivery_circuits ("
            "scope_type, scope_fingerprint, config_revision, status"
            ") VALUES ('endpoint', 'qq-push', 'rev-1', 'closed')"
        ))
        with pytest.raises(IntegrityError):
            conn.execute(text(
                "INSERT INTO outbound_delivery_circuits ("
                "scope_type, scope_fingerprint, config_revision, status"
                ") VALUES ('endpoint', 'qq-push', 'rev-1', 'open')"
            ))

        effective_from = datetime.now() + timedelta(hours=1)
        control = conn.execute(text(
            "SELECT mode, cutover_epoch FROM outbound_delivery_controls "
            "WHERE source_type = 'scheduled_task'"
        )).one()
        assert tuple(control) == ("legacy_direct", 0)
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO outbound_delivery_controls ("
                    "source_type, mode, cutover_epoch, effective_from, "
                    "protocol_version, writer_version"
                    ") VALUES ("
                    "'scheduled_task', 'outbox_hold', 2, :effective_from, 1, 2"
                    ")"
                ),
                {"effective_from": effective_from},
            )
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "UPDATE outbound_delivery_controls SET "
                    "writer_owner = 'server-a', writer_token = NULL, "
                    "writer_lease_expires_at = :expires "
                    "WHERE source_type = 'scheduled_task'"
                ),
                {"expires": datetime.now() + timedelta(minutes=5)},
            )


def test_legacy_source_projection_backfill_never_invents_delivery_success():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE scheduled_tasks ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR, cron_expr VARCHAR, "
            "target_type VARCHAR DEFAULT 'private', target_id VARCHAR, "
            "prompt_template TEXT, enabled INTEGER DEFAULT 1, "
            "last_run_at DATETIME, created_at DATETIME)"
        ))
        conn.execute(text(
            "INSERT INTO scheduled_tasks ("
            "name, cron_expr, target_id, prompt_template, last_run_at"
            ") VALUES ("
            "'legacy', '0 8 * * *', 'opaque-target', 'legacy prompt', "
            "'2026-07-13 08:00:00'"
            ")"
        ))
        conn.execute(text(
            "INSERT INTO scheduled_tasks ("
            "name, cron_expr, target_id, prompt_template, last_run_at"
            ") VALUES ("
            "'legacy-never-run', '0 9 * * *', 'opaque-target-2', "
            "'legacy prompt 2', NULL"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE proactive_outreach_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id VARCHAR, "
            "idempotency_key VARCHAR UNIQUE, grounding_json TEXT DEFAULT '{}', "
            "judge_should BOOLEAN DEFAULT 0, judge_reason TEXT DEFAULT '', "
            "next_check_at DATETIME, next_intent TEXT DEFAULT '', "
            "message TEXT DEFAULT '', status VARCHAR DEFAULT 'pending', "
            "forced BOOLEAN DEFAULT 0, created_at DATETIME, "
            "outbound_run_id INTEGER)"
        ))
        conn.execute(text(
            "INSERT INTO proactive_outreach_log ("
            "user_id, idempotency_key, status"
            ") VALUES ('opaque-user-a', 'legacy-a', 'sending'), "
            "('opaque-user-b', 'legacy-b', 'ambiguous'), "
            "('opaque-user-c', 'legacy-c', 'sent'), "
            "('opaque-user-d', 'legacy-d', 'pending')"
        ))
        conn.execute(text(
            "INSERT INTO proactive_outreach_log ("
            "user_id, idempotency_key, status, outbound_run_id"
            ") VALUES ('opaque-user-e', 'linked-e', 'sending', 101), "
            "('opaque-user-f', 'linked-f', 'ambiguous', 102)"
        ))

    _run_migrations(engine)
    _run_migrations(engine)

    with engine.connect() as conn:
        task = conn.execute(text(
            "SELECT last_run_at, last_attempt_at, last_success_at, "
            "delivery_status, last_run_id, last_error_summary "
            "FROM scheduled_tasks WHERE name = 'legacy'"
        )).mappings().one()
        assert task["last_attempt_at"] == task["last_run_at"]
        assert task["last_success_at"] is None
        assert task["delivery_status"] == "legacy_unknown"
        assert task["last_run_id"] is None
        assert task["last_error_summary"] == ""

        never_run_task = conn.execute(text(
            "SELECT last_attempt_at, last_success_at, delivery_status "
            "FROM scheduled_tasks WHERE name = 'legacy-never-run'"
        )).mappings().one()
        assert never_run_task == {
            "last_attempt_at": None,
            "last_success_at": None,
            "delivery_status": "legacy_unknown",
        }

        outreach = conn.execute(text(
            "SELECT status, outbound_run_id FROM proactive_outreach_log ORDER BY id"
        )).fetchall()
        assert outreach == [
            ("legacy_ambiguous_hold", None),
            ("legacy_ambiguous_hold", None),
            ("sent", None),
            ("pending", None),
            ("legacy_ambiguous_hold", None),
            ("legacy_ambiguous_hold", None),
        ]
        assert conn.execute(text(
            "SELECT COUNT(*) FROM outbound_runs"
        )).scalar_one() == 0
        assert conn.execute(text(
            "SELECT COUNT(*) FROM outbound_delivery_outbox"
        )).scalar_one() == 0
        controls = conn.execute(text(
            "SELECT source_type, mode, cutover_epoch, protocol_version, "
            "writer_version, writer_owner, writer_token, writer_lease_expires_at "
            "FROM outbound_delivery_controls ORDER BY source_type"
        )).fetchall()
        assert controls == [
            ("proactive_outreach", "legacy_direct", 0, 1, 0, None, None, None),
            ("scheduled_task", "legacy_direct", 0, 1, 0, None, None, None),
        ]


@pytest.mark.parametrize("status", ["sending", "ambiguous"])
def test_projection_backfill_preserves_only_valid_proactive_run_link(status):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE proactive_outreach_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id VARCHAR, "
            "idempotency_key VARCHAR UNIQUE, grounding_json TEXT DEFAULT '{}', "
            "judge_should BOOLEAN DEFAULT 0, judge_reason TEXT DEFAULT '', "
            "next_check_at DATETIME, next_intent TEXT DEFAULT '', "
            "message TEXT DEFAULT '', status VARCHAR DEFAULT 'pending', "
            "forced BOOLEAN DEFAULT 0, created_at DATETIME, "
            "outbound_run_id INTEGER)"
        ))
        _create_contract_table(conn, _contract("outbound_runs"))
        conn.execute(text(
            "INSERT INTO outbound_runs ("
            "id, source_type, source_id, occurrence_key, source_revision, "
            "source_snapshot_json, source_snapshot_sha256, "
            "delivery_contract_json, delivery_contract_sha256, writer_owner, "
            "writer_token, writer_protocol_version, task_kind, trigger_type, "
            "status, failure_type, failure_summary, has_ambiguous_ancestor, "
            "delivery_mode, cutover_epoch, created_at, updated_at"
            ") VALUES ("
            "1, 'proactive_outreach', '1', 'valid-occurrence', 'revision-1', "
            "'{}', :snapshot_sha, '{}', :contract_sha, 'migration-test', "
            ":writer_token, 2, 'proactive_outreach', 'legacy_migration', "
            "'blocked', '', '', 0, 'legacy_direct', 0, :now, :now)"
        ), {
            "snapshot_sha": "a" * 64,
            "contract_sha": "b" * 64,
            "writer_token": "c" * 64,
            "now": datetime(2026, 7, 15, 12, 0, 0),
        })
        conn.execute(text(
            "INSERT INTO proactive_outreach_log ("
            "id, user_id, idempotency_key, status, outbound_run_id"
            ") VALUES (1, 'opaque-user', 'valid-linked', :status, 1)"
        ), {"status": status})

    _run_migrations(engine)
    _run_migrations(engine)

    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT status, outbound_run_id "
            "FROM proactive_outreach_log WHERE id = 1"
        )).one()
        assert tuple(row) == (status, 1)


def test_projection_backfill_does_not_overwrite_already_linked_success():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE scheduled_tasks ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR, cron_expr VARCHAR, "
            "target_type VARCHAR DEFAULT 'private', target_id VARCHAR, "
            "prompt_template TEXT, enabled INTEGER DEFAULT 1, "
            "last_run_at DATETIME, created_at DATETIME, "
            "last_attempt_at DATETIME, last_success_at DATETIME, "
            "delivery_status VARCHAR(48) NOT NULL DEFAULT 'legacy_unknown', "
            "last_run_id INTEGER, "
            "last_error_summary TEXT NOT NULL DEFAULT '' "
            "CONSTRAINT ck_scheduled_tasks_last_error_summary_length "
            "CHECK (length(last_error_summary) <= 1000))"
        ))
        conn.execute(text(
            "INSERT INTO scheduled_tasks ("
            "name, last_run_at, last_attempt_at, last_success_at, delivery_status, "
            "last_run_id, last_error_summary"
            ") VALUES ("
            "'already-linked', '2026-07-13 08:00:00', '2026-07-13 08:00:00', "
            "'2026-07-13 08:00:05', 'succeeded', 42, ''"
            ")"
        ))

    _run_migrations(engine)

    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT last_success_at, delivery_status, last_run_id "
            "FROM scheduled_tasks WHERE name = 'already-linked'"
        )).one()
    assert tuple(row) == ("2026-07-13 08:00:05", "succeeded", 42)


@pytest.mark.parametrize(
    "projection_clause",
    [
        "last_success_at DATETIME CHECK(last_success_at IS NULL)",
        (
            "delivery_status VARCHAR(48) NOT NULL ON CONFLICT REPLACE "
            "DEFAULT 'legacy_unknown'"
        ),
        "CONSTRAINT ck_legacy_success_null CHECK(last_success_at IS NULL)",
    ],
    ids=["inline-check", "on-conflict", "table-check"],
)
def test_projection_column_or_constraint_drift_fails_closed(projection_clause):
    from core.schema_migrations import SchemaMigrationValidationError

    projection_columns = [
        "last_attempt_at DATETIME",
        "last_success_at DATETIME",
        "delivery_status VARCHAR(48) NOT NULL DEFAULT 'legacy_unknown'",
        "last_run_id INTEGER",
        (
            "last_error_summary TEXT NOT NULL DEFAULT '' "
            "CONSTRAINT ck_scheduled_tasks_last_error_summary_length "
            "CHECK (length(last_error_summary) <= 1000)"
        ),
    ]
    if projection_clause.startswith("last_success_at"):
        projection_columns[1] = projection_clause
    elif projection_clause.startswith("delivery_status"):
        projection_columns[2] = projection_clause
    else:
        projection_columns.append(projection_clause)

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE scheduled_tasks ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, last_run_at DATETIME, "
            + ", ".join(projection_columns)
            + ")"
        ))

    with pytest.raises(
        SchemaMigrationValidationError,
        match="scheduled_tasks.*投影",
    ):
        _run_migrations(engine)


@pytest.mark.parametrize(
    "source_name",
    ["scheduled_tasks", "proactive_outreach_log"],
)
def test_protected_source_view_fails_before_migration_version(source_name):
    from core.outbound_delivery_schema import outbound_delivery_schema_needs_backup
    from core.schema_migrations import SchemaMigrationValidationError

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            f"CREATE VIEW {source_name} AS SELECT 1 AS id"
        ))
        with pytest.raises(SchemaMigrationValidationError, match=source_name):
            outbound_delivery_schema_needs_backup(conn)

    with pytest.raises(SchemaMigrationValidationError, match=source_name):
        _run_migrations(engine)

    with engine.connect() as conn:
        version_table = conn.execute(text(
            "SELECT COUNT(*) FROM main.sqlite_master "
            "WHERE type = 'table' AND name = 'schema_migrations'"
        )).scalar_one()
        if version_table:
            version_count = conn.execute(text(
                "SELECT COUNT(*) FROM schema_migrations "
                "WHERE version = '20260714_outbound_delivery_schema'"
            )).scalar_one()
            assert version_count == 0


@pytest.mark.parametrize(
    ("table_ddl", "insert_sql", "trigger_sql", "expected_name"),
    [
        (
            "CREATE TABLE scheduled_tasks ("
            "id INTEGER PRIMARY KEY, last_run_at DATETIME)",
            "INSERT INTO scheduled_tasks VALUES (1, '2026-07-14 08:00:00')",
            "CREATE TRIGGER swallow_scheduled BEFORE UPDATE ON scheduled_tasks "
            "BEGIN SELECT RAISE(IGNORE); END",
            "scheduled_tasks",
        ),
        (
            "CREATE TABLE proactive_outreach_log ("
            "id INTEGER PRIMARY KEY, user_id VARCHAR, "
            "idempotency_key VARCHAR UNIQUE, grounding_json TEXT DEFAULT '{}', "
            "judge_should BOOLEAN DEFAULT 0, judge_reason TEXT DEFAULT '', "
            "next_check_at DATETIME, next_intent TEXT DEFAULT '', "
            "message TEXT DEFAULT '', status VARCHAR DEFAULT 'pending', "
            "forced BOOLEAN DEFAULT 0, created_at DATETIME)",
            "INSERT INTO proactive_outreach_log "
            "(id, user_id, idempotency_key, status) "
            "VALUES (1, 'opaque-user', 'trigger-case', 'sending')",
            "CREATE TRIGGER swallow_proactive BEFORE UPDATE ON proactive_outreach_log "
            "BEGIN SELECT RAISE(IGNORE); END",
            "proactive_outreach_log",
        ),
    ],
    ids=["scheduled", "proactive"],
)
def test_source_trigger_cannot_swallow_projection_backfill(
    table_ddl,
    insert_sql,
    trigger_sql,
    expected_name,
):
    from core.schema_migrations import SchemaMigrationValidationError

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(table_ddl))
        conn.execute(text(insert_sql))
        conn.execute(text(trigger_sql))

    with pytest.raises(SchemaMigrationValidationError, match=expected_name):
        _run_migrations(engine)

    with engine.connect() as conn:
        version_table = conn.execute(text(
            "SELECT COUNT(*) FROM main.sqlite_master "
            "WHERE type = 'table' AND name = 'schema_migrations'"
        )).scalar_one()
        if version_table:
            version_count = conn.execute(text(
                "SELECT COUNT(*) FROM schema_migrations "
                "WHERE version = '20260714_outbound_delivery_schema'"
            )).scalar_one()
            assert version_count == 0


@pytest.mark.parametrize(
    ("table_suffix", "index_sql"),
    [
        (
            ", CONSTRAINT last_run_id CHECK(length(name) >= 0)",
            None,
        ),
        (
            ", \"laſt_success_at\" DATETIME "
            "CHECK(\"laſt_success_at\" IS NULL)",
            None,
        ),
        (
            "",
            "CREATE INDEX last_run_id ON scheduled_tasks(name)",
        ),
    ],
    ids=["constraint-name", "unicode-independent-column", "index-name"],
)
def test_unrelated_legacy_schema_names_do_not_block_projection_migration(
    table_suffix,
    index_sql,
):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE scheduled_tasks ("
            "id INTEGER PRIMARY KEY, name TEXT, last_run_at DATETIME"
            + table_suffix
            + ")"
        ))
        if index_sql is not None:
            conn.execute(text(index_sql))

    _run_migrations(engine)

    with engine.connect() as conn:
        columns = {
            str(row["name"])
            for row in conn.execute(
                text("PRAGMA main.table_xinfo('scheduled_tasks')")
            ).mappings()
        }
        version_count = conn.execute(text(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = '20260714_outbound_delivery_schema'"
        )).scalar_one()
    assert "last_success_at" in columns
    assert version_count == 1


def test_projection_foreign_key_target_name_does_not_count_as_column_reference():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE last_run_id (id INTEGER PRIMARY KEY)"
        ))
        conn.execute(text(
            "CREATE TABLE scheduled_tasks ("
            "id INTEGER PRIMARY KEY, name INTEGER, last_run_at DATETIME, "
            "FOREIGN KEY(name) REFERENCES last_run_id(id))"
        ))

    _run_migrations(engine)

    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = '20260714_outbound_delivery_schema'"
        )).scalar_one() == 1


def test_projection_foreign_key_source_with_single_quotes_fails_closed():
    from core.schema_migrations import SchemaMigrationValidationError

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE parent_runs (id INTEGER PRIMARY KEY)"
        ))
        conn.execute(text(
            "CREATE TABLE scheduled_tasks ("
            "id INTEGER PRIMARY KEY, last_run_at DATETIME, "
            "last_attempt_at DATETIME, last_success_at DATETIME, "
            "delivery_status VARCHAR(48) NOT NULL DEFAULT 'legacy_unknown', "
            "last_run_id INTEGER, "
            "last_error_summary TEXT NOT NULL DEFAULT '' "
            "CONSTRAINT ck_scheduled_tasks_last_error_summary_length "
            "CHECK (length(last_error_summary) <= 1000), "
            "FOREIGN KEY('last_run_id') REFERENCES parent_runs(id))"
        ))

    with pytest.raises(
        SchemaMigrationValidationError,
        match="scheduled_tasks.*投影",
    ):
        _run_migrations(engine)


def test_unrelated_index_collation_name_does_not_block_projection_migration():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        raw_connection = conn.connection.driver_connection
        raw_connection.create_collation(
            "last_run_id",
            lambda left, right: (left > right) - (left < right),
        )
        conn.execute(text(
            "CREATE TABLE scheduled_tasks ("
            "id INTEGER PRIMARY KEY, name TEXT, last_run_at DATETIME)"
        ))
        conn.execute(text(
            "CREATE INDEX legacy_name_collation "
            "ON scheduled_tasks(name COLLATE last_run_id)"
        ))

    _run_migrations(engine)

    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = '20260714_outbound_delivery_schema'"
        )).scalar_one() == 1


@pytest.mark.parametrize(
    "malformed_ddl",
    [
        "CREATE TABLE outbound_runs (id INTEGER PRIMARY KEY)",
        "CREATE TABLE outbound_generation_attempts (id INTEGER PRIMARY KEY)",
        (
            "CREATE TABLE outbound_delivery_outbox ("
            "id INTEGER PRIMARY KEY, run_id INTEGER, idempotency_key TEXT)"
        ),
        "CREATE TABLE outbound_delivery_attempts (id INTEGER PRIMARY KEY)",
        "CREATE TABLE outbound_delivery_circuits (id INTEGER PRIMARY KEY)",
        (
            "CREATE TABLE outbound_delivery_controls ("
            "id INTEGER PRIMARY KEY, source_type TEXT UNIQUE, mode TEXT)"
        ),
    ],
    ids=[
        "run",
        "generation-attempt",
        "outbox",
        "delivery-attempt",
        "circuit",
        "control",
    ],
)
def test_migration_rejects_malformed_existing_outbound_tables(malformed_ddl):
    from core.schema_migrations import SchemaMigrationValidationError

    engine = create_engine("sqlite:///:memory:")
    table_name = malformed_ddl.split()[2]
    with engine.begin() as conn:
        conn.execute(text(malformed_ddl))

    with pytest.raises(SchemaMigrationValidationError, match=table_name):
        _run_migrations(engine)

    with engine.connect() as conn:
        version_count = conn.execute(text(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = '20260714_outbound_delivery_schema'"
        )).scalar_one()
    assert version_count == 0


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        (
            lambda ddl, contract: ddl.replace(
                "lease_owner VARCHAR(128)",
                "lease_owner VARCHAR(128) NOT NULL",
                1,
            ),
            "lease_owner",
        ),
        (
            lambda ddl, contract: ddl.replace(
                "status VARCHAR(24) NOT NULL DEFAULT 'pending'",
                "status VARCHAR(24) NOT NULL DEFAULT 'retry_wait'",
                1,
            ),
            "status",
        ),
            (
                lambda ddl, contract: ddl.replace(
                    "CONSTRAINT ck_outbound_delivery_lease CHECK ("
                    + next(
                        expression
                        for name, expression in contract.checks
                        if name == "ck_outbound_delivery_lease"
                    )
                    + "),\n",
                    "",
                    1,
                ),
            "CHECK",
        ),
    ],
    ids=["wrong-nullability", "wrong-default", "missing-check"],
)
def test_outbox_schema_drift_in_columns_defaults_or_checks_fails_closed(
    mutation,
    expected_fragment,
):
    from core.outbound_delivery_schema import OUTBOUND_TABLE_CONTRACTS
    from core.schema_migrations import SchemaMigrationValidationError

    contract = next(
        item
        for item in OUTBOUND_TABLE_CONTRACTS
        if item.name == "outbound_delivery_outbox"
    )
    malformed_ddl = mutation(contract.create_sql, contract)
    assert malformed_ddl != contract.create_sql

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(malformed_ddl))

    with pytest.raises(
        SchemaMigrationValidationError,
        match=f"outbound_delivery_outbox.*{expected_fragment}",
    ):
        _run_migrations(engine)


@pytest.mark.parametrize(
    "index_ddl",
    [
        (
            "CREATE INDEX ix_outbound_delivery_expression "
            "ON outbound_delivery_outbox(json_extract(payload_json, '$'))"
        ),
        (
            "CREATE INDEX ix_outbound_delivery_partial "
            "ON outbound_delivery_outbox(status) WHERE status = 'pending'"
        ),
        (
            "CREATE INDEX ix_outbound_delivery_nocase "
            "ON outbound_delivery_outbox(status COLLATE NOCASE)"
        ),
        (
            "CREATE INDEX ix_outbound_delivery_desc "
            "ON outbound_delivery_outbox(status DESC)"
        ),
    ],
    ids=["expression", "partial", "nocase", "descending"],
)
def test_outbox_schema_rejects_extra_or_noncanonical_indexes(index_ddl):
    from core.outbound_delivery_schema import create_outbound_delivery_schema
    from core.schema_migrations import SchemaMigrationValidationError

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        create_outbound_delivery_schema(conn, engine, None)
        conn.execute(text(index_ddl))

    with pytest.raises(
        SchemaMigrationValidationError,
        match="outbound_delivery_outbox.*索引",
    ):
        _run_migrations(engine)


def test_existing_outbound_table_with_wrong_unique_index_fails_closed():
    from core.schema_migrations import SchemaMigrationValidationError
    from core.outbound_delivery_schema import create_outbound_delivery_schema

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        create_outbound_delivery_schema(conn, engine, None)
        conn.execute(text("DROP INDEX uq_outbound_run_occurrence"))
        conn.execute(text(
            "CREATE UNIQUE INDEX uq_outbound_run_occurrence "
            "ON outbound_runs(source_type, occurrence_key)"
        ))

    with pytest.raises(SchemaMigrationValidationError, match="outbound_runs"):
        _run_migrations(engine)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda ddl: ddl.replace("DEFAULT 'legacy_direct'", "DEFAULT 'LEGACY_DIRECT'"),
        lambda ddl: ddl.replace(
            "mode VARCHAR(24) NOT NULL DEFAULT 'legacy_direct'",
            "mode VARCHAR(24) NOT NULL ON CONFLICT REPLACE "
            "DEFAULT 'legacy_direct'",
        ),
    ],
    ids=["literal-case", "not-null-on-conflict"],
)
def test_control_column_clause_drift_fails_closed(mutation):
    from core.schema_migrations import SchemaMigrationValidationError

    contract = _contract("outbound_delivery_controls")
    malformed_ddl = mutation(contract.create_sql)
    assert malformed_ddl != contract.create_sql
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        _create_contract_table(conn, contract, create_sql=malformed_ddl)
        conn.execute(text(
            "INSERT INTO outbound_delivery_controls ("
            "source_type, mode, cutover_epoch, protocol_version, writer_version"
            ") VALUES "
            "('scheduled_task', 'legacy_direct', 0, 1, 0), "
            "('proactive_outreach', 'legacy_direct', 0, 1, 0)"
        ))

    with pytest.raises(
        SchemaMigrationValidationError,
        match="outbound_delivery_controls",
    ):
        _run_migrations(engine)


def test_constraint_name_requires_exact_ascii_identifier():
    from core.schema_migrations import SchemaMigrationValidationError

    contract = _contract("outbound_delivery_controls")
    malformed_ddl = contract.create_sql.replace(
        "ck_outbound_delivery_control_mode",
        "cK_outbound_delivery_control_mode",
        1,
    )
    assert malformed_ddl != contract.create_sql
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        _create_contract_table(conn, contract, create_sql=malformed_ddl)

    with pytest.raises(
        SchemaMigrationValidationError,
        match="outbound_delivery_controls.*CHECK",
    ):
        _run_migrations(engine)


def test_deferred_foreign_key_clause_fails_closed():
    from core.schema_migrations import SchemaMigrationValidationError

    contract = _contract("outbound_generation_attempts")
    malformed_ddl = contract.create_sql.replace(
        "FOREIGN KEY (run_id) REFERENCES outbound_runs(id)",
        "FOREIGN KEY (run_id) REFERENCES outbound_runs(id) "
        "DEFERRABLE INITIALLY DEFERRED",
    )
    assert malformed_ddl != contract.create_sql
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        _create_contract_table(conn, contract, create_sql=malformed_ddl)

    with pytest.raises(
        SchemaMigrationValidationError,
        match="outbound_generation_attempts",
    ):
        _run_migrations(engine)


def test_temp_table_shadow_and_temp_trigger_fail_closed():
    from core.outbound_delivery_schema import validate_outbound_delivery_schema
    from core.schema_migrations import SchemaMigrationValidationError

    engine = create_engine("sqlite:///:memory:")
    _run_migrations(engine)
    contract = _contract("outbound_delivery_controls")

    with engine.begin() as conn:
        temp_sql = contract.create_sql.replace(
            "CREATE TABLE outbound_delivery_controls",
            "CREATE TEMP TABLE outbound_delivery_controls",
            1,
        )
        _create_contract_table(
            conn,
            contract,
            create_sql=temp_sql,
            schema_prefix="temp.",
        )
        with pytest.raises(
            SchemaMigrationValidationError,
            match="TEMP|temp|临时",
        ):
            validate_outbound_delivery_schema(conn)

        conn.execute(text("DROP TABLE temp.outbound_delivery_controls"))
        conn.execute(text(
            "CREATE TEMP TRIGGER swallow_outbound_control "
            "BEFORE INSERT ON main.outbound_delivery_controls "
            "BEGIN SELECT RAISE(IGNORE); END"
        ))
        with pytest.raises(
            SchemaMigrationValidationError,
            match="TEMP|temp|临时",
        ):
            validate_outbound_delivery_schema(conn)


def test_unrelated_unicode_temp_name_is_not_treated_as_ascii_shadow():
    from core.outbound_delivery_schema import validate_outbound_delivery_schema

    engine = create_engine("sqlite:///:memory:")
    _run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TEMP TABLE outbound_delivery_controlſ (id INTEGER)"
        ))
        validate_outbound_delivery_schema(conn)


def test_existing_orphan_audit_row_prevents_migration_version_commit():
    from core.outbound_delivery_schema import create_outbound_delivery_schema
    from core.schema_migrations import SchemaMigrationValidationError

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        create_outbound_delivery_schema(conn, engine, None)
        conn.execute(text(
            "INSERT INTO outbound_generation_attempts ("
            "run_id, attempt_no, owner, fencing_token, status, started_at"
            ") VALUES (424242, 1, 'producer-a', 'token-a', 'started', "
            "CURRENT_TIMESTAMP)"
        ))

    with pytest.raises(
        SchemaMigrationValidationError,
        match="FOREIGN KEY|foreign key|孤儿",
    ):
        _run_migrations(engine)

    with engine.connect() as conn:
        version_count = conn.execute(text(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version = '20260714_outbound_delivery_schema'"
        )).scalar_one()
        orphan_count = conn.execute(text(
            "SELECT COUNT(*) FROM outbound_generation_attempts "
            "WHERE run_id = 424242"
        )).scalar_one()
    assert version_count == 0
    assert orphan_count == 1


def test_file_migration_backfills_once_and_creates_one_snapshot(tmp_path, monkeypatch):
    from core import schema_migrations
    from core.sqlite_backup import create_sqlite_snapshot

    db_path = tmp_path / "legacy-outbound.db"
    with closing(sqlite3.connect(db_path)) as conn:
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
        conn.commit()

    engine = create_engine(f"sqlite:///{db_path}")
    calls = []

    def tracking_snapshot(source_path, target_path, **kwargs):
        calls.append((source_path, target_path))
        return create_sqlite_snapshot(source_path, target_path, **kwargs)

    monkeypatch.setattr(schema_migrations, "create_sqlite_snapshot", tracking_snapshot)
    try:
        schema_migrations.run_schema_migrations(engine, db_path=str(db_path))
        schema_migrations.run_schema_migrations(engine, db_path=str(db_path))
    finally:
        engine.dispose()

    assert len(calls) == 1
    with closing(sqlite3.connect(db_path)) as conn:
        versions = conn.execute(
            "SELECT version FROM schema_migrations "
            "WHERE version = '20260714_outbound_delivery_schema'"
        ).fetchall()
        task = conn.execute(
            "SELECT last_attempt_at, last_success_at, delivery_status "
            "FROM scheduled_tasks WHERE name = 'legacy'"
        ).fetchone()
    assert versions == [("20260714_outbound_delivery_schema",)]
    assert task == ("2026-07-13 08:00:00", None, "legacy_unknown")


def test_init_db_snapshots_and_migrates_before_metadata_create_all(
    tmp_path,
    monkeypatch,
):
    from core import database, schema_migrations
    from core.sqlite_backup import create_sqlite_snapshot

    db_path = tmp_path / "legacy-init.db"
    with closing(sqlite3.connect(db_path)) as conn:
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
        conn.commit()

    test_engine = create_engine(f"sqlite:///{db_path}")
    events = []
    snapshot_paths = []
    real_run_migrations = schema_migrations.run_schema_migrations
    real_create_all = database.Base.metadata.create_all

    def tracking_migrations(engine, *, db_path=None):
        events.append("migrate")
        return real_run_migrations(engine, db_path=db_path)

    def tracking_create_all(bind=None, **kwargs):
        events.append("create_all")
        return real_create_all(bind=bind, **kwargs)

    def tracking_snapshot(source_path, target_path, **kwargs):
        result = create_sqlite_snapshot(source_path, target_path, **kwargs)
        snapshot_paths.append(result)
        return result

    monkeypatch.setattr(database, "DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setattr(schema_migrations, "run_schema_migrations", tracking_migrations)
    monkeypatch.setattr(database.Base.metadata, "create_all", tracking_create_all)
    monkeypatch.setattr(schema_migrations, "create_sqlite_snapshot", tracking_snapshot)

    try:
        database.init_db()
    finally:
        test_engine.dispose()

    assert events[:2] == ["migrate", "create_all"]
    assert len(snapshot_paths) == 1
    with closing(sqlite3.connect(snapshot_paths[0])) as backup_conn:
        backup_tables = {
            row[0]
            for row in backup_conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        backup_columns = {
            row[1]
            for row in backup_conn.execute("PRAGMA table_info(scheduled_tasks)")
        }
    assert "outbound_runs" not in backup_tables
    assert "last_attempt_at" not in backup_columns

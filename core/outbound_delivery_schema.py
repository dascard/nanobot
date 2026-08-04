"""通用主动出站账本的 SQLite DDL 与严格结构校验。"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from core.chat_delivery_outbox_schema import (
    _has_wrapping_parentheses,
    _tokenize_sql,
)
from core.schema_validation import SchemaMigrationValidationError


OUTBOUND_DELIVERY_SCHEMA_VERSION = "20260714_outbound_delivery_schema"
OUTBOUND_SOURCE_TYPES = ("scheduled_task", "proactive_outreach")
SCHEDULED_TASK_ERROR_SUMMARY_CHECK = (
    "ck_scheduled_tasks_last_error_summary_length",
    "length(last_error_summary) <= 1000",
)


def _canonical_datetime_expression(column: str) -> str:
    prefix_pattern = (
        "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] "
        "[0-9][0-9]:[0-9][0-9]:[0-9][0-9]"
    )
    micros_pattern = "[0-9][0-9][0-9][0-9][0-9][0-9]"
    return (
        f"(typeof({column}) = 'text' AND length({column}) IN (19, 26) "
        f"AND length(CAST({column} AS BLOB)) = length({column}) "
        f"AND substr({column}, 1, 19) GLOB '{prefix_pattern}' "
        f"AND substr({column}, 1, 4) <> '0000' "
        f"AND (length({column}) = 19 OR (substr({column}, 20, 1) = '.' "
        f"AND substr({column}, 21, 6) GLOB '{micros_pattern}')) "
        f"AND COALESCE(strftime('%Y-%m-%d %H:%M:%S', "
        f"substr({column}, 1, 19), '+0 days'), '') "
        f"= substr({column}, 1, 19))"
    )


def _datetime_fields_expression(
    *,
    required: tuple[str, ...] = (),
    nullable: tuple[str, ...] = (),
) -> str:
    expressions = [
        _canonical_datetime_expression(column)
        for column in required
    ]
    expressions.extend(
        f"({column} IS NULL OR {_canonical_datetime_expression(column)})"
        for column in nullable
    )
    return " AND ".join(expressions)


OUTBOUND_RUN_CHECKS = (
    (
        "ck_outbound_run_status",
        "status IN ('claimed', 'generating', 'queued', 'delivering', "
        "'succeeded', 'failed', 'blocked', 'ambiguous', "
        "'succeeded_after_ambiguous_replay')",
    ),
    (
        "ck_outbound_run_claim_lease",
        "((status IN ('claimed', 'generating') AND claim_owner IS NOT NULL "
        "AND length(claim_owner) > 0 AND claim_token IS NOT NULL "
        "AND length(claim_token) > 0 AND claim_expires_at IS NOT NULL) OR "
        "(status NOT IN ('claimed', 'generating') AND claim_owner IS NULL "
        "AND claim_token IS NULL AND claim_expires_at IS NULL))",
    ),
    (
        "ck_outbound_run_identity_fields",
        "length(source_type) > 0 AND length(source_id) > 0 "
        "AND length(occurrence_key) > 0 AND length(source_revision) > 0 "
        "AND length(task_kind) > 0 AND length(trigger_type) > 0",
    ),
    (
        "ck_outbound_run_delivery_mode",
        "delivery_mode IN ('legacy_direct', 'outbox')",
    ),
    (
        "ck_outbound_run_cutover_epoch",
        "typeof(cutover_epoch) = 'integer' AND cutover_epoch >= 0",
    ),
    (
        "ck_outbound_run_ambiguous_ancestor",
        "typeof(has_ambiguous_ancestor) = 'integer' "
        "AND has_ambiguous_ancestor IN (0, 1)",
    ),
    (
        "ck_outbound_run_active_outbox_id_type",
        "active_outbox_id IS NULL OR (typeof(active_outbox_id) = 'integer' "
        "AND active_outbox_id >= 1)",
    ),
    (
        "ck_outbound_run_success_time",
        "((status IN ('succeeded', 'succeeded_after_ambiguous_replay') "
        "AND succeeded_at IS NOT NULL) OR "
        "(status NOT IN ('succeeded', 'succeeded_after_ambiguous_replay') "
        "AND succeeded_at IS NULL))",
    ),
    (
        "ck_outbound_run_source_snapshot",
        "length(source_snapshot_sha256) > 0",
    ),
    (
        "ck_outbound_run_delivery_contract",
        "length(delivery_contract_json) > 0 "
        "AND length(delivery_contract_sha256) > 0 "
        "AND length(writer_owner) > 0 AND length(writer_token) > 0 "
        "AND typeof(writer_protocol_version) = 'integer' "
        "AND writer_protocol_version >= 1",
    ),
    (
        "ck_outbound_run_failure_summary_length",
        "length(failure_summary) <= 1000",
    ),
    (
        "ck_outbound_run_datetime_fields",
        _datetime_fields_expression(
            required=("created_at", "updated_at"),
            nullable=(
                "scheduled_for",
                "claim_expires_at",
                "attempted_at",
                "generated_at",
                "succeeded_at",
            ),
        ),
    ),
)

OUTBOUND_GENERATION_ATTEMPT_CHECKS = (
    (
        "ck_outbound_generation_attempt_no",
        "typeof(attempt_no) = 'integer' AND attempt_no >= 1",
    ),
    (
        "ck_outbound_generation_attempt_fencing_identity",
        "length(owner) > 0 AND length(fencing_token) > 0",
    ),
    (
        "ck_outbound_generation_attempt_status",
        "status IN ('started', 'succeeded', 'failed', 'abandoned')",
    ),
    (
        "ck_outbound_generation_attempt_completion",
        "((status = 'started' AND completed_at IS NULL) OR "
        "(status <> 'started' AND completed_at IS NOT NULL))",
    ),
    (
        "ck_outbound_generation_attempt_success_content",
        "status <> 'succeeded' OR length(content_sha256) > 0",
    ),
    (
        "ck_outbound_generation_attempt_failure_type",
        "status <> 'failed' OR length(error_type) > 0",
    ),
    (
        "ck_outbound_generation_attempt_error_summary_length",
        "length(error_summary) <= 1000",
    ),
    (
        "ck_outbound_generation_attempt_datetime_fields",
        _datetime_fields_expression(
            required=("started_at", "created_at"),
            nullable=("completed_at",),
        ),
    ),
)

OUTBOUND_OUTBOX_CHECKS = (
    (
        "ck_outbound_delivery_status",
        "status IN ('pending', 'leased', 'retry_wait', 'delivered', 'failed', "
        "'blocked', 'ambiguous', 'cancelled', 'superseded')",
    ),
    (
        "ck_outbound_delivery_lease",
        "((status = 'leased' AND lease_owner IS NOT NULL "
        "AND length(lease_owner) > 0 AND lease_token IS NOT NULL "
        "AND length(lease_token) > 0 AND lease_expires_at IS NOT NULL) OR "
        "(status <> 'leased' AND lease_owner IS NULL "
        "AND lease_token IS NULL AND lease_expires_at IS NULL))",
    ),
    (
        "ck_outbound_delivery_terminal_not_due",
        "status = 'pending' OR "
        "(status = 'retry_wait' AND next_attempt_at IS NOT NULL) OR "
        "(status NOT IN ('pending', 'retry_wait') AND next_attempt_at IS NULL)",
    ),
    (
        "ck_outbound_delivery_attempt_counts",
        "typeof(allocated_attempt_count) = 'integer' "
        "AND typeof(request_started_count) = 'integer' "
        "AND allocated_attempt_count >= 0 AND request_started_count >= 0 "
        "AND request_started_count <= allocated_attempt_count "
        "AND request_started_count <= max_attempts",
    ),
    (
        "ck_outbound_delivery_max_attempts",
        "typeof(max_attempts) = 'integer' AND max_attempts >= 1",
    ),
    (
        "ck_outbound_delivery_replay_sequence",
        "typeof(replay_sequence) = 'integer' AND replay_sequence >= 0",
    ),
    (
        "ck_outbound_delivery_replay_parent",
        "((replay_sequence = 0 AND replay_of_outbox_id IS NULL) OR "
        "(replay_sequence > 0 AND replay_of_outbox_id IS NOT NULL))",
    ),
    (
        "ck_outbound_delivery_replay_parent_type",
        "replay_of_outbox_id IS NULL OR "
        "typeof(replay_of_outbox_id) = 'integer'",
    ),
    (
        "ck_outbound_delivery_replay_request_audit",
        "((replay_sequence = 0 AND length(replay_request_sha256) = 0) OR "
        "(replay_sequence > 0 AND length(replay_request_sha256) = 64))",
    ),
    (
        "ck_outbound_delivery_delivered_at",
        "((status = 'delivered' AND delivered_at IS NOT NULL) OR "
        "(status <> 'delivered' AND delivered_at IS NULL))",
    ),
    (
        "ck_outbound_delivery_cancelled_at",
        "((status = 'cancelled' AND cancelled_at IS NOT NULL "
        "AND cancel_reason_type IS NOT NULL AND length(cancel_reason_type) > 0) OR "
        "(status <> 'cancelled' AND cancelled_at IS NULL "
        "AND cancel_reason_type IS NULL))",
    ),
    (
        "ck_outbound_delivery_cutover_epoch",
        "typeof(cutover_epoch) = 'integer' AND cutover_epoch >= 0",
    ),
    (
        "ck_outbound_delivery_identity_fields",
        "length(idempotency_key) > 0 AND length(destination_fingerprint) > 0 "
        "AND length(target_type) > 0 AND length(endpoint_key) > 0 "
        "AND length(payload_sha256) > 0 "
        "AND length(endpoint_config_revision) > 0 "
        "AND length(payload_contract_fingerprint) > 0",
    ),
    (
        "ck_outbound_delivery_error_summary_length",
        "length(last_error_summary) <= 1000",
    ),
    (
        "ck_outbound_delivery_datetime_fields",
        _datetime_fields_expression(
            required=("retry_deadline_at", "created_at", "updated_at"),
            nullable=(
                "lease_expires_at",
                "next_attempt_at",
                "delivered_at",
                "cancelled_at",
            ),
        ),
    ),
)

OUTBOUND_DELIVERY_ATTEMPT_CHECKS = (
    (
        "ck_outbound_delivery_attempt_no",
        "typeof(attempt_no) = 'integer' AND attempt_no >= 1",
    ),
    (
        "ck_outbound_delivery_attempt_fencing_identity",
        "length(worker_owner) > 0 AND length(lease_token) > 0 "
        "AND length(endpoint_config_revision) > 0",
    ),
    (
        "ck_outbound_delivery_attempt_status",
        "status IN ('started', 'succeeded', 'transient_failure', "
        "'permanent_failure', 'ambiguous', 'abandoned_before_send', "
        "'cancelled_before_send')",
    ),
    (
        "ck_outbound_delivery_attempt_phase",
        "transport_phase IN ('allocated', 'request_started', 'connect', 'write', "
        "'read', 'response_received', 'settled')",
    ),
    (
        "ck_outbound_delivery_attempt_request_started",
        "typeof(request_started) = 'integer' "
        "AND request_started IN (0, 1) AND "
        "((request_started = 0 AND transport_phase = 'allocated' "
        "AND request_started_at IS NULL) OR "
        "(request_started = 1 AND transport_phase <> 'allocated' "
        "AND request_started_at IS NOT NULL))",
    ),
    (
        "ck_outbound_delivery_attempt_completion",
        "((status = 'started' AND completed_at IS NULL) OR "
        "(status <> 'started' AND completed_at IS NOT NULL))",
    ),
    (
        "ck_outbound_delivery_attempt_http_status",
        "http_status IS NULL OR (typeof(http_status) = 'integer' "
        "AND http_status >= 100 AND http_status <= 599)",
    ),
    (
        "ck_outbound_delivery_attempt_terminal_requires_request",
        "status NOT IN ('succeeded', 'ambiguous') OR request_started = 1",
    ),
    (
        "ck_outbound_delivery_attempt_before_send_result",
        "status NOT IN ('abandoned_before_send', 'cancelled_before_send') OR "
        "(request_started = 0 AND http_status IS NULL)",
    ),
    (
        "ck_outbound_delivery_attempt_http_requires_request",
        "http_status IS NULL OR request_started = 1",
    ),
    (
        "ck_outbound_delivery_attempt_http_phase",
        "http_status IS NULL OR "
        "transport_phase IN ('read', 'response_received', 'settled')",
    ),
    (
        "ck_outbound_delivery_attempt_success_http_status",
        "status <> 'succeeded' OR (http_status IS NOT NULL "
        "AND http_status >= 200 AND http_status <= 299)",
    ),
    (
        "ck_outbound_delivery_attempt_success_phase",
        "status <> 'succeeded' OR "
        "transport_phase IN ('response_received', 'settled')",
    ),
    (
        "ck_outbound_delivery_attempt_duration",
        "duration_ms IS NULL OR (typeof(duration_ms) = 'integer' "
        "AND duration_ms >= 0)",
    ),
    (
        "ck_outbound_delivery_attempt_circuit_scope",
        "settlement_circuit_scope_type IS NULL OR "
        "settlement_circuit_scope_type IN "
        "('endpoint', 'destination', 'payload_contract')",
    ),
    (
        "ck_outbound_delivery_attempt_settlement_audit",
        "((status IN ('succeeded', 'transient_failure', "
        "'permanent_failure', 'ambiguous') "
        "AND length(settlement_request_sha256) = 64) OR "
        "(status NOT IN ('succeeded', 'transient_failure', "
        "'permanent_failure', 'ambiguous') "
        "AND length(settlement_request_sha256) = 0))",
    ),
    (
        "ck_outbound_delivery_attempt_safe_summary_length",
        "length(safe_summary) <= 1000",
    ),
    (
        "ck_outbound_delivery_attempt_datetime_fields",
        _datetime_fields_expression(
            required=("started_at", "created_at"),
            nullable=(
                "request_started_at",
                "settlement_retry_at",
                "completed_at",
            ),
        ),
    ),
)

OUTBOUND_CIRCUIT_CHECKS = (
    (
        "ck_outbound_delivery_circuit_scope",
        "scope_type IN ('endpoint', 'destination', 'payload_contract')",
    ),
    (
        "ck_outbound_delivery_circuit_identity",
        "length(scope_fingerprint) > 0 AND length(config_revision) > 0",
    ),
    (
        "ck_outbound_delivery_circuit_status",
        "status IN ('closed', 'open')",
    ),
    (
        "ck_outbound_delivery_circuit_open",
        "status <> 'open' OR (opened_at IS NOT NULL AND length(reason_type) > 0)",
    ),
    (
        "ck_outbound_delivery_circuit_datetime_fields",
        _datetime_fields_expression(
            required=("created_at", "updated_at"),
            nullable=("opened_at",),
        ),
    ),
)

OUTBOUND_CONTROL_CHECKS = (
    (
        "ck_outbound_delivery_control_source_identity",
        "length(source_type) > 0",
    ),
    (
        "ck_outbound_delivery_control_mode",
        "mode IN ('legacy_direct', 'outbox_hold', 'outbox_active', "
        "'outbox_draining')",
    ),
    (
        "ck_outbound_delivery_control_epoch",
        "typeof(cutover_epoch) = 'integer' AND cutover_epoch >= 0",
    ),
    (
        "ck_outbound_delivery_control_protocol",
        "typeof(protocol_version) = 'integer' "
        "AND typeof(writer_version) = 'integer' "
        "AND protocol_version >= 1 AND writer_version >= 0",
    ),
    (
        "ck_outbound_delivery_control_writer_lease",
        "((writer_owner IS NULL AND writer_token IS NULL "
        "AND writer_lease_expires_at IS NULL) OR "
        "(writer_owner IS NOT NULL AND length(writer_owner) > 0 "
        "AND writer_token IS NOT NULL AND length(writer_token) > 0 "
        "AND writer_lease_expires_at IS NOT NULL))",
    ),
    (
        "ck_outbound_delivery_control_datetime_fields",
        _datetime_fields_expression(
            required=("effective_from", "created_at", "updated_at"),
            nullable=("writer_lease_expires_at",),
        ),
    ),
)


@dataclass(frozen=True)
class ColumnContract:
    name: str
    sqlite_type: str
    not_null: int
    default: str | None
    primary_key: int = 0
    inline_checks: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class IndexContract:
    name: str
    columns: tuple[str, ...]
    unique: bool = False


@dataclass(frozen=True)
class ForeignKeyContract:
    source_column: str
    target_table: str
    target_column: str = "id"


@dataclass(frozen=True)
class TableContract:
    name: str
    create_sql: str
    columns: tuple[ColumnContract, ...]
    checks: tuple[tuple[str, str], ...]
    indexes: tuple[IndexContract, ...]
    foreign_keys: tuple[ForeignKeyContract, ...] = ()


def _column(
    name: str,
    sqlite_type: str,
    *,
    not_null: bool = True,
    default: str | None = None,
    primary_key: bool = False,
    inline_checks: tuple[tuple[str, str], ...] = (),
) -> ColumnContract:
    return ColumnContract(
        name=name,
        sqlite_type=sqlite_type,
        not_null=int(not_null),
        default=default,
        primary_key=int(primary_key),
        inline_checks=inline_checks,
    )


def _check_sql(checks: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    return tuple(
        f"CONSTRAINT {name} CHECK ({expression})"
        for name, expression in checks
    )


_RUN_COLUMNS = (
    _column("id", "INTEGER", primary_key=True),
    _column("source_type", "VARCHAR(32)"),
    _column("source_id", "VARCHAR(255)"),
    _column("occurrence_key", "VARCHAR(255)"),
    _column("source_revision", "VARCHAR(128)"),
    _column("source_snapshot_json", "TEXT"),
    _column("source_snapshot_sha256", "VARCHAR(64)"),
    _column("delivery_contract_json", "TEXT"),
    _column("delivery_contract_sha256", "VARCHAR(64)"),
    _column("writer_owner", "VARCHAR(128)"),
    _column("writer_token", "VARCHAR(64)"),
    _column("writer_protocol_version", "INTEGER"),
    _column("task_kind", "VARCHAR(64)"),
    _column("scheduled_for", "DATETIME", not_null=False),
    _column("trigger_type", "VARCHAR(32)"),
    _column("status", "VARCHAR(48)", default="'claimed'"),
    _column("claim_owner", "VARCHAR(128)", not_null=False),
    _column("claim_token", "VARCHAR(64)", not_null=False),
    _column("claim_expires_at", "DATETIME", not_null=False),
    _column("claim_generation", "INTEGER", default="0"),
    _column("attempt_count", "INTEGER", default="0"),
    _column("attempted_at", "DATETIME", not_null=False),
    _column("generated_at", "DATETIME", not_null=False),
    _column("succeeded_at", "DATETIME", not_null=False),
    _column("failure_type", "VARCHAR(64)", default="''"),
    _column("failure_summary", "TEXT", default="''"),
    _column("active_outbox_id", "INTEGER", not_null=False),
    _column("has_ambiguous_ancestor", "BOOLEAN", default="0"),
    _column("delivery_mode", "VARCHAR(24)"),
    _column("cutover_epoch", "INTEGER"),
    _column("created_at", "DATETIME", default="CURRENT_TIMESTAMP"),
    _column("updated_at", "DATETIME", default="CURRENT_TIMESTAMP"),
)

_GENERATION_COLUMNS = (
    _column("id", "INTEGER", primary_key=True),
    _column("run_id", "INTEGER"),
    _column("attempt_no", "INTEGER"),
    _column("owner", "VARCHAR(128)"),
    _column("fencing_token", "VARCHAR(64)"),
    _column("status", "VARCHAR(32)", default="'started'"),
    _column("started_at", "DATETIME", default="CURRENT_TIMESTAMP"),
    _column("completed_at", "DATETIME", not_null=False),
    _column("model_trace_id", "VARCHAR(128)", default="''"),
    _column("content_sha256", "VARCHAR(64)", default="''"),
    _column("error_type", "VARCHAR(64)", default="''"),
    _column("error_summary", "TEXT", default="''"),
    _column("created_at", "DATETIME", default="CURRENT_TIMESTAMP"),
)

_OUTBOX_COLUMNS = (
    _column("id", "INTEGER", primary_key=True),
    _column("run_id", "INTEGER"),
    _column("idempotency_key", "VARCHAR(255)"),
    _column("destination_snapshot_json", "TEXT"),
    _column("destination_fingerprint", "VARCHAR(64)"),
    _column("target_type", "VARCHAR(16)"),
    _column("endpoint_key", "VARCHAR(64)"),
    _column("payload_json", "TEXT"),
    _column("payload_sha256", "VARCHAR(64)"),
    _column("status", "VARCHAR(24)", default="'pending'"),
    _column("lease_owner", "VARCHAR(128)", not_null=False),
    _column("lease_token", "VARCHAR(64)", not_null=False),
    _column("lease_expires_at", "DATETIME", not_null=False),
    _column("next_attempt_at", "DATETIME", not_null=False),
    _column("allocated_attempt_count", "INTEGER", default="0"),
    _column("request_started_count", "INTEGER", default="0"),
    _column("max_attempts", "INTEGER"),
    _column("retry_deadline_at", "DATETIME"),
    _column("last_error_type", "VARCHAR(64)", default="''"),
    _column("last_error_summary", "TEXT", default="''"),
    _column("delivered_at", "DATETIME", not_null=False),
    _column("cancelled_at", "DATETIME", not_null=False),
    _column("cancel_reason_type", "VARCHAR(64)", not_null=False),
    _column("replay_of_outbox_id", "INTEGER", not_null=False),
    _column("replay_sequence", "INTEGER", default="0"),
    _column("replay_request_sha256", "VARCHAR(64)", default="''"),
    _column("cutover_epoch", "INTEGER"),
    _column("endpoint_config_revision", "VARCHAR(128)"),
    _column("payload_contract_fingerprint", "VARCHAR(64)"),
    _column("created_at", "DATETIME", default="CURRENT_TIMESTAMP"),
    _column("updated_at", "DATETIME", default="CURRENT_TIMESTAMP"),
)

_DELIVERY_ATTEMPT_COLUMNS = (
    _column("id", "INTEGER", primary_key=True),
    _column("outbox_id", "INTEGER"),
    _column("attempt_no", "INTEGER"),
    _column("worker_owner", "VARCHAR(128)"),
    _column("lease_token", "VARCHAR(64)"),
    _column("status", "VARCHAR(32)", default="'started'"),
    _column("transport_phase", "VARCHAR(32)", default="'allocated'"),
    _column("request_started", "BOOLEAN", default="0"),
    _column("endpoint_config_revision", "VARCHAR(128)"),
    _column("http_status", "INTEGER", not_null=False),
    _column("result_category", "VARCHAR(64)", default="''"),
    _column("error_type", "VARCHAR(64)", default="''"),
    _column("safe_summary", "TEXT", default="''"),
    _column("duration_ms", "INTEGER", not_null=False),
    _column("settlement_retry_at", "DATETIME", not_null=False),
    _column("settlement_circuit_scope_type", "VARCHAR(32)", not_null=False),
    _column("settlement_request_sha256", "VARCHAR(64)", default="''"),
    _column("started_at", "DATETIME", default="CURRENT_TIMESTAMP"),
    _column("request_started_at", "DATETIME", not_null=False),
    _column("completed_at", "DATETIME", not_null=False),
    _column("created_at", "DATETIME", default="CURRENT_TIMESTAMP"),
)

_CIRCUIT_COLUMNS = (
    _column("id", "INTEGER", primary_key=True),
    _column("scope_type", "VARCHAR(32)"),
    _column("scope_fingerprint", "VARCHAR(64)"),
    _column("config_revision", "VARCHAR(128)"),
    _column("status", "VARCHAR(16)", default="'closed'"),
    _column("reason_type", "VARCHAR(64)", default="''"),
    _column("opened_at", "DATETIME", not_null=False),
    _column("opened_by_attempt_id", "INTEGER", not_null=False),
    _column("created_at", "DATETIME", default="CURRENT_TIMESTAMP"),
    _column("updated_at", "DATETIME", default="CURRENT_TIMESTAMP"),
)

_CONTROL_COLUMNS = (
    _column("source_type", "VARCHAR(32)", primary_key=True),
    _column("mode", "VARCHAR(24)", default="'legacy_direct'"),
    _column("cutover_epoch", "INTEGER", default="0"),
    _column("effective_from", "DATETIME", default="CURRENT_TIMESTAMP"),
    _column("protocol_version", "INTEGER", default="1"),
    _column("writer_version", "INTEGER", default="0"),
    _column("writer_owner", "VARCHAR(128)", not_null=False),
    _column("writer_token", "VARCHAR(64)", not_null=False),
    _column("writer_lease_expires_at", "DATETIME", not_null=False),
    _column("created_at", "DATETIME", default="CURRENT_TIMESTAMP"),
    _column("updated_at", "DATETIME", default="CURRENT_TIMESTAMP"),
)


def _column_ddl(column: ColumnContract) -> str:
    parts = [column.name, column.sqlite_type]
    if column.not_null:
        parts.append("NOT NULL")
    if column.primary_key and column.sqlite_type == "INTEGER":
        parts.append("PRIMARY KEY")
        parts.append("AUTOINCREMENT")
    if column.default is not None:
        parts.append(f"DEFAULT {column.default}")
    parts.extend(
        f"CONSTRAINT {name} CHECK ({expression})"
        for name, expression in column.inline_checks
    )
    return " ".join(parts)


def _table_sql(
    name: str,
    columns: tuple[ColumnContract, ...],
    checks: tuple[tuple[str, str], ...],
    foreign_keys: tuple[ForeignKeyContract, ...] = (),
) -> str:
    parts = [_column_ddl(column) for column in columns]
    table_primary_keys = tuple(
        column.name
        for column in columns
        if column.primary_key and column.sqlite_type != "INTEGER"
    )
    if table_primary_keys:
        parts.append(f"PRIMARY KEY ({', '.join(table_primary_keys)})")
    parts.extend(
        f"FOREIGN KEY ({item.source_column}) REFERENCES "
        f"{item.target_table}({item.target_column})"
        for item in foreign_keys
    )
    parts.extend(_check_sql(checks))
    body = ",\n".join(parts)
    return f"CREATE TABLE {name} (\n{body}\n)"


_RUN_FOREIGN_KEYS: tuple[ForeignKeyContract, ...] = ()
_GENERATION_FOREIGN_KEYS = (ForeignKeyContract("run_id", "outbound_runs"),)
_OUTBOX_FOREIGN_KEYS = (
    ForeignKeyContract("run_id", "outbound_runs"),
    ForeignKeyContract("replay_of_outbox_id", "outbound_delivery_outbox"),
)
_DELIVERY_ATTEMPT_FOREIGN_KEYS = (
    ForeignKeyContract("outbox_id", "outbound_delivery_outbox"),
)
_CIRCUIT_FOREIGN_KEYS = (
    ForeignKeyContract("opened_by_attempt_id", "outbound_delivery_attempts"),
)

OUTBOUND_TABLE_CONTRACTS = (
    TableContract(
        name="outbound_runs",
        create_sql=_table_sql(
            "outbound_runs", _RUN_COLUMNS, OUTBOUND_RUN_CHECKS, _RUN_FOREIGN_KEYS
        ),
        columns=_RUN_COLUMNS,
        checks=OUTBOUND_RUN_CHECKS,
        indexes=(
            IndexContract(
                "uq_outbound_run_occurrence",
                ("source_type", "source_id", "occurrence_key"),
                unique=True,
            ),
            IndexContract(
                "ix_outbound_run_source",
                ("source_type", "source_id", "status"),
            ),
            IndexContract(
                "ix_outbound_run_claim_lease",
                ("status", "claim_expires_at"),
            ),
        ),
        foreign_keys=_RUN_FOREIGN_KEYS,
    ),
    TableContract(
        name="outbound_generation_attempts",
        create_sql=_table_sql(
            "outbound_generation_attempts",
            _GENERATION_COLUMNS,
            OUTBOUND_GENERATION_ATTEMPT_CHECKS,
            _GENERATION_FOREIGN_KEYS,
        ),
        columns=_GENERATION_COLUMNS,
        checks=OUTBOUND_GENERATION_ATTEMPT_CHECKS,
        indexes=(
            IndexContract(
                "uq_outbound_generation_attempt",
                ("run_id", "attempt_no"),
                unique=True,
            ),
        ),
        foreign_keys=_GENERATION_FOREIGN_KEYS,
    ),
    TableContract(
        name="outbound_delivery_outbox",
        create_sql=_table_sql(
            "outbound_delivery_outbox",
            _OUTBOX_COLUMNS,
            OUTBOUND_OUTBOX_CHECKS,
            _OUTBOX_FOREIGN_KEYS,
        ),
        columns=_OUTBOX_COLUMNS,
        checks=OUTBOUND_OUTBOX_CHECKS,
        indexes=(
            IndexContract(
                "uq_outbound_delivery_idempotency_key",
                ("idempotency_key",),
                unique=True,
            ),
            IndexContract(
                "uq_outbound_delivery_replay_leaf",
                ("run_id", "destination_fingerprint", "replay_sequence"),
                unique=True,
            ),
            IndexContract(
                "ix_outbound_delivery_due", ("status", "next_attempt_at")
            ),
            IndexContract(
                "ix_outbound_delivery_lease", ("status", "lease_expires_at")
            ),
            IndexContract(
                "ix_outbound_delivery_run_status", ("run_id", "status")
            ),
            IndexContract(
                "ix_outbound_delivery_replay_parent", ("replay_of_outbox_id",)
            ),
        ),
        foreign_keys=_OUTBOX_FOREIGN_KEYS,
    ),
    TableContract(
        name="outbound_delivery_attempts",
        create_sql=_table_sql(
            "outbound_delivery_attempts",
            _DELIVERY_ATTEMPT_COLUMNS,
            OUTBOUND_DELIVERY_ATTEMPT_CHECKS,
            _DELIVERY_ATTEMPT_FOREIGN_KEYS,
        ),
        columns=_DELIVERY_ATTEMPT_COLUMNS,
        checks=OUTBOUND_DELIVERY_ATTEMPT_CHECKS,
        indexes=(
            IndexContract(
                "uq_outbound_delivery_attempt",
                ("outbox_id", "attempt_no"),
                unique=True,
            ),
            IndexContract(
                "ix_outbound_delivery_attempt_status_started",
                ("status", "started_at"),
            ),
        ),
        foreign_keys=_DELIVERY_ATTEMPT_FOREIGN_KEYS,
    ),
    TableContract(
        name="outbound_delivery_circuits",
        create_sql=_table_sql(
            "outbound_delivery_circuits",
            _CIRCUIT_COLUMNS,
            OUTBOUND_CIRCUIT_CHECKS,
            _CIRCUIT_FOREIGN_KEYS,
        ),
        columns=_CIRCUIT_COLUMNS,
        checks=OUTBOUND_CIRCUIT_CHECKS,
        indexes=(
            IndexContract(
                "uq_outbound_delivery_circuit_scope",
                ("scope_type", "scope_fingerprint", "config_revision"),
                unique=True,
            ),
            IndexContract(
                "ix_outbound_delivery_circuit_status", ("status",)
            ),
        ),
        foreign_keys=_CIRCUIT_FOREIGN_KEYS,
    ),
    TableContract(
        name="outbound_delivery_controls",
        create_sql=_table_sql(
            "outbound_delivery_controls", _CONTROL_COLUMNS, OUTBOUND_CONTROL_CHECKS
        ),
        columns=_CONTROL_COLUMNS,
        checks=OUTBOUND_CONTROL_CHECKS,
        indexes=(
            IndexContract(
                "ix_outbound_delivery_control_mode_effective",
                ("mode", "effective_from"),
            ),
        ),
    ),
)


def _normalize_default(
    value: Any,
) -> tuple[tuple[str, str], ...] | None:
    if value is None:
        return None
    tokens = _tokenize_sql(str(value))
    while _has_wrapping_parentheses(tokens):
        tokens = tokens[1:-1]
    return tokens


def _split_create_table_clauses(
    table_sql: str,
    *,
    table_name: str,
    strict_header: bool = True,
) -> tuple[tuple[tuple[str, str], ...], ...]:
    tokens = _tokenize_sql(table_sql)
    if any(kind == "invalid" for kind, _value in tokens):
        raise SchemaMigrationValidationError(
            f"{table_name} 建表 DDL 包含未闭合的引号或注释"
        )
    try:
        open_index = tokens.index(("symbol", "("))
    except ValueError as exc:
        raise SchemaMigrationValidationError(
            f"{table_name} 建表 DDL 缺少列定义"
        ) from exc

    expected_header = _tokenize_sql(f"CREATE TABLE {table_name}")
    if strict_header and tokens[:open_index] != expected_header:
        raise SchemaMigrationValidationError(
            f"{table_name} 建表头不符合契约"
        )

    depth = 0
    close_index: int | None = None
    for index in range(open_index, len(tokens)):
        token = tokens[index]
        if token == ("symbol", "("):
            depth += 1
        elif token == ("symbol", ")"):
            depth -= 1
            if depth == 0:
                close_index = index
                break
            if depth < 0:
                break
    if close_index is None or close_index != len(tokens) - 1:
        raise SchemaMigrationValidationError(
            f"{table_name} 建表 DDL 外层结构不符合契约"
        )

    clauses: list[tuple[tuple[str, str], ...]] = []
    current: list[tuple[str, str]] = []
    depth = 0
    for token in tokens[open_index + 1:close_index]:
        if token == ("symbol", "("):
            depth += 1
        elif token == ("symbol", ")"):
            depth -= 1
            if depth < 0:
                raise SchemaMigrationValidationError(
                    f"{table_name} 建表 DDL 括号不平衡"
                )
        if token == ("symbol", ",") and depth == 0:
            if not current:
                raise SchemaMigrationValidationError(
                    f"{table_name} 建表 DDL 包含空定义"
                )
            clauses.append(tuple(current))
            current = []
            continue
        current.append(token)
    if depth != 0 or not current:
        raise SchemaMigrationValidationError(
            f"{table_name} 建表 DDL 列或约束结构不完整"
        )
    clauses.append(tuple(current))
    return tuple(clauses)


def _column_clause_variants(
    column: ColumnContract,
) -> set[tuple[tuple[str, str], ...]]:
    variants = {_tokenize_sql(_column_ddl(column))}
    if column.not_null and column.default is not None:
        sqlalchemy_parts = [column.name, column.sqlite_type]
        if column.primary_key and column.sqlite_type == "INTEGER":
            sqlalchemy_parts.extend(("NOT NULL", "PRIMARY KEY", "AUTOINCREMENT"))
        else:
            sqlalchemy_parts.extend((f"DEFAULT {column.default}", "NOT NULL"))
        sqlalchemy_parts.extend(
            f"CONSTRAINT {name} CHECK ({expression})"
            for name, expression in column.inline_checks
        )
        variants.add(_tokenize_sql(" ".join(sqlalchemy_parts)))
    return variants


def _expected_table_constraints(
    contract: TableContract,
) -> Counter[tuple[tuple[str, str], ...]]:
    clauses: list[tuple[tuple[str, str], ...]] = []
    table_primary_keys = tuple(
        column.name
        for column in contract.columns
        if column.primary_key and column.sqlite_type != "INTEGER"
    )
    if table_primary_keys:
        clauses.append(_tokenize_sql(
            f"PRIMARY KEY ({', '.join(table_primary_keys)})"
        ))
    clauses.extend(
        _tokenize_sql(
            f"FOREIGN KEY ({item.source_column}) REFERENCES "
            f"{item.target_table}({item.target_column})"
        )
        for item in contract.foreign_keys
    )
    clauses.extend(
        _tokenize_sql(f"CONSTRAINT {name} CHECK ({expression})")
        for name, expression in contract.checks
    )
    return Counter(clauses)


def _validate_table_clauses(
    table_sql: str,
    contract: TableContract,
) -> None:
    clauses = _split_create_table_clauses(
        table_sql,
        table_name=contract.name,
    )
    column_count = len(contract.columns)
    if len(clauses) < column_count:
        raise SchemaMigrationValidationError(
            f"{contract.name} 顶层列定义数量不符合契约"
        )
    for clause, column in zip(
        clauses[:column_count],
        contract.columns,
        strict=True,
    ):
        if clause not in _column_clause_variants(column):
            raise SchemaMigrationValidationError(
                f"{contract.name}.{column.name} 顶层列定义不符合契约"
            )
    actual_constraints = Counter(clauses[column_count:])
    expected_constraints = _expected_table_constraints(contract)
    if actual_constraints != expected_constraints:
        raise SchemaMigrationValidationError(
            f"{contract.name} 顶层 PRIMARY KEY / FOREIGN KEY / CHECK "
            "约束集合不符合契约"
        )


def _sqlite_identifier_key(value: str) -> str:
    return "".join(
        chr(ord(char) + 32) if "A" <= char <= "Z" else char
        for char in value
    )


def _main_schema_objects_named(
    conn: Any,
    object_name: str,
) -> list[dict[str, Any]]:
    expected_key = _sqlite_identifier_key(object_name)
    return [
        dict(row)
        for row in conn.execute(text(
            "SELECT type, name, tbl_name, sql FROM main.sqlite_master "
            "WHERE name IS NOT NULL"
        )).mappings()
        if _sqlite_identifier_key(str(row["name"])) == expected_key
    ]


def _actual_table_name(conn: Any, table_name: str) -> str | None:
    rows = _main_schema_objects_named(conn, table_name)
    if not rows:
        return None
    expected = [("table", table_name)]
    actual = [(str(row["type"]), str(row["name"])) for row in rows]
    if actual != expected:
        raise SchemaMigrationValidationError(
            f"{table_name} 表名、类型或重复对象不符合契约: {actual!r}"
        )
    return table_name


def _reject_temp_schema_objects(conn: Any) -> None:
    protected_tables = {
        *(contract.name for contract in OUTBOUND_TABLE_CONTRACTS),
        "scheduled_tasks",
        "proactive_outreach_log",
    }
    protected_indexes = {
        *(
            index.name
            for contract in OUTBOUND_TABLE_CONTRACTS
            for index in contract.indexes
        ),
        "ix_scheduled_tasks_last_run_id",
        "ix_proactive_outreach_log_outbound_run_id",
    }
    protected_names = {
        _sqlite_identifier_key(name)
        for name in protected_tables | protected_indexes
    }
    conflicts = []
    for row in conn.execute(text(
        "SELECT type, name, tbl_name FROM sqlite_temp_master "
        "WHERE type IN ('table', 'view', 'index', 'trigger')"
    )).mappings():
        name = str(row["name"] or "")
        table_name = str(row["tbl_name"] or "")
        if (
            _sqlite_identifier_key(name) in protected_names
            or _sqlite_identifier_key(table_name) in protected_names
        ):
            conflicts.append((str(row["type"]), name, table_name))
    if conflicts:
        raise SchemaMigrationValidationError(
            "出站账本不允许 TEMP 临时对象遮蔽受保护表或索引: "
            f"{conflicts!r}"
        )


def _validate_columns(conn: Any, contract: TableContract) -> None:
    rows = conn.execute(
        text(f"PRAGMA main.table_xinfo('{contract.name}')")
    ).mappings().all()
    hidden = [
        (str(row["name"]), int(row["hidden"]))
        for row in rows
        if int(row["hidden"]) != 0
    ]
    if hidden:
        raise SchemaMigrationValidationError(
            f"{contract.name} 不允许 hidden 或 generated 列: {hidden!r}"
        )
    actual_names = tuple(str(row["name"]) for row in rows)
    expected_names = tuple(column.name for column in contract.columns)
    if actual_names != expected_names:
        raise SchemaMigrationValidationError(
            f"{contract.name} 列集合或顺序不符合契约: "
            f"expected={expected_names!r} actual={actual_names!r}"
        )
    for row, expected in zip(rows, contract.columns, strict=True):
        actual = (
            str(row["type"]).upper().replace(" ", ""),
            int(row["notnull"]),
            _normalize_default(row["dflt_value"]),
            int(row["pk"]),
        )
        required = (
            expected.sqlite_type,
            expected.not_null,
            _normalize_default(expected.default),
            expected.primary_key,
        )
        if actual != required:
            raise SchemaMigrationValidationError(
                f"{contract.name}.{expected.name} 定义不符合契约: "
                f"expected={required!r} actual={actual!r}"
            )


def _validate_checks_and_auxiliary_objects(conn: Any, contract: TableContract) -> None:
    table_sql = conn.execute(text(
        "SELECT sql FROM main.sqlite_master "
        "WHERE type = 'table' AND name = :name"
    ), {"name": contract.name}).scalar_one_or_none()
    if not isinstance(table_sql, str):
        raise SchemaMigrationValidationError(f"{contract.name} 缺少建表 DDL")
    lowered = table_sql.casefold()
    if "--" in table_sql or "/*" in table_sql or "*/" in table_sql:
        raise SchemaMigrationValidationError(f"{contract.name} 不允许 SQL 注释")
    if re.search(r"\bcollate\b", lowered):
        raise SchemaMigrationValidationError(f"{contract.name} 不允许 COLLATE 声明")
    if re.search(r"\bwithout\s+rowid\b|\bstrict\s*$", lowered):
        raise SchemaMigrationValidationError(
            f"{contract.name} 不允许额外表存储选项"
        )
    _validate_table_clauses(table_sql, contract)

    actual_check_names = Counter(re.findall(
        r"\bCONSTRAINT\s+([A-Za-z_][A-Za-z0-9_]*)\s+CHECK\s*\(",
        table_sql,
        flags=re.ASCII | re.IGNORECASE,
    ))
    expected_check_names = Counter(name for name, _expression in contract.checks)
    if actual_check_names != expected_check_names:
        raise SchemaMigrationValidationError(
            f"{contract.name} CHECK 集合不符合契约: "
            f"names={sorted(actual_check_names.elements())!r}"
        )

    triggers = conn.execute(text(
        "SELECT name FROM main.sqlite_master "
        "WHERE type = 'trigger' AND lower(tbl_name) = lower(:name)"
    ), {"name": contract.name}).scalars().all()
    if triggers:
        raise SchemaMigrationValidationError(
            f"{contract.name} 不允许 trigger: {sorted(str(item) for item in triggers)!r}"
        )

    actual_foreign_keys = sorted(
        (
            str(row["from"]),
            str(row["table"]),
            str(row["to"]),
            str(row["on_update"]).upper(),
            str(row["on_delete"]).upper(),
            str(row["match"]).upper(),
        )
        for row in conn.execute(
            text(f"PRAGMA main.foreign_key_list('{contract.name}')")
        ).mappings()
    )
    expected_foreign_keys = sorted(
        (
            item.source_column,
            item.target_table,
            item.target_column,
            "NO ACTION",
            "NO ACTION",
            "NONE",
        )
        for item in contract.foreign_keys
    )
    if actual_foreign_keys != expected_foreign_keys:
        raise SchemaMigrationValidationError(
            f"{contract.name} FOREIGN KEY 不符合契约: "
            f"expected={expected_foreign_keys!r} actual={actual_foreign_keys!r}"
        )


def _index_signature(conn: Any, index_name: str) -> tuple[tuple[str, ...], bool]:
    escaped = index_name.replace("'", "''")
    rows = conn.execute(
        text(f"PRAGMA main.index_xinfo('{escaped}')")
    ).mappings().all()
    key_rows = sorted(
        (row for row in rows if int(row["key"]) == 1),
        key=lambda row: int(row["seqno"]),
    )
    auxiliary = [row for row in rows if int(row["key"]) == 0]
    valid = all(
        row["name"] is not None
        and int(row["cid"]) >= 0
        and str(row["coll"]).upper() == "BINARY"
        and int(row["desc"]) == 0
        for row in key_rows
    ) and (
        len(auxiliary) == 1
        and int(auxiliary[0]["cid"]) == -1
        and auxiliary[0]["name"] is None
        and str(auxiliary[0]["coll"]).upper() == "BINARY"
        and int(auxiliary[0]["desc"]) == 0
    )
    return tuple(str(row["name"]) for row in key_rows), valid


def _validate_indexes(conn: Any, contract: TableContract) -> None:
    rows = conn.execute(
        text(f"PRAGMA main.index_list('{contract.name}')")
    ).mappings().all()
    actual = {
        str(row["name"]): row
        for row in rows
        if str(row["origin"]) != "pk"
    }
    expected = {item.name: item for item in contract.indexes}
    if set(actual) != set(expected):
        raise SchemaMigrationValidationError(
            f"{contract.name} 索引集合不符合契约: "
            f"expected={sorted(expected)!r} actual={sorted(actual)!r}"
        )
    for name, item in expected.items():
        row = actual[name]
        columns, key_semantics_valid = _index_signature(conn, name)
        if (
            int(row["unique"]) != int(item.unique)
            or str(row["origin"]) != "c"
            or int(row["partial"]) != 0
            or columns != item.columns
            or not key_semantics_valid
        ):
            raise SchemaMigrationValidationError(
                f"{contract.name} 索引 {name} 不符合契约: "
                f"unique={int(row['unique'])} partial={int(row['partial'])} "
                f"columns={columns!r}"
            )


def _validate_table(conn: Any, contract: TableContract) -> None:
    _actual_table_name(conn, contract.name)
    _validate_columns(conn, contract)
    _validate_checks_and_auxiliary_objects(conn, contract)
    _validate_indexes(conn, contract)


def _validate_foreign_key_data(conn: Any) -> None:
    for contract in OUTBOUND_TABLE_CONTRACTS:
        if not contract.foreign_keys:
            continue
        result = conn.execute(text(
            f"PRAGMA main.foreign_key_check('{contract.name}')"
        ))
        violations = result.fetchmany(11)
        if not violations:
            continue
        sample = [tuple(row) for row in violations[:10]]
        count = "至少 11" if len(violations) > 10 else str(len(violations))
        raise SchemaMigrationValidationError(
            f"{contract.name} FOREIGN KEY 存在孤儿数据: "
            f"count={count} sample={sample!r}"
        )


def validate_outbound_delivery_schema(conn: Any) -> None:
    """验证六张出站账本表与冻结合同完全一致。"""

    _reject_temp_schema_objects(conn)
    for contract in OUTBOUND_TABLE_CONTRACTS:
        if _actual_table_name(conn, contract.name) is None:
            raise SchemaMigrationValidationError(f"{contract.name} 缺失")
        _validate_table(conn, contract)
    _validate_foreign_key_data(conn)


def _create_index(conn: Any, table_name: str, contract: IndexContract) -> None:
    unique = "UNIQUE " if contract.unique else ""
    columns = ", ".join(contract.columns)
    conn.execute(text(
        f"CREATE {unique}INDEX main.{contract.name} "
        f"ON {table_name}({columns})"
    ))


def _create_or_validate_outbound_tables(conn: Any) -> None:
    existing = {
        contract.name
        for contract in OUTBOUND_TABLE_CONTRACTS
        if _actual_table_name(conn, contract.name) is not None
    }
    for contract in OUTBOUND_TABLE_CONTRACTS:
        if contract.name in existing:
            _validate_table(conn, contract)

    for contract in OUTBOUND_TABLE_CONTRACTS:
        if contract.name in existing:
            continue
        conn.execute(text(contract.create_sql))
        for index_contract in contract.indexes:
            _create_index(conn, contract.name, index_contract)
        _validate_table(conn, contract)


_SCHEDULED_TASK_PROJECTIONS = (
    _column("last_attempt_at", "DATETIME", not_null=False),
    _column("last_success_at", "DATETIME", not_null=False),
    _column("delivery_status", "VARCHAR(48)", default="'legacy_unknown'"),
    _column(
        "last_error_summary",
        "TEXT",
        default="''",
        inline_checks=(SCHEDULED_TASK_ERROR_SUMMARY_CHECK,),
    ),
    _column("last_run_id", "INTEGER", not_null=False),
)
_PROACTIVE_PROJECTIONS = (
    _column("outbound_run_id", "INTEGER", not_null=False),
)


def _projection_column_ddl(column: ColumnContract) -> str:
    return _column_ddl(column)


def _source_table_exists(conn: Any, table_name: str) -> bool:
    rows = _main_schema_objects_named(conn, table_name)
    if not rows:
        return False
    actual = [(str(row["type"]), str(row["name"])) for row in rows]
    table_sql = rows[0].get("sql") if len(rows) == 1 else None
    if (
        actual != [("table", table_name)]
        or not isinstance(table_sql, str)
        or re.match(
            r"^\s*CREATE\s+VIRTUAL\s+TABLE\b",
            table_sql,
            flags=re.ASCII | re.IGNORECASE,
        )
    ):
        raise SchemaMigrationValidationError(
            f"{table_name} 来源投影对象必须是同名普通 main table: "
            f"{actual!r}"
        )

    expected_key = _sqlite_identifier_key(table_name)
    triggers = [
        str(row["name"])
        for row in conn.execute(text(
            "SELECT name, tbl_name FROM main.sqlite_master "
            "WHERE type = 'trigger'"
        )).mappings()
        if _sqlite_identifier_key(str(row["tbl_name"] or "")) == expected_key
    ]
    if triggers:
        raise SchemaMigrationValidationError(
            f"{table_name} 来源投影表不允许 trigger: {sorted(triggers)!r}"
        )
    return True


def _parenthesized_body(
    tokens: tuple[tuple[str, str], ...],
    open_index: int,
) -> tuple[tuple[str, str], ...]:
    if open_index >= len(tokens) or tokens[open_index] != ("symbol", "("):
        return ()
    depth = 0
    for index in range(open_index, len(tokens)):
        token = tokens[index]
        if token == ("symbol", "("):
            depth += 1
        elif token == ("symbol", ")"):
            depth -= 1
            if depth == 0:
                return tokens[open_index + 1:index]
    return ()


def _projection_references_in_expression(
    tokens: tuple[tuple[str, str], ...],
    projection_names: set[str],
) -> set[str]:
    references: set[str] = set()
    for index, (kind, value) in enumerate(tokens):
        if kind not in {"identifier", "keyword"}:
            continue
        previous_value = tokens[index - 1][1] if index > 0 else ""
        next_token = tokens[index + 1] if index + 1 < len(tokens) else None
        if previous_value in {"as", "collate"}:
            continue
        if next_token == ("symbol", "("):
            continue
        for projection_name in projection_names:
            if _sqlite_identifier_key(value) == _sqlite_identifier_key(
                projection_name
            ):
                references.add(projection_name)
    return references


def _projection_references_in_clause_expressions(
    clause: tuple[tuple[str, str], ...],
    projection_names: set[str],
) -> set[str]:
    references: set[str] = set()
    for index, (_kind, value) in enumerate(clause[:-1]):
        if value not in {"as", "check"}:
            continue
        if clause[index + 1] != ("symbol", "("):
            continue
        references.update(_projection_references_in_expression(
            _parenthesized_body(clause, index + 1),
            projection_names,
        ))
    return references


def _validate_projection_column_clauses(
    conn: Any,
    table_name: str,
    columns: tuple[ColumnContract, ...],
) -> None:
    table_sql = conn.execute(text(
        "SELECT sql FROM main.sqlite_master "
        "WHERE type = 'table' AND name = :name"
    ), {"name": table_name}).scalar_one_or_none()
    if not isinstance(table_sql, str):
        raise SchemaMigrationValidationError(
            f"{table_name} 来源投影表缺少建表 DDL"
        )
    clauses = _split_create_table_clauses(
        table_sql,
        table_name=table_name,
        strict_header=False,
    )
    projection_by_name = {column.name: column for column in columns}
    projection_names = set(projection_by_name)
    matched_names: set[str] = set()
    projection_clause_indexes: set[int] = set()
    for index, clause in enumerate(clauses):
        if not clause or clause[0][0] != "identifier":
            continue
        column = projection_by_name.get(clause[0][1])
        if column is None:
            continue
        if clause not in _column_clause_variants(column):
            raise SchemaMigrationValidationError(
                f"{table_name}.{column.name} 投影列完整定义不符合契约"
            )
        matched_names.add(column.name)
        projection_clause_indexes.add(index)

    if matched_names != projection_names:
        raise SchemaMigrationValidationError(
            f"{table_name} 投影列 DDL 集合不符合契约: "
            f"expected={sorted(projection_names)!r} "
            f"actual={sorted(matched_names)!r}"
        )

    foreign_key_references = {
        projection_name
        for row in conn.execute(
            text(f"PRAGMA main.foreign_key_list('{table_name}')")
        ).mappings()
        for projection_name in projection_names
        if _sqlite_identifier_key(str(row["from"] or ""))
        == _sqlite_identifier_key(projection_name)
    }
    if foreign_key_references:
        raise SchemaMigrationValidationError(
            f"{table_name} 不允许额外 FOREIGN KEY 引用投影列: "
            f"{sorted(foreign_key_references)!r}"
        )

    for index, clause in enumerate(clauses):
        if index in projection_clause_indexes:
            continue
        referenced = _projection_references_in_clause_expressions(
            clause,
            projection_names,
        )
        if referenced:
            raise SchemaMigrationValidationError(
                f"{table_name} 不允许额外约束引用投影列: "
                f"{sorted(referenced)!r}"
            )


def _ensure_projection_columns(
    conn: Any,
    table_name: str,
    columns: tuple[ColumnContract, ...],
) -> None:
    if not _source_table_exists(conn, table_name):
        return
    existing = {
        str(row["name"]): row
        for row in conn.execute(
            text(f"PRAGMA main.table_xinfo('{table_name}')")
        ).mappings()
    }
    for column in columns:
        aliases = [
            name
            for name in existing
            if _sqlite_identifier_key(name)
            == _sqlite_identifier_key(column.name)
        ]
        if aliases and aliases != [column.name]:
            raise SchemaMigrationValidationError(
                f"{table_name}.{column.name} 投影列名不符合契约: "
                f"{aliases!r}"
            )
        row = existing.get(column.name)
        if row is None:
            conn.execute(text(
                f"ALTER TABLE main.{table_name} "
                f"ADD COLUMN {_projection_column_ddl(column)}"
            ))
            continue
        actual = (
            str(row["type"]).upper().replace(" ", ""),
            int(row["notnull"]),
            _normalize_default(row["dflt_value"]),
            int(row["pk"]),
        )
        expected = (
            column.sqlite_type,
            column.not_null,
            _normalize_default(column.default),
            column.primary_key,
        )
        if actual != expected:
            raise SchemaMigrationValidationError(
                f"{table_name}.{column.name} 投影定义不符合契约: "
                f"expected={expected!r} actual={actual!r}"
            )
    _validate_projection_column_clauses(conn, table_name, columns)


def _ensure_projection_index(
    conn: Any,
    *,
    table_name: str,
    index_name: str,
    column_name: str,
) -> None:
    if not _source_table_exists(conn, table_name):
        return
    rows = conn.execute(
        text(f"PRAGMA main.index_list('{table_name}')")
    ).mappings().all()
    existing = next(
        (
            row
            for row in rows
            if _sqlite_identifier_key(str(row["name"]))
            == _sqlite_identifier_key(index_name)
        ),
        None,
    )
    if existing is None:
        conn.execute(text(
            f"CREATE INDEX main.{index_name} ON {table_name}({column_name})"
        ))
        return
    if str(existing["name"]) != index_name:
        raise SchemaMigrationValidationError(
            f"{table_name} 索引名称不符合投影契约: {str(existing['name'])!r}"
        )
    columns, valid = _index_signature(conn, index_name)
    if (
        int(existing["unique"]) != 0
        or str(existing["origin"]) != "c"
        or int(existing["partial"]) != 0
        or columns != (column_name,)
        or not valid
    ):
        raise SchemaMigrationValidationError(
            f"{table_name} 索引 {index_name} 不符合投影契约"
        )


def _validate_projection_indexes(
    conn: Any,
    *,
    table_name: str,
    projection_columns: tuple[ColumnContract, ...],
    expected_index_name: str,
) -> None:
    projection_names = {column.name for column in projection_columns}
    for row in conn.execute(
        text(f"PRAGMA main.index_list('{table_name}')")
    ).mappings():
        name = str(row["name"])
        columns, _valid = _index_signature(conn, name)
        if name == expected_index_name:
            continue
        references_projection = any(
            _sqlite_identifier_key(column_name)
            == _sqlite_identifier_key(projection_name)
            for column_name in columns
            for projection_name in projection_names
        )
        index_sql = conn.execute(text(
            "SELECT sql FROM main.sqlite_master "
            "WHERE type = 'index' AND name = :name"
        ), {"name": name}).scalar_one_or_none()
        if isinstance(index_sql, str):
            tokens = _tokenize_sql(index_sql)
            try:
                key_start = tokens.index(("symbol", "(")) + 1
            except ValueError:
                key_start = len(tokens)
            references_projection = references_projection or bool(
                _projection_references_in_expression(
                    tokens[key_start:],
                    projection_names,
                )
            )
        if references_projection:
            raise SchemaMigrationValidationError(
                f"{table_name} 不允许额外索引引用投影列: {name}"
            )


def _migrate_source_projections(conn: Any) -> None:
    if _source_table_exists(conn, "scheduled_tasks"):
        _ensure_projection_columns(
            conn,
            "scheduled_tasks",
            _SCHEDULED_TASK_PROJECTIONS,
        )
        conn.execute(text(
            "UPDATE main.scheduled_tasks SET "
            "last_attempt_at = last_run_at, "
            "last_success_at = NULL, "
            "delivery_status = 'legacy_unknown', "
            "last_run_id = NULL, "
            "last_error_summary = '' "
            "WHERE last_run_id IS NULL "
            "AND delivery_status = 'legacy_unknown'"
        ))
        _ensure_projection_index(
            conn,
            table_name="scheduled_tasks",
            index_name="ix_scheduled_tasks_last_run_id",
            column_name="last_run_id",
        )
        _validate_projection_indexes(
            conn,
            table_name="scheduled_tasks",
            projection_columns=_SCHEDULED_TASK_PROJECTIONS,
            expected_index_name="ix_scheduled_tasks_last_run_id",
        )
        violation_count = conn.execute(text(
            "SELECT COUNT(*) FROM main.scheduled_tasks "
            "WHERE last_run_id IS NULL "
            "AND delivery_status = 'legacy_unknown' "
            "AND NOT (last_attempt_at IS last_run_at "
            "AND last_success_at IS NULL "
            "AND last_error_summary = '')"
        )).scalar_one()
        if violation_count:
            raise SchemaMigrationValidationError(
                "scheduled_tasks 来源投影回填不变量不成立: "
                f"count={int(violation_count)}"
            )

    if _source_table_exists(conn, "proactive_outreach_log"):
        _ensure_projection_columns(
            conn,
            "proactive_outreach_log",
            _PROACTIVE_PROJECTIONS,
        )
        conn.execute(text(
            "UPDATE main.proactive_outreach_log "
            "SET status = 'legacy_ambiguous_hold', outbound_run_id = NULL "
            "WHERE status IN ('sending', 'ambiguous') "
            "AND NOT EXISTS ("
            "SELECT 1 FROM main.outbound_runs "
            "WHERE outbound_runs.id = proactive_outreach_log.outbound_run_id "
            "AND outbound_runs.source_type = 'proactive_outreach' "
            "AND outbound_runs.source_id = CAST(proactive_outreach_log.id AS TEXT)"
            ")"
        ))
        _ensure_projection_index(
            conn,
            table_name="proactive_outreach_log",
            index_name="ix_proactive_outreach_log_outbound_run_id",
            column_name="outbound_run_id",
        )
        _validate_projection_indexes(
            conn,
            table_name="proactive_outreach_log",
            projection_columns=_PROACTIVE_PROJECTIONS,
            expected_index_name="ix_proactive_outreach_log_outbound_run_id",
        )
        violation_count = conn.execute(text(
            "SELECT COUNT(*) FROM main.proactive_outreach_log "
            "WHERE status IN ('sending', 'ambiguous') "
            "AND NOT EXISTS ("
            "SELECT 1 FROM main.outbound_runs "
            "WHERE outbound_runs.id = proactive_outreach_log.outbound_run_id "
            "AND outbound_runs.source_type = 'proactive_outreach' "
            "AND outbound_runs.source_id = CAST(proactive_outreach_log.id AS TEXT)"
            ")"
        )).scalar_one()
        if violation_count:
            raise SchemaMigrationValidationError(
                "proactive_outreach_log 来源投影回填不变量不成立: "
                f"count={int(violation_count)}"
            )


def _initialize_delivery_controls(conn: Any) -> None:
    for source_type in OUTBOUND_SOURCE_TYPES:
        conn.execute(text(
            "INSERT OR IGNORE INTO main.outbound_delivery_controls (source_type) "
            "VALUES (:source_type)"
        ), {"source_type": source_type})


def create_outbound_delivery_schema(
    conn: Any,
    _engine: Any,
    _db_path: str | None,
) -> None:
    """创建六表、迁移兼容投影，并拒绝任何同名结构漂移。"""

    _reject_temp_schema_objects(conn)
    _create_or_validate_outbound_tables(conn)
    _migrate_source_projections(conn)
    _initialize_delivery_controls(conn)
    validate_outbound_delivery_schema(conn)


def outbound_delivery_schema_needs_backup(conn: Any) -> bool:
    """判断本迁移是否会改写已有来源表或其历史行。"""

    _reject_temp_schema_objects(conn)
    return any(
        _source_table_exists(conn, table_name)
        for table_name in ("scheduled_tasks", "proactive_outreach_log")
    )

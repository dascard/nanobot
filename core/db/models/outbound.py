"""通用主动投递运行、尝试、队列与熔断模型。"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)

from core.db.base import Base
from core.outbound_delivery_schema import (
    OUTBOUND_CIRCUIT_CHECKS,
    OUTBOUND_CONTROL_CHECKS,
    OUTBOUND_DELIVERY_ATTEMPT_CHECKS,
    OUTBOUND_GENERATION_ATTEMPT_CHECKS,
    OUTBOUND_OUTBOX_CHECKS,
    OUTBOUND_RUN_CHECKS,
)


def _outbound_checks(items):
    return tuple(CheckConstraint(expression, name=name) for name, expression in items)


class OutboundRun(Base):
    """一次确定 occurrence 的生成与投递运行。"""

    __tablename__ = "outbound_runs"
    __table_args__ = (
        *_outbound_checks(OUTBOUND_RUN_CHECKS),
        Index(
            "uq_outbound_run_occurrence",
            "source_type",
            "source_id",
            "occurrence_key",
            unique=True,
        ),
        Index(
            "ix_outbound_run_source",
            "source_type",
            "source_id",
            "status",
        ),
        Index(
            "ix_outbound_run_claim_lease",
            "status",
            "claim_expires_at",
        ),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(String(32), nullable=False)
    source_id = Column(String(255), nullable=False)
    occurrence_key = Column(String(255), nullable=False)
    source_revision = Column(String(128), nullable=False)
    source_snapshot_json = Column(Text, nullable=False)
    source_snapshot_sha256 = Column(String(64), nullable=False)
    delivery_contract_json = Column(Text, nullable=False)
    delivery_contract_sha256 = Column(String(64), nullable=False)
    writer_owner = Column(String(128), nullable=False)
    writer_token = Column(String(64), nullable=False)
    writer_protocol_version = Column(Integer, nullable=False)
    task_kind = Column(String(64), nullable=False)
    scheduled_for = Column(DateTime, nullable=True)
    trigger_type = Column(String(32), nullable=False)
    status = Column(
        String(48),
        nullable=False,
        default="claimed",
        server_default=text("'claimed'"),
    )
    claim_owner = Column(String(128), nullable=True)
    claim_token = Column(String(64), nullable=True)
    claim_expires_at = Column(DateTime, nullable=True)
    attempted_at = Column(DateTime, nullable=True)
    generated_at = Column(DateTime, nullable=True)
    succeeded_at = Column(DateTime, nullable=True)
    failure_type = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    failure_summary = Column(
        Text,
        nullable=False,
        default="",
        server_default=text("''"),
    )
    active_outbox_id = Column(Integer, nullable=True)
    has_ambiguous_ancestor = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("0"),
    )
    delivery_mode = Column(String(24), nullable=False)
    cutover_epoch = Column(Integer, nullable=False)
    created_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class OutboundGenerationAttempt(Base):
    """一次不可变的正文生成模型调用记录。"""

    __tablename__ = "outbound_generation_attempts"
    __table_args__ = (
        *_outbound_checks(OUTBOUND_GENERATION_ATTEMPT_CHECKS),
        Index(
            "uq_outbound_generation_attempt",
            "run_id",
            "attempt_no",
            unique=True,
        ),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("outbound_runs.id"), nullable=False)
    attempt_no = Column(Integer, nullable=False)
    owner = Column(String(128), nullable=False)
    fencing_token = Column(String(64), nullable=False)
    status = Column(
        String(32),
        nullable=False,
        default="started",
        server_default=text("'started'"),
    )
    started_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    completed_at = Column(DateTime, nullable=True)
    model_trace_id = Column(
        String(128),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    content_sha256 = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    error_type = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    error_summary = Column(
        Text,
        nullable=False,
        default="",
        server_default=text("''"),
    )
    created_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class OutboundDeliveryOutbox(Base):
    """不可变 payload 与目标快照的通用主动投递队列。"""

    __tablename__ = "outbound_delivery_outbox"
    __table_args__ = (
        *_outbound_checks(OUTBOUND_OUTBOX_CHECKS),
        Index(
            "uq_outbound_delivery_idempotency_key",
            "idempotency_key",
            unique=True,
        ),
        Index(
            "uq_outbound_delivery_replay_leaf",
            "run_id",
            "destination_fingerprint",
            "replay_sequence",
            unique=True,
        ),
        Index(
            "ix_outbound_delivery_due",
            "status",
            "next_attempt_at",
        ),
        Index(
            "ix_outbound_delivery_lease",
            "status",
            "lease_expires_at",
        ),
        Index(
            "ix_outbound_delivery_run_status",
            "run_id",
            "status",
        ),
        Index(
            "ix_outbound_delivery_replay_parent",
            "replay_of_outbox_id",
        ),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("outbound_runs.id"), nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    destination_snapshot_json = Column(Text, nullable=False)
    destination_fingerprint = Column(String(64), nullable=False)
    target_type = Column(String(16), nullable=False)
    endpoint_key = Column(String(64), nullable=False)
    payload_json = Column(Text, nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    status = Column(
        String(24),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    lease_owner = Column(String(128), nullable=True)
    lease_token = Column(String(64), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    next_attempt_at = Column(DateTime, nullable=True)
    allocated_attempt_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    request_started_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    max_attempts = Column(Integer, nullable=False)
    retry_deadline_at = Column(DateTime, nullable=False)
    last_error_type = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    last_error_summary = Column(
        Text,
        nullable=False,
        default="",
        server_default=text("''"),
    )
    delivered_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    cancel_reason_type = Column(String(64), nullable=True)
    replay_of_outbox_id = Column(
        Integer,
        ForeignKey("outbound_delivery_outbox.id"),
        nullable=True,
    )
    replay_sequence = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    replay_request_sha256 = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    cutover_epoch = Column(Integer, nullable=False)
    endpoint_config_revision = Column(String(128), nullable=False)
    payload_contract_fingerprint = Column(String(64), nullable=False)
    created_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class OutboundDeliveryAttempt(Base):
    """一次实际 HTTP 投递尝试的不可变审计记录。"""

    __tablename__ = "outbound_delivery_attempts"
    __table_args__ = (
        *_outbound_checks(OUTBOUND_DELIVERY_ATTEMPT_CHECKS),
        Index(
            "uq_outbound_delivery_attempt",
            "outbox_id",
            "attempt_no",
            unique=True,
        ),
        Index(
            "ix_outbound_delivery_attempt_status_started",
            "status",
            "started_at",
        ),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    outbox_id = Column(
        Integer,
        ForeignKey("outbound_delivery_outbox.id"),
        nullable=False,
    )
    attempt_no = Column(Integer, nullable=False)
    worker_owner = Column(String(128), nullable=False)
    lease_token = Column(String(64), nullable=False)
    status = Column(
        String(32),
        nullable=False,
        default="started",
        server_default=text("'started'"),
    )
    transport_phase = Column(
        String(32),
        nullable=False,
        default="allocated",
        server_default=text("'allocated'"),
    )
    request_started = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("0"),
    )
    endpoint_config_revision = Column(String(128), nullable=False)
    http_status = Column(Integer, nullable=True)
    result_category = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    error_type = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    safe_summary = Column(
        Text,
        nullable=False,
        default="",
        server_default=text("''"),
    )
    duration_ms = Column(Integer, nullable=True)
    settlement_retry_at = Column(DateTime, nullable=True)
    settlement_circuit_scope_type = Column(String(32), nullable=True)
    settlement_request_sha256 = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    started_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    request_started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class OutboundDeliveryCircuit(Base):
    """跨进程、按配置 revision 隔离的稳定失败熔断状态。"""

    __tablename__ = "outbound_delivery_circuits"
    __table_args__ = (
        *_outbound_checks(OUTBOUND_CIRCUIT_CHECKS),
        Index(
            "uq_outbound_delivery_circuit_scope",
            "scope_type",
            "scope_fingerprint",
            "config_revision",
            unique=True,
        ),
        Index("ix_outbound_delivery_circuit_status", "status"),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope_type = Column(String(32), nullable=False)
    scope_fingerprint = Column(String(64), nullable=False)
    config_revision = Column(String(128), nullable=False)
    status = Column(
        String(16),
        nullable=False,
        default="closed",
        server_default=text("'closed'"),
    )
    reason_type = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    opened_at = Column(DateTime, nullable=True)
    opened_by_attempt_id = Column(
        Integer,
        ForeignKey("outbound_delivery_attempts.id"),
        nullable=True,
    )
    created_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class OutboundDeliveryControl(Base):
    """每个 producer source 的持久 cutover 控制行。"""

    __tablename__ = "outbound_delivery_controls"
    __table_args__ = (
        *_outbound_checks(OUTBOUND_CONTROL_CHECKS),
        Index(
            "ix_outbound_delivery_control_mode_effective",
            "mode",
            "effective_from",
        ),
    )

    source_type = Column(String(32), primary_key=True, nullable=False)
    mode = Column(
        String(24),
        nullable=False,
        default="legacy_direct",
        server_default=text("'legacy_direct'"),
    )
    cutover_epoch = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    effective_from = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    protocol_version = Column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    writer_version = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    writer_owner = Column(String(128), nullable=True)
    writer_token = Column(String(64), nullable=True)
    writer_lease_expires_at = Column(DateTime, nullable=True)
    created_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


__all__ = [
    "OutboundDeliveryAttempt",
    "OutboundDeliveryCircuit",
    "OutboundDeliveryControl",
    "OutboundDeliveryOutbox",
    "OutboundGenerationAttempt",
    "OutboundRun",
]

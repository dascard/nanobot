"""Run Checkpoint、恢复 lineage 与副作用回执 ORM 模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)

from core.db.base import Base


class RunCheckpointRow(Base):
    """受 owner ACL 保护的不可变恢复状态；正文不进入证据导出清单。"""

    __tablename__ = "run_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "sequence",
            name="uq_run_checkpoint_sequence",
        ),
        CheckConstraint(
            "sequence > 0",
            name="ck_run_checkpoint_sequence_positive",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_run_checkpoint_schema_version_positive",
        ),
        CheckConstraint(
            "payload_size_bytes >= 0",
            name="ck_run_checkpoint_payload_size_nonnegative",
        ),
        CheckConstraint(
            "ledger_sequence > 0",
            name="ck_run_checkpoint_ledger_sequence_positive",
        ),
        CheckConstraint(
            "side_effect_frontier >= 0",
            name="ck_run_checkpoint_effect_frontier_nonnegative",
        ),
        CheckConstraint(
            "boundary IN ("
            "'turn_started', 'plan_resolved', 'tool_ready', "
            "'tool_completed', 'tool_ambiguous', 'turn_completed', "
            "'restored'"
            ")",
            name="ck_run_checkpoint_boundary",
        ),
        Index("ix_run_checkpoint_run_created", "run_id", "created_at"),
        Index("ix_run_checkpoint_owner", "owner_platform", "owner_type", "owner_id"),
    )

    checkpoint_id = Column(String(160), primary_key=True)
    run_id = Column(String(160), nullable=False, index=True)
    sequence = Column(Integer, nullable=False)
    schema_version = Column(Integer, nullable=False, default=1)
    boundary = Column(String(32), nullable=False, index=True)
    parent_checkpoint_id = Column(String(160), nullable=False, default="", index=True)

    turn_id = Column(String(160), nullable=False)
    correlation_id = Column(String(160), nullable=False)
    actor_type = Column(String(64), nullable=False)
    actor_id = Column(String(160), nullable=False)
    parent_actor_id = Column(String(160), nullable=False, default="")
    owner_platform = Column(String(64), nullable=False, index=True)
    owner_type = Column(String(64), nullable=False, index=True)
    owner_id = Column(String(160), nullable=False, index=True)

    runtime_id = Column(String(160), nullable=False)
    runtime_protocol_version = Column(String(32), nullable=False)
    resumable = Column(Boolean, nullable=False, default=True)
    model_step = Column(Integer, nullable=False, default=0, server_default=text("0"))
    tool_round = Column(Integer, nullable=False, default=0, server_default=text("0"))
    side_effect_frontier = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    manifest_sha256 = Column(String(64), nullable=False, default="")
    prompt_sha256 = Column(String(64), nullable=False, default="")
    model_route_sha256 = Column(String(64), nullable=False, default="")
    tool_plan_sha256 = Column(String(64), nullable=False, default="")
    workspace_sha256 = Column(String(64), nullable=False, default="")
    artifact_set_sha256 = Column(String(64), nullable=False, default="")
    security_sha256 = Column(String(64), nullable=False, default="")
    version_proofs_sha256 = Column(String(64), nullable=False)
    file_proofs_sha256 = Column(String(64), nullable=False)
    artifact_proofs_sha256 = Column(String(64), nullable=False)

    payload_encoding = Column(String(32), nullable=False, default="json+gzip")
    payload_blob = Column(LargeBinary, nullable=False)
    payload_size_bytes = Column(Integer, nullable=False)
    payload_sha256 = Column(String(64), nullable=False, index=True)
    state_sha256 = Column(String(64), nullable=False)

    ledger_sequence = Column(Integer, nullable=False)
    ledger_event_sha256 = Column(String(64), nullable=False)
    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class RunSideEffectReceipt(Base):
    """工具副作用的 prepare/terminal 协调状态；原始参数和结果只保存摘要。"""

    __tablename__ = "run_side_effect_receipts"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "tool_call_id",
            name="uq_run_side_effect_tool_call",
        ),
        UniqueConstraint(
            "run_id",
            "idempotency_key_sha256",
            name="uq_run_side_effect_idempotency",
        ),
        CheckConstraint(
            "effect_class IN ('local_write', 'external')",
            name="ck_run_side_effect_class",
        ),
        CheckConstraint(
            "state IN ('prepared', 'completed', 'failed', 'ambiguous')",
            name="ck_run_side_effect_state",
        ),
        CheckConstraint(
            "result_size_bytes >= 0",
            name="ck_run_side_effect_result_size_nonnegative",
        ),
        Index("ix_run_side_effect_run_state", "run_id", "state"),
        Index("ix_run_side_effect_prepared", "prepared_at"),
    )

    receipt_id = Column(String(160), primary_key=True)
    run_id = Column(String(160), nullable=False, index=True)
    tool_call_id = Column(String(160), nullable=False, index=True)
    tool_name = Column(String(128), nullable=False, index=True)
    execution_port_id = Column(String(160), nullable=False)
    effect_class = Column(String(32), nullable=False)
    state = Column(String(32), nullable=False, default="prepared", index=True)
    idempotency_key_sha256 = Column(String(64), nullable=False)
    request_sha256 = Column(String(64), nullable=False)
    result_sha256 = Column(String(64), nullable=False, default="")
    result_size_bytes = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    error_code = Column(String(128), nullable=False, default="")
    checkpoint_before_id = Column(String(160), nullable=False, index=True)
    checkpoint_after_id = Column(String(160), nullable=False, default="", index=True)
    file_proofs_json = Column(Text, nullable=False, default="[]", server_default=text("'[]'"))
    artifact_proofs_json = Column(Text, nullable=False, default="[]", server_default=text("'[]'"))
    prepared_ledger_sequence = Column(Integer, nullable=False)
    terminal_ledger_sequence = Column(Integer, nullable=True)
    prepared_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    settled_at = Column(DateTime, nullable=True)


class RunRecoveryOperation(Base):
    """幂等的 Resume/Fork/Rewind 子 Run 控制记录。"""

    __tablename__ = "run_recovery_operations"
    __table_args__ = (
        UniqueConstraint(
            "request_id_sha256",
            name="uq_run_recovery_request",
        ),
        UniqueConstraint(
            "run_id",
            name="uq_run_recovery_child_run",
        ),
        CheckConstraint(
            "operation_kind IN ('resume', 'fork', 'rewind')",
            name="ck_run_recovery_operation_kind",
        ),
        CheckConstraint(
            "status IN ("
            "'prepared', 'running', 'succeeded', 'failed', "
            "'cancelled', 'timed_out', 'ambiguous'"
            ")",
            name="ck_run_recovery_status",
        ),
        Index("ix_run_recovery_status_updated", "status", "updated_at"),
        Index("ix_run_recovery_owner", "owner_platform", "owner_type", "owner_id"),
    )

    operation_id = Column(String(160), primary_key=True)
    request_id_sha256 = Column(String(64), nullable=False)
    request_fingerprint_sha256 = Column(String(64), nullable=False)
    operation_kind = Column(String(32), nullable=False)
    # 恢复操作属于新建的子 Run；source 只保留摘要，便于源 Run 受控删除。
    run_id = Column(String(160), nullable=False, index=True)
    restored_checkpoint_id = Column(String(160), nullable=False, index=True)
    source_run_id_sha256 = Column(String(64), nullable=False, index=True)
    source_checkpoint_id_sha256 = Column(String(64), nullable=False)
    source_checkpoint_sha256 = Column(String(64), nullable=False)
    source_head_sequence = Column(Integer, nullable=False)
    source_head_sha256 = Column(String(64), nullable=False)
    owner_platform = Column(String(64), nullable=False, index=True)
    owner_type = Column(String(64), nullable=False, index=True)
    owner_id = Column(String(160), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="prepared", index=True)
    error_code = Column(String(128), nullable=False, default="")
    prepared_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    finished_at = Column(DateTime, nullable=True)


__all__ = [
    "RunCheckpointRow",
    "RunRecoveryOperation",
    "RunSideEffectReceipt",
]

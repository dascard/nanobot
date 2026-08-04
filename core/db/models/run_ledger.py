"""Run Event Ledger shadow 阶段的 ORM 持久模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)

from core.db.base import Base


class RunLedgerEventRow(Base):
    """只追加的运行事实；业务代码不得执行 UPDATE 或 DELETE。"""

    __tablename__ = "run_ledger_events"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "sequence",
            name="uq_run_ledger_run_sequence",
        ),
        CheckConstraint(
            "sequence > 0",
            name="ck_run_ledger_sequence_positive",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_run_ledger_schema_version_positive",
        ),
        CheckConstraint(
            "dropped_field_count >= 0",
            name="ck_run_ledger_dropped_fields_nonnegative",
        ),
        Index(
            "ix_run_ledger_run_time",
            "run_id",
            "occurred_at",
        ),
        Index(
            "ix_run_ledger_type_time",
            "event_type",
            "occurred_at",
        ),
        Index(
            "ix_run_ledger_session_time",
            "session_id",
            "occurred_at",
        ),
        Index(
            "ix_run_ledger_task_run_time",
            "task_run_id",
            "occurred_at",
        ),
    )

    position = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(160), nullable=False, unique=True, index=True)
    run_id = Column(String(160), nullable=False, index=True)
    sequence = Column(Integer, nullable=False)
    event_type = Column(String(128), nullable=False, index=True)
    schema_name = Column(String(64), nullable=False)
    schema_version = Column(Integer, nullable=False)
    occurred_at = Column(DateTime, nullable=False, index=True)
    recorded_at = Column(DateTime, nullable=False, default=datetime.now, index=True)
    source = Column(String(128), nullable=False, index=True)
    source_event_id = Column(String(160), nullable=False, default="", index=True)
    source_sequence = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    request_id = Column(String(160), nullable=False, default="", index=True)
    session_id = Column(String(160), nullable=False, default="", index=True)
    turn_id = Column(String(160), nullable=False, default="", index=True)
    trace_id = Column(String(160), nullable=False, default="", index=True)
    task_id = Column(String(160), nullable=False, default="", index=True)
    task_run_id = Column(String(160), nullable=False, default="", index=True)
    job_id = Column(String(160), nullable=False, default="", index=True)
    tool_call_id = Column(String(160), nullable=False, default="", index=True)
    delivery_id = Column(String(160), nullable=False, default="", index=True)
    parent_job_id = Column(String(160), nullable=False, default="", index=True)

    actor_type = Column(String(64), nullable=False, default="")
    actor_id = Column(String(160), nullable=False, default="", index=True)
    parent_actor_id = Column(String(160), nullable=False, default="")
    owner_platform = Column(String(64), nullable=False, default="", index=True)
    owner_type = Column(String(64), nullable=False, default="", index=True)
    owner_id = Column(String(160), nullable=False, default="", index=True)

    status = Column(String(64), nullable=False, default="", index=True)
    payload_json = Column(
        Text,
        nullable=False,
        default="{}",
        server_default=text("'{}'"),
    )
    payload_sha256 = Column(String(64), nullable=False)
    dropped_field_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    correction_of_event_id = Column(
        String(160),
        nullable=False,
        default="",
        index=True,
    )
    previous_event_sha256 = Column(String(64), nullable=False, default="")
    event_sha256 = Column(String(64), nullable=False, unique=True, index=True)


class RunLedgerStreamHead(Base):
    """每 Run 的可变协调头；它不是运行事实，只用于条件追加。"""

    __tablename__ = "run_ledger_stream_heads"
    __table_args__ = (
        CheckConstraint(
            "last_sequence >= 0",
            name="ck_run_ledger_head_sequence_nonnegative",
        ),
        CheckConstraint(
            "terminal_sequence IS NULL OR terminal_sequence > 0",
            name="ck_run_ledger_head_terminal_positive",
        ),
    )

    run_id = Column(String(160), primary_key=True)
    last_sequence = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    last_event_id = Column(String(160), nullable=False, default="")
    last_event_sha256 = Column(String(64), nullable=False, default="")
    terminal_sequence = Column(Integer, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, index=True)


__all__ = ["RunLedgerEventRow", "RunLedgerStreamHead"]

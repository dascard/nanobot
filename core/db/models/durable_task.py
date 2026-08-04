"""Agent Run 的通用 Durable Task 执行租约模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    text,
)

from core.db.base import Base


class RunTaskControl(Base):
    """只承载 Agent Run 的公共执行控制，不复制领域任务状态机。"""

    __tablename__ = "run_task_controls"
    __table_args__ = (
        CheckConstraint(
            "task_kind IN ("
            "'chat','scheduled','proactive','research','background','recovery'"
            ")",
            name="ck_run_task_kind",
        ),
        CheckConstraint(
            "status IN ("
            "'accepted','running','succeeded','failed','cancelled',"
            "'timed_out','ambiguous'"
            ")",
            name="ck_run_task_status",
        ),
        CheckConstraint(
            "lease_generation >= 0",
            name="ck_run_task_lease_generation_nonnegative",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_run_task_attempt_count_nonnegative",
        ),
        CheckConstraint(
            "(status = 'running' AND lease_owner <> '' "
            "AND lease_token <> '' AND lease_expires_at IS NOT NULL "
            "AND lease_generation > 0 AND attempt_count > 0) "
            "OR (status <> 'running' AND lease_owner = '' "
            "AND lease_token = '' AND lease_expires_at IS NULL)",
            name="ck_run_task_active_lease_shape",
        ),
        Index("ix_run_task_status_lease", "status", "lease_expires_at"),
        Index("ix_run_task_timeout", "status", "timeout_at"),
        Index("ix_run_task_source", "source_type", "source_id"),
        Index("ix_run_task_request", "request_id_sha256"),
    )

    run_id = Column(String(160), primary_key=True)
    task_kind = Column(String(32), nullable=False, index=True)
    source_type = Column(String(64), nullable=False, default="", server_default=text("''"))
    source_id = Column(String(160), nullable=False, default="", server_default=text("''"))
    request_id_sha256 = Column(String(64), nullable=False, index=True)
    idempotency_key_sha256 = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, index=True)

    lease_owner = Column(String(128), nullable=False, default="", server_default=text("''"))
    lease_token = Column(String(64), nullable=False, default="", server_default=text("''"))
    lease_generation = Column(Integer, nullable=False, default=0, server_default=text("0"))
    attempt_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    lease_expires_at = Column(DateTime, nullable=True)
    timeout_at = Column(DateTime, nullable=True)

    cancel_requested_at = Column(DateTime, nullable=True)
    cancel_reason = Column(String(128), nullable=False, default="", server_default=text("''"))
    terminal_reason = Column(String(128), nullable=False, default="", server_default=text("''"))
    result_ref = Column(String(512), nullable=False, default="", server_default=text("''"))
    delivery_receipt_ref = Column(
        String(512),
        nullable=False,
        default="",
        server_default=text("''"),
    )

    created_at = Column(
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


__all__ = ["RunTaskControl"]

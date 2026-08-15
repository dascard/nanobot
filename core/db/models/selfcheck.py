"""自检运行、检查结果与跨进程 Worker 心跳事实。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)

from core.db.base import Base


class SelfcheckRunRow(Base):
    """一次不可变检查范围及其最终汇总。"""

    __tablename__ = "selfcheck_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running','passed','degraded','failed','inconclusive')",
            name="ck_selfcheck_run_status",
        ),
        Index("ix_selfcheck_run_started", "started_at", "status"),
    )

    run_id = Column(String(64), primary_key=True)
    trigger = Column(String(32), nullable=False)
    environment = Column(String(32), nullable=False)
    status = Column(
        String(24),
        nullable=False,
        default="running",
        server_default=text("'running'"),
    )
    requested_by = Column(
        String(128), nullable=False, default="", server_default=text("''")
    )
    capability_registry_sha256 = Column(String(64), nullable=False)
    probe_registry_sha256 = Column(String(64), nullable=False)
    selected_check_ids_json = Column(
        Text, nullable=False, default="[]", server_default=text("'[]'")
    )
    summary_json = Column(
        Text, nullable=False, default="{}", server_default=text("'{}'")
    )
    started_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    completed_at = Column(DateTime, nullable=True)


class SelfcheckResultRow(Base):
    """一项 Probe 的结构化结果；不保存业务正文或秘密。"""

    __tablename__ = "selfcheck_results"
    __table_args__ = (
        CheckConstraint(
            "status IN ('passed','degraded','failed','inconclusive','skipped')",
            name="ck_selfcheck_result_status",
        ),
        CheckConstraint("duration_ms >= 0", name="ck_selfcheck_duration"),
        UniqueConstraint(
            "run_id", "check_id", name="uq_selfcheck_result_run_check"
        ),
        Index("ix_selfcheck_result_check_time", "check_id", "completed_at"),
        Index("ix_selfcheck_result_status_time", "status", "completed_at"),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        String(64),
        ForeignKey("selfcheck_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    check_id = Column(String(64), nullable=False)
    category = Column(String(32), nullable=False)
    status = Column(String(24), nullable=False)
    severity = Column(String(16), nullable=False)
    duration_ms = Column(Integer, nullable=False, default=0, server_default=text("0"))
    detail_code = Column(String(128), nullable=False)
    message = Column(
        String(512), nullable=False, default="", server_default=text("''")
    )
    capability_ids_json = Column(
        Text, nullable=False, default="[]", server_default=text("'[]'")
    )
    metrics_json = Column(
        Text, nullable=False, default="{}", server_default=text("'{}'")
    )
    evidence_json = Column(
        Text, nullable=False, default="{}", server_default=text("'{}'")
    )
    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=False)


class WorkerHeartbeat(Base):
    """每个逻辑 Worker 的最新跨进程活性与循环计数。"""

    __tablename__ = "worker_heartbeats"
    __table_args__ = (
        CheckConstraint(
            "state IN ('running','stopped','failed')",
            name="ck_worker_heartbeat_state",
        ),
        CheckConstraint(
            "cycle_count >= 0 AND success_count >= 0 AND failure_count >= 0",
            name="ck_worker_heartbeat_counts",
        ),
        Index("ix_worker_heartbeat_seen", "last_seen_at", "state"),
    )

    worker_id = Column(String(64), primary_key=True)
    instance_id = Column(String(128), nullable=False)
    mode = Column(String(32), nullable=False)
    state = Column(
        String(16),
        nullable=False,
        default="running",
        server_default=text("'running'"),
    )
    cycle_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    success_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    failure_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    started_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, nullable=False)
    last_success_at = Column(DateTime, nullable=True)
    last_error_at = Column(DateTime, nullable=True)
    last_error_code = Column(
        String(128), nullable=False, default="", server_default=text("''")
    )
    metadata_json = Column(
        Text, nullable=False, default="{}", server_default=text("'{}'")
    )


__all__ = ["SelfcheckResultRow", "SelfcheckRunRow", "WorkerHeartbeat"]

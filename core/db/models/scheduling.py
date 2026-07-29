"""计划任务持久化模型。"""

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
    text,
)

from core.db.base import Base
from core.outbound_delivery_schema import SCHEDULED_TASK_ERROR_SUMMARY_CHECK


SCHEDULED_TASK_EXECUTION_STATUSES = frozenset(
    {
        "pending",
        "running",
        "waiting",
        "succeeded",
        "failed",
        "blocked",
        "ambiguous",
    }
)
SCHEDULED_TASK_STEP_ATTEMPT_STATUSES = frozenset(
    {"started", "succeeded", "failed", "blocked", "ambiguous"}
)
SCHEDULED_TASK_PROGRAM_OPERATIONS = frozenset(
    {"set", "tool", "model", "branch", "loop", "wait", "emit"}
)


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String, index=True)
    cron_expr = Column(String)
    # schedule 规格(core/schedule_spec.py):kind + 规范化 JSON;
    # 旧行两者为空,按 cron_expr 回退解释。
    schedule_kind = Column(
        String(16),
        nullable=False,
        default="cron",
        server_default=text("'cron'"),
    )
    schedule_spec = Column(
        Text,
        nullable=False,
        default="",
        server_default=text("''"),
    )
    # 预计算的下一次触发时刻,UTC naive(与 outbox scheduled_for 一致);
    # NULL 表示由调度器懒初始化。
    next_fire_at = Column(DateTime, nullable=True)
    target_type = Column(String, default="private")
    target_id = Column(String)
    prompt_template = Column(Text)
    # versioned program 是新执行器的唯一语义事实源；prompt_template 仅保留
    # 旧 model -> emit 任务的兼容投影。
    program_json = Column(
        Text,
        nullable=False,
        default="",
        server_default=text("''"),
    )
    program_sha256 = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    # 安全 owner 是访问控制与执行身份的事实源；target 只描述投递目标。
    owner_chat_stream_id = Column(
        String(512),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    owner_platform = Column(
        String(32),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    owner_chat_type = Column(
        String(16),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    owner_session_id = Column(
        String(512),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    created_by_actor_id = Column(
        String(255),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    owner_migration_required = Column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    definition_version = Column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    enabled = Column(Integer, default=1)
    last_run_at = Column(DateTime, nullable=True)
    last_attempt_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    delivery_status = Column(
        String(48),
        nullable=False,
        default="legacy_unknown",
        server_default=text("'legacy_unknown'"),
    )
    last_error_summary = Column(
        Text,
        CheckConstraint(
            SCHEDULED_TASK_ERROR_SUMMARY_CHECK[1],
            name=SCHEDULED_TASK_ERROR_SUMMARY_CHECK[0],
        ),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    last_run_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        Index("ix_scheduled_tasks_last_run_id", "last_run_id"),
        Index(
            "ix_scheduled_tasks_owner",
            "owner_chat_stream_id",
            "id",
        ),
        Index(
            "ix_scheduled_tasks_owner_enabled",
            "owner_chat_stream_id",
            "enabled",
        ),
        Index(
            "ix_scheduled_tasks_enabled_next_fire_at",
            "enabled",
            "next_fire_at",
        ),
    )


class ScheduledTaskExecution(Base):
    """一次冻结定义、可租约领取和逐步恢复的任务执行实例。"""

    __tablename__ = "scheduled_task_executions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, nullable=False)
    task_version = Column(Integer, nullable=False)
    owner_chat_stream_id = Column(String(512), nullable=False)
    occurrence_key = Column(String(255), nullable=False)
    trigger_type = Column(String(32), nullable=False)
    scheduled_for = Column(DateTime, nullable=False)
    task_snapshot_json = Column(Text, nullable=False)
    task_snapshot_sha256 = Column(String(64), nullable=False)
    owner_snapshot_json = Column(Text, nullable=False)
    trigger_snapshot_json = Column(Text, nullable=False)
    program_snapshot_json = Column(Text, nullable=False)
    program_snapshot_sha256 = Column(String(64), nullable=False)
    state_json = Column(
        Text,
        nullable=False,
        default="{}",
        server_default=text("'{}'"),
    )
    current_step_id = Column(
        String(255),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    status = Column(
        String(24),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    lease_owner = Column(String(128), nullable=True)
    lease_token = Column(String(64), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    wake_at = Column(DateTime, nullable=True)
    agent_trace_id = Column(
        String(128),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    agent_run_id = Column(
        String(128),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    outbound_run_id = Column(Integer, nullable=True)
    last_error_code = Column(
        String(128),
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
        onupdate=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'pending','running','waiting','succeeded','failed',"
            "'blocked','ambiguous')",
            name="ck_scheduled_task_execution_status",
        ),
        Index(
            "uq_scheduled_task_execution_occurrence",
            "task_id",
            "occurrence_key",
            unique=True,
        ),
        Index(
            "ix_scheduled_task_execution_claim",
            "status",
            "wake_at",
            "lease_expires_at",
        ),
        Index(
            "ix_scheduled_task_execution_owner",
            "owner_chat_stream_id",
            "status",
        ),
        {"sqlite_autoincrement": True},
    )


class ScheduledTaskOwnerLease(Base):
    """同一会话 owner 的持久互斥租约。"""

    __tablename__ = "scheduled_task_owner_leases"

    owner_chat_stream_id = Column(String(512), primary_key=True)
    execution_id = Column(Integer, nullable=False)
    lease_owner = Column(String(128), nullable=False)
    lease_token = Column(String(64), nullable=False)
    lease_expires_at = Column(DateTime, nullable=False)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        Index(
            "ix_scheduled_task_owner_lease_expiry",
            "lease_expires_at",
        ),
    )


class ScheduledTaskStepAttempt(Base):
    """一个运行时步骤的一次不可变尝试及其成功检查点。"""

    __tablename__ = "scheduled_task_step_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(
        Integer,
        ForeignKey("scheduled_task_executions.id"),
        nullable=False,
    )
    step_id = Column(String(255), nullable=False)
    attempt_no = Column(Integer, nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    operation = Column(String(16), nullable=False)
    status = Column(
        String(24),
        nullable=False,
        default="started",
        server_default=text("'started'"),
    )
    input_sha256 = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    output_sha256 = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    tool_call_id = Column(
        String(128),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    model_trace_id = Column(
        String(128),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    checkpoint_json = Column(
        Text,
        nullable=False,
        default="",
        server_default=text("''"),
    )
    error_type = Column(
        String(128),
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
    started_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "operation IN ("
            "'set','tool','model','branch','loop','wait','emit')",
            name="ck_scheduled_task_step_attempt_operation",
        ),
        CheckConstraint(
            "status IN ("
            "'started','succeeded','failed','blocked','ambiguous')",
            name="ck_scheduled_task_step_attempt_status",
        ),
        Index(
            "uq_scheduled_task_step_attempt",
            "execution_id",
            "step_id",
            "attempt_no",
            unique=True,
        ),
        Index(
            "ix_scheduled_task_step_idempotency",
            "idempotency_key",
        ),
        Index(
            "ix_scheduled_task_step_execution_status",
            "execution_id",
            "status",
        ),
        {"sqlite_autoincrement": True},
    )


__all__ = [
    "SCHEDULED_TASK_EXECUTION_STATUSES",
    "SCHEDULED_TASK_PROGRAM_OPERATIONS",
    "SCHEDULED_TASK_STEP_ATTEMPT_STATUSES",
    "ScheduledTask",
    "ScheduledTaskExecution",
    "ScheduledTaskOwnerLease",
    "ScheduledTaskStepAttempt",
]

"""计划任务持久化模型。"""

from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, Index, Integer, String, Text, text

from core.db.base import Base
from core.outbound_delivery_schema import SCHEDULED_TASK_ERROR_SUMMARY_CHECK


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

    __table_args__ = (
        Index("ix_scheduled_tasks_last_run_id", "last_run_id"),
        Index(
            "ix_scheduled_tasks_enabled_next_fire_at",
            "enabled",
            "next_fire_at",
        ),
    )


__all__ = ["ScheduledTask"]

"""长任务 Session Goal、不可变计划资产与 append-only 事件。"""

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


class SessionGoalRow(Base):
    """长任务当前状态投影；计划正文保存在不可变 revision 表。"""

    __tablename__ = "session_goals"
    __table_args__ = (
        CheckConstraint(
            "owner_type IN ('user', 'group', 'project')",
            name="ck_session_goal_owner_type",
        ),
        CheckConstraint(
            "status IN ("
            "'planning', 'awaiting_approval', 'approved', 'executing', "
            "'completed', 'cancelled', 'failed'"
            ")",
            name="ck_session_goal_status",
        ),
        CheckConstraint(
            "mode IN ('plan', 'execute')",
            name="ck_session_goal_mode",
        ),
        CheckConstraint(
            "version >= 1 AND latest_plan_revision >= 0 "
            "AND approved_plan_revision >= 0",
            name="ck_session_goal_versions_nonnegative",
        ),
        CheckConstraint(
            "max_model_steps > 0 AND max_tool_calls > 0 "
            "AND max_input_tokens > 0 AND max_output_tokens > 0 "
            "AND max_cost_microunits > 0 AND max_elapsed_seconds > 0",
            name="ck_session_goal_budget_positive",
        ),
        CheckConstraint(
            "(status = 'executing' AND mode = 'execute' "
            "AND approved_plan_revision > 0 AND approved_plan_sha256 <> '') "
            "OR status <> 'executing'",
            name="ck_session_goal_execution_requires_approval",
        ),
        CheckConstraint(
            "status NOT IN ('planning', 'awaiting_approval', 'approved') "
            "OR mode = 'plan'",
            name="ck_session_goal_preexecution_mode",
        ),
        CheckConstraint(
            "status <> 'approved' OR (approved_plan_revision > 0 "
            "AND approved_plan_sha256 <> '')",
            name="ck_session_goal_approved_proof",
        ),
        CheckConstraint(
            "mode <> 'execute' OR (approved_plan_revision > 0 "
            "AND approved_plan_sha256 <> '')",
            name="ck_session_goal_execute_mode_proof",
        ),
        Index(
            "ix_session_goal_owner_session_status",
            "platform",
            "owner_type",
            "owner_id",
            "session_id",
            "status",
        ),
    )

    goal_id = Column(String(64), primary_key=True)
    platform = Column(String(32), nullable=False)
    owner_type = Column(String(16), nullable=False)
    owner_id = Column(String(255), nullable=False)
    session_id = Column(String(255), nullable=False)
    objective = Column(Text, nullable=False)
    completion_criteria_json = Column(Text, nullable=False)

    max_model_steps = Column(Integer, nullable=False)
    max_tool_calls = Column(Integer, nullable=False)
    max_input_tokens = Column(Integer, nullable=False)
    max_output_tokens = Column(Integer, nullable=False)
    max_cost_microunits = Column(Integer, nullable=False)
    max_elapsed_seconds = Column(Integer, nullable=False)

    status = Column(String(32), nullable=False, default="planning")
    mode = Column(String(16), nullable=False, default="plan")
    version = Column(Integer, nullable=False, default=1)
    latest_plan_revision = Column(Integer, nullable=False, default=0)
    latest_plan_sha256 = Column(
        String(64), nullable=False, default="", server_default=text("''")
    )
    approved_plan_revision = Column(Integer, nullable=False, default=0)
    approved_plan_sha256 = Column(
        String(64), nullable=False, default="", server_default=text("''")
    )
    approved_by = Column(
        String(255), nullable=False, default="", server_default=text("''")
    )
    approved_at = Column(DateTime, nullable=True)
    execution_started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    terminal_reason = Column(
        String(512), nullable=False, default="", server_default=text("''")
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


class SessionPlanAssetRow(Base):
    """Plan Mode 产生的不可变 Markdown 计划版本。"""

    __tablename__ = "session_plan_assets"
    __table_args__ = (
        UniqueConstraint(
            "goal_id",
            "revision",
            name="uq_session_plan_asset_revision",
        ),
        CheckConstraint(
            "revision >= 1",
            name="ck_session_plan_asset_revision_positive",
        ),
        Index("ix_session_plan_asset_sha256", "content_sha256"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    goal_id = Column(
        String(64),
        ForeignKey("session_goals.goal_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision = Column(Integer, nullable=False)
    media_type = Column(
        String(64),
        nullable=False,
        default="text/markdown",
        server_default=text("'text/markdown'"),
    )
    content = Column(Text, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    source_run_id = Column(
        String(160), nullable=False, default="", server_default=text("''")
    )
    created_by = Column(String(255), nullable=False)
    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class SessionGoalEventRow(Base):
    """Session Goal 的 append-only 控制事件；不保存目标或计划正文。"""

    __tablename__ = "session_goal_events"
    __table_args__ = (
        UniqueConstraint(
            "goal_id",
            "goal_version",
            name="uq_session_goal_event_version",
        ),
        CheckConstraint(
            "event_kind IN ("
            "'created', 'plan_written', 'approval_requested', 'approved', "
            "'execution_started', 'completed', 'cancelled', 'failed'"
            ")",
            name="ck_session_goal_event_kind",
        ),
        CheckConstraint(
            "goal_version >= 1 AND plan_revision >= 0",
            name="ck_session_goal_event_versions_nonnegative",
        ),
        Index("ix_session_goal_event_goal_time", "goal_id", "occurred_at"),
    )

    event_id = Column(String(80), primary_key=True)
    goal_id = Column(
        String(64),
        ForeignKey("session_goals.goal_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    goal_version = Column(Integer, nullable=False)
    event_kind = Column(String(32), nullable=False)
    previous_status = Column(
        String(32), nullable=False, default="", server_default=text("''")
    )
    current_status = Column(String(32), nullable=False)
    previous_mode = Column(
        String(16), nullable=False, default="", server_default=text("''")
    )
    current_mode = Column(String(16), nullable=False)
    plan_revision = Column(Integer, nullable=False, default=0)
    plan_sha256 = Column(
        String(64), nullable=False, default="", server_default=text("''")
    )
    actor_id = Column(String(255), nullable=False)
    source_run_id = Column(
        String(160), nullable=False, default="", server_default=text("''")
    )
    event_sha256 = Column(String(64), nullable=False)
    occurred_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


__all__ = [
    "SessionGoalEventRow",
    "SessionGoalRow",
    "SessionPlanAssetRow",
]

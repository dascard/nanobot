"""多 Agent 计划修订、审计事件和任务屏障 checkpoint。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)

from core.db.base import Base


class AgentOrchestrationPlanRevisionRow(Base):
    """不可变的动态计划 JSON；生命周期只通过事件追加。"""

    __tablename__ = "agent_orchestration_plan_revisions"
    __table_args__ = (
        UniqueConstraint(
            "owner_platform",
            "owner_type",
            "owner_id",
            "plan_id",
            "revision",
            name="uq_agent_orchestration_plan_owner_revision",
        ),
        CheckConstraint(
            "revision >= 1 AND size_bytes > 0",
            name="ck_agent_orchestration_plan_revision_size",
        ),
        CheckConstraint(
            "owner_type IN ('user', 'group', 'project', 'system')",
            name="ck_agent_orchestration_plan_owner_type",
        ),
        Index(
            "ix_agent_orchestration_plan_owner_latest",
            "owner_platform",
            "owner_type",
            "owner_id",
            "plan_id",
            "revision",
        ),
        Index(
            "ix_agent_orchestration_plan_sha256",
            "plan_sha256",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    preview_id = Column(String(160), nullable=False, unique=True)
    owner_platform = Column(String(64), nullable=False)
    owner_type = Column(String(32), nullable=False)
    owner_id = Column(String(255), nullable=False)
    plan_id = Column(String(160), nullable=False)
    revision = Column(Integer, nullable=False)
    plan_sha256 = Column(String(64), nullable=False)
    plan_json = Column(Text, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    source_run_id = Column(String(160), nullable=False)
    source_turn_id = Column(String(160), nullable=False)
    proposed_by = Column(String(160), nullable=False)
    proposed_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    parent_plan_sha256 = Column(
        String(64), nullable=False, default="", server_default=text("''")
    )
    repair_reason_code = Column(
        String(128), nullable=False, default="", server_default=text("''")
    )
    added_task_ids_json = Column(
        Text, nullable=False, default="[]", server_default=text("'[]'")
    )
    removed_task_ids_json = Column(
        Text, nullable=False, default="[]", server_default=text("'[]'")
    )
    changed_task_ids_json = Column(
        Text, nullable=False, default="[]", server_default=text("'[]'")
    )


class AgentOrchestrationPlanEventRow(Base):
    """每个 owner/plan 严格递增的追加式治理事实。"""

    __tablename__ = "agent_orchestration_plan_events"
    __table_args__ = (
        ForeignKeyConstraint(
            (
                "owner_platform",
                "owner_type",
                "owner_id",
                "plan_id",
                "plan_revision",
            ),
            (
                "agent_orchestration_plan_revisions.owner_platform",
                "agent_orchestration_plan_revisions.owner_type",
                "agent_orchestration_plan_revisions.owner_id",
                "agent_orchestration_plan_revisions.plan_id",
                "agent_orchestration_plan_revisions.revision",
            ),
            name="fk_agent_orchestration_event_revision",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "owner_platform",
            "owner_type",
            "owner_id",
            "plan_id",
            "sequence",
            name="uq_agent_orchestration_plan_event_sequence",
        ),
        CheckConstraint(
            "sequence >= 1 AND plan_revision >= 1 "
            "AND related_plan_revision >= 0",
            name="ck_agent_orchestration_plan_event_versions",
        ),
        CheckConstraint(
            "event_kind IN ("
            "'previewed', 'approved', 'frozen', 'revision_superseded'"
            ")",
            name="ck_agent_orchestration_plan_event_kind",
        ),
        Index(
            "ix_agent_orchestration_plan_event_family_time",
            "owner_platform",
            "owner_type",
            "owner_id",
            "plan_id",
            "occurred_at",
        ),
    )

    position = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(160), nullable=False, unique=True)
    owner_platform = Column(String(64), nullable=False)
    owner_type = Column(String(32), nullable=False)
    owner_id = Column(String(255), nullable=False)
    plan_id = Column(String(160), nullable=False)
    plan_revision = Column(Integer, nullable=False)
    plan_sha256 = Column(String(64), nullable=False)
    sequence = Column(Integer, nullable=False)
    event_kind = Column(String(32), nullable=False)
    actor_id = Column(String(160), nullable=False)
    proof_id = Column(
        String(160), nullable=False, default="", server_default=text("''")
    )
    related_plan_revision = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    related_plan_sha256 = Column(
        String(64), nullable=False, default="", server_default=text("''")
    )
    occurred_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    previous_event_sha256 = Column(
        String(64), nullable=False, default="", server_default=text("''")
    )
    event_sha256 = Column(String(64), nullable=False, unique=True)


class AgentOrchestrationCheckpointRow(Base):
    """不可变任务屏障快照；不允许覆盖或跨 owner 读取。"""

    __tablename__ = "agent_orchestration_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "orchestration_id",
            "sequence",
            name="uq_agent_orchestration_checkpoint_sequence",
        ),
        CheckConstraint(
            "sequence >= 1 AND plan_revision >= 1 AND size_bytes > 0",
            name="ck_agent_orchestration_checkpoint_sequence_size",
        ),
        Index(
            "ix_agent_orchestration_checkpoint_owner_latest",
            "owner_id",
            "orchestration_id",
            "sequence",
        ),
    )

    position = Column(Integer, primary_key=True, autoincrement=True)
    checkpoint_id = Column(String(200), nullable=False, unique=True)
    orchestration_id = Column(String(160), nullable=False)
    run_id = Column(String(160), nullable=False)
    owner_id = Column(String(512), nullable=False)
    plan_id = Column(String(160), nullable=False)
    plan_revision = Column(Integer, nullable=False)
    plan_sha256 = Column(String(64), nullable=False)
    freeze_id = Column(String(160), nullable=False)
    sequence = Column(Integer, nullable=False)
    parent_checkpoint_id = Column(
        String(200), nullable=False, default="", server_default=text("''")
    )
    barrier_id = Column(String(200), nullable=False)
    state_json = Column(Text, nullable=False)
    state_sha256 = Column(String(64), nullable=False, unique=True)
    size_bytes = Column(Integer, nullable=False)
    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


__all__ = [
    "AgentOrchestrationCheckpointRow",
    "AgentOrchestrationPlanEventRow",
    "AgentOrchestrationPlanRevisionRow",
]

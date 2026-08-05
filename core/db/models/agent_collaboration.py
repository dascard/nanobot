"""多 Agent 协作任务板与追加式 handoff 事实。"""

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


class AgentCollaborationBoardRow(Base):
    """冻结计划的一次不可变协作执行绑定。"""

    __tablename__ = "agent_collaboration_boards"
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
            name="fk_agent_collaboration_board_plan_revision",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "owner_platform",
            "owner_type",
            "owner_id",
            "idempotency_key_sha256",
            name="uq_agent_collaboration_board_owner_idempotency",
        ),
        CheckConstraint(
            "owner_type IN ('user', 'group', 'project', 'system')",
            name="ck_agent_collaboration_board_owner_type",
        ),
        CheckConstraint(
            "actor_type IN ('user', 'agent', 'tool', 'system', 'adapter')",
            name="ck_agent_collaboration_board_actor_type",
        ),
        CheckConstraint(
            "plan_revision >= 1 AND root_input_size_bytes > 0",
            name="ck_agent_collaboration_board_revision_size",
        ),
        Index(
            "ix_agent_collaboration_board_owner_created",
            "owner_platform",
            "owner_type",
            "owner_id",
            "created_at",
        ),
        Index(
            "ix_agent_collaboration_board_plan",
            "plan_id",
            "plan_revision",
            "plan_sha256",
        ),
        Index("ix_agent_collaboration_board_expiry", "expires_at"),
    )

    board_id = Column(String(160), primary_key=True)
    owner_platform = Column(String(64), nullable=False)
    owner_type = Column(String(32), nullable=False)
    owner_id = Column(String(255), nullable=False)
    plan_id = Column(String(160), nullable=False)
    plan_revision = Column(Integer, nullable=False)
    plan_sha256 = Column(String(64), nullable=False)
    approval_id = Column(String(160), nullable=False)
    freeze_id = Column(String(160), nullable=False)

    run_id = Column(String(160), nullable=False)
    turn_id = Column(String(160), nullable=False)
    correlation_id = Column(String(160), nullable=False)
    actor_type = Column(String(32), nullable=False)
    actor_id = Column(String(160), nullable=False)
    parent_actor_id = Column(
        String(160), nullable=False, default="", server_default=text("''")
    )

    root_input_json = Column(Text, nullable=False)
    root_input_sha256 = Column(String(64), nullable=False)
    root_input_size_bytes = Column(Integer, nullable=False)
    source_type = Column(String(64), nullable=False)
    source_id = Column(String(160), nullable=False)
    created_by = Column(String(160), nullable=False)
    idempotency_key_sha256 = Column(String(64), nullable=False)
    request_sha256 = Column(String(64), nullable=False)
    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    expires_at = Column(DateTime, nullable=False)


class AgentCollaborationEventRow(Base):
    """任务认领、交付和人工复核的不可变哈希链。"""

    __tablename__ = "agent_collaboration_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ("board_id",),
            ("agent_collaboration_boards.board_id",),
            name="fk_agent_collaboration_event_board",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "board_id",
            "sequence",
            name="uq_agent_collaboration_event_sequence",
        ),
        UniqueConstraint(
            "board_id",
            "idempotency_key_sha256",
            name="uq_agent_collaboration_event_idempotency",
        ),
        CheckConstraint(
            "sequence >= 1 AND payload_size_bytes > 0",
            name="ck_agent_collaboration_event_sequence_size",
        ),
        CheckConstraint(
            "event_kind IN ("
            "'board_created', 'agent_invited', 'task_claimed', "
            "'deliverable_submitted', 'deliverable_approved', "
            "'deliverable_rejected'"
            ")",
            name="ck_agent_collaboration_event_kind",
        ),
        Index(
            "ix_agent_collaboration_event_board_time",
            "board_id",
            "occurred_at",
        ),
        Index(
            "ix_agent_collaboration_event_task",
            "board_id",
            "task_id",
            "sequence",
        ),
        Index(
            "ix_agent_collaboration_event_delivery",
            "board_id",
            "delivery_id",
        ),
        Index(
            "ix_agent_collaboration_event_target_expiry",
            "target_actor_id",
            "expires_at",
        ),
    )

    position = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(160), nullable=False, unique=True)
    board_id = Column(String(160), nullable=False)
    sequence = Column(Integer, nullable=False)
    event_kind = Column(String(40), nullable=False)
    actor_id = Column(String(160), nullable=False)
    target_actor_id = Column(
        String(160), nullable=False, default="", server_default=text("''")
    )
    task_id = Column(
        String(128), nullable=False, default="", server_default=text("''")
    )
    delivery_id = Column(
        String(160), nullable=False, default="", server_default=text("''")
    )
    payload_json = Column(Text, nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    payload_size_bytes = Column(Integer, nullable=False)
    idempotency_key_sha256 = Column(String(64), nullable=False)
    request_sha256 = Column(String(64), nullable=False)
    previous_event_sha256 = Column(
        String(64), nullable=False, default="", server_default=text("''")
    )
    event_sha256 = Column(String(64), nullable=False, unique=True)
    occurred_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    expires_at = Column(DateTime, nullable=True)


__all__ = [
    "AgentCollaborationBoardRow",
    "AgentCollaborationEventRow",
]

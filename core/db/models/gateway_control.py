"""多渠道 Gateway 会话绑定与远程控制审计模型。"""

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


class GatewaySessionBindingRow(Base):
    """渠道会话到 canonical principal／Runtime session 的可变投影。"""

    __tablename__ = "gateway_session_bindings"
    __table_args__ = (
        UniqueConstraint(
            "transport",
            "chat_stream_id",
            name="uq_gateway_session_transport_stream",
        ),
        CheckConstraint(
            "generation > 0",
            name="ck_gateway_session_generation_positive",
        ),
        CheckConstraint(
            "(preferred_model_profile_id = '' "
            "AND preferred_model_effective_generation = 0) OR "
            "(preferred_model_profile_id <> '' "
            "AND preferred_model_effective_generation > generation)",
            name="ck_gateway_session_pending_model_shape",
        ),
        Index(
            "ix_gateway_session_owner",
            "owner_platform",
            "owner_type",
            "owner_id",
        ),
        Index(
            "ix_gateway_session_runtime",
            "runtime_session_id",
            "updated_at",
        ),
    )

    binding_id = Column(String(64), primary_key=True)
    transport = Column(String(32), nullable=False)
    owner_platform = Column(String(32), nullable=False)
    owner_type = Column(String(32), nullable=False)
    owner_id = Column(String(512), nullable=False)
    actor_id = Column(String(512), nullable=False)
    chat_type = Column(String(16), nullable=False)
    chat_stream_id = Column(String(640), nullable=False)
    runtime_session_id = Column(String(160), nullable=False)
    current_run_id = Column(String(160), nullable=False, default="", index=True)
    active_model_profile_id = Column(
        String(160),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    preferred_model_profile_id = Column(
        String(160),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    preferred_model_effective_generation = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    generation = Column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        index=True,
    )


class GatewayRunBindingRow(Base):
    """Run 接纳时冻结的渠道、会话和 owner 关系。"""

    __tablename__ = "gateway_run_bindings"
    __table_args__ = (
        Index(
            "ix_gateway_run_binding_owner",
            "owner_platform",
            "owner_type",
            "owner_id",
            "admitted_at",
        ),
        Index(
            "ix_gateway_run_binding_session",
            "binding_id",
            "admitted_at",
        ),
    )

    run_id = Column(String(160), primary_key=True)
    binding_id = Column(String(64), nullable=False, index=True)
    transport = Column(String(32), nullable=False)
    owner_platform = Column(String(32), nullable=False)
    owner_type = Column(String(32), nullable=False)
    owner_id = Column(String(512), nullable=False)
    actor_id = Column(String(512), nullable=False)
    chat_type = Column(String(16), nullable=False)
    chat_stream_id = Column(String(640), nullable=False)
    runtime_session_id = Column(String(160), nullable=False)
    admitted_at = Column(DateTime, nullable=False, default=datetime.now)


class GatewayControlEventRow(Base):
    """远程控制的追加式幂等审计事实。"""

    __tablename__ = "gateway_control_events"
    __table_args__ = (
        UniqueConstraint(
            "request_id_sha256",
            name="uq_gateway_control_request",
        ),
        CheckConstraint(
            "action IN ('stop', 'resume', 'model_switch')",
            name="ck_gateway_control_action",
        ),
        CheckConstraint(
            "outcome IN ('accepted', 'already_terminal')",
            name="ck_gateway_control_outcome",
        ),
        Index(
            "ix_gateway_control_run_time",
            "run_id",
            "occurred_at",
        ),
        Index(
            "ix_gateway_control_binding_time",
            "binding_id",
            "occurred_at",
        ),
    )

    event_id = Column(String(160), primary_key=True)
    request_id_sha256 = Column(String(64), nullable=False)
    request_fingerprint_sha256 = Column(String(64), nullable=False)
    binding_id = Column(String(64), nullable=False, index=True)
    run_id = Column(String(160), nullable=False, index=True)
    action = Column(String(32), nullable=False)
    actor_platform = Column(String(32), nullable=False)
    actor_type = Column(String(32), nullable=False)
    actor_id = Column(String(512), nullable=False)
    outcome = Column(String(32), nullable=False)
    result_json = Column(
        Text,
        nullable=False,
        default="{}",
        server_default=text("'{}'"),
    )
    occurred_at = Column(DateTime, nullable=False, default=datetime.now)


__all__ = [
    "GatewayControlEventRow",
    "GatewayRunBindingRow",
    "GatewaySessionBindingRow",
]

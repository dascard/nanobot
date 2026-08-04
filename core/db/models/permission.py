"""统一 Permission session grant 的可撤销运行状态。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    String,
    UniqueConstraint,
)

from core.db.base import Base


class PermissionSessionGrantRow(Base):
    """服务端签发的精确 session grant；模型只接触不可猜测 ID。"""

    __tablename__ = "permission_session_grants"
    __table_args__ = (
        UniqueConstraint(
            "active_binding_key",
            name="uq_permission_session_grant_active_binding",
        ),
        CheckConstraint(
            "expires_at > issued_at",
            name="ck_permission_session_grant_expiry_order",
        ),
        Index(
            "ix_permission_session_grant_scope",
            "owner_platform",
            "owner_type",
            "owner_id",
            "session_id",
            "action",
        ),
        Index(
            "ix_permission_session_grant_active_expiry",
            "active_binding_key",
            "expires_at",
        ),
    )

    grant_id = Column(String(160), primary_key=True)
    active_binding_key = Column(String(64), nullable=True)
    owner_platform = Column(String(64), nullable=False)
    owner_type = Column(String(64), nullable=False)
    owner_id = Column(String(160), nullable=False)
    session_id = Column(String(160), nullable=False)
    action = Column(String(160), nullable=False)
    resource_sha256 = Column(String(64), nullable=False)
    risk = Column(String(32), nullable=False)
    source_run_id = Column(String(160), nullable=False, index=True)
    source_turn_id = Column(String(160), nullable=False)
    source_request_id = Column(String(160), nullable=False)
    source_decision_id = Column(String(160), nullable=False)
    issued_at = Column(DateTime, nullable=False, default=datetime.now)
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True, index=True)
    revocation_id = Column(String(160), nullable=False, default="")
    revoked_by = Column(String(160), nullable=False, default="")
    revoke_reason = Column(String(160), nullable=False, default="")


__all__ = ["PermissionSessionGrantRow"]

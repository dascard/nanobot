"""主动外呼租约与审计模型。"""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text, text

from core.db.base import Base


class ProactiveOutreachLease(Base):
    """主动外呼按用户串行评估的短期租约。"""

    __tablename__ = "proactive_outreach_leases"
    __table_args__ = (
        Index(
            "ix_proactive_outreach_lease_expires_at",
            "lease_expires_at",
        ),
    )

    user_id = Column(String(255), primary_key=True)
    owner_token = Column(String(64), nullable=False)
    lease_expires_at = Column(DateTime, nullable=False)
    created_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class ProactiveOutreachLog(Base):
    """主动情感外呼记录，用于幂等投递和调度审计。"""

    __tablename__ = "proactive_outreach_log"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, index=True)
    idempotency_key = Column(String, unique=True, index=True)
    grounding_json = Column(Text, default="{}")
    judge_should = Column(Boolean, default=False)
    judge_reason = Column(Text, default="")
    next_check_at = Column(DateTime, nullable=True)
    next_intent = Column(Text, default="")
    message = Column(Text, default="")
    status = Column(String, index=True, default="pending")
    forced = Column(Boolean, default=False)
    outbound_run_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.now, index=True)

    __table_args__ = (
        Index(
            "ix_proactive_outreach_log_outbound_run_id",
            "outbound_run_id",
        ),
    )


__all__ = ["ProactiveOutreachLease", "ProactiveOutreachLog"]

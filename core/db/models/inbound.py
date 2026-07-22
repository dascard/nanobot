"""入站幂等与私聊恢复投递模型。"""

from sqlalchemy import CheckConstraint, Column, DateTime, Index, Integer, String, Text, text

from core.db.base import Base


class InboundMessageClaim(Base):
    """入站消息幂等 claim。"""

    __tablename__ = "inbound_message_claims"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'completed', 'failed')",
            name="ck_inbound_message_claim_status",
        ),
        CheckConstraint(
            "attempt_count >= 1",
            name="ck_inbound_message_claim_attempt_count",
        ),
        Index(
            "uq_inbound_message_claim_identity",
            "platform",
            "chat_type",
            "session_id",
            "message_id",
            unique=True,
        ),
        Index(
            "ix_inbound_message_claim_status_lease",
            "status",
            "lease_expires_at",
        ),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False)
    chat_type = Column(String(16), nullable=False)
    session_id = Column(String(255), nullable=False)
    message_id = Column(String(255), nullable=False)
    status = Column(
        String(16),
        nullable=False,
        default="processing",
        server_default=text("'processing'"),
    )
    owner_token = Column(String(64), nullable=False)
    lease_expires_at = Column(DateTime, nullable=True)
    response_json = Column(Text, nullable=False, default="", server_default=text("''"))
    error_summary = Column(Text, nullable=False, default="", server_default=text("''"))
    attempt_count = Column(Integer, nullable=False, default=1, server_default=text("1"))
    created_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    completed_at = Column(DateTime, nullable=True)


class ChatDeliveryOutbox(Base):
    """私聊断连后的持久投递任务。"""

    __tablename__ = "chat_delivery_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'sending', 'ambiguous', 'delivered', 'failed')",
            name="ck_chat_delivery_outbox_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_chat_delivery_outbox_attempt_count",
        ),
        Index("uq_chat_delivery_outbox_delivery_key", "delivery_key", unique=True),
        Index(
            "uq_chat_delivery_outbox_claim_identity",
            "platform",
            "chat_type",
            "session_id",
            "message_id",
            unique=True,
        ),
        Index("ix_chat_delivery_outbox_due", "status", "next_attempt_at"),
        Index(
            "ix_chat_delivery_outbox_status_lease",
            "status",
            "lease_expires_at",
        ),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    delivery_key = Column(String(64), nullable=False)
    platform = Column(String(32), nullable=False)
    chat_type = Column(String(16), nullable=False)
    session_id = Column(String(255), nullable=False)
    message_id = Column(String(255), nullable=False)
    target_type = Column(String(16), nullable=False)
    target_id = Column(String(255), nullable=False)
    envelope_json = Column(Text, nullable=False, default="{}", server_default=text("'{}'"))
    status = Column(
        String(16),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    owner_token = Column(String(64), nullable=False, default="", server_default=text("''"))
    lease_expires_at = Column(DateTime, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    next_attempt_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=False, default="", server_default=text("''"))
    created_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    delivered_at = Column(DateTime, nullable=True)


__all__ = ["ChatDeliveryOutbox", "InboundMessageClaim"]

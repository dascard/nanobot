"""管理配置、规则、审计和用量模型。"""

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
)

from core.db.base import Base


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    admin_user = Column(String, default="admin")
    action = Column(String, nullable=False)
    target_type = Column(String, default="")
    target_id = Column(String, default="")
    detail_json = Column(Text, default="{}")
    ip_address = Column(String, default="")
    created_at = Column(DateTime, default=datetime.now)


class AdminIdempotencyRecord(Base):
    """Admin 写操作的跨线程／跨进程 at-most-once 账本。"""

    __tablename__ = "admin_idempotency_records"

    request_id = Column(String(64), primary_key=True)
    action = Column(String(128), nullable=False, index=True)
    target_id = Column(String(255), nullable=False, default="")
    request_sha256 = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="running")
    result_json = Column(Text, nullable=False, default="{}")
    error_code = Column(String(128), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('running','succeeded','failed')",
            name="ck_admin_idempotency_records_status",
        ),
        Index(
            "idx_admin_idempotency_action_target",
            "action",
            "target_id",
        ),
    )


class MemoryCleanupRun(Base):
    __tablename__ = "memory_cleanup_runs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    cleanup_version = Column(String(64), nullable=False, default="")
    bundle_sha256 = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="applying")
    actor = Column(String(255), nullable=False, default="cli")
    audit_log_id = Column(Integer, nullable=True)
    target_counts_json = Column(Text, nullable=False, default="{}")
    result_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=datetime.now)
    applied_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "bundle_sha256",
            name="uq_memory_cleanup_run_bundle_sha256",
        ),
        Index("idx_memory_cleanup_run_status", "status", "id"),
    )


class UserBlockRule(Base):
    __tablename__ = "user_block_rules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, index=True, nullable=False)
    target_type = Column(String, default="private")
    group_id = Column(String, default="")
    rule_mode = Column(String, default="log_only")
    reason = Column(Text, default="")
    enabled = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
    )


class ContentBlockRule(Base):
    __tablename__ = "content_block_rules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    pattern = Column(String, nullable=False)
    match_type = Column(String, default="contains")
    scope_type = Column(String, default="session")
    chat_stream_id = Column(String, default="")
    no_reply = Column(Integer, default=0)
    no_learn = Column(Integer, default=1)
    no_context = Column(Integer, default=0)
    category = Column(String, default="no_learn")
    enabled = Column(Integer, default=1)
    reason = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
    )


class ToolOverride(Base):
    __tablename__ = "tool_overrides"
    id = Column(Integer, primary_key=True, autoincrement=True)
    tool_name = Column(String, nullable=False, index=True)
    scope_type = Column(String, nullable=False)
    scope_id = Column(String, nullable=False)
    enabled = Column(Integer, nullable=False, default=1)
    reason = Column(Text, default="")
    updated_at = Column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
    )
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint(
            "tool_name",
            "scope_type",
            "scope_id",
            name="uq_tool_override",
        ),
    )


class RuntimeToolDecision(Base):
    __tablename__ = "runtime_tool_decisions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, index=True)
    message_id = Column(String, default="")
    chat_type = Column(String, default="group")
    platform = Column(String, default="")
    group_id = Column(String, default="")
    user_id = Column(String, default="")
    runtime_preset = Column(String, default="full")
    enabled_tools_json = Column(Text, default="[]")
    disabled_tools_json = Column(Text, default="[]")
    disabled_reasons_json = Column(Text, default="{}")
    effective_tools_json = Column(Text, default="[]")
    created_at = Column(DateTime, default=datetime.now)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key = Column(String, primary_key=True)
    value = Column(Text, default="")
    description = Column(Text, default="")
    updated_at = Column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
    )


class WebSearchProviderUsage(Base):
    __tablename__ = "web_search_provider_usage"
    provider_id = Column(String, primary_key=True, index=True)
    total_calls = Column(Integer, default=0)
    success_calls = Column(Integer, default=0)
    failure_calls = Column(Integer, default=0)
    last_called_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    last_error_at = Column(DateTime, nullable=True)
    last_error_code = Column(String, default="")
    last_duration_ms = Column(Integer, default=0)
    updated_at = Column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
    )


__all__ = [
    "AdminAuditLog",
    "AdminIdempotencyRecord",
    "ContentBlockRule",
    "MemoryCleanupRun",
    "RuntimeToolDecision",
    "SystemSetting",
    "ToolOverride",
    "UserBlockRule",
    "WebSearchProviderUsage",
]

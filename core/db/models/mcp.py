"""MCP 控制面配置、加密秘密和安全诊断事实。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    text,
)

from core.db.base import Base


class McpConfigurationStateRow(Base):
    """全量 MCP 配置的 CAS generation。"""

    __tablename__ = "mcp_configuration_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_mcp_configuration_singleton"),
        CheckConstraint("revision >= 1", name="ck_mcp_configuration_revision"),
    )

    id = Column(Integer, primary_key=True, default=1)
    revision = Column(Integer, nullable=False)
    registry_sha256 = Column(String(64), nullable=False)
    updated_by = Column(String(255), nullable=False)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class McpServerRow(Base):
    """一个不含秘密值的 MCP server 配置。"""

    __tablename__ = "mcp_servers"
    __table_args__ = (
        CheckConstraint(
            "transport IN ('stdio', 'sse', 'http')",
            name="ck_mcp_server_transport",
        ),
        CheckConstraint(
            "auth_mode IN ('none', 'bearer', 'oauth_client_credentials')",
            name="ck_mcp_server_auth_mode",
        ),
        CheckConstraint("enabled IN (0, 1)", name="ck_mcp_server_enabled"),
        CheckConstraint(
            "connect_timeout_seconds > 0 AND request_timeout_seconds > 0 "
            "AND sse_read_timeout_seconds > 0",
            name="ck_mcp_server_timeouts",
        ),
        CheckConstraint(
            "reconnect_attempts >= 0 AND max_tools > 0",
            name="ck_mcp_server_limits",
        ),
        Index("ix_mcp_server_enabled_transport", "enabled", "transport"),
    )

    server_id = Column(String(64), primary_key=True)
    display_name = Column(String(128), nullable=False)
    transport = Column(String(16), nullable=False)
    enabled = Column(Boolean, nullable=False, default=False)
    endpoint = Column(Text, nullable=False, default="", server_default=text("''"))
    command = Column(Text, nullable=False, default="", server_default=text("''"))
    args_json = Column(Text, nullable=False, default="[]")
    cwd = Column(Text, nullable=False, default="", server_default=text("''"))
    connect_timeout_seconds = Column(Float, nullable=False, default=10.0)
    request_timeout_seconds = Column(Float, nullable=False, default=60.0)
    sse_read_timeout_seconds = Column(Float, nullable=False, default=300.0)
    reconnect_attempts = Column(Integer, nullable=False, default=1)
    max_tools = Column(Integer, nullable=False, default=20)
    auth_mode = Column(String(32), nullable=False, default="none")
    oauth_token_url = Column(
        Text, nullable=False, default="", server_default=text("''")
    )
    oauth_scopes_json = Column(Text, nullable=False, default="[]")
    secret_refs_json = Column(Text, nullable=False, default="[]")
    config_sha256 = Column(String(64), nullable=False)
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


class McpSecretRow(Base):
    """由独立主密钥加密的 MCP 凭据值。"""

    __tablename__ = "mcp_secrets"

    secret_id = Column(String(128), primary_key=True)
    encrypted_value = Column(Text, nullable=False)
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


class McpDiagnosticRow(Base):
    """不保存 endpoint、命令、参数、秘密引用或响应正文的诊断事实。"""

    __tablename__ = "mcp_diagnostics"
    __table_args__ = (
        CheckConstraint(
            "status IN ('healthy', 'failed', 'disabled')",
            name="ck_mcp_diagnostic_status",
        ),
        CheckConstraint(
            "operation IN ('discover', 'health', 'call')",
            name="ck_mcp_diagnostic_operation",
        ),
        CheckConstraint(
            "latency_ms >= 0 AND tool_count >= 0",
            name="ck_mcp_diagnostic_counts",
        ),
        Index(
            "ix_mcp_diagnostic_server_time",
            "server_id",
            "occurred_at",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    server_id = Column(String(64), nullable=False)
    config_sha256 = Column(String(64), nullable=False)
    transport = Column(String(16), nullable=False)
    operation = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False)
    error_code = Column(
        String(64), nullable=False, default="", server_default=text("''")
    )
    error_type = Column(
        String(128), nullable=False, default="", server_default=text("''")
    )
    retryable = Column(Boolean, nullable=False, default=False)
    ambiguous = Column(Boolean, nullable=False, default=False)
    latency_ms = Column(Integer, nullable=False, default=0)
    tool_count = Column(Integer, nullable=False, default=0)
    occurred_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


__all__ = [
    "McpConfigurationStateRow",
    "McpDiagnosticRow",
    "McpSecretRow",
    "McpServerRow",
]

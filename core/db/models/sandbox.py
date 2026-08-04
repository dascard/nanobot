"""Sandbox 工作区、授权、配额与管理操作 ORM 模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
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


SANDBOX_EXECUTION_PROFILES = frozenset({
    "restricted",
    "developer",
    "trusted_developer",
})
SANDBOX_LEASE_NONTERMINAL_STATUSES = frozenset({
    "provisioning",
    "active",
    "idle",
    "stopping",
})
SANDBOX_LEASE_TERMINAL_STATUSES = frozenset({
    "stopped",
    "expired",
    "destroyed",
    "failed",
})
SANDBOX_LEASE_STATUSES = (
    SANDBOX_LEASE_NONTERMINAL_STATUSES
    | SANDBOX_LEASE_TERMINAL_STATUSES
)


class Workspace(Base):
    """Sandbox 长期工作区元数据；真实宿主路径不写入数据库。"""

    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint(
            "owner_type IN ('user', 'group', 'project')",
            name="ck_workspace_owner_type",
        ),
        CheckConstraint(
            "status IN ('active', 'disabled', 'archived')",
            name="ck_workspace_status",
        ),
        CheckConstraint(
            "quota_bytes > 0",
            name="ck_workspace_quota_positive",
        ),
        CheckConstraint(
            "used_bytes >= 0",
            name="ck_workspace_used_nonnegative",
        ),
        UniqueConstraint(
            "platform",
            "owner_type",
            "owner_id",
            "name",
            name="uq_workspace_owner_name",
        ),
        Index(
            "ix_workspace_owner",
            "platform",
            "owner_type",
            "owner_id",
        ),
    )

    id = Column(String(36), primary_key=True)
    platform = Column(String(32), nullable=False)
    owner_type = Column(String(16), nullable=False)
    owner_id = Column(String(255), nullable=False)
    name = Column(String(64), nullable=False, default="default")
    status = Column(
        String(16),
        nullable=False,
        default="active",
        server_default=text("'active'"),
    )
    quota_bytes = Column(BigInteger, nullable=False)
    used_bytes = Column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    last_accessed_at = Column(DateTime, nullable=True)
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
        onupdate=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class Asset(Base):
    """全局内容寻址的不可变资产元数据。"""

    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_asset_size_nonnegative"),
    )

    sha256 = Column(String(64), primary_key=True)
    size_bytes = Column(BigInteger, nullable=False)
    media_type = Column(String(255), nullable=False, default="application/octet-stream")
    storage_key = Column(String(255), nullable=False, unique=True)
    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class WorkspaceAsset(Base):
    """Workspace 对物理 Asset 的不可变版本与 owner ACL 快照。"""

    __tablename__ = "workspace_assets"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "logical_name",
            "version",
            name="uq_workspace_asset_logical_version",
        ),
        UniqueConstraint(
            "workspace_id",
            "asset_sha256",
            "logical_name",
            name="uq_workspace_asset_link",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_workspace_asset_version_positive",
        ),
        CheckConstraint(
            "source_kind IN ('legacy', 'upload', 'import', 'tool', 'model', 'runtime')",
            name="ck_workspace_asset_source_kind",
        ),
        CheckConstraint(
            "acl_owner_type IN ('user', 'group', 'project')",
            name="ck_workspace_asset_acl_owner_type",
        ),
        Index("ix_workspace_asset_artifact_id", "artifact_id", unique=True),
        Index("ix_workspace_asset_sha256", "asset_sha256"),
        Index("ix_workspace_asset_source_run_id", "source_run_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    artifact_id = Column(String(64), nullable=False)
    asset_sha256 = Column(
        String(64),
        ForeignKey("assets.sha256", ondelete="RESTRICT"),
        nullable=False,
    )
    logical_name = Column(String(512), nullable=False)
    version = Column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    source_run_id = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    source_kind = Column(
        String(16),
        nullable=False,
        default="legacy",
        server_default=text("'legacy'"),
    )
    acl_platform = Column(String(32), nullable=False)
    acl_owner_type = Column(String(16), nullable=False)
    acl_owner_id = Column(String(255), nullable=False)
    acl_sha256 = Column(String(64), nullable=False)
    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class SandboxLease(Base):
    """可复用 Sandbox 容器的 Server 侧控制账本。"""

    __tablename__ = "sandbox_leases"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'provisioning', 'active', 'idle', 'stopping', "
            "'stopped', 'expired', 'destroyed', 'failed'"
            ")",
            name="ck_sandbox_lease_status",
        ),
        Index("ix_sandbox_lease_grant_id", "grant_id"),
        Index(
            "ix_sandbox_lease_workspace_status",
            "workspace_id",
            "status",
        ),
        Index(
            "ix_sandbox_lease_status_expiry",
            "status",
            "idle_expires_at",
            "max_expires_at",
        ),
        Index(
            "uq_sandbox_lease_key_current",
            "lease_key",
            unique=True,
            sqlite_where=text(
                "status IN ('provisioning', 'active', 'idle', 'stopping')"
            ),
            postgresql_where=text(
                "status IN ('provisioning', 'active', 'idle', 'stopping')"
            ),
        ),
    )

    lease_id = Column(String(64), primary_key=True)
    lease_key = Column(String(64), nullable=False)
    grant_id = Column(
        String(36),
        ForeignKey("sandbox_access_grants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    chat_stream_id = Column(String(512), nullable=False)
    workspace_id = Column(
        String(36),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
    )
    profile_id = Column(String(32), nullable=False)
    catalog_generation = Column(String(64), nullable=False)
    policy_sha256 = Column(String(64), nullable=False)
    status = Column(
        String(16),
        nullable=False,
        default="provisioning",
        server_default=text("'provisioning'"),
    )
    image_digest = Column(
        String(255),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    controller_epoch = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    last_active_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    idle_expires_at = Column(DateTime, nullable=True)
    max_expires_at = Column(DateTime, nullable=True)
    stopped_at = Column(DateTime, nullable=True)
    reconciled_at = Column(DateTime, nullable=True)
    last_error_code = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    last_error_summary = Column(
        String(255),
        nullable=False,
        default="",
        server_default=text("''"),
    )


class SandboxControllerState(Base):
    """Server 侧 sandboxd epoch 与周期 reconciler leader fencing。"""

    __tablename__ = "sandbox_controller_states"
    __table_args__ = (
        CheckConstraint(
            "state_key = 'sandboxd'",
            name="ck_sandbox_controller_state_key",
        ),
    )

    state_key = Column(
        String(32),
        primary_key=True,
        default="sandboxd",
        server_default=text("'sandboxd'"),
    )
    controller_epoch = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    leader_owner = Column(
        String(128),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    leader_token = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    leader_expires_at = Column(DateTime, nullable=True)
    reconciled_at = Column(DateTime, nullable=True)
    last_error_code = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    last_error_summary = Column(
        String(255),
        nullable=False,
        default="",
        server_default=text("''"),
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
        onupdate=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class SandboxRun(Base):
    """Sandbox 命令的运行账本；不持久化命令和输出正文。"""

    __tablename__ = "sandbox_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_sandbox_run_status",
        ),
        CheckConstraint(
            "profile_id IN ('restricted', 'developer', 'trusted_developer')",
            name="ck_sandbox_run_profile",
        ),
        CheckConstraint(
            "execution_mode IN ('oneshot', 'lease')",
            name="ck_sandbox_run_execution_mode",
        ),
        CheckConstraint(
            "process_state IN ("
            "'not_applicable', 'starting', 'running', 'exited', 'lost'"
            ")",
            name="ck_sandbox_run_process_state",
        ),
        CheckConstraint("cpu_time_ms >= 0", name="ck_sandbox_run_cpu_nonnegative"),
        CheckConstraint(
            "peak_memory_bytes >= 0",
            name="ck_sandbox_run_memory_nonnegative",
        ),
        CheckConstraint(
            "stdout_bytes >= 0 AND stderr_bytes >= 0",
            name="ck_sandbox_run_output_nonnegative",
        ),
        Index("ix_sandbox_run_workspace_created", "workspace_id", "created_at"),
        Index("ix_sandbox_run_status_created", "status", "created_at"),
    )

    run_id = Column(String(64), primary_key=True)
    request_id = Column(String(64), nullable=False, unique=True, index=True)
    workspace_id = Column(
        String(36),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
    )
    lease_id = Column(
        String(64),
        ForeignKey("sandbox_leases.lease_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    profile_id = Column(
        String(32),
        nullable=False,
        default="restricted",
        server_default=text("'restricted'"),
    )
    execution_mode = Column(
        String(16),
        nullable=False,
        default="oneshot",
        server_default=text("'oneshot'"),
    )
    process_state = Column(
        String(16),
        nullable=False,
        default="not_applicable",
        server_default=text("'not_applicable'"),
    )
    trace_id = Column(String(64), nullable=False, default="", index=True)
    agent_run_id = Column(String(64), nullable=False, default="", index=True)
    tool_call_id = Column(String(64), nullable=False, default="", index=True)
    image_digest = Column(String(255), nullable=False)
    status = Column(
        String(16),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    exit_code = Column(Integer, nullable=True)
    termination_reason = Column(String(64), nullable=False, default="")
    cpu_time_ms = Column(Integer, nullable=False, default=0, server_default=text("0"))
    peak_memory_bytes = Column(BigInteger, nullable=False, default=0, server_default=text("0"))
    stdout_bytes = Column(BigInteger, nullable=False, default=0, server_default=text("0"))
    stderr_bytes = Column(BigInteger, nullable=False, default=0, server_default=text("0"))
    stdout_truncated = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("0"),
    )
    stderr_truncated = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("0"),
    )
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
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
        onupdate=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class SandboxAccessGrant(Base):
    """canonical chat session 的 Sandbox 能力授权。"""

    __tablename__ = "sandbox_access_grants"
    __table_args__ = (
        CheckConstraint(
            "chat_type IN ('private', 'group')",
            name="ck_sandbox_access_grant_chat_type",
        ),
        CheckConstraint(
            "capability_level IN ('off', 'workspace', 'assets', 'exec')",
            name="ck_sandbox_access_grant_capability",
        ),
        CheckConstraint(
            "execution_profile IN "
            "('restricted', 'developer', 'trusted_developer')",
            name="ck_sandbox_access_grant_execution_profile",
        ),
        CheckConstraint(
            "status IN ('provisioning', 'active', 'disabled', 'error')",
            name="ck_sandbox_access_grant_status",
        ),
        CheckConstraint("version >= 1", name="ck_sandbox_access_grant_version"),
        UniqueConstraint(
            "platform",
            "chat_type",
            "external_session_id",
            name="uq_sandbox_access_grant_external_session",
        ),
        Index(
            "ix_sandbox_access_grant_platform_type",
            "platform",
            "chat_type",
        ),
    )

    id = Column(String(36), primary_key=True)
    chat_stream_id = Column(String(512), nullable=False, unique=True, index=True)
    platform = Column(String(32), nullable=False)
    chat_type = Column(String(16), nullable=False)
    external_session_id = Column(String(255), nullable=False)
    workspace_id = Column(
        String(36),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    capability_level = Column(
        String(16),
        nullable=False,
        default="off",
        server_default=text("'off'"),
    )
    execution_profile = Column(
        String(32),
        nullable=False,
        default="restricted",
        server_default=text("'restricted'"),
    )
    status = Column(
        String(16),
        nullable=False,
        default="disabled",
        server_default=text("'disabled'"),
    )
    version = Column(Integer, nullable=False, default=1, server_default=text("1"))
    reason = Column(Text, nullable=False, default="", server_default=text("''"))
    created_by = Column(String(128), nullable=False, default="", server_default=text("''"))
    updated_by = Column(String(128), nullable=False, default="", server_default=text("''"))
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
        onupdate=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class WorkspaceQuotaBinding(Base):
    """Workspace 与宿主 project quota 的期望和已应用状态。"""

    __tablename__ = "workspace_quota_bindings"
    __table_args__ = (
        CheckConstraint("project_id >= 10000", name="ck_workspace_quota_project_id"),
        CheckConstraint(
            "desired_quota_bytes > 0",
            name="ck_workspace_quota_desired_positive",
        ),
        CheckConstraint(
            "applied_quota_bytes >= 0",
            name="ck_workspace_quota_applied_nonnegative",
        ),
        CheckConstraint(
            "status IN ('pending', 'applying', 'applied', 'error')",
            name="ck_workspace_quota_status",
        ),
        CheckConstraint("generation >= 1", name="ck_workspace_quota_generation"),
    )

    workspace_id = Column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    project_id = Column(Integer, nullable=False, unique=True, index=True)
    desired_quota_bytes = Column(BigInteger, nullable=False)
    applied_quota_bytes = Column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    status = Column(
        String(16),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    generation = Column(Integer, nullable=False, default=1, server_default=text("1"))
    last_error_code = Column(String(64), nullable=False, default="", server_default=text("''"))
    last_error_summary = Column(String(255), nullable=False, default="", server_default=text("''"))
    last_applied_at = Column(DateTime, nullable=True)
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
        onupdate=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class WorkspaceRuntimeQuotaBinding(Base):
    """Workspace 的可重建 Runtime 与独立宿主 project quota 绑定。"""

    __tablename__ = "workspace_runtime_quota_bindings"
    __table_args__ = (
        CheckConstraint(
            "project_id >= 10000",
            name="ck_workspace_runtime_quota_project_id",
        ),
        CheckConstraint(
            "desired_quota_bytes > 0",
            name="ck_workspace_runtime_quota_desired_positive",
        ),
        CheckConstraint(
            "applied_quota_bytes >= 0",
            name="ck_workspace_runtime_quota_applied_nonnegative",
        ),
        CheckConstraint(
            "status IN ('pending', 'applying', 'applied', 'error')",
            name="ck_workspace_runtime_quota_status",
        ),
        CheckConstraint(
            "generation >= 1",
            name="ck_workspace_runtime_quota_generation",
        ),
    )

    workspace_id = Column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    project_id = Column(Integer, nullable=False, unique=True, index=True)
    desired_quota_bytes = Column(BigInteger, nullable=False)
    applied_quota_bytes = Column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    status = Column(
        String(16),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    generation = Column(Integer, nullable=False, default=1, server_default=text("1"))
    last_error_code = Column(String(64), nullable=False, default="", server_default=text("''"))
    last_error_summary = Column(String(255), nullable=False, default="", server_default=text("''"))
    last_applied_at = Column(DateTime, nullable=True)
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
        onupdate=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class WorkspaceMaintenanceState(Base):
    """Workspace 配额维护门禁、fencing 与已应用代际。"""

    __tablename__ = "workspace_maintenance_states"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ready', 'quiescing', 'error')",
            name="ck_workspace_maintenance_status",
        ),
        CheckConstraint(
            "generation >= 1",
            name="ck_workspace_maintenance_generation",
        ),
        CheckConstraint(
            "applied_quota_generation >= 0",
            name="ck_workspace_maintenance_applied_generation",
        ),
    )

    workspace_id = Column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    status = Column(
        String(16),
        nullable=False,
        default="quiescing",
        server_default=text("'quiescing'"),
    )
    generation = Column(Integer, nullable=False, default=1, server_default=text("1"))
    applied_quota_generation = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    locked_by = Column(
        String(128),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    fencing_token = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    lease_expires_at = Column(DateTime, nullable=True)
    last_error_code = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    last_error_summary = Column(
        String(255),
        nullable=False,
        default="",
        server_default=text("''"),
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
        onupdate=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class SandboxAdminOperation(Base):
    """跨数据库与 sandboxd 的可恢复管理操作账本。"""

    __tablename__ = "sandbox_admin_operations"
    __table_args__ = (
        CheckConstraint(
            "operation_type IN ("
            "'set_access', 'set_quota', 'bind_workspace', 'import_quota', "
            "'lease_stop', 'lease_destroy', 'lease_recreate', 'kill_switch'"
            ")",
            name="ck_sandbox_admin_operation_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_sandbox_admin_operation_status",
        ),
        CheckConstraint(
            "desired_capability IN ('', 'off', 'workspace', 'assets', 'exec')",
            name="ck_sandbox_admin_desired_capability",
        ),
        CheckConstraint(
            "previous_capability IN ('', 'off', 'workspace', 'assets', 'exec')",
            name="ck_sandbox_admin_previous_capability",
        ),
        CheckConstraint("desired_quota_bytes >= 0", name="ck_sandbox_admin_desired_quota"),
        CheckConstraint("attempt_count >= 0", name="ck_sandbox_admin_attempt_count"),
        CheckConstraint("max_attempts >= 1", name="ck_sandbox_admin_max_attempts"),
        Index(
            "ix_sandbox_admin_operation_status_retry",
            "status",
            "next_attempt_at",
        ),
        Index(
            "ix_sandbox_admin_operation_session_created",
            "chat_stream_id",
            "created_at",
        ),
    )

    operation_id = Column(String(64), primary_key=True)
    request_id = Column(String(64), nullable=False, unique=True, index=True)
    operation_type = Column(String(32), nullable=False)
    chat_stream_id = Column(String(512), nullable=False, default="", server_default=text("''"))
    workspace_id = Column(
        String(36),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    desired_capability = Column(String(16), nullable=False, default="", server_default=text("''"))
    previous_capability = Column(String(16), nullable=False, default="", server_default=text("''"))
    desired_quota_bytes = Column(BigInteger, nullable=False, default=0, server_default=text("0"))
    expected_grant_version = Column(Integer, nullable=True)
    expected_quota_generation = Column(Integer, nullable=True)
    status = Column(
        String(16),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    step = Column(String(64), nullable=False, default="queued", server_default=text("'queued'"))
    attempt_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    max_attempts = Column(Integer, nullable=False, default=5, server_default=text("5"))
    locked_by = Column(String(128), nullable=False, default="", server_default=text("''"))
    lease_token = Column(String(64), nullable=False, default="", server_default=text("''"))
    lease_expires_at = Column(DateTime, nullable=True)
    next_attempt_at = Column(DateTime, nullable=True)
    error_code = Column(String(64), nullable=False, default="", server_default=text("''"))
    error_summary = Column(String(255), nullable=False, default="", server_default=text("''"))
    reason = Column(String(255), nullable=False, default="", server_default=text("''"))
    created_by = Column(String(128), nullable=False, default="", server_default=text("''"))
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
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
        onupdate=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class SandboxProjectSequence(Base):
    """原子分配 project ID 的数据库序列。"""

    __tablename__ = "sandbox_project_sequences"
    __table_args__ = (
        CheckConstraint("next_value >= 10000", name="ck_sandbox_project_sequence_value"),
    )

    name = Column(String(32), primary_key=True)
    next_value = Column(Integer, nullable=False, default=10000, server_default=text("10000"))
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


__all__ = [
    "Asset",
    "SANDBOX_EXECUTION_PROFILES",
    "SANDBOX_LEASE_NONTERMINAL_STATUSES",
    "SANDBOX_LEASE_STATUSES",
    "SANDBOX_LEASE_TERMINAL_STATUSES",
    "SandboxAccessGrant",
    "SandboxAdminOperation",
    "SandboxControllerState",
    "SandboxLease",
    "SandboxProjectSequence",
    "SandboxRun",
    "Workspace",
    "WorkspaceAsset",
    "WorkspaceMaintenanceState",
    "WorkspaceQuotaBinding",
    "WorkspaceRuntimeQuotaBinding",
]

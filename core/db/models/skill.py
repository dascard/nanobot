"""Agent Skill 不可变版本、作用域绑定与生命周期事件。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)

from core.db.base import Base


class SkillPackageRow(Base):
    """一个经过严格校验的不可变 SKILL.md 版本。"""

    __tablename__ = "skill_packages"
    __table_args__ = (
        UniqueConstraint(
            "scope",
            "scope_key",
            "skill_name",
            "version",
            name="uq_skill_package_scope_version",
        ),
        CheckConstraint(
            "scope IN ('builtin', 'project', 'agent', 'user')",
            name="ck_skill_package_scope",
        ),
        CheckConstraint(
            "source_kind IN ('bundled', 'managed')",
            name="ck_skill_package_source_kind",
        ),
        CheckConstraint(
            "trusted IN (0, 1)",
            name="ck_skill_package_trusted",
        ),
        CheckConstraint(
            "skill_md_size > 0 AND bundle_size > 0 AND file_count >= 0",
            name="ck_skill_package_sizes",
        ),
        Index(
            "ix_skill_package_scope_name",
            "scope",
            "scope_key",
            "skill_name",
        ),
        Index("ix_skill_package_bundle_sha256", "bundle_sha256"),
    )

    package_id = Column(String(80), primary_key=True)
    scope = Column(String(16), nullable=False)
    scope_key = Column(String(255), nullable=False)
    skill_name = Column(String(64), nullable=False)
    version = Column(String(64), nullable=False)
    description = Column(String(1024), nullable=False)
    license_text = Column(
        String(512), nullable=False, default="", server_default=text("''")
    )
    compatibility = Column(
        String(500), nullable=False, default="", server_default=text("''")
    )
    metadata_json = Column(Text, nullable=False, default="{}")
    allowed_tools_json = Column(Text, nullable=False, default="[]")
    dependencies_json = Column(Text, nullable=False, default="[]")
    required_permissions_json = Column(Text, nullable=False, default="[]")
    skill_md = Column(LargeBinary, nullable=False)
    skill_md_sha256 = Column(String(64), nullable=False)
    skill_md_size = Column(Integer, nullable=False)
    bundle_sha256 = Column(String(64), nullable=False)
    bundle_size = Column(Integer, nullable=False)
    file_count = Column(Integer, nullable=False)
    source_kind = Column(String(16), nullable=False)
    source_label = Column(
        String(255), nullable=False, default="", server_default=text("''")
    )
    trusted = Column(Boolean, nullable=False, default=False)
    created_by = Column(String(255), nullable=False)
    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class SkillPackageFileRow(Base):
    """Skill 资源文件；只允许按相对路径读取，不具有执行语义。"""

    __tablename__ = "skill_package_files"
    __table_args__ = (
        UniqueConstraint(
            "package_id",
            "relative_path",
            name="uq_skill_package_file_path",
        ),
        CheckConstraint(
            "size_bytes > 0",
            name="ck_skill_package_file_size",
        ),
        Index("ix_skill_package_file_sha256", "content_sha256"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    package_id = Column(
        String(80),
        ForeignKey("skill_packages.package_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relative_path = Column(String(512), nullable=False)
    media_type = Column(String(128), nullable=False)
    content = Column(LargeBinary, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=False)


class SkillBindingRow(Base):
    """同一作用域内一个 Skill 的可变激活投影。"""

    __tablename__ = "skill_bindings"
    __table_args__ = (
        UniqueConstraint(
            "scope",
            "scope_key",
            "skill_name",
            name="uq_skill_binding_scope_name",
        ),
        CheckConstraint(
            "scope IN ('project', 'agent', 'user')",
            name="ck_skill_binding_scope",
        ),
        CheckConstraint(
            "status IN ('active', 'uninstalled')",
            name="ck_skill_binding_status",
        ),
        CheckConstraint(
            "pinned IN (0, 1) AND trusted IN (0, 1)",
            name="ck_skill_binding_flags",
        ),
        CheckConstraint(
            "generation >= 1",
            name="ck_skill_binding_generation",
        ),
        CheckConstraint(
            "(status = 'active' AND active_package_id IS NOT NULL) "
            "OR (status = 'uninstalled' AND active_package_id IS NULL)",
            name="ck_skill_binding_active_package",
        ),
        Index(
            "ix_skill_binding_scope_status",
            "scope",
            "scope_key",
            "status",
        ),
    )

    binding_id = Column(String(80), primary_key=True)
    scope = Column(String(16), nullable=False)
    scope_key = Column(String(255), nullable=False)
    skill_name = Column(String(64), nullable=False)
    active_package_id = Column(
        String(80),
        ForeignKey("skill_packages.package_id"),
        nullable=True,
    )
    previous_package_id = Column(
        String(80),
        ForeignKey("skill_packages.package_id"),
        nullable=True,
    )
    status = Column(String(16), nullable=False, default="active")
    pinned = Column(Boolean, nullable=False, default=True)
    trusted = Column(Boolean, nullable=False, default=False)
    generation = Column(Integer, nullable=False, default=1)
    updated_by = Column(String(255), nullable=False)
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


class SkillLifecycleEventRow(Base):
    """Skill 管理动作的 append-only 审计事实，不保存正文。"""

    __tablename__ = "skill_lifecycle_events"
    __table_args__ = (
        UniqueConstraint(
            "binding_id",
            "generation",
            name="uq_skill_lifecycle_binding_generation",
        ),
        CheckConstraint(
            "event_kind IN ("
            "'installed', 'version_added', 'upgraded', 'rolled_back', "
            "'pinned', 'unpinned', 'uninstalled', 'reinstalled'"
            ")",
            name="ck_skill_lifecycle_event_kind",
        ),
        CheckConstraint(
            "generation >= 1",
            name="ck_skill_lifecycle_generation",
        ),
        Index(
            "ix_skill_lifecycle_binding_time",
            "binding_id",
            "occurred_at",
        ),
    )

    event_id = Column(String(80), primary_key=True)
    binding_id = Column(
        String(80),
        ForeignKey("skill_bindings.binding_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    generation = Column(Integer, nullable=False)
    event_kind = Column(String(32), nullable=False)
    previous_package_id = Column(
        String(80), nullable=False, default="", server_default=text("''")
    )
    current_package_id = Column(
        String(80), nullable=False, default="", server_default=text("''")
    )
    previous_pinned = Column(Boolean, nullable=False, default=False)
    current_pinned = Column(Boolean, nullable=False, default=False)
    actor_id = Column(String(255), nullable=False)
    event_sha256 = Column(String(64), nullable=False)
    occurred_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


__all__ = [
    "SkillBindingRow",
    "SkillLifecycleEventRow",
    "SkillPackageFileRow",
    "SkillPackageRow",
]

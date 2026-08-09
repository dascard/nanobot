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


class SkillInvocationRow(Base):
    """一次精确 Skill 版本加载事实，用于成功率和 Prompt 成本统计。"""

    __tablename__ = "skill_invocations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('succeeded', 'failed')",
            name="ck_skill_invocation_status",
        ),
        CheckConstraint(
            "scope IN ('builtin', 'project', 'agent', 'user')",
            name="ck_skill_invocation_scope",
        ),
        CheckConstraint(
            "result_kind IN ('body', 'resource')",
            name="ck_skill_invocation_result_kind",
        ),
        CheckConstraint(
            "prompt_tokens >= 0 AND resource_bytes >= 0 AND latency_ms >= 0",
            name="ck_skill_invocation_costs",
        ),
        Index(
            "ix_skill_invocation_package_time",
            "package_id",
            "occurred_at",
        ),
    )

    invocation_id = Column(String(80), primary_key=True)
    package_id = Column(String(80), nullable=False, index=True)
    skill_name = Column(String(64), nullable=False, index=True)
    version = Column(String(64), nullable=False)
    scope = Column(String(16), nullable=False)
    lock_sha256 = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False)
    result_kind = Column(String(16), nullable=False)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    resource_bytes = Column(Integer, nullable=False, default=0)
    latency_ms = Column(Integer, nullable=False, default=0)
    error_code = Column(
        String(128), nullable=False, default="", server_default=text("''")
    )
    run_id = Column(
        String(128), nullable=False, default="", server_default=text("''")
    )
    trace_id = Column(
        String(128), nullable=False, default="", server_default=text("''")
    )
    occurred_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class SkillEvaluationRow(Base):
    """针对不可变 Skill 版本的追加式离线或灰度评测结果。"""

    __tablename__ = "skill_evaluations"
    __table_args__ = (
        CheckConstraint(
            "passed IN (0, 1)",
            name="ck_skill_evaluation_passed",
        ),
        CheckConstraint(
            "score_micros >= 0 AND score_micros <= 1000000",
            name="ck_skill_evaluation_score",
        ),
        CheckConstraint(
            "prompt_tokens >= 0 AND cost_microunits >= 0",
            name="ck_skill_evaluation_costs",
        ),
        Index(
            "ix_skill_evaluation_package_time",
            "package_id",
            "occurred_at",
        ),
    )

    evaluation_id = Column(String(80), primary_key=True)
    package_id = Column(String(80), nullable=False, index=True)
    skill_name = Column(String(64), nullable=False, index=True)
    version = Column(String(64), nullable=False)
    bundle_sha256 = Column(String(64), nullable=False)
    suite_id = Column(String(128), nullable=False, index=True)
    evaluator_id = Column(String(128), nullable=False)
    evaluator_version = Column(String(64), nullable=False)
    passed = Column(Boolean, nullable=False)
    score_micros = Column(Integer, nullable=False)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    cost_microunits = Column(Integer, nullable=False, default=0)
    evidence_sha256 = Column(String(64), nullable=False)
    created_by = Column(String(255), nullable=False)
    occurred_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class SkillCandidatePublicationIntentRow(Base):
    """候选发布的权威事务意图；文件回执只是可重建投影。"""

    __tablename__ = "skill_candidate_publication_intents"
    __table_args__ = (
        UniqueConstraint(
            "approval_id",
            name="uq_skill_candidate_publication_approval",
        ),
        CheckConstraint(
            "status IN ('pending', 'ambiguous', 'finalized')",
            name="ck_skill_candidate_publication_status",
        ),
        CheckConstraint(
            "reconcile_attempts >= 0",
            name="ck_skill_candidate_publication_attempts",
        ),
        Index(
            "ix_skill_candidate_publication_status_time",
            "status",
            "updated_at",
        ),
    )

    publication_id = Column(String(80), primary_key=True)
    approval_id = Column(String(128), nullable=False)
    candidate_sha256 = Column(String(64), nullable=False, index=True)
    gate_report_sha256 = Column(String(64), nullable=False)
    approval_token_sha256 = Column(String(64), nullable=False)
    publication_sha256 = Column(String(64), nullable=False)
    package_id = Column(
        String(80),
        ForeignKey("skill_packages.package_id"),
        nullable=False,
    )
    binding_id = Column(
        String(80),
        ForeignKey("skill_bindings.binding_id"),
        nullable=False,
    )
    evaluation_id = Column(
        String(80),
        ForeignKey("skill_evaluations.evaluation_id"),
        nullable=False,
    )
    receipt_json = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    reconcile_attempts = Column(Integer, nullable=False, default=0)
    last_error_code = Column(
        String(128), nullable=False, default="", server_default=text("''")
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
        server_default=text("CURRENT_TIMESTAMP"),
    )
    finalized_at = Column(DateTime, nullable=True)


__all__ = [
    "SkillBindingRow",
    "SkillCandidatePublicationIntentRow",
    "SkillEvaluationRow",
    "SkillInvocationRow",
    "SkillLifecycleEventRow",
    "SkillPackageFileRow",
    "SkillPackageRow",
]

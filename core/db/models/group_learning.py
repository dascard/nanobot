"""群学习白名单、增量游标、候选、证据和运行账本模型。"""

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
    String,
    Text,
    UniqueConstraint,
    text,
)

from core.db.base import Base
from core.group_learning.states import (
    GROUP_LEARNING_CANDIDATE_SOURCES,
    GROUP_LEARNING_CANDIDATE_STATUSES,
    GROUP_LEARNING_RUN_STATUSES,
    sql_string_values,
)


class GroupLearningSchedule(Base):
    """显式存在且启用的 canonical 群会话才进入自动学习白名单。"""

    __tablename__ = "group_learning_schedules"
    __table_args__ = (
        CheckConstraint(
            "interval_minutes >= 15",
            name="ck_group_learning_schedule_interval",
        ),
        CheckConstraint(
            "window_hours >= 1",
            name="ck_group_learning_schedule_window",
        ),
        CheckConstraint(
            "consecutive_failures >= 0",
            name="ck_group_learning_schedule_failures",
        ),
        CheckConstraint(
            "config_generation >= 1",
            name="ck_group_learning_schedule_generation",
        ),
        CheckConstraint(
            "lease_generation >= 0",
            name="ck_group_learning_schedule_lease_generation",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_group_learning_schedule_attempt_count",
        ),
        Index(
            "ix_group_learning_schedule_due",
            "enabled",
            "next_run_at",
        ),
        Index(
            "ix_group_learning_schedule_lease",
            "lease_expires_at",
        ),
    )

    chat_stream_id = Column(String(512), primary_key=True)
    enabled = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("1"),
    )
    aspects_json = Column(
        Text,
        nullable=False,
        default="[]",
        server_default=text("'[]'"),
    )
    interval_minutes = Column(
        Integer,
        nullable=False,
        default=1440,
        server_default=text("1440"),
    )
    window_hours = Column(
        Integer,
        nullable=False,
        default=24,
        server_default=text("24"),
    )
    next_run_at = Column(DateTime, nullable=True)
    last_started_at = Column(DateTime, nullable=True)
    last_completed_at = Column(DateTime, nullable=True)
    lease_owner = Column(
        String(128),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    lease_token = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    lease_expires_at = Column(DateTime, nullable=True)
    consecutive_failures = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    last_error_code = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    config_generation = Column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    lease_generation = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    attempt_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
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


class GroupLearningStreamState(Base):
    """群学习增量扫描和成功治理游标；不承载调度配置。"""

    __tablename__ = "group_learning_stream_states"
    __table_args__ = (
        CheckConstraint(
            "last_scanned_chat_log_id >= 0",
            name="ck_group_learning_state_scanned_cursor",
        ),
        CheckConstraint(
            "last_success_chat_log_id >= 0",
            name="ck_group_learning_state_success_cursor",
        ),
        CheckConstraint(
            "last_candidate_watermark >= 0",
            name="ck_group_learning_state_candidate_watermark",
        ),
        CheckConstraint(
            "rules_generation >= 0",
            name="ck_group_learning_state_rules_generation",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_group_learning_state_version",
        ),
    )

    chat_stream_id = Column(String(512), primary_key=True)
    last_scanned_chat_log_id = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    last_success_chat_log_id = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    last_candidate_watermark = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    rules_generation = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    last_success_run_id = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    last_success_at = Column(DateTime, nullable=True)
    last_error_code = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    version = Column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class GroupLearningCandidate(Base):
    """规则、模型、旧数据或人工产生的待治理候选。"""

    __tablename__ = "group_learning_candidates"
    __table_args__ = (
        CheckConstraint(
            "candidate_type IN ('topic', 'expression', 'slang', 'style')",
            name="ck_group_learning_candidate_type",
        ),
        CheckConstraint(
            "source IN ("
            + sql_string_values(GROUP_LEARNING_CANDIDATE_SOURCES)
            + ")",
            name="ck_group_learning_candidate_source",
        ),
        CheckConstraint(
            "status IN ("
            + sql_string_values(GROUP_LEARNING_CANDIDATE_STATUSES)
            + ")",
            name="ck_group_learning_candidate_status",
        ),
        CheckConstraint(
            "approval_source IS NULL OR "
            "approval_source IN ('human', 'model')",
            name="ck_group_learning_candidate_approval_source",
        ),
        CheckConstraint(
            "rule_version >= 0",
            name="ck_group_learning_candidate_rule_version",
        ),
        CheckConstraint(
            "hit_count >= 1",
            name="ck_group_learning_candidate_hit_count",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_group_learning_candidate_version",
        ),
        UniqueConstraint(
            "chat_stream_id",
            "candidate_type",
            "fingerprint",
            name="uq_group_learning_candidate_fingerprint",
        ),
        Index(
            "ix_group_learning_candidate_stream_status",
            "chat_stream_id",
            "status",
            "id",
        ),
        Index(
            "ix_group_learning_candidate_stream_type_key",
            "chat_stream_id",
            "candidate_type",
            "normalized_key",
        ),
        Index(
            "ix_group_learning_candidate_conflict_group",
            "conflict_group_id",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    chat_stream_id = Column(String(512), nullable=False, index=True)
    candidate_type = Column(String(24), nullable=False)
    content = Column(Text, nullable=False)
    meaning = Column(
        Text,
        nullable=False,
        default="",
        server_default=text("''"),
    )
    normalized_key = Column(String(512), nullable=False)
    fingerprint = Column(String(64), nullable=False)
    content_hash = Column(String(64), nullable=False)
    source = Column(String(32), nullable=False)
    status = Column(
        String(32),
        nullable=False,
        default="raw",
        server_default=text("'raw'"),
    )
    rule_id = Column(
        String(128),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    rule_version = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    first_seen_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    last_seen_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    hit_count = Column(
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
    model_decision = Column(
        String(32),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    model_contract_version = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    model_review_run_id = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    model_observed_at = Column(DateTime, nullable=True)
    observation_reason_hash = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    reviewed_content = Column(Text, nullable=True)
    reviewed_meaning = Column(Text, nullable=True)
    reviewed_content_hash = Column(String(64), nullable=True)
    merge_target_memory_id = Column(Integer, nullable=True)
    alias_target_memory_id = Column(Integer, nullable=True)
    promoted_group_memory_id = Column(Integer, nullable=True)
    conflict_group_id = Column(String(64), nullable=True)
    approval_source = Column(String(16), nullable=True)
    human_reviewer_id = Column(String(128), nullable=True)
    human_reviewed_at = Column(DateTime, nullable=True)
    human_action = Column(String(32), nullable=True)
    rejection_reason_code = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    waiting_reason_code = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    version = Column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
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


class GroupLearningEvidence(Base):
    """候选与可信 ChatLog 的规范化证据关联。"""

    __tablename__ = "group_learning_evidence"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "chat_log_id",
            name="uq_group_learning_evidence_candidate_log",
        ),
        Index(
            "ix_group_learning_evidence_candidate_created",
            "candidate_id",
            "created_at",
        ),
        Index(
            "ix_group_learning_evidence_chat_log",
            "chat_log_id",
        ),
        Index(
            "ix_group_learning_evidence_batch",
            "batch_id",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    evidence_id = Column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    candidate_id = Column(
        String(64),
        ForeignKey(
            "group_learning_candidates.candidate_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    chat_log_id = Column(Integer, nullable=False)
    sender_id = Column(String(255), nullable=False)
    source_run_id = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    batch_id = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    evidence_hash = Column(String(64), nullable=False)
    evidence_kind = Column(
        String(32),
        nullable=False,
        default="usage",
        server_default=text("'usage'"),
    )
    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class GroupLearningRun(Base):
    """一次扫描、模型审核或人工治理动作的运行账本。"""

    __tablename__ = "group_learning_runs"
    __table_args__ = (
        CheckConstraint(
            "trigger IN ('schedule', 'manual', 'tool', 'migration_review')",
            name="ck_group_learning_run_trigger",
        ),
        CheckConstraint(
            "status IN ("
            + sql_string_values(GROUP_LEARNING_RUN_STATUSES)
            + ")",
            name="ck_group_learning_run_status",
        ),
        CheckConstraint(
            "mode IN ('candidate_only', 'active')",
            name="ck_group_learning_run_mode",
        ),
        CheckConstraint(
            "cursor_start_chat_log_id >= 0 AND "
            "cursor_end_chat_log_id >= 0",
            name="ck_group_learning_run_cursor",
        ),
        CheckConstraint(
            "context_start_chat_log_id >= 0 AND "
            "context_end_chat_log_id >= 0",
            name="ck_group_learning_run_context_cursor",
        ),
        CheckConstraint(
            "candidate_watermark >= 0",
            name="ck_group_learning_run_candidate_watermark",
        ),
        CheckConstraint(
            "rules_generation >= 0",
            name="ck_group_learning_run_rules_generation",
        ),
        CheckConstraint(
            "raw_message_count >= 0 AND "
            "cleaned_message_count >= 0 AND "
            "eligible_message_count >= 0",
            name="ck_group_learning_run_message_counts",
        ),
        CheckConstraint(
            "candidate_count >= 0 AND accepted_count >= 0 AND "
            "rejected_count >= 0 AND conflict_count >= 0 AND "
            "waiting_count >= 0",
            name="ck_group_learning_run_decision_counts",
        ),
        CheckConstraint(
            "input_chars >= 0 AND input_tokens >= 0 AND "
            "output_tokens >= 0 AND total_tokens >= 0 AND "
            "latency_ms >= 0 AND attempt_count >= 0 AND "
            "raw_output_bytes >= 0 AND "
            "(cost_microusd IS NULL OR cost_microusd >= 0)",
            name="ck_group_learning_run_observation_metrics",
        ),
        Index(
            "ix_group_learning_run_stream_started",
            "chat_stream_id",
            "started_at",
        ),
        Index(
            "ix_group_learning_run_status_started",
            "status",
            "started_at",
        ),
    )

    run_id = Column(String(64), primary_key=True)
    idempotency_key = Column(
        String(128),
        nullable=False,
        unique=True,
        index=True,
    )
    chat_stream_id = Column(String(512), nullable=False, index=True)
    trigger = Column(String(32), nullable=False)
    mode = Column(
        String(24),
        nullable=False,
        default="candidate_only",
        server_default=text("'candidate_only'"),
    )
    selected_aspects_json = Column(
        Text,
        nullable=False,
        default="[]",
        server_default=text("'[]'"),
    )
    cursor_start_chat_log_id = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    cursor_end_chat_log_id = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    context_start_chat_log_id = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    context_end_chat_log_id = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    candidate_watermark = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    rules_generation = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    task_contract_version = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    model_route = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    provider = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    model = Column(
        String(255),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    task_run_id = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    status = Column(
        String(32),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    raw_message_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    cleaned_message_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    eligible_message_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    candidate_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    accepted_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    rejected_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    conflict_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    waiting_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    error_code = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    input_chars = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    input_tokens = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    output_tokens = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    total_tokens = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    cost_microusd = Column(Integer, nullable=True)
    latency_ms = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    attempt_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    raw_output_bytes = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    raw_output_sha256 = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    trace_id = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    job_id = Column(
        String(64),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    started_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    completed_at = Column(DateTime, nullable=True)
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


__all__ = [
    "GroupLearningCandidate",
    "GroupLearningEvidence",
    "GroupLearningRun",
    "GroupLearningSchedule",
    "GroupLearningStreamState",
]

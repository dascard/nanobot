"""群学习只读 DTO 与 Repository Port。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class GroupLearningScheduleRecord:
    chat_stream_id: str
    enabled: bool
    aspects_json: str
    interval_minutes: int
    window_hours: int
    next_run_at: datetime | None
    last_started_at: datetime | None
    last_completed_at: datetime | None
    lease_owner: str
    lease_token: str
    lease_expires_at: datetime | None
    consecutive_failures: int
    last_error_code: str
    config_generation: int
    lease_generation: int
    attempt_count: int
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class GroupLearningStreamStateRecord:
    chat_stream_id: str
    last_scanned_chat_log_id: int
    last_success_chat_log_id: int
    last_candidate_watermark: int
    rules_generation: int
    last_success_run_id: str
    last_success_at: datetime | None
    last_error_code: str
    version: int
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class GroupLearningCandidateRecord:
    id: int
    candidate_id: str
    chat_stream_id: str
    candidate_type: str
    content: str
    meaning: str
    normalized_key: str
    fingerprint: str
    content_hash: str
    source: str
    status: str
    rule_id: str
    rule_version: int
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    hit_count: int
    source_run_id: str
    model_decision: str
    model_contract_version: str
    model_review_run_id: str
    model_observed_at: datetime | None
    observation_reason_hash: str
    reviewed_content: str
    reviewed_meaning: str
    reviewed_content_hash: str
    merge_target_memory_id: int | None
    alias_target_memory_id: int | None
    promoted_group_memory_id: int | None
    conflict_group_id: str
    approval_source: str
    human_reviewer_id: str
    human_reviewed_at: datetime | None
    human_action: str
    rejection_reason_code: str
    waiting_reason_code: str
    version: int
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class GroupLearningEvidenceRecord:
    id: int
    evidence_id: str
    candidate_id: str
    chat_log_id: int
    sender_id: str
    source_run_id: str
    batch_id: str
    evidence_hash: str
    evidence_kind: str
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class GroupLearningRunRecord:
    run_id: str
    idempotency_key: str
    chat_stream_id: str
    trigger: str
    mode: str
    selected_aspects_json: str
    cursor_start_chat_log_id: int
    cursor_end_chat_log_id: int
    context_start_chat_log_id: int
    context_end_chat_log_id: int
    candidate_watermark: int
    rules_generation: int
    task_contract_version: str
    model_route: str
    provider: str
    model: str
    task_run_id: str
    status: str
    raw_message_count: int
    cleaned_message_count: int
    eligible_message_count: int
    candidate_count: int
    accepted_count: int
    rejected_count: int
    conflict_count: int
    waiting_count: int
    error_code: str
    input_chars: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_microusd: int | None
    latency_ms: int
    attempt_count: int
    raw_output_bytes: int
    raw_output_sha256: str
    trace_id: str
    job_id: str
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class LegacyGroupLearningRecord:
    source: str
    legacy_id: int
    chat_stream_id: str
    legacy_group_id: str
    candidate_type: str
    content: str
    meaning: str
    checked: bool
    status: str
    approval_source: str
    governance_mode: str
    approved_content_hash: str
    human_reviewer_id: str
    human_reviewed_at: datetime | None
    human_action: str
    human_proof_audit_log_id: int
    model_review_run_id: str
    model_contract_version: str
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class GroupLearningRunWrite:
    run_id: str
    idempotency_key: str
    chat_stream_id: str
    trigger: str
    selected_aspects_json: str
    cursor_start_chat_log_id: int
    cursor_end_chat_log_id: int
    context_start_chat_log_id: int
    context_end_chat_log_id: int
    rules_generation: int
    raw_message_count: int
    cleaned_message_count: int
    eligible_message_count: int
    trace_id: str = ""
    job_id: str = ""


@dataclass(frozen=True, slots=True)
class GroupLearningCandidateWrite:
    candidate_id: str
    chat_stream_id: str
    candidate_type: str
    content: str
    meaning: str
    normalized_key: str
    fingerprint: str
    content_hash: str
    source: str
    rule_id: str
    rule_version: int
    source_run_id: str


@dataclass(frozen=True, slots=True)
class GroupLearningEvidenceWrite:
    evidence_id: str
    candidate_id: str
    chat_log_id: int
    sender_id: str
    source_run_id: str
    batch_id: str
    evidence_hash: str
    evidence_kind: str


@dataclass(frozen=True, slots=True)
class GroupLearningBatchWrite:
    run: GroupLearningRunWrite
    candidates: tuple[GroupLearningCandidateWrite, ...]
    evidence: tuple[GroupLearningEvidenceWrite, ...]


@dataclass(frozen=True, slots=True)
class GroupLearningBatchPersistResult:
    run_id: str
    replayed: bool
    candidate_ids: tuple[str, ...]
    candidate_count: int
    evidence_added_count: int
    candidate_watermark: int
    candidate_created_count: int = 0
    candidate_updated_count: int = 0


@dataclass(frozen=True, slots=True)
class GroupLearningObservationWrite:
    candidate_id: str
    action: str
    reviewed_content: str
    reviewed_meaning: str
    reviewed_content_hash: str
    target_memory_id: int | None
    reason_hash: str


@dataclass(frozen=True, slots=True)
class GroupLearningObservationMetrics:
    task_run_id: str
    contract_version: str
    provider: str
    model: str
    input_chars: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_microusd: int | None
    latency_ms: int
    attempt_count: int
    raw_output_bytes: int
    raw_output_sha256: str
    route_key: str = "group_memory_learning"


@dataclass(frozen=True, slots=True)
class GroupLearningHumanReviewWrite:
    candidate_id: str
    reviewer_id: str
    action: str
    reviewed_content: str
    reviewed_meaning: str
    reviewed_content_hash: str
    reviewed_at: datetime


@runtime_checkable
class GroupLearningQueryRepositoryPort(Protocol):
    """只读查询面；阶段 7A 不暴露 add、update、commit 或 delete。"""

    def list_schedules(
        self,
        *,
        limit: int = 100,
    ) -> Sequence[GroupLearningScheduleRecord]: ...

    def get_schedule(
        self,
        chat_stream_id: str,
    ) -> GroupLearningScheduleRecord | None: ...

    def get_stream_state(
        self,
        chat_stream_id: str,
    ) -> GroupLearningStreamStateRecord | None: ...

    def get_candidate(
        self,
        candidate_id: str,
    ) -> GroupLearningCandidateRecord | None: ...

    def list_candidates(
        self,
        *,
        chat_stream_id: str,
        candidate_type: str = "",
        status: str = "",
        after_id: int = 0,
        limit: int = 100,
    ) -> Sequence[GroupLearningCandidateRecord]: ...

    def count_candidates(
        self,
        *,
        chat_stream_id: str,
    ) -> int: ...

    def list_evidence(
        self,
        *,
        candidate_id: str,
        after_id: int = 0,
        limit: int = 100,
    ) -> Sequence[GroupLearningEvidenceRecord]: ...

    def list_runs(
        self,
        *,
        chat_stream_id: str,
        limit: int = 100,
    ) -> Sequence[GroupLearningRunRecord]: ...

    def get_run(
        self,
        run_id: str,
    ) -> GroupLearningRunRecord | None: ...

    def list_legacy_records(
        self,
    ) -> Sequence[LegacyGroupLearningRecord]: ...


@runtime_checkable
class GroupLearningCommandRepositoryPort(Protocol):
    """阶段 7B 的 candidate-only 写面；不包含 GroupMemory 晋级方法。"""

    def persist_candidate_batch(
        self,
        write: GroupLearningBatchWrite,
    ) -> GroupLearningBatchPersistResult: ...

    def record_model_observation(
        self,
        *,
        run_id: str,
        observations: Sequence[GroupLearningObservationWrite],
        discoveries: Sequence[GroupLearningCandidateWrite],
        discovery_evidence: Sequence[GroupLearningEvidenceWrite],
        metrics: GroupLearningObservationMetrics,
        observed_at: datetime,
    ) -> None: ...

    def record_model_failure(
        self,
        *,
        run_id: str,
        error_code: str,
        metrics: GroupLearningObservationMetrics,
    ) -> None: ...

    def complete_report_only_run(
        self,
        *,
        run_id: str,
        completed_at: datetime,
    ) -> None: ...

    def apply_human_review(
        self,
        write: GroupLearningHumanReviewWrite,
    ) -> GroupLearningCandidateRecord: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


__all__ = [
    "GroupLearningBatchPersistResult",
    "GroupLearningBatchWrite",
    "GroupLearningCandidateWrite",
    "GroupLearningCandidateRecord",
    "GroupLearningCommandRepositoryPort",
    "GroupLearningEvidenceWrite",
    "GroupLearningEvidenceRecord",
    "GroupLearningHumanReviewWrite",
    "GroupLearningObservationMetrics",
    "GroupLearningObservationWrite",
    "GroupLearningQueryRepositoryPort",
    "GroupLearningRunRecord",
    "GroupLearningRunWrite",
    "GroupLearningScheduleRecord",
    "GroupLearningStreamStateRecord",
    "LegacyGroupLearningRecord",
]

"""群学习管理工作台的类型化 Admin API 模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class GroupLearningMutationRequest(BaseModel):
    request_id: str = Field(
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,63}$",
    )
    reason: str = Field(min_length=1, max_length=500)


class GroupLearningRegistryResponse(BaseModel):
    generation: int
    sha256: str


class GroupLearningAspectResponse(BaseModel):
    aspect_id: str
    display_name: str
    task_key: str
    schedule_default: bool
    writes_long_term_memory: bool
    memory_type: str
    prompt_injectable: bool
    owner_module: str
    lifecycle: str


class GroupLearningRuleResponse(BaseModel):
    rule_id: str
    version: int
    candidate_type: str
    owner_module: str
    canonicalizer_id: str
    max_input_chars: int
    max_matches_per_message: int
    max_candidates_per_batch: int
    scope: str
    positive_fixtures: list[str]
    negative_fixtures: list[str]
    performance_budget_ms: float
    lifecycle: str
    pattern: str
    extractor_id: str
    globally_enabled: bool


class GroupLearningEvidencePolicyResponse(BaseModel):
    policy_id: str
    candidate_type: str
    min_evidence_count: int
    min_sender_count: int
    explicit_evidence_kinds: list[str]
    min_explicit_evidence_count: int
    same_sender_cross_batch_min_hits: int
    same_sender_cross_batch_min_batches: int
    version: int
    owner_module: str


class GroupLearningSchedulePolicyResponse(BaseModel):
    policy_id: str
    version: str
    default_interval_minutes: int
    min_interval_minutes: int
    max_interval_minutes: int
    default_window_hours: int
    min_window_hours: int
    max_window_hours: int
    context_message_limit: int
    min_new_messages: int
    max_new_messages: int


class GroupLearningDescriptorsResponse(BaseModel):
    feature_enabled: bool
    aspect_registry: GroupLearningRegistryResponse
    rule_registry: GroupLearningRegistryResponse
    evidence_policy_registry: GroupLearningRegistryResponse
    aspects: list[GroupLearningAspectResponse]
    rules: list[GroupLearningRuleResponse]
    evidence_policies: list[GroupLearningEvidencePolicyResponse]
    schedule_policy: GroupLearningSchedulePolicyResponse
    candidate_types: list[str]
    candidate_statuses: list[str]
    candidate_sources: list[str]
    run_statuses: list[str]
    human_actions: list[str]
    conflict_resolutions: list[str]
    model_actions: list[str]


class GroupLearningSessionItemResponse(BaseModel):
    chat_stream_id: str
    session_id: str
    session_name: str
    runtime_session_id: str
    identity_status: str
    schedule_exists: bool
    schedule_enabled: bool
    selected_aspects: list[str]
    memory_count: int
    candidate_count: int
    conflict_count: int
    waiting_count: int
    last_run_status: str
    last_run_at: str
    group_profile_mode: str


class GroupLearningSessionListResponse(BaseModel):
    total: int
    items: list[GroupLearningSessionItemResponse]


class GroupLearningOverviewResponse(BaseModel):
    chat_stream_id: str
    feature_enabled: bool
    schedule: dict[str, Any] | None
    stream_state: dict[str, Any] | None
    counts: dict[str, int]
    selected_aspects: list[str]
    disabled_rule_ids: list[str]
    enabled_rule_ids: list[str]
    group_profile_mode: str
    recent_run: dict[str, Any] | None
    registry: dict[str, GroupLearningRegistryResponse]


class GroupLearningCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    candidate_id: str
    chat_stream_id: str
    candidate_type: str
    content: str
    meaning: str
    source: str
    status: str
    rule_id: str
    rule_version: int
    hit_count: int
    source_run_id: str
    model_decision: str
    model_contract_version: str
    model_review_run_id: str
    reviewed_content: str
    reviewed_meaning: str
    merge_target_memory_id: int | None
    alias_target_memory_id: int | None
    promoted_group_memory_id: int | None
    conflict_group_id: str
    approval_source: str
    human_reviewer_id: str
    human_reviewed_at: str
    human_action: str
    rejection_reason_code: str
    waiting_reason_code: str
    version: int
    first_seen_at: str
    last_seen_at: str
    updated_at: str


class GroupLearningCandidateListResponse(BaseModel):
    chat_stream_id: str
    items: list[GroupLearningCandidateResponse]
    next_after_id: int | None


class GroupLearningEvidenceResponse(BaseModel):
    id: int
    evidence_id: str
    candidate_id: str
    chat_log_id: int
    sender_ref: str
    source_run_id: str
    batch_id: str
    evidence_kind: str
    created_at: str
    content_preview: str
    preview_truncated: bool
    preview_redacted: bool
    available: bool


class GroupLearningCandidateDetailResponse(BaseModel):
    candidate: GroupLearningCandidateResponse
    evidence: list[GroupLearningEvidenceResponse]
    next_evidence_after_id: int | None


class GroupLearningRunResponse(BaseModel):
    run_id: str
    chat_stream_id: str
    trigger: str
    mode: str
    selected_aspects: list[str]
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
    started_at: str
    completed_at: str
    created_at: str
    updated_at: str


class GroupLearningRunListResponse(BaseModel):
    chat_stream_id: str
    items: list[GroupLearningRunResponse]


class GroupLearningSchedulePutRequest(GroupLearningMutationRequest):
    enabled: bool = True
    aspects: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=7,
    )
    interval_minutes: int | None = None
    window_hours: int | None = None


class GroupLearningSchedulePauseRequest(GroupLearningMutationRequest):
    pass


class GroupLearningScheduleMutationResponse(BaseModel):
    ok: bool
    replayed: bool = False
    schedule: dict[str, Any]


class GroupLearningReviewRequest(GroupLearningMutationRequest):
    action: Literal[
        "accept",
        "edit_accept",
        "reject",
        "merge",
        "resolve_conflict",
    ]
    reviewed_content: str = Field(min_length=1, max_length=4000)
    reviewed_meaning: str = Field(default="", max_length=4000)
    target_memory_id: int | None = Field(default=None, ge=1)
    conflict_resolution: Literal[
        "",
        "keep_target",
        "replace_target",
    ] = ""


class GroupLearningGovernanceResponse(BaseModel):
    ok: bool
    replayed: bool = False
    result: dict[str, Any]


class GroupLearningDryRunRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    chat_stream_id: str = ""
    rule_ids: list[str] | None = None


class GroupLearningRuleMatchResponse(BaseModel):
    rule_id: str
    rule_version: int
    candidate_type: str
    canonical_content: str
    meaning: str
    start: int
    end: int


class GroupLearningDryRunResponse(BaseModel):
    input_chars: int
    elapsed_ms: float
    registry_generation: int
    registry_sha256: str
    effective_rule_ids: list[str]
    matches: list[GroupLearningRuleMatchResponse]


class GroupLearningFeatureUpdateRequest(GroupLearningMutationRequest):
    enabled: bool


class GroupLearningFeatureResponse(BaseModel):
    ok: bool
    replayed: bool = False
    enabled: bool


class GroupLearningRuleActivationRequest(GroupLearningMutationRequest):
    enabled: bool
    chat_stream_id: str = ""


class GroupLearningRuleActivationResponse(BaseModel):
    ok: bool
    replayed: bool = False
    rule_id: str
    chat_stream_id: str
    enabled: bool
    global_disabled: list[str]
    session_disabled: dict[str, list[str]]


class GroupLearningExtractRequest(GroupLearningMutationRequest):
    window_hours: int = Field(default=24, ge=0, le=720)
    instructions: str = Field(default="", max_length=2000)
    aspects: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=7,
    )


__all__ = [
    "GroupLearningAspectResponse",
    "GroupLearningCandidateDetailResponse",
    "GroupLearningCandidateListResponse",
    "GroupLearningDescriptorsResponse",
    "GroupLearningDryRunRequest",
    "GroupLearningDryRunResponse",
    "GroupLearningExtractRequest",
    "GroupLearningFeatureResponse",
    "GroupLearningFeatureUpdateRequest",
    "GroupLearningGovernanceResponse",
    "GroupLearningOverviewResponse",
    "GroupLearningReviewRequest",
    "GroupLearningRuleActivationRequest",
    "GroupLearningRuleActivationResponse",
    "GroupLearningRunListResponse",
    "GroupLearningScheduleMutationResponse",
    "GroupLearningSchedulePauseRequest",
    "GroupLearningSchedulePutRequest",
    "GroupLearningSessionListResponse",
]

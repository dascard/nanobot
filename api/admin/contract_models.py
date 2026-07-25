"""首批 Admin Endpoint Contract 的 Pydantic Request/Response 模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class GroupMemoryExtractRequest(BaseModel):
    window_hours: int = Field(default=24, ge=0, le=720)
    instructions: str = ""
    aspects: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=7,
    )


class GroupMemoryInjectionConfigRequest(BaseModel):
    group_profile_mode: Literal["off", "preview", "on"] = "on"


class GroupMemoryInjectionPreviewRequest(BaseModel):
    user_input: str = ""
    max_items: int = Field(default=10, ge=1, le=30)
    max_chars: int = Field(default=1200, ge=200, le=4000)


class GroupMemoryUpdateRequest(BaseModel):
    content: str | None = None
    status: Literal[
        "review",
        "active",
        "disabled",
        "archived",
        "rejected",
    ] | None = None
    inject_policy: Literal[
        "auto",
        "manual_only",
        "never",
    ] | None = None
    disabled_reason: str | None = None
    rejected_reason: str | None = None


class GroupMemoryItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    chat_stream_id: str
    group_id: str
    memory_type: str
    content: str
    content_hash: str
    cluster_key: str
    confidence: float
    evidence_count: int
    decay_score: float
    first_seen: str
    last_seen: str
    updated_at: str
    status: str
    source: str
    inject_policy: str
    disabled_reason: str
    rejected_reason: str
    merged_into_id: int | None
    last_injected_at: str
    injected_count: int
    evidence_log_ids_json: str
    meta_json: str


class GroupMemoryListResponse(BaseModel):
    group_id: str
    chat_stream_id: str
    total: int
    memories: list[GroupMemoryItemResponse]


class GroupMemoryOverviewItemResponse(BaseModel):
    group_id: str
    raw_group_id: str
    stream_id: str
    session_name: str
    log_count: int
    latest_log_at: str
    memory_count: int
    active_count: int
    injectable_count: int
    latest_memory_at: str
    last_injected_at: str
    recent_injected_ids: list[int]
    group_profile_mode: str


class GroupMemoryOverviewResponse(BaseModel):
    total: int
    items: list[GroupMemoryOverviewItemResponse]


class GroupMemoryExtractResponse(GroupMemoryListResponse):
    ok: bool
    group_name: str
    window_hours: int | None
    raw_count: int
    eligible_count: int
    deduped_count: int
    message_count: int
    source_log_count: int
    stats: dict[str, int]
    memory_count: int
    active_count: int
    injectable_count: int


class GroupMemoryInjectionConfigResponse(BaseModel):
    ok: bool
    group_id: str
    chat_stream_id: str
    group_profile_mode: str


class GroupMemoryInjectionPreviewResponse(BaseModel):
    group_id: str
    group_profile_mode: str
    group_memory_context: str
    group_memory_ids: list[int]
    group_memory_skipped: list[dict[str, Any]]
    group_memory_context_chars: int
    score_components: dict[str, Any]
    debug: dict[str, Any]


class GroupMemoryUpdateResponse(BaseModel):
    ok: bool
    memory: GroupMemoryItemResponse


class ToolUpdateBody(BaseModel):
    private_default: bool | None = None
    private_superuser_default: bool | None = None
    group_default: bool | None = None
    lightweight_default: bool | None = None


class ToolOverrideBody(BaseModel):
    scope_type: Literal["group", "user", "chat_type", "platform"]
    scope_id: str
    enabled: bool
    reason: str = ""


class ToolListItemResponse(BaseModel):
    name: str
    label: str
    category: str
    risk_level: str
    private_default: bool
    private_superuser_default: bool
    group_default: bool
    lightweight_default: bool
    force_enabled: bool
    force_disabled: bool
    force_disabled_group: bool
    description: str
    configured_enabled: bool
    configured_disabled_reason: str
    runtime_effective: bool
    runtime_disabled_reason: str
    override_present: bool
    override_enabled: bool | None
    effective: bool
    disabled_reason: str
    registered: bool | None
    is_subagent: bool
    sandbox_managed: bool
    registration_lifecycle: str
    execution_port_id: str
    schema_provider_id: str


class ToolRegistrationSummaryResponse(BaseModel):
    generation: int
    sha256: str


class ToolListResponse(BaseModel):
    tools: list[ToolListItemResponse]
    registry_info: dict[str, Any]
    registry_available: bool
    registry_empty: bool
    bridge_count: int
    runtime_preset: str
    platform: str
    tool_registration: ToolRegistrationSummaryResponse


class ToolTargetItemResponse(BaseModel):
    id: str
    label: str
    name: str
    scope_type: str
    source: str
    recent_at: str


class ToolTargetsResponse(BaseModel):
    scope_type: str
    items: list[ToolTargetItemResponse]


class ToolMutationResponse(BaseModel):
    ok: bool
    tool: str | None = None


class AuditLogItemResponse(BaseModel):
    id: int
    admin_user: str
    action: str
    target_type: str
    target_id: str
    detail_json: Any
    ip_address: str
    created_at: str


class AuditLogListResponse(BaseModel):
    total: int
    items: list[AuditLogItemResponse]


__all__ = [
    "AuditLogListResponse",
    "GroupMemoryExtractRequest",
    "GroupMemoryExtractResponse",
    "GroupMemoryInjectionConfigRequest",
    "GroupMemoryInjectionConfigResponse",
    "GroupMemoryInjectionPreviewRequest",
    "GroupMemoryInjectionPreviewResponse",
    "GroupMemoryListResponse",
    "GroupMemoryOverviewResponse",
    "GroupMemoryUpdateRequest",
    "GroupMemoryUpdateResponse",
    "ToolListResponse",
    "ToolMutationResponse",
    "ToolOverrideBody",
    "ToolTargetsResponse",
    "ToolUpdateBody",
]

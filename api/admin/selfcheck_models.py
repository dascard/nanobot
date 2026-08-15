"""自检能力清单 Admin API 的类型化响应。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, StrictBool, field_validator


class SelfcheckRegistryResponse(BaseModel):
    namespace: str
    generation: int
    sha256: str


class SelfcheckCoverageKindResponse(BaseModel):
    total: int
    covered: int
    unverified: int
    exempted: int


class SelfcheckCoverageResponse(BaseModel):
    total: int
    covered: int
    unverified: int
    exempted: int
    required_unverified: int
    by_kind: dict[str, SelfcheckCoverageKindResponse]


class SelfcheckCapabilityResponse(BaseModel):
    capability_id: str
    kind: str
    source_id: str
    label: str
    owner: str
    criticality: str
    lifecycle: str
    coverage_policy: str
    coverage_status: str
    probe_ids: list[str]
    verification_suite_ids: list[str]
    related_operation_ids: list[str]
    exemption_reason: str
    attributes: dict[str, str]


class SelfcheckCapabilitiesResponse(BaseModel):
    registry: SelfcheckRegistryResponse
    coverage: SelfcheckCoverageResponse
    items: list[SelfcheckCapabilityResponse]


class SelfcheckProbeResponse(BaseModel):
    check_id: str
    category: str
    label: str
    level: str
    severity: str
    executor_key: str
    timeout_seconds: float
    environments: list[str]
    capability_kinds: list[str]
    capability_source_ids: list[str]
    destructive: bool
    requires_model: bool


class SelfcheckProbeCatalogResponse(BaseModel):
    registry: SelfcheckRegistryResponse
    items: list[SelfcheckProbeResponse]


class SelfcheckRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger: Literal["manual", "predeploy"] = "manual"
    check_ids: list[str] | None = None
    allow_model_checks: StrictBool = False

    @field_validator("check_ids")
    @classmethod
    def validate_check_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if not value or len(value) > 100:
            raise ValueError("check_ids 必须包含 1..100 项")
        if len(value) != len(set(value)):
            raise ValueError("check_ids 不能重复")
        return value


class SelfcheckResultResponse(BaseModel):
    check_id: str
    category: str
    status: str
    severity: str
    level: str
    duration_ms: int
    detail_code: str
    message: str
    capability_ids: list[str]
    metrics: dict[str, Any]
    evidence: dict[str, Any]
    started_at: datetime
    completed_at: datetime


class SelfcheckRunResponse(BaseModel):
    run_id: str
    trigger: str
    environment: str
    status: str
    capability_registry_sha256: str
    probe_registry_sha256: str
    summary: dict[str, int]
    results: list[SelfcheckResultResponse]
    started_at: datetime
    completed_at: datetime


class SelfcheckRunListItemResponse(BaseModel):
    run_id: str
    trigger: str
    environment: str
    status: str
    capability_registry_sha256: str
    probe_registry_sha256: str
    summary: dict[str, int]
    started_at: datetime
    completed_at: datetime | None


class SelfcheckRunListResponse(BaseModel):
    total: int
    items: list[SelfcheckRunListItemResponse]


__all__ = [
    "SelfcheckCapabilitiesResponse",
    "SelfcheckCapabilityResponse",
    "SelfcheckCoverageKindResponse",
    "SelfcheckCoverageResponse",
    "SelfcheckRegistryResponse",
    "SelfcheckProbeCatalogResponse",
    "SelfcheckProbeResponse",
    "SelfcheckResultResponse",
    "SelfcheckRunListItemResponse",
    "SelfcheckRunListResponse",
    "SelfcheckRunRequest",
    "SelfcheckRunResponse",
]

"""Runtime Module Diagnostics 的类型化响应合同。"""

from __future__ import annotations

from pydantic import BaseModel


class RuntimeRegistrySnapshotResponse(BaseModel):
    namespace: str
    generation: int
    sha256: str


class RuntimeModuleContributionResponse(BaseModel):
    kind: str
    contribution_id: str


class RuntimeModuleHealthCheckResponse(BaseModel):
    name: str
    healthy: bool
    detail_code: str


class RuntimeModuleHealthResponse(BaseModel):
    status: str
    ready: bool
    checks: list[RuntimeModuleHealthCheckResponse]


class RuntimeModuleManifestResponse(BaseModel):
    module_id: str
    version: str
    owner: str
    domain: str
    lifecycle: str
    required_modules: list[str]
    optional_modules: list[str]
    provided_capabilities: list[str]
    contributions: list[RuntimeModuleContributionResponse]
    startup_phase: int
    shutdown_phase: int
    health_checks: list[str]
    readiness_checks: list[str]
    feature_flag: str
    compatibility_aliases: list[str]
    release_impacts: list[str]
    health: RuntimeModuleHealthResponse | None


class RuntimeVerificationSuiteResponse(BaseModel):
    suite_id: str
    owner: str
    applicable_release_impacts: list[str]
    command: list[str]
    working_directory: str
    preconditions: list[str]
    timeout_seconds: int
    allow_skip: bool
    required_credentials: list[str]
    output_artifacts: list[str]
    success_criteria: list[str]
    cleanup: list[str]
    security_level: str
    feature_lifecycle_states: list[str]
    feature_enablement_gates: list[str]
    artifact_profiles: list[str]
    always_required: bool
    dependencies: list[str]


class RuntimeModuleDiagnosticsResponse(BaseModel):
    available: bool
    ready: bool
    composition_state: str
    composition_generation: int
    composition_sha256: str
    module_registry: RuntimeRegistrySnapshotResponse
    contribution_registry: RuntimeRegistrySnapshotResponse
    verification_registry: RuntimeRegistrySnapshotResponse
    verification_suites: list[RuntimeVerificationSuiteResponse]
    modules: list[RuntimeModuleManifestResponse]
    error_code: str


__all__ = [
    "RuntimeModuleContributionResponse",
    "RuntimeModuleDiagnosticsResponse",
    "RuntimeModuleHealthCheckResponse",
    "RuntimeModuleHealthResponse",
    "RuntimeModuleManifestResponse",
    "RuntimeRegistrySnapshotResponse",
    "RuntimeVerificationSuiteResponse",
]

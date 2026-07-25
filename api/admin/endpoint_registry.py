"""首批 Admin API 的代码所有 Endpoint Contract Registry。"""

from __future__ import annotations

from api.endpoint_contracts import EndpointContractDescriptor
from core.registry import RegistryBuilder, RegistrySnapshot


class EndpointContractRegistry:
    def __init__(
        self,
        descriptors: tuple[EndpointContractDescriptor, ...],
    ) -> None:
        builder = RegistryBuilder[EndpointContractDescriptor](
            "endpoint_contract"
        )
        operation_ids: set[str] = set()
        client_functions: set[str] = set()
        routes: set[tuple[str, str]] = set()
        for descriptor in descriptors:
            if descriptor.operation_id in operation_ids:
                raise ValueError(
                    f"重复 Endpoint operation_id："
                    f"{descriptor.operation_id}"
                )
            if descriptor.client_function in client_functions:
                raise ValueError(
                    f"重复 Endpoint client_function："
                    f"{descriptor.client_function}"
                )
            route_key = (descriptor.path, descriptor.method)
            if route_key in routes:
                raise ValueError(
                    f"重复 Endpoint 路由：{route_key}"
                )
            operation_ids.add(descriptor.operation_id)
            client_functions.add(descriptor.client_function)
            routes.add(route_key)
            builder.register(descriptor)
        self._snapshot = builder.freeze()

    @property
    def registry_snapshot(
        self,
    ) -> RegistrySnapshot[EndpointContractDescriptor]:
        return self._snapshot

    def descriptors(self) -> tuple[EndpointContractDescriptor, ...]:
        return tuple(self._snapshot)


def _endpoint(
    contract_id: str,
    operation_id: str,
    *,
    owner: str,
    method: str,
    path: str,
    client_function: str,
    response_schema: str,
    request_schema: str = "",
    errors: tuple[int, ...] = (401, 422),
    pagination: str = "none",
) -> EndpointContractDescriptor:
    return EndpointContractDescriptor(
        contract_id=contract_id,
        operation_id=operation_id,
        owner_module=owner,
        method=method,
        path=path,
        client_function=client_function,
        request_schema=request_schema,
        response_schema=response_schema,
        error_statuses=errors,
        pagination=pagination,
    )


ADMIN_ENDPOINT_CONTRACT_REGISTRY = EndpointContractRegistry((
    _endpoint(
        "admin.runtime.modules",
        "adminRuntimeModulesDiagnostics",
        owner="runtime.modules",
        method="GET",
        path="/api/v1/admin/runtime/modules",
        client_function="getRuntimeModuleDiagnostics",
        response_schema="RuntimeModuleDiagnosticsResponse",
        errors=(401, 503),
    ),
    _endpoint(
        "admin.group_learning.descriptors",
        "adminGroupLearningDescriptors",
        owner="group.learning",
        method="GET",
        path="/api/v1/admin/group-learning/descriptors",
        client_function="getGroupLearningDescriptors",
        response_schema="GroupLearningDescriptorsResponse",
        errors=(401, 503),
    ),
    _endpoint(
        "admin.group_learning.sessions",
        "adminGroupLearningSessions",
        owner="group.learning",
        method="GET",
        path="/api/v1/admin/group-learning/sessions",
        client_function="listGroupLearningSessions",
        response_schema="GroupLearningSessionListResponse",
        pagination="limit",
    ),
    _endpoint(
        "admin.group_learning.overview",
        "adminGroupLearningOverview",
        owner="group.learning",
        method="GET",
        path=(
            "/api/v1/admin/group-learning/sessions/"
            "{chat_stream_id}/overview"
        ),
        client_function="getGroupLearningOverview",
        response_schema="GroupLearningOverviewResponse",
        errors=(400, 401, 503),
    ),
    _endpoint(
        "admin.group_learning.candidates",
        "adminGroupLearningCandidates",
        owner="group.learning",
        method="GET",
        path=(
            "/api/v1/admin/group-learning/sessions/"
            "{chat_stream_id}/candidates"
        ),
        client_function="listGroupLearningCandidates",
        response_schema="GroupLearningCandidateListResponse",
        errors=(400, 401, 422),
        pagination="cursor_limit",
    ),
    _endpoint(
        "admin.group_learning.candidate_detail",
        "adminGroupLearningCandidateDetail",
        owner="group.learning",
        method="GET",
        path=(
            "/api/v1/admin/group-learning/candidates/"
            "{candidate_id}"
        ),
        client_function="getGroupLearningCandidate",
        response_schema="GroupLearningCandidateDetailResponse",
        errors=(401, 404, 422),
        pagination="cursor_limit",
    ),
    _endpoint(
        "admin.group_learning.runs",
        "adminGroupLearningRuns",
        owner="group.learning",
        method="GET",
        path=(
            "/api/v1/admin/group-learning/sessions/"
            "{chat_stream_id}/runs"
        ),
        client_function="listGroupLearningRuns",
        response_schema="GroupLearningRunListResponse",
        errors=(400, 401, 422),
        pagination="limit",
    ),
    _endpoint(
        "admin.group_learning.schedule_put",
        "adminGroupLearningSchedulePut",
        owner="group.learning",
        method="PUT",
        path=(
            "/api/v1/admin/group-learning/sessions/"
            "{chat_stream_id}/schedule"
        ),
        client_function="putGroupLearningSchedule",
        request_schema="GroupLearningSchedulePutRequest",
        response_schema="GroupLearningScheduleMutationResponse",
        errors=(400, 401, 409, 422, 503),
    ),
    _endpoint(
        "admin.group_learning.schedule_pause",
        "adminGroupLearningSchedulePause",
        owner="group.learning",
        method="POST",
        path=(
            "/api/v1/admin/group-learning/sessions/"
            "{chat_stream_id}/schedule/pause"
        ),
        client_function="pauseGroupLearningSchedule",
        request_schema="GroupLearningSchedulePauseRequest",
        response_schema="GroupLearningScheduleMutationResponse",
        errors=(400, 401, 404, 409, 422, 503),
    ),
    _endpoint(
        "admin.group_learning.candidate_review",
        "adminGroupLearningCandidateReview",
        owner="group.learning",
        method="POST",
        path=(
            "/api/v1/admin/group-learning/candidates/"
            "{candidate_id}/review"
        ),
        client_function="reviewGroupLearningCandidate",
        request_schema="GroupLearningReviewRequest",
        response_schema="GroupLearningGovernanceResponse",
        errors=(400, 401, 404, 409, 422, 503),
    ),
    _endpoint(
        "admin.group_learning.rules_dry_run",
        "adminGroupLearningRulesDryRun",
        owner="group.learning",
        method="POST",
        path="/api/v1/admin/group-learning/rules/dry-run",
        client_function="dryRunGroupLearningRules",
        request_schema="GroupLearningDryRunRequest",
        response_schema="GroupLearningDryRunResponse",
        errors=(400, 401, 422, 503),
    ),
    _endpoint(
        "admin.group_learning.feature_update",
        "adminGroupLearningFeatureUpdate",
        owner="group.learning",
        method="PUT",
        path="/api/v1/admin/group-learning/features",
        client_function="updateGroupLearningFeature",
        request_schema="GroupLearningFeatureUpdateRequest",
        response_schema="GroupLearningFeatureResponse",
        errors=(401, 409, 422, 503),
    ),
    _endpoint(
        "admin.group_learning.rule_activation",
        "adminGroupLearningRuleActivation",
        owner="group.learning",
        method="PUT",
        path=(
            "/api/v1/admin/group-learning/rules/"
            "{rule_id}/activation"
        ),
        client_function="setGroupLearningRuleActivation",
        request_schema="GroupLearningRuleActivationRequest",
        response_schema="GroupLearningRuleActivationResponse",
        errors=(400, 401, 409, 422, 503),
    ),
    _endpoint(
        "admin.group_learning.extract",
        "adminGroupLearningExtract",
        owner="group.learning",
        method="POST",
        path=(
            "/api/v1/admin/group-learning/sessions/"
            "{chat_stream_id}/extract"
        ),
        client_function="extractGroupLearningSession",
        request_schema="GroupLearningExtractRequest",
        response_schema="GroupMemoryExtractResponse",
        errors=(400, 401, 404, 409, 422, 502, 503),
    ),
    _endpoint(
        "admin.group_memory.overview",
        "adminGroupMemoryOverview",
        owner="group.memory",
        method="GET",
        path="/api/v1/admin/group-memories/overview",
        client_function="listGroupMemoryOverview",
        response_schema="GroupMemoryOverviewResponse",
        pagination="limit",
    ),
    _endpoint(
        "admin.group_memory.items",
        "adminGroupMemoryItems",
        owner="group.memory",
        method="GET",
        path="/api/v1/admin/group-memories/{group_id}/items",
        client_function="listGroupMemoryItems",
        response_schema="GroupMemoryListResponse",
    ),
    _endpoint(
        "admin.group_memory.extract",
        "adminGroupMemoryExtract",
        owner="group.memory",
        method="POST",
        path="/api/v1/admin/group-memories/{group_id}/extract",
        client_function="extractGroupMemories",
        request_schema="GroupMemoryExtractRequest",
        response_schema="GroupMemoryExtractResponse",
        errors=(400, 401, 404, 422, 500),
    ),
    _endpoint(
        "admin.group_memory.injection_config",
        "adminGroupMemoryInjectionConfig",
        owner="group.memory",
        method="PUT",
        path=(
            "/api/v1/admin/group-memories/{group_id}/"
            "injection-config"
        ),
        client_function="setGroupMemoryInjectionConfig",
        request_schema="GroupMemoryInjectionConfigRequest",
        response_schema="GroupMemoryInjectionConfigResponse",
    ),
    _endpoint(
        "admin.group_memory.injection_preview",
        "adminGroupMemoryInjectionPreview",
        owner="group.memory",
        method="POST",
        path=(
            "/api/v1/admin/group-memories/{group_id}/"
            "injection-preview"
        ),
        client_function="previewGroupMemoryInjection",
        request_schema="GroupMemoryInjectionPreviewRequest",
        response_schema="GroupMemoryInjectionPreviewResponse",
    ),
    _endpoint(
        "admin.group_memory.update_item",
        "adminGroupMemoryUpdateItem",
        owner="group.memory",
        method="PATCH",
        path="/api/v1/admin/group-memories/items/{memory_id}",
        client_function="updateGroupMemoryItem",
        request_schema="GroupMemoryUpdateRequest",
        response_schema="GroupMemoryUpdateResponse",
        errors=(400, 401, 404, 409, 422),
    ),
    _endpoint(
        "admin.tools.list",
        "adminToolsList",
        owner="tool.runtime",
        method="GET",
        path="/api/v1/admin/tools",
        client_function="listTools",
        response_schema="ToolListResponse",
    ),
    _endpoint(
        "admin.tools.targets",
        "adminToolTargetsList",
        owner="tool.runtime",
        method="GET",
        path="/api/v1/admin/tools/targets",
        client_function="listToolTargets",
        response_schema="ToolTargetsResponse",
        pagination="limit",
    ),
    _endpoint(
        "admin.tools.defaults_update",
        "adminToolDefaultsUpdate",
        owner="tool.runtime",
        method="PUT",
        path="/api/v1/admin/tools/{tool_name}",
        client_function="updateToolDefaults",
        request_schema="ToolUpdateBody",
        response_schema="ToolMutationResponse",
        errors=(400, 401, 404, 409, 422),
    ),
    _endpoint(
        "admin.tools.override_set",
        "adminToolOverrideSet",
        owner="tool.runtime",
        method="PUT",
        path="/api/v1/admin/tools/{tool_name}/override",
        client_function="setToolOverride",
        request_schema="ToolOverrideBody",
        response_schema="ToolMutationResponse",
        errors=(400, 401, 404, 409, 422),
    ),
    _endpoint(
        "admin.tools.override_delete",
        "adminToolOverrideDelete",
        owner="tool.runtime",
        method="DELETE",
        path="/api/v1/admin/tools/{tool_name}/override",
        client_function="deleteToolOverride",
        response_schema="ToolMutationResponse",
        errors=(401, 404, 409, 422),
    ),
    _endpoint(
        "admin.audit_logs.list",
        "adminAuditLogsList",
        owner="admin.api",
        method="GET",
        path="/api/v1/admin/audit-logs",
        client_function="listAuditLogs",
        response_schema="AuditLogListResponse",
        pagination="page_limit",
    ),
))


__all__ = [
    "ADMIN_ENDPOINT_CONTRACT_REGISTRY",
    "EndpointContractRegistry",
]

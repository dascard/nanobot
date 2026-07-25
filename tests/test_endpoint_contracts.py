"""Endpoint Contract、OpenAPI 与生成客户端的首批垂直切片。"""

from __future__ import annotations

from pathlib import Path
import warnings


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_CONTRACT_IDS = {
    "admin.audit_logs.list",
    "admin.runtime.modules",
    "admin.group_learning.candidate_detail",
    "admin.group_learning.candidate_review",
    "admin.group_learning.candidates",
    "admin.group_learning.descriptors",
    "admin.group_learning.extract",
    "admin.group_learning.feature_update",
    "admin.group_learning.overview",
    "admin.group_learning.rule_activation",
    "admin.group_learning.rules_dry_run",
    "admin.group_learning.runs",
    "admin.group_learning.schedule_pause",
    "admin.group_learning.schedule_put",
    "admin.group_learning.sessions",
    "admin.group_memory.extract",
    "admin.group_memory.injection_config",
    "admin.group_memory.injection_preview",
    "admin.group_memory.items",
    "admin.group_memory.overview",
    "admin.group_memory.update_item",
    "admin.tools.defaults_update",
    "admin.tools.list",
    "admin.tools.override_delete",
    "admin.tools.override_set",
    "admin.tools.targets",
}


def _operation(schema: dict, path: str, method: str) -> dict:
    return schema["paths"][path][method.lower()]


def _success_schema(operation: dict) -> dict:
    for status, response in operation["responses"].items():
        if not str(status).startswith("2"):
            continue
        content = response.get("content", {})
        if not content:
            continue
        for media in content.values():
            schema = media.get("schema")
            if schema:
                return schema
    raise AssertionError(
        f"{operation.get('operationId')} 缺少成功响应 Schema"
    )


def test_endpoint_contract_registry_is_frozen_and_covers_first_slice():
    from api.admin.endpoint_registry import ADMIN_ENDPOINT_CONTRACT_REGISTRY

    snapshot = ADMIN_ENDPOINT_CONTRACT_REGISTRY.registry_snapshot

    assert snapshot.namespace == "endpoint_contract"
    assert snapshot.generation == 1
    assert EXPECTED_CONTRACT_IDS <= {
        descriptor.contract_id
        for descriptor in snapshot
    }
    assert len({
        descriptor.operation_id
        for descriptor in snapshot
    }) == len(tuple(snapshot))
    assert len({
        descriptor.client_function
        for descriptor in snapshot
    }) == len(tuple(snapshot))
    assert {
        descriptor.owner_module
        for descriptor in snapshot
    } <= {
        "admin.api",
        "group.learning",
        "group.memory",
        "runtime.modules",
        "tool.runtime",
    }


def test_openapi_has_stable_global_contracts_and_typed_first_slice():
    from api.admin.endpoint_registry import ADMIN_ENDPOINT_CONTRACT_REGISTRY
    from server import app

    app.openapi_schema = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        schema = app.openapi()
    assert not [
        warning
        for warning in caught
        if "Duplicate Operation ID" in str(warning.message)
    ]
    registry_meta = schema["x-nanobot-endpoint-registry"]
    snapshot = ADMIN_ENDPOINT_CONTRACT_REGISTRY.registry_snapshot
    assert registry_meta == {
        "generation": snapshot.generation,
        "sha256": snapshot.sha256,
    }
    operation_ids: list[str] = []

    for path_item in schema["paths"].values():
        for method in (
            "get",
            "put",
            "post",
            "delete",
            "patch",
            "options",
            "head",
        ):
            operation = path_item.get(method)
            if not operation:
                continue
            operation_id = operation.get("operationId")
            assert operation_id
            operation_ids.append(operation_id)
            assert operation["x-nanobot-contract-lifecycle"] in {
                "typed",
                "compatibility",
            }
            assert "default" in operation["responses"]
            error_schema = (
                operation["responses"]["default"]["content"]
                ["application/json"]["schema"]
            )
            assert error_schema["$ref"].endswith("/ApiErrorResponse")
            _success_schema(operation)

    assert len(operation_ids) == len(set(operation_ids))

    for descriptor in ADMIN_ENDPOINT_CONTRACT_REGISTRY.registry_snapshot:
        operation = _operation(
            schema,
            descriptor.path,
            descriptor.method,
        )
        assert operation["operationId"] == descriptor.operation_id
        assert (
            operation["x-nanobot-endpoint-contract-id"]
            == descriptor.contract_id
        )
        assert operation["x-nanobot-contract-lifecycle"] == "typed"
        assert (
            _success_schema(operation)["$ref"].rsplit("/", 1)[-1]
            == descriptor.response_schema
        )
        for status in descriptor.error_statuses:
            assert str(status) in operation["responses"]


def test_generated_openapi_and_typescript_client_are_current():
    from scripts.generate_openapi_client import (
        OPENAPI_OUTPUT,
        TYPESCRIPT_OUTPUT,
        render_artifacts,
    )

    rendered = render_artifacts()

    assert OPENAPI_OUTPUT.read_text(encoding="utf-8") == rendered.openapi
    assert (
        TYPESCRIPT_OUTPUT.read_text(encoding="utf-8")
        == rendered.typescript
    )
    assert "此文件由 scripts/generate_openapi_client.py 生成" in (
        rendered.typescript
    )
    for function_name in (
        "listGroupMemoryOverview",
        "listGroupMemoryItems",
        "extractGroupMemories",
        "setGroupMemoryInjectionConfig",
        "previewGroupMemoryInjection",
        "updateGroupMemoryItem",
        "getGroupLearningDescriptors",
        "listGroupLearningSessions",
        "getGroupLearningOverview",
        "listGroupLearningCandidates",
        "getGroupLearningCandidate",
        "listGroupLearningRuns",
        "putGroupLearningSchedule",
        "pauseGroupLearningSchedule",
        "reviewGroupLearningCandidate",
        "dryRunGroupLearningRules",
        "updateGroupLearningFeature",
        "setGroupLearningRuleActivation",
        "extractGroupLearningSession",
        "listTools",
        "listToolTargets",
        "updateToolDefaults",
        "setToolOverride",
        "deleteToolOverride",
        "listAuditLogs",
        "getRuntimeModuleDiagnostics",
    ):
        assert f"function {function_name}" in rendered.typescript


def test_first_web_features_only_use_generated_endpoint_client():
    app_source = (ROOT / "webui" / "src" / "App.jsx").read_text(
        encoding="utf-8"
    )
    tools_source = (
        ROOT / "webui" / "src" / "features" / "tools" / "ToolsPage.jsx"
    ).read_text(encoding="utf-8")
    group_learning_source = (
        ROOT
        / "webui"
        / "src"
        / "features"
        / "group-learning"
        / "GroupLearningPage.jsx"
    ).read_text(encoding="utf-8")
    workflow = (
        ROOT / ".github" / "workflows" / "quality-gate.yml"
    ).read_text(encoding="utf-8")

    assert "/group-memories" not in app_source
    assert "api/generated/adminClient" in group_learning_source
    assert "api." not in tools_source
    assert "api/generated/adminClient" in tools_source
    assert "generate_openapi_client.py --check" in workflow

"""Feature 与 Compatibility 生命周期 Registry 测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _settings_service_with_rows(rows):
    from core.settings_service import SettingsService

    stored_rows = [
        SimpleNamespace(key=key, value=value)
        for key, value in rows
    ]

    class FakeQuery:
        def all(self):
            return stored_rows

    class FakeSession:
        def query(self, _model):
            return FakeQuery()

        def close(self):
            return None

    return SettingsService(session_factory=FakeSession)


def test_feature_lifecycle_registry_has_fixed_states_and_sandbox_gates():
    from core.lifecycle import (
        FEATURE_LIFECYCLE_REGISTRY,
        FeatureLifecycleState,
        FeatureScope,
    )

    assert {state.value for state in FeatureLifecycleState} == {
        "experimental",
        "hidden",
        "preview",
        "stable",
        "deprecated",
        "retired",
    }
    sandbox = FEATURE_LIFECYCLE_REGISTRY.require("sandbox")
    assert sandbox.state is FeatureLifecycleState.EXPERIMENTAL
    assert sandbox.default_enabled is False
    assert set(sandbox.supported_scopes) == {
        FeatureScope.GLOBAL,
        FeatureScope.PRIVATE_SESSION,
        FeatureScope.ADMIN,
    }
    assert {
        "infrastructure_allowed",
        "sandboxd_ready",
        "apparmor_loaded",
        "fixed_image_digest",
        "workspace_quota_ready",
        "explicit_session_grant",
    }.issubset(sandbox.enablement_gates)
    assert sandbox.rollback_behavior.value == "disable_preserve_data"
    assert sandbox.removal_conditions


def test_private_timing_v2_is_preview_default_off_and_requires_release_gates():
    from core.lifecycle import (
        FEATURE_LIFECYCLE_REGISTRY,
        FeatureLifecycleState,
        FeatureScope,
    )

    descriptor = FEATURE_LIFECYCLE_REGISTRY.require(
        "private_timing_v2"
    )

    assert descriptor.state is FeatureLifecycleState.PREVIEW
    assert descriptor.default_enabled is False
    assert descriptor.supported_scopes == (
        FeatureScope.PRIVATE_SESSION,
        FeatureScope.ADMIN,
    )
    assert set(descriptor.enablement_gates) == {
        "offline_eval_passed",
        "task_slo_activation_ready",
        "token_observability_ready",
        "explicit_session_allowlist",
        "operator_approval",
    }


def test_feature_descriptor_rejects_missing_owner_and_removal_conditions():
    from core.lifecycle import (
        FeatureLifecycleDescriptor,
        FeatureLifecycleState,
        FeatureRollbackBehavior,
        FeatureScope,
    )

    common = {
        "feature_id": "feature.invalid",
        "state": FeatureLifecycleState.PREVIEW,
        "default_enabled": False,
        "supported_scopes": (FeatureScope.GLOBAL,),
        "data_migrations": (),
        "rollback_behavior": (
            FeatureRollbackBehavior.DISABLE_PRESERVE_DATA
        ),
        "enablement_gates": ("operator_approval",),
    }
    with pytest.raises(ValueError, match="owner"):
        FeatureLifecycleDescriptor(
            owner_module="",
            removal_conditions=("review",),
            **common,
        )
    with pytest.raises(ValueError, match="removal"):
        FeatureLifecycleDescriptor(
            owner_module="tests.lifecycle",
            removal_conditions=(),
            **common,
        )


def test_retired_tool_feature_cannot_be_enabled_by_any_scope_or_gate():
    from core.lifecycle import (
        FEATURE_LIFECYCLE_REGISTRY,
        FeatureDecisionCode,
        FeatureLifecycleState,
        FeatureScope,
        evaluate_feature_enablement,
    )
    from core.tool_registration import get_tool_registration

    registration = get_tool_registration("bash")
    assert registration is not None
    descriptor = FEATURE_LIFECYCLE_REGISTRY.require(
        registration.feature_lifecycle_id
    )

    assert registration.lifecycle == "retired"
    assert registration.execution_binding is None
    assert descriptor.state is FeatureLifecycleState.RETIRED
    decision = evaluate_feature_enablement(
        descriptor.feature_id,
        requested=True,
        scope=FeatureScope.PRIVATE_SESSION,
        satisfied_gates=frozenset(descriptor.enablement_gates),
    )
    assert decision.enabled is False
    assert decision.code is FeatureDecisionCode.RETIRED


def test_sandbox_feature_requires_supported_scope_and_every_gate():
    from core.lifecycle import (
        FEATURE_LIFECYCLE_REGISTRY,
        FeatureDecisionCode,
        FeatureScope,
        evaluate_feature_enablement,
    )

    descriptor = FEATURE_LIFECYCLE_REGISTRY.require("sandbox")
    not_requested = evaluate_feature_enablement(
        "sandbox",
        requested=False,
        scope=FeatureScope.PRIVATE_SESSION,
    )
    unsupported = evaluate_feature_enablement(
        "sandbox",
        requested=True,
        scope=FeatureScope.GROUP_SESSION,
    )
    missing = evaluate_feature_enablement(
        "sandbox",
        requested=True,
        scope=FeatureScope.PRIVATE_SESSION,
        satisfied_gates=frozenset({"sandboxd_ready"}),
    )
    enabled = evaluate_feature_enablement(
        "sandbox",
        requested=True,
        scope=FeatureScope.PRIVATE_SESSION,
        satisfied_gates=frozenset(descriptor.enablement_gates),
    )

    assert not_requested.code is FeatureDecisionCode.NOT_REQUESTED
    assert unsupported.code is FeatureDecisionCode.UNSUPPORTED_SCOPE
    assert missing.code is FeatureDecisionCode.MISSING_GATES
    assert "sandboxd_ready" not in missing.missing_gates
    assert enabled.enabled is True
    assert enabled.code is FeatureDecisionCode.ENABLED


def test_all_tool_registrations_reference_one_feature_lifecycle_snapshot():
    from core.lifecycle import (
        FEATURE_LIFECYCLE_REGISTRY,
        FeatureLifecycleState,
    )
    from core.tool_registration import list_tool_registrations

    for registration in list_tool_registrations():
        feature = FEATURE_LIFECYCLE_REGISTRY.require(
            registration.feature_lifecycle_id
        )
        if registration.lifecycle == "retired":
            assert feature.state is FeatureLifecycleState.RETIRED
        else:
            assert feature.state is not FeatureLifecycleState.RETIRED


def test_compatibility_registry_declares_fixed_removal_gate_and_owner():
    from core.lifecycle import (
        COMPATIBILITY_REGISTRY,
        CompatibilityKind,
        CompatibilityTombstoneBehavior,
    )

    descriptor = COMPATIBILITY_REGISTRY.require(
        "setting.daily_digest_hour"
    )

    assert descriptor.kind is CompatibilityKind.SETTING
    assert descriptor.alias_value == "daily_digest.hour"
    assert descriptor.canonical_replacement == (
        "memory_digest.schedule_hour"
    )
    assert descriptor.owner_module == "memory.runtime"
    assert descriptor.test_ids
    assert descriptor.removal_gate.consecutive_zero_usage_days == 30
    assert descriptor.removal_gate.minimum_full_releases == 1
    assert descriptor.removal_gate.require_migration_reconciliation is True
    assert descriptor.removal_gate.require_rollback_drill is True
    assert descriptor.removal_gate.require_backup_restore_drill is True
    assert set(descriptor.removal_gate.required_approvers) == {
        "release_owner",
        "group_memory_data_owner",
        "production_operator",
    }
    assert (
        descriptor.tombstone_behavior
        is CompatibilityTombstoneBehavior.FORWARD
    )


def test_group_analysis_omitted_aspects_compatibility_is_registered():
    from core.lifecycle import (
        COMPATIBILITY_REGISTRY,
        CompatibilityKind,
        CompatibilityTombstoneBehavior,
    )

    descriptor = COMPATIBILITY_REGISTRY.require(
        "schema.group_analysis_omitted_aspects"
    )

    assert descriptor.kind is CompatibilityKind.SCHEMA
    assert descriptor.alias_value == "group_analysis.aspects_omitted"
    assert descriptor.canonical_replacement == (
        "group_analysis.aspects_explicit"
    )
    assert descriptor.owner_module == "group.learning"
    assert (
        descriptor.tombstone_behavior
        is CompatibilityTombstoneBehavior.PRESERVE
    )


def test_compatibility_descriptor_without_tests_or_removal_gate_fails_closed():
    from core.lifecycle import (
        CompatibilityDescriptor,
        CompatibilityKind,
        CompatibilityRemovalGate,
        CompatibilityTombstoneBehavior,
        CompatibilityWarningPolicy,
    )

    gate = CompatibilityRemovalGate.production_default()
    common = {
        "compatibility_id": "compat.invalid",
        "kind": CompatibilityKind.ROUTE,
        "alias_value": "legacy",
        "canonical_replacement": "canonical",
        "introduced_version": "1.0.0",
        "warning_policy": CompatibilityWarningPolicy.LOG_ONCE,
        "tombstone_behavior": CompatibilityTombstoneBehavior.FORWARD,
        "owner_module": "tests.lifecycle",
        "removal_conditions": ("usage_zero",),
    }
    with pytest.raises(ValueError, match="测试"):
        CompatibilityDescriptor(
            test_ids=(),
            removal_gate=gate,
            **common,
        )
    with pytest.raises(ValueError, match="removal"):
        CompatibilityDescriptor(
            test_ids=("tests.test_lifecycle",),
            removal_gate=gate,
            removal_conditions=(),
            **{key: value for key, value in common.items() if key != "removal_conditions"},
        )


def test_alias_resolution_is_typed_and_usage_is_observable_without_raw_payload():
    from core.lifecycle import (
        COMPATIBILITY_REGISTRY,
        CompatibilityKind,
        InMemoryCompatibilityUsageRecorder,
    )

    recorder = InMemoryCompatibilityUsageRecorder()
    resolution = COMPATIBILITY_REGISTRY.resolve_alias(
        CompatibilityKind.ROUTE,
        "vision",
        recorder=recorder,
    )

    assert resolution is not None
    assert resolution.descriptor.compatibility_id == "route.vision"
    assert resolution.canonical_replacement == "sticker_describe"
    usage = recorder.snapshot()["route.vision"]
    assert usage.count == 1
    assert usage.kind is CompatibilityKind.ROUTE
    assert not hasattr(usage, "alias_value")
    assert (
        COMPATIBILITY_REGISTRY.resolve_alias(
            CompatibilityKind.ROUTE,
            "unknown",
            recorder=recorder,
        )
        is None
    )


def test_settings_alias_projection_and_provenance_come_from_compatibility_registry(
    monkeypatch,
):
    from core.config_registry import (
        LEGACY_SETTING_ALIASES,
        canonical_setting_key,
    )

    alias = LEGACY_SETTING_ALIASES[
        "memory_digest.schedule_hour"
    ]
    assert alias.key == "daily_digest.hour"
    assert alias.env_name == "DAILY_DIGEST_HOUR"
    assert alias.compatibility_id == "setting.daily_digest_hour"
    assert canonical_setting_key(alias.key) == (
        "memory_digest.schedule_hour"
    )

    monkeypatch.delenv(
        "MEMORY_DIGEST_SCHEDULE_HOUR",
        raising=False,
    )
    monkeypatch.delenv("DAILY_DIGEST_HOUR", raising=False)
    service = _settings_service_with_rows([
        ("daily_digest.hour", "6"),
    ])
    resolved = service.get_resolved("memory_digest.schedule_hour")

    assert resolved.value == 6
    assert resolved.source == "legacy_database"
    assert resolved.compatibility_id == "setting.daily_digest_hour"
    assert resolved.provenance()["compatibility_id"] == (
        "setting.daily_digest_hour"
    )


def test_model_route_alias_and_deprecated_route_record_compatibility_usage():
    from core.lifecycle import get_compatibility_usage_snapshot
    from core.model_provider.route_registry import (
        require_model_route_descriptor,
    )

    before = get_compatibility_usage_snapshot()
    alias_before = before.get("route.vision")
    legacy_before = before.get("route.classifier_legacy")

    assert require_model_route_descriptor("vision").route_key == (
        "sticker_describe"
    )
    assert require_model_route_descriptor(
        "classifier_legacy"
    ).route_key == "classifier_legacy"

    after = get_compatibility_usage_snapshot()
    assert after["route.vision"].count == (
        (alias_before.count if alias_before else 0) + 1
    )
    assert after["route.classifier_legacy"].count == (
        (legacy_before.count if legacy_before else 0) + 1
    )


def test_identity_compatibility_adapter_records_explicit_legacy_prefix():
    from core.chat_stream_identity import (
        parse_compatibility_chat_stream_identity,
    )
    from core.lifecycle import get_compatibility_usage_snapshot

    before = get_compatibility_usage_snapshot().get(
        "identity.group_prefix"
    )
    identity = parse_compatibility_chat_stream_identity("group_42")

    assert identity is not None
    assert identity.chat_stream_id == "qq:42:group"
    after = get_compatibility_usage_snapshot()[
        "identity.group_prefix"
    ]
    assert after.count == (before.count if before else 0) + 1


def test_deprecated_render_endpoint_exposes_warning_and_usage(client):
    from core.lifecycle import get_compatibility_usage_snapshot

    before = get_compatibility_usage_snapshot().get(
        "endpoint.render"
    )
    response = client.get("/api/v1/render?text=hello")

    assert response.status_code == 200
    assert response.json()["status"] == "deprecated"
    assert response.headers["Deprecation"] == "true"
    assert (
        response.headers["Link"]
        == '<retired.without_replacement>; rel="successor-version"'
    )
    after = get_compatibility_usage_snapshot()["endpoint.render"]
    assert after.count == (before.count if before else 0) + 1


@pytest.mark.asyncio
async def test_deprecated_group_timing_endpoint_keeps_behavior_and_warns(
    monkeypatch,
):
    from fastapi import Response

    from api.group_utility_routes import (
        GroupTimingRequest,
        group_timing_deprecated,
    )
    from core.lifecycle import get_compatibility_usage_snapshot

    class FakeRuntime:
        async def process_message(self, *args, **kwargs):
            return {"action": "no_reply", "reason": "test"}

    monkeypatch.setattr(
        "core.timing_runtime.get_group_runtime",
        lambda: FakeRuntime(),
    )
    before = get_compatibility_usage_snapshot().get(
        "endpoint.group_timing"
    )
    response = Response()

    result = await group_timing_deprecated(
        GroupTimingRequest(group_id="42", message="你好"),
        response=response,
        _auth=None,
    )

    assert result == {"action": "no_reply", "reason": "test"}
    assert response.headers["Deprecation"] == "true"
    assert response.headers["Link"] == (
        '</api/v1/group/message>; rel="successor-version"'
    )
    after = get_compatibility_usage_snapshot()[
        "endpoint.group_timing"
    ]
    assert after.count == (before.count if before else 0) + 1

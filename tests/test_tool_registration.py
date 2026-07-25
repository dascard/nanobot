from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_tool_registration_snapshot_binds_all_capabilities_once():
    from core.tool_registration import TOOL_REGISTRATION_REGISTRY

    snapshot = TOOL_REGISTRATION_REGISTRY.registry_snapshot

    assert snapshot.namespace == "tool_registration"
    assert snapshot.generation == 1
    assert len(snapshot.sha256) == 64
    registration = snapshot.require("reply")
    assert registration.descriptor.name == "reply"
    assert registration.schema_provider_id == "canonical"
    assert registration.execution_binding is not None
    assert registration.execution_binding.port_id == "tool.reply.execute"
    assert registration.prompt_template_keys == ("tools/reply/usage",)
    assert registration.trace_policy == "standard"
    assert registration.lifecycle == "active"


def test_active_registration_requires_schema_provider_and_execution_binding():
    from core.tool_registration import (
        ToolRegistrationError,
        ToolRegistrationRegistry,
    )
    from core.tool_registry import get_tool_descriptor

    descriptor = get_tool_descriptor("reply")
    assert descriptor is not None

    with pytest.raises(ToolRegistrationError, match="schema provider"):
        ToolRegistrationRegistry.from_descriptors(
            (descriptor,),
            schema_provider_ids={},
            execution_port_ids={"reply": "tool.reply.execute"},
        )

    with pytest.raises(ToolRegistrationError, match="execution binding"):
        ToolRegistrationRegistry.from_descriptors(
            (descriptor,),
            schema_provider_ids={"reply": "canonical"},
            execution_port_ids={},
        )


def test_retired_tools_are_tombstones_without_execution_binding():
    from core.tool_registration import (
        get_tool_registration,
        list_active_tool_registrations,
    )

    active_names = {
        registration.name
        for registration in list_active_tool_registrations()
    }
    for name in {
        "python_sandbox",
        "bash",
        "read",
        "write",
        "edit",
        "grep",
        "glob",
        "memory_read",
        "memory_write",
    }:
        registration = get_tool_registration(name)
        assert registration is not None
        assert registration.lifecycle == "retired"
        assert registration.execution_binding is None
        assert name not in active_names


def test_kt_projection_is_registry_owned_and_ignores_yaml_contents():
    from nanobot_kt.tool_registration_adapter import (
        apply_tool_registration_projection,
        build_kt_tool_configs,
    )

    expected = build_kt_tool_configs()
    config = SimpleNamespace(
        tools=[
            SimpleNamespace(
                name="yaml_only",
                type="package",
                module="example.yaml",
                class_name="YamlTool",
            )
        ]
    )

    projection = apply_tool_registration_projection(config)

    assert config.tools == list(expected)
    assert projection.declared_names == ("yaml_only",)
    assert projection.projected_names == tuple(item.name for item in expected)
    assert "yaml_only" in projection.removed_declared_names
    assert "python_sandbox" not in projection.projected_names


def test_creature_yaml_is_only_a_compatible_registry_projection():
    from kohakuterrarium.core.config import load_agent_config

    from nanobot_kt.tool_registration_adapter import build_kt_tool_configs

    config = load_agent_config("creatures/nanobot")

    assert tuple(sorted(item.name for item in config.tools)) == tuple(
        sorted(item.name for item in build_kt_tool_configs())
    )


def test_kt_adapter_covers_every_active_execution_port():
    from nanobot_kt.tool_registration_adapter import (
        validate_kt_execution_bindings,
    )

    result = validate_kt_execution_bindings()

    assert result.active_tool_count > 0
    assert result.missing_port_ids == ()
    assert result.orphan_port_ids == ()


def test_loaded_tool_drift_fails_closed_but_allows_framework_tools():
    from nanobot_kt.tool_registration_adapter import (
        ToolRegistrationDriftError,
        validate_loaded_tool_names,
    )

    expected = set(validate_loaded_tool_names.expected_names())
    validate_loaded_tool_names(expected | {"skill"})

    with pytest.raises(ToolRegistrationDriftError, match="缺失"):
        validate_loaded_tool_names(expected - {"reply"})

    with pytest.raises(ToolRegistrationDriftError, match="未登记"):
        validate_loaded_tool_names(expected | {"unknown_runtime_tool"})


def test_tool_schema_is_resolved_through_registration_snapshot():
    from core.tool_registration import get_tool_registration
    from core.tool_schema_preview import build_tool_schema

    registration = get_tool_registration("reply")
    assert registration is not None
    assert registration.schema_provider_id == "canonical"

    schema = build_tool_schema("reply", include_template_overlay=False)

    assert schema["function"]["name"] == registration.name
    assert schema["registration"]["generation"] == 1
    assert schema["registration"]["sha256"]
    assert schema["registration"]["schema_provider_id"] == "canonical"


def test_group_analysis_aspects_schema_is_derived_from_aspect_registry():
    from core.group_learning import GROUP_ANALYSIS_ASPECT_REGISTRY
    from core.tool_schema_preview import build_tool_schema

    schema = build_tool_schema(
        "group_analysis",
        include_template_overlay=False,
    )
    aspects = schema["function"]["parameters"]["properties"]["aspects"]

    assert aspects["type"] == "array"
    assert aspects["items"]["enum"] == list(
        GROUP_ANALYSIS_ASPECT_REGISTRY.ordered_ids
    )
    assert aspects["uniqueItems"] is True
    assert aspects["minItems"] == 1
    assert aspects["maxItems"] == len(
        GROUP_ANALYSIS_ASPECT_REGISTRY.ordered_ids
    )


def test_every_registered_tool_schema_is_validated_fail_closed():
    from core.tool_schema_preview import validate_registered_tool_schemas

    validate_registered_tool_schemas()


def test_tool_profiles_are_frozen_descriptors_with_compatible_aliases():
    from core.tool_registration import (
        RESEARCH_TOOL_NAMES,
        TOOL_PROFILE_REGISTRY,
        get_default_lightweight_tool_names,
        normalize_tool_profile_id,
    )

    snapshot = TOOL_PROFILE_REGISTRY.registry_snapshot

    assert snapshot.namespace == "tool_profile"
    assert snapshot.generation == 1
    assert set(snapshot.ordered_ids) == {
        "full",
        "lightweight",
        "none",
        "research",
    }
    assert normalize_tool_profile_id("lite") == "lightweight"
    assert normalize_tool_profile_id("research_companion") == "research"
    assert normalize_tool_profile_id("unexpected") == "full"
    assert get_default_lightweight_tool_names() == frozenset({
        "reply",
        "no_reply",
        "image_summary",
        "image_generation",
        "sticker_search",
    })
    assert RESEARCH_TOOL_NAMES == frozenset({
        "web_search",
        "reply",
        "no_reply",
    })


def test_profiles_preserve_existing_effective_tool_behavior(db_session):
    from core.runtime_tool_service import resolve_effective_tools
    from core.tool_registration import (
        RESEARCH_TOOL_NAMES,
        get_default_lightweight_tool_names,
    )
    from core.tool_registry import get_tool_def

    none_enabled, _ = resolve_effective_tools(
        chat_type="private",
        runtime_preset="none",
        db=db_session,
    )
    assert {
        name for name, enabled in none_enabled.items() if enabled
    } == {
        name
        for name in none_enabled
        if (get_tool_def(name) and get_tool_def(name).force_enabled)
    }

    lightweight_enabled, _ = resolve_effective_tools(
        chat_type="private",
        runtime_preset="lightweight",
        db=db_session,
    )
    assert {
        name for name, enabled in lightweight_enabled.items() if enabled
    } <= get_default_lightweight_tool_names()

    research_enabled, _ = resolve_effective_tools(
        chat_type="private",
        runtime_preset="research",
        db=db_session,
    )
    assert {
        name for name, enabled in research_enabled.items() if enabled
    } == RESEARCH_TOOL_NAMES


def test_profile_never_reopens_a_tool_disabled_by_tool_plan(db_session):
    from core.database import ToolOverride
    from core.runtime_tool_service import resolve_effective_tools

    db_session.add(
        ToolOverride(
            tool_name="web_search",
            scope_type="user",
            scope_id="u-profile",
            enabled=False,
            reason="测试禁用",
        )
    )
    db_session.commit()

    enabled, disabled = resolve_effective_tools(
        chat_type="private",
        user_id="u-profile",
        runtime_preset="research",
        db=db_session,
    )

    assert enabled["web_search"] is False
    assert disabled["web_search"] == "测试禁用"


def test_registration_projection_is_exposed_to_admin_and_tool_plan(db_session):
    from core.tool_plan import build_tool_plan
    from core.tool_registration import TOOL_REGISTRATION_REGISTRY
    from core.tool_schema_preview import build_tool_schema_config

    snapshot = TOOL_REGISTRATION_REGISTRY.registry_snapshot
    config = build_tool_schema_config(db_session, "reply")
    plan = build_tool_plan(
        chat_type="private",
        runtime_preset="full",
        db=db_session,
    )

    assert config["registration"]["generation"] == snapshot.generation
    assert config["registration"]["sha256"] == snapshot.sha256
    reply_schema = next(
        schema
        for schema in plan.sent_tool_schemas
        if schema["function"]["name"] == "reply"
    )
    assert "registration" not in reply_schema

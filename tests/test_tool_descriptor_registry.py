from dataclasses import replace

import pytest


def test_tool_descriptor_registry_derives_runtime_and_prompt_contracts():
    from core.tool_registry import (
        TOOL_METADATA,
        TOOL_DESCRIPTOR_REGISTRY,
        get_tool_descriptor,
        list_tool_descriptors,
    )

    descriptors = {item.name: item for item in list_tool_descriptors()}
    ordered_names = [item.name for item in list_tool_descriptors()]

    assert set(TOOL_METADATA) <= set(descriptors)
    assert TOOL_DESCRIPTOR_REGISTRY.frozen is True
    assert ordered_names == sorted(ordered_names)
    assert descriptors["reply"].availability_policy == "force_enabled"
    assert descriptors["python_sandbox"].availability_policy == "force_disabled"
    assert descriptors["sandbox_exec"].execution_policy == "foreground_only"
    assert descriptors["sandbox_exec"].trace_policy == "metadata_only"
    assert descriptors["sandbox_exec"].prompt_source_precedence == (
        "runtime",
        "default",
    )
    assert descriptors["sandbox_exec"].prompt_editable is True
    assert descriptors["python_sandbox"].prompt_editable is False
    assert descriptors["sandbox_exec"].owner_module == "core.sandbox"
    for name in {
        "sandbox_poll",
        "sandbox_write_stdin",
        "sandbox_terminate",
        "workspace_apply_patch",
    }:
        assert descriptors[name].execution_policy == "foreground_only"
        assert descriptors[name].trace_policy == "metadata_only"
        assert descriptors[name].owner_module == "core.sandbox"
    assert descriptors["group_analysis"].prompt_template_keys == (
        "tools/group_analysis/usage",
        "tools/group_analysis/system",
        "tools/group_analysis/topics",
        "tools/group_analysis/titles",
        "tools/group_analysis/quotes",
        "tools/group_analysis/quality",
    )
    assert get_tool_descriptor("missing") is None

    with pytest.raises(TypeError):
        TOOL_METADATA["injected"] = TOOL_METADATA["reply"]


def test_tool_descriptor_registry_fails_closed_on_key_identity_mismatch():
    from core.tool_registry import (
        TOOL_METADATA,
        ToolDescriptorRegistry,
        ToolDescriptorRegistryError,
    )

    invalid = dict(TOOL_METADATA)
    invalid["reply_alias"] = invalid.pop("reply")

    with pytest.raises(ToolDescriptorRegistryError, match="key.*name"):
        ToolDescriptorRegistry.from_metadata(invalid)


def test_tool_descriptor_registry_fails_closed_on_conflicting_availability():
    from core.tool_registry import (
        TOOL_METADATA,
        ToolDescriptorRegistry,
        ToolDescriptorRegistryError,
    )

    invalid = dict(TOOL_METADATA)
    invalid["reply"] = replace(
        invalid["reply"],
        force_disabled=True,
    )

    with pytest.raises(ToolDescriptorRegistryError, match="force_enabled.*force_disabled"):
        ToolDescriptorRegistry.from_metadata(invalid)


def test_tool_schema_preview_uses_descriptor_execution_policy():
    from core.tool_schema_preview import build_tool_schema

    sandbox = build_tool_schema("sandbox_exec")
    reply = build_tool_schema("reply")

    sandbox_properties = sandbox["function"]["parameters"]["properties"]
    reply_properties = reply["function"]["parameters"]["properties"]
    assert "run_in_background" not in sandbox_properties
    assert "run_in_background" in reply_properties


def test_prompt_registry_uses_tool_descriptor_template_bindings():
    from core.prompt_v2.template_registry import tool_template_keys
    from core.tool_registry import get_tool_descriptor

    descriptor = get_tool_descriptor("image_summary")
    assert descriptor is not None
    assert tool_template_keys("image_summary") == descriptor.prompt_template_keys

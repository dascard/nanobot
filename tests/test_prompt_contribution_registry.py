from __future__ import annotations

import copy

import pytest


def _section(
    contribution_id: str,
    *,
    phase: str = "context",
    dependencies: tuple[str, ...] = (),
):
    from core.prompt_v2.section_descriptors import PromptSectionDescriptor

    return PromptSectionDescriptor(
        section_id=contribution_id,
        owner_module="tests.prompt",
        domain="test",
        phase=phase,
        authority="data",
        trust="untrusted_data",
        dependencies=dependencies,
        source_precedence=("request",),
        editable=False,
        failure_policy="fail_closed",
    )


def _contribution(
    contribution_id: str,
    *,
    phase: str = "context",
    priority: int = 100,
    before: tuple[str, ...] = (),
    after: tuple[str, ...] = (),
    multiplicity: str = "singleton",
    chat_types: frozenset[str] = frozenset(),
):
    from core.prompt_v2.contribution_registry import PromptContributionDescriptor

    return PromptContributionDescriptor(
        contribution_id=contribution_id,
        section_descriptor=_section(contribution_id, phase=phase),
        priority=priority,
        before=before,
        after=after,
        chat_types=chat_types,
        required_variables=frozenset(),
        multiplicity=multiplicity,
        renderer_id="runtime",
        sensitive_trace_policy="hash_and_size",
    )


def test_prompt_contribution_registry_freezes_stable_snapshot():
    from core.prompt_v2.contribution_registry import PromptContributionRegistry

    first = PromptContributionRegistry([
        _contribution("second", after=("first",)),
        _contribution("first", priority=10),
    ]).freeze()
    second = PromptContributionRegistry([
        _contribution("first", priority=10),
        _contribution("second", after=("first",)),
    ]).freeze()

    assert first.registry_snapshot.generation == 1
    assert first.registry_snapshot.sha256 == second.registry_snapshot.sha256
    assert first.registry_snapshot.canonical_json == second.registry_snapshot.canonical_json
    assert first.registry_snapshot.ordered_ids == ("first", "second")


def test_prompt_contribution_order_uses_phase_dependencies_then_priority():
    from core.prompt_v2.contribution_registry import PromptContributionRegistry

    registry = PromptContributionRegistry([
        _contribution("request", phase="request", priority=1),
        _contribution("context_late", priority=900, after=("context_early",)),
        _contribution("policy", phase="policy", priority=500),
        _contribution("context_early", priority=100),
        _contribution("identity", phase="identity", priority=999),
    ]).freeze()

    resolved = registry.resolve(
        {
            "request",
            "context_late",
            "policy",
            "context_early",
            "identity",
        },
        platform="qq",
        chat_type="private",
    )

    assert resolved.ordered_ids == (
        "policy",
        "identity",
        "context_early",
        "context_late",
        "request",
    )


def test_prompt_contribution_before_is_normalized_to_dependency():
    from core.prompt_v2.contribution_registry import PromptContributionRegistry

    registry = PromptContributionRegistry([
        _contribution("late", priority=1),
        _contribution("early", priority=999, before=("late",)),
    ]).freeze()

    resolved = registry.resolve(
        {"late", "early"},
        platform="qq",
        chat_type="private",
    )

    assert resolved.ordered_ids == ("early", "late")
    assert resolved.descriptors["late"].after == ("early",)


def test_prompt_contribution_singleton_priority_conflict_fails_closed():
    from core.prompt_v2.contribution_registry import (
        PromptContributionConflictError,
        PromptContributionRegistry,
    )

    registry = PromptContributionRegistry([
        _contribution("alpha"),
        _contribution("beta"),
    ]).freeze()

    with pytest.raises(PromptContributionConflictError, match="singleton"):
        registry.resolve(
            {"alpha", "beta"},
            platform="qq",
            chat_type="private",
        )


def test_many_contributions_can_share_phase_and_priority():
    from core.prompt_v2.contribution_registry import PromptContributionRegistry

    registry = PromptContributionRegistry([
        _contribution("alpha", multiplicity="many"),
        _contribution("beta", multiplicity="many"),
    ]).freeze()

    resolved = registry.resolve(
        {"beta", "alpha"},
        platform="qq",
        chat_type="private",
    )

    assert resolved.ordered_ids == ("alpha", "beta")


def test_prompt_contribution_applicability_filters_chat_type():
    from core.prompt_v2.contribution_registry import PromptContributionRegistry

    registry = PromptContributionRegistry([
        _contribution(
            "private_only",
            chat_types=frozenset({"private"}),
            multiplicity="many",
        ),
        _contribution(
            "group_only",
            chat_types=frozenset({"group"}),
            multiplicity="many",
        ),
    ]).freeze()

    assert registry.resolve(
        {"private_only", "group_only"},
        platform="qq",
        chat_type="private",
    ).ordered_ids == ("private_only",)


def test_prompt_renderer_rejects_missing_declared_input_variable():
    from core.prompt_v2.contribution_registry import (
        PromptContributionRenderContext,
        PromptContributionRendererError,
        validate_prompt_contribution_inputs,
    )

    descriptor = _contribution("requires_input", multiplicity="many")
    descriptor = descriptor.__class__(
        contribution_id=descriptor.contribution_id,
        section_descriptor=descriptor.section_descriptor,
        priority=descriptor.priority,
        required_variables=frozenset({"required_value"}),
        multiplicity=descriptor.multiplicity,
        renderer_id=descriptor.renderer_id,
        sensitive_trace_policy=descriptor.sensitive_trace_policy,
    )
    context = PromptContributionRenderContext(
        descriptor=descriptor,
        node={},
        template_values={},
        runtime_sections={},
        input_variables={},
    )

    with pytest.raises(PromptContributionRendererError, match="required_value"):
        validate_prompt_contribution_inputs(context)


def test_canonical_contribution_order_matches_current_flow_for_every_live_branch():
    from core.prompt_v2.contribution_registry import (
        resolve_prompt_contributions,
    )
    from core.prompt_v2.flow import (
        DEFAULT_FLOW,
        ordered_nodes_for_chat,
    )
    from core.prompt_v2.flow_contract import LIVE_PROMPT_BRANCHES

    for platform, chat_type in LIVE_PROMPT_BRANCHES:
        ordered_nodes = ordered_nodes_for_chat(
            DEFAULT_FLOW,
            chat_type,
            platform=platform,
        )
        before = tuple(str(node["id"]) for node in ordered_nodes)
        resolution = resolve_prompt_contributions(
            DEFAULT_FLOW,
            ordered_nodes,
            platform=platform,
            chat_type=chat_type,
        )

        assert resolution.ordered_ids == before
        assert resolution.generation == 1
        assert len(resolution.sha256) == 64


def test_group_memory_uses_untrusted_data_contribution_contract():
    from core.prompt_v2.contribution_registry import (
        canonical_prompt_contributions,
    )

    descriptor = next(
        item
        for item in canonical_prompt_contributions()
        if item.contribution_id == "group_context"
    )

    assert descriptor.owner_module == "app.group_memory"
    assert descriptor.domain == "group_memory"
    assert descriptor.phase == "context"
    assert descriptor.authority == "data"
    assert descriptor.trust == "untrusted_data"
    assert descriptor.after == ("session_guidance",)
    assert descriptor.chat_types == frozenset({"group"})
    assert descriptor.required_variables == frozenset({
        "group_profile_context",
    })
    assert descriptor.sensitive_trace_policy == "hash_and_size"


def test_registration_order_does_not_change_canonical_resolution_or_hash():
    from core.prompt_v2.contribution_registry import (
        PromptContributionRegistry,
        canonical_prompt_contributions,
    )

    descriptors = list(canonical_prompt_contributions())
    forward = PromptContributionRegistry(descriptors).freeze()
    reverse = PromptContributionRegistry(reversed(descriptors)).freeze()
    active_ids = {item.contribution_id for item in descriptors}

    forward_resolution = forward.resolve(
        active_ids,
        platform="qq",
        chat_type="group",
    )
    reverse_resolution = reverse.resolve(
        active_ids,
        platform="qq",
        chat_type="group",
    )

    assert forward.registry_snapshot.sha256 == reverse.registry_snapshot.sha256
    assert forward_resolution.ordered_ids == reverse_resolution.ordered_ids


def test_unknown_flow_extension_is_low_authority_many_untrusted_data():
    from core.prompt_v2.contribution_registry import (
        resolve_prompt_contributions,
    )
    from core.prompt_v2.flow import DEFAULT_FLOW, ordered_nodes_for_chat

    flow = copy.deepcopy(DEFAULT_FLOW)
    extension = {
        "id": "external_context",
        "type": "template",
        "template_key": "chat/external_context",
        "chat_types": ["private"],
    }
    current_user_index = next(
        index
        for index, node in enumerate(flow["nodes"])
        if node["id"] == "current_user_event"
    )
    flow["nodes"].insert(current_user_index, extension)
    edge = next(
        item
        for item in flow["edges"]
        if item["from"] == "runtime_context"
        and item["to"] == "current_user_event"
    )
    flow["edges"].remove(edge)
    flow["edges"].extend([
        {
            "from": "runtime_context",
            "to": "external_context",
            "chat_types": ["private"],
        },
        {
            "from": "external_context",
            "to": "current_user_event",
        },
    ])
    ordered_nodes = ordered_nodes_for_chat(flow, "private", platform="qq")

    resolution = resolve_prompt_contributions(
        flow,
        ordered_nodes,
        platform="qq",
        chat_type="private",
    )
    descriptor = resolution.descriptors["external_context"]

    assert descriptor.authority == "data"
    assert descriptor.trust == "untrusted_data"
    assert descriptor.multiplicity == "many"
    assert descriptor.priority > 0


@pytest.mark.asyncio
async def test_compiler_exposes_contribution_metadata_without_changing_messages():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            chat_type="private",
            platform="qq",
            session_id="qq:u1:private",
            user_id="u1",
            user_input="当前问题",
            persona_text="示例画像",
            history_messages=[{"role": "user", "content": "历史问题"}],
            runtime_tool_prompt="[RuntimeTool]\n只允许 reply/no_reply",
        )
    )

    contribution = plan.debug["prompt_contribution_registry"]
    assert contribution["namespace"] == "prompt_contribution"
    assert contribution["generation"] == 1
    assert len(contribution["sha256"]) == 64
    assert contribution["ordered_ids"] == [
        item["contribution_id"] for item in plan.flow_sections
    ]
    persona_sections = [
        item
        for item in plan.flow_sections
        if item["contribution_id"] == "persona_reference"
    ]
    assert len(persona_sections) == 1
    assert persona_sections[0]["active_source"] == "request"
    assert persona_sections[0]["sensitive_trace_policy"] == "hash_and_size"
    assert sum(
        '"section":"persona_reference"' in str(message.get("content") or "")
        for message in plan.messages
    ) == 1


def test_flow_cannot_override_contribution_priority_or_multiplicity():
    from core.prompt_v2.flow import DEFAULT_FLOW, PromptFlowError, validate_flow

    for field, value in (
        ("priority", -1000),
        ("multiplicity", "many"),
        ("renderer_id", "unsafe"),
    ):
        flow = copy.deepcopy(DEFAULT_FLOW)
        persona = next(
            node for node in flow["nodes"] if node["id"] == "persona_reference"
        )
        persona[field] = value
        with pytest.raises(PromptFlowError, match="Prompt contribution"):
            validate_flow(flow)

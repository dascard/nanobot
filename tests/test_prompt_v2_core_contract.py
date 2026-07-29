from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path

import pytest


_ALWAYS_REQUIRED = {
    "base_contract",
    "runtime_context",
    "identity_context",
    "session_guidance",
    "persona_reference",
    "runtime_tool_prompt",
    "current_user_event",
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "platform,chat_type,conditional_required,forbidden",
    [
        (
            "qq",
            "group",
            {"qq_common_policy", "group_policy", "qq_group_policy"},
            {"private_policy"},
        ),
        (
            "qq",
            "private",
            {"qq_common_policy", "private_policy"},
            {"group_policy", "qq_group_policy"},
        ),
        (
            "web",
            "group",
            {"group_policy"},
            {"qq_common_policy", "qq_group_policy", "private_policy"},
        ),
        (
            "web",
            "private",
            {"private_policy"},
            {"qq_common_policy", "group_policy", "qq_group_policy"},
        ),
        (
            "internal",
            "private",
            {"private_policy"},
            {"qq_common_policy", "group_policy", "qq_group_policy"},
        ),
        (
            "external_private",
            "private",
            {"private_policy"},
            {"qq_common_policy", "group_policy", "qq_group_policy"},
        ),
    ],
)
async def test_core_flow_contract_matches_all_live_branches(
    platform,
    chat_type,
    conditional_required,
    forbidden,
):
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.flow_contract import required_contracts
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            platform=platform,
            chat_type=chat_type,
            session_id="group_1001" if chat_type == "group" else "private_u1",
            group_id="1001" if chat_type == "group" else "",
            user_id="u1",
            user_input="你好",
            runtime_tool_prompt="[RuntimeTool]\n只允许 reply/no_reply",
        ),
        strict_audit=True,
    )

    expected = _ALWAYS_REQUIRED | conditional_required
    contract_ids = {
        contract.node_id for contract in required_contracts(platform, chat_type)
    }
    assert contract_ids == expected

    sections = list(plan.flow_sections)
    for node_id in expected:
        matching = [section for section in sections if section["node_id"] == node_id]
        assert len(matching) == 1
        assert matching[0]["origin"] == "flow"
        expected_status = "empty" if node_id == "session_guidance" else "emitted"
        assert matching[0]["status"] == expected_status
    assert not ({section["node_id"] for section in sections} & forbidden)


@pytest.mark.asyncio
async def test_internal_group_fails_closed_in_strict_compile():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.flow import PromptFlowError
    from core.prompt_v2.schema import PromptCompileRequest

    with pytest.raises(PromptFlowError, match="internal/group"):
        await compile_prompt_plan(
            PromptCompileRequest(
                platform="internal",
                chat_type="group",
                session_id="group_internal",
                group_id="internal",
                user_id="research-user",
                user_input="内部研究请求",
                runtime_tool_prompt="[RuntimeTool]\n只允许 web_search/reply/no_reply",
            ),
            strict_audit=True,
        )


@pytest.mark.asyncio
async def test_internal_private_has_private_policy_without_qq_policy():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            platform="internal",
            chat_type="private",
            session_id="research_internal-private",
            user_id="research-user",
            user_input="内部研究请求",
            runtime_tool_prompt="[RuntimeTool]\n只允许 web_search/reply/no_reply",
        ),
        strict_audit=True,
    )

    node_ids = [section["node_id"] for section in plan.flow_sections]
    assert "base_contract" in node_ids
    assert "private_policy" in node_ids
    assert "qq_common_policy" not in node_ids
    assert "qq_group_policy" not in node_ids
    assert plan.platform == "internal"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "platform,chat_type",
    [
        ("qq", "group"),
        ("qq", "private"),
        ("web", "group"),
        ("web", "private"),
    ],
)
async def test_flow_v2_keeps_existing_branch_messages_and_section_hashes(
    tmp_path,
    monkeypatch,
    platform,
    chat_type,
):
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    current_flow = json.loads(
        Path("prompts.v2.default/chat/flow.json").read_text(encoding="utf-8")
    )
    assert current_flow["version"] == 2
    legacy_flow = copy.deepcopy(current_flow)
    legacy_flow["version"] = 1
    private_edge = next(
        edge
        for edge in legacy_flow["edges"]
        if (edge.get("from"), edge.get("to"))
        == ("base_contract", "private_policy")
    )
    private_edge["platforms"] = ["web"]

    legacy_path = tmp_path / "legacy" / "flow.json"
    current_path = tmp_path / "current" / "flow.json"
    legacy_path.parent.mkdir(parents=True)
    current_path.parent.mkdir(parents=True)
    legacy_path.write_text(
        json.dumps(legacy_flow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    current_path.write_text(
        json.dumps(current_flow, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    request = PromptCompileRequest(
        platform=platform,
        chat_type=chat_type,
        session_id="group_1001" if chat_type == "group" else "private_u1",
        group_id="1001" if chat_type == "group" else "",
        user_id="u1",
        user_input="验证既有分支不变",
        runtime_tool_prompt="[RuntimeTool]\n只允许 reply/no_reply",
    )
    monkeypatch.setattr(
        "core.prompt_v2.context_adapters._current_time_text",
        lambda _current_time=None: "2026-07-14 12:00:00 CST",
    )

    monkeypatch.setattr(
        "core.prompt_v2.flow.runtime_flow_path",
        lambda: legacy_path,
    )
    legacy_plan = await compile_prompt_plan(request, strict_audit=True)
    monkeypatch.setattr(
        "core.prompt_v2.flow.runtime_flow_path",
        lambda: current_path,
    )
    current_plan = await compile_prompt_plan(request, strict_audit=True)

    assert current_plan.messages == legacy_plan.messages
    assert current_plan.section_hashes == legacy_plan.section_hashes
    assert current_plan.prompt_sha256 == legacy_plan.prompt_sha256


@pytest.mark.parametrize(
    "case,expected_issue",
    [
        ("missing_base", "required flow section base_contract must appear once"),
        ("missing_runtime", "required flow section runtime_context must appear once"),
        ("duplicate_runtime", "required flow section runtime_context must appear once"),
        ("runtime_renamed", "runtime_context node_id must be runtime_context"),
        ("identity_wrong_type", "identity_context node_type must be template"),
        ("identity_wrong_template", "identity_context template_key must be chat/identity_context"),
        ("web_contains_qq", "forbidden flow section qq_common_policy"),
        ("qq_group_missing_policy", "required flow section qq_group_policy must appear once"),
        ("message_out_of_bounds", "runtime_context message index is out of bounds"),
        ("message_index_reused", "message index is owned by multiple flow sections"),
        ("identity_wrong_role", "identity_context message role must be system"),
        ("current_user_not_final", "current_user_event must reference the final message"),
        ("runtime_before_branch", "core flow section order is invalid"),
        ("identity_before_runtime", "core flow section order is invalid"),
        ("fallback_base", "required flow section base_contract must originate from flow"),
        ("empty_base", "required flow section base_contract status must be emitted"),
        ("missing_origin", "base_contract origin is required"),
        ("missing_status", "base_contract status is required"),
        ("invalid_origin", "base_contract origin is invalid"),
        ("invalid_status", "base_contract status is invalid"),
        ("bool_index", "base_contract message index must be an integer"),
        ("message_not_object", "base_contract message must be an object"),
        ("flow_section_not_object", "flow section at index"),
        (
            "auxiliary_template_wrong_role",
            "conversation_context_header message role must be system",
        ),
        ("auxiliary_role_bypass", "conversation_context_header node_type is invalid"),
        ("history_wrong_role", "history_messages message role must be user or assistant"),
        ("unowned_message", "message index is not owned by a flow section"),
        ("auxiliary_section_order", "flow section message order is invalid"),
        ("message_order", "core flow message order is invalid"),
        ("missing_super_user_fact", "runtime_context missing required field is_super_user"),
        ("runtime_platform_mismatch", "runtime_context field platform does not match plan"),
    ],
)
@pytest.mark.asyncio
async def test_core_flow_audit_rejects_structural_mutations(case, expected_issue):
    from core.prompt_v2.audit import audit_prompt_plan
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    platform = "web" if case == "web_contains_qq" else "qq"
    plan = await compile_prompt_plan(
        PromptCompileRequest(
            platform=platform,
            chat_type="group",
            session_id="group_1001",
            group_id="1001",
            user_id="u1",
            user_input="你好",
            history_messages=[
                {"role": "user", "content": "历史问题"},
                {"role": "assistant", "content": "历史回答"},
            ],
            runtime_tool_prompt="[RuntimeTool]\n只允许 reply/no_reply",
        ),
        strict_audit=True,
    )
    sections = copy.deepcopy(plan.flow_sections)
    messages = copy.deepcopy(plan.messages)

    def section(node_id):
        return next(item for item in sections if item["node_id"] == node_id)

    if case == "missing_base":
        sections = [item for item in sections if item["node_id"] != "base_contract"]
    elif case == "missing_runtime":
        sections = [item for item in sections if item["node_id"] != "runtime_context"]
    elif case == "duplicate_runtime":
        sections.append(copy.deepcopy(section("runtime_context")))
    elif case == "runtime_renamed":
        section("runtime_context")["node_id"] = "runtime_alias"
    elif case == "identity_wrong_type":
        section("identity_context")["node_type"] = "runtime"
    elif case == "identity_wrong_template":
        section("identity_context")["template_key"] = "chat/main"
    elif case == "web_contains_qq":
        sections.append(
            {
                "node_id": "qq_common_policy",
                "node_type": "template",
                "template_key": "chat/platform/qq/common",
                "runtime_key": "",
                "origin": "flow",
                "status": "emitted",
                "message_indexes": [0],
            }
        )
    elif case == "qq_group_missing_policy":
        sections = [item for item in sections if item["node_id"] != "qq_group_policy"]
    elif case == "message_out_of_bounds":
        section("runtime_context")["message_indexes"] = [len(messages)]
    elif case == "message_index_reused":
        section("identity_context")["message_indexes"] = list(
            section("runtime_context")["message_indexes"]
        )
    elif case == "identity_wrong_role":
        index = section("identity_context")["message_indexes"][0]
        messages[index]["role"] = "assistant"
    elif case == "current_user_not_final":
        messages.append({"role": "system", "content": "tail"})
    elif case == "runtime_before_branch":
        runtime = section("runtime_context")
        sections.remove(runtime)
        sections.insert(sections.index(section("group_policy")), runtime)
    elif case == "identity_before_runtime":
        identity = section("identity_context")
        sections.remove(identity)
        sections.insert(sections.index(section("runtime_context")), identity)
    elif case == "fallback_base":
        section("base_contract")["origin"] = "fallback"
    elif case == "empty_base":
        section("base_contract")["status"] = "empty"
    elif case == "missing_origin":
        section("base_contract").pop("origin")
    elif case == "missing_status":
        section("base_contract").pop("status")
    elif case == "invalid_origin":
        section("base_contract")["origin"] = "untrusted"
    elif case == "invalid_status":
        section("base_contract")["status"] = "untrusted"
    elif case == "bool_index":
        section("base_contract")["message_indexes"] = [True]
    elif case == "message_not_object":
        index = section("base_contract")["message_indexes"][0]
        messages[index] = "not-a-message"
    elif case == "flow_section_not_object":
        sections.append("not-a-section")
    elif case == "auxiliary_template_wrong_role":
        index = section("conversation_context_header")["message_indexes"][0]
        messages[index]["role"] = "assistant"
    elif case == "auxiliary_role_bypass":
        header = section("conversation_context_header")
        header["node_type"] = "opaque"
        messages[header["message_indexes"][0]]["role"] = "assistant"
    elif case == "history_wrong_role":
        index = section("history_messages")["message_indexes"][0]
        messages[index]["role"] = "system"
    elif case == "unowned_message":
        messages.insert(-1, {"role": "assistant", "content": "未归属内容"})
        section("current_user_event")["message_indexes"] = [len(messages) - 1]
    elif case == "auxiliary_section_order":
        history = section("history_messages")
        sections.remove(history)
        sections.insert(sections.index(section("conversation_context_header")), history)
    elif case == "message_order":
        runtime_indexes = list(section("runtime_context")["message_indexes"])
        identity_indexes = list(section("identity_context")["message_indexes"])
        section("runtime_context")["message_indexes"] = identity_indexes
        section("identity_context")["message_indexes"] = runtime_indexes
    elif case in {"missing_super_user_fact", "runtime_platform_mismatch"}:
        index = section("runtime_context")["message_indexes"][0]
        content = str(messages[index]["content"])
        facts = json.loads(
            content.split("<runtime_context>", 1)[1]
            .split("</runtime_context>", 1)[0]
            .strip()
        )
        if case == "missing_super_user_fact":
            facts.pop("is_super_user")
        else:
            facts["platform"] = "web"
        messages[index]["content"] = (
            "<runtime_context>\n"
            + json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n</runtime_context>"
        )
    else:  # pragma: no cover
        raise AssertionError(case)

    mutated = dataclasses.replace(plan, flow_sections=sections, messages=messages)
    audit = audit_prompt_plan(mutated)

    assert audit.ok is False
    assert any(expected_issue in issue for issue in audit.issues), audit.issues


@pytest.mark.parametrize("case", ["base", "identity", "qq_common"])
def test_validate_flow_rejects_reserved_identity_drift(case):
    from core.prompt_v2.flow import PromptFlowError, validate_flow

    flow = json.loads(
        Path("prompts.v2.default/chat/flow.json").read_text(encoding="utf-8")
    )
    nodes = {node["id"]: node for node in flow["nodes"]}
    if case == "base":
        nodes["base_contract"]["template_key"] = "chat/untrusted"
    elif case == "identity":
        identity = nodes["identity_context"]
        identity["type"] = "runtime"
        identity.pop("template_key")
        identity["runtime_key"] = "group_context"
    else:
        nodes["qq_common_policy"]["template_key"] = "chat/untrusted"

    with pytest.raises(PromptFlowError):
        validate_flow(flow)


def test_default_and_runtime_flow_files_are_identical():
    from core.prompt_v2.flow import DEFAULT_FLOW, validate_runtime_contract

    default_flow = json.loads(
        Path("prompts.v2.default/chat/flow.json").read_text(encoding="utf-8")
    )
    runtime_flow = json.loads(
        Path("data/prompts_v2/chat/flow.json").read_text(encoding="utf-8")
    )

    assert runtime_flow == default_flow
    assert DEFAULT_FLOW == default_flow
    assert default_flow["version"] == 2
    private_edges = [
        edge
        for edge in default_flow["edges"]
        if (edge.get("from"), edge.get("to"))
        == ("base_contract", "private_policy")
    ]
    assert len(private_edges) == 1
    assert private_edges[0]["chat_types"] == ["private"]
    assert private_edges[0]["platforms"] == [
        "web",
        "internal",
        "external_private",
    ]
    validate_runtime_contract(default_flow)


@pytest.mark.asyncio
async def test_unknown_platform_uses_external_private_policy():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            platform="unsupported-platform",
            chat_type="private",
            user_input="你好",
            runtime_tool_prompt="[RuntimeTool]\n只允许 reply/no_reply",
        ),
        strict_audit=True,
    )

    assert plan.platform == "unsupported-platform"
    assert plan.policy_profile == "external_private"
    assert plan.debug["policy_profile"] == "external_private"
    node_ids = [section["node_id"] for section in plan.flow_sections]
    assert "private_policy" in node_ids
    assert "qq_common_policy" not in node_ids


def test_flow_contract_rejects_custom_platform_even_with_complete_paths():
    from core.prompt_v2.flow import PromptFlowError, validate_runtime_contract

    flow = json.loads(
        Path("prompts.v2.default/chat/flow.json").read_text(encoding="utf-8")
    )
    for edge in flow["edges"]:
        if edge.get("platforms") == ["web"]:
            edge["platforms"] = ["web", "custom-platform"]

    with pytest.raises(PromptFlowError, match="platforms 不支持"):
        validate_runtime_contract(flow)

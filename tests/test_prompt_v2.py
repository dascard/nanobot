import dataclasses
import inspect
import json

import pytest


def test_prompt_v2_flow_selects_single_conditional_path_by_edge_condition():
    from core.prompt_v2.flow import ordered_nodes_for_chat

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/main"},
            {"id": "private_policy", "type": "template", "template_key": "chat/branch_private"},
            {"id": "group_policy", "type": "template", "template_key": "chat/branch_group"},
            {"id": "current_user_event", "type": "runtime", "runtime_key": "current_user_event"},
        ],
        "edges": [
            {"from": "base", "to": "private_policy", "chat_types": ["private"]},
            {"from": "base", "to": "group_policy", "chat_types": ["group"]},
            {"from": "private_policy", "to": "current_user_event", "chat_types": ["private"]},
            {"from": "group_policy", "to": "current_user_event", "chat_types": ["group"]},
        ],
    }

    assert [node["id"] for node in ordered_nodes_for_chat(flow, "private")] == [
        "base",
        "private_policy",
        "current_user_event",
    ]
    assert [node["id"] for node in ordered_nodes_for_chat(flow, "group")] == [
        "base",
        "group_policy",
        "current_user_event",
    ]


def test_prompt_v2_flow_rejects_ambiguous_outgoing_branch_condition():
    from core.prompt_v2.flow import PromptFlowError, validate_flow

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/main"},
            {"id": "private_policy", "type": "template", "template_key": "chat/branch_private"},
            {"id": "b", "type": "template", "template_key": "chat/identity_context"},
        ],
        "edges": [
            {"from": "base", "to": "private_policy", "chat_types": ["private"]},
            {"from": "base", "to": "b", "chat_types": ["private"]},
        ],
    }

    with pytest.raises(PromptFlowError, match="同一条件只能有一条出边"):
        validate_flow(flow)


def test_prompt_v2_flow_orders_multiple_entry_nodes_by_topology():
    from core.prompt_v2.flow import ordered_nodes_for_chat

    flow = {
        "version": 1,
        "nodes": [
            {"id": "identity", "type": "template", "template_key": "chat/identity_context"},
            {"id": "base", "type": "template", "template_key": "chat/main"},
            {"id": "current_user_event", "type": "runtime", "runtime_key": "current_user_event"},
        ],
        "edges": [
            {"from": "base", "to": "current_user_event"},
        ],
    }

    assert [node["id"] for node in ordered_nodes_for_chat(flow, "private")] == [
        "identity",
        "base",
        "current_user_event",
    ]


def test_prompt_v2_flow_ignores_nodes_only_used_by_inactive_conditions():
    from core.prompt_v2.flow import ordered_nodes_for_chat

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/main"},
            {"id": "group_policy", "type": "template", "template_key": "chat/branch_group"},
            {"id": "runtime_context", "type": "runtime", "runtime_key": "runtime_context"},
            {"id": "current_user_event", "type": "runtime", "runtime_key": "current_user_event"},
        ],
        "edges": [
            {"from": "base", "to": "group_policy", "chat_types": ["group"]},
            {"from": "group_policy", "to": "runtime_context", "chat_types": ["private"]},
            {"from": "runtime_context", "to": "current_user_event"},
        ],
    }

    assert [node["id"] for node in ordered_nodes_for_chat(flow, "private")] == [
        "group_policy",
        "runtime_context",
        "current_user_event",
    ]


def test_prompt_v2_flow_entry_is_derived_from_in_degree_not_node_order():
    from core.prompt_v2.flow import ordered_nodes_for_chat

    flow = {
        "version": 1,
        "nodes": [
            {"id": "identity", "type": "template", "template_key": "chat/identity_context"},
            {"id": "base", "type": "template", "template_key": "chat/main"},
            {"id": "current_user_event", "type": "runtime", "runtime_key": "current_user_event"},
        ],
        "edges": [
            {"from": "base", "to": "identity"},
            {"from": "identity", "to": "current_user_event"},
        ],
    }

    assert [node["id"] for node in ordered_nodes_for_chat(flow, "private")] == [
        "base",
        "identity",
        "current_user_event",
    ]


def test_prompt_v2_flow_filters_by_chat_type_and_platform():
    from core.prompt_v2.flow import ordered_nodes_for_chat

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/main"},
            {"id": "qq_common", "type": "template", "template_key": "chat/platform/qq/common", "platforms": ["qq"]},
            {"id": "group_policy", "type": "template", "template_key": "chat/branch_group", "chat_types": ["group"]},
            {
                "id": "qq_group",
                "type": "template",
                "template_key": "chat/platform/qq/group",
                "chat_types": ["group"],
                "platforms": ["qq"],
            },
            {"id": "private_policy", "type": "template", "template_key": "chat/branch_private", "chat_types": ["private"]},
            {"id": "current_user_event", "type": "runtime", "runtime_key": "current_user_event"},
        ],
        "edges": [
            {"from": "base", "to": "qq_common", "platforms": ["qq"]},
            {"from": "base", "to": "group_policy", "chat_types": ["group"], "platforms": ["web"]},
            {"from": "qq_common", "to": "group_policy", "chat_types": ["group"], "platforms": ["qq"]},
            {"from": "group_policy", "to": "qq_group", "chat_types": ["group"], "platforms": ["qq"]},
            {"from": "qq_group", "to": "current_user_event", "chat_types": ["group"], "platforms": ["qq"]},
            {"from": "group_policy", "to": "current_user_event", "chat_types": ["group"], "platforms": ["web"]},
            {"from": "qq_common", "to": "private_policy", "chat_types": ["private"], "platforms": ["qq"]},
            {"from": "base", "to": "private_policy", "chat_types": ["private"], "platforms": ["web"]},
            {"from": "private_policy", "to": "current_user_event", "chat_types": ["private"]},
        ],
    }

    assert [node["id"] for node in ordered_nodes_for_chat(flow, "group", platform="qq")] == [
        "base",
        "qq_common",
        "group_policy",
        "qq_group",
        "current_user_event",
    ]
    assert [node["id"] for node in ordered_nodes_for_chat(flow, "private", platform="qq")] == [
        "base",
        "qq_common",
        "private_policy",
        "current_user_event",
    ]
    assert [node["id"] for node in ordered_nodes_for_chat(flow, "private", platform="web")] == [
        "base",
        "private_policy",
        "current_user_event",
    ]


def test_prompt_v2_flow_allows_disjoint_platform_branches_for_same_chat_type():
    from core.prompt_v2.flow import ordered_nodes_for_chat

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/main"},
            {"id": "qq", "type": "template", "template_key": "chat/platform/qq/group"},
            {"id": "group_policy", "type": "template", "template_key": "chat/branch_group"},
            {"id": "current_user_event", "type": "runtime", "runtime_key": "current_user_event"},
        ],
        "edges": [
            {"from": "base", "to": "qq", "chat_types": ["group"], "platforms": ["qq"]},
            {"from": "base", "to": "group_policy", "chat_types": ["group"], "platforms": ["web"]},
            {"from": "qq", "to": "current_user_event", "chat_types": ["group"], "platforms": ["qq"]},
            {"from": "group_policy", "to": "current_user_event", "chat_types": ["group"], "platforms": ["web"]},
        ],
    }

    assert [node["id"] for node in ordered_nodes_for_chat(flow, "group", platform="qq")] == [
        "base",
        "qq",
        "current_user_event",
    ]
    assert [node["id"] for node in ordered_nodes_for_chat(flow, "group", platform="web")] == [
        "base",
        "group_policy",
        "current_user_event",
    ]


def test_prompt_v2_flow_rejects_overlapping_platform_branches():
    from core.prompt_v2.flow import PromptFlowError, validate_flow

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/main"},
            {"id": "group_policy", "type": "template", "template_key": "chat/branch_group"},
            {"id": "qq", "type": "template", "template_key": "chat/platform/qq/group"},
        ],
        "edges": [
            {"from": "base", "to": "group_policy", "chat_types": ["group"]},
            {"from": "base", "to": "qq", "chat_types": ["group"], "platforms": ["qq"]},
        ],
    }

    with pytest.raises(PromptFlowError, match="同一条件只能有一条出边"):
        validate_flow(flow)


def test_prompt_v2_flow_rejects_invalid_platform_values():
    from core.prompt_v2.flow import PromptFlowError, validate_flow

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/main", "platforms": ["QQ!"]},
            {"id": "current_user_event", "type": "runtime", "runtime_key": "current_user_event"},
        ],
        "edges": [{"from": "base", "to": "current_user_event"}],
    }

    with pytest.raises(PromptFlowError, match="platforms 不支持"):
        validate_flow(flow)


def _mutate_reserved_flow_node(flow, node_id, invalid_fields):
    node = next(item for item in flow["nodes"] if item["id"] == node_id)
    replacement_id = invalid_fields.get("id", node_id)
    node.update(invalid_fields)
    if replacement_id == node_id:
        return
    for edge in flow["edges"]:
        if edge["from"] == node_id:
            edge["from"] = replacement_id
        if edge["to"] == node_id:
            edge["to"] = replacement_id


@pytest.mark.parametrize(
    ("node_id", "invalid_fields"),
    [
        ("persona_reference", {"id": "renamed_persona_reference"}),
        (
            "runtime_tool_prompt",
            {"type": "template", "template_key": "chat/main"},
        ),
        ("current_user_event", {"runtime_key": "runtime_context"}),
        ("private_policy", {"id": "renamed_private_policy"}),
        (
            "group_policy",
            {"type": "runtime", "runtime_key": "runtime_context"},
        ),
        ("group_policy", {"template_key": "chat/main"}),
    ],
    ids=(
        "persona-node-id",
        "runtime-tool-node-type",
        "current-user-runtime-key",
        "private-policy-node-id",
        "group-policy-node-type",
        "group-policy-template-key",
    ),
)
def test_prompt_v2_flow_rejects_reserved_section_identity(node_id, invalid_fields):
    import copy

    from core.prompt_v2.flow import DEFAULT_FLOW, PromptFlowError, validate_flow

    flow = copy.deepcopy(DEFAULT_FLOW)
    _mutate_reserved_flow_node(flow, node_id, invalid_fields)

    with pytest.raises(PromptFlowError):
        validate_flow(flow)


def test_prompt_v2_save_flow_rejects_renamed_reserved_node_before_write(
    tmp_path,
    monkeypatch,
):
    import copy

    from core.prompt_v2 import flow as flow_module

    flow = copy.deepcopy(flow_module.DEFAULT_FLOW)
    _mutate_reserved_flow_node(
        flow,
        "runtime_tool_prompt",
        {"id": "renamed_runtime_tool_prompt"},
    )
    runtime_path = tmp_path / "chat" / "flow.json"
    monkeypatch.setattr(flow_module, "runtime_flow_path", lambda: runtime_path)

    with pytest.raises(flow_module.PromptFlowError):
        flow_module.save_flow(flow)

    assert runtime_path.exists() is False


@pytest.mark.parametrize(
    "invalid_variant",
    [
        "missing-singleton",
        "fully-renamed-singleton",
        "platform-excluded-singleton",
        "platform-excluded-policy",
    ],
)
def test_prompt_v2_save_flow_rejects_runtime_contract_that_strict_audit_would_reject(
    tmp_path,
    monkeypatch,
    invalid_variant,
):
    import copy

    from core.prompt_v2 import flow as flow_module

    flow = copy.deepcopy(flow_module.DEFAULT_FLOW)
    if invalid_variant == "missing-singleton":
        flow["nodes"] = [
            node for node in flow["nodes"] if node["id"] != "runtime_tool_prompt"
        ]
        flow["edges"] = [
            edge
            for edge in flow["edges"]
            if "runtime_tool_prompt" not in {edge["from"], edge["to"]}
        ]
        flow["edges"].append(
            {"from": "effort_constraint", "to": "current_user_event"}
        )
    elif invalid_variant == "fully-renamed-singleton":
        _mutate_reserved_flow_node(
            flow,
            "runtime_tool_prompt",
            {
                "id": "custom_runtime_context",
                "runtime_key": "runtime_context",
            },
        )
    elif invalid_variant == "platform-excluded-singleton":
        node = next(
            item for item in flow["nodes"] if item["id"] == "runtime_tool_prompt"
        )
        node["platforms"] = ["qq"]
    else:
        node = next(item for item in flow["nodes"] if item["id"] == "private_policy")
        node["platforms"] = ["qq"]

    runtime_path = tmp_path / invalid_variant / "chat" / "flow.json"
    monkeypatch.setattr(flow_module, "runtime_flow_path", lambda: runtime_path)

    with pytest.raises(flow_module.PromptFlowError):
        flow_module.save_flow(flow)

    assert runtime_path.exists() is False


def test_prompt_v2_save_flow_accepts_default_platform_branches(tmp_path, monkeypatch):
    from pathlib import Path

    from core.prompt_v2 import flow as flow_module

    flow = json.loads(
        Path("prompts.v2.default/chat/flow.json").read_text(encoding="utf-8")
    )
    runtime_path = tmp_path / "chat" / "flow.json"
    monkeypatch.setattr(flow_module, "runtime_flow_path", lambda: runtime_path)

    result = flow_module.save_flow(flow)

    assert result["saved"] is True
    assert result["flow"] == flow_module.validate_flow(flow)
    assert runtime_path.exists() is True


def test_prompt_v2_template_values_and_runtime_context_include_platform():
    from core.prompt_v2.context_adapters import build_runtime_context, build_template_values
    from core.prompt_v2.schema import PromptCompileRequest
    from core.prompt_v2.variables import render_scoped_template

    request = PromptCompileRequest(chat_type="group", platform="Web", session_id="group_1001")
    values = build_template_values(request, current_time="2026-06-18 10:00:00 CST")

    assert request.normalized_platform == "web"
    assert values["platform"] == "web"
    assert render_scoped_template("chat/main", "platform={{ platform }}", values) == "platform=web"

    runtime_context = build_runtime_context(request, current_time=values["current_time"])
    assert "platform: web" in runtime_context
    assert "chat_type: group" in runtime_context


@pytest.mark.asyncio
async def test_prompt_v2_compile_plan_exposes_platform(monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    from core.prompt_v2 import compiler
    from core.prompt_v2.schema import PromptCompileRequest

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/main"},
            {
                "id": "private_policy",
                "type": "template",
                "template_key": "chat/branch_private",
                "chat_types": ["private"],
                "platforms": ["web"],
            },
            {"id": "current_user_event", "type": "runtime", "runtime_key": "current_user_event"},
        ],
        "edges": [
            {"from": "base", "to": "private_policy", "chat_types": ["private"], "platforms": ["web"]},
            {"from": "private_policy", "to": "current_user_event", "chat_types": ["private"], "platforms": ["web"]},
        ],
    }
    monkeypatch.setattr(
        compiler,
        "load_flow",
        lambda: SimpleNamespace(flow=flow, path=Path("test-flow.json"), source="test"),
    )

    plan = await compiler.compile_prompt_plan(
        PromptCompileRequest(chat_type="private", platform="web", user_input="你好"),
    )

    assert plan.platform == "web"
    assert plan.debug["platform"] == "web"
    assert plan.debug["flow_node_ids"] == ["base", "private_policy", "current_user_event"]


@pytest.mark.asyncio
async def test_prompt_v2_default_flow_selects_qq_platform_templates():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(chat_type="group", platform="qq", user_input="你好"),
    )

    assert plan.platform == "qq"
    assert plan.debug["platform"] == "qq"
    assert "qq_common_policy" in plan.debug["flow_node_ids"]
    assert "qq_group_policy" in plan.debug["flow_node_ids"]
    joined = "\n".join(str(message["content"]) for message in plan.messages)
    assert "platform: qq" in joined
    assert "QQ 平台" in joined
    assert "QQ 群聊" in joined


@pytest.mark.asyncio
async def test_prompt_v2_default_flow_skips_qq_templates_for_web_private():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(chat_type="private", platform="web", user_input="你好"),
    )

    assert plan.platform == "web"
    assert plan.debug["platform"] == "web"
    assert "qq_common_policy" not in plan.debug["flow_node_ids"]
    assert "qq_group_policy" not in plan.debug["flow_node_ids"]
    joined = "\n".join(str(message["content"]) for message in plan.messages)
    assert "platform: web" in joined
    assert "QQ 平台" not in joined
    assert "OneBot" not in joined
    assert "CQ 码" not in joined


@pytest.mark.asyncio
async def test_prompt_v2_compiles_group_plan_without_duplicate_dynamic_sections():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest, PromptPlan

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            chat_type="group",
            session_id="group_1001",
            user_id="group_1001",
            group_id="1001",
            sender_name="雀",
            sender_id="0000000000",
            bot_name="nanobot",
            bot_aliases=["bot"],
            current_message_id="m1",
            trigger_reason="direct_mention",
            timing_decision="continue",
            user_input="当前问题",
            persona_text="画像文本",
            history_header="<conversation_context>\n群聊历史说明\n</conversation_context>",
            history_messages=[
                {"role": "user", "content": "[msg_id]old\n[发言内容]旧问题"},
                {"role": "assistant", "content": "[发言内容]旧回复"},
            ],
            group_profile_context="[GroupProfileContext]\ngroup_id: 1001\n- style: 轻松\n[/GroupProfileContext]",
            expression_context="[ExpressionContext]\n- 哈哈\n[/ExpressionContext]",
            jargon_context="[JargonContext]\n- 梗=解释\n[/JargonContext]",
            runtime_tool_prompt="[RuntimeTool]\n规则：必须 reply/no_reply",
            tool_schemas=[{"type": "function", "function": {"name": "reply"}}],
        )
    )

    assert dataclasses.is_dataclass(plan)
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.engine = "v1"
    assert isinstance(plan, PromptPlan)
    assert plan.engine == "prompt"
    assert plan.chat_type == "group"
    assert plan.prompt_key == "chat_group"
    assert plan.messages_without_current_user == plan.messages[:-1]
    assert plan.current_user_content == plan.messages[-1]["content"]
    assert plan.request_json["messages"] == plan.messages
    assert plan.request_json["tools"] == plan.tool_schemas
    assert len(plan.prompt_sha256) == 64
    assert plan.token_estimate > 0
    assert plan.section_hashes["base_contract"]
    assert plan.section_hashes["runtime_tool_prompt"]
    assert plan.debug["history_message_count"] == 2
    assert plan.debug["flow_node_ids"][:4] == [
        "base_contract",
        "qq_common_policy",
        "group_policy",
        "qq_group_policy",
    ]
    assert all(
        {"node_id", "node_type", "template_key", "runtime_key"}.issubset(section)
        for section in plan.flow_sections
    )

    roles = [m["role"] for m in plan.messages]
    assert roles == [
        "system",
        "system",
        "system",
        "system",
        "system",
        "system",
        "system",
        "system",
        "user",
        "assistant",
        "system",
        "system",
        "user",
    ]

    contents = [str(m["content"]) for m in plan.messages]
    joined = "\n".join(contents)
    assert "## QQ 平台" in joined
    assert "## QQ 群聊" in joined
    assert "## 群聊行为" in joined
    assert "## 私聊行为" not in joined
    assert sum("<user_input>" in c for c in contents) == 1
    assert sum("[RuntimeTool]" in c for c in contents) == 1
    assert sum("<persona_reference" in c for c in contents) == 1
    assert "当前问题" in plan.current_user_content
    assert "当前问题" not in "\n".join(contents[:-1])
    serialized_plan = json.loads(json.dumps(plan.to_dict(), ensure_ascii=False))
    assert serialized_plan["engine"] == "prompt"
    assert serialized_plan["flow_sections"] == plan.flow_sections


@pytest.mark.asyncio
async def test_prompt_v2_flow_sections_describe_output_status_and_message_indexes(
    monkeypatch,
):
    from pathlib import Path
    from types import SimpleNamespace

    from core.prompt_v2 import compiler
    from core.prompt_v2.schema import PromptCompileRequest

    ordered_nodes = [
        {"id": "base_contract", "type": "template", "template_key": "chat/main"},
        {
            "id": "private_policy",
            "type": "template",
            "template_key": "chat/branch_private",
        },
        {"id": "runtime_context", "type": "runtime", "runtime_key": "runtime_context"},
        {
            "id": "effort_constraint",
            "type": "runtime",
            "runtime_key": "effort_constraint",
        },
        {
            "id": "runtime_tool_prompt",
            "type": "runtime",
            "runtime_key": "runtime_tool_prompt",
        },
        {
            "id": "current_user_event",
            "type": "runtime",
            "runtime_key": "current_user_event",
        },
        {
            "id": "duplicate_runtime_tool_prompt",
            "type": "runtime",
            "runtime_key": "runtime_tool_prompt",
        },
    ]
    monkeypatch.setattr(
        compiler,
        "load_flow",
        lambda: SimpleNamespace(flow={}, path=Path("test-flow.json"), source="test"),
    )
    monkeypatch.setattr(
        compiler,
        "ordered_nodes_for_chat",
        lambda _flow, _chat_type, platform: ordered_nodes,
    )
    original_load_template = compiler.load_template

    def load_template(template_key):
        template = original_load_template(template_key)
        if template_key == "chat/main":
            return dataclasses.replace(template, body="BASE SECTION")
        return template

    monkeypatch.setattr(compiler, "load_template", load_template)

    plan = await compiler.compile_prompt_plan(
        PromptCompileRequest(
            chat_type="private",
            platform="web",
            user_id="u1",
            user_input="CURRENT USER SECTION",
            persona_text="FALLBACK PERSONA",
            runtime_tool_prompt="RUNTIME TOOL SECTION",
        )
    )
    sections = {section["node_id"]: section for section in plan.flow_sections}
    legacy_fields = {"node_id", "node_type", "template_key", "runtime_key"}

    assert all(legacy_fields.issubset(section) for section in plan.flow_sections)

    base = sections["base_contract"]
    assert (base["origin"], base["status"]) == ("flow", "emitted")
    assert [plan.messages[index]["content"] for index in base["message_indexes"]] == [
        "BASE SECTION"
    ]

    runtime_tool = sections["runtime_tool_prompt"]
    assert (runtime_tool["origin"], runtime_tool["status"]) == ("flow", "emitted")
    assert [
        plan.messages[index]["content"]
        for index in runtime_tool["message_indexes"]
    ] == ["RUNTIME TOOL SECTION"]

    effort = sections["effort_constraint"]
    assert (effort["origin"], effort["status"], effort["message_indexes"]) == (
        "flow",
        "empty",
        [],
    )

    duplicate = sections["duplicate_runtime_tool_prompt"]
    assert (
        duplicate["origin"],
        duplicate["status"],
        duplicate["message_indexes"],
    ) == ("flow", "skipped_duplicate", [])

    persona = sections["persona_reference"]
    assert (persona["origin"], persona["status"]) == ("fallback", "emitted")
    assert persona["message_indexes"]
    assert all(
        "FALLBACK PERSONA" in str(plan.messages[index]["content"])
        for index in persona["message_indexes"]
    )

    current_user = sections["current_user_event"]
    assert (current_user["origin"], current_user["status"]) == ("flow", "emitted")
    assert current_user["message_indexes"] == [len(plan.messages) - 1]
    assert plan.messages[current_user["message_indexes"][0]] == plan.messages[-1]


@pytest.mark.asyncio
async def test_prompt_v2_compiles_private_plan_and_keeps_rules_separate():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            chat_type="private",
            session_id="private_u1",
            user_id="u1",
            sender_name="用户",
            user_input="<user_input>\n你好\n</user_input>",
            persona_text="无已存储画像",
            history_header="<conversation_context>\n私聊历史说明\n</conversation_context>",
            history_messages=[{"role": "user", "content": "上文"}],
            runtime_tool_prompt="[RuntimeTool]\n规则：必须 reply/no_reply",
        )
    )

    joined = "\n".join(str(m["content"]) for m in plan.messages)
    assert plan.chat_type == "private"
    assert plan.prompt_key == "chat_private"
    assert "## 私聊行为" in joined
    assert "## 群聊行为" not in joined
    assert "## 群聊发言时机" not in joined
    assert plan.current_user_content == "<user_input>\n你好\n</user_input>"
    assert sum("<user_input>" in str(m["content"]) for m in plan.messages) == 1


@pytest.mark.asyncio
async def test_prompt_v2_moves_group_profile_header_after_history_messages():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            chat_type="group",
            user_input="当前输入",
            history_header=(
                "[GroupProfileContext]\n"
                "group_id: 1001\n"
                "- style: 轻松\n"
                "[/GroupProfileContext]\n"
                "<conversation_context>\n群聊历史说明\n</conversation_context>"
            ),
            history_messages=[{"role": "user", "content": "UNIQUE_HISTORY_MESSAGE"}],
            runtime_tool_prompt="[RuntimeTool]\n必须 reply/no_reply",
        )
    )

    contents = [str(m["content"]) for m in plan.messages]
    header_idx = next(i for i, c in enumerate(contents) if "<conversation_context>" in c)
    history_idx = next(i for i, c in enumerate(contents) if "UNIQUE_HISTORY_MESSAGE" in c)
    profile_idx = next(i for i, c in enumerate(contents) if "[GroupProfileContext]" in c)

    assert header_idx < history_idx < profile_idx
    assert sum("[GroupProfileContext]" in c for c in contents) == 1
    assert "[GroupProfileContext]" not in contents[header_idx]


@pytest.mark.asyncio
async def test_prompt_v2_moves_group_memory_context_after_history_messages():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            chat_type="group",
            user_input="当前输入",
            history_header=(
                '<group_memory_context group_id="group_1001" selected_count="1">\n'
                "- 群风格: 轻松\n"
                "</group_memory_context>\n"
                "<conversation_context>\n群聊历史说明\n</conversation_context>"
            ),
            history_messages=[{"role": "user", "content": "UNIQUE_HISTORY_MESSAGE"}],
            runtime_tool_prompt="[RuntimeTool]\n必须 reply/no_reply",
        )
    )

    contents = [str(m["content"]) for m in plan.messages]
    header_idx = next(i for i, c in enumerate(contents) if "<conversation_context>" in c)
    history_idx = next(i for i, c in enumerate(contents) if "UNIQUE_HISTORY_MESSAGE" in c)
    memory_idx = next(i for i, c in enumerate(contents) if 'selected_count="1"' in c)

    assert header_idx < history_idx < memory_idx
    assert sum('selected_count="1"' in c for c in contents) == 1
    assert "<group_memory_context" not in contents[header_idx]
    assert "[GroupProfileContext]" not in contents[memory_idx]


def test_prompt_v2_audit_reports_duplicate_required_sections():
    from core.prompt_v2.audit import audit_prompt_plan
    from core.prompt_v2.schema import PromptPlan

    plan = PromptPlan(
        engine="v2",
        chat_type="group",
        prompt_key="chat_group",
        messages=[{"role": "user", "content": "正文不参与结构审计"}],
        tool_schemas=[],
        section_hashes={},
        prompt_sha256="x" * 64,
        token_estimate=1,
        warnings=[],
        debug={},
        flow_sections=[
            {
                "node_id": "private_policy",
                "node_type": "template",
                "template_key": "chat/branch_private",
                "runtime_key": "",
            },
            *[
                {
                    "node_id": f"{runtime_key}_{index}",
                    "node_type": "runtime",
                    "template_key": "",
                    "runtime_key": runtime_key,
                }
                for runtime_key in ("persona_reference", "runtime_tool_prompt", "current_user_event")
                for index in range(2)
            ],
        ],
    )

    audit = audit_prompt_plan(plan)
    assert audit.ok is False
    assert any("current user input" in issue for issue in audit.issues)
    assert any("runtime_tool_prompt" in issue for issue in audit.issues)
    assert any("persona_reference" in issue for issue in audit.issues)
    assert any("group plan must select its policy" in issue for issue in audit.issues)
    assert any("group plan contains private policy" in issue for issue in audit.issues)


def _valid_prompt_v2_flow_sections(chat_type: str) -> list[dict[str, str]]:
    return [
        {
            "node_id": f"{chat_type}_policy",
            "node_type": "template",
            "template_key": f"chat/branch_{chat_type}",
            "runtime_key": "",
        },
        *[
            {
                "node_id": runtime_key,
                "node_type": "runtime",
                "template_key": "",
                "runtime_key": runtime_key,
            }
            for runtime_key in ("persona_reference", "runtime_tool_prompt", "current_user_event")
        ],
    ]


def test_prompt_v2_audit_accepts_legacy_sections_without_output_metadata():
    from core.prompt_v2.audit import audit_prompt_plan
    from core.prompt_v2.schema import PromptPlan

    plan = PromptPlan(
        engine="prompt",
        chat_type="private",
        prompt_key="chat_private",
        messages=[],
        tool_schemas=[],
        section_hashes={},
        prompt_sha256="x" * 64,
        token_estimate=0,
        warnings=[],
        debug={},
        flow_sections=_valid_prompt_v2_flow_sections("private"),
    )

    assert audit_prompt_plan(plan).ok is True


def test_prompt_v2_audit_does_not_treat_explicit_empty_status_as_legacy():
    from core.prompt_v2.audit import audit_prompt_plan
    from core.prompt_v2.schema import PromptPlan

    flow_sections = _valid_prompt_v2_flow_sections("private")
    next(
        section
        for section in flow_sections
        if section["runtime_key"] == "persona_reference"
    )["status"] = ""
    plan = PromptPlan(
        engine="prompt",
        chat_type="private",
        prompt_key="chat_private",
        messages=[],
        tool_schemas=[],
        section_hashes={},
        prompt_sha256="x" * 64,
        token_estimate=0,
        warnings=[],
        debug={},
        flow_sections=flow_sections,
    )

    audit = audit_prompt_plan(plan)

    assert audit.ok is False
    assert any("persona_reference status must be emitted" in issue for issue in audit.issues)


@pytest.mark.parametrize(
    ("runtime_key", "invalid_fields"),
    [
        ("persona_reference", {"node_id": "renamed_persona_reference"}),
        ("runtime_tool_prompt", {"node_type": "template"}),
        ("current_user_event", {"template_key": "chat/main"}),
    ],
    ids=("wrong-node-id", "wrong-node-type", "unexpected-template-key"),
)
def test_prompt_v2_audit_rejects_invalid_singleton_section_identity(runtime_key, invalid_fields):
    from core.prompt_v2.audit import audit_prompt_plan
    from core.prompt_v2.schema import PromptPlan

    flow_sections = _valid_prompt_v2_flow_sections("private")
    section = next(item for item in flow_sections if item["runtime_key"] == runtime_key)
    section.update(invalid_fields)
    plan = PromptPlan(
        engine="prompt",
        chat_type="private",
        prompt_key="chat_private",
        messages=[],
        tool_schemas=[],
        section_hashes={},
        prompt_sha256="x" * 64,
        token_estimate=0,
        warnings=[],
        debug={},
        flow_sections=flow_sections,
    )

    audit = audit_prompt_plan(plan)

    assert audit.ok is False
    assert any(runtime_key in issue for issue in audit.issues)


@pytest.mark.parametrize(
    ("chat_type", "invalid_fields"),
    [
        ("private", {"node_id": "renamed_private_policy"}),
        ("private", {"node_type": "runtime"}),
        ("private", {"runtime_key": "unexpected_policy_runtime"}),
        ("private", {"template_key": "chat/main"}),
        ("group", {"node_id": "renamed_group_policy"}),
        ("group", {"node_type": "runtime"}),
        ("group", {"runtime_key": "unexpected_policy_runtime"}),
        ("group", {"template_key": "chat/main"}),
    ],
    ids=(
        "private-wrong-node-id",
        "private-wrong-node-type",
        "private-unexpected-runtime-key",
        "private-wrong-template-key",
        "group-wrong-node-id",
        "group-wrong-node-type",
        "group-unexpected-runtime-key",
        "group-wrong-template-key",
    ),
)
def test_prompt_v2_audit_rejects_invalid_policy_section_identity(chat_type, invalid_fields):
    from core.prompt_v2.audit import audit_prompt_plan
    from core.prompt_v2.schema import PromptPlan

    flow_sections = _valid_prompt_v2_flow_sections(chat_type)
    flow_sections[0].update(invalid_fields)
    plan = PromptPlan(
        engine="prompt",
        chat_type=chat_type,
        prompt_key=f"chat_{chat_type}",
        messages=[],
        tool_schemas=[],
        section_hashes={},
        prompt_sha256="x" * 64,
        token_estimate=0,
        warnings=[],
        debug={},
        flow_sections=flow_sections,
    )

    audit = audit_prompt_plan(plan)

    assert audit.ok is False
    assert any(chat_type in issue for issue in audit.issues)


@pytest.mark.asyncio
@pytest.mark.parametrize("strict_audit", [True, False], ids=("strict", "non-strict"))
async def test_prompt_v2_compile_audit_rejects_renamed_singleton_node(
    monkeypatch,
    strict_audit,
):
    import copy
    from pathlib import Path
    from types import SimpleNamespace

    from core.prompt_v2 import compiler
    from core.prompt_v2.flow import DEFAULT_FLOW, PromptFlowError
    from core.prompt_v2.schema import PromptCompileRequest

    flow = copy.deepcopy(DEFAULT_FLOW)
    old_node_id = "runtime_tool_prompt"
    new_node_id = "renamed_runtime_tool_prompt"
    next(node for node in flow["nodes"] if node["id"] == old_node_id)["id"] = new_node_id
    for edge in flow["edges"]:
        if edge["from"] == old_node_id:
            edge["from"] = new_node_id
        if edge["to"] == old_node_id:
            edge["to"] = new_node_id
    monkeypatch.setattr(
        compiler,
        "load_flow",
        lambda: SimpleNamespace(flow=flow, path=Path("test-flow.json"), source="test"),
    )
    request = PromptCompileRequest(chat_type="private", user_input="你好")

    with pytest.raises(PromptFlowError):
        await compiler.compile_prompt_plan(request, strict_audit=strict_audit)


@pytest.mark.asyncio
async def test_prompt_v2_strict_audit_rejects_missing_policy_template(monkeypatch):
    from core.prompt_v2 import compiler
    from core.prompt_v2.audit import PromptAuditError
    from core.prompt_v2.schema import PromptCompileRequest

    original_load_template = compiler.load_template

    def load_template(prompt_key):
        if prompt_key == "chat/branch_private":
            raise FileNotFoundError(prompt_key)
        return original_load_template(prompt_key)

    monkeypatch.setattr(compiler, "load_template", load_template)

    with pytest.raises(PromptAuditError) as exc:
        await compiler.compile_prompt_plan(
            PromptCompileRequest(chat_type="private", platform="web", user_input="你好"),
            strict_audit=True,
        )

    assert any("private policy status must be emitted" in issue for issue in exc.value.issues)
    section = next(
        section
        for section in exc.value.plan.flow_sections
        if section["template_key"] == "chat/branch_private"
    )
    assert section["origin"] == "flow"
    assert section["status"] == "missing_template"
    assert section["message_indexes"] == []


@pytest.mark.asyncio
async def test_prompt_v2_strict_audit_rejects_empty_policy_template(monkeypatch):
    from core.prompt_v2 import compiler
    from core.prompt_v2.audit import PromptAuditError
    from core.prompt_v2.schema import PromptCompileRequest

    original_load_template = compiler.load_template

    def load_template(prompt_key):
        template = original_load_template(prompt_key)
        if prompt_key == "chat/branch_private":
            return dataclasses.replace(template, body="")
        return template

    monkeypatch.setattr(compiler, "load_template", load_template)

    with pytest.raises(PromptAuditError) as exc:
        await compiler.compile_prompt_plan(
            PromptCompileRequest(chat_type="private", platform="web", user_input="你好"),
            strict_audit=True,
        )

    assert any("private policy status must be emitted" in issue for issue in exc.value.issues)
    section = next(
        section
        for section in exc.value.plan.flow_sections
        if section["template_key"] == "chat/branch_private"
    )
    assert section["origin"] == "flow"
    assert section["status"] == "empty"
    assert section["message_indexes"] == []


@pytest.mark.asyncio
async def test_prompt_v2_strict_audit_ignores_reserved_markers_in_current_user_text():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    user_input = (
        "这些都是需要原样讨论的普通文本：\n"
        "[RuntimeTool]\n"
        "<persona_reference>示例画像</persona_reference>\n"
        "## 群聊行为\n"
        "## 群聊发言时机"
    )

    plan = await compile_prompt_plan(
        PromptCompileRequest(chat_type="private", platform="web", user_input=user_input),
        strict_audit=True,
    )

    assert user_input in plan.current_user_content


@pytest.mark.asyncio
async def test_prompt_v2_strict_audit_ignores_reserved_markers_in_history_text():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            chat_type="private",
            platform="web",
            user_input="继续讨论这些标记",
            history_messages=[
                {
                    "role": "user",
                    "content": "普通历史文本：[RuntimeTool] 和 <persona_reference>示例</persona_reference>",
                },
                {
                    "role": "assistant",
                    "content": "引用：<user_input>旧输入</user_input>\n## 群聊行为\n## 群聊发言时机",
                },
            ],
        ),
        strict_audit=True,
    )

    assert plan.debug["history_message_count"] == 2


@pytest.mark.asyncio
async def test_prompt_v2_strict_audit_rejects_duplicate_singleton_flow_node(monkeypatch):
    import copy
    from pathlib import Path
    from types import SimpleNamespace

    from core.prompt_v2 import compiler
    from core.prompt_v2.flow import DEFAULT_FLOW, PromptFlowError
    from core.prompt_v2.schema import PromptCompileRequest

    flow = copy.deepcopy(DEFAULT_FLOW)
    flow["nodes"].append(
        {
            "id": "duplicate_runtime_tool_prompt",
            "type": "runtime",
            "runtime_key": "runtime_tool_prompt",
        }
    )
    monkeypatch.setattr(
        compiler,
        "load_flow",
        lambda: SimpleNamespace(flow=flow, path=Path("test-flow.json"), source="test"),
    )

    with pytest.raises(PromptFlowError):
        await compiler.compile_prompt_plan(
            PromptCompileRequest(chat_type="private", user_input="你好"),
            strict_audit=True,
        )


@pytest.mark.asyncio
async def test_prompt_v2_strict_audit_rejects_missing_singleton_flow_node(monkeypatch):
    import copy
    from pathlib import Path
    from types import SimpleNamespace

    from core.prompt_v2 import compiler
    from core.prompt_v2.audit import PromptAuditError
    from core.prompt_v2.flow import DEFAULT_FLOW
    from core.prompt_v2.schema import PromptCompileRequest

    flow = copy.deepcopy(DEFAULT_FLOW)
    flow["nodes"] = [node for node in flow["nodes"] if node["id"] != "runtime_tool_prompt"]
    flow["edges"] = [
        edge
        for edge in flow["edges"]
        if "runtime_tool_prompt" not in {edge["from"], edge["to"]}
    ]
    flow["edges"].append({"from": "effort_constraint", "to": "current_user_event"})
    monkeypatch.setattr(
        compiler,
        "load_flow",
        lambda: SimpleNamespace(flow=flow, path=Path("test-flow.json"), source="test"),
    )

    with pytest.raises(PromptAuditError) as exc:
        await compiler.compile_prompt_plan(
            PromptCompileRequest(chat_type="private", user_input="你好"),
            strict_audit=True,
        )

    assert any(
        "runtime_tool_prompt flow node must appear once, got 0" in issue
        for issue in exc.value.issues
    )
    fallback = next(
        section
        for section in exc.value.plan.flow_sections
        if section["runtime_key"] == "runtime_tool_prompt"
        and section["origin"] == "fallback"
    )
    assert fallback["status"] == "emitted"
    assert fallback["message_indexes"]


@pytest.mark.asyncio
async def test_prompt_v2_strict_audit_raises_instead_of_returning_warning(monkeypatch):
    import core.prompt_v2.compiler as compiler
    from core.prompt_v2.audit import PromptAuditError, PromptAuditResult
    from core.prompt_v2.schema import PromptCompileRequest

    monkeypatch.setattr(
        compiler,
        "audit_prompt_plan",
        lambda _plan: PromptAuditResult(ok=False, issues=["audit broken"]),
    )

    preview_plan = await compiler.compile_prompt_plan(
        PromptCompileRequest(chat_type="group", user_input="你好"),
    )
    assert "audit broken" in preview_plan.warnings

    with pytest.raises(PromptAuditError) as exc:
        await compiler.compile_prompt_plan(
            PromptCompileRequest(chat_type="group", user_input="你好"),
            strict_audit=True,
        )

    assert "audit broken" in str(exc.value)


@pytest.mark.asyncio
async def test_prompt_v2_identity_context_renders_whitelisted_variables():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            chat_type="private",
            sender_id="0000000000",
            bot_name="七濑",
            bot_aliases=["小七", "bot"],
            user_input="你好",
            runtime_tool_prompt="[RuntimeTool]\n必须 reply/no_reply",
        )
    )

    identity = next(str(m["content"]) for m in plan.messages if "<identity_context>" in str(m["content"]))
    assert "你叫 七濑" in identity
    assert "别人可能这样叫你" in identity
    assert "小七" in identity
    assert "bot" in identity
    assert "{{" not in identity
    assert "}}" not in identity


@pytest.mark.asyncio
async def test_prompt_v2_identity_context_uses_non_empty_default_values(monkeypatch):
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    monkeypatch.setattr("core.identity.NANOBOT_CHARACTER_NAME", "nanobot")
    monkeypatch.setattr("core.identity.NANOBOT_BOT_ALIASES", {"nanobot"})
    monkeypatch.setattr("core.identity.NANOBOT_SUPER_USER_IDS", set())

    plan = await compile_prompt_plan(
        PromptCompileRequest(
            chat_type="private",
            user_input="你好",
            runtime_tool_prompt="[RuntimeTool]\n必须 reply/no_reply",
        )
    )

    identity = next(str(m["content"]) for m in plan.messages if "<identity_context>" in str(m["content"]))
    assert "你叫 nanobot" in identity
    assert "nanobot" in identity
    assert "super_user_id:" not in identity
    assert "{{" not in identity
    assert "character_name:" not in identity


def test_prompt_v2_section_variables_are_whitelisted_by_scope():
    from core.prompt_v2.variables import PromptVariableError, render_scoped_template

    rendered = render_scoped_template(
        "identity_context",
        "你叫 {{character_name}}\n{{ name_hint }}\n{{ alias_names }}\n{{is_super_user}}",
        {
            "character_name": "七濑",
            "name_hint": "小七",
            "alias_names": "小七\n七七",
            "is_super_user": "true",
        },
    )
    assert rendered == "你叫 七濑\n小七\n小七\n七七\ntrue"

    with pytest.raises(PromptVariableError):
        render_scoped_template("identity_context", "{{ user_input }}", {"user_input": "禁止"})

    with pytest.raises(PromptVariableError):
        render_scoped_template("identity_context", "{{ unknown_name }}", {})


def test_prompt_v2_renders_classifier_legacy_task_template(tmp_path, monkeypatch):
    default_dir = tmp_path / "defaults"
    runtime_dir = tmp_path / "runtime"
    task_path = default_dir / "tasks" / "classifier_legacy.md"
    task_path.parent.mkdir(parents=True)
    task_path.write_text(
        "---\nname: 旧分类器兼容\nversion: 1\nkind: task\ntool_name: classifier_legacy\n---\n"
        "{{ system_prompt }}\n待判定消息:\n{{ message }}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(runtime_dir))

    from core.prompt_v2.task_templates import render_task_prompt

    rendered = render_task_prompt(
        "classifier_legacy",
        {"system_prompt": "旧系统", "message": "ping"},
        fallback_text="fallback",
    )

    assert "旧系统" in rendered
    assert "待判定消息:" in rendered
    assert "ping" in rendered


def test_prompt_v2_renders_memory_extract_task_template(tmp_path, monkeypatch):
    default_dir = tmp_path / "defaults"
    task_path = default_dir / "tasks" / "memory_extract.md"
    task_path.parent.mkdir(parents=True)
    task_path.write_text(
        "---\nname: 记忆抽取\nversion: 1\nkind: task\ntool_name: memory_extract\n---\n"
        "已有记忆:\n{{ existing_memory }}\n对话:\n{{ conversation }}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
    monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(default_dir / "runtime"))

    from core.prompt_v2.task_templates import render_task_prompt

    rendered = render_task_prompt(
        "memory_extract",
        {"conversation": "用户: 喜欢 TypeScript", "existing_memory": "{}"},
        fallback_text="fallback",
    )

    assert "喜欢 TypeScript" in rendered
    assert "已有记忆:" in rendered


def test_prompt_v2_templates_are_isolated_from_prompt_manager_and_legacy_runtime():
    import core.prompt_v2.compiler as compiler
    from core.prompt_v2.template_loader import load_template

    source = inspect.getsource(compiler)
    assert "core.legacy_prompt_runtime" not in source
    assert "core.prompt_runtime" not in source
    assert "PromptManager" not in source
    assert "prompt_assembler" not in source
    assert "build_nanobot_prompt" not in source

    main = load_template("chat/main").body
    group = load_template("chat/branch_group").body
    private = load_template("chat/branch_private").body
    assert "{{ user_input }}" not in main
    assert "{{ user_input }}" not in group
    assert "{{ user_input }}" not in private
    assert "## 群聊行为" not in main
    assert "## 私聊行为" not in main
    assert "## 群聊行为" in group
    assert "## 私聊行为" not in group
    assert "## 私聊行为" in private
    assert "## 群聊行为" not in private

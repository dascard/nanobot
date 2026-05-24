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
            {"id": "private_branch", "type": "template", "template_key": "chat/branch_private"},
            {"id": "group_branch", "type": "template", "template_key": "chat/branch_group"},
            {"id": "tail", "type": "runtime", "runtime_key": "current_user_event"},
        ],
        "edges": [
            {"from": "base", "to": "private_branch", "chat_types": ["private"]},
            {"from": "base", "to": "group_branch", "chat_types": ["group"]},
            {"from": "private_branch", "to": "tail", "chat_types": ["private"]},
            {"from": "group_branch", "to": "tail", "chat_types": ["group"]},
        ],
    }

    assert [node["id"] for node in ordered_nodes_for_chat(flow, "private")] == [
        "base",
        "private_branch",
        "tail",
    ]
    assert [node["id"] for node in ordered_nodes_for_chat(flow, "group")] == [
        "base",
        "group_branch",
        "tail",
    ]


def test_prompt_v2_flow_rejects_ambiguous_outgoing_branch_condition():
    from core.prompt_v2.flow import PromptFlowError, validate_flow

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/main"},
            {"id": "a", "type": "template", "template_key": "chat/branch_private"},
            {"id": "b", "type": "template", "template_key": "chat/identity_context"},
        ],
        "edges": [
            {"from": "base", "to": "a", "chat_types": ["private"]},
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
            {"id": "tail", "type": "runtime", "runtime_key": "current_user_event"},
        ],
        "edges": [
            {"from": "base", "to": "tail"},
        ],
    }

    assert [node["id"] for node in ordered_nodes_for_chat(flow, "private")] == [
        "identity",
        "base",
        "tail",
    ]


def test_prompt_v2_flow_ignores_nodes_only_used_by_inactive_conditions():
    from core.prompt_v2.flow import ordered_nodes_for_chat

    flow = {
        "version": 1,
        "nodes": [
            {"id": "base", "type": "template", "template_key": "chat/main"},
            {"id": "inactive_group_branch", "type": "template", "template_key": "chat/branch_group"},
            {"id": "runtime_context", "type": "runtime", "runtime_key": "runtime_context"},
            {"id": "tail", "type": "runtime", "runtime_key": "current_user_event"},
        ],
        "edges": [
            {"from": "base", "to": "inactive_group_branch", "chat_types": ["group"]},
            {"from": "inactive_group_branch", "to": "runtime_context", "chat_types": ["private"]},
            {"from": "runtime_context", "to": "tail"},
        ],
    }

    assert [node["id"] for node in ordered_nodes_for_chat(flow, "private")] == [
        "inactive_group_branch",
        "runtime_context",
        "tail",
    ]


def test_prompt_v2_flow_entry_is_derived_from_in_degree_not_node_order():
    from core.prompt_v2.flow import ordered_nodes_for_chat

    flow = {
        "version": 1,
        "nodes": [
            {"id": "identity", "type": "template", "template_key": "chat/identity_context"},
            {"id": "base", "type": "template", "template_key": "chat/main"},
            {"id": "tail", "type": "runtime", "runtime_key": "current_user_event"},
        ],
        "edges": [
            {"from": "base", "to": "identity"},
            {"from": "identity", "to": "tail"},
        ],
    }

    assert [node["id"] for node in ordered_nodes_for_chat(flow, "private")] == ["base", "identity", "tail"]


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
    assert plan.engine == "v2"
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

    roles = [m["role"] for m in plan.messages]
    assert roles == [
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
    assert "## 群聊行为" in joined
    assert "## 私聊行为" not in joined
    assert sum("<user_input>" in c for c in contents) == 1
    assert sum("[RuntimeTool]" in c for c in contents) == 1
    assert sum("<persona_reference" in c for c in contents) == 1
    assert "当前问题" in plan.current_user_content
    assert "当前问题" not in "\n".join(contents[:-1])
    assert json.loads(json.dumps(plan.to_dict(), ensure_ascii=False))["engine"] == "v2"


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


def test_prompt_v2_audit_reports_duplicate_required_sections():
    from core.prompt_v2.audit import audit_prompt_plan
    from core.prompt_v2.schema import PromptPlan

    plan = PromptPlan(
        engine="v2",
        chat_type="group",
        prompt_key="chat_group",
        messages=[
            {"role": "system", "content": "<persona_reference>x</persona_reference>"},
            {"role": "system", "content": "<persona_reference>y</persona_reference>"},
            {"role": "system", "content": "[RuntimeTool]\none"},
            {"role": "system", "content": "[RuntimeTool]\ntwo"},
            {"role": "system", "content": "<user_input>bad</user_input>"},
            {"role": "user", "content": "<user_input>ok</user_input>"},
        ],
        tool_schemas=[],
        section_hashes={},
        prompt_sha256="x" * 64,
        token_estimate=1,
        warnings=[],
        debug={},
    )

    audit = audit_prompt_plan(plan)
    assert audit.ok is False
    assert any("current user input" in issue for issue in audit.issues)
    assert any("runtime_tool_prompt" in issue for issue in audit.issues)
    assert any("persona_reference" in issue for issue in audit.issues)


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
    monkeypatch.setattr("core.identity.SUPER_USER_IDS", set())

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
    assert "super_user_id: 未配置" in identity
    assert "{{" not in identity
    assert "character_name:" not in identity


def test_prompt_v2_section_variables_are_whitelisted_by_scope():
    from core.prompt_v2.variables import PromptVariableError, render_scoped_template

    rendered = render_scoped_template(
        "identity_context",
        "你叫 {{character_name}}\n{{ name_hint }}\n{{ alias_names }}\n{{super_user_id}}",
        {
            "character_name": "七濑",
            "name_hint": "小七",
            "alias_names": "小七\n七七",
            "super_user_id": "0000000000",
        },
    )
    assert rendered == "你叫 七濑\n小七\n小七\n七七\n0000000000"

    with pytest.raises(PromptVariableError):
        render_scoped_template("identity_context", "{{ user_input }}", {"user_input": "禁止"})

    with pytest.raises(PromptVariableError):
        render_scoped_template("identity_context", "{{ unknown_name }}", {})


def test_prompt_v2_templates_are_isolated_from_prompt_manager_and_legacy_runtime():
    import core.prompt_v2.compiler as compiler
    from core.prompt_v2.template_loader import load_template

    source = inspect.getsource(compiler)
    assert "core.legacy_prompt_runtime" not in source
    assert "PromptManager" not in source
    assert "prompt_assembler" not in source

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

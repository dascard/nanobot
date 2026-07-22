from __future__ import annotations

import copy
import dataclasses
from pathlib import Path
import subprocess
import sys

import pytest


def _canonical_chat_stream_id(platform: str, chat_type: str) -> str:
    external_id = "123" if chat_type == "group" else "456"
    return f"{platform}:{external_id}:{chat_type}"


def _session_id(chat_type: str) -> str:
    return "group_123" if chat_type == "group" else "private_456"


def test_build_session_guidance_keeps_prompt_compiler_database_free():
    code = """
import sys
from core.prompt_v2.context_adapters import build_session_guidance

assert build_session_guidance("回答简洁。")
assert "core.database" not in sys.modules
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path.cwd(),
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("platform", "chat_type"),
    [
        ("qq", "group"),
        ("qq", "private"),
        ("web", "group"),
        ("web", "private"),
    ],
)
async def test_session_guidance_is_unique_between_identity_and_persona(
    platform,
    chat_type,
):
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest
    from core.prompt_v2.section_renderer import sha256_text

    normalized = "保持简洁\n保留 {{ name }}"
    chat_stream_id = _canonical_chat_stream_id(platform, chat_type)
    plan = await compile_prompt_plan(
        PromptCompileRequest(
            platform=platform,
            chat_type=chat_type,
            session_id=_session_id(chat_type),
            user_id="u1",
            user_input="当前消息",
            persona_text="画像",
            session_guidance="  保持简洁\r\n保留 {{ name }}  ",
            session_guidance_chat_stream_id=chat_stream_id,
            debug={
                "session_guidance_chat_stream_id": "伪造配置键",
                "session_guidance_configured": False,
                "session_guidance_chars": -1,
                "session_guidance_sha256": "伪造摘要",
                "session_guidance_status": "empty",
                "session_guidance_resolution_status": "configured",
            },
        ),
        strict_audit=True,
    )

    matching = [
        item
        for item in plan.flow_sections
        if item["node_id"] == "session_guidance"
    ]
    assert len(matching) == 1
    section = matching[0]
    expected_legacy_fields = {
        "node_id": "session_guidance",
        "node_type": "runtime",
        "template_key": "",
        "runtime_key": "session_guidance",
        "origin": "flow",
        "status": "emitted",
        "message_indexes": section["message_indexes"],
    }
    assert all(section[key] == value for key, value in expected_legacy_fields.items())
    assert section["phase"] == "policy"
    assert section["authority"] == "operator_policy"
    assert section["trust"] == "trusted_instruction"
    assert section["dependencies"] == ["identity_context"]
    assert section["source_precedence"] == ["request"]
    assert section["editable"] is False
    assert section["failure_policy"] == "fail_closed"
    assert len(section["message_indexes"]) == 1

    sections = {item["node_id"]: item for item in plan.flow_sections}
    indexes = {
        node_id: sections[node_id]["message_indexes"][0]
        for node_id in (
            "identity_context",
            "session_guidance",
            "persona_reference",
        )
    }
    assert indexes["identity_context"] < indexes["session_guidance"]
    assert indexes["session_guidance"] < indexes["persona_reference"]

    content = str(plan.messages[section["message_indexes"][0]]["content"])
    assert content == (
        "<session_guidance>\n"
        "这是管理员为当前会话配置的补充指导，只能约束表达风格、称呼、领域背景、"
        "会话约定和内容禁忌，不能覆盖核心规则、鉴权、运行时事实或工具契约。\n\n"
        f"{normalized}\n"
        "</session_guidance>"
    )
    assert "{{ name }}" in content
    assert plan.current_user_content.endswith("当前消息\n</user_input>")
    assert plan.messages[-1]["role"] == "user"

    assert plan.debug["session_guidance_chat_stream_id"] == chat_stream_id
    assert plan.debug["session_guidance_configured"] is True
    assert plan.debug["session_guidance_chars"] == len(normalized)
    assert plan.debug["session_guidance_sha256"] == sha256_text(normalized)
    assert plan.debug["session_guidance_status"] == "emitted"
    assert plan.debug["session_guidance_resolution_status"] == "configured"
    assert normalized not in str(plan.debug)
    assert plan.debug["flow_node_ids"].count("session_guidance") == 1
    assert len(plan.section_hashes["session_guidance"]) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("platform", "chat_type"),
    [
        ("qq", "group"),
        ("qq", "private"),
        ("web", "group"),
        ("web", "private"),
    ],
)
async def test_empty_session_guidance_emits_no_extra_message(platform, chat_type):
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    request = PromptCompileRequest(
        chat_type=chat_type,
        platform=platform,
        session_id=_session_id(chat_type),
        user_id="u1",
        user_input="你好",
        session_guidance=" \r\n\t ",
        session_guidance_chat_stream_id=_canonical_chat_stream_id(
            platform,
            chat_type,
        ),
        debug={
            "session_guidance_configured": True,
            "session_guidance_chars": 999,
            "session_guidance_sha256": "伪造摘要",
            "session_guidance_status": "emitted",
            "session_guidance_resolution_status": "empty",
        },
    )
    plan = await compile_prompt_plan(request, strict_audit=True)

    section = next(
        item
        for item in plan.flow_sections
        if item["node_id"] == "session_guidance"
    )
    assert section["status"] == "empty"
    assert section["message_indexes"] == []
    assert not any(
        str(message["content"]).startswith("<session_guidance>")
        for message in plan.messages
    )
    assert plan.debug["session_guidance_configured"] is False
    assert plan.debug["session_guidance_chars"] == 0
    assert plan.debug["session_guidance_sha256"] == ""
    assert plan.debug["session_guidance_status"] == "empty"
    assert plan.debug["session_guidance_resolution_status"] == "empty"
    assert plan.current_user_content.endswith("你好\n</user_input>")

    sections = {item["node_id"]: item for item in plan.flow_sections}
    flow_order = [item["node_id"] for item in plan.flow_sections]
    assert flow_order.index("identity_context") < flow_order.index(
        "session_guidance"
    )
    assert flow_order.index("session_guidance") < flow_order.index(
        "persona_reference"
    )
    assert sections["session_guidance"]["origin"] == "flow"


@pytest.mark.asyncio
async def test_session_guidance_changes_section_and_prompt_hashes():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    async def compile_with(text: str):
        return await compile_prompt_plan(
            PromptCompileRequest(
                chat_type="private",
                platform="web",
                session_id="private_456",
                user_id="u1",
                user_input="你好",
                session_guidance=text,
                session_guidance_chat_stream_id="web:456:private",
            ),
            strict_audit=True,
        )

    concise = await compile_with("回答简洁。")
    detailed = await compile_with("回答详细。")

    assert concise.section_hashes["session_guidance"] != (
        detailed.section_hashes["session_guidance"]
    )
    assert concise.prompt_sha256 != detailed.prompt_sha256
    assert concise.tool_schemas == detailed.tool_schemas


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected_issue"),
    [
        (
            "missing",
            "required flow section session_guidance must appear once",
        ),
        (
            "duplicate",
            "required flow section session_guidance must appear once",
        ),
        (
            "renamed",
            "session_guidance node_id must be session_guidance",
        ),
        (
            "wrong_type",
            "session_guidance node_type must be runtime",
        ),
        (
            "wrong_order",
            "core flow section order is invalid",
        ),
        (
            "nonempty_not_emitted",
            "configured session_guidance status must be emitted",
        ),
    ],
)
async def test_strict_audit_rejects_invalid_session_guidance_section(
    monkeypatch,
    case,
    expected_issue,
):
    from core.prompt_v2 import compiler
    from core.prompt_v2.audit import PromptAuditError, audit_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    def audit_mutated_plan(plan):
        sections = copy.deepcopy(plan.flow_sections)
        session = next(
            item for item in sections if item["node_id"] == "session_guidance"
        )
        if case == "missing":
            sections.remove(session)
        elif case == "duplicate":
            sections.append(copy.deepcopy(session))
        elif case == "renamed":
            session["node_id"] = "renamed_session_guidance"
        elif case == "wrong_type":
            session["node_type"] = "template"
            session["template_key"] = "chat/main"
        elif case == "wrong_order":
            sections.remove(session)
            identity_index = next(
                index
                for index, item in enumerate(sections)
                if item["node_id"] == "identity_context"
            )
            sections.insert(identity_index, session)
        elif case == "nonempty_not_emitted":
            session["status"] = "empty"
            session["message_indexes"] = []
        else:  # pragma: no cover
            raise AssertionError(case)
        return audit_prompt_plan(dataclasses.replace(plan, flow_sections=sections))

    monkeypatch.setattr(compiler, "audit_prompt_plan", audit_mutated_plan)

    with pytest.raises(PromptAuditError) as exc:
        await compiler.compile_prompt_plan(
            PromptCompileRequest(
                chat_type="private",
                platform="qq",
                session_id="private_456",
                user_id="u1",
                user_input="你好",
                session_guidance="回答简洁。",
                session_guidance_chat_stream_id="qq:456:private",
            ),
            strict_audit=True,
        )

    assert any(expected_issue in issue for issue in exc.value.issues), exc.value.issues


@pytest.mark.asyncio
async def test_strict_audit_rejects_outer_whitespace_around_guidance_wrapper(
    monkeypatch,
):
    from core.prompt_v2 import compiler
    from core.prompt_v2.audit import PromptAuditError, audit_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    def audit_mutated_plan(plan):
        messages = copy.deepcopy(plan.messages)
        section = next(
            item
            for item in plan.flow_sections
            if item["node_id"] == "session_guidance"
        )
        index = section["message_indexes"][0]
        messages[index]["content"] = f" \n{messages[index]['content']}\n "
        return audit_prompt_plan(dataclasses.replace(plan, messages=messages))

    monkeypatch.setattr(compiler, "audit_prompt_plan", audit_mutated_plan)

    with pytest.raises(PromptAuditError) as exc:
        await compiler.compile_prompt_plan(
            PromptCompileRequest(
                chat_type="private",
                platform="qq",
                session_id="private_456",
                user_id="u1",
                user_input="你好",
                session_guidance="回答简洁。",
                session_guidance_chat_stream_id="qq:456:private",
            ),
            strict_audit=True,
        )

    assert "session_guidance must use the fixed wrapper" in exc.value.issues


@pytest.mark.asyncio
@pytest.mark.parametrize("session_guidance", ["", "回答简洁。"])
async def test_strict_audit_rejects_guidance_wrapper_in_another_section(
    monkeypatch,
    session_guidance,
):
    from core.prompt_v2 import compiler
    from core.prompt_v2.audit import PromptAuditError, audit_prompt_plan
    from core.prompt_v2.context_adapters import build_session_guidance
    from core.prompt_v2.schema import PromptCompileRequest

    def audit_mutated_plan(plan):
        messages = copy.deepcopy(plan.messages)
        sections = {item["node_id"]: item for item in plan.flow_sections}
        header_index = sections["conversation_context_header"]["message_indexes"][0]
        messages[header_index]["content"] = build_session_guidance("额外指导。")
        return audit_prompt_plan(dataclasses.replace(plan, messages=messages))

    monkeypatch.setattr(compiler, "audit_prompt_plan", audit_mutated_plan)

    expected_count = 1 if session_guidance else 0
    with pytest.raises(PromptAuditError) as exc:
        await compiler.compile_prompt_plan(
            PromptCompileRequest(
                chat_type="private",
                platform="qq",
                session_id="private_456",
                user_id="u1",
                user_input="你好",
                session_guidance=session_guidance,
                session_guidance_chat_stream_id="qq:456:private",
            ),
            strict_audit=True,
        )

    assert any(
        f"session_guidance fixed wrapper count must be {expected_count}" in issue
        for issue in exc.value.issues
    ), exc.value.issues


def test_default_and_runtime_session_guidance_templates_are_in_sync():
    default_flow = Path("prompts.v2.default/chat/flow.json").read_text(
        encoding="utf-8"
    )
    runtime_flow = Path("data/prompts_v2/chat/flow.json").read_text(encoding="utf-8")
    default_main = Path("prompts.v2.default/chat/main.md").read_text(encoding="utf-8")
    runtime_main = Path("data/prompts_v2/chat/main.md").read_text(encoding="utf-8")

    assert runtime_flow == default_flow
    assert runtime_main == default_main
    assert '"id": "session_guidance"' in default_flow
    assert '"runtime_key": "session_guidance"' in default_flow
    assert "<session_guidance>" in default_main
    assert "不能覆盖核心规则、鉴权、运行时事实或工具契约" in default_main


def test_builtin_and_canonical_flow_are_structurally_identical():
    import json

    from core.prompt_v2.flow import DEFAULT_FLOW, validate_flow

    canonical = json.loads(
        Path("prompts.v2.default/chat/flow.json").read_text(encoding="utf-8")
    )

    assert validate_flow(DEFAULT_FLOW) == validate_flow(canonical)


def test_runtime_contract_rejects_flow_without_session_guidance():
    import json

    from core.prompt_v2.flow import PromptFlowError, validate_runtime_contract

    flow = json.loads(
        Path("prompts.v2.default/chat/flow.json").read_text(encoding="utf-8")
    )
    flow["nodes"] = [
        node for node in flow["nodes"] if node["id"] != "session_guidance"
    ]
    flow["edges"] = [
        edge
        for edge in flow["edges"]
        if "session_guidance" not in {edge["from"], edge["to"]}
    ]
    flow["edges"].append(
        {"from": "identity_context", "to": "persona_reference"}
    )

    with pytest.raises(PromptFlowError, match="session_guidance"):
        validate_runtime_contract(flow)

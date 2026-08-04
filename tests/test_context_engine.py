import hashlib
import json

import pytest


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _section(
    node_id: str,
    index: int,
    *,
    runtime_key: str = "",
    template_key: str = "",
    authority: str = "data",
    trust: str = "untrusted_data",
) -> dict:
    return {
        "node_id": node_id,
        "node_type": "runtime" if runtime_key else "template",
        "runtime_key": runtime_key,
        "template_key": template_key,
        "origin": "flow",
        "status": "emitted",
        "message_indexes": [index],
        "active_source": "request" if runtime_key else "default",
        "authority": authority,
        "trust": trust,
    }


def test_context_manifest_separates_layers_scopes_and_provenance():
    from core.context_engine import (
        ContextLayer,
        ContextProvenance,
        ContextScope,
        build_prompt_context_manifest,
        validate_context_manifest,
    )

    messages = [
        {"role": "system", "content": "安全合同"},
        {"role": "system", "content": "稳定策略"},
        {"role": "system", "content": "身份"},
        {"role": "system", "content": "会话指导"},
        {"role": "user", "content": "群记忆"},
        {"role": "user", "content": "项目资料"},
        {"role": "user", "content": "累计摘要"},
        {"role": "system", "content": "历史说明"},
        {"role": "user", "content": "最近消息"},
        {"role": "user", "content": "用户画像"},
        {"role": "system", "content": "请求事实"},
        {"role": "user", "content": "当前问题"},
    ]
    sections = [
        _section(
            "base_contract",
            0,
            template_key="chat/main",
            authority="operator_policy",
            trust="trusted_instruction",
        ),
        _section(
            "group_policy",
            1,
            template_key="chat/branch_group",
            authority="application_policy",
            trust="trusted_instruction",
        ),
        _section(
            "identity_context",
            2,
            template_key="chat/identity_context",
            authority="application_policy",
            trust="trusted_instruction",
        ),
        _section(
            "session_guidance",
            3,
            runtime_key="session_guidance",
            authority="operator_policy",
            trust="trusted_instruction",
        ),
        _section("group_context", 4, runtime_key="group_context"),
        _section("project_context", 5, runtime_key="project_context"),
        _section("summary_context", 6, runtime_key="summary_context"),
        _section(
            "conversation_context_header",
            7,
            runtime_key="conversation_context_header",
            trust="trusted_data",
        ),
        _section("history_messages", 8, runtime_key="history_messages"),
        _section("persona_reference", 9, runtime_key="persona_reference"),
        _section(
            "runtime_context",
            10,
            runtime_key="runtime_context",
            trust="trusted_data",
        ),
        _section(
            "current_user_event",
            11,
            runtime_key="current_user_event",
            authority="user",
            trust="untrusted_instruction",
        ),
    ]
    manifest = build_prompt_context_manifest(
        messages=messages,
        tool_schemas=[{
            "type": "function",
            "function": {
                "name": "reply",
                "description": "发送回复",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
        flow_sections=sections,
        section_hashes={
            section["node_id"]: _digest(section["node_id"])
            for section in sections
        },
        request_prompt_sha256=_digest("request"),
        chat_type="group",
        provenance={
            "history_messages": ContextProvenance(
                "chat_log",
                ("chat_log:41",),
            ),
            "summary_context": ContextProvenance(
                "rolling_summary",
                ("summary:7",),
            ),
        },
    )

    payload = manifest.to_dict()
    validate_context_manifest(payload)
    by_id = {entry["entry_id"]: entry for entry in payload["entries"]}
    assert by_id["base_contract"]["layer"] == ContextLayer.SECURITY_POLICY.value
    assert by_id["identity_context"]["layer"] == ContextLayer.STABLE_SYSTEM.value
    assert by_id["identity_context"]["scope"] == ContextScope.USER.value
    assert by_id["session_guidance"]["scope"] == ContextScope.SESSION.value
    assert by_id["persona_reference"]["scope"] == ContextScope.USER.value
    assert by_id["group_context"]["scope"] == ContextScope.GROUP.value
    assert by_id["project_context"]["scope"] == ContextScope.PROJECT.value
    assert by_id["summary_context"]["layer"] == ContextLayer.SUMMARY.value
    assert by_id["history_messages"]["layer"] == (
        ContextLayer.RECENT_CONVERSATION.value
    )
    assert by_id["tool_schemas"]["layer"] == ContextLayer.TOOL_CONTRACT.value
    assert by_id["summary_context"]["source_refs"] == ["summary:7"]
    assert by_id["history_messages"]["source_refs"] == ["chat_log:41"]
    tool_result_budget = next(
        item
        for item in payload["layer_budgets"]
        if item["layer"] == ContextLayer.TOOL_RESULT.value
    )
    assert tool_result_budget["used_tokens"] == 0
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "累计摘要" not in serialized
    assert "最近消息" not in serialized
    assert "项目资料" not in serialized


def test_context_budget_is_chat_type_specific_and_fails_before_model_call():
    from core.context_engine import (
        ContextBudgetExceededError,
        ContextLayer,
        build_prompt_context_manifest,
    )

    messages = [{"role": "user", "content": "长" * 9_000}]
    sections = [
        _section("history_messages", 0, runtime_key="history_messages"),
    ]
    kwargs = {
        "messages": messages,
        "tool_schemas": [],
        "flow_sections": sections,
        "section_hashes": {"history_messages": _digest("history")},
        "request_prompt_sha256": _digest("request"),
    }

    with pytest.raises(ContextBudgetExceededError) as exc_info:
        build_prompt_context_manifest(chat_type="private", **kwargs)
    assert exc_info.value.layer is ContextLayer.RECENT_CONVERSATION
    assert exc_info.value.max_tokens == 8_000

    group_manifest = build_prompt_context_manifest(
        chat_type="group",
        **kwargs,
    )
    recent_budget = next(
        budget
        for budget in group_manifest.layer_budgets
        if budget.layer is ContextLayer.RECENT_CONVERSATION
    )
    assert recent_budget.max_tokens == 24_000
    assert 8_000 < recent_budget.used_tokens <= 24_000


def test_context_manifest_rejects_tampered_usage_and_digest():
    from core.context_engine import (
        ContextManifestError,
        build_prompt_context_manifest,
        validate_context_manifest,
    )

    manifest = build_prompt_context_manifest(
        messages=[{"role": "user", "content": "问题"}],
        tool_schemas=[],
        flow_sections=[
            _section(
                "current_user_event",
                0,
                runtime_key="current_user_event",
            )
        ],
        section_hashes={"current_user_event": _digest("current")},
        request_prompt_sha256=_digest("request"),
        chat_type="private",
    ).to_dict()
    manifest["total_tokens"] += 1

    with pytest.raises(ContextManifestError, match="sha256"):
        validate_context_manifest(manifest)

    from core.prompt_v2.section_renderer import sha256_text, stable_json

    manifest["sha256"] = sha256_text(stable_json({
        key: value
        for key, value in manifest.items()
        if key != "sha256"
    }))
    with pytest.raises(ContextManifestError, match="total_tokens"):
        validate_context_manifest(manifest)


def test_context_manifest_rejects_non_hex_content_digest_even_when_resigned():
    from core.context_engine import (
        ContextManifestError,
        build_prompt_context_manifest,
        validate_context_manifest,
    )
    from core.prompt_v2.section_renderer import sha256_text, stable_json

    manifest = build_prompt_context_manifest(
        messages=[{"role": "user", "content": "问题"}],
        tool_schemas=[],
        flow_sections=[
            _section(
                "current_user_event",
                0,
                runtime_key="current_user_event",
            )
        ],
        section_hashes={"current_user_event": _digest("current")},
        request_prompt_sha256=_digest("request"),
        chat_type="private",
    ).to_dict()
    manifest["entries"][0]["content_sha256"] = "z" * 64
    manifest["sha256"] = sha256_text(stable_json({
        key: value
        for key, value in manifest.items()
        if key != "sha256"
    }))

    with pytest.raises(ContextManifestError, match="entry 摘要"):
        validate_context_manifest(manifest)


@pytest.mark.asyncio
async def test_canonical_compiler_emits_separate_context_layers():
    from core.context_builder import build_conversation_context_header
    from core.context_engine import ContextLayer, ContextScope
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    summary = (
        '<rolling_session_summary source_type="conversation_turn">\n'
        "更早摘要\n"
        "</rolling_session_summary>"
    )
    plan = await compile_prompt_plan(PromptCompileRequest(
        chat_type="private",
        platform="qq",
        session_id="private-u1",
        user_id="u1",
        current_message_id="m-current",
        user_input="现在的问题",
        persona_text="偏好简洁",
        session_guidance="称呼我为朋友",
        session_guidance_chat_stream_id="qq:private:u1",
        project_context="只读项目事实",
        summary_context=summary,
        history_header=build_conversation_context_header(is_group=False),
        history_messages=[
            {"role": "user", "content": "上一问"},
            {"role": "assistant", "content": "上一答"},
        ],
        tool_schemas=[{
            "type": "function",
            "function": {
                "name": "reply",
                "description": "发送回复",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
        debug={
            "context_debug": {
                "rolling_summary_id": 9,
                "rolling_summary_recent_raw_turn_ids": [11, 12],
                "persona_fact_ids": [3],
            }
        },
    ))

    by_id = {
        entry["entry_id"]: entry
        for entry in plan.context_manifest["entries"]
    }
    assert by_id["summary_context"]["layer"] == ContextLayer.SUMMARY.value
    assert by_id["summary_context"]["source_refs"] == ["summary:9"]
    assert by_id["project_context"]["scope"] == ContextScope.PROJECT.value
    assert by_id["persona_reference"]["scope"] == ContextScope.USER.value
    assert by_id["history_messages"]["source_refs"] == [
        "conversation_turn:11",
        "conversation_turn:12",
    ]
    summary_indexes = by_id["summary_context"]["message_indexes"]
    header_indexes = by_id["conversation_context_header"]["message_indexes"]
    assert summary_indexes != header_indexes
    assert "更早摘要" in plan.messages[summary_indexes[0]]["content"]
    assert "更早摘要" not in plan.messages[header_indexes[0]]["content"]
    assert plan.context_manifest["request_prompt_sha256"] == plan.prompt_sha256


def test_structured_context_keeps_summary_out_of_recent_conversation_header(
    db_session,
):
    from core.context_builder import build_structured_chat_context
    from core.database import ConversationTurn, RollingSessionSummary, User

    db_session.add(User(id="context-u"))
    first = ConversationTurn(
        user_id="context-u",
        session_id="context-s",
        role="user",
        content="已摘要的旧消息",
    )
    db_session.add(first)
    db_session.flush()
    db_session.add(RollingSessionSummary(
        session_id="context-s",
        user_id="context-u",
        chat_type="private",
        status="active",
        summary_kind="deterministic_fallback",
        summary_text="独立累计摘要",
        covered_from_turn_id=first.id,
        covered_until_turn_id=first.id,
        source_turn_ids_json=json.dumps([first.id]),
        source_turn_count=1,
    ))
    recent = ConversationTurn(
        user_id="context-u",
        session_id="context-s",
        role="user",
        content="最近原文",
    )
    db_session.add(recent)
    db_session.commit()

    result = build_structured_chat_context(
        db_session,
        "context-s",
        user_id="context-u",
        read_only=True,
    )

    assert "独立累计摘要" in result.summary_context
    assert "独立累计摘要" not in result.conversation_context_header
    assert [item["turn_id"] for item in result.recent_messages] == [recent.id]
    legacy_header, legacy_messages, legacy_debug = result.legacy_tuple()
    assert "独立累计摘要" in legacy_header
    assert legacy_messages[0]["turn_id"] == recent.id
    assert legacy_debug["rolling_summary_id"] > 0

from __future__ import annotations

from typing import Any

from core.prompt_v2.audit import audit_prompt_plan
from core.prompt_v2.context_adapters import (
    build_identity_context,
    build_persona_reference,
    build_runtime_context,
    combine_group_context_sections,
    ensure_user_input_block,
    jsonable,
)
from core.prompt_v2.schema import PromptCompileRequest, PromptPlan
from core.prompt_v2.section_renderer import (
    estimate_tokens,
    hash_section,
    sha256_text,
    stable_json,
    system_message,
)
from core.prompt_v2.template_loader import load_template


def _split_policy_sections(template_body: str, chat_type: str) -> tuple[str, str]:
    marker = "## 群聊行为" if chat_type == "group" else "## 私聊行为"
    idx = template_body.find(marker)
    if idx < 0:
        return template_body.strip(), ""
    return template_body[:idx].strip(), template_body[idx:].strip()


def _clean_runtime_tool_prompt(text: str) -> str:
    value = str(text or "").strip()
    if value:
        return value
    return "[RuntimeTool]\n本轮真实可调用工具以 API tools schema 为准。如需回复必须调用 reply(content)，不回复则调用 no_reply(reason)。"


def _extract_marked_sections(text: str, start: str, end: str) -> tuple[list[str], str]:
    sections: list[str] = []
    rest = str(text or "")
    while True:
        start_idx = rest.find(start)
        if start_idx < 0:
            break
        end_idx = rest.find(end, start_idx + len(start))
        if end_idx < 0:
            break
        section_end = end_idx + len(end)
        sections.append(rest[start_idx:section_end].strip())
        rest = (rest[:start_idx] + rest[section_end:]).strip()
    return sections, rest


async def compile_prompt_plan(request: PromptCompileRequest | dict[str, Any]) -> PromptPlan:
    if isinstance(request, dict):
        request = PromptCompileRequest(**request)

    chat_type = request.normalized_chat_type
    prompt_key = request.normalized_prompt_key
    template = load_template(prompt_key)
    base_contract, chat_policy = _split_policy_sections(template.body, chat_type)

    runtime_context = build_runtime_context(request)
    identity_context = build_identity_context(request)
    persona_reference = build_persona_reference(request.user_id, request.persona_text)
    history_header = str(request.history_header or "").strip()
    group_profile_sections: list[str] = []
    if chat_type == "group":
        group_profile_sections, history_header = _extract_marked_sections(
            history_header,
            "[GroupProfileContext]",
            "[/GroupProfileContext]",
        )
    history_messages = request.normalized_history_messages()
    group_context = ""
    if chat_type == "group":
        group_context = combine_group_context_sections(
            "\n\n".join(group_profile_sections),
            request.group_profile_context,
            request.expression_context,
            request.jargon_context,
        )
    runtime_tool_prompt = _clean_runtime_tool_prompt(request.runtime_tool_prompt)
    current_user = ensure_user_input_block(request.user_input)

    section_hashes: dict[str, str] = {}
    for name, content in [
        ("base_contract", base_contract),
        ("chat_policy", chat_policy),
        ("runtime_context", runtime_context),
        ("identity_context", identity_context),
        ("persona_reference", persona_reference),
        ("conversation_context_header", history_header),
        ("history_messages", history_messages),
        ("group_context", group_context),
        ("runtime_tool_prompt", runtime_tool_prompt),
        ("current_user_event", current_user),
    ]:
        hash_section(section_hashes, name, content)

    messages: list[dict[str, Any]] = [
        system_message(base_contract),
        system_message(chat_policy),
        system_message(runtime_context),
        system_message(identity_context),
        system_message(persona_reference),
    ]
    if history_header:
        messages.append(system_message(history_header))
    else:
        messages.append(system_message(
            "<conversation_context>\n本轮没有可注入的历史上下文。\n</conversation_context>"
        ))
    messages.extend(history_messages)
    if group_context:
        messages.append(system_message(group_context))
    if request.effort_constraint:
        messages.append(system_message(request.effort_constraint))
    messages.append(system_message(runtime_tool_prompt))
    messages.append({"role": "user", "content": current_user})

    token_estimate = sum(estimate_tokens(str(m.get("content") or "")) for m in messages)
    prompt_sha = sha256_text(stable_json({"messages": messages, "tools": request.tool_schemas}))
    debug = {
        "template_path": str(template.path),
        "history_message_count": len(history_messages),
        "has_group_context": bool(group_context),
        "tool_schema_count": len(request.tool_schemas or []),
        **dict(request.debug or {}),
    }
    plan = PromptPlan(
        engine="v2",
        chat_type=chat_type,
        prompt_key=prompt_key,
        messages=messages,
        tool_schemas=list(request.tool_schemas or []),
        section_hashes=section_hashes,
        prompt_sha256=prompt_sha,
        token_estimate=token_estimate,
        warnings=[],
        debug=jsonable(debug),
    )
    audit = audit_prompt_plan(plan)
    if audit.ok:
        return plan
    return PromptPlan(
        engine=plan.engine,
        chat_type=plan.chat_type,
        prompt_key=plan.prompt_key,
        messages=plan.messages,
        tool_schemas=plan.tool_schemas,
        section_hashes=plan.section_hashes,
        prompt_sha256=plan.prompt_sha256,
        token_estimate=plan.token_estimate,
        warnings=list(plan.warnings) + audit.issues,
        debug=plan.debug,
    )

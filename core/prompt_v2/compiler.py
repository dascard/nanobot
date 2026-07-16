from __future__ import annotations

from typing import Any

from core.prompt_v2.audit import PromptAuditError, audit_prompt_plan
from core.prompt_v2.context_adapters import (
    build_current_user_event,
    build_persona_reference,
    build_runtime_context,
    build_session_guidance,
    build_template_values,
    combine_group_context_sections,
    jsonable,
)
from core.prompt_v2.flow import load_flow, ordered_nodes_for_chat
from core.prompt_v2.flow_storage import template_governance_read_lock
from core.prompt_v2.request_metrics import calculate_request_metrics
from core.prompt_v2.schema import (
    PromptCompileRequest,
    PromptFlowOrigin,
    PromptFlowSection,
    PromptFlowStatus,
    PromptPlan,
)
from core.prompt_v2.section_renderer import (
    hash_section,
    sha256_text,
    system_message,
)
from core.prompt_v2.template_loader import load_template
from core.prompt_v2.variables import render_scoped_template
from core.session_guidance import normalize_session_guidance


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


async def compile_prompt_plan(
    request: PromptCompileRequest | dict[str, Any],
    *,
    strict_audit: bool = True,
) -> PromptPlan:
    from core.prompt_v2.template_registry import runtime_template_dir

    with template_governance_read_lock(runtime_template_dir()):
        return await _compile_prompt_plan_locked(
            request,
            strict_audit=strict_audit,
        )


async def _compile_prompt_plan_locked(
    request: PromptCompileRequest | dict[str, Any],
    *,
    strict_audit: bool = True,
) -> PromptPlan:
    if isinstance(request, dict):
        request = PromptCompileRequest(**request)

    chat_type = request.normalized_chat_type
    platform = request.normalized_platform
    prompt_key = request.normalized_prompt_key

    template_values = build_template_values(request)
    runtime_context = build_runtime_context(request, current_time=template_values["current_time"])
    normalized_session_guidance = normalize_session_guidance(request.session_guidance)
    session_guidance = build_session_guidance(normalized_session_guidance)
    persona_reference = build_persona_reference(request.user_id, request.persona_text)
    history_header = str(request.history_header or "").strip()
    group_profile_sections: list[str] = []
    if chat_type == "group":
        legacy_sections, history_header = _extract_marked_sections(
            history_header,
            "[GroupProfileContext]",
            "[/GroupProfileContext]",
        )
        memory_sections, history_header = _extract_marked_sections(
            history_header,
            "<group_memory_context",
            "</group_memory_context>",
        )
        group_profile_sections = [*legacy_sections, *memory_sections]
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
    current_user = build_current_user_event(request)
    flow_state = load_flow()
    ordered_nodes = ordered_nodes_for_chat(flow_state.flow, chat_type, platform=platform)
    flow_sections: list[PromptFlowSection] = []
    warnings: list[str] = []

    runtime_sections: dict[str, Any] = {
        "runtime_context": runtime_context,
        "session_guidance": session_guidance,
        "persona_reference": persona_reference,
        "conversation_context_header": history_header or (
            "<conversation_context>\n本轮没有可注入的历史上下文。\n</conversation_context>"
        ),
        "history_messages": history_messages,
        "group_context": group_context,
        "effort_constraint": request.effort_constraint,
        "runtime_tool_prompt": runtime_tool_prompt,
        "current_user_event": current_user,
    }

    section_hashes: dict[str, str] = {}
    messages: list[dict[str, Any]] = []
    template_paths: dict[str, str] = {}
    template_resolutions: dict[str, dict[str, Any]] = {}
    seen_current_user = False
    seen_runtime_keys: set[str] = set()
    singleton_runtime_keys = {
        "session_guidance",
        "persona_reference",
        "runtime_tool_prompt",
        "current_user_event",
    }
    current_user_flow_section: PromptFlowSection | None = None

    def append_system(section_id: str, content: Any) -> list[int]:
        text = str(content or "").strip()
        hash_section(section_hashes, section_id, content)
        if text:
            message_index = len(messages)
            messages.append(system_message(text))
            return [message_index]
        return []

    def section_metadata(
        *,
        node_id: str,
        node_type: str,
        template_key: str = "",
        runtime_key: str = "",
        origin: PromptFlowOrigin = "flow",
        status: PromptFlowStatus,
        message_indexes: list[int] | None = None,
    ) -> PromptFlowSection:
        return {
            "node_id": node_id,
            "node_type": node_type,
            "template_key": template_key,
            "runtime_key": runtime_key,
            "origin": origin,
            "status": status,
            "message_indexes": list(message_indexes or []),
        }

    for node in ordered_nodes:
        node_id = str(node.get("id") or "").strip()
        node_type = str(node.get("type") or "").strip()
        if node_type == "template":
            template_key = str(node.get("template_key") or "").strip()
            try:
                template = load_template(template_key)
            except FileNotFoundError:
                warnings.append(f"template node {node_id} missing template: {template_key}")
                hash_section(section_hashes, node_id, "")
                flow_sections.append(
                    section_metadata(
                        node_id=node_id,
                        node_type=node_type,
                        template_key=template_key,
                        status="missing_template",
                    )
                )
                continue
            rendered = render_scoped_template(template_key, template.body, template_values).strip()
            template_paths[node_id] = str(template.path)
            if template.resolution is None:
                raise RuntimeError(f"Prompt 模板缺少来源解析记录: {template_key}")
            template_resolutions[node_id] = template.resolution.to_dict()
            message_indexes = append_system(node_id, rendered)
            flow_sections.append(
                section_metadata(
                    node_id=node_id,
                    node_type=node_type,
                    template_key=template_key,
                    status="emitted" if message_indexes else "empty",
                    message_indexes=message_indexes,
                )
            )
            if node_id in {"group_policy", "private_policy"}:
                hash_section(section_hashes, "chat_policy", rendered)
            continue

        runtime_key = str(node.get("runtime_key") or "").strip()
        content = runtime_sections.get(runtime_key, "")
        if runtime_key in singleton_runtime_keys and runtime_key in seen_runtime_keys:
            warnings.append(f"flow duplicated singleton runtime node {runtime_key}; skipped node {node_id}")
            hash_section(section_hashes, node_id, content)
            flow_sections.append(
                section_metadata(
                    node_id=node_id,
                    node_type=node_type,
                    runtime_key=runtime_key,
                    status="skipped_duplicate",
                )
            )
            continue
        seen_runtime_keys.add(runtime_key)
        if runtime_key == "history_messages":
            hash_section(section_hashes, node_id, content)
            start_index = len(messages)
            messages.extend(history_messages)
            message_indexes = list(range(start_index, len(messages)))
        elif runtime_key == "current_user_event":
            seen_current_user = True
            hash_section(section_hashes, node_id, content)
            message_indexes = []
        else:
            message_indexes = append_system(node_id, content)
        section = section_metadata(
            node_id=node_id,
            node_type=node_type,
            runtime_key=runtime_key,
            status=(
                "emitted"
                if message_indexes or runtime_key == "current_user_event"
                else "empty"
            ),
            message_indexes=message_indexes,
        )
        flow_sections.append(section)
        if runtime_key == "current_user_event":
            current_user_flow_section = section

    if "base_contract" not in section_hashes:
        hash_section(section_hashes, "base_contract", "")
    if "persona_reference" not in seen_runtime_keys:
        message_indexes = append_system("persona_reference", persona_reference)
        flow_sections.append(
            section_metadata(
                node_id="persona_reference",
                node_type="runtime",
                runtime_key="persona_reference",
                origin="fallback",
                status="emitted" if message_indexes else "empty",
                message_indexes=message_indexes,
            )
        )
        warnings.append("flow missing persona_reference; compiler appended singleton runtime section")
    if "runtime_tool_prompt" not in seen_runtime_keys:
        message_indexes = append_system("runtime_tool_prompt", runtime_tool_prompt)
        flow_sections.append(
            section_metadata(
                node_id="runtime_tool_prompt",
                node_type="runtime",
                runtime_key="runtime_tool_prompt",
                origin="fallback",
                status="emitted" if message_indexes else "empty",
                message_indexes=message_indexes,
            )
        )
        warnings.append("flow missing runtime_tool_prompt; compiler appended singleton runtime section")
    if "current_user_event" not in section_hashes:
        hash_section(section_hashes, "current_user_event", current_user)
    if not seen_current_user:
        warnings.append("flow missing current_user_event; compiler appended user event at tail")
    messages.append({"role": "user", "content": current_user})
    current_user_index = len(messages) - 1
    if current_user_flow_section is not None:
        current_user_flow_section["message_indexes"] = [current_user_index]
    else:
        flow_sections.append(
            section_metadata(
                node_id="current_user_event",
                node_type="runtime",
                runtime_key="current_user_event",
                origin="fallback",
                status="emitted",
                message_indexes=[current_user_index],
            )
        )

    metrics = calculate_request_metrics(
        messages=messages,
        tools=list(request.tool_schemas or []),
    )
    session_guidance_configured = bool(normalized_session_guidance)
    session_guidance_status = "emitted" if session_guidance_configured else "empty"
    debug = {
        **dict(request.debug or {}),
        "template_path": next(iter(template_paths.values()), ""),
        "template_paths": template_paths,
        "flow_path": str(flow_state.path),
        "flow_source": flow_state.source,
        "flow_node_count": len(ordered_nodes),
        "flow_entry_node_id": str(ordered_nodes[0].get("id") or "") if ordered_nodes else "",
        "flow_node_ids": [str(node.get("id") or "") for node in ordered_nodes],
        "platform": platform,
        "history_message_count": len(history_messages),
        "has_group_context": bool(group_context),
        "tool_schema_count": len(request.tool_schemas or []),
        "template_resolutions": template_resolutions,
        "request_prompt_sha256": metrics.prompt_sha256,
        "session_guidance_chat_stream_id": str(
            request.session_guidance_chat_stream_id or ""
        ).strip(),
        "session_guidance_configured": session_guidance_configured,
        "session_guidance_chars": len(normalized_session_guidance),
        "session_guidance_sha256": (
            sha256_text(normalized_session_guidance)
            if session_guidance_configured
            else ""
        ),
        "session_guidance_status": session_guidance_status,
        "message_token_estimate": metrics.message_token_estimate,
        "tool_schema_token_estimate": metrics.tool_schema_token_estimate,
        "token_estimate": metrics.token_estimate,
    }
    plan = PromptPlan(
        engine="prompt",
        chat_type=chat_type,
        prompt_key=prompt_key,
        messages=messages,
        tool_schemas=list(request.tool_schemas or []),
        section_hashes=section_hashes,
        prompt_sha256=metrics.prompt_sha256,
        token_estimate=metrics.token_estimate,
        warnings=warnings,
        debug=jsonable(debug),
        platform=platform,
        flow_sections=flow_sections,
        message_token_estimate=metrics.message_token_estimate,
        tool_schema_token_estimate=metrics.tool_schema_token_estimate,
    )
    audit = audit_prompt_plan(plan)
    if audit.ok:
        return plan
    if strict_audit:
        raise PromptAuditError(audit.issues, plan=plan)
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
        platform=plan.platform,
        flow_sections=plan.flow_sections,
        message_token_estimate=plan.message_token_estimate,
        tool_schema_token_estimate=plan.tool_schema_token_estimate,
    )

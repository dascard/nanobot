from __future__ import annotations

import json
import time
from types import MappingProxyType
from typing import Any

from core.prompt_v2.audit import PromptAuditError, audit_prompt_plan
from core.prompt_v2.contribution_registry import (
    PROMPT_CONTRIBUTION_REGISTRY,
    PromptContributionDescriptor,
    PromptContributionRenderContext,
    PromptContributionRenderResult,
    PromptContributionRendererPort,
    contribution_for_node,
    render_prompt_contribution,
    require_prompt_renderer,
    resolve_prompt_contributions,
    validate_prompt_contribution_inputs,
)
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
from core.prompt_v2.section_descriptors import (
    PromptSectionDescriptor,
    descriptor_for_node,
    validate_descriptor_source,
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


class _TemplateContributionRenderer:
    """把现有模板加载器适配到稳定 Renderer Port。"""

    @property
    def renderer_id(self) -> str:
        return "template"

    def render(
        self,
        context: PromptContributionRenderContext,
    ) -> PromptContributionRenderResult:
        template_key = str(context.node.get("template_key") or "").strip()
        template = load_template(template_key)
        rendered = render_scoped_template(
            template_key,
            template.body,
            dict(context.template_values),
        ).strip()
        if template.resolution is None:
            raise RuntimeError(f"Prompt 模板缺少来源解析记录: {template_key}")
        return PromptContributionRenderResult(
            content=rendered,
            template_path=str(template.path),
            active_source=template.resolution.active_source,
            template_resolution=template.resolution.to_dict(),
        )


class _RuntimeContributionRenderer:
    """把请求级 runtime section 适配到稳定 Renderer Port。"""

    @property
    def renderer_id(self) -> str:
        return "runtime"

    def render(
        self,
        context: PromptContributionRenderContext,
    ) -> PromptContributionRenderResult:
        runtime_key = str(context.node.get("runtime_key") or "").strip()
        return PromptContributionRenderResult(
            content=context.runtime_sections.get(runtime_key, ""),
            active_source="request",
        )


def _prompt_contribution_renderers(
) -> MappingProxyType[str, PromptContributionRendererPort]:
    return MappingProxyType({
        "template": _TemplateContributionRenderer(),
        "runtime": _RuntimeContributionRenderer(),
    })


async def compile_prompt_plan(
    request: PromptCompileRequest | dict[str, Any],
    *,
    strict_audit: bool = True,
) -> PromptPlan:
    from core.prompt_v2.template_registry import runtime_template_dir
    from core.runtime.event_bus import emit_runtime_event

    request_view = request if isinstance(request, PromptCompileRequest) else None
    platform = (
        request_view.normalized_platform
        if request_view is not None
        else str(request.get("platform") or "qq").strip().lower() or "qq"
    )
    chat_type = (
        request_view.normalized_chat_type
        if request_view is not None
        else str(request.get("chat_type") or "private").strip().lower() or "private"
    )
    prompt_key = (
        request_view.normalized_prompt_key
        if request_view is not None
        else str(request.get("prompt_key") or "").strip()
    )
    started = time.perf_counter()
    emit_runtime_event(
        "prompt.compile",
        "started",
        attributes={
            "prompt_key": prompt_key,
            "platform": platform,
            "chat_type": chat_type,
        },
    )

    try:
        with template_governance_read_lock(runtime_template_dir()):
            plan = await _compile_prompt_plan_locked(
                request,
                strict_audit=strict_audit,
            )
    except BaseException as exc:
        emit_runtime_event(
            "prompt.compile",
            "failed",
            attributes={
                "prompt_key": prompt_key,
                "platform": platform,
                "chat_type": chat_type,
                "latency_ms": (time.perf_counter() - started) * 1000,
                "error_type": type(exc).__name__,
            },
        )
        raise
    emit_runtime_event(
        "prompt.compile",
        "succeeded",
        attributes={
            "prompt_key": prompt_key,
            "platform": platform,
            "chat_type": chat_type,
            "message_count": len(plan.messages),
            "section_count": len(plan.flow_sections),
            "tool_count": len(plan.tool_schemas),
            "latency_ms": (time.perf_counter() - started) * 1000,
            "request_sha256": plan.prompt_sha256,
        },
    )
    return plan


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
        )
    runtime_tool_prompt = _clean_runtime_tool_prompt(request.runtime_tool_prompt)
    current_user = build_current_user_event(request)
    flow_state = load_flow()
    selected_nodes = ordered_nodes_for_chat(
        flow_state.flow,
        chat_type,
        platform=platform,
    )
    contribution_resolution = resolve_prompt_contributions(
        flow_state.flow,
        selected_nodes,
        chat_type=chat_type,
        platform=platform,
    )
    selected_nodes_by_id = {
        str(node.get("id") or "").strip(): node
        for node in selected_nodes
    }
    ordered_nodes = [
        selected_nodes_by_id[contribution_id]
        for contribution_id in contribution_resolution.ordered_ids
    ]
    contributions_by_node_id = dict(contribution_resolution.descriptors)
    descriptors_by_node_id = {
        descriptor_id: contribution.section_descriptor
        for descriptor_id, contribution in contributions_by_node_id.items()
    }
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
    contribution_renderers = _prompt_contribution_renderers()
    contribution_input_variables = MappingProxyType({
        **vars(request),
        **template_values,
    })

    def append_section(
        section_id: str,
        content: Any,
        descriptor: PromptSectionDescriptor,
    ) -> list[int]:
        text = str(content or "").strip()
        hash_section(section_hashes, section_id, content)
        if text:
            message_index = len(messages)
            if descriptor.trust == "untrusted_data":
                payload = json.dumps(
                    {
                        "section": section_id,
                        "trust": descriptor.trust,
                        "content": text,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).replace("<", "\\u003c").replace(">", "\\u003e")
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "<context_data_json>\n"
                            f"{payload}\n"
                            "</context_data_json>"
                        ),
                    }
                )
            else:
                messages.append(system_message(text))
            return [message_index]
        return []

    def section_metadata(
        *,
        node_id: str,
        node_type: str,
        template_key: str = "",
        runtime_key: str = "",
        active_source: str = "request",
        origin: PromptFlowOrigin = "flow",
        status: PromptFlowStatus,
        message_indexes: list[int] | None = None,
        descriptor: PromptSectionDescriptor | None = None,
        contribution: PromptContributionDescriptor | None = None,
    ) -> PromptFlowSection:
        resolved_descriptor = descriptor or descriptor_for_node(
            {
                "id": node_id,
                "type": node_type,
                "template_key": template_key,
                "runtime_key": runtime_key,
            }
        )
        resolved_contribution = contribution or contributions_by_node_id.get(
            node_id
        ) or contribution_for_node({
            "id": node_id,
            "type": node_type,
            "template_key": template_key,
            "runtime_key": runtime_key,
        })
        metadata = resolved_descriptor.to_dict()
        metadata.pop("section_id", None)
        contribution_metadata = resolved_contribution.metadata()
        for field in (
            "section_id",
            "owner_module",
            "domain",
            "phase",
            "authority",
            "trust",
            "dependencies",
            "source_precedence",
            "editable",
            "failure_policy",
        ):
            contribution_metadata.pop(field, None)
        return {
            "node_id": node_id,
            "node_type": node_type,
            "template_key": template_key,
            "runtime_key": runtime_key,
            "origin": origin,
            "status": status,
            "message_indexes": list(message_indexes or []),
            "active_source": active_source,
            "contribution_generation": contribution_resolution.generation,
            **contribution_metadata,
            **metadata,
        }

    for node in ordered_nodes:
        node_id = str(node.get("id") or "").strip()
        node_type = str(node.get("type") or "").strip()
        descriptor = descriptors_by_node_id.get(node_id) or descriptor_for_node(node)
        contribution = contributions_by_node_id.get(node_id) or contribution_for_node(
            node
        )
        renderer = require_prompt_renderer(
            contribution_renderers,
            contribution,
        )
        render_context = PromptContributionRenderContext(
            descriptor=contribution,
            node=node,
            template_values=template_values,
            runtime_sections=runtime_sections,
            input_variables=contribution_input_variables,
        )
        validate_prompt_contribution_inputs(render_context)
        if node_type == "template":
            template_key = str(node.get("template_key") or "").strip()
            try:
                render_result = render_prompt_contribution(
                    renderer,
                    render_context,
                )
                rendered = str(render_result.content or "").strip()
            except FileNotFoundError:
                if descriptor.failure_policy == "fail_closed":
                    raise
                warnings.append(f"template node {node_id} missing template: {template_key}")
                hash_section(section_hashes, node_id, "")
                flow_sections.append(
                    section_metadata(
                        node_id=node_id,
                        node_type=node_type,
                        template_key=template_key,
                        active_source="unresolved",
                        status="missing_template",
                        descriptor=descriptor,
                        contribution=contribution,
                    )
                )
                continue
            except Exception:
                if descriptor.failure_policy == "fail_closed":
                    raise
                warnings.append(f"template node {node_id} render failed: {template_key}")
                hash_section(section_hashes, node_id, "")
                flow_sections.append(
                    section_metadata(
                        node_id=node_id,
                        node_type=node_type,
                        template_key=template_key,
                        active_source="unresolved",
                        status="missing_template",
                        descriptor=descriptor,
                        contribution=contribution,
                    )
                )
                continue
            template_paths[node_id] = render_result.template_path
            if render_result.template_resolution is None:
                raise RuntimeError(f"Prompt 模板缺少来源解析记录: {template_key}")
            validate_descriptor_source(
                descriptor,
                render_result.active_source,
            )
            template_resolutions[node_id] = dict(
                render_result.template_resolution
            )
            message_indexes = append_section(node_id, rendered, descriptor)
            flow_sections.append(
                section_metadata(
                    node_id=node_id,
                    node_type=node_type,
                    template_key=template_key,
                    active_source=render_result.active_source,
                    status="emitted" if message_indexes else "empty",
                    message_indexes=message_indexes,
                    descriptor=descriptor,
                    contribution=contribution,
                )
            )
            if node_id in {"group_policy", "private_policy"}:
                hash_section(section_hashes, "chat_policy", rendered)
            continue

        runtime_key = str(node.get("runtime_key") or "").strip()
        render_result = render_prompt_contribution(
            renderer,
            render_context,
        )
        content = render_result.content
        if runtime_key in singleton_runtime_keys and runtime_key in seen_runtime_keys:
            warnings.append(f"flow duplicated singleton runtime node {runtime_key}; skipped node {node_id}")
            hash_section(section_hashes, node_id, content)
            flow_sections.append(
                section_metadata(
                    node_id=node_id,
                    node_type=node_type,
                    runtime_key=runtime_key,
                    active_source=render_result.active_source,
                    status="skipped_duplicate",
                    descriptor=descriptor,
                    contribution=contribution,
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
            message_indexes = append_section(node_id, content, descriptor)
        section = section_metadata(
            node_id=node_id,
            node_type=node_type,
            runtime_key=runtime_key,
            active_source=render_result.active_source,
            status=(
                "emitted"
                if message_indexes or runtime_key == "current_user_event"
                else "empty"
            ),
            message_indexes=message_indexes,
            descriptor=descriptor,
            contribution=contribution,
        )
        flow_sections.append(section)
        if runtime_key == "current_user_event":
            current_user_flow_section = section

    if "base_contract" not in section_hashes:
        hash_section(section_hashes, "base_contract", "")
    if "persona_reference" not in seen_runtime_keys:
        descriptor = descriptor_for_node(
            {
                "id": "persona_reference",
                "type": "runtime",
                "runtime_key": "persona_reference",
            }
        )
        message_indexes = append_section(
            "persona_reference",
            persona_reference,
            descriptor,
        )
        flow_sections.append(
            section_metadata(
                node_id="persona_reference",
                node_type="runtime",
                runtime_key="persona_reference",
                origin="fallback",
                status="emitted" if message_indexes else "empty",
                message_indexes=message_indexes,
                descriptor=descriptor,
            )
        )
        warnings.append("flow missing persona_reference; compiler appended singleton runtime section")
    if "runtime_tool_prompt" not in seen_runtime_keys:
        descriptor = descriptor_for_node(
            {
                "id": "runtime_tool_prompt",
                "type": "runtime",
                "runtime_key": "runtime_tool_prompt",
            }
        )
        message_indexes = append_section(
            "runtime_tool_prompt",
            runtime_tool_prompt,
            descriptor,
        )
        flow_sections.append(
            section_metadata(
                node_id="runtime_tool_prompt",
                node_type="runtime",
                runtime_key="runtime_tool_prompt",
                origin="fallback",
                status="emitted" if message_indexes else "empty",
                message_indexes=message_indexes,
                descriptor=descriptor,
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

    from core.prompt_v2.tool_templates import collect_tool_template_resolutions

    template_resolutions.update(
        collect_tool_template_resolutions(request.tool_schemas)
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
        "prompt_contribution_registry": {
            "namespace": contribution_resolution.registry_snapshot.namespace,
            "generation": contribution_resolution.generation,
            "sha256": contribution_resolution.sha256,
            "canonical_sha256": (
                PROMPT_CONTRIBUTION_REGISTRY.registry_snapshot.sha256
            ),
            "ordered_ids": [
                str(section.get("contribution_id") or "")
                for section in flow_sections
            ],
        },
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

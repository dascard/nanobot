"""Prompt Runtime 预览服务。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session


def _strip(value: Any) -> str:
    return str(value or "").strip()


def _iso(value: Any) -> str:
    return value.isoformat(sep=" ", timespec="seconds") if value else ""


def _redact_guidance_from_error(detail: str, guidance_text: str) -> str:
    if not guidance_text:
        return detail
    return detail.replace(guidance_text, "[REDACTED]")


def _recent_prompt_preview_logs(db: Session, body: Any) -> tuple[str, list[dict]]:
    from core.database import AgentRun, LLMApiRequestLog
    from core.tracing import row_to_dict

    q = db.query(AgentRun)
    if _strip(getattr(body, "session_id", "")):
        q = q.filter(AgentRun.session_id == _strip(body.session_id))
    if _strip(getattr(body, "user_id", "")):
        q = q.filter(AgentRun.user_id == _strip(body.user_id))
    if _strip(getattr(body, "group_id", "")):
        q = q.filter(AgentRun.group_id == _strip(body.group_id))
    if _strip(getattr(body, "chat_type", "")):
        q = q.filter(AgentRun.chat_type == _strip(body.chat_type))
    run = q.order_by(AgentRun.started_at.desc()).first()
    if not run:
        return "", []
    logs = (
        db.query(LLMApiRequestLog)
        .filter(LLMApiRequestLog.run_id == run.run_id)
        .order_by(LLMApiRequestLog.created_at.desc())
        .limit(5)
        .all()
    )
    return run.run_id, [row_to_dict(row) for row in logs]


async def preview_effective_prompt_v2(body: Any, db: Session) -> dict[str, Any]:
    """构建 canonical Prompt Runtime 有效预览。"""
    from core.context_builder import build_structured_chat_context
    from core.context_engine import ContextManifestError
    from core.chat_stream_identity import (
        ChatStreamIdentityError,
        resolve_chat_stream_identity,
    )
    from core.database import Persona
    from core.identity import is_super_user_id
    from core.prompt_v2 import compiler as prompt_compiler
    from core.prompt_v2.audit import PromptAuditError
    from core.prompt_v2.flow import PromptFlowError
    from core.prompt_v2.schema import PromptCompileRequest
    from core.prompt_v2.template_baseline import TemplateBaselineError
    from core.prompt_v2.template_resolution import build_template_trace_fields
    from core.session_guidance import (
        SessionGuidanceValidationError,
        normalize_session_guidance,
        resolve_session_guidance,
        summarize_session_guidance,
    )
    from core.tool_plan import build_tool_plan
    from fastapi import HTTPException

    chat_type = _strip(getattr(body, "chat_type", "")) or "private"
    platform = (_strip(getattr(body, "platform", "")) or "qq").lower()
    is_group = chat_type == "group"
    group_id = _strip(getattr(body, "group_id", ""))
    session_id = _strip(getattr(body, "session_id", "")) or (
        f"group_{group_id}" if is_group and group_id else ""
    )
    user_id = _strip(getattr(body, "user_id", ""))
    is_super_user = bool(user_id and is_super_user_id(user_id))
    runtime_chat_type = (
        "private_superuser"
        if chat_type == "private" and is_super_user
        else chat_type
    )

    session_guidance_override = getattr(body, "session_guidance_override", None)
    try:
        if session_guidance_override is not None:
            if not session_id:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "session_guidance_identity_required",
                        "message": "预览会话指导需要明确的 session_id",
                    },
                )
            identity = resolve_chat_stream_identity(
                platform=platform,
                chat_type=chat_type,
                session_id=session_id,
            )
            normalized_guidance = normalize_session_guidance(
                session_guidance_override
            )
            session_guidance = summarize_session_guidance(
                chat_stream_id=identity.chat_stream_id,
                text=normalized_guidance,
                updated_at=None,
                status="configured" if normalized_guidance else "empty",
            )
        elif session_id:
            session_guidance = resolve_session_guidance(
                db,
                platform=platform,
                chat_type=chat_type,
                session_id=session_id,
            )
        else:
            session_guidance = summarize_session_guidance(
                chat_stream_id="",
                text="",
                updated_at=None,
                status="not_requested",
            )
    except (ChatStreamIdentityError, SessionGuidanceValidationError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc

    persona_text = ""
    if user_id:
        persona = db.query(Persona).filter(
            Persona.user_id == user_id,
            Persona.status == "active",
        ).first()
        if persona and persona.persona_json:
            persona_text = persona.persona_json

    user_input = str(getattr(body, "user_input", "") or "")
    structured_context = build_structured_chat_context(
        db,
        session_id,
        user_id=user_id,
        is_group=is_group,
        group_id=group_id,
        current_user_input=user_input,
        read_only=True,
    )
    history_header = structured_context.conversation_context_header
    history_messages = list(structured_context.recent_messages)
    history_debug = dict(structured_context.debug)
    persona_debug: dict[str, Any] = {}
    if user_id:
        try:
            from app.persona.injection_service import PersonaInjectionService

            persona_result = PersonaInjectionService(db).build_context(
                user_id=user_id,
                current_user_input=user_input,
                recent_messages=history_messages,
            )
            persona_debug = dict(persona_result.debug or {})
            if persona_result.context:
                persona_text = persona_result.context
        except Exception as exc:
            persona_debug = {"persona_error": str(exc)}
    context_debug = {**history_debug, **persona_debug}
    runtime_preset = _strip(getattr(body, "runtime_preset", "")) or "full"
    tool_plan = build_tool_plan(
        chat_type=runtime_chat_type,
        group_id=group_id,
        user_id=user_id,
        platform=platform,
        session_id=session_id,
        runtime_preset=runtime_preset,
        db=db,
    )
    enabled = dict(tool_plan.enabled)
    disabled = dict(tool_plan.disabled)
    runtime_tool_prompt = tool_plan.runtime_tool_prompt
    configured_tool_schemas = list(tool_plan.sent_tool_schemas)

    v2_prompt_key = _strip(getattr(body, "prompt_key", ""))
    if v2_prompt_key in {"", "group_chat", "private_chat"}:
        v2_prompt_key = "chat_group" if is_group else "chat_private"

    try:
        plan = await prompt_compiler.compile_prompt_plan(
            PromptCompileRequest(
                chat_type=chat_type,
                platform=platform,
                prompt_key=v2_prompt_key,
                session_id=session_id,
                user_id=user_id,
                group_id=group_id,
                sender_name=str(getattr(body, "sender_name", "") or ""),
                sender_id=user_id,
                is_super_user=is_super_user,
                user_input=user_input,
                persona_text=persona_text or "无已存储画像",
                history_header=history_header,
                history_messages=history_messages,
                summary_context=structured_context.summary_context,
                memory_recall_context=(
                    structured_context.memory_recall_context
                ),
                project_context=structured_context.project_context,
                session_guidance=session_guidance.text,
                session_guidance_chat_stream_id=session_guidance.chat_stream_id,
                runtime_tool_prompt=runtime_tool_prompt,
                tool_schemas=configured_tool_schemas,
                debug={
                    "history_debug": history_debug,
                    "context_debug": context_debug,
                    **session_guidance.debug,
                },
            ),
            strict_audit=True,
        )
    except (
        ContextManifestError,
        PromptAuditError,
        PromptFlowError,
        TemplateBaselineError,
    ) as exc:
        raise HTTPException(
            status_code=400,
            detail=_redact_guidance_from_error(str(exc), session_guidance.text),
        ) from exc
    preview_degraded_reasons = []
    if history_debug.get("model_dependent_retrieval_skipped"):
        preview_degraded_reasons.append(
            "group_memory_model_calls_forbidden"
        )
    warnings = list(plan.warnings)
    warnings.extend(
        reason for reason in preview_degraded_reasons
        if reason not in warnings
    )
    recent_run_id, recent_logs = _recent_prompt_preview_logs(db, body)
    tools = [
        {"name": name, "enabled": bool(enabled.get(name, True))}
        for name in sorted(enabled.keys())
    ]
    template_resolutions = plan.template_resolutions
    template_trace_fields = build_template_trace_fields(template_resolutions)
    return {
        "engine": "prompt",
        "chat_type": chat_type,
        "runtime_chat_type": runtime_chat_type,
        "is_super_user": is_super_user,
        "platform": platform,
        "session_id": session_id,
        "user_id": user_id,
        "group_id": group_id,
        **session_guidance.debug,
        "session_guidance_updated_at": _iso(session_guidance.updated_at),
        "prompt_key": plan.prompt_key,
        "prompt_mode": "prompt",
        **template_trace_fields,
        "template_resolutions": template_resolutions,
        "prompt_sha256": plan.prompt_sha256,
        "request_prompt_sha256": plan.prompt_sha256,
        "message_token_estimate": plan.message_token_estimate,
        "tool_schema_token_estimate": plan.tool_schema_token_estimate,
        "token_estimate": plan.token_estimate,
        "context_manifest": plan.context_manifest,
        "preview_exact": not preview_degraded_reasons,
        "preview_degraded_reasons": preview_degraded_reasons,
        "section_hashes": plan.section_hashes,
        "warnings": warnings,
        "debug": plan.debug,
        "history_debug": history_debug,
        "context_debug": context_debug,
        "persona_context": persona_debug.get("persona_context", ""),
        "persona_fact_ids": persona_debug.get("persona_fact_ids", []),
        "persona_skipped": persona_debug.get("persona_skipped", []),
        "persona_context_chars": persona_debug.get("persona_context_chars", 0),
        "persona_score_components": persona_debug.get("score_components", {}),
        "group_profile_mode": history_debug.get("group_profile_mode", "off"),
        "group_memory_context": history_debug.get("group_memory_context", ""),
        "group_memory_ids": history_debug.get("group_memory_ids", []),
        "group_memory_skipped": history_debug.get("group_memory_skipped", []),
        "group_memory_context_chars": history_debug.get("group_memory_context_chars", 0),
        "score_components": history_debug.get("score_components", {}),
        "runtime_preset": runtime_preset,
        "runtime_tool_prompt": runtime_tool_prompt,
        "tool_plan_sha256": tool_plan.sha256,
        "tools": tools,
        "tool_schemas": plan.tool_schemas,
        "effective_tool_schemas": plan.tool_schemas,
        "disabled_tools": disabled,
        "messages": plan.messages,
        "request_json": plan.request_json,
        "prompt_plan": plan.to_dict(),
        "compiled_prompt": plan.to_dict(),
        "prompt_build": plan.to_dict(),
        "recent_agent_run_id": recent_run_id,
        "recent_llm_api_logs": recent_logs,
    }

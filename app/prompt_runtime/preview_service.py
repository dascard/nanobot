"""Prompt Runtime 预览服务。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session


def _strip(value: Any) -> str:
    return str(value or "").strip()


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
    """构建 Prompt Runtime V2 有效预览。"""
    from core.context_builder import build_chat_context
    from core.database import Persona
    from core.prompt_v2 import compiler as prompt_compiler
    from core.prompt_v2.schema import PromptCompileRequest
    from core.runtime_tool_service import build_runtime_tool_prompt, resolve_effective_tools
    from core.tool_schema_preview import build_effective_tool_schemas

    chat_type = _strip(getattr(body, "chat_type", "")) or "private"
    is_group = chat_type == "group"
    group_id = _strip(getattr(body, "group_id", ""))
    session_id = _strip(getattr(body, "session_id", "")) or (
        f"group_{group_id}" if is_group and group_id else ""
    )
    user_id = _strip(getattr(body, "user_id", ""))

    persona_text = ""
    if user_id:
        persona = db.query(Persona).filter(Persona.user_id == user_id).first()
        if persona and persona.persona_json:
            persona_text = persona.persona_json

    history_header, history_messages, history_debug = build_chat_context(
        db,
        session_id,
        user_id=user_id,
        is_group=is_group,
        group_id=group_id,
    )
    runtime_preset = _strip(getattr(body, "runtime_preset", "")) or "full"
    enabled, disabled = resolve_effective_tools(
        chat_type=chat_type,
        group_id=group_id,
        user_id=user_id,
        runtime_preset=runtime_preset,
        db=db,
    )
    runtime_tool_prompt = build_runtime_tool_prompt(enabled, disabled, chat_type)
    configured_tool_schemas = build_effective_tool_schemas(enabled)

    v2_prompt_key = _strip(getattr(body, "prompt_key", ""))
    if v2_prompt_key in {"", "group_chat", "private_chat"}:
        v2_prompt_key = "chat_group" if is_group else "chat_private"

    plan = await prompt_compiler.compile_prompt_plan(
        PromptCompileRequest(
            chat_type=chat_type,
            prompt_key=v2_prompt_key,
            session_id=session_id,
            user_id=user_id,
            group_id=group_id,
            sender_name=str(getattr(body, "sender_name", "") or ""),
            sender_id=user_id,
            user_input=str(getattr(body, "user_input", "") or ""),
            persona_text=persona_text or "无已存储画像",
            history_header=history_header,
            history_messages=history_messages,
            runtime_tool_prompt=runtime_tool_prompt,
            tool_schemas=configured_tool_schemas,
            debug={"history_debug": history_debug},
        )
    )
    recent_run_id, recent_logs = _recent_prompt_preview_logs(db, body)
    tools = [
        {"name": name, "enabled": bool(enabled.get(name, True))}
        for name in sorted(enabled.keys())
    ]
    return {
        "engine": "v2",
        "chat_type": chat_type,
        "session_id": session_id,
        "user_id": user_id,
        "group_id": group_id,
        "prompt_key": plan.prompt_key,
        "prompt_mode": "v2",
        "prompt_source": "Prompt Runtime V2",
        "prompt_runtime_path": plan.debug.get("template_path", ""),
        "prompt_default_path": plan.debug.get("template_path", ""),
        "prompt_sha256": plan.prompt_sha256,
        "section_hashes": plan.section_hashes,
        "warnings": plan.warnings,
        "debug": plan.debug,
        "history_debug": history_debug,
        "runtime_preset": runtime_preset,
        "runtime_tool_prompt": runtime_tool_prompt,
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

"""Nanobot 主链路的提示词运行时适配。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("nanobot.prompt_runtime")


@dataclass(frozen=True)
class PromptRuntimeInput:
    prompt_engine: str
    prompt_mode: str
    prompt_key: str
    chat_type: str
    runtime_chat_type: str
    session_id: str
    user_id: str
    group_id: str
    sender_name: str
    sender_id: str
    session_name: str
    trigger_reason: str
    timing_decision: str
    current_message_id: str
    source_message_ids: list[str]
    self_id: str
    bot_id: str
    bot_name: str
    bot_aliases: list[str]
    user_input: str
    persona_text: str
    history_header: str
    history_messages: list[dict[str, Any]]
    runtime_tool_prompt: str
    effort_constraint: str
    trace_id: str
    run_id: str
    is_group: bool = False
    group_profile_context: str = ""
    expression_context: str = ""
    jargon_context: str = ""
    tool_schemas: list[dict[str, Any]] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)
    audit_failure_policy: str = "fail_fast"


@dataclass(frozen=True)
class PromptRuntimeResult:
    prompt_key: str
    prompt_mode: str
    prompt_source: str
    prompt_runtime_path: str
    prompt_default_path: str
    prompt_sha256: str
    pre_event_messages: list[dict[str, Any]]
    event_content: Any
    meta_update: dict[str, Any] = field(default_factory=dict)


class PromptRuntimeAuditFailure(RuntimeError):
    def __init__(self, message: str, *, meta_update: dict[str, Any]):
        self.meta_update = dict(meta_update or {})
        super().__init__(message)


def _v1_prompt_key(is_group: bool) -> str:
    return "group_chat" if is_group else "private_chat"


def _v1_prompt_mode(value: str) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in {"legacy", "shadow", "managed"} else "shadow"


def _build_v1_prompt(input: PromptRuntimeInput) -> PromptRuntimeResult:
    from core.prompt_assembler import PromptAssembler, PromptBuildContext

    prompt_mode = _v1_prompt_mode(input.prompt_mode)
    prompt_key = _v1_prompt_key(input.is_group)
    prompt_build = PromptAssembler().build(
        PromptBuildContext(
            mode=prompt_mode,
            chat_type=input.runtime_chat_type,
            prompt_key=prompt_key,
            session_id=input.session_id,
            user_id=input.user_id,
            group_id=input.group_id,
            sender_name=input.sender_name,
            sender_id=input.sender_id,
            session_name=input.session_name,
            trigger_reason=input.trigger_reason,
            timing_decision=input.timing_decision,
            current_message_id=input.current_message_id,
            source_message_ids=input.source_message_ids,
            self_id=input.self_id,
            bot_id=input.bot_id,
            bot_name=input.bot_name,
            bot_aliases=input.bot_aliases,
            user_input=input.user_input,
            persona_text=input.persona_text,
            history_header=input.history_header,
            history_messages=input.history_messages,
            runtime_tool_prompt=input.runtime_tool_prompt,
            effort_constraint=input.effort_constraint,
        ),
        trace_id=input.trace_id,
        run_id=input.run_id,
    )
    return PromptRuntimeResult(
        prompt_key=prompt_build.prompt_key,
        prompt_mode=prompt_build.prompt_mode,
        prompt_source=prompt_build.prompt_source,
        prompt_runtime_path=prompt_build.prompt_runtime_path,
        prompt_default_path=prompt_build.prompt_default_path,
        prompt_sha256=prompt_build.prompt_sha256,
        pre_event_messages=prompt_build.pre_event_messages,
        event_content=prompt_build.event_content,
    )


async def build_prompt_runtime(input: PromptRuntimeInput) -> PromptRuntimeResult:
    if input.prompt_engine != "v2":
        return _build_v1_prompt(input)

    from core.prompt_v2.audit import PromptAuditError
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest
    from core.tracing import PromptTracer

    prompt_request = PromptCompileRequest(
        chat_type=input.chat_type,
        prompt_key=input.prompt_key,
        session_id=input.session_id,
        user_id=input.user_id,
        group_id=input.group_id,
        sender_name=input.sender_name,
        sender_id=input.sender_id,
        session_name=input.session_name,
        trigger_reason=input.trigger_reason,
        timing_decision=input.timing_decision,
        current_message_id=input.current_message_id,
        source_message_ids=input.source_message_ids,
        self_id=input.self_id,
        bot_id=input.bot_id,
        bot_name=input.bot_name,
        bot_aliases=input.bot_aliases,
        user_input=input.user_input,
        persona_text=input.persona_text,
        history_header=input.history_header,
        history_messages=input.history_messages,
        group_profile_context=input.group_profile_context,
        expression_context=input.expression_context,
        jargon_context=input.jargon_context,
        runtime_tool_prompt=input.runtime_tool_prompt,
        effort_constraint=input.effort_constraint,
        tool_schemas=input.tool_schemas,
        debug=input.debug,
    )
    try:
        prompt_plan = await compile_prompt_plan(prompt_request, strict_audit=True)
    except PromptAuditError as exc:
        audit_issues = list(getattr(exc, "issues", []) or [str(exc)])
        meta_update = {
            "prompt_engine": "v2",
            "prompt_v2_audit_failed": True,
            "audit_issues": audit_issues,
        }
        raise PromptRuntimeAuditFailure(
            f"Prompt Runtime V2 审计失败: {exc}",
            meta_update=meta_update,
        ) from exc

    PromptTracer.record_render(
        trace_id=input.trace_id,
        run_id=input.run_id,
        prompt_key=prompt_plan.prompt_key,
        mode="v2",
        variables=prompt_plan.debug,
        rendered_content=json.dumps(prompt_plan.request_json, ensure_ascii=False),
        token_estimate=prompt_plan.token_estimate,
        warnings=prompt_plan.warnings,
        prompt_source="Prompt Runtime V2",
        prompt_runtime_path=str(prompt_plan.debug.get("template_path", "")),
        prompt_default_path=str(prompt_plan.debug.get("template_path", "")),
        prompt_sha256=prompt_plan.prompt_sha256,
    )
    context_debug = dict(prompt_plan.debug.get("context_debug", {}) or {})
    meta_update = {
        "prompt_engine": "v2",
        "group_memory": context_debug,
    }
    if context_debug.get("group_memory_injected") and context_debug.get("group_memory_ids"):
        try:
            from app.group_memory.injection_service import record_group_memory_injected
            from core.uow import UnitOfWork

            with UnitOfWork() as uow:
                recorded_count = record_group_memory_injected(
                    uow.db,
                    list(context_debug.get("group_memory_ids") or []),
                )
                uow.commit()
            meta_update["group_memory_recorded_count"] = recorded_count
        except Exception as exc:
            logger.warning("[PromptRuntime] failed to record group memory injection: %s", exc)
    if context_debug.get("persona_injected") and context_debug.get("persona_fact_ids"):
        try:
            from app.persona.injection_service import record_persona_injected
            from core.uow import UnitOfWork

            with UnitOfWork() as uow:
                recorded_count = record_persona_injected(
                    uow.db,
                    list(context_debug.get("persona_fact_ids") or []),
                )
                uow.commit()
            meta_update["persona_recorded_count"] = recorded_count
        except Exception as exc:
            logger.warning("[PromptRuntime] failed to record persona injection: %s", exc)
    for key in (
        "group_memory_injected",
        "group_memory_ids",
        "group_memory_skipped",
        "group_memory_context_chars",
        "group_profile_mode",
        "persona_injected",
        "persona_fact_ids",
        "persona_skipped",
        "persona_context_chars",
        "rolling_summary_enabled",
        "rolling_summary_injected",
        "rolling_summary_id",
        "rolling_summary_covered_until_turn_id",
        "rolling_summary_source_turn_count",
        "rolling_summary_pending_turn_ids",
        "rolling_summary_raw_start_turn_id",
        "rolling_summary_recent_raw_turn_ids",
        "rolling_summary_skipped_reason",
        "rolling_summary_error",
    ):
        if key in context_debug:
            meta_update[key] = context_debug[key]

    return PromptRuntimeResult(
        prompt_key=prompt_plan.prompt_key,
        prompt_mode="v2",
        prompt_source="Prompt Runtime V2",
        prompt_runtime_path=str(prompt_plan.debug.get("template_path", "")),
        prompt_default_path=str(prompt_plan.debug.get("template_path", "")),
        prompt_sha256=prompt_plan.prompt_sha256,
        pre_event_messages=prompt_plan.messages_without_current_user,
        event_content=prompt_plan.current_user_content,
        meta_update=meta_update,
    )

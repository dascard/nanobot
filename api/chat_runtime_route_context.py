"""聊天运行时路由上下文组装。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from api import chat_runtime_facade


@dataclass
class ChatRuntimeRouteServices:
    build_multimodal_user_input_text: Callable[..., str]
    max_query_chars: int
    estimate_tokens: Callable[[str], int]
    get_effort_constraint: Callable[[str | None], str]
    chat_request_platform: Callable[[Any], str]
    build_runtime_payload: Callable[..., chat_runtime_facade.ChatRuntimePayload]
    build_persona_context: Callable[..., Any]
    logger: Any


@dataclass(frozen=True)
class ChatRuntimeRouteInput:
    req: Any
    final_query: str
    final_files: list[str]
    memory_header: str
    history_messages: list[dict[str, str]]
    ctx_debug: dict[str, Any]
    is_group: bool
    is_superuser: bool
    private_decision: Any | None
    guardrail_status: str | None
    classifier_ran: bool
    summary_context: str = ""
    memory_recall_context: str = ""
    project_context: str = ""


@dataclass(frozen=True)
class ChatRuntimeRouteContext:
    safe_user_input: str
    enriched_query: str
    bridge_meta: dict[str, Any]
    platform: str
    prompt_budget: dict[str, Any]
    persona_text: str
    ctx_debug: dict[str, Any]
    injection_mode: bool


def _empty_effort_constraint(_effort: str | None) -> str:
    return ""


def _inject_persona_context(
    runtime_input: ChatRuntimeRouteInput,
    *,
    services: ChatRuntimeRouteServices,
    safe_user_input: str,
) -> tuple[str, dict[str, Any]]:
    # 所有聊天画像均以本次动态门禁结果为准；旧快照不得绕过关闭开关回流。
    persona_text = ""
    ctx_debug = dict(runtime_input.ctx_debug)
    try:
        persona_result = services.build_persona_context(
            user_id=runtime_input.req.user_id,
            current_user_input=safe_user_input,
            recent_messages=runtime_input.history_messages,
        )
        ctx_debug.update(getattr(persona_result, "debug", {}) or {})
        context = getattr(persona_result, "context", "")
        persona_text = context or ""
    except Exception as exc:
        services.logger.warning(
            "[/chat] persona injection context failed user=%s: %s",
            runtime_input.req.user_id,
            exc,
        )
    return persona_text, ctx_debug


def _log_prompt_budget(
    runtime_input: ChatRuntimeRouteInput,
    *,
    services: ChatRuntimeRouteServices,
    prompt_budget: dict[str, Any],
    injection_mode: bool,
) -> None:
    if not injection_mode:
        chat_type = "group" if runtime_input.is_group else "private"
        services.logger.info(
            f"[/chat] Prompt budget: type={chat_type}, "
            f"query_chars={prompt_budget['safe_user_input_chars']}, "
            f"query_tokens~{prompt_budget['safe_user_input_tokens']}, "
            f"persona_chars={prompt_budget['persona_chars']}, "
            f"persona_tokens~{prompt_budget['persona_tokens']}, "
            f"history_msgs={prompt_budget['history_messages']}, "
            f"history_total_chars~{prompt_budget['history_total_chars']}, "
            f"enriched_chars={prompt_budget['enriched_query_chars']}, "
            f"enriched_tokens~{prompt_budget['enriched_query_tokens']}"
        )
        return

    services.logger.info(
        f"[/chat] Injection mode, using mock enriched_query, "
        f"persona_chars={prompt_budget['persona_chars']}, "
        f"persona_tokens~{prompt_budget['persona_tokens']}, "
        f"history_msgs={prompt_budget['history_messages']}"
    )


def build_chat_runtime_route_context(
    runtime_input: ChatRuntimeRouteInput,
    *,
    services: ChatRuntimeRouteServices,
) -> ChatRuntimeRouteContext:
    safe_user_input = services.build_multimodal_user_input_text(
        runtime_input.final_query,
        runtime_input.final_files,
        max_chars=services.max_query_chars,
    )
    persona_text, ctx_debug = _inject_persona_context(
        runtime_input,
        services=services,
        safe_user_input=safe_user_input,
    )
    get_effort_constraint = (
        services.get_effort_constraint
        if runtime_input.private_decision
        else _empty_effort_constraint
    )
    runtime_payload = services.build_runtime_payload(
        chat_runtime_facade.ChatRuntimeInput(
            final_query=runtime_input.final_query,
            final_files=runtime_input.final_files,
            req_user_id=runtime_input.req.user_id,
            req_session_id=runtime_input.req.session_id,
            sender_name=runtime_input.req.sender_name or "",
            session_name=runtime_input.req.session_name,
            message_id=runtime_input.req.message_id or "",
            persona_text=persona_text,
            memory_header=runtime_input.memory_header,
            history_messages=runtime_input.history_messages,
            is_group=runtime_input.is_group,
            is_superuser=runtime_input.is_superuser,
            stream=bool(runtime_input.req.stream),
            platform=services.chat_request_platform(runtime_input.req),
            private_decision=runtime_input.private_decision,
            guardrail_status=runtime_input.guardrail_status,
            classifier_ran=runtime_input.classifier_ran,
            event_time=str(
                (
                    getattr(runtime_input.req, "client_meta", None)
                    if isinstance(
                        getattr(runtime_input.req, "client_meta", None),
                        dict,
                    )
                    else {}
                ).get("event_time")
                or ""
            ),
            summary_context=runtime_input.summary_context,
            memory_recall_context=runtime_input.memory_recall_context,
            project_context=runtime_input.project_context,
        ),
        build_multimodal_user_input_text=services.build_multimodal_user_input_text,
        max_query_chars=services.max_query_chars,
        estimate_tokens=services.estimate_tokens,
        get_effort_constraint=get_effort_constraint,
    )
    platform = str(runtime_payload.bridge_meta.get("platform") or "")
    bridge_meta = dict(runtime_payload.bridge_meta)
    bridge_meta["context_debug"] = ctx_debug
    _log_prompt_budget(
        runtime_input,
        services=services,
        prompt_budget=runtime_payload.prompt_budget,
        injection_mode=runtime_payload.injection_mode,
    )
    return ChatRuntimeRouteContext(
        safe_user_input=runtime_payload.safe_user_input,
        enriched_query=runtime_payload.enriched_query,
        bridge_meta=bridge_meta,
        platform=platform,
        prompt_budget=runtime_payload.prompt_budget,
        persona_text=persona_text,
        ctx_debug=ctx_debug,
        injection_mode=runtime_payload.injection_mode,
    )

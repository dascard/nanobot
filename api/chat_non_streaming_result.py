"""聊天非流式结果收尾 helper。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from api import chat_response_contract
from core.inbound_idempotency import CompletedInboundResponse


logger = logging.getLogger("nanobot.routes")


@dataclass(frozen=True)
class ChatNonStreamingResultCallbacks:
    pop_bridge_reply_meta: Callable[[Any, str], dict[str, Any] | None]
    private_prompt_audit_failure_meta: Callable[[], dict[str, Any]]
    finalize_private_buffer: Callable[..., Awaitable[None]]
    persist_chat_turn: Callable[..., int]
    expand_chat_transport_answer: Callable[[str], str]
    chat_response_payload: Callable[..., dict[str, Any]]
    persist_claimed_chat_turn: Callable[..., Any] | None = None


@dataclass(frozen=True)
class ChatNonStreamingResultContext:
    req: Any
    persist_req: Any
    bridge: Any
    answer: str
    platform: str
    bridge_meta: dict[str, Any]
    guardrail_status: str | None
    private_timing_meta: dict[str, Any] | None
    empty_assistant_placeholder: str
    evolution_threshold: int
    callbacks: ChatNonStreamingResultCallbacks
    claim_key: Any | None = None
    request_sha256: str = ""


@dataclass(frozen=True)
class ChatNonStreamingResult:
    payload: dict[str, Any] | None
    pending: int | None = None
    should_trigger_evolution: bool = False
    prompt_audit_failed: bool = False
    completion: CompletedInboundResponse | None = None


def _request_attr(req: Any, name: str, default: Any = "") -> Any:
    return getattr(req, name, default)


def _is_prompt_audit_failed(reply_meta: dict[str, Any] | None) -> bool:
    return (reply_meta or {}).get("_agent_result") == "prompt_v2_audit_failed"


async def finalize_non_streaming_chat_result(
    db: Any,
    context: ChatNonStreamingResultContext,
) -> ChatNonStreamingResult:
    callbacks = context.callbacks
    req = context.req
    private_reply_meta = callbacks.pop_bridge_reply_meta(
        context.bridge,
        str(_request_attr(req, "session_id")),
    )

    if _is_prompt_audit_failed(private_reply_meta):
        logger.error(
            "[/chat] Prompt V2 audit failed: user=%s session=%s",
            _request_attr(req, "user_id"),
            _request_attr(req, "session_id"),
        )
        await callbacks.finalize_private_buffer(
            str(_request_attr(req, "user_id")),
            context.empty_assistant_placeholder,
        )
        callbacks.persist_chat_turn(
            db,
            context.persist_req,
            context.empty_assistant_placeholder,
            context.guardrail_status,
            assistant_meta=callbacks.private_prompt_audit_failure_meta(),
            assistant_processed=1,
            timing_meta=context.private_timing_meta,
        )
        return ChatNonStreamingResult(payload=None, prompt_audit_failed=True)

    answer = context.answer
    logger.info(
        "[/chat] Bridge returned: answer_len=%s, answer_stripped_empty=%s",
        len(answer),
        not answer.strip(),
    )
    if answer:
        logger.debug("[/chat] Answer preview: %s", answer[:300])
    else:
        logger.warning("[/chat] EMPTY ANSWER returned from bridge!")

    transport_answer = answer
    try:
        transport_answer = callbacks.expand_chat_transport_answer(answer)
    except Exception:
        logger.warning("[/chat] generated image ref expansion failed", exc_info=True)

    await callbacks.finalize_private_buffer(str(_request_attr(req, "user_id")), answer)
    if context.claim_key is not None:
        if callbacks.persist_claimed_chat_turn is None or not context.request_sha256:
            raise RuntimeError("claimed 非流式结果缺少可恢复持久化依赖")
        completion = chat_response_contract.build_completed_inbound_response(
            outcome="respond",
            reply=answer,
            reply_meta=private_reply_meta,
            guardrail_status=context.guardrail_status,
        )
        claimed_result = callbacks.persist_claimed_chat_turn(
            db,
            context.persist_req,
            answer,
            context.guardrail_status,
            key=context.claim_key,
            request_sha256=context.request_sha256,
            completion=completion,
            timing_meta=context.private_timing_meta,
        )
        pending = int(claimed_result.pending)
        completion = claimed_result.completion
    else:
        pending = callbacks.persist_chat_turn(
            db,
            context.persist_req,
            answer,
            context.guardrail_status,
            timing_meta=context.private_timing_meta,
        )
        completion = chat_response_contract.build_completed_inbound_response(
            outcome="respond",
            reply=answer,
            reply_meta=private_reply_meta,
            guardrail_status=context.guardrail_status,
            unprocessed_logs=pending,
        )

    payload = callbacks.chat_response_payload(
        req,
        status="ok",
        answer=transport_answer,
        reply_meta=private_reply_meta,
        platform=context.platform,
        chat_type=str(context.bridge_meta.get("chat_type") or ""),
        unprocessed_logs=pending,
        guardrail_status=context.guardrail_status,
        include_answer_chunks=True,
    )
    return ChatNonStreamingResult(
        payload=payload,
        pending=pending,
        should_trigger_evolution=pending >= context.evolution_threshold,
        completion=completion,
    )

"""Chat pre-bridge route result helper."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from api import chat_pre_bridge_decision, chat_response_contract
from core.inbound_idempotency import CompletedInboundResponse


@dataclass(frozen=True)
class ChatPreBridgeRouteCallbacks:
    clone_chat_request: Callable
    persist_chat_turn: Callable
    chat_response_payload: Callable
    finalize_private_buffer: Callable


@dataclass(frozen=True)
class ChatPreBridgeRouteEarlyResponse:
    payload: dict[str, Any]
    completion: CompletedInboundResponse | None = None


@dataclass(frozen=True)
class ChatPreBridgeRouteContinue:
    final_query: str
    final_files: list[str]
    private_decision: Any | None
    private_timing_meta: dict[str, Any] | None
    guardrail_status: str | None
    classifier_ran: bool
    persist_req: Any


async def _resolve_early_return(
    req: Any,
    pre_bridge: chat_pre_bridge_decision.ChatPreBridgeEarlyReturn,
    *,
    callbacks: ChatPreBridgeRouteCallbacks,
) -> ChatPreBridgeRouteEarlyResponse:
    if pre_bridge.persist_answer is not None:
        callbacks.persist_chat_turn(
            req,
            pre_bridge.persist_answer,
            guardrail_status=pre_bridge.persist_guardrail_status,
            timing_meta=pre_bridge.persist_timing_meta,
        )

    if pre_bridge.status == "ok":
        outcome = "respond"
    elif pre_bridge.status in {"no_reply", "wait"}:
        outcome = pre_bridge.status
    else:
        outcome = "silent"
    raw_reply = (
        pre_bridge.persist_answer
        if pre_bridge.persist_answer is not None
        else pre_bridge.answer
    )

    return ChatPreBridgeRouteEarlyResponse(
        payload=callbacks.chat_response_payload(
            req,
            status=pre_bridge.status,
            reason=pre_bridge.reason,
            answer=pre_bridge.answer,
            source=pre_bridge.source,
            intent=pre_bridge.intent,
            guardrail_status=pre_bridge.guardrail_status,
            include_answer_chunks=True,
        ),
        completion=chat_response_contract.build_completed_inbound_response(
            outcome=outcome,
            reply=raw_reply if outcome == "respond" else "",
            reason=pre_bridge.reason,
            source=pre_bridge.source,
            intent=pre_bridge.intent,
            guardrail_status=pre_bridge.guardrail_status,
        ),
    )


async def _resolve_continue(
    req: Any,
    pre_bridge: chat_pre_bridge_decision.ChatPreBridgeContinue,
    *,
    callbacks: ChatPreBridgeRouteCallbacks,
) -> ChatPreBridgeRouteEarlyResponse | ChatPreBridgeRouteContinue:
    persist_req = callbacks.clone_chat_request(
        req,
        query=pre_bridge.final_query,
        files=pre_bridge.final_files,
    )

    if pre_bridge.classifier_ran and pre_bridge.guardrail_status == "silent":
        await callbacks.finalize_private_buffer(req.user_id)
        callbacks.persist_chat_turn(
            persist_req,
            "（数据中转，自动静默）",
            pre_bridge.guardrail_status,
            timing_meta=pre_bridge.private_timing_meta,
        )
        return ChatPreBridgeRouteEarlyResponse(
            payload=callbacks.chat_response_payload(
                req,
                status="silent",
                reason="guardrail_silent",
                guardrail_status=pre_bridge.guardrail_status,
                include_answer_chunks=True,
            ),
            completion=chat_response_contract.build_completed_inbound_response(
                outcome="silent",
                reason="guardrail_silent",
                guardrail_status=pre_bridge.guardrail_status,
            ),
        )

    return ChatPreBridgeRouteContinue(
        final_query=pre_bridge.final_query,
        final_files=pre_bridge.final_files,
        private_decision=pre_bridge.private_decision,
        private_timing_meta=pre_bridge.private_timing_meta,
        guardrail_status=pre_bridge.guardrail_status,
        classifier_ran=pre_bridge.classifier_ran,
        persist_req=persist_req,
    )


async def resolve_pre_bridge_route_result(
    req: Any,
    pre_bridge: Any,
    *,
    callbacks: ChatPreBridgeRouteCallbacks,
) -> ChatPreBridgeRouteEarlyResponse | ChatPreBridgeRouteContinue:
    if isinstance(pre_bridge, chat_pre_bridge_decision.ChatPreBridgeEarlyReturn):
        return await _resolve_early_return(req, pre_bridge, callbacks=callbacks)
    if isinstance(pre_bridge, chat_pre_bridge_decision.ChatPreBridgeContinue):
        return await _resolve_continue(req, pre_bridge, callbacks=callbacks)
    raise TypeError(f"unsupported pre_bridge result: {type(pre_bridge)!r}")

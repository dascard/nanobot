"""Chat 私聊 pre-bridge 决策编排。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from api import chat_private_buffer


@dataclass(frozen=True)
class ChatPreBridgeServices:
    private_buffer_store: Any
    private_buffer_config: Callable[[], Any]
    private_buffer_follower_timeout_seconds: float
    now: Callable[[], float]
    sleep: Callable[[float], Awaitable[None]]
    wait_private_buffer_deadline: Callable[[str], Awaitable[bool]]
    finalize_private_buffer: Callable[[str], Awaitable[None]]
    normalize_files: Callable[[Any], list[str]]
    join_buffered_messages: Callable[[Sequence[str]], str]
    build_guardrail_input: Callable[[str, Any], str]
    get_guardrail: Callable[[], Any]
    detect_guardrail: Callable[[Any, str, bool], dict[str, Any]]
    guardrail_status_from_result: Callable[[dict[str, Any] | None], str]
    is_guardrail_superuser: Callable[[str], bool]
    get_private_gate: Callable[[], Any]
    get_casual_reply: Callable[[str, bool], str]
    private_timing_meta: Callable[[Any | None], dict[str, Any] | None]
    logger: Any


@dataclass(frozen=True)
class ChatPreBridgeEarlyReturn:
    status: str
    reason: str = ""
    answer: str = ""
    source: str = ""
    intent: str = ""
    guardrail_status: str | None = None
    persist_answer: str | None = None
    persist_guardrail_status: str | None = None
    persist_timing_meta: dict[str, Any] | None = None


@dataclass(frozen=True)
class ChatPreBridgeContinue:
    final_query: str
    final_files: list[str]
    private_decision: Any | None
    private_timing_meta: dict[str, Any] | None
    guardrail_status: str | None
    classifier_ran: bool


async def _classify_private_timing(
    req: Any,
    *,
    is_superuser: bool,
    services: ChatPreBridgeServices,
) -> tuple[
    Any | None,
    dict[str, Any] | None,
    ChatPreBridgeEarlyReturn | None,
    str | None,
    list[str] | None,
]:
    private_decision = None
    private_timing_meta = None
    buffered_query = None
    buffered_files = None

    try:
        private_gate = services.get_private_gate()
        try:
            private_decision = await private_gate.classify(
                req.query,
                user_id=req.user_id,
                has_files=bool(req.files),
                is_superuser=is_superuser,
            )
        except TypeError as exc:
            if "is_superuser" not in str(exc):
                raise
            private_decision = await private_gate.classify(
                req.query,
                user_id=req.user_id,
                has_files=bool(req.files),
            )

        private_timing_meta = services.private_timing_meta(private_decision)
        if private_decision.action == "no_reply":
            return (
                private_decision,
                private_timing_meta,
                ChatPreBridgeEarlyReturn(
                    status="no_reply",
                    reason=private_decision.reason,
                    persist_answer="",
                    persist_guardrail_status=None,
                    persist_timing_meta=private_timing_meta,
                ),
                None,
                None,
            )

        if private_decision.effort == "casual":
            reply = services.get_casual_reply(req.query, is_superuser)
            answer = reply if reply else ("你先说事" if req.query else "")
            return (
                private_decision,
                private_timing_meta,
                ChatPreBridgeEarlyReturn(
                    status="ok",
                    answer=answer,
                    source="casual_template",
                    intent=private_decision.reason,
                    guardrail_status="casual_template",
                    persist_answer=answer,
                    persist_guardrail_status="casual_template",
                    persist_timing_meta=private_timing_meta,
                ),
                None,
                None,
            )

        if private_decision.action == "reply_now":
            messages = req.merged_messages or [req.query]
            buffered_query = services.join_buffered_messages(messages)
            buffered_files = services.normalize_files(req.files)
    except Exception as exc:
        services.logger.warning(
            "[/chat] PrivateGate classify failed user=%s: %s",
            req.user_id,
            exc,
        )

    return private_decision, private_timing_meta, None, buffered_query, buffered_files


async def _run_guardrail_buffer(
    req: Any,
    *,
    services: ChatPreBridgeServices,
) -> tuple[str | None, list[str] | None, str | None, ChatPreBridgeEarlyReturn | None]:
    guardrail = services.get_guardrail()
    messages = req.merged_messages or [req.query]
    merged = services.join_buffered_messages(messages)
    guardrail_input = services.build_guardrail_input(merged, req.files)

    def guardrail_task_factory() -> asyncio.Task[Any]:
        return asyncio.create_task(
            asyncio.to_thread(
                services.detect_guardrail,
                guardrail,
                guardrail_input,
                services.is_guardrail_superuser(req.user_id),
            )
        )

    buffer_result = await services.private_buffer_store.begin_or_append(
        req.user_id,
        merged_query=merged,
        files=services.normalize_files(req.files),
        guardrail_task_factory=guardrail_task_factory,
        now=services.now(),
        config=services.private_buffer_config(),
    )

    if isinstance(buffer_result, chat_private_buffer.PrivateBufferFollowerJoined):
        try:
            await asyncio.wait_for(
                buffer_result.done_event.wait(),
                timeout=services.private_buffer_follower_timeout_seconds,
            )
        except asyncio.TimeoutError:
            services.logger.warning(
                "[/chat] Private buffer follower timed out: user=%s",
                req.user_id,
            )
            await services.finalize_private_buffer(req.user_id)
        return (
            None,
            None,
            None,
            ChatPreBridgeEarlyReturn(
                status="silent",
                reason="private_buffer_follower",
            ),
        )

    if not await services.wait_private_buffer_deadline(req.user_id):
        return (
            None,
            None,
            None,
            ChatPreBridgeEarlyReturn(
                status="silent",
                reason="private_buffer_missing",
            ),
        )

    snapshot = await services.private_buffer_store.snapshot(req.user_id)
    if snapshot is None:
        return (
            None,
            None,
            None,
            ChatPreBridgeEarlyReturn(
                status="silent",
                reason="private_buffer_missing",
            ),
        )

    buffered_messages = snapshot.messages
    buffered_files = snapshot.files
    buffered_query = services.join_buffered_messages(buffered_messages)
    buffered_guardrail_input = services.build_guardrail_input(buffered_query, buffered_files)
    if len(buffered_messages) > 1:
        result = await asyncio.to_thread(
            services.detect_guardrail,
            guardrail,
            buffered_guardrail_input,
            services.is_guardrail_superuser(req.user_id),
        )
    else:
        result = await snapshot.guardrail_task

    await services.private_buffer_store.store_guardrail_result(req.user_id, result)
    guardrail_status = services.guardrail_status_from_result(result)
    services.logger.info(
        "[/chat] Guardrail result: injection=%s, passthrough=%s, user=%s",
        result.get("injection", False),
        result.get("passthrough", False),
        req.user_id,
    )
    return buffered_query, buffered_files, guardrail_status, None


async def resolve_chat_pre_bridge_decision(
    req: Any,
    *,
    is_group: bool,
    is_superuser: bool,
    services: ChatPreBridgeServices,
) -> ChatPreBridgeEarlyReturn | ChatPreBridgeContinue:
    if is_group and not req.classification_request:
        return ChatPreBridgeContinue(
            final_query=req.query,
            final_files=services.normalize_files(req.files),
            private_decision=None,
            private_timing_meta=None,
            guardrail_status=None,
            classifier_ran=False,
        )

    guardrail_status: str | None = None
    classifier_ran = False
    buffered_query: str | None = None
    buffered_files: list[str] | None = None
    private_decision = None
    private_timing_meta: dict[str, Any] | None = None

    if not is_group and not req.classification_request:
        (
            private_decision,
            private_timing_meta,
            early_return,
            buffered_query,
            buffered_files,
        ) = await _classify_private_timing(
            req,
            is_superuser=is_superuser,
            services=services,
        )
        if early_return is not None:
            return early_return

    if not is_group or req.classification_request:
        try:
            classifier_ran = True
            (
                guardrail_buffered_query,
                guardrail_buffered_files,
                guardrail_status,
                early_return,
            ) = await _run_guardrail_buffer(req, services=services)
            if early_return is not None:
                return early_return
            buffered_query = guardrail_buffered_query or buffered_query
            buffered_files = (
                guardrail_buffered_files
                if guardrail_buffered_files is not None
                else buffered_files
            )
        except asyncio.CancelledError:
            await services.finalize_private_buffer(req.user_id)
            raise
        except Exception:
            await services.finalize_private_buffer(req.user_id)
            raise

    final_query = buffered_query or req.query
    final_files = (
        buffered_files if buffered_files is not None else services.normalize_files(req.files)
    )
    return ChatPreBridgeContinue(
        final_query=final_query,
        final_files=final_files,
        private_decision=private_decision,
        private_timing_meta=private_timing_meta,
        guardrail_status=guardrail_status,
        classifier_ran=classifier_ran,
    )

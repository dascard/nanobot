"""聊天流式结果收尾 helper。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger("nanobot.routes")


@dataclass(frozen=True)
class ChatStreamResultCallbacks:
    drain_stream_queue_until_task_done: Callable[[asyncio.Queue[Any], asyncio.Task[Any]], Awaitable[None]]
    pop_bridge_reply_meta: Callable[[Any, str], dict[str, Any] | None]
    private_prompt_audit_failure_meta: Callable[[], dict[str, Any]]
    finalize_private_buffer: Callable[..., Awaitable[None]]
    persist_chat_turn: Callable[..., int]
    expand_chat_transport_answer: Callable[[str], str]
    build_chat_push_envelope: Callable[..., Any]
    push_envelope_to_qq: Callable[[str, str, dict[str, Any]], Awaitable[bool]]


@dataclass(frozen=True)
class ChatStreamResultContext:
    req: Any
    persist_req: Any
    bridge: Any
    result_holder: MutableMapping[str, Any]
    runner_task: asyncio.Task[Any]
    stream_queue: asyncio.Queue[Any]
    platform: str
    bridge_meta: dict[str, Any]
    guardrail_status: str | None
    private_timing_meta: dict[str, Any] | None
    empty_assistant_placeholder: str
    callbacks: ChatStreamResultCallbacks


def _request_attr(req: Any, name: str, default: Any = "") -> Any:
    return getattr(req, name, default)


def _is_prompt_audit_failed(reply_meta: dict[str, Any] | None) -> bool:
    return (reply_meta or {}).get("_agent_result") == "prompt_v2_audit_failed"


async def persist_stream_result_after_runner_done(
    context: ChatStreamResultContext,
    *,
    push: bool,
    persist_db: Any | None = None,
    drain_stream: bool = False,
) -> None:
    callbacks = context.callbacks
    drain_task = (
        asyncio.create_task(
            callbacks.drain_stream_queue_until_task_done(
                context.stream_queue,
                context.runner_task,
            )
        )
        if drain_stream
        else None
    )
    try:
        await context.runner_task
        if drain_task is not None:
            await drain_task

        final_answer = context.empty_assistant_placeholder
        assistant_meta = None
        assistant_processed = None
        should_push = False

        if "error" in context.result_holder:
            err_msg = str(context.result_holder.get("error") or "unknown")
            logger.error(
                "[/chat] Stream-aborted runner failed: user=%s, session=%s, error=%s",
                _request_attr(context.req, "user_id"),
                _request_attr(context.req, "session_id"),
                err_msg,
            )
        else:
            private_reply_meta = callbacks.pop_bridge_reply_meta(
                context.bridge,
                str(_request_attr(context.req, "session_id")),
            )
            if _is_prompt_audit_failed(private_reply_meta):
                assistant_meta = callbacks.private_prompt_audit_failure_meta()
                assistant_processed = 1
            else:
                answer = str(context.result_holder.get("answer") or "")
                if answer.strip():
                    final_answer = answer
                    should_push = push

        await callbacks.finalize_private_buffer(
            str(_request_attr(context.req, "user_id")),
            final_answer,
        )

        def _write(db_for_write: Any) -> None:
            callbacks.persist_chat_turn(
                db_for_write,
                context.persist_req,
                final_answer,
                context.guardrail_status,
                assistant_meta=assistant_meta,
                assistant_processed=assistant_processed,
                timing_meta=context.private_timing_meta,
            )

        if persist_db is not None:
            _write(persist_db)
        else:
            from core.uow import UnitOfWork

            with UnitOfWork() as uow:
                if uow.db is None:
                    raise RuntimeError("UnitOfWork session is not open")
                _write(uow.db)

        if should_push:
            push_answer = final_answer
            try:
                push_answer = callbacks.expand_chat_transport_answer(final_answer)
            except Exception:
                pass

            push_payload = callbacks.build_chat_push_envelope(
                context.req,
                answer=push_answer,
                platform=context.platform,
                chat_type=str(context.bridge_meta.get("chat_type") or ""),
                is_group=bool(context.bridge_meta.get("is_group")),
            )
            ok = await callbacks.push_envelope_to_qq(
                push_payload.target_type,
                push_payload.target_id,
                push_payload.envelope,
            )
            if ok:
                logger.info(
                    "[/chat] Stream-aborted result pushed: user=%s, len=%s",
                    _request_attr(context.req, "user_id"),
                    len(final_answer),
                )
            else:
                logger.error(
                    "[/chat] Stream-aborted result push failed: user=%s, session=%s, len=%s",
                    _request_attr(context.req, "user_id"),
                    _request_attr(context.req, "session_id"),
                    len(final_answer),
                )
    except Exception as exc:
        logger.error("[/chat] Background finish failed: %s", exc)
    finally:
        if drain_task is not None and not drain_task.done():
            drain_task.cancel()
            await asyncio.gather(drain_task, return_exceptions=True)

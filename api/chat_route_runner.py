"""聊天 route runner 编排 helper。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any

from api import (
    chat_non_streaming_result,
    chat_response_contract,
    chat_sse_loop,
    chat_streaming_helpers,
    chat_streaming_result,
)


logger = logging.getLogger("nanobot.routes")


@dataclass(frozen=True)
class ChatRouteHttpError:
    status_code: int
    detail: str


@dataclass(frozen=True)
class ChatRouteNonStreamingResult:
    payload: dict[str, Any] | None
    http_error: ChatRouteHttpError | None = None
    pending: int | None = None
    should_trigger_evolution: bool = False
    prompt_audit_failed: bool = False


@dataclass(frozen=True)
class ChatRouteRunnerCallbacks:
    call_bridge_non_streaming: Callable[..., Awaitable[Any]]
    finalize_private_buffer: Callable[..., Awaitable[None]]
    persist_chat_turn: Callable[..., int]
    pop_bridge_reply_meta: Callable[[Any, str], dict[str, Any] | None]
    private_prompt_audit_failure_meta: Callable[[], dict[str, Any]]
    expand_chat_transport_answer: Callable[[str], str]
    build_chat_push_envelope: Callable[..., Any]
    push_envelope_to_qq: Callable[[str, str, dict[str, Any]], Awaitable[bool]]
    chat_response_payload: Callable[..., dict[str, Any]]
    chat_sse_data: Callable[[dict[str, Any]], str]
    stream_error_event: Callable[[], dict[str, Any]]
    drain_stream_queue_until_task_done: Callable[[asyncio.Queue[Any], asyncio.Task[Any]], Awaitable[None]]
    finalize_non_streaming_chat_result: Callable[..., Awaitable[Any]]
    add_background_task: Callable[..., None]
    evolution_task: Callable[..., Any]


@dataclass(frozen=True)
class ChatRouteRunnerContext:
    req: Any
    persist_req: Any
    bridge: Any
    enriched_query: str
    bridge_meta: dict[str, Any]
    platform: str
    guardrail_status: str | None
    private_timing_meta: dict[str, Any] | None
    queue_maxsize: int
    empty_assistant_placeholder: str
    safe_error_message: str
    evolution_threshold: int
    callbacks: ChatRouteRunnerCallbacks


async def _run_stream_bridge(
    context: ChatRouteRunnerContext,
    result_holder: MutableMapping[str, Any],
    done: asyncio.Event,
    stream_queue: asyncio.Queue[dict[str, Any]],
) -> None:
    req = context.req
    try:
        result_holder["answer"] = await context.bridge.handle_message(
            context.enriched_query,
            user_id=req.user_id,
            session_id=req.session_id,
            sender_name=req.sender_name or "",
            metadata=context.bridge_meta,
            stream_queue=stream_queue,
            stream=True,
        )
    except Exception as exc:
        result_holder["error"] = str(exc)
    finally:
        done.set()


def _stream_result_context(
    context: ChatRouteRunnerContext,
    *,
    result_holder: MutableMapping[str, Any],
    runner_task: asyncio.Task[Any],
    stream_queue: asyncio.Queue[Any],
) -> chat_streaming_result.ChatStreamResultContext:
    callbacks = context.callbacks
    return chat_streaming_result.ChatStreamResultContext(
        req=context.req,
        persist_req=context.persist_req,
        bridge=context.bridge,
        result_holder=result_holder,
        runner_task=runner_task,
        stream_queue=stream_queue,
        platform=context.platform,
        bridge_meta=context.bridge_meta,
        guardrail_status=context.guardrail_status,
        private_timing_meta=context.private_timing_meta,
        empty_assistant_placeholder=context.empty_assistant_placeholder,
        callbacks=chat_streaming_result.ChatStreamResultCallbacks(
            drain_stream_queue_until_task_done=callbacks.drain_stream_queue_until_task_done,
            pop_bridge_reply_meta=callbacks.pop_bridge_reply_meta,
            private_prompt_audit_failure_meta=callbacks.private_prompt_audit_failure_meta,
            finalize_private_buffer=callbacks.finalize_private_buffer,
            persist_chat_turn=callbacks.persist_chat_turn,
            expand_chat_transport_answer=callbacks.expand_chat_transport_answer,
            build_chat_push_envelope=callbacks.build_chat_push_envelope,
            push_envelope_to_qq=callbacks.push_envelope_to_qq,
        ),
    )


async def iter_streaming_chat_response(
    db: Any,
    context: ChatRouteRunnerContext,
) -> AsyncIterator[str]:
    callbacks = context.callbacks
    result_holder: dict[str, Any] = {}
    done = asyncio.Event()
    stream_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=context.queue_maxsize)
    persisted = False
    runner_task = asyncio.create_task(_run_stream_bridge(context, result_holder, done, stream_queue))
    stream_result_context = _stream_result_context(
        context,
        result_holder=result_holder,
        runner_task=runner_task,
        stream_queue=stream_queue,
    )

    async def persist_stream_result_after_runner_done(
        *,
        push: bool,
        persist_db: Any | None = None,
        drain_stream: bool = False,
    ) -> None:
        await chat_streaming_result.persist_stream_result_after_runner_done(
            stream_result_context,
            push=push,
            persist_db=persist_db,
            drain_stream=drain_stream,
        )

    try:
        async for event in chat_sse_loop.iter_chat_stream_events(
            stream_queue,
            done,
            heartbeat_interval=5,
            coalescer=chat_streaming_helpers.StreamEventCoalescer(),
            callbacks=chat_sse_loop.ChatSseLoopCallbacks(
                normalize_event=chat_response_contract.normalize_chat_stream_event,
            ),
        ):
            yield callbacks.chat_sse_data(event)

        if "error" in result_holder:
            err_msg = str(result_holder.get("error") or "unknown")
            logger.error(
                "[/chat] Stream runner failed: user=%s, session=%s, error=%s",
                context.req.user_id,
                context.req.session_id,
                err_msg,
            )
            try:
                await callbacks.finalize_private_buffer(
                    context.req.user_id,
                    context.empty_assistant_placeholder,
                )
                callbacks.persist_chat_turn(
                    db,
                    context.persist_req,
                    context.empty_assistant_placeholder,
                    context.guardrail_status,
                    timing_meta=context.private_timing_meta,
                )
                persisted = True
            except Exception as exc:
                logger.error("[/chat] Stream persist failed on error path: %s", exc)
            yield callbacks.chat_sse_data(callbacks.stream_error_event())
            return

        answer = result_holder.get("answer", "")
        private_reply_meta = callbacks.pop_bridge_reply_meta(context.bridge, context.req.session_id)
        transport_answer = answer
        try:
            transport_answer = callbacks.expand_chat_transport_answer(answer)
        except Exception:
            logger.warning("[/chat] stream generated image ref expansion failed", exc_info=True)

        if (private_reply_meta or {}).get("_agent_result") == "prompt_v2_audit_failed":
            await callbacks.finalize_private_buffer(
                context.req.user_id,
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
            persisted = True
            yield callbacks.chat_sse_data(callbacks.stream_error_event())
            return

        await callbacks.finalize_private_buffer(context.req.user_id, answer)
        pending = callbacks.persist_chat_turn(
            db,
            context.persist_req,
            answer,
            context.guardrail_status,
            timing_meta=context.private_timing_meta,
        )
        persisted = True
        if pending >= context.evolution_threshold:
            callbacks.add_background_task(callbacks.evolution_task, context.req.user_id)
        done_payload = callbacks.chat_response_payload(
            context.req,
            status="done",
            answer=transport_answer,
            reply_meta=private_reply_meta,
            platform=context.platform,
            chat_type=str(context.bridge_meta.get("chat_type") or ""),
            unprocessed_logs=pending,
            guardrail_status=context.guardrail_status,
        )
        yield callbacks.chat_sse_data(done_payload)
    finally:
        if not persisted:
            if runner_task.done():
                await persist_stream_result_after_runner_done(push=False, persist_db=db)
            else:
                callbacks.add_background_task(
                    persist_stream_result_after_runner_done,
                    push=True,
                    persist_db=None,
                    drain_stream=True,
                )
                await callbacks.finalize_private_buffer(context.req.user_id)
                logger.warning(
                    "[/chat] Stream aborted, running in background: user=%s, session=%s",
                    context.req.user_id,
                    context.req.session_id,
                )
        done.set()


def _non_streaming_context(
    context: ChatRouteRunnerContext,
    *,
    answer: str,
) -> chat_non_streaming_result.ChatNonStreamingResultContext:
    callbacks = context.callbacks
    return chat_non_streaming_result.ChatNonStreamingResultContext(
        req=context.req,
        persist_req=context.persist_req,
        bridge=context.bridge,
        answer=answer,
        platform=context.platform,
        bridge_meta=context.bridge_meta,
        guardrail_status=context.guardrail_status,
        private_timing_meta=context.private_timing_meta,
        empty_assistant_placeholder=context.empty_assistant_placeholder,
        evolution_threshold=context.evolution_threshold,
        callbacks=chat_non_streaming_result.ChatNonStreamingResultCallbacks(
            pop_bridge_reply_meta=callbacks.pop_bridge_reply_meta,
            private_prompt_audit_failure_meta=callbacks.private_prompt_audit_failure_meta,
            finalize_private_buffer=callbacks.finalize_private_buffer,
            persist_chat_turn=callbacks.persist_chat_turn,
            expand_chat_transport_answer=callbacks.expand_chat_transport_answer,
            chat_response_payload=callbacks.chat_response_payload,
        ),
    )


async def run_non_streaming_chat_response(
    db: Any,
    context: ChatRouteRunnerContext,
) -> ChatRouteNonStreamingResult:
    try:
        answer = await context.callbacks.call_bridge_non_streaming(
            context.bridge,
            enriched_query=context.enriched_query,
            user_id=context.req.user_id,
            session_id=context.req.session_id,
            sender_name=context.req.sender_name or "",
            metadata=context.bridge_meta,
        )
    except asyncio.CancelledError:
        await context.callbacks.finalize_private_buffer(
            context.req.user_id,
            context.empty_assistant_placeholder,
        )
        raise
    except Exception as exc:
        logger.error("[/chat] KT Agent failed: %s", exc)
        try:
            await context.callbacks.finalize_private_buffer(
                context.req.user_id,
                context.empty_assistant_placeholder,
            )
            context.callbacks.persist_chat_turn(
                db,
                context.persist_req,
                context.empty_assistant_placeholder,
                context.guardrail_status,
                timing_meta=context.private_timing_meta,
            )
        except Exception as persist_exc:
            logger.error("[/chat] Persist failed on KT error path: %s", persist_exc)
        return ChatRouteNonStreamingResult(
            payload=None,
            http_error=ChatRouteHttpError(502, context.safe_error_message),
        )

    result = await context.callbacks.finalize_non_streaming_chat_result(
        db,
        _non_streaming_context(context, answer=answer),
    )
    if result.prompt_audit_failed:
        return ChatRouteNonStreamingResult(
            payload=None,
            http_error=ChatRouteHttpError(500, context.safe_error_message),
            prompt_audit_failed=True,
        )
    if result.should_trigger_evolution:
        context.callbacks.add_background_task(context.callbacks.evolution_task, context.req.user_id)
    return ChatRouteNonStreamingResult(
        payload=result.payload,
        pending=result.pending,
        should_trigger_evolution=result.should_trigger_evolution,
    )

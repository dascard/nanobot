"""聊天流式结果收尾 helper。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any

from api import chat_response_contract
from core import chat_delivery_service
from core.inbound_idempotency import CompletedInboundResponse


logger = logging.getLogger("nanobot.routes")


def _safe_log(method_name: str, message: str, *args: Any, **kwargs: Any) -> None:
    try:
        log_method = getattr(logger, method_name)
        log_method(message, *args, **kwargs)
    except BaseException:
        pass


async def _best_effort_fail_claim(owner: Any | None, error: BaseException) -> None:
    if owner is None:
        return
    try:
        await owner.fail(error)
    except BaseException as cleanup_error:
        _safe_log(
            "error",
            "[/chat] Stream claim cleanup failed: primary=%r cleanup=%r",
            error,
            cleanup_error,
        )


@dataclass(frozen=True)
class ChatStreamResultCallbacks:
    drain_stream_queue_until_task_done: Callable[[asyncio.Queue[Any], asyncio.Task[Any]], Awaitable[None]]
    pop_bridge_reply_meta: Callable[[Any, str], dict[str, Any] | None]
    private_prompt_audit_failure_meta: Callable[[], dict[str, Any]]
    finalize_private_buffer: Callable[..., Awaitable[None]]
    persist_chat_turn: Callable[..., int]
    expand_chat_transport_answer: Callable[[str], str]
    build_chat_push_envelope: Callable[..., Any]
    push_envelope_to_qq: Callable[[str, str, dict[str, Any]], Awaitable[bool | None]]
    persist_claimed_chat_turn: Callable[..., Any] | None = None


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
    claim_owner: Any | None = None
    claim_key: Any | None = None
    request_sha256: str = ""


@dataclass(frozen=True)
class ChatStreamFinalizationResult:
    answer: str
    transport_answer: str
    reply_meta: dict[str, Any] | None
    pending: int
    completion: CompletedInboundResponse


@dataclass(frozen=True)
class RegisteredStreamFinalizationDelivery:
    target_type: str
    target_id: str
    envelope: dict[str, Any]
    row_id: int | None = None


def _request_attr(req: Any, name: str, default: Any = "") -> Any:
    return getattr(req, name, default)


def _is_prompt_audit_failed(reply_meta: dict[str, Any] | None) -> bool:
    return (reply_meta or {}).get("_agent_result") == "prompt_v2_audit_failed"


async def _cancel_and_wait_execution_tasks(
    runner_task: asyncio.Task[Any],
    drain_task: asyncio.Task[Any] | None,
) -> None:
    tasks = [runner_task]
    if drain_task is not None:
        tasks.append(drain_task)
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _log_secondary_cleanup_error(
    primary: BaseException,
    *,
    stage: str,
    cleanup_error: BaseException,
) -> None:
    _safe_log(
        "error",
        "[/chat] Stream cleanup failed without replacing primary: stage=%s primary=%r cleanup=%r",
        stage,
        primary,
        cleanup_error,
    )


async def register_stream_finalization_delivery(
    context: ChatStreamResultContext,
    result: ChatStreamFinalizationResult,
) -> RegisteredStreamFinalizationDelivery | None:
    """构造断连投递；claimed 请求先持久登记 outbox。"""

    if not result.answer.strip():
        return None
    callbacks = context.callbacks
    push_payload = callbacks.build_chat_push_envelope(
        context.req,
        answer=result.transport_answer,
        platform=context.platform,
        chat_type=str(context.bridge_meta.get("chat_type") or ""),
        is_group=bool(context.bridge_meta.get("is_group")),
    )
    row_id = None
    if context.claim_key is not None:
        registration = await chat_delivery_service.enqueue_chat_response_delivery(
            key=context.claim_key,
            target_type=push_payload.target_type,
            target_id=push_payload.target_id,
            envelope=push_payload.envelope,
        )
        row_id = registration.row_id
    return RegisteredStreamFinalizationDelivery(
        target_type=push_payload.target_type,
        target_id=push_payload.target_id,
        envelope=push_payload.envelope,
        row_id=row_id,
    )


async def deliver_registered_stream_finalization(
    context: ChatStreamResultContext,
    _result: ChatStreamFinalizationResult,
    registered: RegisteredStreamFinalizationDelivery | None,
) -> bool:
    """发送已登记的结果；claimed 请求只领取指定 outbox 行。"""

    if registered is None:
        return False
    if registered.row_id is not None:
        delivery = await chat_delivery_service.deliver_chat_delivery(
            publisher=context.callbacks.push_envelope_to_qq,
            row_id=registered.row_id,
        )
        return bool(delivery is not None and delivery.status == "delivered")
    return bool(await context.callbacks.push_envelope_to_qq(
        registered.target_type,
        registered.target_id,
        registered.envelope,
    ))


async def push_stream_finalization_result(
    context: ChatStreamResultContext,
    result: ChatStreamFinalizationResult,
) -> bool:
    """兼容的一步式登记并投递 helper。"""

    try:
        registered = await register_stream_finalization_delivery(context, result)
        ok = await deliver_registered_stream_finalization(
            context,
            result,
            registered,
        )
    except BaseException:
        _safe_log(
            "error",
            "[/chat] Stream-aborted result push raised: user=%s, session=%s",
            _request_attr(context.req, "user_id"),
            _request_attr(context.req, "session_id"),
            exc_info=True,
        )
        return False

    if ok:
        _safe_log(
            "info",
            "[/chat] Stream-aborted result pushed: user=%s, len=%s",
            _request_attr(context.req, "user_id"),
            len(result.answer),
        )
        return True

    _safe_log(
        "error",
        "[/chat] Stream-aborted result push failed: user=%s, session=%s, len=%s",
        _request_attr(context.req, "user_id"),
        _request_attr(context.req, "session_id"),
        len(result.answer),
    )
    return False


async def persist_stream_result_after_runner_done(
    context: ChatStreamResultContext,
    *,
    push: bool,
    persist_db: Any | None = None,
    drain_stream: bool = False,
    settle_claim: bool = True,
) -> ChatStreamFinalizationResult:
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

        if "error" in context.result_holder:
            stored_error = context.result_holder.get("error")
            if isinstance(stored_error, BaseException):
                bridge_error = stored_error
            else:
                bridge_error = RuntimeError(str(stored_error or "unknown"))
            _safe_log(
                "error",
                "[/chat] Stream-aborted runner failed: user=%s, session=%s, error=%s",
                _request_attr(context.req, "user_id"),
                _request_attr(context.req, "session_id"),
                bridge_error,
            )
            try:
                await callbacks.finalize_private_buffer(
                    str(_request_attr(context.req, "user_id")),
                    context.empty_assistant_placeholder,
                )
            except BaseException as cleanup_error:
                _log_secondary_cleanup_error(
                    bridge_error,
                    stage="bridge_finalize_buffer",
                    cleanup_error=cleanup_error,
                )
            else:
                def _write_bridge_error(db_for_write: Any) -> None:
                    callbacks.persist_chat_turn(
                        db_for_write,
                        context.persist_req,
                        context.empty_assistant_placeholder,
                        context.guardrail_status,
                        assistant_meta=None,
                        assistant_processed=None,
                        timing_meta=context.private_timing_meta,
                    )

                if context.claim_key is None:
                    try:
                        if persist_db is not None:
                            _write_bridge_error(persist_db)
                        else:
                            from core.uow import UnitOfWork

                            with UnitOfWork() as uow:
                                if uow.db is None:
                                    raise RuntimeError("UnitOfWork session is not open")
                                _write_bridge_error(uow.db)
                    except BaseException as cleanup_error:
                        _log_secondary_cleanup_error(
                            bridge_error,
                            stage="bridge_persist_placeholder",
                            cleanup_error=cleanup_error,
                        )
            raise bridge_error

        private_reply_meta = callbacks.pop_bridge_reply_meta(
            context.bridge,
            str(_request_attr(context.req, "session_id")),
        )
        if _is_prompt_audit_failed(private_reply_meta):
            prompt_audit_error = RuntimeError("prompt_v2_audit_failed")
            try:
                await callbacks.finalize_private_buffer(
                    str(_request_attr(context.req, "user_id")),
                    context.empty_assistant_placeholder,
                )
            except BaseException as cleanup_error:
                _log_secondary_cleanup_error(
                    prompt_audit_error,
                    stage="audit_finalize_buffer",
                    cleanup_error=cleanup_error,
                )
            else:
                def _write_prompt_audit(db_for_write: Any) -> None:
                    callbacks.persist_chat_turn(
                        db_for_write,
                        context.persist_req,
                        context.empty_assistant_placeholder,
                        context.guardrail_status,
                        assistant_meta=callbacks.private_prompt_audit_failure_meta(),
                        assistant_processed=1,
                        timing_meta=context.private_timing_meta,
                    )

                if context.claim_key is None:
                    try:
                        if persist_db is not None:
                            _write_prompt_audit(persist_db)
                        else:
                            from core.uow import UnitOfWork

                            with UnitOfWork() as uow:
                                if uow.db is None:
                                    raise RuntimeError("UnitOfWork session is not open")
                                _write_prompt_audit(uow.db)
                    except BaseException as cleanup_error:
                        _log_secondary_cleanup_error(
                            prompt_audit_error,
                            stage="audit_persist_placeholder",
                            cleanup_error=cleanup_error,
                        )
            raise prompt_audit_error

        final_answer = str(context.result_holder.get("answer") or "")
        await callbacks.finalize_private_buffer(
            str(_request_attr(context.req, "user_id")),
            final_answer,
        )

        completion = chat_response_contract.build_completed_inbound_response(
            outcome="respond",
            reply=final_answer,
            reply_meta=private_reply_meta,
            guardrail_status=context.guardrail_status,
        )

        def _write_success(db_for_write: Any) -> tuple[int, CompletedInboundResponse]:
            if context.claim_key is not None:
                if callbacks.persist_claimed_chat_turn is None or not context.request_sha256:
                    raise RuntimeError("claimed 流式结果缺少可恢复持久化依赖")
                claimed_result = callbacks.persist_claimed_chat_turn(
                    db_for_write,
                    context.persist_req,
                    final_answer,
                    context.guardrail_status,
                    key=context.claim_key,
                    request_sha256=context.request_sha256,
                    completion=completion,
                    assistant_meta=None,
                    assistant_processed=None,
                    timing_meta=context.private_timing_meta,
                )
                return int(claimed_result.pending), claimed_result.completion
            pending = callbacks.persist_chat_turn(
                db_for_write,
                context.persist_req,
                final_answer,
                context.guardrail_status,
                assistant_meta=None,
                assistant_processed=None,
                timing_meta=context.private_timing_meta,
            )
            return pending, chat_response_contract.build_completed_inbound_response(
                outcome="respond",
                reply=final_answer,
                reply_meta=private_reply_meta,
                guardrail_status=context.guardrail_status,
                unprocessed_logs=pending,
            )

        if persist_db is not None:
            pending, completion = _write_success(persist_db)
        else:
            from core.uow import UnitOfWork

            with UnitOfWork() as uow:
                if uow.db is None:
                    raise RuntimeError("UnitOfWork session is not open")
                pending, completion = _write_success(uow.db)
        transport_answer = final_answer
        try:
            transport_answer = callbacks.expand_chat_transport_answer(final_answer)
        except Exception:
            _safe_log(
                "warning",
                "[/chat] stream generated image ref expansion failed",
                exc_info=True,
            )

        finalization_result = ChatStreamFinalizationResult(
            answer=final_answer,
            transport_answer=transport_answer,
            reply_meta=private_reply_meta,
            pending=pending,
            completion=completion,
        )
        if settle_claim and context.claim_owner is not None:
            completed = await context.claim_owner.complete(completion)
            if completed is not True:
                raise RuntimeError("流式 claim complete 未成功")

        if push:
            await push_stream_finalization_result(context, finalization_result)
        return finalization_result
    except BaseException as exc:
        await _cancel_and_wait_execution_tasks(context.runner_task, drain_task)
        await _best_effort_fail_claim(context.claim_owner, exc)
        raise
    finally:
        if drain_task is not None and not drain_task.done():
            drain_task.cancel()
            await asyncio.gather(drain_task, return_exceptions=True)

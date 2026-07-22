"""聊天 route runner 编排 helper。"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, MutableMapping
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
_STREAM_FINALIZER_TASKS: set[asyncio.Task[Any]] = set()


def _safe_log(method_name: str, message: str, *args: Any, **kwargs: Any) -> None:
    try:
        log_method = getattr(logger, method_name)
        log_method(message, *args, **kwargs)
    except BaseException:
        pass


async def _resolve_maybe_awaitable(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _observe_stream_finalizer(task: asyncio.Task[Any]) -> None:
    """取走后台 finalizer 异常并在完成后释放强引用。"""

    try:
        if task.cancelled():
            _safe_log("warning", "[/chat] Stream finalizer task was cancelled: name=%s", task.get_name())
        else:
            error = task.exception()
            if error is not None:
                _safe_log(
                    "error",
                    "[/chat] Stream finalizer failed: name=%s error=%s",
                    task.get_name(),
                    error,
                    exc_info=(type(error), error, error.__traceback__),
                )
    finally:
        _STREAM_FINALIZER_TASKS.discard(task)


def _register_stream_finalizer(
    coroutine: Coroutine[Any, Any, Any],
    *,
    name: str,
) -> asyncio.Task[Any]:
    """注册一个受强引用保护且异常可观察的 stream finalizer。"""

    task = asyncio.create_task(coroutine, name=name)
    _STREAM_FINALIZER_TASKS.add(task)
    task.add_done_callback(_observe_stream_finalizer)
    return task


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
    push_envelope_to_qq: Callable[[str, str, dict[str, Any]], Awaitable[bool | None]]
    chat_response_payload: Callable[..., dict[str, Any]]
    chat_sse_data: Callable[[dict[str, Any]], str]
    stream_error_event: Callable[[], dict[str, Any]]
    drain_stream_queue_until_task_done: Callable[[asyncio.Queue[Any], asyncio.Task[Any]], Awaitable[None]]
    finalize_non_streaming_chat_result: Callable[..., Awaitable[Any]]
    add_background_task: Callable[..., None]
    evolution_task: Callable[..., Any]
    persist_claimed_chat_turn: Callable[..., Any] | None = None


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
    claim_owner: Any | None = None
    claim_key: Any | None = None
    request_sha256: str = ""


async def _best_effort_fail_claim(owner: Any | None, error: Any) -> None:
    if owner is None:
        return
    try:
        await owner.fail(error)
    except BaseException as cleanup_error:
        _safe_log(
            "error",
            "[/chat] Claim fail cleanup failed: primary=%r cleanup=%r",
            error,
            cleanup_error,
        )


class ColdChatStreamingBody(AsyncIterator[str]):
    """首次拉取 body 时才启动 claim 续租和 Bridge runner。"""

    def __init__(self, context: ChatRouteRunnerContext) -> None:
        self._context = context
        self._iterator: AsyncIterator[str] | None = None
        self._state_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._active_pull_task: asyncio.Task[Any] | None = None
        self._inner_started = asyncio.Event()
        self._claim_fail_task: asyncio.Task[None] | None = None
        self._closing = False
        self._closed = False

    def __aiter__(self) -> ColdChatStreamingBody:
        return self

    async def _fail_claim_once(self, error: BaseException) -> None:
        async with self._state_lock:
            task = self._claim_fail_task
            if task is None:
                task = asyncio.create_task(
                    _best_effort_fail_claim(self._context.claim_owner, error),
                    name="chat-cold-body-fail",
                )
                self._claim_fail_task = task
        await asyncio.shield(task)

    async def __anext__(self) -> str:
        current = asyncio.current_task()
        if current is None:
            raise RuntimeError("流式 body pull 缺少当前 task")
        async with self._state_lock:
            if self._closing or self._closed:
                raise StopAsyncIteration
            active = self._active_pull_task
            if active is not None and not active.done() and active is not current:
                raise RuntimeError("流式 body 不支持并发拉取")
            self._active_pull_task = current
            iterator = self._iterator
        try:
            if iterator is None:
                if self._context.claim_owner is not None:
                    await self._context.claim_owner.resume()
                async with self._state_lock:
                    if self._closing or self._closed:
                        raise asyncio.CancelledError("流式 body 在 resume 期间关闭")
                    if self._iterator is None:
                        self._iterator = iter_streaming_chat_response(
                            None,
                            self._context,
                            lifecycle_started=self._inner_started,
                        )
                    iterator = self._iterator
            return await iterator.__anext__()
        except StopAsyncIteration:
            async with self._state_lock:
                self._closed = True
            raise
        except BaseException as exc:
            if not self._inner_started.is_set():
                await self._fail_claim_once(exc)
            async with self._state_lock:
                if not self._closing:
                    self._closed = True
            raise
        finally:
            async with self._state_lock:
                if self._active_pull_task is current:
                    self._active_pull_task = None

    async def aclose(self) -> None:
        async with self._close_lock:
            async with self._state_lock:
                if self._closed:
                    return
                self._closing = True
                active_pull_task = self._active_pull_task

            try:
                current = asyncio.current_task()
                if (
                    active_pull_task is not None
                    and active_pull_task is not current
                    and not active_pull_task.done()
                ):
                    active_pull_task.cancel()
                    await asyncio.gather(active_pull_task, return_exceptions=True)

                async with self._state_lock:
                    iterator = self._iterator
                inner_started = self._inner_started.is_set()
                close = getattr(iterator, "aclose", None)
                if iterator is not None and close is not None:
                    await close()
                if not inner_started:
                    await self._fail_claim_once(
                        RuntimeError("流式响应在 inner 启动前关闭"),
                    )
            finally:
                async with self._state_lock:
                    self._closing = False
                    self._closed = True


async def _await_with_owner_guard(
    owner: Any | None,
    awaitable: Awaitable[Any],
) -> Any:
    """在 Bridge 前后 checkpoint，并在执行期间响应 owner 失权。"""

    checkpoint = getattr(owner, "checkpoint", None)
    wait_unusable = getattr(owner, "wait_unusable", None)
    if owner is None or not callable(checkpoint) or not callable(wait_unusable):
        return await awaitable

    try:
        await checkpoint()
    except BaseException:
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise
    business_task = asyncio.ensure_future(awaitable)
    unusable_task = asyncio.create_task(
        wait_unusable(),
        name="chat-owner-unusable-guard",
    )
    try:
        done, _pending = await asyncio.wait(
            {business_task, unusable_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if unusable_task in done:
            try:
                await unusable_task
            except BaseException as owner_error:
                if not business_task.done():
                    business_task.cancel()
                await asyncio.gather(business_task, return_exceptions=True)
                raise owner_error
            raise RuntimeError("claim owner unusable guard 意外正常返回")

        result = await business_task
        await checkpoint()
        return result
    finally:
        for task in (business_task, unusable_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(
            business_task,
            unusable_task,
            return_exceptions=True,
        )


async def _run_stream_bridge(
    context: ChatRouteRunnerContext,
    result_holder: MutableMapping[str, Any],
    done: asyncio.Event,
    stream_queue: asyncio.Queue[dict[str, Any]],
) -> None:
    req = context.req
    try:
        result_holder["answer"] = await _await_with_owner_guard(
            context.claim_owner,
            context.bridge.handle_message(
                context.enriched_query,
                user_id=req.user_id,
                session_id=req.session_id,
                sender_name=req.sender_name or "",
                metadata=context.bridge_meta,
                stream_queue=stream_queue,
                stream=True,
            ),
        )
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        result_holder["error"] = exc
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
            persist_claimed_chat_turn=callbacks.persist_claimed_chat_turn,
        ),
        claim_owner=context.claim_owner,
        claim_key=context.claim_key,
        request_sha256=context.request_sha256,
    )


async def iter_streaming_chat_response(
    db: Any,
    context: ChatRouteRunnerContext,
    *,
    lifecycle_started: asyncio.Event | None = None,
) -> AsyncIterator[str]:
    callbacks = context.callbacks
    result_holder: dict[str, Any] = {}
    done = asyncio.Event()
    stream_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=context.queue_maxsize)
    runner_task = asyncio.create_task(_run_stream_bridge(context, result_holder, done, stream_queue))
    stream_result_context = _stream_result_context(
        context,
        result_holder=result_holder,
        runner_task=runner_task,
        stream_queue=stream_queue,
    )
    finalizer_task: asyncio.Task[Any] | None = None
    delivery_task: asyncio.Task[Any] | None = None
    done_handed_off = False
    suppress_abort_delivery = False
    claim_completion_task: asyncio.Task[bool] | None = None
    claim_completion_succeeded = context.claim_owner is None

    async def complete_claim_once(result: Any) -> bool:
        nonlocal claim_completion_task, claim_completion_succeeded
        if context.claim_owner is None:
            return True
        if claim_completion_task is None:
            async def settle_claim() -> bool:
                completed = await context.claim_owner.complete(result.completion)
                return completed is True

            claim_completion_task = asyncio.create_task(
                settle_claim(),
                name=(
                    "chat-stream-claim-complete:"
                    f"{context.req.message_id or context.req.session_id}"
                ),
            )
        try:
            claim_completion_succeeded = await asyncio.shield(
                claim_completion_task
            )
        except asyncio.CancelledError as exc:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            raise RuntimeError("流式 claim complete task 被取消") from exc
        return claim_completion_succeeded

    def ensure_finalizer(
        *,
        drain_stream: bool = False,
    ) -> asyncio.Task[Any]:
        nonlocal finalizer_task
        if finalizer_task is None:
            finalizer_task = _register_stream_finalizer(
                chat_streaming_result.persist_stream_result_after_runner_done(
                    stream_result_context,
                    push=False,
                    persist_db=None,
                    drain_stream=drain_stream,
                    settle_claim=False,
                ),
                name=f"chat-stream-finalizer:{context.req.message_id or context.req.session_id}",
            )
        return finalizer_task

    async def deliver_after_finalizer(task: asyncio.Task[Any]) -> bool:
        try:
            result = await asyncio.shield(task)
        except BaseException as exc:
            _safe_log(
                "error",
                "[/chat] Stream delivery skipped after finalizer failure: user=%s session=%s error=%r",
                context.req.user_id,
                context.req.session_id,
                exc,
            )
            return False
        try:
            if (
                context.claim_owner is not None
                and claim_completion_task is not None
                and not claim_completion_succeeded
                and await complete_claim_once(result) is not True
            ):
                raise RuntimeError("断连流式 claim complete 未成功")
            registered = await (
                chat_streaming_result.register_stream_finalization_delivery(
                    stream_result_context,
                    result,
                )
            )
            if context.claim_owner is not None and not claim_completion_succeeded:
                if await complete_claim_once(result) is not True:
                    raise RuntimeError("断连流式 claim complete 未成功")
        except BaseException as exc:
            await _best_effort_fail_claim(context.claim_owner, exc)
            _safe_log(
                "error",
                "[/chat] Stream delivery registration/claim settlement failed: user=%s session=%s error=%r",
                context.req.user_id,
                context.req.session_id,
                exc,
            )
            return False
        try:
            return await (
                chat_streaming_result.deliver_registered_stream_finalization(
                    stream_result_context,
                    result,
                    registered,
                )
            )
        except BaseException as exc:
            _safe_log(
                "error",
                "[/chat] Registered stream delivery failed: user=%s session=%s error=%r",
                context.req.user_id,
                context.req.session_id,
                exc,
            )
            return False

    def ensure_delivery(task: asyncio.Task[Any]) -> asyncio.Task[Any]:
        nonlocal delivery_task
        if delivery_task is None:
            delivery_task = _register_stream_finalizer(
                deliver_after_finalizer(task),
                name=f"chat-stream-delivery:{context.req.message_id or context.req.session_id}",
            )
        return delivery_task

    try:
        if lifecycle_started is not None:
            lifecycle_started.set()
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

        try:
            finalization_result = await asyncio.shield(
                ensure_finalizer(
                    drain_stream=False,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _safe_log(
                "error",
                "[/chat] Stream finalization failed: user=%s, session=%s, error=%s",
                context.req.user_id,
                context.req.session_id,
                exc,
            )
            yield callbacks.chat_sse_data(callbacks.stream_error_event())
            return

        if context.claim_owner is not None:
            try:
                if await complete_claim_once(finalization_result) is not True:
                    raise RuntimeError("流式 claim complete 未成功")
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                suppress_abort_delivery = True
                await _best_effort_fail_claim(context.claim_owner, exc)
                _safe_log(
                    "error",
                    "[/chat] Stream claim settlement failed before done delivery: user=%s session=%s error=%r",
                    context.req.user_id,
                    context.req.session_id,
                    exc,
                )
                yield callbacks.chat_sse_data(callbacks.stream_error_event())
                return
        done_payload = callbacks.chat_response_payload(
            context.req,
            status="done",
            answer=finalization_result.transport_answer,
            reply_meta=finalization_result.reply_meta,
            platform=context.platform,
            chat_type=str(context.bridge_meta.get("chat_type") or ""),
            unprocessed_logs=finalization_result.pending,
            guardrail_status=context.guardrail_status,
        )
        done_event = callbacks.chat_sse_data(done_payload)
        done_handed_off = True
        yield done_event
        if finalization_result.pending >= context.evolution_threshold:
            callbacks.add_background_task(
                callbacks.evolution_task,
                context.req.user_id,
            )
    finally:
        if not done_handed_off and not suppress_abort_delivery:
            task = ensure_finalizer(drain_stream=finalizer_task is None)
            ensure_delivery(task)
            _safe_log(
                "warning",
                "[/chat] Stream aborted, running owned finalizer/delivery: user=%s, session=%s",
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
            persist_claimed_chat_turn=callbacks.persist_claimed_chat_turn,
        ),
        claim_key=context.claim_key,
        request_sha256=context.request_sha256,
    )


async def run_non_streaming_chat_response(
    db: Any,
    context: ChatRouteRunnerContext,
) -> ChatRouteNonStreamingResult:
    try:
        answer = await _await_with_owner_guard(
            context.claim_owner,
            context.callbacks.call_bridge_non_streaming(
                context.bridge,
                enriched_query=context.enriched_query,
                user_id=context.req.user_id,
                session_id=context.req.session_id,
                sender_name=context.req.sender_name or "",
                metadata=context.bridge_meta,
            ),
        )
    except Exception as exc:
        _safe_log("error", "[/chat] KT Agent failed: %s", exc)
        try:
            await context.callbacks.finalize_private_buffer(
                context.req.user_id,
                context.empty_assistant_placeholder,
            )
            if context.claim_key is None:
                await _resolve_maybe_awaitable(
                    context.callbacks.persist_chat_turn(
                        db,
                        context.persist_req,
                        context.empty_assistant_placeholder,
                        context.guardrail_status,
                        timing_meta=context.private_timing_meta,
                    )
                )
        except BaseException as persist_exc:
            _safe_log("error", "[/chat] Persist failed on KT error path: %r", persist_exc)
        await _best_effort_fail_claim(context.claim_owner, exc)
        return ChatRouteNonStreamingResult(
            payload=None,
            http_error=ChatRouteHttpError(502, context.safe_error_message),
        )
    except BaseException as exc:
        try:
            await context.callbacks.finalize_private_buffer(
                context.req.user_id,
                context.empty_assistant_placeholder,
            )
        except BaseException as cleanup_error:
            _safe_log(
                "error",
                "[/chat] Fatal Bridge buffer cleanup failed: primary=%r cleanup=%r",
                exc,
                cleanup_error,
            )
        await _best_effort_fail_claim(context.claim_owner, exc)
        raise

    try:
        result = await context.callbacks.finalize_non_streaming_chat_result(
            db,
            _non_streaming_context(context, answer=answer),
        )
    except BaseException as exc:
        await _best_effort_fail_claim(context.claim_owner, exc)
        raise

    if result.prompt_audit_failed:
        await _best_effort_fail_claim(
            context.claim_owner,
            RuntimeError("prompt_v2_audit_failed"),
        )
        return ChatRouteNonStreamingResult(
            payload=None,
            http_error=ChatRouteHttpError(500, context.safe_error_message),
            prompt_audit_failed=True,
        )

    if context.claim_owner is not None:
        if result.completion is None:
            completion_error = RuntimeError("非流式成功结果缺少 claim completion")
            await _best_effort_fail_claim(context.claim_owner, completion_error)
            raise completion_error
        try:
            completed = await context.claim_owner.complete(result.completion)
            if completed is not True:
                raise RuntimeError("非流式 claim complete 未成功")
        except BaseException as exc:
            await _best_effort_fail_claim(context.claim_owner, exc)
            raise

    if result.should_trigger_evolution:
        context.callbacks.add_background_task(context.callbacks.evolution_task, context.req.user_id)
    return ChatRouteNonStreamingResult(
        payload=result.payload,
        pending=result.pending,
        should_trigger_evolution=result.should_trigger_evolution,
    )

"""聊天 SSE 事件循环 helper。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from api.chat_streaming_helpers import (
    StreamEventCoalescer,
    collect_ready_stream_events,
)


@dataclass(frozen=True)
class ChatSseLoopCallbacks:
    normalize_event: Callable[[Any], dict[str, Any] | None]


async def _yield_ready_events(
    raw_event: Any,
    stream_queue: asyncio.Queue[Any],
    *,
    coalescer: StreamEventCoalescer,
    callbacks: ChatSseLoopCallbacks,
) -> AsyncIterator[dict[str, Any]]:
    events = collect_ready_stream_events(
        raw_event,
        stream_queue,
        normalize_event=callbacks.normalize_event,
        coalescer=coalescer,
    )
    for event in events:
        yield event


async def iter_chat_stream_events(
    stream_queue: asyncio.Queue[Any],
    done: asyncio.Event,
    *,
    heartbeat_interval: float,
    coalescer: StreamEventCoalescer,
    callbacks: ChatSseLoopCallbacks,
) -> AsyncIterator[dict[str, Any]]:
    while True:
        if done.is_set() and stream_queue.empty():
            break

        get_task = asyncio.create_task(stream_queue.get())
        done_task = asyncio.create_task(done.wait())
        try:
            completed, pending = await asyncio.wait(
                {get_task, done_task},
                timeout=heartbeat_interval,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for pending_task in pending:
                pending_task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if not completed:
                yield {"status": "heartbeat"}
                continue

            if get_task in completed:
                async for event in _yield_ready_events(
                    get_task.result(),
                    stream_queue,
                    coalescer=coalescer,
                    callbacks=callbacks,
                ):
                    yield event
                continue

            if done_task in completed:
                break
        finally:
            for task in (get_task, done_task):
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    await asyncio.sleep(0)
    while True:
        try:
            event = stream_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        async for ready_event in _yield_ready_events(
            event,
            stream_queue,
            coalescer=coalescer,
            callbacks=callbacks,
        ):
            yield ready_event

    pending_delta = coalescer.flush()
    if pending_delta is not None:
        yield pending_delta

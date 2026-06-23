"""聊天流式事件辅助函数。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any


class StreamEventCoalescer:
    def __init__(self) -> None:
        self._delta_parts: list[str] = []

    def feed(self, event: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        if event is None:
            return []

        if event.get("status") == "delta":
            self._delta_parts.append(str(event.get("text") or ""))
            return []

        output: list[dict[str, Any]] = []
        pending_delta = self.flush()
        if pending_delta is not None:
            output.append(pending_delta)
        output.append(dict(event))
        return output

    def flush(self) -> dict[str, str] | None:
        if not self._delta_parts:
            return None
        text = "".join(self._delta_parts)
        self._delta_parts.clear()
        return {"status": "delta", "text": text}


def collect_ready_stream_events(
    raw_event: Any,
    stream_queue: asyncio.Queue[Any],
    *,
    normalize_event: Callable[[Any], dict[str, Any] | None],
    coalescer: StreamEventCoalescer,
) -> list[dict[str, Any]]:
    event = normalize_event(raw_event)
    output = coalescer.feed(event)
    if event is None or event.get("status") != "delta":
        return output

    while True:
        try:
            next_raw = stream_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        output.extend(coalescer.feed(normalize_event(next_raw)))

    pending_delta = coalescer.flush()
    if pending_delta is not None:
        output.append(pending_delta)
    return output


async def drain_stream_queue_until_task_done(
    stream_queue: asyncio.Queue[Any],
    runner_task: asyncio.Task[Any],
    *,
    poll_timeout: float = 0.1,
) -> None:
    while True:
        while True:
            try:
                stream_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        if runner_task.done():
            return

        try:
            await asyncio.wait_for(stream_queue.get(), timeout=poll_timeout)
        except asyncio.TimeoutError:
            continue

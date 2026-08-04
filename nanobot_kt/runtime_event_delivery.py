"""Bridge 类型化 Runtime 事件的持久化与旧 SSE 投影。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from core.agent_runtime import RuntimeRunEvent, RuntimeRunEventKind


RuntimeEventHandler = Callable[[RuntimeRunEvent], Awaitable[None]]


def create_default_run_event_sink() -> Any:
    """延迟创建生产 Ledger Sink，保留测试替换 ``_run_event_sink``。"""

    from core.run_ledger.sinks import default_runtime_run_event_sink

    return default_runtime_run_event_sink()


def build_runtime_event_handler(
    owner: object,
    stream_queue: asyncio.Queue[dict[str, Any]] | None = None,
) -> RuntimeEventHandler:
    """生成先持久化、后投影旧 SSE 的单一事件处理器。"""

    async def handle(event: RuntimeRunEvent) -> None:
        durable_sink = getattr(owner, "_run_event_sink", None)
        if durable_sink is not None:
            await durable_sink.append(event)
        if stream_queue is None:
            return
        if event.kind is RuntimeRunEventKind.TEXT_DELTA and event.text_delta:
            await stream_queue.put({"status": "delta", "text": event.text_delta})
        elif event.kind is RuntimeRunEventKind.ERROR and event.error is not None:
            await stream_queue.put({
                "status": "error",
                "message": event.error.message,
            })

    return handle


__all__ = [
    "RuntimeEventHandler",
    "build_runtime_event_handler",
    "create_default_run_event_sink",
]

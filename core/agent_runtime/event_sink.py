"""类型化 Run 事件 Sink 的框架无关实现。"""

from __future__ import annotations

import asyncio

from core.agent_runtime.contracts import RuntimeRunEvent


class InMemoryRunEventSink:
    """测试与进程内投影使用的严格去重 Sink，不提供持久化保证。"""

    def __init__(self) -> None:
        self._events: list[RuntimeRunEvent] = []
        self._event_ids: set[str] = set()
        self._lock = asyncio.Lock()

    async def append(self, event: RuntimeRunEvent) -> None:
        if not isinstance(event, RuntimeRunEvent):
            raise TypeError("RunEventSink 只接受 RuntimeRunEvent")
        async with self._lock:
            if event.event_id in self._event_ids:
                raise ValueError(f"Run 事件重复写入：{event.event_id}")
            self._events.append(event)
            self._event_ids.add(event.event_id)

    @property
    def events(self) -> tuple[RuntimeRunEvent, ...]:
        return tuple(self._events)


__all__ = ["InMemoryRunEventSink"]

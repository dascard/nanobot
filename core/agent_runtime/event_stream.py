"""Runtime 瞬时事件的顺序生成与异步流转发。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from datetime import datetime, timezone
import inspect
from typing import TypeVar
from uuid import uuid4

from core.agent_runtime.contracts import (
    RuntimeArtifactRef,
    RuntimeAttribute,
    RuntimeRunError,
    RuntimeRunEvent,
    RuntimeRunEventHandler,
    RuntimeRunEventKind,
    RuntimeRunIdentity,
    RuntimeRunStatus,
    RuntimeToolCall,
    RuntimeUsage,
    validate_run_status_transition,
)


_ResultT = TypeVar("_ResultT")
_STREAM_END = object()


class RuntimeRunEventEmitter:
    """为一次 Runtime 调用分配严格递增序号并校验状态迁移。"""

    def __init__(
        self,
        identity: RuntimeRunIdentity,
        handler: RuntimeRunEventHandler,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._identity = identity
        self._handler = handler
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._emission_id = uuid4().hex
        self._sequence = 0
        self._status: RuntimeRunStatus | None = None

    @property
    def status(self) -> RuntimeRunStatus | None:
        return self._status

    async def status_changed(self, status: RuntimeRunStatus) -> RuntimeRunEvent:
        target = RuntimeRunStatus(status)
        if target.is_terminal:
            raise ValueError("终态必须通过 end() 发出")
        if self._status is not None:
            validate_run_status_transition(self._status, target)
        event = await self._emit(RuntimeRunEventKind.STATUS, target)
        self._status = target
        return event

    async def text_delta(self, text: str) -> RuntimeRunEvent:
        return await self._emit(
            RuntimeRunEventKind.TEXT_DELTA,
            self._active_status(),
            text_delta=text,
        )

    async def tool_activity(self, tool_call: RuntimeToolCall) -> RuntimeRunEvent:
        return await self._emit(
            RuntimeRunEventKind.TOOL_ACTIVITY,
            self._active_status(),
            tool_call=tool_call,
        )

    async def usage(self, usage: RuntimeUsage) -> RuntimeRunEvent:
        return await self._emit(
            RuntimeRunEventKind.USAGE,
            self._active_status(),
            usage=usage,
        )

    async def artifact(self, artifact: RuntimeArtifactRef) -> RuntimeRunEvent:
        return await self._emit(
            RuntimeRunEventKind.ARTIFACT,
            self._active_status(),
            artifact=artifact,
        )

    async def error(self, error: RuntimeRunError) -> RuntimeRunEvent:
        return await self._emit(
            RuntimeRunEventKind.ERROR,
            self._active_status(),
            error=error,
        )

    async def end(
        self,
        status: RuntimeRunStatus,
        *,
        attributes: tuple[RuntimeAttribute, ...] = (),
    ) -> RuntimeRunEvent:
        target = RuntimeRunStatus(status)
        if not target.is_terminal:
            raise ValueError("end() 只能发出终态")
        if self._status is None:
            raise ValueError("end() 前必须先发出状态事件")
        validate_run_status_transition(self._status, target)
        event = await self._emit(
            RuntimeRunEventKind.END,
            target,
            attributes=attributes,
        )
        self._status = target
        return event

    def _active_status(self) -> RuntimeRunStatus:
        if self._status is None:
            raise ValueError("payload 事件前必须先发出状态事件")
        if self._status.is_terminal:
            raise ValueError("终态后不能继续发出事件")
        return self._status

    async def _emit(
        self,
        kind: RuntimeRunEventKind,
        status: RuntimeRunStatus,
        *,
        text_delta: str = "",
        tool_call: RuntimeToolCall | None = None,
        usage: RuntimeUsage | None = None,
        artifact: RuntimeArtifactRef | None = None,
        error: RuntimeRunError | None = None,
        attributes: tuple[RuntimeAttribute, ...] = (),
    ) -> RuntimeRunEvent:
        self._sequence += 1
        event = RuntimeRunEvent(
            event_id=(
                f"{self._identity.run_id}:{self._identity.turn_id}:"
                f"{self._emission_id}:{self._sequence}"
            ),
            identity=self._identity,
            sequence=self._sequence,
            kind=kind,
            status=status,
            occurred_at=self._clock(),
            text_delta=text_delta,
            tool_call=tool_call,
            usage=usage,
            artifact=artifact,
            error=error,
            attributes=attributes,
        )
        handled = self._handler(event)
        if inspect.isawaitable(handled):
            await handled
        return event


async def relay_runtime_run_events(
    execute: Callable[[RuntimeRunEventHandler], Awaitable[_ResultT]],
) -> AsyncIterator[RuntimeRunEvent]:
    """把 callback 形式的事件执行转换为可取消、保留异常的异步迭代器。"""

    queue: asyncio.Queue[RuntimeRunEvent | object] = asyncio.Queue()
    failure: BaseException | None = None

    def enqueue(event: RuntimeRunEvent) -> None:
        queue.put_nowait(event)

    async def run() -> None:
        nonlocal failure
        try:
            await execute(enqueue)
        except BaseException as exc:
            failure = exc
        finally:
            queue.put_nowait(_STREAM_END)

    task = asyncio.create_task(run())
    completed = False
    try:
        while True:
            item = await queue.get()
            if item is _STREAM_END:
                completed = True
                break
            if not isinstance(item, RuntimeRunEvent):
                raise RuntimeError("Runtime 事件流收到未知对象")
            yield item
        await task
        if failure is not None:
            raise failure
    finally:
        if not completed and not task.done():
            task.cancel()
        if not task.done():
            with suppress(asyncio.CancelledError):
                await task


__all__ = ["RuntimeRunEventEmitter", "relay_runtime_run_events"]

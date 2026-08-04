"""把 RuntimeEvent、RuntimeRunEvent 与 Permission 决定写入 Ledger。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from core.agent_runtime.contracts import RuntimeRunEvent
from core.run_ledger.adapters import (
    permission_decision_event,
    runtime_event_to_ledger,
    runtime_run_event_to_ledger,
)
from core.run_ledger.persistence import SqlAlchemyRunEventLedgerWriter
from core.runtime.events import RuntimeEvent


logger = logging.getLogger("nanobot.run_ledger")


class SqlAlchemyRuntimeEventLedgerSink:
    """RuntimeEvent Bus 的同步 shadow sink；无 run_id 的观测事件不入账。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._writer = SqlAlchemyRunEventLedgerWriter(session_factory)

    def emit(self, event: RuntimeEvent) -> None:
        draft = runtime_event_to_ledger(event)
        if draft is not None:
            self._writer.append(draft)


class SqlAlchemyRuntimeRunEventSink:
    """AgentRuntime 类型化事件的 durable RunEventSink Adapter。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._writer = SqlAlchemyRunEventLedgerWriter(session_factory)

    async def append(self, event: RuntimeRunEvent) -> None:
        self._writer.append(runtime_run_event_to_ledger(event))


class LedgeredPermissionPort:
    """先取得确定性决定，再在返回调用方前持久化决定事实。"""

    def __init__(
        self,
        delegate: Any,
        session_factory: Callable[[], Session],
    ) -> None:
        evaluate = getattr(delegate, "evaluate", None)
        if not callable(evaluate):
            raise TypeError("delegate 必须实现 PermissionPort.evaluate")
        self._delegate = delegate
        self._writer = SqlAlchemyRunEventLedgerWriter(session_factory)

    async def evaluate(self, request: Any) -> Any:
        decision = await self._delegate.evaluate(request)
        self._writer.append(permission_decision_event(request, decision))
        return decision


def default_runtime_run_event_sink() -> SqlAlchemyRuntimeRunEventSink:
    """延迟解析 SessionLocal，保留测试替换与 composition root 边界。"""

    from core import database

    return SqlAlchemyRuntimeRunEventSink(lambda: database.SessionLocal())


__all__ = [
    "LedgeredPermissionPort",
    "SqlAlchemyRuntimeEventLedgerSink",
    "SqlAlchemyRuntimeRunEventSink",
    "default_runtime_run_event_sink",
]

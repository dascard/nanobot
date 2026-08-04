"""把 RuntimeEvent、RuntimeRunEvent 与 Permission 决定写入 Ledger。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from core.agent_runtime.contracts import RuntimeRunEvent
from core.run_ledger.adapters import (
    permission_decision_event,
    runtime_event_admission_events,
    runtime_event_terminal_event,
    runtime_event_to_ledger,
    runtime_run_event_to_ledger,
)
from core.run_ledger.contracts import RunLedgerAuthorityError
from core.run_ledger.persistence import SqlAlchemyRunEventLedgerWriter
from core.runtime.events import RuntimeEvent


logger = logging.getLogger("nanobot.run_ledger")


class SqlAlchemyRuntimeEventLedgerSink:
    """RuntimeEvent Bus 的同步权威 sink；无 run_id 的观测事件不入账。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._writer = SqlAlchemyRunEventLedgerWriter(session_factory)

    def emit(self, event: RuntimeEvent) -> None:
        draft = runtime_event_to_ledger(event)
        if draft is None:
            return
        try:
            head = self._writer.head(draft.run_id)
            events = []
            if head is None:
                if not _allows_implicit_admission(event):
                    raise RunLedgerAuthorityError(
                        "业务 Run 缺少接纳事实",
                        run_id=draft.run_id,
                        event_type=draft.event_type,
                        code="run_not_admitted",
                    )
                events.extend(runtime_event_admission_events(event))
            events.append(draft)
            terminal = (
                runtime_event_terminal_event(event)
                if _allows_implicit_admission(event)
                else None
            )
            if terminal is not None:
                events.append(terminal)
            self._writer.append_many(tuple(events))
        except RunLedgerAuthorityError:
            raise
        except Exception as exc:
            raise RunLedgerAuthorityError(
                "RuntimeEvent 权威入账失败",
                run_id=draft.run_id,
                event_type=draft.event_type,
            ) from exc


class SqlAlchemyRuntimeRunEventSink:
    """AgentRuntime 类型化事件的 durable RunEventSink Adapter。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._writer = SqlAlchemyRunEventLedgerWriter(session_factory)

    async def append(self, event: RuntimeRunEvent) -> None:
        draft = runtime_run_event_to_ledger(event)
        try:
            if self._writer.head(draft.run_id) is None:
                raise RunLedgerAuthorityError(
                    "Runtime 调用对应的业务 Run 尚未接纳",
                    run_id=draft.run_id,
                    event_type=draft.event_type,
                    code="run_not_admitted",
                )
            self._writer.append(draft)
        except RunLedgerAuthorityError:
            raise
        except Exception as exc:
            raise RunLedgerAuthorityError(
                "RuntimeRunEvent 权威入账失败",
                run_id=draft.run_id,
                event_type=draft.event_type,
            ) from exc


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
        event = permission_decision_event(request, decision)
        try:
            self._writer.append(event)
        except Exception as exc:
            raise RunLedgerAuthorityError(
                "Permission 决定权威入账失败",
                run_id=event.run_id,
                event_type=event.event_type,
            ) from exc
        return decision


def _allows_implicit_admission(event: RuntimeEvent) -> bool:
    """只有服务端生成且一次一 ID 的领域 Attempt 可以自动接纳。"""

    run_id = str(event.context.run_id or "")
    return event.name == "delivery.attempt" and run_id.startswith("delivery:")


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

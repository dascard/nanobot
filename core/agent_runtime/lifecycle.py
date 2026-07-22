"""Agent Runtime 的显式、可审计生命周期状态机。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from core.agent_runtime.contracts import (
    RuntimeLifecycleEvent,
    RuntimeLifecycleEventSink,
    RuntimeLifecycleState,
)
from core.agent_runtime.errors import AgentRuntimeStateError


logger = logging.getLogger("nanobot.agent_runtime.lifecycle")


_ALLOWED_TRANSITIONS: dict[RuntimeLifecycleState, frozenset[RuntimeLifecycleState]] = {
    RuntimeLifecycleState.NEW: frozenset(
        {
            RuntimeLifecycleState.STARTING,
            RuntimeLifecycleState.STOPPED,
        }
    ),
    RuntimeLifecycleState.STARTING: frozenset(
        {
            RuntimeLifecycleState.RUNNING,
            RuntimeLifecycleState.FAILED,
        }
    ),
    RuntimeLifecycleState.RUNNING: frozenset(
        {
            RuntimeLifecycleState.STOPPING,
            RuntimeLifecycleState.FAILED,
        }
    ),
    RuntimeLifecycleState.STOPPING: frozenset(
        {
            RuntimeLifecycleState.STOPPED,
            RuntimeLifecycleState.FAILED,
        }
    ),
    RuntimeLifecycleState.FAILED: frozenset(
        {
            RuntimeLifecycleState.STOPPING,
            RuntimeLifecycleState.STOPPED,
        }
    ),
    RuntimeLifecycleState.STOPPED: frozenset(),
}


class RuntimeLifecycleMachine:
    """Adapter 共用的有限状态机；STOPPED 是终态。"""

    def __init__(
        self,
        runtime_id: str,
        *,
        initial_state: RuntimeLifecycleState = RuntimeLifecycleState.NEW,
        event_sinks: tuple[RuntimeLifecycleEventSink, ...] = (),
        now: Callable[[], datetime] | None = None,
    ) -> None:
        normalized_id = str(runtime_id or "").strip()
        if not normalized_id:
            raise ValueError("runtime_id 不能为空")
        self.runtime_id = normalized_id
        self._state = initial_state
        self._events: list[RuntimeLifecycleEvent] = []
        self._event_sinks = tuple(event_sinks)
        self._now = now or (lambda: datetime.now(timezone.utc))

    @property
    def state(self) -> RuntimeLifecycleState:
        return self._state

    @property
    def events(self) -> tuple[RuntimeLifecycleEvent, ...]:
        return tuple(self._events)

    def ensure(self, *allowed: RuntimeLifecycleState) -> None:
        if self._state not in allowed:
            expected = ", ".join(state.value for state in allowed)
            raise AgentRuntimeStateError(
                f"运行时状态为 {self._state.value}，操作要求状态为 {expected}",
                runtime_id=self.runtime_id,
            )

    def transition(
        self,
        target: RuntimeLifecycleState,
        *,
        reason: str = "",
    ) -> RuntimeLifecycleEvent:
        previous = self._state
        if target not in _ALLOWED_TRANSITIONS[previous]:
            raise AgentRuntimeStateError(
                f"不允许从 {previous.value} 转换为 {target.value}",
                runtime_id=self.runtime_id,
            )
        occurred_at = self._now()
        if occurred_at.tzinfo is None:
            raise ValueError("生命周期时钟必须返回带时区的 datetime")
        event = RuntimeLifecycleEvent(
            sequence=len(self._events) + 1,
            runtime_id=self.runtime_id,
            previous_state=previous,
            current_state=target,
            occurred_at=occurred_at,
            reason=str(reason or ""),
        )
        self._state = target
        self._events.append(event)
        for sink in self._event_sinks:
            try:
                sink(event)
            except Exception:
                logger.exception(
                    "Agent Runtime 生命周期事件 sink 执行失败",
                    extra={"runtime_id": self.runtime_id, "sequence": event.sequence},
                )
        return event

    def fail(self, reason: str) -> RuntimeLifecycleEvent:
        if self._state is RuntimeLifecycleState.FAILED:
            raise AgentRuntimeStateError(
                "运行时已经处于 failed 状态",
                runtime_id=self.runtime_id,
            )
        return self.transition(RuntimeLifecycleState.FAILED, reason=reason)

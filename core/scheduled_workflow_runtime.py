"""统一定时任务执行回调的框架无关进程绑定。"""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock

from core.scheduled_workflow import ScheduledWorkflowCallbacks


ScheduledWorkflowCallbacksFactory = Callable[
    [],
    ScheduledWorkflowCallbacks,
]

_lock = RLock()
_callbacks_factory: ScheduledWorkflowCallbacksFactory | None = None


def bind_scheduled_workflow_callbacks(
    factory: ScheduledWorkflowCallbacksFactory,
) -> None:
    """由 Composition Root 绑定生产 Adapter，禁止隐式覆盖。"""

    if not callable(factory):
        raise TypeError("scheduled workflow callbacks factory 必须可调用")
    global _callbacks_factory
    with _lock:
        if _callbacks_factory is not None:
            raise RuntimeError(
                "Scheduled Workflow Runtime 已绑定，禁止隐式替换"
            )
        _callbacks_factory = factory


def clear_scheduled_workflow_callbacks() -> None:
    """幂等清除生产 Adapter 绑定。"""

    global _callbacks_factory
    with _lock:
        _callbacks_factory = None


def create_scheduled_workflow_callbacks(
) -> ScheduledWorkflowCallbacks | None:
    """创建当前生产回调；运行时尚未就绪时返回 ``None``。"""

    with _lock:
        factory = _callbacks_factory
    if factory is None:
        return None
    callbacks = factory()
    if callbacks is None:
        raise RuntimeError("Scheduled Workflow callbacks factory 返回空值")
    return callbacks


def scheduled_workflow_runtime_state() -> str:
    with _lock:
        return "running" if _callbacks_factory is not None else "stopped"


__all__ = [
    "ScheduledWorkflowCallbacksFactory",
    "bind_scheduled_workflow_callbacks",
    "clear_scheduled_workflow_callbacks",
    "create_scheduled_workflow_callbacks",
    "scheduled_workflow_runtime_state",
]

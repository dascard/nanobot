"""多 Agent 执行深度的请求级绑定。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


_CURRENT_ORCHESTRATION_DEPTH: ContextVar[int] = ContextVar(
    "nanobot_agent_orchestration_depth",
    default=0,
)


def current_orchestration_depth() -> int:
    return _CURRENT_ORCHESTRATION_DEPTH.get()


@contextmanager
def orchestration_worker_scope() -> Iterator[None]:
    """Worker 内固定为第 1 层；嵌套调度器会据此失败关闭。"""

    if current_orchestration_depth() != 0:
        raise RuntimeError("多 Agent Worker 禁止继续 spawn")
    token = _CURRENT_ORCHESTRATION_DEPTH.set(1)
    try:
        yield
    finally:
        _CURRENT_ORCHESTRATION_DEPTH.reset(token)


__all__ = [
    "current_orchestration_depth",
    "orchestration_worker_scope",
]

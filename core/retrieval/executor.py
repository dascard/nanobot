"""Reranker 阻塞调用的显式执行器生命周期。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RerankerExecutorPort(Protocol):
    @property
    def started(self) -> bool: ...

    def start(self) -> None: ...

    def submit(
        self, operation: Callable[..., Any], /, *args: Any, **kwargs: Any
    ) -> Future: ...

    def stop(self) -> None: ...


class ManagedRerankerExecutor:
    """只在显式 start 后接收任务，并可在进程关停时回收。"""

    def __init__(
        self,
        *,
        max_workers: int = 1,
        thread_name_prefix: str = "nanobot-reranker",
    ) -> None:
        self._max_workers = max(1, int(max_workers))
        self._thread_name_prefix = str(thread_name_prefix or "nanobot-reranker")
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()

    @property
    def started(self) -> bool:
        with self._lock:
            return self._executor is not None

    def start(self) -> None:
        with self._lock:
            if self._executor is not None:
                return
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix=self._thread_name_prefix,
            )

    def submit(
        self,
        operation: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Future:
        with self._lock:
            executor = self._executor
        if executor is None:
            raise RuntimeError("Reranker executor 尚未启动")
        return executor.submit(operation, *args, **kwargs)

    def stop(self) -> None:
        with self._lock:
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)


_RUNTIME_LOCK = threading.Lock()
_RUNTIME_EXECUTOR: RerankerExecutorPort | None = None


def start_retrieval_runtime(
    executor: RerankerExecutorPort | None = None,
) -> RerankerExecutorPort:
    """Composition root：启动并安装进程级 Retrieval 执行器。"""

    global _RUNTIME_EXECUTOR
    candidate = executor or ManagedRerankerExecutor(
        max_workers=1,
        thread_name_prefix="group-memory-reranker",
    )
    candidate.start()
    with _RUNTIME_LOCK:
        if _RUNTIME_EXECUTOR is not None:
            candidate.stop()
            raise RuntimeError("Retrieval runtime 已启动")
        _RUNTIME_EXECUTOR = candidate
    return candidate


def get_retrieval_reranker_executor() -> RerankerExecutorPort | None:
    with _RUNTIME_LOCK:
        return _RUNTIME_EXECUTOR


def stop_retrieval_runtime(
    executor: RerankerExecutorPort | None,
) -> None:
    """只移除当前安装实例，避免旧 lifespan 误停新实例。"""

    global _RUNTIME_EXECUTOR
    if executor is None:
        return
    with _RUNTIME_LOCK:
        if _RUNTIME_EXECUTOR is executor:
            _RUNTIME_EXECUTOR = None
    executor.stop()


__all__ = [
    "ManagedRerankerExecutor",
    "RerankerExecutorPort",
    "get_retrieval_reranker_executor",
    "start_retrieval_runtime",
    "stop_retrieval_runtime",
]

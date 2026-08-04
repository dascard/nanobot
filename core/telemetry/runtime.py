"""Telemetry Sink 的有界缓冲和显式进程生命周期。"""

from __future__ import annotations

import os
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from core.runtime.event_bus import (
    LoggingRuntimeEventSink,
    get_runtime_event_emitter,
    install_runtime_event_sinks,
)
from core.runtime.events import RuntimeEvent
from core.run_ledger.sinks import SqlAlchemyRuntimeEventLedgerSink
from core.telemetry.persistence import SqlAlchemyRuntimeEventSink


class RuntimeEventBatchSink(Protocol):
    def emit_many(self, events: tuple[RuntimeEvent, ...]) -> None: ...


_STOP = object()


class BufferedRuntimeEventSink:
    """业务线程只做非阻塞入队；后台线程批量交给持久化 Adapter。"""

    def __init__(
        self,
        delegate: RuntimeEventBatchSink,
        *,
        capacity: int = 4096,
        batch_size: int = 64,
    ) -> None:
        if not callable(getattr(delegate, "emit_many", None)):
            raise TypeError("delegate 必须实现 emit_many")
        if capacity <= 0 or batch_size <= 0 or batch_size > capacity:
            raise ValueError("Telemetry 队列容量或批量大小无效")
        self._delegate = delegate
        self._queue: queue.Queue[RuntimeEvent | object] = queue.Queue(
            maxsize=capacity
        )
        self._batch_size = int(batch_size)
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._dropped_count = 0

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._running

    @property
    def dropped_count(self) -> int:
        with self._state_lock:
            return self._dropped_count

    def start(self) -> None:
        with self._state_lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._run,
                name="nanobot-telemetry-writer",
                daemon=True,
            )
            thread = self._thread
        thread.start()

    def emit(self, event: RuntimeEvent) -> None:
        if not isinstance(event, RuntimeEvent):
            raise TypeError("Buffered Sink 只接受 RuntimeEvent")
        with self._state_lock:
            running = self._running
        if not running:
            self._drop(1)
            return
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self._drop(1)

    def stop(self, *, timeout_seconds: float = 5.0) -> bool:
        with self._state_lock:
            if not self._running:
                return True
            thread = self._thread
        stop_enqueued = False
        try:
            self._queue.put(_STOP, timeout=max(0.1, timeout_seconds))
            stop_enqueued = True
        except queue.Full:
            try:
                dropped = self._queue.get_nowait()
            except queue.Empty:
                pass
            else:
                self._queue.task_done()
                if dropped is _STOP:
                    stop_enqueued = True
                else:
                    self._drop(1)
            if not stop_enqueued:
                try:
                    self._queue.put_nowait(_STOP)
                    stop_enqueued = True
                except queue.Full:
                    return False
        if not stop_enqueued:
            return False
        if thread is not None:
            thread.join(timeout=max(0.1, timeout_seconds))
            if thread.is_alive():
                return False
        with self._state_lock:
            self._running = False
            self._thread = None
        return True

    def _drop(self, count: int) -> None:
        with self._state_lock:
            self._dropped_count += max(0, int(count))

    def _run(self) -> None:
        try:
            while True:
                item = self._queue.get()
                if item is _STOP:
                    self._queue.task_done()
                    break
                batch = [item]
                stop_after_batch = False
                while len(batch) < self._batch_size:
                    try:
                        candidate = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if candidate is _STOP:
                        stop_after_batch = True
                        self._queue.task_done()
                        break
                    batch.append(candidate)
                typed_batch = tuple(
                    event
                    for event in batch
                    if isinstance(event, RuntimeEvent)
                )
                try:
                    self._delegate.emit_many(typed_batch)
                except Exception:
                    self._drop(len(typed_batch))
                finally:
                    for _event in batch:
                        self._queue.task_done()
                if stop_after_batch:
                    break
        finally:
            with self._state_lock:
                self._running = False
                self._thread = None


@dataclass(slots=True)
class TelemetryRuntimeHandle:
    buffered_sink: BufferedRuntimeEventSink | None
    job_observer: object | None = None
    ledger_sink: SqlAlchemyRuntimeEventLedgerSink | None = None
    installed: bool = True


_RUNTIME_LOCK = threading.Lock()
_RUNTIME_HANDLE: TelemetryRuntimeHandle | None = None


def start_telemetry_runtime(
    *,
    session_factory: Callable[[], Session] | None = None,
    capacity: int = 4096,
    batch_size: int = 64,
) -> TelemetryRuntimeHandle:
    """由 Composition Root／worker 显式安装进程级持久化 Sink。"""

    global _RUNTIME_HANDLE
    with _RUNTIME_LOCK:
        if _RUNTIME_HANDLE is not None and _RUNTIME_HANDLE.installed:
            return _RUNTIME_HANDLE
        if (
            session_factory is None
            and os.environ.get("NANOBOT_TESTING") == "1"
        ):
            handle = TelemetryRuntimeHandle(buffered_sink=None)
            _RUNTIME_HANDLE = handle
            return handle

        if session_factory is None:
            from core import database

            session_factory = database.SessionLocal
        persistent = SqlAlchemyRuntimeEventSink(
            session_factory
        )
        ledger = SqlAlchemyRuntimeEventLedgerSink(session_factory)
        buffered = BufferedRuntimeEventSink(
            persistent,
            capacity=capacity,
            batch_size=batch_size,
        )
        buffered.start()
        install_runtime_event_sinks((
            LoggingRuntimeEventSink(),
            ledger,
            buffered,
        ))
        from core.telemetry.job_observer import (
            install_job_telemetry_observer,
        )

        observer = install_job_telemetry_observer(
            get_runtime_event_emitter()
        )
        handle = TelemetryRuntimeHandle(
            buffered_sink=buffered,
            ledger_sink=ledger,
            job_observer=observer,
        )
        _RUNTIME_HANDLE = handle
        return handle


def stop_telemetry_runtime(
    handle: object | None,
) -> None:
    global _RUNTIME_HANDLE
    if handle is None:
        return
    with _RUNTIME_LOCK:
        active = _RUNTIME_HANDLE
        if active is None or handle is not active:
            return
        _RUNTIME_HANDLE = None
        active.installed = False
    uninstall = getattr(active.job_observer, "uninstall", None)
    if callable(uninstall):
        uninstall()
    if active.buffered_sink is not None:
        active.buffered_sink.stop()
    install_runtime_event_sinks((LoggingRuntimeEventSink(),))


__all__ = [
    "BufferedRuntimeEventSink",
    "TelemetryRuntimeHandle",
    "start_telemetry_runtime",
    "stop_telemetry_runtime",
]

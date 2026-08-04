"""Agent Run 租约 owner 的异步 heartbeat、取消和 timeout 协调。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from core.durable_tasks.contracts import (
    RunTaskHeartbeatReason,
    RunTaskLease,
)
from core.durable_tasks.service import (
    DEFAULT_RUN_TASK_LEASE_SECONDS,
    SqlAlchemyRunTaskService,
)


logger = logging.getLogger("nanobot.durable_tasks")


_CANCEL_MESSAGE = {
    RunTaskHeartbeatReason.CANCEL_REQUESTED: "durable_task_cancelled",
    RunTaskHeartbeatReason.TIMED_OUT: "durable_task_timed_out",
    RunTaskHeartbeatReason.LEASE_LOST: "durable_task_lease_lost",
    RunTaskHeartbeatReason.TERMINAL: "durable_task_lease_lost",
}


def durable_cancel_status(error: BaseException) -> str:
    """把 owner 主动发出的取消原因映射为稳定 Run 终态。"""

    reason = str(error or "")
    if reason == "durable_task_timed_out":
        return "timed_out"
    if reason == "durable_task_lease_lost":
        return "ambiguous"
    return "cancelled"


class RunTaskOwner:
    """在 fresh Session 中续租；失权时取消当前执行协程。"""

    def __init__(
        self,
        lease: RunTaskLease,
        *,
        session_factory: Callable[[], Any] | None = None,
        lease_seconds: float = DEFAULT_RUN_TASK_LEASE_SECONDS,
        heartbeat_interval_seconds: float | None = None,
    ) -> None:
        self.lease = lease
        self._session_factory = session_factory or self._default_session
        self._lease_seconds = float(lease_seconds)
        self._heartbeat_interval = (
            max(0.1, min(30.0, self._lease_seconds / 3.0))
            if heartbeat_interval_seconds is None
            else max(0.05, float(heartbeat_interval_seconds))
        )
        self._execution_task: asyncio.Task[Any] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._stopped = False

    @staticmethod
    def _default_session() -> Any:
        from core import database

        return database.SessionLocal()

    async def start(self) -> None:
        if self._heartbeat_task is not None:
            raise RuntimeError("RunTaskOwner 已启动")
        execution_task = asyncio.current_task()
        if execution_task is None:
            raise RuntimeError("RunTaskOwner 必须在 asyncio Task 中启动")
        self._execution_task = execution_task
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(),
            name=f"run-task-heartbeat:{self.lease.run_id}",
        )

    def _renew_once(self):
        db = self._session_factory()
        try:
            return SqlAlchemyRunTaskService(db).heartbeat(
                self.lease,
                lease_seconds=self._lease_seconds,
            )
        finally:
            db.close()

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                delay = self._heartbeat_interval
                timeout_at = self.lease.timeout_at
                if timeout_at is not None:
                    current = datetime.now(timezone.utc)
                    if timeout_at.tzinfo is None:
                        current = current.replace(tzinfo=None)
                    delay = min(
                        delay,
                        max(0.0, (timeout_at - current).total_seconds()),
                    )
                if delay > 0:
                    await asyncio.sleep(delay)
                heartbeat = await asyncio.to_thread(self._renew_once)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                logger.error(
                    "Run Durable Task heartbeat 异常：run_id=%s error_type=%s",
                    self.lease.run_id,
                    type(exc).__name__,
                )
                self._cancel_execution("durable_task_lease_lost")
                return
            if heartbeat.renewed:
                assert heartbeat.lease is not None
                self.lease = heartbeat.lease
                continue
            self._cancel_execution(
                _CANCEL_MESSAGE.get(
                    heartbeat.reason,
                    "durable_task_lease_lost",
                )
            )
            return

    def _cancel_execution(self, reason: str) -> None:
        task = self._execution_task
        if task is not None and not task.done():
            task.cancel(reason)

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)


__all__ = ["RunTaskOwner", "durable_cancel_status"]

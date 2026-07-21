"""Sandbox 运行账本的独立短事务写入。"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from core.database import SandboxRun
from core.time_utils import db_now_naive


class SandboxRunLedger:
    """让容器运行状态脱离模型工具外层 UnitOfWork 持久化。"""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        commit_attempts: int = 3,
    ) -> None:
        self._session_factory = session_factory
        self._commit_attempts = max(1, int(commit_attempts))

    def _update(
        self,
        run_id: str,
        mutate: Callable[[SandboxRun], None],
    ) -> None:
        last_error: Exception | None = None
        for attempt in range(self._commit_attempts):
            db: Session | None = None
            try:
                db = self._session_factory()
                row = db.get(SandboxRun, run_id)
                if row is None:
                    raise RuntimeError("Sandbox 运行账本不存在")
                mutate(row)
                row.updated_at = db_now_naive()
                db.commit()
                return
            except Exception as exc:
                last_error = exc
                if db is not None:
                    try:
                        db.rollback()
                    except Exception:
                        pass
            finally:
                if db is not None:
                    try:
                        db.close()
                    except Exception:
                        pass
            if attempt + 1 < self._commit_attempts:
                time.sleep(0.05 * (attempt + 1))
        if last_error is None:
            raise RuntimeError("Sandbox 运行账本更新失败")
        raise last_error

    def mark_running(self, run_id: str) -> None:
        def mutate(row: SandboxRun) -> None:
            if row.status != "pending":
                raise RuntimeError("Sandbox 运行状态转换无效")
            row.status = "running"
            row.started_at = db_now_naive()

        self._update(run_id, mutate)

    def mark_terminal(
        self,
        run_id: str,
        *,
        status: str,
        termination_reason: str,
        data: dict[str, Any],
    ) -> None:
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("Sandbox 运行终态无效")

        def mutate(row: SandboxRun) -> None:
            if row.status not in {"pending", "running"}:
                raise RuntimeError("Sandbox 运行状态转换无效")
            row.status = status
            row.exit_code = (
                int(data["exit_code"])
                if data.get("exit_code") is not None
                else None
            )
            row.termination_reason = termination_reason
            row.cpu_time_ms = max(0, int(data.get("cpu_time_ms") or 0))
            row.peak_memory_bytes = max(
                0,
                int(data.get("peak_memory_bytes") or 0),
            )
            row.stdout_bytes = max(0, int(data.get("stdout_bytes") or 0))
            row.stderr_bytes = max(0, int(data.get("stderr_bytes") or 0))
            row.stdout_truncated = bool(data.get("stdout_truncated"))
            row.stderr_truncated = bool(data.get("stderr_truncated"))
            row.finished_at = db_now_naive()

        self._update(run_id, mutate)

    def mark_failed(self, run_id: str, *, termination_reason: str) -> None:
        def mutate(row: SandboxRun) -> None:
            if row.status in {"completed", "failed", "cancelled"}:
                return
            row.status = "failed"
            row.termination_reason = termination_reason
            row.finished_at = db_now_naive()

        self._update(run_id, mutate)


__all__ = ["SandboxRunLedger"]

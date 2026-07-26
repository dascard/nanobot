"""Sandbox 运行账本的独立短事务写入。"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from core.database import (
    SANDBOX_LEASE_NONTERMINAL_STATUSES,
    SandboxLease,
    SandboxRun,
)
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

    def mark_running(
        self,
        run_id: str,
        *,
        process_state: str | None = None,
    ) -> None:
        def mutate(row: SandboxRun) -> None:
            if row.status != "pending":
                raise RuntimeError("Sandbox 运行状态转换无效")
            row.status = "running"
            row.started_at = db_now_naive()
            row.last_seen_at = row.started_at
            if process_state is not None:
                row.process_state = str(process_state)

        self._update(run_id, mutate)

    def mark_observed(
        self,
        run_id: str,
        *,
        process_state: str = "running",
        data: dict[str, Any] | None = None,
    ) -> None:
        observed = dict(data or {})

        def mutate(row: SandboxRun) -> None:
            if row.status in {"completed", "failed", "cancelled"}:
                return
            if row.status not in {"pending", "running"}:
                raise RuntimeError("Sandbox 运行状态转换无效")
            row.status = "running"
            row.process_state = str(process_state)
            row.started_at = row.started_at or db_now_naive()
            row.last_seen_at = db_now_naive()
            row.cpu_time_ms = max(
                int(row.cpu_time_ms or 0),
                max(0, int(observed.get("cpu_time_ms") or 0)),
            )
            row.peak_memory_bytes = max(
                int(row.peak_memory_bytes or 0),
                max(0, int(observed.get("peak_memory_bytes") or 0)),
            )
            row.stdout_bytes = max(
                int(row.stdout_bytes or 0),
                max(0, int(observed.get("stdout_bytes") or 0)),
            )
            row.stderr_bytes = max(
                int(row.stderr_bytes or 0),
                max(0, int(observed.get("stderr_bytes") or 0)),
            )
            row.stdout_truncated = bool(
                row.stdout_truncated
                or observed.get("stdout_truncated")
            )
            row.stderr_truncated = bool(
                row.stderr_truncated
                or observed.get("stderr_truncated")
            )

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
            if row.status in {"completed", "failed", "cancelled"}:
                return
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
            row.process_state = str(
                data.get("process_state")
                or (
                    "lost"
                    if data.get("lease_recycled")
                    else "exited"
                )
            )
            row.finished_at = db_now_naive()
            row.last_seen_at = row.finished_at

        self._update(run_id, mutate)

    def mark_failed(self, run_id: str, *, termination_reason: str) -> None:
        def mutate(row: SandboxRun) -> None:
            if row.status in {"completed", "failed", "cancelled"}:
                return
            row.status = "failed"
            if str(row.execution_mode) == "lease":
                row.process_state = "lost"
            row.termination_reason = termination_reason
            row.finished_at = db_now_naive()
            row.last_seen_at = row.finished_at

        self._update(run_id, mutate)

    def settle_lease(
        self,
        lease_id: str,
        *,
        termination_reason: str,
        status: str,
        affected_process_ids: set[str] | None = None,
        target_process_id: str = "",
        target_data: dict[str, Any] | None = None,
    ) -> None:
        """整 Lease 回收时在一个短事务内结算 Lease 与全部活动 Run。"""

        if status not in {"failed", "cancelled"}:
            raise ValueError("Sandbox Lease 运行终态无效")
        reason = str(termination_reason or "lease_recycled")[:64]
        expected_ids = set(affected_process_ids or ())
        target = dict(target_data or {})
        last_error: Exception | None = None
        for attempt in range(self._commit_attempts):
            db: Session | None = None
            try:
                db = self._session_factory()
                lease = db.get(SandboxLease, lease_id)
                if lease is None:
                    raise RuntimeError("Sandbox Lease 账本不存在")
                now = db_now_naive()
                active_runs = (
                    db.query(SandboxRun)
                    .filter(
                        SandboxRun.lease_id == lease_id,
                        SandboxRun.status.in_({"pending", "running"}),
                    )
                    .all()
                )
                if target_process_id and (
                    expected_ids
                    and target_process_id not in expected_ids
                ):
                    raise RuntimeError(
                        "Sandbox Lease 回收响应遗漏目标进程"
                    )
                if str(lease.status) in SANDBOX_LEASE_NONTERMINAL_STATUSES:
                    lease.status = (
                        "stopped"
                        if status == "cancelled"
                        else "failed"
                    )
                    lease.stopped_at = now
                    lease.reconciled_at = now
                    lease.last_error_code = reason
                    lease.last_error_summary = (
                        "Sandbox Lease 已按进程终止边界回收"
                    )
                for row in active_runs:
                    row.status = status
                    row.process_state = "lost"
                    row.termination_reason = reason
                    row.finished_at = now
                    row.last_seen_at = now
                    row.updated_at = now
                    if str(row.run_id) == target_process_id:
                        row.exit_code = (
                            int(target["exit_code"])
                            if target.get("exit_code") is not None
                            else None
                        )
                        row.cpu_time_ms = max(
                            0,
                            int(target.get("cpu_time_ms") or 0),
                        )
                        row.peak_memory_bytes = max(
                            0,
                            int(target.get("peak_memory_bytes") or 0),
                        )
                        row.stdout_bytes = max(
                            0,
                            int(target.get("stdout_bytes") or 0),
                        )
                        row.stderr_bytes = max(
                            0,
                            int(target.get("stderr_bytes") or 0),
                        )
                        row.stdout_truncated = bool(
                            target.get("stdout_truncated")
                        )
                        row.stderr_truncated = bool(
                            target.get("stderr_truncated")
                        )
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
            raise RuntimeError("Sandbox Lease 终态账本更新失败")
        raise last_error


__all__ = ["SandboxRunLedger"]

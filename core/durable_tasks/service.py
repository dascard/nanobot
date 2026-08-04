"""Agent Run Durable Task 的 SQLAlchemy 控制服务。"""

from __future__ import annotations

import hashlib
import math
import secrets
import socket
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from core.db.models.durable_task import RunTaskControl
from core.durable_tasks.contracts import (
    RunTaskConflict,
    RunTaskHeartbeat,
    RunTaskHeartbeatReason,
    RunTaskKind,
    RunTaskLease,
    RunTaskLeaseLost,
    RunTaskStatus,
    RunTaskView,
)


DEFAULT_RUN_TASK_LEASE_SECONDS = 90.0
DEFAULT_RUN_TASK_TIMEOUT_SECONDS = 1200.0
_PROCESS_RUN_TASK_OWNER = (
    f"agent-run:{socket.gethostname().strip() or 'host'}:{secrets.token_hex(12)}"
)[:128]
_TERMINAL_STATUSES = frozenset(
    status for status in RunTaskStatus if status.terminal
)


def _utc_naive(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is not None:
        current = current.astimezone(timezone.utc).replace(tzinfo=None)
    return current


def _positive_seconds(value: int | float, *, name: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"{name} 必须是有限正数")
    return seconds


def _safe_text(
    value: object,
    *,
    max_chars: int,
    required: bool = False,
) -> str:
    normalized = str(value or "").strip()
    normalized = "".join(
        character if ord(character) >= 32 else " "
        for character in normalized
    )[:max_chars]
    if required and not normalized:
        raise ValueError("字段不能为空")
    return normalized


def _sha256(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _lease(row: RunTaskControl) -> RunTaskLease:
    if (
        str(row.status) != RunTaskStatus.RUNNING.value
        or row.lease_expires_at is None
    ):
        raise RunTaskConflict("任务当前没有活动执行租约")
    return RunTaskLease(
        run_id=str(row.run_id),
        owner=str(row.lease_owner),
        token=str(row.lease_token),
        generation=int(row.lease_generation),
        attempt_no=int(row.attempt_count),
        expires_at=row.lease_expires_at,
        timeout_at=row.timeout_at,
    )


def _view(row: RunTaskControl) -> RunTaskView:
    return RunTaskView(
        run_id=str(row.run_id),
        task_kind=RunTaskKind(str(row.task_kind)),
        source_type=str(row.source_type or ""),
        source_id=str(row.source_id or ""),
        request_id_sha256=str(row.request_id_sha256),
        idempotency_key_sha256=str(row.idempotency_key_sha256),
        status=RunTaskStatus(str(row.status)),
        lease_generation=int(row.lease_generation or 0),
        attempt_count=int(row.attempt_count or 0),
        lease_owner=str(row.lease_owner or ""),
        lease_expires_at=row.lease_expires_at,
        timeout_at=row.timeout_at,
        cancel_requested_at=row.cancel_requested_at,
        cancel_reason=str(row.cancel_reason or ""),
        terminal_reason=str(row.terminal_reason or ""),
        result_ref=str(row.result_ref or ""),
        delivery_receipt_ref=str(row.delivery_receipt_ref or ""),
        created_at=row.created_at,
        updated_at=row.updated_at,
        finished_at=row.finished_at,
    )


def run_status_to_task_status(status: object) -> RunTaskStatus:
    normalized = str(status or "").strip().lower()
    if normalized in {"success", "succeeded", "no_reply", "suppressed"}:
        return RunTaskStatus.SUCCEEDED
    if normalized in {"cancelled", "canceled"}:
        return RunTaskStatus.CANCELLED
    if normalized in {"timed_out", "timeout"}:
        return RunTaskStatus.TIMED_OUT
    if normalized == "ambiguous":
        return RunTaskStatus.AMBIGUOUS
    return RunTaskStatus.FAILED


def default_run_task_owner() -> str:
    return _PROCESS_RUN_TASK_OWNER


class SqlAlchemyRunTaskService:
    """以 Run 为粒度提供 claim、heartbeat、fence 和只读投影。"""

    def __init__(self, db: Session) -> None:
        if not isinstance(db, Session):
            raise TypeError("db 必须是 SQLAlchemy Session")
        self._db = db

    def admit_running(
        self,
        *,
        run_id: str,
        task_kind: RunTaskKind | str,
        source_type: str,
        source_id: str,
        request_id: str,
        idempotency_key: str,
        owner: str,
        lease_seconds: int | float = DEFAULT_RUN_TASK_LEASE_SECONDS,
        timeout_seconds: int | float = DEFAULT_RUN_TASK_TIMEOUT_SECONDS,
        delivery_receipt_ref: str = "",
        now: datetime | None = None,
    ) -> RunTaskLease:
        current = _utc_naive(now)
        lease_duration = _positive_seconds(
            lease_seconds,
            name="lease_seconds",
        )
        timeout_duration = _positive_seconds(
            timeout_seconds,
            name="timeout_seconds",
        )
        normalized_run_id = _safe_text(
            run_id,
            max_chars=160,
            required=True,
        )
        if self._db.get(RunTaskControl, normalized_run_id) is not None:
            raise RunTaskConflict("Run Durable Task 已存在")
        token = secrets.token_hex(32)
        row = RunTaskControl(
            run_id=normalized_run_id,
            task_kind=RunTaskKind(task_kind).value,
            source_type=_safe_text(source_type, max_chars=64),
            source_id=_safe_text(source_id, max_chars=160),
            request_id_sha256=_sha256(request_id or normalized_run_id),
            idempotency_key_sha256=_sha256(
                idempotency_key or request_id or normalized_run_id
            ),
            status=RunTaskStatus.RUNNING.value,
            lease_owner=_safe_text(owner, max_chars=128, required=True),
            lease_token=token,
            lease_generation=1,
            attempt_count=1,
            lease_expires_at=current + timedelta(seconds=lease_duration),
            timeout_at=current + timedelta(seconds=timeout_duration),
            delivery_receipt_ref=_safe_text(
                delivery_receipt_ref,
                max_chars=512,
            ),
            created_at=current,
            updated_at=current,
        )
        self._db.add(row)
        self._db.flush()
        return _lease(row)

    def admit_prepared(
        self,
        *,
        run_id: str,
        task_kind: RunTaskKind | str,
        source_type: str,
        source_id: str,
        request_id: str,
        idempotency_key: str,
        timeout_seconds: int | float = DEFAULT_RUN_TASK_TIMEOUT_SECONDS,
        now: datetime | None = None,
    ) -> RunTaskView:
        current = _utc_naive(now)
        timeout_duration = _positive_seconds(
            timeout_seconds,
            name="timeout_seconds",
        )
        normalized_run_id = _safe_text(
            run_id,
            max_chars=160,
            required=True,
        )
        if self._db.get(RunTaskControl, normalized_run_id) is not None:
            raise RunTaskConflict("Run Durable Task 已存在")
        row = RunTaskControl(
            run_id=normalized_run_id,
            task_kind=RunTaskKind(task_kind).value,
            source_type=_safe_text(source_type, max_chars=64),
            source_id=_safe_text(source_id, max_chars=160),
            request_id_sha256=_sha256(request_id or normalized_run_id),
            idempotency_key_sha256=_sha256(
                idempotency_key or request_id or normalized_run_id
            ),
            status=RunTaskStatus.ACCEPTED.value,
            lease_owner="",
            lease_token="",
            lease_generation=0,
            attempt_count=0,
            lease_expires_at=None,
            timeout_at=current + timedelta(seconds=timeout_duration),
            created_at=current,
            updated_at=current,
        )
        self._db.add(row)
        self._db.flush()
        return _view(row)

    def claim_prepared(
        self,
        run_id: str,
        *,
        owner: str,
        lease_seconds: int | float = DEFAULT_RUN_TASK_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> RunTaskLease:
        current = _utc_naive(now)
        seconds = _positive_seconds(lease_seconds, name="lease_seconds")
        normalized_owner = _safe_text(
            owner,
            max_chars=128,
            required=True,
        )
        token = secrets.token_hex(32)
        updated = (
            self._db.query(RunTaskControl)
            .filter(
                RunTaskControl.run_id == str(run_id or ""),
                RunTaskControl.status == RunTaskStatus.ACCEPTED.value,
                RunTaskControl.cancel_requested_at.is_(None),
                (
                    RunTaskControl.timeout_at.is_(None)
                    | (RunTaskControl.timeout_at > current)
                ),
            )
            .update(
                {
                    RunTaskControl.status: RunTaskStatus.RUNNING.value,
                    RunTaskControl.lease_owner: normalized_owner,
                    RunTaskControl.lease_token: token,
                    RunTaskControl.lease_generation:
                        RunTaskControl.lease_generation + 1,
                    RunTaskControl.attempt_count:
                        RunTaskControl.attempt_count + 1,
                    RunTaskControl.lease_expires_at:
                        current + timedelta(seconds=seconds),
                    RunTaskControl.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        self._db.flush()
        self._db.expire_all()
        row = self._db.get(RunTaskControl, str(run_id or ""))
        if updated != 1 or row is None or str(row.lease_token) != token:
            raise RunTaskConflict("Run 已被其他执行 owner 领取或不可执行")
        return _lease(row)

    @staticmethod
    def _matches(
        row: RunTaskControl,
        lease: RunTaskLease,
        *,
        now: datetime,
    ) -> bool:
        return bool(
            str(row.status) == RunTaskStatus.RUNNING.value
            and str(row.lease_owner) == lease.owner
            and str(row.lease_token) == lease.token
            and int(row.lease_generation) == lease.generation
            and int(row.attempt_count) == lease.attempt_no
            and row.lease_expires_at is not None
            and row.lease_expires_at > now
        )

    def heartbeat(
        self,
        lease: RunTaskLease,
        *,
        lease_seconds: int | float = DEFAULT_RUN_TASK_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> RunTaskHeartbeat:
        current = _utc_naive(now)
        seconds = _positive_seconds(lease_seconds, name="lease_seconds")
        row = self._db.get(RunTaskControl, lease.run_id)
        if row is None:
            return RunTaskHeartbeat(False, RunTaskHeartbeatReason.LEASE_LOST)
        status = RunTaskStatus(str(row.status))
        if status.terminal:
            return RunTaskHeartbeat(False, RunTaskHeartbeatReason.TERMINAL)
        if row.cancel_requested_at is not None:
            return RunTaskHeartbeat(
                False,
                RunTaskHeartbeatReason.CANCEL_REQUESTED,
            )
        if row.timeout_at is not None and row.timeout_at <= current:
            return RunTaskHeartbeat(False, RunTaskHeartbeatReason.TIMED_OUT)
        if not self._matches(row, lease, now=current):
            return RunTaskHeartbeat(False, RunTaskHeartbeatReason.LEASE_LOST)
        expires_at = current + timedelta(seconds=seconds)
        updated = (
            self._db.query(RunTaskControl)
            .filter(
                RunTaskControl.run_id == lease.run_id,
                RunTaskControl.status == RunTaskStatus.RUNNING.value,
                RunTaskControl.lease_owner == lease.owner,
                RunTaskControl.lease_token == lease.token,
                RunTaskControl.lease_generation == lease.generation,
                RunTaskControl.attempt_count == lease.attempt_no,
                RunTaskControl.lease_expires_at > current,
                RunTaskControl.cancel_requested_at.is_(None),
                (
                    RunTaskControl.timeout_at.is_(None)
                    | (RunTaskControl.timeout_at > current)
                ),
            )
            .update(
                {
                    RunTaskControl.lease_expires_at: expires_at,
                    RunTaskControl.updated_at: current,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            self._db.rollback()
            return RunTaskHeartbeat(False, RunTaskHeartbeatReason.LEASE_LOST)
        self._db.commit()
        renewed = RunTaskLease(
            run_id=lease.run_id,
            owner=lease.owner,
            token=lease.token,
            generation=lease.generation,
            attempt_no=lease.attempt_no,
            expires_at=expires_at,
            timeout_at=row.timeout_at,
        )
        return RunTaskHeartbeat(
            True,
            RunTaskHeartbeatReason.RENEWED,
            renewed,
        )

    def settle(
        self,
        lease: RunTaskLease,
        *,
        status: RunTaskStatus | str,
        terminal_reason: str = "",
        result_ref: str = "",
        now: datetime | None = None,
    ) -> RunTaskView:
        target = RunTaskStatus(status)
        if target not in _TERMINAL_STATUSES:
            raise ValueError("settle 只接受终态")
        current = _utc_naive(now)
        row = self._db.get(RunTaskControl, lease.run_id)
        if row is None or not self._matches(row, lease, now=current):
            raise RunTaskLeaseLost("durable_run_task_lease_lost")
        updated = (
            self._db.query(RunTaskControl)
            .filter(
                RunTaskControl.run_id == lease.run_id,
                RunTaskControl.status == RunTaskStatus.RUNNING.value,
                RunTaskControl.lease_owner == lease.owner,
                RunTaskControl.lease_token == lease.token,
                RunTaskControl.lease_generation == lease.generation,
                RunTaskControl.attempt_count == lease.attempt_no,
                RunTaskControl.lease_expires_at > current,
            )
            .update(
                {
                    RunTaskControl.status: target.value,
                    RunTaskControl.lease_owner: "",
                    RunTaskControl.lease_token: "",
                    RunTaskControl.lease_expires_at: None,
                    RunTaskControl.terminal_reason: _safe_text(
                        terminal_reason,
                        max_chars=128,
                    ),
                    RunTaskControl.result_ref: _safe_text(
                        result_ref,
                        max_chars=512,
                    ),
                    RunTaskControl.updated_at: current,
                    RunTaskControl.finished_at: current,
                },
                synchronize_session=False,
            )
        )
        self._db.flush()
        self._db.expire_all()
        settled = self._db.get(RunTaskControl, lease.run_id)
        if updated != 1 or settled is None:
            raise RunTaskLeaseLost("durable_run_task_lease_lost")
        return _view(settled)

    def request_cancel(
        self,
        run_id: str,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> RunTaskView:
        current = _utc_naive(now)
        row = self._db.get(RunTaskControl, str(run_id or ""))
        if row is None:
            raise RunTaskConflict("Run Durable Task 不存在")
        if RunTaskStatus(str(row.status)).terminal:
            return _view(row)
        normalized_reason = _safe_text(
            reason,
            max_chars=128,
            required=True,
        )
        if row.cancel_requested_at is None:
            row.cancel_requested_at = current
            row.cancel_reason = normalized_reason
            row.updated_at = current
            self._db.flush()
        elif str(row.cancel_reason) != normalized_reason:
            raise RunTaskConflict("Run 已存在不同的取消请求")
        return _view(row)

    def attach_delivery_receipt(
        self,
        run_id: str,
        *,
        receipt_ref: str,
        now: datetime | None = None,
    ) -> RunTaskView:
        row = self._db.get(RunTaskControl, str(run_id or ""))
        if row is None:
            raise RunTaskConflict("Run Durable Task 不存在")
        normalized = _safe_text(
            receipt_ref,
            max_chars=512,
            required=True,
        )
        existing = str(row.delivery_receipt_ref or "")
        if existing and existing != normalized:
            raise RunTaskConflict("Run 已绑定不同的投递 receipt")
        if not existing:
            row.delivery_receipt_ref = normalized
            row.updated_at = _utc_naive(now)
            self._db.flush()
        return _view(row)

    def get(self, run_id: str) -> RunTaskView | None:
        row = self._db.get(RunTaskControl, str(run_id or ""))
        return _view(row) if row is not None else None


def classify_run_task(
    *,
    run_type: str,
    meta: dict[str, Any],
) -> tuple[RunTaskKind, str, str]:
    preset = str(meta.get("runtime_preset") or "").strip().lower()
    task_run_id = str(meta.get("task_run_id") or "").strip()
    source = str(meta.get("source") or "").strip().lower()
    if preset == "research" or str(meta.get("chat_type") or "") == "research":
        return RunTaskKind.RESEARCH, "research_request", str(
            meta.get("message_id") or task_run_id
        )
    if task_run_id or str(run_type or "").startswith("scheduled"):
        return RunTaskKind.SCHEDULED, "scheduled_execution", task_run_id
    if source.startswith("proactive"):
        return RunTaskKind.PROACTIVE, "proactive_outreach", str(
            meta.get("message_id") or ""
        )
    if str(run_type or "") == "chat":
        return RunTaskKind.CHAT, "inbound_message", str(
            meta.get("message_id") or ""
        )
    return RunTaskKind.BACKGROUND, "agent_run", ""


__all__ = [
    "DEFAULT_RUN_TASK_LEASE_SECONDS",
    "DEFAULT_RUN_TASK_TIMEOUT_SECONDS",
    "SqlAlchemyRunTaskService",
    "classify_run_task",
    "default_run_task_owner",
    "run_status_to_task_status",
]

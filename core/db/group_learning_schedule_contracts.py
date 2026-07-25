"""群学习白名单调度、增量加载和租约 Port 合同。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from core.jobs import JobLease


class GroupLearningScheduleLeaseLost(RuntimeError):
    """调度租约的 owner、token、generation、attempt 或有效期已失配。"""


@dataclass(frozen=True, slots=True)
class GroupLearningScheduleWrite:
    chat_stream_id: str
    enabled: bool
    aspects: tuple[str, ...]
    interval_minutes: int
    window_hours: int
    next_run_at: datetime


@dataclass(frozen=True, slots=True)
class GroupLearningScheduleState:
    chat_stream_id: str
    enabled: bool
    aspects: tuple[str, ...]
    interval_minutes: int
    window_hours: int
    next_run_at: datetime | None
    last_started_at: datetime | None
    last_completed_at: datetime | None
    consecutive_failures: int
    last_error_code: str
    config_generation: int
    lease_generation: int
    attempt_count: int


@dataclass(frozen=True, slots=True)
class GroupLearningScheduleClaim:
    chat_stream_id: str
    aspects: tuple[str, ...]
    interval_minutes: int
    window_hours: int
    config_generation: int
    lease: JobLease


@dataclass(frozen=True, slots=True)
class GroupLearningChatLogRecord:
    chat_log_id: int
    role: str
    user_id: str
    sender_name: str
    content: str
    meta_json: str
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class GroupLearningIncrementalLogs:
    chat_stream_id: str
    success_cursor: int
    context: tuple[GroupLearningChatLogRecord, ...]
    new: tuple[GroupLearningChatLogRecord, ...]


@runtime_checkable
class GroupLearningScheduleRepositoryPort(Protocol):
    def get_schedule(
        self,
        chat_stream_id: str,
    ) -> GroupLearningScheduleState | None: ...

    def put_schedule(
        self,
        write: GroupLearningScheduleWrite,
        *,
        now: datetime,
    ) -> GroupLearningScheduleState: ...

    def disable_schedule(
        self,
        chat_stream_id: str,
        *,
        now: datetime,
    ) -> GroupLearningScheduleState: ...

    def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: float,
    ) -> GroupLearningScheduleClaim | None: ...

    def load_incremental_logs(
        self,
        claim: GroupLearningScheduleClaim,
        *,
        now: datetime,
        context_limit: int,
        max_new_messages: int,
    ) -> GroupLearningIncrementalLogs: ...

    def settle_success(
        self,
        claim: GroupLearningScheduleClaim,
        *,
        now: datetime,
    ) -> GroupLearningScheduleState: ...

    def settle_failure(
        self,
        claim: GroupLearningScheduleClaim,
        *,
        error_code: str,
        retry_at: datetime,
        now: datetime,
    ) -> GroupLearningScheduleState: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


__all__ = [
    "GroupLearningChatLogRecord",
    "GroupLearningIncrementalLogs",
    "GroupLearningScheduleClaim",
    "GroupLearningScheduleLeaseLost",
    "GroupLearningScheduleRepositoryPort",
    "GroupLearningScheduleState",
    "GroupLearningScheduleWrite",
]

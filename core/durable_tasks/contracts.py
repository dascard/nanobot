"""Durable Task 的框架无关执行租约与只读视图合同。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _text(
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


class RunTaskKind(StrEnum):
    CHAT = "chat"
    SCHEDULED = "scheduled"
    PROACTIVE = "proactive"
    RESEARCH = "research"
    BACKGROUND = "background"
    RECOVERY = "recovery"


class RunTaskStatus(StrEnum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    AMBIGUOUS = "ambiguous"

    @property
    def terminal(self) -> bool:
        return self not in {self.ACCEPTED, self.RUNNING}


class RunTaskHeartbeatReason(StrEnum):
    RENEWED = "renewed"
    CANCEL_REQUESTED = "cancel_requested"
    TIMED_OUT = "timed_out"
    LEASE_LOST = "lease_lost"
    TERMINAL = "terminal"


class RunTaskError(RuntimeError):
    """Durable Task 控制错误基类。"""


class RunTaskConflict(RunTaskError):
    """任务身份、状态或幂等事实冲突。"""


class RunTaskLeaseLost(RunTaskError):
    """执行 owner 的 token、generation 或有效期已失效。"""


@dataclass(frozen=True, slots=True)
class RunTaskLease:
    run_id: str
    owner: str
    token: str
    generation: int
    attempt_no: int
    expires_at: datetime
    timeout_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "run_id",
            _text(self.run_id, max_chars=160, required=True),
        )
        object.__setattr__(
            self,
            "owner",
            _text(self.owner, max_chars=128, required=True),
        )
        object.__setattr__(
            self,
            "token",
            _text(self.token, max_chars=64, required=True),
        )
        if type(self.generation) is not int or self.generation <= 0:
            raise ValueError("generation 必须是正整数")
        if type(self.attempt_no) is not int or self.attempt_no <= 0:
            raise ValueError("attempt_no 必须是正整数")
        if not isinstance(self.expires_at, datetime):
            raise TypeError("expires_at 必须是 datetime")
        if self.timeout_at is not None and not isinstance(
            self.timeout_at,
            datetime,
        ):
            raise TypeError("timeout_at 必须是 datetime 或 None")


@dataclass(frozen=True, slots=True)
class RunTaskHeartbeat:
    renewed: bool
    reason: RunTaskHeartbeatReason
    lease: RunTaskLease | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reason",
            RunTaskHeartbeatReason(self.reason),
        )
        if self.renewed != (self.lease is not None):
            raise ValueError("renewed 与 lease 必须一致")
        if self.renewed and self.reason is not RunTaskHeartbeatReason.RENEWED:
            raise ValueError("续租成功必须使用 renewed reason")


@dataclass(frozen=True, slots=True)
class RunTaskView:
    run_id: str
    task_kind: RunTaskKind
    source_type: str
    source_id: str
    request_id_sha256: str
    idempotency_key_sha256: str
    status: RunTaskStatus
    lease_generation: int
    attempt_count: int
    lease_owner: str
    lease_expires_at: datetime | None
    timeout_at: datetime | None
    cancel_requested_at: datetime | None
    cancel_reason: str
    terminal_reason: str
    result_ref: str
    delivery_receipt_ref: str
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_kind", RunTaskKind(self.task_kind))
        object.__setattr__(self, "status", RunTaskStatus(self.status))
        for field_name in ("request_id_sha256", "idempotency_key_sha256"):
            value = str(getattr(self, field_name) or "")
            if _SHA256_PATTERN.fullmatch(value) is None:
                raise ValueError(f"{field_name} 必须是 SHA-256")

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "task_kind": self.task_kind.value,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "request_id_sha256": self.request_id_sha256,
            "idempotency_key_sha256": self.idempotency_key_sha256,
            "status": self.status.value,
            "lease": {
                "active": self.status is RunTaskStatus.RUNNING,
                "owner": self.lease_owner,
                "generation": self.lease_generation,
                "attempt_no": self.attempt_count,
                "expires_at": (
                    self.lease_expires_at.isoformat()
                    if self.lease_expires_at is not None
                    else None
                ),
            },
            "timeout_at": (
                self.timeout_at.isoformat()
                if self.timeout_at is not None
                else None
            ),
            "cancel_requested_at": (
                self.cancel_requested_at.isoformat()
                if self.cancel_requested_at is not None
                else None
            ),
            "cancel_reason": self.cancel_reason,
            "terminal_reason": self.terminal_reason,
            "result_ref": self.result_ref,
            "delivery_receipt_ref": self.delivery_receipt_ref,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "finished_at": (
                self.finished_at.isoformat()
                if self.finished_at is not None
                else None
            ),
        }


__all__ = [
    "RunTaskConflict",
    "RunTaskError",
    "RunTaskHeartbeat",
    "RunTaskHeartbeatReason",
    "RunTaskKind",
    "RunTaskLease",
    "RunTaskLeaseLost",
    "RunTaskStatus",
    "RunTaskView",
]

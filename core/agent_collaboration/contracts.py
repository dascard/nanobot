"""任务板、handoff 与人工复核的框架无关协作合同。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import hashlib

from core.agent_orchestration.contracts import canonical_json_bytes, plain_json
from core.agent_runtime.contracts import RuntimeRunIdentity
from core.durable_tasks import RunTaskLease


MAX_COLLABORATION_EVENT_BYTES = 768 * 1024


class AgentCollaborationEventKind(StrEnum):
    BOARD_CREATED = "board_created"
    AGENT_INVITED = "agent_invited"
    TASK_CLAIMED = "task_claimed"
    DELIVERABLE_SUBMITTED = "deliverable_submitted"
    DELIVERABLE_APPROVED = "deliverable_approved"
    DELIVERABLE_REJECTED = "deliverable_rejected"


class AgentCollaborationError(RuntimeError):
    """对 API、群聊和 Agent Link 保持稳定的协作错误。"""

    def __init__(self, code: str, safe_message: str) -> None:
        self.code = _identifier(code, "协作错误码", max_chars=128)
        self.safe_message = _text(safe_message, "协作错误说明", max_chars=500)
        super().__init__(f"{self.code}: {self.safe_message}")


class AgentCollaborationConflict(AgentCollaborationError):
    """幂等、租约、版本或状态冲突。"""


class AgentCollaborationNotFound(AgentCollaborationError):
    """资源不存在或 owner 不匹配；两种情况不做区分。"""


class AgentCollaborationAccessDenied(AgentCollaborationError):
    """当前 actor 没有协作任务访问权。"""


def _text(value: object, name: str, *, max_chars: int) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > max_chars
        or any(ord(character) < 32 for character in normalized)
    ):
        raise ValueError(f"{name} 无效")
    return normalized


def _identifier(value: object, name: str, *, max_chars: int = 160) -> str:
    normalized = _text(value, name, max_chars=max_chars)
    if any(character.isspace() for character in normalized):
        raise ValueError(f"{name} 不能包含空白")
    return normalized


def _sha256(value: object, name: str, *, allow_empty: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized and allow_empty:
        return ""
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{name} 必须是 SHA-256")
    return normalized


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} 必须包含时区")
    return value


def _json_mapping(value: Mapping[str, object], name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        raise ValueError(f"{name} 必须是字符串 key 的 JSON 对象")
    payload = plain_json(value)
    if not isinstance(payload, dict):
        raise ValueError(f"{name} 必须是 JSON 对象")
    canonical_json_bytes(payload)
    return payload


@dataclass(frozen=True, slots=True)
class AgentCollaborationBoard:
    board_id: str
    identity: RuntimeRunIdentity
    plan_id: str
    plan_revision: int
    plan_sha256: str
    approval_id: str
    freeze_id: str
    root_input: Mapping[str, object]
    source_type: str
    source_id: str
    created_by: str
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "board_id", _identifier(self.board_id, "board_id"))
        if not isinstance(self.identity, RuntimeRunIdentity):
            raise ValueError("board identity 无效")
        object.__setattr__(self, "plan_id", _identifier(self.plan_id, "plan_id"))
        if type(self.plan_revision) is not int or self.plan_revision < 1:
            raise ValueError("plan_revision 必须是正整数")
        object.__setattr__(
            self,
            "plan_sha256",
            _sha256(self.plan_sha256, "plan_sha256"),
        )
        object.__setattr__(
            self,
            "approval_id",
            _identifier(self.approval_id, "approval_id"),
        )
        object.__setattr__(
            self,
            "freeze_id",
            _identifier(self.freeze_id, "freeze_id"),
        )
        object.__setattr__(
            self,
            "root_input",
            _json_mapping(self.root_input, "root_input"),
        )
        object.__setattr__(
            self,
            "source_type",
            _identifier(self.source_type, "source_type", max_chars=64),
        )
        object.__setattr__(
            self,
            "source_id",
            _identifier(self.source_id, "source_id"),
        )
        object.__setattr__(
            self,
            "created_by",
            _identifier(self.created_by, "created_by"),
        )
        _aware(self.created_at, "created_at")
        _aware(self.expires_at, "expires_at")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at 必须晚于 created_at")

    @property
    def root_input_sha256(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.root_input)).hexdigest()


@dataclass(frozen=True, slots=True)
class AgentCollaborationEvent:
    event_id: str
    board_id: str
    sequence: int
    kind: AgentCollaborationEventKind
    actor_id: str
    target_actor_id: str
    task_id: str
    delivery_id: str
    payload: Mapping[str, object]
    idempotency_key_sha256: str
    request_sha256: str
    occurred_at: datetime
    expires_at: datetime | None = None
    previous_event_sha256: str = ""
    event_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _identifier(self.event_id, "event_id"))
        object.__setattr__(self, "board_id", _identifier(self.board_id, "board_id"))
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("event sequence 必须是正整数")
        object.__setattr__(self, "kind", AgentCollaborationEventKind(self.kind))
        object.__setattr__(self, "actor_id", _identifier(self.actor_id, "actor_id"))
        for name, maximum in (
            ("target_actor_id", 160),
            ("task_id", 128),
            ("delivery_id", 160),
        ):
            raw = str(getattr(self, name) or "").strip()
            if raw:
                raw = _identifier(raw, name, max_chars=maximum)
            object.__setattr__(self, name, raw)
        payload = _json_mapping(self.payload, "event payload")
        if len(canonical_json_bytes(payload)) > MAX_COLLABORATION_EVENT_BYTES:
            raise ValueError("event payload 超过大小限制")
        object.__setattr__(self, "payload", payload)
        object.__setattr__(
            self,
            "idempotency_key_sha256",
            _sha256(self.idempotency_key_sha256, "idempotency_key_sha256"),
        )
        object.__setattr__(
            self,
            "request_sha256",
            _sha256(self.request_sha256, "request_sha256"),
        )
        _aware(self.occurred_at, "occurred_at")
        if self.expires_at is not None:
            _aware(self.expires_at, "expires_at")
            if self.expires_at <= self.occurred_at:
                raise ValueError("event expires_at 必须晚于 occurred_at")
        previous = _sha256(
            self.previous_event_sha256,
            "previous_event_sha256",
            allow_empty=True,
        )
        if (self.sequence == 1) != (not previous):
            raise ValueError("event previous digest 与 sequence 不一致")
        object.__setattr__(self, "previous_event_sha256", previous)
        digest = hashlib.sha256(
            canonical_json_bytes(self.to_dict(include_hash=False))
        ).hexdigest()
        declared = str(self.event_sha256 or "").strip().lower()
        if declared and _sha256(declared, "event_sha256") != digest:
            raise ValueError("event_sha256 与内容不一致")
        object.__setattr__(self, "event_sha256", digest)

    @property
    def payload_sha256(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.payload)).hexdigest()

    @property
    def payload_size_bytes(self) -> int:
        return len(canonical_json_bytes(self.payload))

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "event_id": self.event_id,
            "board_id": self.board_id,
            "sequence": self.sequence,
            "kind": self.kind.value,
            "actor_id": self.actor_id,
            "target_actor_id": self.target_actor_id,
            "task_id": self.task_id,
            "delivery_id": self.delivery_id,
            "payload": plain_json(self.payload),
            "idempotency_key_sha256": self.idempotency_key_sha256,
            "request_sha256": self.request_sha256,
            "occurred_at": self.occurred_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "previous_event_sha256": self.previous_event_sha256,
        }
        if include_hash:
            payload["event_sha256"] = self.event_sha256
        return payload


@dataclass(frozen=True, slots=True)
class AgentCollaborationClaim:
    board_id: str
    task_id: str
    actor_id: str
    lease: RunTaskLease
    task_payload: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "board_id", _identifier(self.board_id, "board_id"))
        object.__setattr__(self, "task_id", _identifier(self.task_id, "task_id"))
        object.__setattr__(self, "actor_id", _identifier(self.actor_id, "actor_id"))
        if not isinstance(self.lease, RunTaskLease):
            raise ValueError("claim lease 无效")
        object.__setattr__(
            self,
            "task_payload",
            _json_mapping(self.task_payload, "task_payload"),
        )

    def to_dict(self) -> dict[str, object]:
        """原始 token 只能出现在认领响应，不能进入事件或状态投影。"""

        return {
            "board_id": self.board_id,
            "task_id": self.task_id,
            "actor_id": self.actor_id,
            "lease": {
                "token": self.lease.token,
                "generation": self.lease.generation,
                "attempt_no": self.lease.attempt_no,
                "expires_at": self.lease.expires_at.isoformat(),
                "timeout_at": (
                    self.lease.timeout_at.isoformat()
                    if self.lease.timeout_at is not None
                    else None
                ),
            },
            "task": plain_json(self.task_payload),
        }


__all__ = [
    "AgentCollaborationAccessDenied",
    "AgentCollaborationBoard",
    "AgentCollaborationClaim",
    "AgentCollaborationConflict",
    "AgentCollaborationError",
    "AgentCollaborationEvent",
    "AgentCollaborationEventKind",
    "AgentCollaborationNotFound",
    "MAX_COLLABORATION_EVENT_BYTES",
]

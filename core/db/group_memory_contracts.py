"""群体记忆持久化的不可变 DTO 与 Repository Port。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class GroupMemoryRecord:
    id: int
    chat_stream_id: str
    group_id: str
    memory_type: str
    content: str
    content_hash: str
    cluster_key: str
    evidence_log_ids_json: str
    confidence: float
    evidence_count: int
    first_seen: datetime | None
    last_seen: datetime | None
    updated_at: datetime | None
    decay_score: float
    status: str
    inject_policy: str
    disabled_reason: str
    rejected_reason: str
    merged_into_id: int | None
    last_injected_at: datetime | None
    injected_count: int
    source: str
    meta_json: str
    created_at: datetime | None
    approval_source: str = ""
    governance_mode: str = ""
    approved_content_hash: str = ""
    model_review_run_id: str = ""
    model_contract_version: str = ""
    human_reviewer_id: str = ""
    human_reviewed_at: datetime | None = None
    human_action: str = ""
    conflict_group_id: str = ""
    version: int = 1


@dataclass(frozen=True, slots=True)
class ChatStreamConfigRecord:
    chat_stream_id: str
    group_profile_mode: str


@dataclass(frozen=True, slots=True)
class GroupLogSummaryRecord:
    session_id: str
    user_id: str
    log_count: int
    latest_at: datetime | None
    session_name: str


@dataclass(frozen=True, slots=True)
class GroupUserRecord:
    user_id: str
    name: str


@runtime_checkable
class GroupMemoryQueryRepositoryPort(Protocol):
    def list_memories(
        self,
        *,
        chat_stream_id: str,
        legacy_group_ids: Sequence[str],
        memory_type: str = "",
        limit: int = 100,
    ) -> Sequence[GroupMemoryRecord]: ...

    def get_memory(self, memory_id: int) -> GroupMemoryRecord | None: ...

    def find_duplicate(
        self,
        *,
        chat_stream_id: str,
        legacy_group_ids: Sequence[str],
        memory_type: str,
        content_hash: str,
        exclude_id: int,
    ) -> GroupMemoryRecord | None: ...

    def list_all_memories(self) -> Sequence[GroupMemoryRecord]: ...

    def list_group_log_summaries(self) -> Sequence[GroupLogSummaryRecord]: ...

    def list_group_users(self) -> Sequence[GroupUserRecord]: ...

    def list_stream_configs(self) -> Sequence[ChatStreamConfigRecord]: ...

    def get_stream_config(
        self,
        chat_stream_ids: Sequence[str],
    ) -> ChatStreamConfigRecord | None: ...


@runtime_checkable
class GroupMemoryCommandRepositoryPort(Protocol):
    def set_injection_mode(
        self,
        chat_stream_id: str,
        mode: str,
    ) -> ChatStreamConfigRecord: ...

    def update_memory(
        self,
        memory_id: int,
        **values: object,
    ) -> GroupMemoryRecord: ...

    def mark_injected(
        self,
        memory_ids: Sequence[int],
        *,
        injected_at: datetime,
    ) -> int: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@runtime_checkable
class GroupMemoryRepositoryPort(
    GroupMemoryQueryRepositoryPort,
    GroupMemoryCommandRepositoryPort,
    Protocol,
):
    """群体记忆 Query/Command 共享的持久化端口。"""


class GroupMemoryRepositoryConflict(RuntimeError):
    """数据库唯一约束或并发更新发生冲突。"""


def group_memory_record_to_dict(
    record: GroupMemoryRecord,
) -> Mapping[str, object]:
    """供旧字典消费者迁移使用的稳定投影。"""

    return {
        "id": record.id,
        "chat_stream_id": record.chat_stream_id,
        "group_id": record.group_id,
        "memory_type": record.memory_type,
        "content": record.content,
        "content_hash": record.content_hash,
        "cluster_key": record.cluster_key,
        "confidence": record.confidence,
        "evidence_count": record.evidence_count,
        "decay_score": record.decay_score,
        "first_seen": (
            record.first_seen.strftime("%Y-%m-%d")
            if record.first_seen
            else ""
        ),
        "last_seen": (
            record.last_seen.strftime("%Y-%m-%d")
            if record.last_seen
            else ""
        ),
        "updated_at": (
            record.updated_at.strftime("%Y-%m-%d %H:%M")
            if record.updated_at
            else ""
        ),
        "status": record.status,
        "source": record.source,
        "inject_policy": record.inject_policy,
        "disabled_reason": record.disabled_reason,
        "rejected_reason": record.rejected_reason,
        "merged_into_id": record.merged_into_id,
        "last_injected_at": (
            record.last_injected_at.strftime("%Y-%m-%d %H:%M")
            if record.last_injected_at
            else ""
        ),
        "injected_count": record.injected_count,
        "evidence_log_ids_json": record.evidence_log_ids_json,
        "meta_json": record.meta_json,
    }


__all__ = [
    "ChatStreamConfigRecord",
    "GroupLogSummaryRecord",
    "GroupMemoryCommandRepositoryPort",
    "GroupMemoryQueryRepositoryPort",
    "GroupMemoryRecord",
    "GroupMemoryRepositoryConflict",
    "GroupMemoryRepositoryPort",
    "GroupUserRecord",
    "group_memory_record_to_dict",
]

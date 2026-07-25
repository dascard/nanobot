"""阶段 7D 旧群学习数据迁移的显式 Command Port。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class LegacyGroupLearningMigrationWrite:
    source_ref: str
    source: str
    legacy_id: int
    existing_group_memory_id: int | None
    chat_stream_id: str
    candidate_id: str
    candidate_type: str
    content: str
    meaning: str
    normalized_key: str
    fingerprint: str
    content_hash: str
    planned_status: str
    approval_source: str
    approved_content_hash: str
    human_reviewer_id: str
    human_reviewed_at: datetime | None
    human_action: str
    model_review_run_id: str
    model_contract_version: str
    conflict_group_id: str
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class LegacyGroupLearningMigrationResult:
    source_count: int
    planned_count: int
    migrated_count: int
    replayed_count: int
    human_promoted_count: int
    legacy_group_memory_downgraded_count: int
    run_count: int
    source_sha256: str
    planned_sha256: str


@dataclass(frozen=True, slots=True)
class LegacyGroupLearningPersistResult:
    migrated_count: int
    replayed_count: int
    human_promoted_count: int
    legacy_group_memory_downgraded_count: int
    run_count: int


@runtime_checkable
class GroupLearningLegacyMigrationRepositoryPort(Protocol):
    """只允许迁移候选、关联正式记忆和收紧旧 GroupMemory。"""

    def apply_legacy_migration(
        self,
        writes: Sequence[LegacyGroupLearningMigrationWrite],
        *,
        planned_sha256: str,
        actor: str,
        migrated_at: datetime,
    ) -> LegacyGroupLearningPersistResult: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


__all__ = [
    "GroupLearningLegacyMigrationRepositoryPort",
    "LegacyGroupLearningMigrationResult",
    "LegacyGroupLearningMigrationWrite",
    "LegacyGroupLearningPersistResult",
]

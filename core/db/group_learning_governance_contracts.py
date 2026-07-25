"""阶段 7C 群学习治理写面的不可变合同。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class GroupLearningGovernanceResult:
    run_id: str
    status: str
    promoted_count: int = 0
    merged_count: int = 0
    alias_count: int = 0
    rejected_count: int = 0
    conflict_count: int = 0
    waiting_count: int = 0


@dataclass(frozen=True, slots=True)
class GroupLearningHumanGovernanceWrite:
    candidate_id: str
    reviewer_id: str
    action: str
    reviewed_content: str
    reviewed_meaning: str
    reviewed_content_hash: str
    reviewed_at: datetime
    target_memory_id: int | None = None
    conflict_resolution: str = ""


@runtime_checkable
class GroupLearningGovernanceRepositoryPort(Protocol):
    """正式 GroupMemory 晋级专用写面，不与 7B candidate-only 写面混用。"""

    def settle_model_run(
        self,
        *,
        run_id: str,
        chat_stream_id: str,
        settled_at: datetime,
    ) -> GroupLearningGovernanceResult: ...

    def apply_human_governance(
        self,
        write: GroupLearningHumanGovernanceWrite,
    ) -> GroupLearningGovernanceResult: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


__all__ = [
    "GroupLearningGovernanceRepositoryPort",
    "GroupLearningGovernanceResult",
    "GroupLearningHumanGovernanceWrite",
]

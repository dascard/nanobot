"""阶段 7C 群学习正式治理应用服务。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import hashlib

from core.db.group_learning_governance_contracts import (
    GroupLearningGovernanceRepositoryPort,
    GroupLearningGovernanceResult,
    GroupLearningHumanGovernanceWrite,
)
from core.settings_service import settings
from core.time_utils import db_now_naive


def _reviewed_hash(content: str, meaning: str) -> str:
    return hashlib.sha256(
        f"{content}\0{meaning}".encode("utf-8")
    ).hexdigest()


class GroupLearningGovernanceService:
    """在全局开关允许时原子执行正式记忆治理。"""

    def __init__(
        self,
        *,
        repository: GroupLearningGovernanceRepositoryPort,
        enabled: Callable[[], bool] | None = None,
    ) -> None:
        self.repository = repository
        self._enabled = enabled or (
            lambda: settings.get_bool(
                "group_learning.enabled",
                False,
            )
        )

    @staticmethod
    def _disabled(run_id: str) -> GroupLearningGovernanceResult:
        return GroupLearningGovernanceResult(
            run_id=str(run_id or "").strip(),
            status="disabled",
        )

    def settle_model_run(
        self,
        *,
        run_id: str,
        chat_stream_id: str,
        settled_at: datetime | None = None,
    ) -> GroupLearningGovernanceResult:
        if not self._enabled():
            return self._disabled(run_id)
        try:
            result = self.repository.settle_model_run(
                run_id=str(run_id or "").strip(),
                chat_stream_id=str(chat_stream_id or "").strip(),
                settled_at=settled_at or db_now_naive(),
            )
            self.repository.commit()
            return result
        except BaseException:
            self.repository.rollback()
            raise

    def review_human_candidate(
        self,
        *,
        candidate_id: str,
        reviewer_id: str,
        action: str,
        reviewed_content: str,
        reviewed_meaning: str = "",
        target_memory_id: int | None = None,
        conflict_resolution: str = "",
        reviewed_at: datetime | None = None,
    ) -> GroupLearningGovernanceResult:
        if not self._enabled():
            return self._disabled("")
        content = str(reviewed_content or "").strip()
        meaning = str(reviewed_meaning or "").strip()
        write = GroupLearningHumanGovernanceWrite(
            candidate_id=str(candidate_id or "").strip(),
            reviewer_id=str(reviewer_id or "").strip(),
            action=str(action or "").strip(),
            reviewed_content=content,
            reviewed_meaning=meaning,
            reviewed_content_hash=_reviewed_hash(content, meaning),
            reviewed_at=reviewed_at or db_now_naive(),
            target_memory_id=target_memory_id,
            conflict_resolution=str(
                conflict_resolution or ""
            ).strip(),
        )
        try:
            result = self.repository.apply_human_governance(write)
            self.repository.commit()
            return result
        except BaseException:
            self.repository.rollback()
            raise


__all__ = ["GroupLearningGovernanceService"]

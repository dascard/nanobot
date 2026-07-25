"""群学习 Descriptor、白名单、候选、证据和运行的只读查询服务。"""

from __future__ import annotations

from dataclasses import dataclass

from core.chat_stream_identity import parse_canonical_chat_stream_id
from core.db.group_learning_contracts import (
    GroupLearningCandidateRecord,
    GroupLearningEvidenceRecord,
    GroupLearningQueryRepositoryPort,
    GroupLearningRunRecord,
    GroupLearningScheduleRecord,
    GroupLearningStreamStateRecord,
)
from core.group_learning import (
    GROUP_ANALYSIS_ASPECT_REGISTRY,
    GROUP_LEARNING_EVIDENCE_POLICY_REGISTRY,
    LEARNING_SIGNAL_RULE_REGISTRY,
    GroupAnalysisAspectDescriptor,
    LearningSignalRuleDescriptor,
    list_group_analysis_aspects,
)
from core.group_learning.evidence import EvidencePolicyDescriptor


def require_canonical_group_stream_id(value: object) -> str:
    """拒绝旧别名、私聊身份和非规范化序列化。"""

    normalized = str(value or "")
    identity = parse_canonical_chat_stream_id(normalized)
    if identity.chat_type != "group":
        raise ValueError("群学习只接受 canonical group chat_stream_id")
    return identity.chat_stream_id


@dataclass(frozen=True, slots=True)
class GroupLearningOverview:
    chat_stream_id: str
    schedule: GroupLearningScheduleRecord | None
    stream_state: GroupLearningStreamStateRecord | None
    aspects: tuple[GroupAnalysisAspectDescriptor, ...]
    rules: tuple[LearningSignalRuleDescriptor, ...]
    evidence_policies: tuple[EvidencePolicyDescriptor, ...]
    aspect_registry_sha256: str
    rule_registry_sha256: str
    evidence_policy_registry_sha256: str


@dataclass(frozen=True, slots=True)
class GroupLearningCandidatePage:
    chat_stream_id: str
    items: tuple[GroupLearningCandidateRecord, ...]
    next_after_id: int | None


@dataclass(frozen=True, slots=True)
class GroupLearningCandidateDetail:
    candidate: GroupLearningCandidateRecord
    evidence: tuple[GroupLearningEvidenceRecord, ...]
    next_evidence_after_id: int | None


class GroupLearningQueryService:
    """只组装只读视图，不向 Repository 暴露任何写操作。"""

    def __init__(
        self,
        repository: GroupLearningQueryRepositoryPort,
    ) -> None:
        self.repository = repository

    def overview(self, chat_stream_id: str) -> GroupLearningOverview:
        canonical_id = require_canonical_group_stream_id(chat_stream_id)
        return GroupLearningOverview(
            chat_stream_id=canonical_id,
            schedule=self.repository.get_schedule(canonical_id),
            stream_state=self.repository.get_stream_state(canonical_id),
            aspects=list_group_analysis_aspects(),
            rules=tuple(LEARNING_SIGNAL_RULE_REGISTRY),
            evidence_policies=tuple(
                GROUP_LEARNING_EVIDENCE_POLICY_REGISTRY
            ),
            aspect_registry_sha256=(
                GROUP_ANALYSIS_ASPECT_REGISTRY.sha256
            ),
            rule_registry_sha256=(
                LEARNING_SIGNAL_RULE_REGISTRY.sha256
            ),
            evidence_policy_registry_sha256=(
                GROUP_LEARNING_EVIDENCE_POLICY_REGISTRY.sha256
            ),
        )

    def list_candidates(
        self,
        chat_stream_id: str,
        *,
        candidate_type: str = "",
        status: str = "",
        after_id: int = 0,
        limit: int = 100,
    ) -> GroupLearningCandidatePage:
        canonical_id = require_canonical_group_stream_id(chat_stream_id)
        bounded_limit = max(1, min(int(limit), 200))
        rows = tuple(self.repository.list_candidates(
            chat_stream_id=canonical_id,
            candidate_type=str(candidate_type or "").strip(),
            status=str(status or "").strip(),
            after_id=max(0, int(after_id)),
            limit=bounded_limit,
        ))
        return GroupLearningCandidatePage(
            chat_stream_id=canonical_id,
            items=rows,
            next_after_id=(
                rows[-1].id if len(rows) == bounded_limit else None
            ),
        )

    def candidate_detail(
        self,
        candidate_id: str,
        *,
        evidence_after_id: int = 0,
        evidence_limit: int = 100,
    ) -> GroupLearningCandidateDetail:
        normalized_id = str(candidate_id or "").strip()
        candidate = self.repository.get_candidate(normalized_id)
        if candidate is None:
            raise LookupError("群学习候选不存在")
        require_canonical_group_stream_id(candidate.chat_stream_id)
        bounded_limit = max(1, min(int(evidence_limit), 200))
        evidence = tuple(self.repository.list_evidence(
            candidate_id=normalized_id,
            after_id=max(0, int(evidence_after_id)),
            limit=bounded_limit,
        ))
        return GroupLearningCandidateDetail(
            candidate=candidate,
            evidence=evidence,
            next_evidence_after_id=(
                evidence[-1].id
                if len(evidence) == bounded_limit
                else None
            ),
        )

    def list_runs(
        self,
        chat_stream_id: str,
        *,
        limit: int = 100,
    ) -> tuple[GroupLearningRunRecord, ...]:
        canonical_id = require_canonical_group_stream_id(chat_stream_id)
        return tuple(self.repository.list_runs(
            chat_stream_id=canonical_id,
            limit=max(1, min(int(limit), 200)),
        ))


__all__ = [
    "GroupLearningCandidateDetail",
    "GroupLearningCandidatePage",
    "GroupLearningOverview",
    "GroupLearningQueryService",
    "require_canonical_group_stream_id",
]

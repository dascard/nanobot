"""阶段 7C 群学习正式治理写面的 SQLAlchemy Adapter。"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json

from sqlalchemy.orm import Session

from core.chat_stream_identity import parse_canonical_chat_stream_id
from core.db.group_learning_governance_contracts import (
    GroupLearningGovernanceRepositoryPort,
    GroupLearningGovernanceResult,
    GroupLearningHumanGovernanceWrite,
)
from core.db.models import (
    GroupLearningCandidate,
    GroupLearningEvidence,
    GroupLearningRun,
    GroupLearningStreamState,
    GroupMemory,
)
from core.group_learning import (
    EvidenceFact,
    canonicalize_learning_text,
    evaluate_evidence_policy,
    evidence_policy_for,
)
from core.group_learning.states import (
    GROUP_LEARNING_CONFLICT_RESOLUTIONS,
    GROUP_LEARNING_HUMAN_ACTIONS,
    GROUP_LEARNING_MODEL_ACTIONS,
)


_POSITIVE_MODEL_ACTIONS = frozenset({
    "new",
    "merge_into",
    "add_alias",
})
_MODEL_ACTIONS = frozenset(GROUP_LEARNING_MODEL_ACTIONS)
_HUMAN_ACTIONS = frozenset(GROUP_LEARNING_HUMAN_ACTIONS)
_HUMAN_CONFLICT_RESOLUTIONS = frozenset(
    GROUP_LEARNING_CONFLICT_RESOLUTIONS
)


def _sha256(*parts: object) -> str:
    return hashlib.sha256(
        "\0".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()


def _memory_content(content: str, meaning: str) -> str:
    return f"{content}：{meaning}" if meaning else content


def _memory_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _admin_content_hash(content: str) -> str:
    """管理端归一化 32 位哈希;延迟导入避免 core.db↔core.group_memory 循环。"""
    from core.group_memory import _content_hash

    return _content_hash(content)


def _conflict_group_id(
    *,
    chat_stream_id: str,
    candidate_id: str,
    target_memory_id: int = 0,
) -> str:
    digest = _sha256(
        chat_stream_id,
        candidate_id,
        target_memory_id,
    )
    return f"glconf_{digest[:48]}"


def _parse_memory_meta(row: GroupMemory) -> dict[str, object]:
    try:
        parsed = json.loads(str(row.meta_json or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class SqlAlchemyGroupLearningGovernanceRepository:
    """原子结算 Candidate、正式记忆、Run 和 success cursor。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _candidate(
        self,
        candidate_id: str,
    ) -> GroupLearningCandidate:
        row = (
            self._session.query(GroupLearningCandidate)
            .filter(
                GroupLearningCandidate.candidate_id
                == str(candidate_id or "").strip()
            )
            .first()
        )
        if row is None:
            raise LookupError("group learning candidate not found")
        return row

    def _evidence(
        self,
        candidate_id: str,
    ) -> tuple[GroupLearningEvidence, ...]:
        return tuple(
            self._session.query(GroupLearningEvidence)
            .filter(
                GroupLearningEvidence.candidate_id == candidate_id
            )
            .order_by(
                GroupLearningEvidence.chat_log_id.asc(),
                GroupLearningEvidence.id.asc(),
            )
            .all()
        )

    @staticmethod
    def _evidence_decision(
        candidate: GroupLearningCandidate,
        evidence: tuple[GroupLearningEvidence, ...],
    ):
        return evaluate_evidence_policy(
            str(candidate.candidate_type),
            tuple(
                EvidenceFact(
                    sender_id=str(row.sender_id or ""),
                    batch_id=str(row.batch_id or ""),
                    evidence_kind=str(row.evidence_kind or "message"),
                )
                for row in evidence
            ),
        )

    def _exact_memory(
        self,
        *,
        chat_stream_id: str,
        memory_type: str,
        content_hash: str,
        alternate_hashes: tuple[str, ...] = (),
    ) -> GroupMemory | None:
        # 管理端编辑过的行使用归一化 32 位 content_hash,治理去重必须
        # 同时匹配两种格式,否则同文候选会绕过去重重复入库。
        hashes = sorted({
            str(content_hash),
            *(str(item) for item in alternate_hashes if str(item)),
        })
        return (
            self._session.query(GroupMemory)
            .filter(
                GroupMemory.chat_stream_id == chat_stream_id,
                GroupMemory.memory_type == memory_type,
                GroupMemory.content_hash.in_(hashes),
            )
            .first()
        )

    def _same_key_memory(
        self,
        *,
        chat_stream_id: str,
        memory_type: str,
        normalized_key: str,
        approved_content_hash: str,
    ) -> GroupMemory | None:
        rows = (
            self._session.query(GroupMemory)
            .filter(
                GroupMemory.chat_stream_id == chat_stream_id,
                GroupMemory.memory_type == memory_type,
                GroupMemory.status == "active",
            )
            .order_by(GroupMemory.id.asc())
            .all()
        )
        for row in rows:
            meta = _parse_memory_meta(row)
            stored_key = canonicalize_learning_text(
                meta.get("normalized_key")
                or row.cluster_key
                or str(row.content or "").split("：", 1)[0]
            ).casefold()
            if (
                stored_key == normalized_key.casefold()
                and str(row.approved_content_hash or "")
                != approved_content_hash
            ):
                return row
        return None

    def _target_memory(
        self,
        *,
        memory_id: int | None,
        chat_stream_id: str,
        memory_type: str,
    ) -> GroupMemory:
        if memory_id is None:
            raise ValueError("群学习治理动作缺少目标记忆")
        row = self._session.get(GroupMemory, int(memory_id))
        if (
            row is None
            or str(row.chat_stream_id or "") != chat_stream_id
            or str(row.memory_type or "") != memory_type
            or str(row.status or "") != "active"
        ):
            raise ValueError("群学习治理目标不是当前会话同类型 active 记忆")
        return row

    def _mark_human_target(
        self,
        memory: GroupMemory,
        *,
        reviewer_id: str,
        action: str,
        reviewed_at: datetime,
        approved_content_hash: str | None = None,
    ) -> None:
        memory.approval_source = "human"
        memory.governance_mode = "human_managed"
        if approved_content_hash is not None:
            memory.approved_content_hash = approved_content_hash
        memory.human_reviewer_id = reviewer_id
        memory.human_reviewed_at = reviewed_at
        memory.human_action = action
        memory.version = int(memory.version or 1) + 1
        memory.updated_at = reviewed_at

    def _insert_memory(
        self,
        *,
        candidate: GroupLearningCandidate,
        content: str,
        meaning: str,
        approved_content_hash: str,
        evidence: tuple[GroupLearningEvidence, ...],
        approval_source: str,
        governance_mode: str,
        approved_at: datetime,
        human_reviewer_id: str = "",
        human_action: str = "",
    ) -> tuple[GroupMemory, bool]:
        identity = parse_canonical_chat_stream_id(
            str(candidate.chat_stream_id)
        )
        if identity.chat_type != "group":
            raise ValueError("群学习治理只接受 canonical group session")
        rendered_content = _memory_content(content, meaning)
        content_hash = _memory_content_hash(rendered_content)
        existing = self._exact_memory(
            chat_stream_id=identity.chat_stream_id,
            memory_type=str(candidate.candidate_type),
            content_hash=content_hash,
            alternate_hashes=(_admin_content_hash(rendered_content),),
        )
        if existing is not None:
            return existing, False

        policy = evidence_policy_for(str(candidate.candidate_type))
        evidence_ids = sorted({
            int(row.chat_log_id)
            for row in evidence
            if int(row.chat_log_id or 0) > 0
        })
        memory = GroupMemory(
            chat_stream_id=identity.chat_stream_id,
            group_id=identity.legacy_runtime_session_id,
            memory_type=str(candidate.candidate_type),
            content=rendered_content,
            content_hash=content_hash,
            cluster_key=str(candidate.normalized_key or ""),
            evidence_log_ids_json=json.dumps(
                evidence_ids,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            confidence=1.0 if approval_source == "human" else 0.9,
            evidence_count=len(evidence_ids),
            first_seen=approved_at,
            last_seen=approved_at,
            updated_at=approved_at,
            decay_score=1.0,
            status="active",
            inject_policy="auto",
            source="group_learning",
            meta_json=json.dumps(
                {
                    "candidate_id": str(candidate.candidate_id),
                    "evidence_policy_id": policy.policy_id,
                    "evidence_policy_version": policy.version,
                    "meaning": meaning,
                    "normalized_key": str(
                        candidate.normalized_key or ""
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            approval_source=approval_source,
            governance_mode=governance_mode,
            approved_content_hash=approved_content_hash,
            model_review_run_id=(
                str(candidate.model_review_run_id or "")
                if approval_source == "model"
                else None
            ),
            model_contract_version=(
                str(candidate.model_contract_version or "")
                if approval_source == "model"
                else None
            ),
            human_reviewer_id=human_reviewer_id or None,
            human_reviewed_at=(
                approved_at if approval_source == "human" else None
            ),
            human_action=human_action or None,
            version=1,
            created_at=approved_at,
        )
        self._session.add(memory)
        self._session.flush()
        return memory, True

    @staticmethod
    def _reviewed_material(
        candidate: GroupLearningCandidate,
    ) -> tuple[str, str, str]:
        content = str(
            candidate.reviewed_content or candidate.content or ""
        ).strip()
        meaning = str(
            candidate.reviewed_meaning
            if candidate.reviewed_meaning is not None
            else candidate.meaning or ""
        ).strip()
        if not content:
            raise ValueError("群学习审核内容不能为空")
        approved_hash = _sha256(content, meaning)
        stored_hash = str(candidate.reviewed_content_hash or "")
        if stored_hash and stored_hash != approved_hash:
            raise ValueError("群学习审核内容 Hash 不匹配")
        return content, meaning, approved_hash

    @staticmethod
    def _mark_candidate(
        candidate: GroupLearningCandidate,
        *,
        status: str,
        approval_source: str,
        settled_at: datetime,
        promoted_memory_id: int | None = None,
        waiting_reason: str = "",
        rejection_reason: str = "",
        conflict_group_id: str | None = None,
    ) -> None:
        candidate.status = status
        candidate.approval_source = approval_source
        candidate.promoted_group_memory_id = promoted_memory_id
        candidate.waiting_reason_code = waiting_reason
        candidate.rejection_reason_code = rejection_reason
        if conflict_group_id is not None:
            candidate.conflict_group_id = conflict_group_id
        candidate.version = int(candidate.version or 1) + 1
        candidate.updated_at = settled_at

    def _settle_positive_candidate(
        self,
        *,
        candidate: GroupLearningCandidate,
        action: str,
        settled_at: datetime,
    ) -> str:
        content, meaning, approved_hash = self._reviewed_material(
            candidate
        )
        evidence = self._evidence(str(candidate.candidate_id))
        policy = self._evidence_decision(candidate, evidence)
        if not policy.eligible:
            self._mark_candidate(
                candidate,
                status="waiting_for_evidence",
                approval_source="model",
                settled_at=settled_at,
                waiting_reason=policy.reason_code,
            )
            return "waiting"

        if action in {"merge_into", "add_alias"}:
            target_id = (
                candidate.merge_target_memory_id
                if action == "merge_into"
                else candidate.alias_target_memory_id
            )
            target = self._target_memory(
                memory_id=target_id,
                chat_stream_id=str(candidate.chat_stream_id),
                memory_type=str(candidate.candidate_type),
            )
            status = "merged" if action == "merge_into" else "alias"
            self._mark_candidate(
                candidate,
                status=status,
                approval_source="model",
                settled_at=settled_at,
                promoted_memory_id=int(target.id),
            )
            return status

        rendered_content = _memory_content(content, meaning)
        exact = self._exact_memory(
            chat_stream_id=str(candidate.chat_stream_id),
            memory_type=str(candidate.candidate_type),
            content_hash=_memory_content_hash(rendered_content),
            alternate_hashes=(_admin_content_hash(rendered_content),),
        )
        if exact is not None:
            self._mark_candidate(
                candidate,
                status="merged",
                approval_source="model",
                settled_at=settled_at,
                promoted_memory_id=int(exact.id),
            )
            return "merged"

        same_key = self._same_key_memory(
            chat_stream_id=str(candidate.chat_stream_id),
            memory_type=str(candidate.candidate_type),
            normalized_key=str(candidate.normalized_key or ""),
            approved_content_hash=approved_hash,
        )
        if same_key is not None:
            conflict_group_id = _conflict_group_id(
                chat_stream_id=str(candidate.chat_stream_id),
                candidate_id=str(candidate.candidate_id),
                target_memory_id=int(same_key.id),
            )
            self._mark_candidate(
                candidate,
                status="conflict",
                approval_source="model",
                settled_at=settled_at,
                conflict_group_id=conflict_group_id,
            )
            return "conflict"

        memory, created = self._insert_memory(
            candidate=candidate,
            content=content,
            meaning=meaning,
            approved_content_hash=approved_hash,
            evidence=evidence,
            approval_source="model",
            governance_mode="automatic",
            approved_at=settled_at,
        )
        self._mark_candidate(
            candidate,
            status="accepted" if created else "merged",
            approval_source="model",
            settled_at=settled_at,
            promoted_memory_id=int(memory.id),
        )
        return "promoted" if created else "merged"

    @staticmethod
    def _result_for_run(
        run: GroupLearningRun,
        *,
        status: str,
        candidates: tuple[GroupLearningCandidate, ...],
    ) -> GroupLearningGovernanceResult:
        return GroupLearningGovernanceResult(
            run_id=str(run.run_id),
            status=status,
            promoted_count=sum(
                row.status == "accepted"
                and row.promoted_group_memory_id is not None
                for row in candidates
            ),
            merged_count=sum(
                row.status == "merged" for row in candidates
            ),
            alias_count=sum(row.status == "alias" for row in candidates),
            rejected_count=sum(
                row.status == "rejected" for row in candidates
            ),
            conflict_count=sum(
                row.status == "conflict" for row in candidates
            ),
            waiting_count=sum(
                row.status == "waiting_for_evidence"
                for row in candidates
            ),
        )

    def settle_model_run(
        self,
        *,
        run_id: str,
        chat_stream_id: str,
        settled_at: datetime,
    ) -> GroupLearningGovernanceResult:
        normalized_run_id = str(run_id or "").strip()
        identity = parse_canonical_chat_stream_id(chat_stream_id)
        if identity.chat_type != "group":
            raise ValueError("群学习治理只接受 canonical group session")
        run = self._session.get(GroupLearningRun, normalized_run_id)
        if run is None:
            raise LookupError("group learning run not found")
        if str(run.chat_stream_id) != identity.chat_stream_id:
            raise ValueError("群学习治理 run scope 不一致")
        candidates = tuple(
            self._session.query(GroupLearningCandidate)
            .filter(
                GroupLearningCandidate.source_run_id
                == normalized_run_id
            )
            .order_by(GroupLearningCandidate.id.asc())
            .all()
        )
        if run.mode == "active" and run.status == "succeeded":
            return self._result_for_run(
                run,
                status="replayed",
                candidates=candidates,
            )
        if run.mode != "candidate_only" or run.status != "succeeded":
            raise ValueError("群学习 run 尚未完成模型审核")

        for candidate in candidates:
            if candidate.approval_source == "human":
                continue
            action = str(candidate.model_decision or "").strip()
            if action not in _MODEL_ACTIONS:
                raise ValueError("群学习候选尚无合法模型审核决定")
            if not candidate.model_review_run_id:
                raise ValueError("群学习候选缺少模型审核 run")
            if not candidate.model_contract_version:
                raise ValueError("群学习候选缺少模型合同版本")
            if action == "reject":
                self._mark_candidate(
                    candidate,
                    status="rejected",
                    approval_source="model",
                    settled_at=settled_at,
                    rejection_reason=(
                        candidate.rejection_reason_code
                        or "model_rejected"
                    ),
                )
            elif action == "conflict_with":
                self._mark_candidate(
                    candidate,
                    status="conflict",
                    approval_source="model",
                    settled_at=settled_at,
                    conflict_group_id=(
                        candidate.conflict_group_id
                        or _conflict_group_id(
                            chat_stream_id=identity.chat_stream_id,
                            candidate_id=str(candidate.candidate_id),
                        )
                    ),
                )
            else:
                self._settle_positive_candidate(
                    candidate=candidate,
                    action=action,
                    settled_at=settled_at,
                )

        result = self._result_for_run(
            run,
            status="succeeded",
            candidates=candidates,
        )
        run.mode = "active"
        run.status = "succeeded"
        run.accepted_count = (
            result.promoted_count
            + result.merged_count
            + result.alias_count
        )
        run.rejected_count = result.rejected_count
        run.conflict_count = result.conflict_count
        run.waiting_count = result.waiting_count
        run.error_code = ""
        run.completed_at = settled_at
        run.updated_at = settled_at

        state = self._session.get(
            GroupLearningStreamState,
            identity.chat_stream_id,
        )
        if state is None:
            state = GroupLearningStreamState(
                chat_stream_id=identity.chat_stream_id,
            )
            self._session.add(state)
        state.last_scanned_chat_log_id = max(
            int(state.last_scanned_chat_log_id or 0),
            int(run.cursor_end_chat_log_id or 0),
        )
        if str(run.trigger or "") == "schedule":
            state.last_success_chat_log_id = max(
                int(state.last_success_chat_log_id or 0),
                int(run.cursor_end_chat_log_id or 0),
            )
            state.last_success_run_id = str(run.run_id)
            state.last_success_at = settled_at
        state.last_error_code = ""
        state.version = int(state.version or 1) + 1
        state.updated_at = settled_at
        self._session.flush()
        return result

    def apply_human_governance(
        self,
        write: GroupLearningHumanGovernanceWrite,
    ) -> GroupLearningGovernanceResult:
        candidate = self._candidate(write.candidate_id)
        reviewer_id = str(write.reviewer_id or "").strip()
        action = str(write.action or "").strip()
        content = str(write.reviewed_content or "").strip()
        meaning = str(write.reviewed_meaning or "").strip()
        if not reviewer_id:
            raise ValueError("reviewer_id 不能为空")
        if action not in _HUMAN_ACTIONS:
            raise ValueError("human governance action 无效")
        if not content:
            raise ValueError("reviewed_content 不能为空")
        approved_hash = _sha256(content, meaning)
        if str(write.reviewed_content_hash) != approved_hash:
            raise ValueError("reviewed_content_hash 不匹配")

        candidate.reviewed_content = content
        candidate.reviewed_meaning = meaning
        candidate.reviewed_content_hash = approved_hash
        candidate.approval_source = "human"
        candidate.human_reviewer_id = reviewer_id
        candidate.human_reviewed_at = write.reviewed_at
        candidate.human_action = action
        candidate.waiting_reason_code = ""
        candidate.version = int(candidate.version or 1) + 1
        candidate.updated_at = write.reviewed_at

        if action == "reject":
            candidate.status = "rejected"
            candidate.rejection_reason_code = "human_rejected"
            self._session.flush()
            return GroupLearningGovernanceResult(
                run_id=str(candidate.source_run_id or ""),
                status="succeeded",
                rejected_count=1,
            )

        evidence = self._evidence(str(candidate.candidate_id))
        if action in {"merge", "resolve_conflict"}:
            target = self._target_memory(
                memory_id=write.target_memory_id,
                chat_stream_id=str(candidate.chat_stream_id),
                memory_type=str(candidate.candidate_type),
            )
            resolution = str(write.conflict_resolution or "").strip()
            if action == "merge":
                if resolution:
                    raise ValueError("merge 不接受 conflict_resolution")
                candidate.status = "merged"
                candidate.promoted_group_memory_id = int(target.id)
                candidate.conflict_group_id = None
                candidate.rejection_reason_code = ""
                self._session.flush()
                return GroupLearningGovernanceResult(
                    run_id=str(candidate.source_run_id or ""),
                    status="succeeded",
                    merged_count=1,
                )
            if resolution not in _HUMAN_CONFLICT_RESOLUTIONS:
                raise ValueError("resolve_conflict 缺少合法 resolution")
            if resolution == "keep_target":
                candidate.status = "merged"
                candidate.promoted_group_memory_id = int(target.id)
                candidate.conflict_group_id = None
                candidate.rejection_reason_code = ""
                self._mark_human_target(
                    target,
                    reviewer_id=reviewer_id,
                    action="resolve_conflict_keep_target",
                    reviewed_at=write.reviewed_at,
                )
                self._session.flush()
                return GroupLearningGovernanceResult(
                    run_id=str(candidate.source_run_id or ""),
                    status="succeeded",
                    merged_count=1,
                )

            rendered_content = _memory_content(content, meaning)
            content_hash = _memory_content_hash(rendered_content)
            duplicate = self._exact_memory(
                chat_stream_id=str(candidate.chat_stream_id),
                memory_type=str(candidate.candidate_type),
                content_hash=content_hash,
                alternate_hashes=(_admin_content_hash(rendered_content),),
            )
            if duplicate is not None and int(duplicate.id) != int(target.id):
                raise ValueError("冲突替换内容已存在于另一条正式记忆")
            target.content = rendered_content
            target.content_hash = content_hash
            target.cluster_key = str(candidate.normalized_key or "")
            meta = _parse_memory_meta(target)
            meta.update({
                "candidate_id": str(candidate.candidate_id),
                "meaning": meaning,
                "normalized_key": str(candidate.normalized_key or ""),
                "conflict_resolution": "replace_target",
            })
            target.meta_json = json.dumps(
                meta,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            target.conflict_group_id = None
            target.status = "active"
            target.inject_policy = "auto"
            self._mark_human_target(
                target,
                reviewer_id=reviewer_id,
                action="resolve_conflict_replace_target",
                reviewed_at=write.reviewed_at,
                approved_content_hash=approved_hash,
            )
            candidate.status = "accepted"
            candidate.promoted_group_memory_id = int(target.id)
            candidate.conflict_group_id = None
            candidate.rejection_reason_code = ""
            self._session.flush()
            return GroupLearningGovernanceResult(
                run_id=str(candidate.source_run_id or ""),
                status="succeeded",
                promoted_count=1,
            )

        rendered_content = _memory_content(content, meaning)
        existing = self._exact_memory(
            chat_stream_id=str(candidate.chat_stream_id),
            memory_type=str(candidate.candidate_type),
            content_hash=_memory_content_hash(rendered_content),
        )
        if existing is None:
            memory, created = self._insert_memory(
                candidate=candidate,
                content=content,
                meaning=meaning,
                approved_content_hash=approved_hash,
                evidence=evidence,
                approval_source="human",
                governance_mode="human_managed",
                approved_at=write.reviewed_at,
                human_reviewer_id=reviewer_id,
                human_action=action,
            )
        else:
            memory = existing
            created = False
            if str(memory.governance_mode or "") != "human_managed":
                self._mark_human_target(
                    memory,
                    reviewer_id=reviewer_id,
                    action=action,
                    reviewed_at=write.reviewed_at,
                    approved_content_hash=approved_hash,
                )
        candidate.status = "accepted"
        candidate.rejection_reason_code = ""
        candidate.promoted_group_memory_id = int(memory.id)
        self._session.flush()
        return GroupLearningGovernanceResult(
            run_id=str(candidate.source_run_id or ""),
            status="succeeded",
            promoted_count=1 if created else 0,
            merged_count=0 if created else 1,
        )

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()


def group_learning_governance_repository(
    value: Session | GroupLearningGovernanceRepositoryPort,
) -> GroupLearningGovernanceRepositoryPort:
    if isinstance(value, GroupLearningGovernanceRepositoryPort):
        return value
    return SqlAlchemyGroupLearningGovernanceRepository(value)


__all__ = [
    "SqlAlchemyGroupLearningGovernanceRepository",
    "group_learning_governance_repository",
]

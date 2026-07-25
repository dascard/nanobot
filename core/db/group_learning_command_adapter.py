"""阶段 7B 群学习 candidate-only 写面的 SQLAlchemy Adapter。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
import hashlib
import json
import re

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.db.group_learning_adapter import (
    group_learning_candidate_record,
)
from core.db.group_learning_contracts import (
    GroupLearningBatchPersistResult,
    GroupLearningBatchWrite,
    GroupLearningCandidateRecord,
    GroupLearningCandidateWrite,
    GroupLearningCommandRepositoryPort,
    GroupLearningEvidenceWrite,
    GroupLearningHumanReviewWrite,
    GroupLearningObservationMetrics,
    GroupLearningObservationWrite,
)
from core.db.models import (
    GroupLearningCandidate,
    GroupLearningEvidence,
    GroupLearningRun,
    GroupLearningStreamState,
)
from core.time_utils import db_now_naive


_MODEL_ACTIONS = frozenset({
    "new",
    "merge_into",
    "add_alias",
    "conflict_with",
    "reject",
})
_HUMAN_ACTIONS = frozenset({
    "accept",
    "edit_accept",
    "reject",
})
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_REPORT_ONLY_ASPECTS = frozenset({"titles", "quotes", "quality"})


def _require_sha256(value: object, *, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _HASH_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} 必须是 SHA-256")
    return normalized


def _reviewed_hash(content: str, meaning: str) -> str:
    return hashlib.sha256(
        f"{content}\0{meaning}".encode("utf-8")
    ).hexdigest()


def _conflict_group_id(
    *,
    candidate_id: str,
    target_memory_id: int,
) -> str:
    digest = hashlib.sha256(
        f"{candidate_id}\0{target_memory_id}".encode("utf-8")
    ).hexdigest()
    return f"glconf_{digest[:48]}"


class SqlAlchemyGroupLearningCommandRepository:
    """候选、证据和观察账本写面；不含正式记忆晋级能力。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _candidate_for_write(
        self,
        write: GroupLearningCandidateWrite,
    ) -> GroupLearningCandidate | None:
        return (
            self._session.query(GroupLearningCandidate)
            .filter(
                GroupLearningCandidate.chat_stream_id
                == write.chat_stream_id,
                GroupLearningCandidate.candidate_type
                == write.candidate_type,
                GroupLearningCandidate.fingerprint
                == write.fingerprint,
            )
            .first()
        )

    def _candidate_by_public_id(
        self,
        candidate_id: str,
    ) -> GroupLearningCandidate | None:
        return (
            self._session.query(GroupLearningCandidate)
            .filter(
                GroupLearningCandidate.candidate_id
                == str(candidate_id or "").strip()
            )
            .first()
        )

    def _insert_candidate(
        self,
        write: GroupLearningCandidateWrite,
        *,
        now: datetime,
    ) -> tuple[GroupLearningCandidate, bool]:
        existing_id = self._candidate_by_public_id(write.candidate_id)
        if existing_id is not None:
            if (
                existing_id.chat_stream_id != write.chat_stream_id
                or existing_id.candidate_type != write.candidate_type
                or existing_id.fingerprint != write.fingerprint
            ):
                raise ValueError("candidate_id 已绑定其他候选")
            if existing_id.approval_source != "human":
                existing_id.source_run_id = write.source_run_id
            existing_id.last_seen_at = now
            return existing_id, False

        row = self._candidate_for_write(write)
        if row is not None:
            if row.approval_source != "human":
                row.source_run_id = write.source_run_id
            row.last_seen_at = now
            return row, False

        row = GroupLearningCandidate(
            candidate_id=write.candidate_id,
            chat_stream_id=write.chat_stream_id,
            candidate_type=write.candidate_type,
            content=write.content,
            meaning=write.meaning,
            normalized_key=write.normalized_key,
            fingerprint=_require_sha256(
                write.fingerprint,
                field_name="candidate.fingerprint",
            ),
            content_hash=_require_sha256(
                write.content_hash,
                field_name="candidate.content_hash",
            ),
            source=write.source,
            status="pending_model_review",
            rule_id=write.rule_id,
            rule_version=write.rule_version,
            first_seen_at=now,
            last_seen_at=now,
            hit_count=1,
            source_run_id=write.source_run_id,
        )
        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
        except IntegrityError:
            row = self._candidate_for_write(write)
            if row is None:
                collision = self._candidate_by_public_id(
                    write.candidate_id
                )
                if collision is None:
                    raise
                if (
                    collision.chat_stream_id != write.chat_stream_id
                    or collision.candidate_type != write.candidate_type
                    or collision.fingerprint != write.fingerprint
                ):
                    raise ValueError(
                        "candidate_id 已绑定其他候选"
                    )
                row = collision
            if row.approval_source != "human":
                row.source_run_id = write.source_run_id
            row.last_seen_at = now
            return row, False
        return row, True

    def _insert_evidence(
        self,
        write: GroupLearningEvidenceWrite,
        *,
        actual_candidate_id: str,
    ) -> bool:
        duplicate = (
            self._session.query(GroupLearningEvidence)
            .filter(
                GroupLearningEvidence.candidate_id
                == actual_candidate_id,
                GroupLearningEvidence.chat_log_id
                == write.chat_log_id,
            )
            .first()
        )
        if duplicate is not None:
            return False
        public_id_collision = (
            self._session.query(GroupLearningEvidence)
            .filter(
                GroupLearningEvidence.evidence_id
                == write.evidence_id
            )
            .first()
        )
        if public_id_collision is not None:
            raise ValueError("evidence_id 已绑定其他证据")

        row = GroupLearningEvidence(
            evidence_id=write.evidence_id,
            candidate_id=actual_candidate_id,
            chat_log_id=write.chat_log_id,
            sender_id=write.sender_id,
            source_run_id=write.source_run_id,
            batch_id=write.batch_id,
            evidence_hash=_require_sha256(
                write.evidence_hash,
                field_name="evidence.evidence_hash",
            ),
            evidence_kind=write.evidence_kind,
        )
        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
        except IntegrityError:
            duplicate = (
                self._session.query(GroupLearningEvidence)
                .filter(
                    GroupLearningEvidence.candidate_id
                    == actual_candidate_id,
                    GroupLearningEvidence.chat_log_id
                    == write.chat_log_id,
                )
                .first()
            )
            if duplicate is None:
                raise
            return False
        return True

    @staticmethod
    def _assert_replay_matches(
        row: GroupLearningRun,
        write: GroupLearningBatchWrite,
    ) -> None:
        expected = (
            write.run.run_id,
            write.run.chat_stream_id,
            write.run.trigger,
            write.run.selected_aspects_json,
            write.run.cursor_start_chat_log_id,
            write.run.cursor_end_chat_log_id,
            write.run.context_start_chat_log_id,
            write.run.context_end_chat_log_id,
            write.run.rules_generation,
        )
        actual = (
            row.run_id,
            row.chat_stream_id,
            row.trigger,
            row.selected_aspects_json,
            row.cursor_start_chat_log_id,
            row.cursor_end_chat_log_id,
            row.context_start_chat_log_id,
            row.context_end_chat_log_id,
            row.rules_generation,
        )
        if actual != expected:
            raise ValueError("idempotency_key 对应的批次载荷不一致")

    def _replay_result(
        self,
        row: GroupLearningRun,
        write: GroupLearningBatchWrite,
    ) -> GroupLearningBatchPersistResult:
        self._assert_replay_matches(row, write)
        candidate_ids: list[str] = []
        for candidate_write in write.candidates:
            candidate = self._candidate_for_write(candidate_write)
            if candidate is not None:
                candidate_ids.append(str(candidate.candidate_id))
        return GroupLearningBatchPersistResult(
            run_id=str(row.run_id),
            replayed=True,
            candidate_ids=tuple(dict.fromkeys(candidate_ids)),
            candidate_count=int(row.candidate_count or 0),
            evidence_added_count=0,
            candidate_watermark=int(row.candidate_watermark or 0),
            candidate_created_count=0,
            candidate_updated_count=0,
        )

    def persist_candidate_batch(
        self,
        write: GroupLearningBatchWrite,
    ) -> GroupLearningBatchPersistResult:
        run_write = write.run
        existing = (
            self._session.query(GroupLearningRun)
            .filter(
                GroupLearningRun.idempotency_key
                == run_write.idempotency_key
            )
            .first()
        )
        if existing is not None:
            return self._replay_result(existing, write)
        run_id_collision = self._session.get(
            GroupLearningRun,
            run_write.run_id,
        )
        if run_id_collision is not None:
            raise ValueError("run_id 已绑定其他幂等批次")

        now = db_now_naive()
        run = GroupLearningRun(
            run_id=run_write.run_id,
            idempotency_key=run_write.idempotency_key,
            chat_stream_id=run_write.chat_stream_id,
            trigger=run_write.trigger,
            mode="candidate_only",
            selected_aspects_json=run_write.selected_aspects_json,
            cursor_start_chat_log_id=(
                run_write.cursor_start_chat_log_id
            ),
            cursor_end_chat_log_id=run_write.cursor_end_chat_log_id,
            context_start_chat_log_id=(
                run_write.context_start_chat_log_id
            ),
            context_end_chat_log_id=(
                run_write.context_end_chat_log_id
            ),
            rules_generation=run_write.rules_generation,
            status="running",
            raw_message_count=run_write.raw_message_count,
            cleaned_message_count=run_write.cleaned_message_count,
            eligible_message_count=run_write.eligible_message_count,
            trace_id=run_write.trace_id,
            job_id=run_write.job_id,
            started_at=now,
        )
        try:
            with self._session.begin_nested():
                self._session.add(run)
                self._session.flush()
        except IntegrityError:
            existing = (
                self._session.query(GroupLearningRun)
                .filter(
                    GroupLearningRun.idempotency_key
                    == run_write.idempotency_key
                )
                .first()
            )
            if existing is not None:
                return self._replay_result(existing, write)
            run_id_collision = self._session.get(
                GroupLearningRun,
                run_write.run_id,
            )
            if run_id_collision is not None:
                raise ValueError(
                    "run_id 已绑定其他幂等批次"
                ) from None
            raise

        candidate_id_map: dict[str, str] = {}
        candidates: dict[str, GroupLearningCandidate] = {}
        created_candidate_ids: set[str] = set()
        for candidate_write in write.candidates:
            row, created = self._insert_candidate(
                candidate_write,
                now=now,
            )
            actual_id = str(row.candidate_id)
            candidate_id_map[candidate_write.candidate_id] = actual_id
            candidates[actual_id] = row
            if created:
                created_candidate_ids.add(actual_id)

        evidence_added_count = 0
        for evidence_write in write.evidence:
            actual_candidate_id = candidate_id_map.get(
                evidence_write.candidate_id
            )
            if actual_candidate_id is None:
                raise ValueError("evidence 引用了当前批次外的候选")
            evidence_added_count += int(self._insert_evidence(
                evidence_write,
                actual_candidate_id=actual_candidate_id,
            ))

        self._session.flush()
        for candidate_id, candidate in candidates.items():
            evidence_count = int(
                self._session.query(
                    func.count(GroupLearningEvidence.id)
                )
                .filter(
                    GroupLearningEvidence.candidate_id == candidate_id
                )
                .scalar()
                or 0
            )
            candidate.hit_count = max(1, evidence_count)
            if candidate.approval_source != "human":
                candidate.source_run_id = run_write.run_id
            candidate.last_seen_at = now

        self._session.flush()
        candidate_watermark = max(
            (int(row.id or 0) for row in candidates.values()),
            default=0,
        )
        run.candidate_count = len(candidates)
        run.candidate_watermark = candidate_watermark
        run.status = "candidate_persisted"

        state = self._session.get(
            GroupLearningStreamState,
            run_write.chat_stream_id,
        )
        if state is None:
            state = GroupLearningStreamState(
                chat_stream_id=run_write.chat_stream_id,
            )
            self._session.add(state)
        state.last_scanned_chat_log_id = max(
            int(state.last_scanned_chat_log_id or 0),
            run_write.cursor_end_chat_log_id,
        )
        state.last_candidate_watermark = max(
            int(state.last_candidate_watermark or 0),
            candidate_watermark,
        )
        state.rules_generation = run_write.rules_generation
        state.last_error_code = ""
        state.version = int(state.version or 1) + 1
        state.updated_at = now
        self._session.flush()
        return GroupLearningBatchPersistResult(
            run_id=run_write.run_id,
            replayed=False,
            candidate_ids=tuple(candidates),
            candidate_count=len(candidates),
            evidence_added_count=evidence_added_count,
            candidate_watermark=candidate_watermark,
            candidate_created_count=len(created_candidate_ids),
            candidate_updated_count=(
                len(candidates) - len(created_candidate_ids)
            ),
        )

    @staticmethod
    def _apply_metrics(
        run: GroupLearningRun,
        metrics: GroupLearningObservationMetrics,
    ) -> None:
        run.task_contract_version = metrics.contract_version
        run.model_route = str(metrics.route_key or "").strip()
        run.provider = metrics.provider
        run.model = metrics.model
        run.task_run_id = metrics.task_run_id
        run.input_chars = metrics.input_chars
        run.input_tokens = metrics.input_tokens
        run.output_tokens = metrics.output_tokens
        run.total_tokens = metrics.total_tokens
        run.cost_microusd = metrics.cost_microusd
        run.latency_ms = metrics.latency_ms
        run.attempt_count = metrics.attempt_count
        run.raw_output_bytes = metrics.raw_output_bytes
        run.raw_output_sha256 = metrics.raw_output_sha256

    def _require_candidate_only_run(
        self,
        run_id: str,
    ) -> GroupLearningRun:
        run = self._session.get(
            GroupLearningRun,
            str(run_id or "").strip(),
        )
        if run is None:
            raise LookupError("group learning run not found")
        if run.mode != "candidate_only":
            raise ValueError("阶段 7B 只能更新 candidate_only run")
        return run

    def record_model_observation(
        self,
        *,
        run_id: str,
        observations: Sequence[GroupLearningObservationWrite],
        discoveries: Sequence[GroupLearningCandidateWrite],
        discovery_evidence: Sequence[GroupLearningEvidenceWrite],
        metrics: GroupLearningObservationMetrics,
        observed_at: datetime,
    ) -> None:
        run = self._require_candidate_only_run(run_id)
        updated_actions: list[str] = []
        for write in observations:
            action = str(write.action or "").strip()
            if action not in _MODEL_ACTIONS:
                raise ValueError("model observation action 无效")
            row = self._candidate_by_public_id(write.candidate_id)
            if row is None:
                raise LookupError("group learning candidate not found")
            if row.chat_stream_id != run.chat_stream_id:
                raise ValueError("model observation candidate scope 不一致")
            if row.source_run_id != run.run_id:
                raise ValueError("model observation candidate 不属于当前 run")
            if row.approval_source == "human":
                continue
            reviewed_content = str(write.reviewed_content or "").strip()
            reviewed_meaning = str(write.reviewed_meaning or "").strip()
            if not reviewed_content:
                raise ValueError("reviewed_content 不能为空")
            expected_hash = _reviewed_hash(
                reviewed_content,
                reviewed_meaning,
            )
            if write.reviewed_content_hash != expected_hash:
                raise ValueError("reviewed_content_hash 不匹配")
            target_memory_id = write.target_memory_id
            requires_target = action in {
                "merge_into",
                "add_alias",
                "conflict_with",
            }
            if requires_target != (target_memory_id is not None):
                raise ValueError("model observation target 语义无效")
            row.model_decision = action
            row.model_contract_version = metrics.contract_version
            row.model_review_run_id = metrics.task_run_id
            row.model_observed_at = observed_at
            row.observation_reason_hash = _require_sha256(
                write.reason_hash,
                field_name="observation.reason_hash",
            )
            row.reviewed_content = reviewed_content
            row.reviewed_meaning = reviewed_meaning
            row.reviewed_content_hash = expected_hash
            row.merge_target_memory_id = (
                int(target_memory_id)
                if action == "merge_into"
                and target_memory_id is not None
                else None
            )
            row.alias_target_memory_id = (
                int(target_memory_id)
                if action == "add_alias"
                and target_memory_id is not None
                else None
            )
            row.conflict_group_id = (
                _conflict_group_id(
                    candidate_id=row.candidate_id,
                    target_memory_id=int(target_memory_id),
                )
                if action == "conflict_with"
                and target_memory_id is not None
                else None
            )
            row.rejection_reason_code = (
                "model_rejected_observation"
                if action == "reject"
                else ""
            )
            row.status = "pending_model_review"
            row.version = int(row.version or 1) + 1
            row.updated_at = observed_at
            updated_actions.append(action)

        discovery_id_map: dict[str, str] = {}
        discovery_rows: dict[str, GroupLearningCandidate] = {}
        created_discovery_ids: set[str] = set()
        observed_discovery_ids: set[str] = set()
        for write in discoveries:
            if write.source != "model":
                raise ValueError("model discovery source 必须为 model")
            if (
                write.chat_stream_id != run.chat_stream_id
                or write.source_run_id != run.run_id
            ):
                raise ValueError("model discovery scope 不一致")
            row, created = self._insert_candidate(
                write,
                now=observed_at,
            )
            if row.approval_source != "human":
                row.model_decision = "new"
                row.model_contract_version = metrics.contract_version
                row.model_review_run_id = metrics.task_run_id
                row.model_observed_at = observed_at
                row.status = "pending_model_review"
                observed_discovery_ids.add(str(row.candidate_id))
            if created:
                created_discovery_ids.add(str(row.candidate_id))
            discovery_id_map[write.candidate_id] = row.candidate_id
            discovery_rows[row.candidate_id] = row

        for write in discovery_evidence:
            actual_candidate_id = discovery_id_map.get(write.candidate_id)
            if actual_candidate_id is None:
                raise ValueError(
                    "model discovery evidence 引用了当前输出外候选"
                )
            self._insert_evidence(
                write,
                actual_candidate_id=actual_candidate_id,
            )

        self._session.flush()
        for candidate_id, row in discovery_rows.items():
            count = int(
                self._session.query(
                    func.count(GroupLearningEvidence.id)
                )
                .filter(
                    GroupLearningEvidence.candidate_id == candidate_id
                )
                .scalar()
                or 0
            )
            row.hit_count = max(1, count)

        all_rows = tuple(discovery_rows.values())
        discovery_watermark = max(
            (int(row.id or 0) for row in all_rows),
            default=0,
        )
        run.candidate_count = int(run.candidate_count or 0) + len(
            created_discovery_ids
        )
        run.accepted_count = sum(
            action in {"new", "merge_into", "add_alias"}
            for action in updated_actions
        ) + len(observed_discovery_ids)
        run.rejected_count = updated_actions.count("reject")
        run.conflict_count = updated_actions.count("conflict_with")
        run.waiting_count = 0
        run.candidate_watermark = max(
            int(run.candidate_watermark or 0),
            discovery_watermark,
        )
        run.status = "succeeded"
        run.error_code = ""
        run.completed_at = observed_at
        self._apply_metrics(run, metrics)

        state = self._session.get(
            GroupLearningStreamState,
            run.chat_stream_id,
        )
        if state is not None:
            state.last_candidate_watermark = max(
                int(state.last_candidate_watermark or 0),
                discovery_watermark,
            )
            state.last_error_code = ""
            state.updated_at = observed_at
        self._session.flush()

    def record_model_failure(
        self,
        *,
        run_id: str,
        error_code: str,
        metrics: GroupLearningObservationMetrics,
    ) -> None:
        run = self._require_candidate_only_run(run_id)
        normalized_error = str(error_code or "").strip()[:64]
        if not normalized_error:
            raise ValueError("error_code 不能为空")
        now = db_now_naive()
        run.status = "failed"
        run.error_code = normalized_error
        run.completed_at = now
        self._apply_metrics(run, metrics)
        state = self._session.get(
            GroupLearningStreamState,
            run.chat_stream_id,
        )
        if state is not None:
            state.last_error_code = normalized_error
            state.updated_at = now
        self._session.flush()

    def complete_report_only_run(
        self,
        *,
        run_id: str,
        completed_at: datetime,
    ) -> None:
        run = self._require_candidate_only_run(run_id)
        try:
            selected = json.loads(str(run.selected_aspects_json or "[]"))
        except json.JSONDecodeError as exc:
            raise ValueError("报告型 run aspects 无效") from exc
        if (
            not isinstance(selected, list)
            or not selected
            or any(not isinstance(item, str) for item in selected)
            or not set(selected) <= _REPORT_ONLY_ASPECTS
        ):
            raise ValueError("只有报告方面可以无模型治理结算")
        actual_count = int(
            self._session.query(func.count(GroupLearningCandidate.id))
            .filter(
                GroupLearningCandidate.source_run_id == run.run_id
            )
            .scalar()
            or 0
        )
        if actual_count or int(run.candidate_count or 0):
            raise ValueError("报告型 run 不能包含群学习候选")
        if run.status == "succeeded":
            return
        if run.status != "candidate_persisted":
            raise ValueError("报告型 run 尚未完成候选持久化")
        run.status = "succeeded"
        run.error_code = ""
        run.accepted_count = 0
        run.rejected_count = 0
        run.conflict_count = 0
        run.waiting_count = 0
        run.completed_at = completed_at
        self._session.flush()

    def apply_human_review(
        self,
        write: GroupLearningHumanReviewWrite,
    ) -> GroupLearningCandidateRecord:
        row = self._candidate_by_public_id(write.candidate_id)
        if row is None:
            raise LookupError("group learning candidate not found")
        reviewer_id = str(write.reviewer_id or "").strip()
        action = str(write.action or "").strip()
        reviewed_content = str(write.reviewed_content or "").strip()
        reviewed_meaning = str(write.reviewed_meaning or "").strip()
        if not reviewer_id:
            raise ValueError("reviewer_id 不能为空")
        if action not in _HUMAN_ACTIONS:
            raise ValueError("阶段 7B human action 无效")
        if not reviewed_content:
            raise ValueError("reviewed_content 不能为空")
        expected_hash = _reviewed_hash(
            reviewed_content,
            reviewed_meaning,
        )
        if write.reviewed_content_hash != expected_hash:
            raise ValueError("reviewed_content_hash 不匹配")
        row.status = "rejected" if action == "reject" else "accepted"
        row.approval_source = "human"
        row.reviewed_content = reviewed_content
        row.reviewed_meaning = reviewed_meaning
        row.reviewed_content_hash = expected_hash
        row.human_reviewer_id = reviewer_id
        row.human_reviewed_at = write.reviewed_at
        row.human_action = action
        row.rejection_reason_code = (
            "human_rejected"
            if action == "reject"
            else ""
        )
        row.version = int(row.version or 1) + 1
        row.updated_at = write.reviewed_at
        self._session.flush()
        return group_learning_candidate_record(row)

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()


def group_learning_command_repository(
    value: Session | GroupLearningCommandRepositoryPort,
) -> GroupLearningCommandRepositoryPort:
    if isinstance(value, GroupLearningCommandRepositoryPort):
        return value
    return SqlAlchemyGroupLearningCommandRepository(value)


__all__ = [
    "SqlAlchemyGroupLearningCommandRepository",
    "group_learning_command_repository",
]

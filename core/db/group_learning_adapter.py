"""群学习只读 Repository Port 的 SQLAlchemy Adapter。"""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session

from foundation.identity import (
    ChatStreamIdentityError,
    parse_compatibility_chat_stream_identity,
    parse_canonical_chat_stream_id,
)

from core.db.group_learning_contracts import (
    GroupLearningCandidateRecord,
    GroupLearningEvidenceRecord,
    GroupLearningQueryRepositoryPort,
    GroupLearningRunRecord,
    GroupLearningScheduleRecord,
    GroupLearningStreamStateRecord,
    LegacyGroupLearningRecord,
)
from core.db.models import (
    AdminAuditLog,
    ExpressionMemory,
    GroupLearningCandidate,
    GroupLearningEvidence,
    GroupLearningRun,
    GroupLearningSchedule,
    GroupLearningStreamState,
    GroupMemory,
    JargonMemory,
)
from core.group_learning.legacy_migration import (
    LegacyHumanReviewProof,
    validate_legacy_human_review_proof,
)


def _canonical_legacy_stream_id(value: object) -> str:
    raw = str(value or "").strip()
    try:
        identity = parse_canonical_chat_stream_id(raw)
    except ChatStreamIdentityError:
        identity = parse_compatibility_chat_stream_identity(raw)
    if identity is None or identity.chat_type != "group":
        return ""
    return identity.chat_stream_id


def _legacy_human_proof(
    *,
    audits: tuple[AdminAuditLog, ...],
    source: str,
    legacy_id: int,
    chat_stream_id: str,
    content: str,
    meaning: str,
) -> LegacyHumanReviewProof | None:
    target_type = (
        "expression_memory"
        if source == "legacy_expression"
        else "jargon_memory"
    )
    target_id = str(int(legacy_id))
    for audit in audits:
        if (
            str(audit.target_type or "") != target_type
            or str(audit.target_id or "") != target_id
        ):
            continue
        proof = validate_legacy_human_review_proof(
            source=source,
            legacy_id=legacy_id,
            chat_stream_id=chat_stream_id,
            content=content,
            meaning=meaning,
            audit_log_id=int(audit.id or 0),
            admin_user=str(audit.admin_user or ""),
            action=str(audit.action or ""),
            target_type=str(audit.target_type or ""),
            target_id=str(audit.target_id or ""),
            detail_json=str(audit.detail_json or "{}"),
            created_at=audit.created_at,
        )
        if proof is not None:
            return proof
    return None


def _schedule_record(
    row: GroupLearningSchedule,
) -> GroupLearningScheduleRecord:
    return GroupLearningScheduleRecord(
        chat_stream_id=str(row.chat_stream_id or ""),
        enabled=bool(row.enabled),
        aspects_json=str(row.aspects_json or "[]"),
        interval_minutes=int(row.interval_minutes or 0),
        window_hours=int(row.window_hours or 0),
        next_run_at=row.next_run_at,
        last_started_at=row.last_started_at,
        last_completed_at=row.last_completed_at,
        lease_owner=str(row.lease_owner or ""),
        lease_token=str(row.lease_token or ""),
        lease_expires_at=row.lease_expires_at,
        consecutive_failures=int(row.consecutive_failures or 0),
        last_error_code=str(row.last_error_code or ""),
        config_generation=int(row.config_generation or 1),
        lease_generation=int(row.lease_generation or 0),
        attempt_count=int(row.attempt_count or 0),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _state_record(
    row: GroupLearningStreamState,
) -> GroupLearningStreamStateRecord:
    return GroupLearningStreamStateRecord(
        chat_stream_id=str(row.chat_stream_id or ""),
        last_scanned_chat_log_id=int(
            row.last_scanned_chat_log_id or 0
        ),
        last_success_chat_log_id=int(
            row.last_success_chat_log_id or 0
        ),
        last_candidate_watermark=int(
            row.last_candidate_watermark or 0
        ),
        rules_generation=int(row.rules_generation or 0),
        last_success_run_id=str(row.last_success_run_id or ""),
        last_success_at=row.last_success_at,
        last_error_code=str(row.last_error_code or ""),
        version=int(row.version or 1),
        updated_at=row.updated_at,
    )


def group_learning_candidate_record(
    row: GroupLearningCandidate,
) -> GroupLearningCandidateRecord:
    return GroupLearningCandidateRecord(
        id=int(row.id or 0),
        candidate_id=str(row.candidate_id or ""),
        chat_stream_id=str(row.chat_stream_id or ""),
        candidate_type=str(row.candidate_type or ""),
        content=str(row.content or ""),
        meaning=str(row.meaning or ""),
        normalized_key=str(row.normalized_key or ""),
        fingerprint=str(row.fingerprint or ""),
        content_hash=str(row.content_hash or ""),
        source=str(row.source or ""),
        status=str(row.status or ""),
        rule_id=str(row.rule_id or ""),
        rule_version=int(row.rule_version or 0),
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        hit_count=int(row.hit_count or 0),
        source_run_id=str(row.source_run_id or ""),
        model_decision=str(row.model_decision or ""),
        model_contract_version=str(
            row.model_contract_version or ""
        ),
        model_review_run_id=str(row.model_review_run_id or ""),
        model_observed_at=row.model_observed_at,
        observation_reason_hash=str(
            row.observation_reason_hash or ""
        ),
        reviewed_content=str(row.reviewed_content or ""),
        reviewed_meaning=str(row.reviewed_meaning or ""),
        reviewed_content_hash=str(row.reviewed_content_hash or ""),
        merge_target_memory_id=(
            int(row.merge_target_memory_id)
            if row.merge_target_memory_id is not None
            else None
        ),
        alias_target_memory_id=(
            int(row.alias_target_memory_id)
            if row.alias_target_memory_id is not None
            else None
        ),
        promoted_group_memory_id=(
            int(row.promoted_group_memory_id)
            if row.promoted_group_memory_id is not None
            else None
        ),
        conflict_group_id=str(row.conflict_group_id or ""),
        approval_source=str(row.approval_source or ""),
        human_reviewer_id=str(row.human_reviewer_id or ""),
        human_reviewed_at=row.human_reviewed_at,
        human_action=str(row.human_action or ""),
        rejection_reason_code=str(
            row.rejection_reason_code or ""
        ),
        waiting_reason_code=str(row.waiting_reason_code or ""),
        version=int(row.version or 1),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _evidence_record(
    row: GroupLearningEvidence,
) -> GroupLearningEvidenceRecord:
    return GroupLearningEvidenceRecord(
        id=int(row.id or 0),
        evidence_id=str(row.evidence_id or ""),
        candidate_id=str(row.candidate_id or ""),
        chat_log_id=int(row.chat_log_id or 0),
        sender_id=str(row.sender_id or ""),
        source_run_id=str(row.source_run_id or ""),
        batch_id=str(row.batch_id or ""),
        evidence_hash=str(row.evidence_hash or ""),
        evidence_kind=str(row.evidence_kind or ""),
        created_at=row.created_at,
    )


def _run_record(row: GroupLearningRun) -> GroupLearningRunRecord:
    return GroupLearningRunRecord(
        run_id=str(row.run_id or ""),
        idempotency_key=str(row.idempotency_key or ""),
        chat_stream_id=str(row.chat_stream_id or ""),
        trigger=str(row.trigger or ""),
        mode=str(row.mode or "candidate_only"),
        selected_aspects_json=str(row.selected_aspects_json or "[]"),
        cursor_start_chat_log_id=int(
            row.cursor_start_chat_log_id or 0
        ),
        cursor_end_chat_log_id=int(row.cursor_end_chat_log_id or 0),
        context_start_chat_log_id=int(
            row.context_start_chat_log_id or 0
        ),
        context_end_chat_log_id=int(
            row.context_end_chat_log_id or 0
        ),
        candidate_watermark=int(row.candidate_watermark or 0),
        rules_generation=int(row.rules_generation or 0),
        task_contract_version=str(row.task_contract_version or ""),
        model_route=str(row.model_route or ""),
        provider=str(row.provider or ""),
        model=str(row.model or ""),
        task_run_id=str(row.task_run_id or ""),
        status=str(row.status or ""),
        raw_message_count=int(row.raw_message_count or 0),
        cleaned_message_count=int(row.cleaned_message_count or 0),
        eligible_message_count=int(
            row.eligible_message_count or 0
        ),
        candidate_count=int(row.candidate_count or 0),
        accepted_count=int(row.accepted_count or 0),
        rejected_count=int(row.rejected_count or 0),
        conflict_count=int(row.conflict_count or 0),
        waiting_count=int(row.waiting_count or 0),
        error_code=str(row.error_code or ""),
        input_chars=int(row.input_chars or 0),
        input_tokens=int(row.input_tokens or 0),
        output_tokens=int(row.output_tokens or 0),
        total_tokens=int(row.total_tokens or 0),
        cost_microusd=(
            int(row.cost_microusd)
            if row.cost_microusd is not None
            else None
        ),
        latency_ms=int(row.latency_ms or 0),
        attempt_count=int(row.attempt_count or 0),
        raw_output_bytes=int(row.raw_output_bytes or 0),
        raw_output_sha256=str(row.raw_output_sha256 or ""),
        trace_id=str(row.trace_id or ""),
        job_id=str(row.job_id or ""),
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyGroupLearningQueryRepository:
    """只实现查询 Port，避免阶段 7A 意外形成 Writer。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_schedules(
        self,
        *,
        limit: int = 100,
    ) -> tuple[GroupLearningScheduleRecord, ...]:
        rows = (
            self._session.query(GroupLearningSchedule)
            .order_by(GroupLearningSchedule.chat_stream_id.asc())
            .limit(max(1, min(int(limit), 2000)))
            .all()
        )
        return tuple(_schedule_record(row) for row in rows)

    def get_schedule(
        self,
        chat_stream_id: str,
    ) -> GroupLearningScheduleRecord | None:
        row = self._session.get(
            GroupLearningSchedule,
            str(chat_stream_id or "").strip(),
        )
        return _schedule_record(row) if row is not None else None

    def get_stream_state(
        self,
        chat_stream_id: str,
    ) -> GroupLearningStreamStateRecord | None:
        row = self._session.get(
            GroupLearningStreamState,
            str(chat_stream_id or "").strip(),
        )
        return _state_record(row) if row is not None else None

    def get_candidate(
        self,
        candidate_id: str,
    ) -> GroupLearningCandidateRecord | None:
        row = (
            self._session.query(GroupLearningCandidate)
            .filter(
                GroupLearningCandidate.candidate_id
                == str(candidate_id or "").strip()
            )
            .first()
        )
        return (
            group_learning_candidate_record(row)
            if row is not None
            else None
        )

    def list_candidates(
        self,
        *,
        chat_stream_id: str,
        candidate_type: str = "",
        status: str = "",
        after_id: int = 0,
        limit: int = 100,
    ) -> tuple[GroupLearningCandidateRecord, ...]:
        query = self._session.query(GroupLearningCandidate).filter(
            GroupLearningCandidate.chat_stream_id
            == str(chat_stream_id or "").strip(),
            GroupLearningCandidate.id > max(0, int(after_id)),
        )
        if candidate_type:
            query = query.filter(
                GroupLearningCandidate.candidate_type
                == str(candidate_type).strip()
            )
        if status:
            query = query.filter(
                GroupLearningCandidate.status == str(status).strip()
            )
        rows = (
            query.order_by(GroupLearningCandidate.id.asc())
            .limit(max(1, min(int(limit), 500)))
            .all()
        )
        return tuple(
            group_learning_candidate_record(row)
            for row in rows
        )

    def count_candidates(
        self,
        *,
        chat_stream_id: str,
    ) -> int:
        return int(
            self._session.query(GroupLearningCandidate)
            .filter(
                GroupLearningCandidate.chat_stream_id
                == str(chat_stream_id or "").strip()
            )
            .count()
        )

    def list_evidence(
        self,
        *,
        candidate_id: str,
        after_id: int = 0,
        limit: int = 100,
    ) -> tuple[GroupLearningEvidenceRecord, ...]:
        rows = (
            self._session.query(GroupLearningEvidence)
            .filter(
                GroupLearningEvidence.candidate_id
                == str(candidate_id or "").strip(),
                GroupLearningEvidence.id > max(0, int(after_id)),
            )
            .order_by(GroupLearningEvidence.id.asc())
            .limit(max(1, min(int(limit), 500)))
            .all()
        )
        return tuple(_evidence_record(row) for row in rows)

    def list_runs(
        self,
        *,
        chat_stream_id: str,
        limit: int = 100,
    ) -> tuple[GroupLearningRunRecord, ...]:
        rows = (
            self._session.query(GroupLearningRun)
            .filter(
                GroupLearningRun.chat_stream_id
                == str(chat_stream_id or "").strip()
            )
            .order_by(
                GroupLearningRun.started_at.desc(),
                GroupLearningRun.run_id.desc(),
            )
            .limit(max(1, min(int(limit), 500)))
            .all()
        )
        return tuple(_run_record(row) for row in rows)

    def get_run(
        self,
        run_id: str,
    ) -> GroupLearningRunRecord | None:
        row = self._session.get(
            GroupLearningRun,
            str(run_id or "").strip(),
        )
        return _run_record(row) if row is not None else None

    def list_legacy_records(
        self,
    ) -> tuple[LegacyGroupLearningRecord, ...]:
        records: list[LegacyGroupLearningRecord] = []
        audits = tuple(
            self._session.query(AdminAuditLog)
            .filter(AdminAuditLog.action.in_((
                "legacy_expression.accept",
                "legacy_expression.reject",
                "legacy_jargon.accept",
                "legacy_jargon.reject",
            )))
            .order_by(
                AdminAuditLog.created_at.desc(),
                AdminAuditLog.id.desc(),
            )
            .all()
        )
        for row in (
            self._session.query(ExpressionMemory)
            .order_by(ExpressionMemory.id.asc())
            .all()
        ):
            chat_stream_id = _canonical_legacy_stream_id(
                row.chat_stream_id
            )
            proof = _legacy_human_proof(
                audits=audits,
                source="legacy_expression",
                legacy_id=int(row.id or 0),
                chat_stream_id=chat_stream_id,
                content=str(row.expression or ""),
                meaning="",
            )
            records.append(LegacyGroupLearningRecord(
                source="legacy_expression",
                legacy_id=int(row.id or 0),
                chat_stream_id=str(row.chat_stream_id or ""),
                legacy_group_id="",
                candidate_type="expression",
                content=str(row.expression or ""),
                meaning="",
                checked=bool(row.checked),
                status=str(row.status or ""),
                approval_source="human" if proof is not None else "",
                governance_mode=(
                    "human_managed" if proof is not None else ""
                ),
                approved_content_hash=(
                    proof.approved_content_hash
                    if proof is not None
                    else ""
                ),
                human_reviewer_id=(
                    proof.reviewer_id if proof is not None else ""
                ),
                human_reviewed_at=(
                    proof.reviewed_at if proof is not None else None
                ),
                human_action=(
                    proof.human_action if proof is not None else ""
                ),
                human_proof_audit_log_id=(
                    proof.audit_log_id if proof is not None else 0
                ),
                model_review_run_id="",
                model_contract_version="",
                created_at=row.created_at,
                updated_at=row.last_seen,
            ))
        for row in (
            self._session.query(JargonMemory)
            .order_by(JargonMemory.id.asc())
            .all()
        ):
            chat_stream_id = _canonical_legacy_stream_id(
                row.chat_stream_id
            )
            proof = _legacy_human_proof(
                audits=audits,
                source="legacy_jargon",
                legacy_id=int(row.id or 0),
                chat_stream_id=chat_stream_id,
                content=str(row.term or ""),
                meaning=str(row.meaning or ""),
            )
            records.append(LegacyGroupLearningRecord(
                source="legacy_jargon",
                legacy_id=int(row.id or 0),
                chat_stream_id=str(row.chat_stream_id or ""),
                legacy_group_id="",
                candidate_type="slang",
                content=str(row.term or ""),
                meaning=str(row.meaning or ""),
                checked=bool(row.checked),
                status=str(row.status or ""),
                approval_source="human" if proof is not None else "",
                governance_mode=(
                    "human_managed" if proof is not None else ""
                ),
                approved_content_hash=(
                    proof.approved_content_hash
                    if proof is not None
                    else ""
                ),
                human_reviewer_id=(
                    proof.reviewer_id if proof is not None else ""
                ),
                human_reviewed_at=(
                    proof.reviewed_at if proof is not None else None
                ),
                human_action=(
                    proof.human_action if proof is not None else ""
                ),
                human_proof_audit_log_id=(
                    proof.audit_log_id if proof is not None else 0
                ),
                model_review_run_id="",
                model_contract_version="",
                created_at=row.created_at,
                updated_at=row.last_seen,
            ))
        for row in (
            self._session.query(GroupMemory)
            .filter(
                or_(
                    GroupMemory.source.is_(None),
                    GroupMemory.source
                    != "legacy_group_learning_migration",
                )
            )
            .order_by(GroupMemory.id.asc())
            .all()
        ):
            records.append(LegacyGroupLearningRecord(
                source="legacy_group_memory",
                legacy_id=int(row.id or 0),
                chat_stream_id=str(row.chat_stream_id or ""),
                legacy_group_id=str(row.group_id or ""),
                candidate_type=str(row.memory_type or ""),
                content=str(row.content or ""),
                meaning="",
                checked=False,
                status=str(row.status or ""),
                approval_source=str(row.approval_source or ""),
                governance_mode=str(row.governance_mode or ""),
                approved_content_hash=str(
                    row.approved_content_hash or ""
                ),
                human_reviewer_id=str(row.human_reviewer_id or ""),
                human_reviewed_at=row.human_reviewed_at,
                human_action=str(row.human_action or ""),
                human_proof_audit_log_id=0,
                model_review_run_id=str(row.model_review_run_id or ""),
                model_contract_version=str(
                    row.model_contract_version or ""
                ),
                created_at=row.created_at,
                updated_at=row.updated_at,
            ))
        return tuple(records)


def group_learning_query_repository(
    value: Session | GroupLearningQueryRepositoryPort,
) -> GroupLearningQueryRepositoryPort:
    if isinstance(value, GroupLearningQueryRepositoryPort):
        return value
    return SqlAlchemyGroupLearningQueryRepository(value)


__all__ = [
    "SqlAlchemyGroupLearningQueryRepository",
    "group_learning_candidate_record",
    "group_learning_query_repository",
]

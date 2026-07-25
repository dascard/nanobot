"""旧表达、黑话和群体记忆迁移的确定性计划与只读审计。"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json

from foundation.identity import (
    ChatStreamIdentityError,
    parse_compatibility_chat_stream_identity,
    parse_canonical_chat_stream_id,
)

from app.group_learning.candidate_service import (
    group_learning_candidate_identity,
)
from app.group_learning.query_service import (
    require_canonical_group_stream_id,
)
from core.db.group_learning_contracts import (
    GroupLearningQueryRepositoryPort,
    LegacyGroupLearningRecord,
)
from core.group_learning import GROUP_LEARNING_MEMORY_TYPES
from core.group_learning.legacy_migration import legacy_content_hash
from core.group_learning.rules import canonicalize_learning_text


def _sha256_json(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _legacy_identity(
    row: LegacyGroupLearningRecord,
) -> str | None:
    if row.chat_stream_id:
        try:
            identity = parse_canonical_chat_stream_id(
                row.chat_stream_id
            )
        except ChatStreamIdentityError:
            identity = parse_compatibility_chat_stream_identity(
                row.chat_stream_id
            )
    else:
        identity = parse_compatibility_chat_stream_identity(
            row.legacy_group_id
        )
    if identity is None or identity.chat_type != "group":
        return None
    return identity.chat_stream_id


def _has_human_proof(row: LegacyGroupLearningRecord) -> bool:
    complete = (
        row.approval_source == "human"
        and row.governance_mode == "human_managed"
        and bool(row.approved_content_hash)
        and bool(row.human_reviewer_id)
        and row.human_reviewed_at is not None
        and row.human_action
        in {
            "accept",
            "edit_accept",
            "create",
            "legacy_accept",
            "legacy_reject",
        }
    )
    if not complete:
        return False
    if row.source in {"legacy_expression", "legacy_jargon"}:
        return row.human_proof_audit_log_id > 0
    return True


def _has_model_proof(row: LegacyGroupLearningRecord) -> bool:
    return (
        row.approval_source == "model"
        and row.governance_mode == "automatic"
        and bool(row.approved_content_hash)
        and bool(row.model_review_run_id)
        and bool(row.model_contract_version)
    )


def _authority_rank(plan: "LegacyCandidateMigrationPlan") -> int:
    if plan.planned_approval_source == "human":
        return 3
    if plan.planned_approval_source == "model":
        return 2
    return 0


def _conflict_group_id(
    chat_stream_id: str,
    candidate_type: str,
    normalized_key: str,
) -> str:
    payload = (
        f"{chat_stream_id}\0{candidate_type}\0"
        f"{normalized_key}"
    )
    digest = hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()
    return f"glconf_{digest[:48]}"


@dataclass(frozen=True, slots=True)
class LegacyCandidateMigrationPlan:
    """Application Service 内部使用的完整迁移计划。"""

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
    normalized_key_hash: str
    source_content_hash: str
    candidate_content_hash: str
    fingerprint: str
    planned_status: str
    planned_approval_source: str
    approved_content_hash: str
    human_reviewer_id: str
    human_reviewed_at: datetime | None
    human_action: str
    model_review_run_id: str
    model_contract_version: str
    checked_without_human_proof: bool
    conflict_group_id: str
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class PlannedLegacyCandidate:
    source_ref: str
    chat_stream_id: str
    candidate_type: str
    normalized_key_hash: str
    content_hash: str
    fingerprint: str
    planned_status: str
    planned_approval_source: str
    checked_without_human_proof: bool


@dataclass(frozen=True, slots=True)
class GroupLearningMigrationAudit:
    source_count: int
    valid_count: int
    planned_count: int
    duplicate_count: int
    conflict_count: int
    invalid_identity_count: int
    unsupported_type_count: int
    checked_without_human_proof_count: int
    source_counts: tuple[tuple[str, int], ...]
    source_sha256: str
    planned_sha256: str
    planned: tuple[PlannedLegacyCandidate, ...]


@dataclass(frozen=True, slots=True)
class _MigrationPlanningResult:
    source_count: int
    valid_count: int
    duplicate_count: int
    conflict_count: int
    invalid_identity_count: int
    unsupported_type_count: int
    checked_without_human_proof_count: int
    source_counts: tuple[tuple[str, int], ...]
    source_sha256: str
    plans: tuple[LegacyCandidateMigrationPlan, ...]


def _build_planning_result(
    source_rows: tuple[LegacyGroupLearningRecord, ...],
    *,
    selected_stream: str | None,
) -> _MigrationPlanningResult:
    source_payload: list[dict[str, object]] = []
    planned_by_fingerprint: dict[
        str,
        LegacyCandidateMigrationPlan,
    ] = {}
    conflict_fingerprints: dict[
        tuple[str, str, str],
        set[str],
    ] = defaultdict(set)
    source_counts: Counter[str] = Counter()
    invalid_identity_count = 0
    unsupported_type_count = 0
    duplicate_count = 0
    checked_without_human_proof_count = 0
    valid_count = 0

    for row in source_rows:
        canonical_id = _legacy_identity(row)
        if selected_stream is not None and canonical_id != selected_stream:
            continue
        source_counts[row.source] += 1
        row_content = canonicalize_learning_text(row.content)
        row_meaning = canonicalize_learning_text(row.meaning)
        source_hash = legacy_content_hash(row_content, row_meaning)
        human_proof = _has_human_proof(row)
        model_proof = _has_model_proof(row)
        source_payload.append({
            "source": row.source,
            "legacy_id": row.legacy_id,
            "chat_stream_id": canonical_id or "",
            "candidate_type": row.candidate_type,
            "content_hash": source_hash,
            "checked": row.checked,
            "approval_source": row.approval_source,
            "governance_mode": row.governance_mode,
            "human_proof": human_proof,
            "human_proof_audit_log_id": (
                row.human_proof_audit_log_id
            ),
            "model_proof": model_proof,
        })
        if canonical_id is None:
            invalid_identity_count += 1
            continue
        if (
            row.candidate_type not in GROUP_LEARNING_MEMORY_TYPES
            or not row_content
        ):
            unsupported_type_count += 1
            continue
        valid_count += 1
        (
            normalized_key,
            fingerprint,
            candidate_content_hash,
            candidate_id,
        ) = group_learning_candidate_identity(
            chat_stream_id=canonical_id,
            candidate_type=row.candidate_type,
            content=row_content,
            meaning=row_meaning,
        )
        checked_without_proof = bool(row.checked and not human_proof)
        checked_without_human_proof_count += int(
            checked_without_proof
        )
        if human_proof and row.human_action == "legacy_reject":
            planned_status = "rejected"
        elif human_proof or model_proof:
            planned_status = "accepted"
        else:
            planned_status = "pending_model_review"
        plan = LegacyCandidateMigrationPlan(
            source_ref=f"{row.source}:{row.legacy_id}",
            source=row.source,
            legacy_id=row.legacy_id,
            existing_group_memory_id=(
                row.legacy_id
                if row.source == "legacy_group_memory"
                else None
            ),
            chat_stream_id=canonical_id,
            candidate_id=candidate_id,
            candidate_type=row.candidate_type,
            content=row_content,
            meaning=row_meaning,
            normalized_key=normalized_key,
            normalized_key_hash=hashlib.sha256(
                normalized_key.encode("utf-8")
            ).hexdigest(),
            source_content_hash=source_hash,
            candidate_content_hash=candidate_content_hash,
            fingerprint=fingerprint,
            planned_status=planned_status,
            planned_approval_source=(
                "human"
                if human_proof
                else ("model" if model_proof else "")
            ),
            approved_content_hash=(
                row.approved_content_hash
                if human_proof or model_proof
                else ""
            ),
            human_reviewer_id=(
                row.human_reviewer_id if human_proof else ""
            ),
            human_reviewed_at=(
                row.human_reviewed_at if human_proof else None
            ),
            human_action=(
                row.human_action if human_proof else ""
            ),
            model_review_run_id=(
                row.model_review_run_id if model_proof else ""
            ),
            model_contract_version=(
                row.model_contract_version if model_proof else ""
            ),
            checked_without_human_proof=checked_without_proof,
            conflict_group_id="",
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        previous = planned_by_fingerprint.get(fingerprint)
        if previous is not None:
            duplicate_count += 1
            if _authority_rank(plan) > _authority_rank(previous):
                planned_by_fingerprint[fingerprint] = plan
        else:
            planned_by_fingerprint[fingerprint] = plan
        conflict_fingerprints[
            (
                canonical_id,
                row.candidate_type,
                normalized_key,
            )
        ].add(fingerprint)

    conflict_keys = {
        key
        for key, fingerprints in conflict_fingerprints.items()
        if len(fingerprints) > 1
    }
    plans = []
    for plan in planned_by_fingerprint.values():
        key = (
            plan.chat_stream_id,
            plan.candidate_type,
            plan.normalized_key,
        )
        plans.append(
            replace(
                plan,
                conflict_group_id=(
                    _conflict_group_id(*key)
                    if key in conflict_keys
                    else ""
                ),
            )
        )
    plans.sort(key=lambda item: (
        item.chat_stream_id,
        item.candidate_type,
        item.fingerprint,
        item.source_ref,
    ))
    return _MigrationPlanningResult(
        source_count=len(source_payload),
        valid_count=valid_count,
        duplicate_count=duplicate_count,
        conflict_count=len(conflict_keys),
        invalid_identity_count=invalid_identity_count,
        unsupported_type_count=unsupported_type_count,
        checked_without_human_proof_count=(
            checked_without_human_proof_count
        ),
        source_counts=tuple(sorted(source_counts.items())),
        source_sha256=_sha256_json(source_payload),
        plans=tuple(plans),
    )


class GroupLearningMigrationAuditService:
    """生成可重复对账报告；不执行 add、flush、commit 或迁移。"""

    def __init__(
        self,
        repository: GroupLearningQueryRepositoryPort,
    ) -> None:
        self.repository = repository

    def plans(
        self,
        *,
        chat_stream_id: str | None = None,
    ) -> tuple[LegacyCandidateMigrationPlan, ...]:
        selected_stream = (
            require_canonical_group_stream_id(chat_stream_id)
            if chat_stream_id is not None
            else None
        )
        source_rows = tuple(sorted(
            self.repository.list_legacy_records(),
            key=lambda row: (
                row.source,
                row.legacy_id,
                row.chat_stream_id,
                row.legacy_group_id,
            ),
        ))
        return _build_planning_result(
            source_rows,
            selected_stream=selected_stream,
        ).plans

    def audit(
        self,
        *,
        chat_stream_id: str | None = None,
    ) -> GroupLearningMigrationAudit:
        selected_stream = (
            require_canonical_group_stream_id(chat_stream_id)
            if chat_stream_id is not None
            else None
        )
        source_rows = tuple(sorted(
            self.repository.list_legacy_records(),
            key=lambda row: (
                row.source,
                row.legacy_id,
                row.chat_stream_id,
                row.legacy_group_id,
            ),
        ))
        result = _build_planning_result(
            source_rows,
            selected_stream=selected_stream,
        )
        planned = tuple(
            PlannedLegacyCandidate(
                source_ref=item.source_ref,
                chat_stream_id=item.chat_stream_id,
                candidate_type=item.candidate_type,
                normalized_key_hash=item.normalized_key_hash,
                content_hash=item.source_content_hash,
                fingerprint=item.fingerprint,
                planned_status=item.planned_status,
                planned_approval_source=(
                    item.planned_approval_source
                ),
                checked_without_human_proof=(
                    item.checked_without_human_proof
                ),
            )
            for item in result.plans
        )
        planned_sha256 = _sha256_json([
            {
                "source_ref": item.source_ref,
                "chat_stream_id": item.chat_stream_id,
                "candidate_type": item.candidate_type,
                "normalized_key_hash": item.normalized_key_hash,
                "content_hash": item.content_hash,
                "fingerprint": item.fingerprint,
                "planned_status": item.planned_status,
                "planned_approval_source": (
                    item.planned_approval_source
                ),
                "checked_without_human_proof": (
                    item.checked_without_human_proof
                ),
            }
            for item in planned
        ])
        return GroupLearningMigrationAudit(
            source_count=result.source_count,
            valid_count=result.valid_count,
            planned_count=len(planned),
            duplicate_count=result.duplicate_count,
            conflict_count=result.conflict_count,
            invalid_identity_count=result.invalid_identity_count,
            unsupported_type_count=result.unsupported_type_count,
            checked_without_human_proof_count=(
                result.checked_without_human_proof_count
            ),
            source_counts=result.source_counts,
            source_sha256=result.source_sha256,
            planned_sha256=planned_sha256,
            planned=planned,
        )


__all__ = [
    "GroupLearningMigrationAudit",
    "GroupLearningMigrationAuditService",
    "LegacyCandidateMigrationPlan",
    "PlannedLegacyCandidate",
]

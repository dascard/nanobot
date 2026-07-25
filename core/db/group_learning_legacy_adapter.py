"""阶段 7D 旧群学习记录到新治理模型的 SQLAlchemy Adapter。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
import hashlib
import json

from sqlalchemy.orm import Session

from foundation.identity import parse_canonical_chat_stream_id

from core.db.group_learning_legacy_contracts import (
    GroupLearningLegacyMigrationRepositoryPort,
    LegacyGroupLearningMigrationWrite,
    LegacyGroupLearningPersistResult,
)
from core.db.models import (
    GroupLearningCandidate,
    GroupLearningRun,
    GroupMemory,
)


def _sha256(*parts: object) -> str:
    payload = "\0".join(str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _render_memory_content(content: str, meaning: str) -> str:
    return f"{content}：{meaning}" if meaning else content


def _migration_run_id(
    chat_stream_id: str,
    planned_sha256: str,
) -> str:
    return f"glr_migration_{_sha256(chat_stream_id, planned_sha256)[:40]}"


class SqlAlchemyGroupLearningLegacyMigrationRepository:
    """迁移写面与在线候选 Writer 隔离，不接受消息 Evidence。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _existing_candidate(
        self,
        write: LegacyGroupLearningMigrationWrite,
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

    def _legacy_group_memory(
        self,
        write: LegacyGroupLearningMigrationWrite,
    ) -> GroupMemory:
        if write.existing_group_memory_id is None:
            raise ValueError("旧 GroupMemory 迁移缺少原记录 ID")
        row = self._session.get(
            GroupMemory,
            int(write.existing_group_memory_id),
        )
        if (
            row is None
            or str(row.chat_stream_id or "") != write.chat_stream_id
            or str(row.memory_type or "") != write.candidate_type
        ):
            raise ValueError("旧 GroupMemory 迁移范围不一致")
        return row

    def _promote_human_memory(
        self,
        write: LegacyGroupLearningMigrationWrite,
        *,
        migrated_at: datetime,
    ) -> tuple[GroupMemory, bool]:
        identity = parse_canonical_chat_stream_id(
            write.chat_stream_id
        )
        if identity.chat_type != "group":
            raise ValueError("旧群学习迁移只接受 canonical group session")
        content = _render_memory_content(
            write.content,
            write.meaning,
        )
        content_hash = hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest()
        row = (
            self._session.query(GroupMemory)
            .filter(
                GroupMemory.chat_stream_id == write.chat_stream_id,
                GroupMemory.memory_type == write.candidate_type,
                GroupMemory.content_hash == content_hash,
            )
            .first()
        )
        created = row is None
        if row is None:
            row = GroupMemory(
                chat_stream_id=write.chat_stream_id,
                group_id=identity.legacy_runtime_session_id,
                memory_type=write.candidate_type,
                content=content,
                content_hash=content_hash,
                cluster_key=write.normalized_key,
                evidence_log_ids_json="[]",
                confidence=1.0,
                evidence_count=0,
                first_seen=write.created_at or migrated_at,
                last_seen=write.updated_at or migrated_at,
                decay_score=1.0,
                source="legacy_group_learning_migration",
                meta_json=json.dumps(
                    {
                        "legacy_source_ref": write.source_ref,
                        "meaning": write.meaning,
                        "normalized_key": write.normalized_key,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                created_at=write.created_at or migrated_at,
            )
            self._session.add(row)
        row.status = "active"
        row.inject_policy = "auto"
        row.approval_source = "human"
        row.governance_mode = "human_managed"
        row.approved_content_hash = write.approved_content_hash
        row.human_reviewer_id = write.human_reviewer_id
        row.human_reviewed_at = write.human_reviewed_at
        row.human_action = write.human_action
        row.updated_at = migrated_at
        self._session.flush()
        return row, created

    @staticmethod
    def _downgrade_unproven_memory(row: GroupMemory) -> bool:
        changed = False
        if str(row.status or "") == "active":
            row.status = "review"
            changed = True
        if str(row.inject_policy or "") != "manual_only":
            row.inject_policy = "manual_only"
            changed = True
        return changed

    def apply_legacy_migration(
        self,
        writes: Sequence[LegacyGroupLearningMigrationWrite],
        *,
        planned_sha256: str,
        actor: str,
        migrated_at: datetime,
    ) -> LegacyGroupLearningPersistResult:
        normalized_actor = str(actor or "").strip()
        if not normalized_actor:
            raise ValueError("迁移 actor 不能为空")
        by_stream: dict[
            str,
            list[LegacyGroupLearningMigrationWrite],
        ] = defaultdict(list)
        for write in writes:
            identity = parse_canonical_chat_stream_id(
                write.chat_stream_id
            )
            if identity.chat_type != "group":
                raise ValueError(
                    "旧群学习迁移只接受 canonical group session"
                )
            by_stream[identity.chat_stream_id].append(write)

        migrated_count = 0
        replayed_count = 0
        human_promoted_count = 0
        downgraded_count = 0
        run_count = 0

        for chat_stream_id, stream_writes in sorted(
            by_stream.items()
        ):
            run_id = _migration_run_id(
                chat_stream_id,
                planned_sha256,
            )
            run = self._session.get(GroupLearningRun, run_id)
            if run is None:
                run = GroupLearningRun(
                    run_id=run_id,
                    idempotency_key=run_id,
                    chat_stream_id=chat_stream_id,
                    trigger="migration_review",
                    mode=(
                        "active"
                        if any(
                            item.planned_status == "accepted"
                            for item in stream_writes
                        )
                        else "candidate_only"
                    ),
                    selected_aspects_json=json.dumps(
                        sorted({
                            {
                                "topic": "topics",
                                "expression": "expressions",
                                "slang": "slang",
                                "style": "style",
                            }[item.candidate_type]
                            for item in stream_writes
                        }),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    cursor_start_chat_log_id=0,
                    cursor_end_chat_log_id=0,
                    context_start_chat_log_id=0,
                    context_end_chat_log_id=0,
                    candidate_watermark=0,
                    rules_generation=0,
                    task_contract_version=(
                        "legacy_group_learning_migration_v1"
                    ),
                    status="succeeded",
                    raw_message_count=0,
                    cleaned_message_count=0,
                    eligible_message_count=0,
                    candidate_count=len(stream_writes),
                    accepted_count=sum(
                        item.planned_status == "accepted"
                        for item in stream_writes
                    ),
                    rejected_count=sum(
                        item.planned_status == "rejected"
                        for item in stream_writes
                    ),
                    conflict_count=sum(
                        bool(item.conflict_group_id)
                        for item in stream_writes
                    ),
                    waiting_count=0,
                    error_code="",
                    raw_output_sha256=planned_sha256,
                    trace_id="",
                    job_id=f"legacy-migration:{normalized_actor[:32]}",
                    started_at=migrated_at,
                    completed_at=migrated_at,
                    created_at=migrated_at,
                    updated_at=migrated_at,
                )
                self._session.add(run)
                run_count += 1

            stream_candidates: list[GroupLearningCandidate] = []
            for write in stream_writes:
                existing = self._existing_candidate(write)
                if existing is not None:
                    replayed_count += 1
                    stream_candidates.append(existing)
                    continue

                promoted_memory_id: int | None = None
                if write.source == "legacy_group_memory":
                    memory = self._legacy_group_memory(write)
                    if write.approval_source:
                        promoted_memory_id = int(memory.id)
                    elif self._downgrade_unproven_memory(memory):
                        downgraded_count += 1
                elif (
                    write.approval_source == "human"
                    and write.planned_status == "accepted"
                ):
                    memory, created = self._promote_human_memory(
                        write,
                        migrated_at=migrated_at,
                    )
                    promoted_memory_id = int(memory.id)
                    human_promoted_count += int(created)

                candidate = GroupLearningCandidate(
                    candidate_id=write.candidate_id,
                    chat_stream_id=write.chat_stream_id,
                    candidate_type=write.candidate_type,
                    content=write.content,
                    meaning=write.meaning,
                    normalized_key=write.normalized_key,
                    fingerprint=write.fingerprint,
                    content_hash=write.content_hash,
                    source=write.source,
                    status=write.planned_status,
                    rule_id=f"migration.{write.source}.v1",
                    rule_version=1,
                    first_seen_at=write.created_at or migrated_at,
                    last_seen_at=write.updated_at or migrated_at,
                    hit_count=1,
                    source_run_id=run_id,
                    model_decision="",
                    model_contract_version=(
                        write.model_contract_version
                    ),
                    model_review_run_id=(
                        write.model_review_run_id
                    ),
                    reviewed_content=(
                        write.content
                        if write.approval_source == "human"
                        else None
                    ),
                    reviewed_meaning=(
                        write.meaning
                        if write.approval_source == "human"
                        else None
                    ),
                    reviewed_content_hash=(
                        write.approved_content_hash or None
                    ),
                    promoted_group_memory_id=promoted_memory_id,
                    conflict_group_id=(
                        write.conflict_group_id or None
                    ),
                    approval_source=(
                        write.approval_source or None
                    ),
                    human_reviewer_id=(
                        write.human_reviewer_id or None
                    ),
                    human_reviewed_at=write.human_reviewed_at,
                    human_action=write.human_action or None,
                    rejection_reason_code=(
                        "legacy_human_rejected"
                        if write.planned_status == "rejected"
                        else ""
                    ),
                    waiting_reason_code="",
                    version=1,
                    created_at=write.created_at or migrated_at,
                    updated_at=migrated_at,
                )
                self._session.add(candidate)
                self._session.flush()
                stream_candidates.append(candidate)
                migrated_count += 1

            if stream_candidates:
                run.candidate_watermark = max(
                    int(item.id or 0)
                    for item in stream_candidates
                )
                run.updated_at = migrated_at

        self._session.flush()
        return LegacyGroupLearningPersistResult(
            migrated_count=migrated_count,
            replayed_count=replayed_count,
            human_promoted_count=human_promoted_count,
            legacy_group_memory_downgraded_count=downgraded_count,
            run_count=run_count,
        )

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()


def group_learning_legacy_migration_repository(
    value: Session | GroupLearningLegacyMigrationRepositoryPort,
) -> GroupLearningLegacyMigrationRepositoryPort:
    if isinstance(value, GroupLearningLegacyMigrationRepositoryPort):
        return value
    return SqlAlchemyGroupLearningLegacyMigrationRepository(value)


__all__ = [
    "SqlAlchemyGroupLearningLegacyMigrationRepository",
    "group_learning_legacy_migration_repository",
]

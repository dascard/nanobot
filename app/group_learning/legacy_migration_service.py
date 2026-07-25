"""阶段 7D 旧群学习数据的审计绑定迁移 Application Service。"""

from __future__ import annotations

from hmac import compare_digest

from sqlalchemy.orm import Session

from app.group_learning.migration_audit import (
    GroupLearningMigrationAudit,
    GroupLearningMigrationAuditService,
)
from core.db.group_learning_adapter import (
    SqlAlchemyGroupLearningQueryRepository,
)
from core.db.group_learning_legacy_adapter import (
    SqlAlchemyGroupLearningLegacyMigrationRepository,
)
from core.db.group_learning_legacy_contracts import (
    GroupLearningLegacyMigrationRepositoryPort,
    LegacyGroupLearningMigrationResult,
    LegacyGroupLearningMigrationWrite,
)
from core.db.group_learning_contracts import (
    GroupLearningQueryRepositoryPort,
)
from core.time_utils import db_now_naive


class GroupLearningLegacyMigrationError(RuntimeError):
    """旧群学习数据迁移未完成。"""


class GroupLearningLegacyMigrationMismatch(
    GroupLearningLegacyMigrationError
):
    """apply 输入与最新 dry-run 审计摘要不一致。"""


class GroupLearningLegacyMigrationService:
    def __init__(
        self,
        query_repository: GroupLearningQueryRepositoryPort,
        command_repository: GroupLearningLegacyMigrationRepositoryPort,
    ) -> None:
        self.query_repository = query_repository
        self.command_repository = command_repository

    def audit(
        self,
        *,
        chat_stream_id: str | None = None,
    ) -> GroupLearningMigrationAudit:
        return GroupLearningMigrationAuditService(
            self.query_repository
        ).audit(chat_stream_id=chat_stream_id)

    def apply(
        self,
        *,
        expected_source_sha256: str,
        expected_planned_sha256: str,
        actor: str,
        chat_stream_id: str | None = None,
    ) -> LegacyGroupLearningMigrationResult:
        audit_service = GroupLearningMigrationAuditService(
            self.query_repository
        )
        audit = audit_service.audit(chat_stream_id=chat_stream_id)
        if (
            not compare_digest(
                str(expected_source_sha256 or ""),
                audit.source_sha256,
            )
            or not compare_digest(
                str(expected_planned_sha256 or ""),
                audit.planned_sha256,
            )
        ):
            raise GroupLearningLegacyMigrationMismatch(
                "旧群学习迁移审计摘要已变化，请重新 dry-run"
            )
        plans = audit_service.plans(
            chat_stream_id=chat_stream_id
        )
        writes = tuple(
            LegacyGroupLearningMigrationWrite(
                source_ref=plan.source_ref,
                source=plan.source,
                legacy_id=plan.legacy_id,
                existing_group_memory_id=(
                    plan.existing_group_memory_id
                ),
                chat_stream_id=plan.chat_stream_id,
                candidate_id=plan.candidate_id,
                candidate_type=plan.candidate_type,
                content=plan.content,
                meaning=plan.meaning,
                normalized_key=plan.normalized_key,
                fingerprint=plan.fingerprint,
                content_hash=plan.candidate_content_hash,
                planned_status=plan.planned_status,
                approval_source=plan.planned_approval_source,
                approved_content_hash=plan.approved_content_hash,
                human_reviewer_id=plan.human_reviewer_id,
                human_reviewed_at=plan.human_reviewed_at,
                human_action=plan.human_action,
                model_review_run_id=plan.model_review_run_id,
                model_contract_version=(
                    plan.model_contract_version
                ),
                conflict_group_id=plan.conflict_group_id,
                created_at=plan.created_at,
                updated_at=plan.updated_at,
            )
            for plan in plans
        )
        try:
            persisted = (
                self.command_repository.apply_legacy_migration(
                    writes,
                    planned_sha256=audit.planned_sha256,
                    actor=actor,
                    migrated_at=db_now_naive(),
                )
            )
            self.command_repository.commit()
        except BaseException:
            self.command_repository.rollback()
            raise
        return LegacyGroupLearningMigrationResult(
            source_count=audit.source_count,
            planned_count=audit.planned_count,
            migrated_count=persisted.migrated_count,
            replayed_count=persisted.replayed_count,
            human_promoted_count=persisted.human_promoted_count,
            legacy_group_memory_downgraded_count=(
                persisted.legacy_group_memory_downgraded_count
            ),
            run_count=persisted.run_count,
            source_sha256=audit.source_sha256,
            planned_sha256=audit.planned_sha256,
        )


def build_group_learning_legacy_migration_service(
    session: Session,
) -> GroupLearningLegacyMigrationService:
    return GroupLearningLegacyMigrationService(
        SqlAlchemyGroupLearningQueryRepository(session),
        SqlAlchemyGroupLearningLegacyMigrationRepository(session),
    )


__all__ = [
    "GroupLearningLegacyMigrationError",
    "GroupLearningLegacyMigrationMismatch",
    "GroupLearningLegacyMigrationService",
    "build_group_learning_legacy_migration_service",
]

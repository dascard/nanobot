"""Workspace 配额维护期间的 Lease 账本收敛与条件重建。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from core.database import (
    SandboxAccessGrant,
    SandboxLease,
    SandboxRun,
)
from core.sandbox.access_policy import SandboxAccessPolicy
from core.sandbox.backend import SandboxBackend
from core.sandbox.contracts import SandboxServiceError
from core.sandbox.lease_service import SandboxLeaseService
from core.sandbox.repositories import SandboxLeaseRepository
from core.time_utils import db_now_naive


logger = logging.getLogger("nanobot.sandbox.quota_service")


@dataclass(frozen=True, slots=True)
class LeaseRebuildCandidate:
    lease_id: str
    grant_id: str
    chat_stream_id: str
    workspace_id: str
    profile_id: str
    grant_version: int


class SandboxQuotaService:
    """只处理目标 Workspace 的 Lease；不承担宿主 quota 副作用。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = SandboxLeaseRepository(db)

    def snapshot_rebuild_candidates(
        self,
        workspace_id: str,
    ) -> tuple[LeaseRebuildCandidate, ...]:
        leases = (
            self.db.query(SandboxLease)
            .filter(
                SandboxLease.workspace_id == str(workspace_id or ""),
                SandboxLease.status.in_({"active", "idle"}),
            )
            .order_by(SandboxLease.created_at.asc())
            .all()
        )
        candidates: list[LeaseRebuildCandidate] = []
        for lease in leases:
            grant = self.db.get(SandboxAccessGrant, str(lease.grant_id))
            if grant is None:
                continue
            candidates.append(LeaseRebuildCandidate(
                lease_id=str(lease.lease_id),
                grant_id=str(lease.grant_id),
                chat_stream_id=str(lease.chat_stream_id),
                workspace_id=str(lease.workspace_id),
                profile_id=str(lease.profile_id),
                grant_version=int(grant.version),
            ))
        return tuple(candidates)

    def settle_stopped_leases(
        self,
        candidates: tuple[LeaseRebuildCandidate, ...],
        *,
        reason_code: str = "quota_reconfigured",
        reason_summary: str = "Workspace 配额维护已回收旧 Lease",
    ) -> None:
        """宿主确认回收后，将旧 Lease 与活动 Run 一次性终态化。"""

        now = db_now_naive()
        for candidate in candidates:
            self.repository.transition(
                candidate.lease_id,
                expected_statuses={
                    "provisioning",
                    "active",
                    "idle",
                    "stopping",
                },
                target_status="stopped",
                changes={
                    "stopped_at": now,
                    "reconciled_at": now,
                    "last_error_code": reason_code,
                    "last_error_summary": reason_summary,
                },
            )
            runs = (
                self.db.query(SandboxRun)
                .filter(
                    SandboxRun.lease_id == candidate.lease_id,
                    SandboxRun.status.in_({"pending", "running"}),
                )
                .all()
            )
            for run in runs:
                run.status = "failed"
                run.process_state = "lost"
                run.termination_reason = reason_code
                run.finished_at = now
                run.last_seen_at = now
                run.updated_at = now

    def rebuild_if_still_allowed(
        self,
        candidates: tuple[LeaseRebuildCandidate, ...],
        backend: SandboxBackend,
    ) -> tuple[str, ...]:
        """重读 Grant/Profile/catalog/硬开关，符合时才懒式重建。"""

        rebuilt: list[str] = []
        for candidate in candidates:
            self.db.expire_all()
            grant = self.db.get(
                SandboxAccessGrant,
                candidate.grant_id,
            )
            if (
                grant is None
                or int(grant.version) != candidate.grant_version
                or str(grant.chat_stream_id) != candidate.chat_stream_id
                or str(grant.workspace_id or "")
                != candidate.workspace_id
                or str(grant.execution_profile)
                != candidate.profile_id
            ):
                continue
            decision = SandboxAccessPolicy(self.db).evaluate(
                "sandbox_exec",
                platform=grant.platform,
                chat_type=grant.chat_type,
                session_id=grant.external_session_id,
            )
            if (
                not decision.allowed
                or decision.grant_id != candidate.grant_id
                or decision.workspace_id != candidate.workspace_id
                or decision.execution_profile != candidate.profile_id
                or decision.grant_version != candidate.grant_version
            ):
                continue
            try:
                lease = SandboxLeaseService(
                    self.db,
                    backend,
                ).ensure_for_access(decision)
            except SandboxServiceError:
                logger.warning(
                    "配额维护后 Lease 条件重建失败 workspace_id=%s",
                    candidate.workspace_id,
                    exc_info=True,
                )
                continue
            rebuilt.append(str(lease.lease_id))
        return tuple(rebuilt)


__all__ = [
    "LeaseRebuildCandidate",
    "SandboxQuotaService",
]

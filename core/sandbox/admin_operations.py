"""Sandbox 管理操作的租约、重试与跨边界结算状态机。"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from core.database import (
    SandboxAccessGrant,
    SandboxAdminOperation,
    Workspace,
    WorkspaceQuotaBinding,
)
from core.fencing import lease_deadline, new_fencing_token
from core.sandbox.access_contracts import SandboxCapability
from core.sandbox.backend import SandboxBackend
from core.sandbox.client import HttpSandboxdAdminBackend
from core.sandbox.contracts import SandboxServiceError
from core.settings_service import settings
from core.time_utils import db_now_naive


logger = logging.getLogger("nanobot.sandbox.admin_operations")
DEFAULT_LEASE_SECONDS = 180
DEFAULT_POLL_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class ClaimedSandboxOperation:
    operation_id: str
    operation_type: str
    chat_stream_id: str
    workspace_id: str
    desired_capability: str
    previous_capability: str
    desired_quota_bytes: int
    expected_grant_version: int | None
    expected_quota_generation: int | None
    request_id: str
    attempt_count: int
    max_attempts: int
    worker_id: str
    lease_token: str


class SandboxOperationLeaseLost(RuntimeError):
    """管理操作已被其他 runner 接管或被新期望状态取代。"""


def _claim_operation(
    db: Session,
    *,
    worker_id: str,
    lease_seconds: int,
) -> ClaimedSandboxOperation | None:
    now = db_now_naive()
    candidate = (
        db.query(SandboxAdminOperation)
        .filter(
            or_(
                (
                    (SandboxAdminOperation.status == "pending")
                    & or_(
                        SandboxAdminOperation.next_attempt_at.is_(None),
                        SandboxAdminOperation.next_attempt_at <= now,
                    )
                ),
                (
                    (SandboxAdminOperation.status == "running")
                    & or_(
                        SandboxAdminOperation.lease_expires_at.is_(None),
                        SandboxAdminOperation.lease_expires_at <= now,
                    )
                ),
            )
        )
        .order_by(SandboxAdminOperation.created_at.asc())
        .first()
    )
    if candidate is None:
        db.rollback()
        return None
    previous_status = str(candidate.status)
    token = new_fencing_token()
    conditions = [
        SandboxAdminOperation.operation_id == candidate.operation_id,
        SandboxAdminOperation.status == previous_status,
    ]
    if previous_status == "pending":
        conditions.append(or_(
            SandboxAdminOperation.next_attempt_at.is_(None),
            SandboxAdminOperation.next_attempt_at <= now,
        ))
    else:
        conditions.append(or_(
            SandboxAdminOperation.lease_expires_at.is_(None),
            SandboxAdminOperation.lease_expires_at <= now,
        ))
    changed = db.execute(
        update(SandboxAdminOperation)
        .where(*conditions)
        .values(
            status="running",
            step="claimed",
            attempt_count=SandboxAdminOperation.attempt_count + 1,
            locked_by=worker_id,
            lease_token=token,
            lease_expires_at=lease_deadline(now, lease_seconds),
            next_attempt_at=None,
            started_at=SandboxAdminOperation.started_at if candidate.started_at else now,
            updated_at=now,
        )
    )
    if int(changed.rowcount or 0) != 1:
        db.rollback()
        return None
    db.commit()
    row = db.get(SandboxAdminOperation, candidate.operation_id)
    if row is None:
        return None
    return ClaimedSandboxOperation(
        operation_id=str(row.operation_id),
        operation_type=str(row.operation_type),
        chat_stream_id=str(row.chat_stream_id or ""),
        workspace_id=str(row.workspace_id or ""),
        desired_capability=str(row.desired_capability or ""),
        previous_capability=str(row.previous_capability or ""),
        desired_quota_bytes=int(row.desired_quota_bytes or 0),
        expected_grant_version=(
            int(row.expected_grant_version)
            if row.expected_grant_version is not None
            else None
        ),
        expected_quota_generation=(
            int(row.expected_quota_generation)
            if row.expected_quota_generation is not None
            else None
        ),
        request_id=str(row.request_id),
        attempt_count=int(row.attempt_count or 0),
        max_attempts=int(row.max_attempts or 0),
        worker_id=worker_id,
        lease_token=token,
    )


def _lease_filter(claim: ClaimedSandboxOperation):
    return (
        SandboxAdminOperation.operation_id == claim.operation_id,
        SandboxAdminOperation.status == "running",
        SandboxAdminOperation.locked_by == claim.worker_id,
        SandboxAdminOperation.lease_token == claim.lease_token,
    )


def _mark_applying(db: Session, claim: ClaimedSandboxOperation) -> WorkspaceQuotaBinding:
    now = db_now_naive()
    if claim.operation_type == "set_access":
        grant = (
            db.query(SandboxAccessGrant)
            .filter(SandboxAccessGrant.chat_stream_id == claim.chat_stream_id)
            .one_or_none()
        )
        if (
            grant is None
            or claim.expected_grant_version is None
            or int(grant.version) != claim.expected_grant_version
        ):
            db.rollback()
            raise SandboxOperationLeaseLost
    operation_changed = db.execute(
        update(SandboxAdminOperation)
        .where(*_lease_filter(claim))
        .values(step="applying_quota", updated_at=now)
    )
    if int(operation_changed.rowcount or 0) != 1:
        db.rollback()
        raise SandboxOperationLeaseLost
    binding = db.get(WorkspaceQuotaBinding, claim.workspace_id)
    if (
        binding is None
        or claim.expected_quota_generation is None
        or int(binding.generation) != claim.expected_quota_generation
        or int(binding.desired_quota_bytes) != claim.desired_quota_bytes
    ):
        db.rollback()
        raise SandboxOperationLeaseLost
    binding.status = "applying"
    binding.last_error_code = ""
    binding.last_error_summary = ""
    binding.updated_at = now
    db.commit()
    return binding


def _response_data(response: dict) -> dict:
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError("invalid sandboxd quota response")
    return data


def _settle_success(db: Session, claim: ClaimedSandboxOperation) -> None:
    now = db_now_naive()
    changed = db.execute(
        update(SandboxAdminOperation)
        .where(*_lease_filter(claim))
        .values(
            status="succeeded",
            step="completed",
            locked_by="",
            lease_token="",
            lease_expires_at=None,
            next_attempt_at=None,
            error_code="",
            error_summary="",
            finished_at=now,
            updated_at=now,
        )
    )
    if int(changed.rowcount or 0) != 1:
        db.rollback()
        raise SandboxOperationLeaseLost

    if claim.workspace_id and claim.expected_quota_generation is not None:
        binding = db.get(WorkspaceQuotaBinding, claim.workspace_id)
        workspace = db.get(Workspace, claim.workspace_id)
        if (
            binding is None
            or workspace is None
            or int(binding.generation) != claim.expected_quota_generation
            or int(binding.desired_quota_bytes) != claim.desired_quota_bytes
        ):
            db.rollback()
            raise SandboxOperationLeaseLost
        binding.status = "applied"
        binding.applied_quota_bytes = claim.desired_quota_bytes
        binding.last_error_code = ""
        binding.last_error_summary = ""
        binding.last_applied_at = now
        binding.updated_at = now
        workspace.quota_bytes = claim.desired_quota_bytes
        workspace.updated_at = now

    if claim.operation_type == "set_access":
        grant = (
            db.query(SandboxAccessGrant)
            .filter(SandboxAccessGrant.chat_stream_id == claim.chat_stream_id)
            .one_or_none()
        )
        if (
            grant is None
            or claim.expected_grant_version is None
            or int(grant.version) != claim.expected_grant_version
        ):
            db.rollback()
            raise SandboxOperationLeaseLost
        desired = SandboxCapability.parse(claim.desired_capability)
        if str(grant.capability_level) != desired.value_name:
            grant.capability_level = desired.value_name
        grant.status = "disabled" if desired is SandboxCapability.OFF else "active"
        grant.updated_at = now
    db.commit()


def _settle_superseded(db: Session, claim: ClaimedSandboxOperation) -> None:
    now = db_now_naive()
    changed = db.execute(
        update(SandboxAdminOperation)
        .where(*_lease_filter(claim))
        .values(
            status="cancelled",
            step="superseded",
            locked_by="",
            lease_token="",
            lease_expires_at=None,
            next_attempt_at=None,
            error_code="operation_superseded",
            error_summary="管理目标已被更新的操作取代",
            finished_at=now,
            updated_at=now,
        )
    )
    if int(changed.rowcount or 0) == 1:
        db.commit()
    else:
        db.rollback()


def _settle_failure(
    db: Session,
    claim: ClaimedSandboxOperation,
    *,
    code: str,
    summary: str,
    retryable: bool,
) -> None:
    now = db_now_naive()
    retry = bool(retryable and claim.attempt_count < claim.max_attempts)
    delay_seconds = min(300, 2 ** max(0, claim.attempt_count - 1))
    changed = db.execute(
        update(SandboxAdminOperation)
        .where(*_lease_filter(claim))
        .values(
            status="pending" if retry else "failed",
            step="retry_wait" if retry else "failed",
            locked_by="",
            lease_token="",
            lease_expires_at=None,
            next_attempt_at=(now + timedelta(seconds=delay_seconds)) if retry else None,
            error_code=str(code or "runtime_unavailable")[:64],
            error_summary=str(summary or "Sandbox 管理操作失败")[:255],
            finished_at=None if retry else now,
            updated_at=now,
        )
    )
    if int(changed.rowcount or 0) != 1:
        db.rollback()
        return
    binding = db.get(WorkspaceQuotaBinding, claim.workspace_id) if claim.workspace_id else None
    if binding is not None and (
        claim.expected_quota_generation is None
        or int(binding.generation) == claim.expected_quota_generation
    ):
        binding.status = "error"
        binding.last_error_code = str(code or "runtime_unavailable")[:64]
        binding.last_error_summary = str(summary or "Sandbox 管理操作失败")[:255]
        binding.updated_at = now
    if not retry and claim.operation_type == "set_access":
        grant = (
            db.query(SandboxAccessGrant)
            .filter(SandboxAccessGrant.chat_stream_id == claim.chat_stream_id)
            .one_or_none()
        )
        if grant is not None and str(grant.capability_level) == "off":
            grant.status = "error"
            grant.updated_at = now
    db.commit()


class SandboxAdminOperationRunner:
    """仅由 nanobot-server lifespan 启动的单线程可恢复 runner。"""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        backend_factory: Callable[[], SandboxBackend] | None = None,
        worker_id: str = "sandbox-admin-runner",
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
    ) -> None:
        self.session_factory = session_factory
        self.backend_factory = backend_factory or self._default_backend
        self.worker_id = str(worker_id)[:128]
        self.lease_seconds = int(lease_seconds)
        self.poll_seconds = float(poll_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _default_backend() -> SandboxBackend:
        return HttpSandboxdAdminBackend(
            socket_path=settings.get_str("sandbox.sandboxd_socket"),
            token_file=settings.get_str("sandbox.sandboxd_admin_token_file"),
            timeout_seconds=settings.get_float("sandbox.backend_timeout_seconds", 15),
            run_timeout_seconds=settings.get_float("sandbox.run_timeout_seconds", 165),
        )

    def start(self) -> "SandboxAdminOperationRunner":
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="sandbox-admin-operations",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, float(timeout)))
        self._thread = None

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception:
                logger.error("Sandbox 管理 operation runner 异常", exc_info=True)
                worked = False
            if not worked:
                self._stop.wait(self.poll_seconds)

    def run_once(self) -> bool:
        db = self.session_factory()
        try:
            claim = _claim_operation(
                db,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
        finally:
            db.close()
        if claim is None:
            return False
        try:
            desired = (
                SandboxCapability.parse(claim.desired_capability)
                if claim.operation_type == "set_access"
                else None
            )
            if desired is SandboxCapability.OFF:
                settle_db = self.session_factory()
                try:
                    _settle_success(settle_db, claim)
                finally:
                    settle_db.close()
                return True

            prepare_db = self.session_factory()
            try:
                binding = _mark_applying(prepare_db, claim)
                project_id = int(binding.project_id)
                generation = int(binding.generation)
            finally:
                prepare_db.close()

            backend = self.backend_factory()
            try:
                backend.ensure_workspace(
                    claim.workspace_id,
                    request_id=f"{claim.operation_id}:workspace"[:64],
                )
                response = backend.apply_workspace_quota({
                    "request_id": f"{claim.operation_id}:quota"[:64],
                    "workspace_id": claim.workspace_id,
                    "project_id": project_id,
                    "quota_bytes": claim.desired_quota_bytes,
                    "generation": generation,
                })
            finally:
                close = getattr(backend, "close", None)
                if callable(close):
                    close()
            data = _response_data(response)
            if (
                str(data.get("workspace_id") or "") != claim.workspace_id
                or int(data.get("project_id") or 0) != project_id
                or int(data.get("quota_bytes") or 0) != claim.desired_quota_bytes
                or int(data.get("generation") or 0) != generation
                or data.get("applied") is not True
            ):
                raise RuntimeError("invalid sandboxd quota confirmation")
            settle_db = self.session_factory()
            try:
                _settle_success(settle_db, claim)
            finally:
                settle_db.close()
        except SandboxOperationLeaseLost:
            settle_db = self.session_factory()
            try:
                _settle_superseded(settle_db, claim)
            finally:
                settle_db.close()
        except SandboxServiceError as exc:
            settle_db = self.session_factory()
            try:
                _settle_failure(
                    settle_db,
                    claim,
                    code=exc.code.value,
                    summary=exc.summary,
                    retryable=exc.retryable,
                )
            finally:
                settle_db.close()
        except Exception:
            settle_db = self.session_factory()
            try:
                _settle_failure(
                    settle_db,
                    claim,
                    code="runtime_unavailable",
                    summary="Sandbox 配额控制面返回无效结果",
                    retryable=True,
                )
            finally:
                settle_db.close()
        return True


def start_sandbox_admin_operations(*, testing: bool) -> SandboxAdminOperationRunner | None:
    if testing:
        return None
    from core.database import SessionLocal

    return SandboxAdminOperationRunner(SessionLocal).start()


def stop_sandbox_admin_operations(
    runner: SandboxAdminOperationRunner | None,
) -> None:
    if runner is not None:
        runner.stop()


__all__ = [
    "ClaimedSandboxOperation",
    "SandboxAdminOperationRunner",
    "SandboxOperationLeaseLost",
    "start_sandbox_admin_operations",
    "stop_sandbox_admin_operations",
]

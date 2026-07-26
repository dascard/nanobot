"""Sandbox 管理操作的租约、重试与跨边界结算状态机。"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from core.database import (
    SandboxAccessGrant,
    SandboxAdminOperation,
    Workspace,
    WorkspaceMaintenanceState,
    WorkspaceQuotaBinding,
    WorkspaceRuntimeQuotaBinding,
)
from core.fencing import lease_deadline, new_fencing_token
from core.sandbox.access_contracts import SandboxCapability
from core.sandbox.backend import SandboxBackend
from core.sandbox.client import (
    HttpSandboxdAdminBackend,
    HttpSandboxdBackend,
)
from core.sandbox.contracts import SandboxServiceError
from core.sandbox.quota_service import SandboxQuotaService
from core.settings_service import settings
from core.time_utils import db_now_naive


logger = logging.getLogger("nanobot.sandbox.admin_operations")
DEFAULT_LEASE_SECONDS = 180
DEFAULT_POLL_SECONDS = 1.0
_LEASE_ID_RE = re.compile(r"sbxlease_[A-Za-z0-9_-]{1,55}")


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
    lease_token: str = field(repr=False)
    lease_expires_at: datetime


class SandboxOperationLeaseLost(RuntimeError):
    """管理操作已被其他 runner 接管或被新期望状态取代。"""


def _claim_operation(
    db: Session,
    *,
    worker_id: str,
    lease_seconds: int,
) -> ClaimedSandboxOperation | None:
    now = db_now_naive()
    normalized_worker = str(worker_id or "").strip()
    if not normalized_worker or len(normalized_worker) > 128:
        raise ValueError("worker_id 必须是 1-128 字符")
    expires_at = lease_deadline(now, lease_seconds)
    candidate = (
        db.query(SandboxAdminOperation)
        .filter(
            SandboxAdminOperation.operation_type.in_({
                "set_access",
                "set_quota",
            }),
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
            locked_by=normalized_worker,
            lease_token=token,
            lease_expires_at=expires_at,
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
        worker_id=normalized_worker,
        lease_token=token,
        lease_expires_at=expires_at,
    )


def _lease_filter(
    claim: ClaimedSandboxOperation,
    *,
    now: datetime,
):
    return (
        SandboxAdminOperation.operation_id == claim.operation_id,
        SandboxAdminOperation.status == "running",
        SandboxAdminOperation.locked_by == claim.worker_id,
        SandboxAdminOperation.lease_token == claim.lease_token,
        SandboxAdminOperation.attempt_count == claim.attempt_count,
        SandboxAdminOperation.lease_expires_at.is_not(None),
        SandboxAdminOperation.lease_expires_at > now,
    )


def _mark_applying(
    db: Session,
    claim: ClaimedSandboxOperation,
) -> tuple[WorkspaceQuotaBinding, WorkspaceRuntimeQuotaBinding]:
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
        .where(*_lease_filter(claim, now=now))
        .values(step="applying_quota", updated_at=now)
    )
    if int(operation_changed.rowcount or 0) != 1:
        db.rollback()
        raise SandboxOperationLeaseLost
    binding = db.get(WorkspaceQuotaBinding, claim.workspace_id)
    runtime_binding = db.get(
        WorkspaceRuntimeQuotaBinding,
        claim.workspace_id,
    )
    if (
        binding is None
        or runtime_binding is None
        or claim.expected_quota_generation is None
        or int(binding.generation) != claim.expected_quota_generation
        or int(runtime_binding.generation) != claim.expected_quota_generation
        or int(binding.desired_quota_bytes) != claim.desired_quota_bytes
    ):
        db.rollback()
        raise SandboxOperationLeaseLost
    maintenance_changed = db.execute(
        update(WorkspaceMaintenanceState)
        .where(
            WorkspaceMaintenanceState.workspace_id
            == claim.workspace_id,
            WorkspaceMaintenanceState.generation
            == claim.expected_quota_generation,
            WorkspaceMaintenanceState.status.in_(
                {"quiescing", "error"},
            ),
            or_(
                WorkspaceMaintenanceState.fencing_token == "",
                WorkspaceMaintenanceState.fencing_token
                == claim.lease_token,
                WorkspaceMaintenanceState.lease_expires_at.is_(None),
                WorkspaceMaintenanceState.lease_expires_at <= now,
            ),
        )
        .values(
            status="quiescing",
            locked_by=claim.worker_id,
            fencing_token=claim.lease_token,
            lease_expires_at=claim.lease_expires_at,
            last_error_code="",
            last_error_summary="",
            updated_at=now,
        )
    )
    if int(maintenance_changed.rowcount or 0) != 1:
        db.rollback()
        raise SandboxOperationLeaseLost
    binding.status = "applying"
    binding.last_error_code = ""
    binding.last_error_summary = ""
    binding.updated_at = now
    runtime_binding.status = "applying"
    runtime_binding.last_error_code = ""
    runtime_binding.last_error_summary = ""
    runtime_binding.updated_at = now
    db.commit()
    return binding, runtime_binding


def _response_data(response: dict) -> dict:
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError("invalid sandboxd quota response")
    return data


def _quota_lease_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RuntimeError("invalid sandboxd quota lease list")
    normalized = tuple(str(item or "") for item in value)
    if (
        any(not _LEASE_ID_RE.fullmatch(item) for item in normalized)
        or len(normalized) != len(set(normalized))
    ):
        raise RuntimeError("invalid sandboxd quota lease list")
    return normalized


def _settle_success(db: Session, claim: ClaimedSandboxOperation) -> None:
    now = db_now_naive()
    changed = db.execute(
        update(SandboxAdminOperation)
        .where(*_lease_filter(claim, now=now))
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
        maintenance_changed = db.execute(
            update(WorkspaceMaintenanceState)
            .where(
                WorkspaceMaintenanceState.workspace_id
                == claim.workspace_id,
                WorkspaceMaintenanceState.generation
                == claim.expected_quota_generation,
                WorkspaceMaintenanceState.status == "quiescing",
                WorkspaceMaintenanceState.locked_by == claim.worker_id,
                WorkspaceMaintenanceState.fencing_token
                == claim.lease_token,
            )
            .values(
                status="ready",
                applied_quota_generation=(
                    claim.expected_quota_generation
                ),
                locked_by="",
                fencing_token="",
                lease_expires_at=None,
                last_error_code="",
                last_error_summary="",
                updated_at=now,
            )
        )
        if int(maintenance_changed.rowcount or 0) != 1:
            db.rollback()
            raise SandboxOperationLeaseLost
        binding = db.get(WorkspaceQuotaBinding, claim.workspace_id)
        runtime_binding = db.get(
            WorkspaceRuntimeQuotaBinding,
            claim.workspace_id,
        )
        workspace = db.get(Workspace, claim.workspace_id)
        if (
            binding is None
            or runtime_binding is None
            or workspace is None
            or int(binding.generation) != claim.expected_quota_generation
            or int(runtime_binding.generation)
            != claim.expected_quota_generation
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
        runtime_binding.status = "applied"
        runtime_binding.applied_quota_bytes = int(
            runtime_binding.desired_quota_bytes
        )
        runtime_binding.last_error_code = ""
        runtime_binding.last_error_summary = ""
        runtime_binding.last_applied_at = now
        runtime_binding.updated_at = now
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
        .where(*_lease_filter(claim, now=now))
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
        .where(*_lease_filter(claim, now=now))
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
    runtime_binding = (
        db.get(WorkspaceRuntimeQuotaBinding, claim.workspace_id)
        if claim.workspace_id
        else None
    )
    if binding is not None and (
        claim.expected_quota_generation is None
        or int(binding.generation) == claim.expected_quota_generation
    ):
        binding.status = "error"
        binding.last_error_code = str(code or "runtime_unavailable")[:64]
        binding.last_error_summary = str(summary or "Sandbox 管理操作失败")[:255]
        binding.updated_at = now
    if runtime_binding is not None and (
        claim.expected_quota_generation is None
        or int(runtime_binding.generation) == claim.expected_quota_generation
    ):
        runtime_binding.status = "error"
        runtime_binding.last_error_code = str(
            code or "runtime_unavailable"
        )[:64]
        runtime_binding.last_error_summary = str(
            summary or "Sandbox 管理操作失败"
        )[:255]
        runtime_binding.updated_at = now
    maintenance = (
        db.get(WorkspaceMaintenanceState, claim.workspace_id)
        if claim.workspace_id
        else None
    )
    if (
        maintenance is not None
        and (
            claim.expected_quota_generation is None
            or int(maintenance.generation)
            == claim.expected_quota_generation
        )
        and str(maintenance.fencing_token or "")
        == claim.lease_token
    ):
        maintenance.status = "error"
        maintenance.locked_by = ""
        maintenance.fencing_token = ""
        maintenance.lease_expires_at = None
        maintenance.last_error_code = str(
            code or "runtime_unavailable"
        )[:64]
        maintenance.last_error_summary = str(
            summary or "Sandbox 管理操作失败"
        )[:255]
        maintenance.updated_at = now
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
        lease_backend_factory: Callable[[], SandboxBackend] | None = None,
        worker_id: str = "sandbox-admin-runner",
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
    ) -> None:
        self.session_factory = session_factory
        self.backend_factory = backend_factory or self._default_backend
        self.lease_backend_factory = (
            lease_backend_factory or self._default_lease_backend
        )
        self.worker_id = str(worker_id)[:128]
        self.lease_seconds = int(lease_seconds)
        self.poll_seconds = float(poll_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.lease_reconciler: Any | None = None

    @staticmethod
    def _default_backend() -> SandboxBackend:
        return HttpSandboxdAdminBackend(
            socket_path=settings.get_str("sandbox.sandboxd_socket"),
            token_file=settings.get_str("sandbox.sandboxd_admin_token_file"),
            timeout_seconds=settings.get_float("sandbox.backend_timeout_seconds", 15),
            run_timeout_seconds=settings.get_float("sandbox.run_timeout_seconds", 165),
        )

    @staticmethod
    def _default_lease_backend() -> SandboxBackend:
        return HttpSandboxdBackend(
            socket_path=settings.get_str("sandbox.sandboxd_socket"),
            token_file=settings.get_str("sandbox.sandboxd_token_file"),
            timeout_seconds=settings.get_float(
                "sandbox.backend_timeout_seconds",
                15,
            ),
            run_timeout_seconds=settings.get_float(
                "sandbox.run_timeout_seconds",
                165,
            ),
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
        if self.lease_reconciler is not None:
            self.lease_reconciler.stop(timeout=timeout)
            self.lease_reconciler = None
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
                # 关闭授权必须与部分降级一致地回收该 Workspace 的活动
                # Lease:enqueue 已立即收窄权限,但撤权用户正在运行的容器
                # (含 developer 网络出口)不得继续跑到 TTL。
                off_candidates: tuple = ()
                if claim.workspace_id:
                    snapshot_db = self.session_factory()
                    try:
                        off_candidates = SandboxQuotaService(
                            snapshot_db,
                        ).snapshot_rebuild_candidates(claim.workspace_id)
                    finally:
                        snapshot_db.close()
                if off_candidates:
                    backend = self.backend_factory()
                    try:
                        for candidate in off_candidates:
                            backend.stop_lease(
                                candidate.lease_id,
                                request_id=(
                                    f"{claim.operation_id}:off:"
                                    f"{candidate.lease_id[-16:]}"
                                )[:64],
                            )
                    finally:
                        close = getattr(backend, "close", None)
                        if callable(close):
                            close()
                settle_db = self.session_factory()
                try:
                    if off_candidates:
                        SandboxQuotaService(settle_db).settle_stopped_leases(
                            off_candidates,
                            reason_code="access_revoked",
                            reason_summary="Sandbox 授权已关闭,Lease 已回收",
                        )
                    _settle_success(settle_db, claim)
                finally:
                    settle_db.close()
                return True

            prepare_db = self.session_factory()
            try:
                binding, runtime_binding = _mark_applying(prepare_db, claim)
                rebuild_candidates = SandboxQuotaService(
                    prepare_db,
                ).snapshot_rebuild_candidates(claim.workspace_id)
                project_id = int(binding.project_id)
                runtime_project_id = int(runtime_binding.project_id)
                runtime_quota_bytes = int(
                    runtime_binding.desired_quota_bytes
                )
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
                    "runtime_project_id": runtime_project_id,
                    "runtime_quota_bytes": runtime_quota_bytes,
                    "generation": generation,
                })
            finally:
                close = getattr(backend, "close", None)
                if callable(close):
                    close()
            data = _response_data(response)
            _quota_lease_ids(data.get("terminated_lease_ids"))
            failed_lease_ids = _quota_lease_ids(
                data.get("failed_lease_ids"),
            )
            if (
                str(data.get("workspace_id") or "") != claim.workspace_id
                or int(data.get("project_id") or 0) != project_id
                or int(data.get("quota_bytes") or 0) != claim.desired_quota_bytes
                or int(data.get("runtime_project_id") or 0)
                != runtime_project_id
                or int(data.get("runtime_quota_bytes") or 0)
                != runtime_quota_bytes
                or int(data.get("generation") or 0) != generation
                or failed_lease_ids
                or data.get("workspace_project_id_matches") is not True
                or data.get("workspace_quota_bytes_matches") is not True
                or data.get("runtime_project_id_matches") is not True
                or data.get("runtime_quota_bytes_matches") is not True
                or data.get("quota_verified") is not True
                or data.get("applied") is not True
            ):
                raise RuntimeError("invalid sandboxd quota confirmation")
            settle_db = self.session_factory()
            try:
                SandboxQuotaService(settle_db).settle_stopped_leases(
                    rebuild_candidates,
                )
                _settle_success(settle_db, claim)
            finally:
                settle_db.close()
            if rebuild_candidates:
                lease_backend = self.lease_backend_factory()
                rebuild_db = self.session_factory()
                try:
                    SandboxQuotaService(
                        rebuild_db,
                    ).rebuild_if_still_allowed(
                        rebuild_candidates,
                        lease_backend,
                    )
                finally:
                    rebuild_db.close()
                    close = getattr(lease_backend, "close", None)
                    if callable(close):
                        close()
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
    from core.sandbox.lease_reconciler import SandboxLeaseReconciler

    runner = SandboxAdminOperationRunner(SessionLocal).start()
    if settings.get_bool("sandbox.session_execution_allowed", False):
        runner.lease_reconciler = SandboxLeaseReconciler(
            SessionLocal,
            interval_seconds=settings.get_float(
                "sandbox.lease_reconcile_interval_seconds",
                15,
            ),
        ).start()
    return runner


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

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from core.database import (
    SandboxAccessGrant,
    SandboxAdminOperation,
    Workspace,
    WorkspaceMaintenanceState,
    WorkspaceQuotaBinding,
    WorkspaceRuntimeQuotaBinding,
)
from core.sandbox.admin_operations import (
    SandboxAdminOperationRunner,
    SandboxOperationLeaseLost,
    _claim_operation,
    _settle_success,
)
from core.sandbox.admin_service import (
    SandboxAdminRequestError,
    SandboxAdminService,
)
from core.sandbox.contracts import (
    SandboxErrorCode,
    SandboxServiceError,
    success_result,
)
from core.settings_service import settings
from core.time_utils import db_now_naive


MIB = 1024 * 1024


class _QuotaBackend:
    def __init__(self, *, failure: SandboxServiceError | None = None) -> None:
        self.failure = failure
        self.ensure_calls: list[tuple[str, str]] = []
        self.apply_calls: list[dict] = []
        self.stop_lease_calls: list[tuple[str, str]] = []
        self.closed = False

    def stop_lease(self, lease_id: str, *, request_id: str):
        self.stop_lease_calls.append((lease_id, request_id))
        return success_result(
            "已停止",
            data={"lease_id": lease_id, "lease_recycled": True},
        )

    def ensure_workspace(self, workspace_id: str, *, request_id: str):
        self.ensure_calls.append((workspace_id, request_id))
        return success_result("Workspace 已创建", data={"workspace_id": workspace_id})

    def apply_workspace_quota(self, payload):
        self.apply_calls.append(dict(payload))
        if self.failure is not None:
            raise self.failure
        return success_result(
            "配额已应用",
            data={
                "workspace_id": payload["workspace_id"],
                "project_id": payload["project_id"],
                "quota_bytes": payload["quota_bytes"],
                "runtime_project_id": payload["runtime_project_id"],
                "runtime_quota_bytes": payload["runtime_quota_bytes"],
                "generation": payload["generation"],
                "used_bytes": 0,
                "terminated_lease_ids": [],
                "failed_lease_ids": [],
                "workspace_project_id_matches": True,
                "workspace_quota_bytes_matches": True,
                "runtime_project_id_matches": True,
                "runtime_quota_bytes_matches": True,
                "quota_verified": True,
                "applied": True,
            },
        )

    def close(self):
        self.closed = True


def _factory(db_session):
    return sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        expire_on_commit=False,
    )


def _enqueue_access(
    db,
    *,
    request_id: str,
    session_id: str,
    capability: str = "workspace",
    execution_profile: str = "restricted",
    quota_bytes: int = 64 * MIB,
    expected_version: int | None = None,
):
    return SandboxAdminService(db).enqueue_access_change(
        request_id=request_id,
        platform="qq",
        chat_type="private",
        session_id=session_id,
        capability=capability,
        execution_profile=execution_profile,
        quota_bytes=None if capability == "off" else quota_bytes,
        expected_version=expected_version,
        reason="测试授权",
        actor="admin-test",
    )


def test_profile_selection_is_validated_and_part_of_idempotency(
    db_session,
):
    first = _enqueue_access(
        db_session,
        request_id="developer-profile-request-1",
        session_id="private_developer-profile",
        capability="exec",
        execution_profile="developer",
    )
    db_session.commit()

    grant = db_session.query(SandboxAccessGrant).one()
    runtime_binding = db_session.query(
        WorkspaceRuntimeQuotaBinding
    ).one()
    assert grant.execution_profile == "developer"
    assert runtime_binding.desired_quota_bytes == 10 * 1024 * MIB

    repeated = _enqueue_access(
        db_session,
        request_id="developer-profile-request-1",
        session_id="private_developer-profile",
        capability="exec",
        execution_profile="developer",
    )
    assert repeated.created is False
    assert repeated.operation.operation_id == first.operation.operation_id

    with pytest.raises(SandboxAdminRequestError) as conflict:
        _enqueue_access(
            db_session,
            request_id="developer-profile-request-1",
            session_id="private_developer-profile",
            capability="exec",
            execution_profile="restricted",
        )
    assert conflict.value.code == "idempotency_conflict"

    with pytest.raises(SandboxAdminRequestError) as rejected:
        _enqueue_access(
            db_session,
            request_id="trusted-profile-request-1",
            session_id="private_trusted-profile",
            capability="exec",
            execution_profile="trusted_developer",
        )
    assert rejected.value.code == "invalid_execution_profile"


def test_identical_access_resave_is_noop_and_does_not_requiesce(db_session):
    """原样保存已应用的授权不得再次 quiesce Workspace/推进版本。"""
    _enqueue_access(
        db_session,
        request_id="noop-access-request-1",
        session_id="private_noop-session",
        capability="assets",
        quota_bytes=64 * MIB,
    )
    db_session.commit()
    runner = SandboxAdminOperationRunner(
        _factory(db_session),
        backend_factory=lambda: _QuotaBackend(),
        worker_id="runner-noop",
    )
    assert runner.run_once() is True
    db_session.expire_all()
    grant = db_session.query(SandboxAccessGrant).one()
    version_before = int(grant.version)
    binding = db_session.query(WorkspaceQuotaBinding).one()
    generation_before = int(binding.generation)
    maintenance = db_session.get(
        WorkspaceMaintenanceState,
        str(grant.workspace_id),
    )
    assert maintenance is not None and maintenance.status == "ready"

    resave = _enqueue_access(
        db_session,
        request_id="noop-access-request-2",
        session_id="private_noop-session",
        capability="assets",
        quota_bytes=64 * MIB,
    )
    db_session.commit()

    assert resave.created is False
    assert resave.operation.status == "succeeded"
    assert resave.operation.step == "noop_no_change"
    db_session.expire_all()
    grant = db_session.query(SandboxAccessGrant).one()
    binding = db_session.query(WorkspaceQuotaBinding).one()
    maintenance = db_session.get(
        WorkspaceMaintenanceState,
        str(grant.workspace_id),
    )
    assert int(grant.version) == version_before
    assert grant.status == "active"
    assert int(binding.generation) == generation_before
    assert binding.status == "applied"
    assert maintenance.status == "ready"


def test_quota_change_replay_wins_over_stale_usage_precheck(db_session):
    """已入队请求的重放不得被后续用量变化的预检误判为 409。"""
    _enqueue_access(
        db_session,
        request_id="quota-replay-access",
        session_id="private_quota-replay",
        capability="assets",
        quota_bytes=64 * MIB,
    )
    db_session.commit()
    grant = db_session.query(SandboxAccessGrant).one()
    workspace_id = str(grant.workspace_id)

    first = SandboxAdminService(db_session).enqueue_quota_change(
        request_id="quota-replay-request-1",
        workspace_id=workspace_id,
        quota_bytes=96 * MIB,
        reason="扩容",
        actor="admin-test",
    )
    db_session.commit()
    assert first.created is True

    workspace = db_session.get(Workspace, workspace_id)
    workspace.used_bytes = 128 * MIB  # 用量随后超过目标配额
    db_session.commit()

    replayed = SandboxAdminService(db_session).enqueue_quota_change(
        request_id="quota-replay-request-1",
        workspace_id=workspace_id,
        quota_bytes=96 * MIB,
        reason="扩容",
        actor="admin-test",
    )

    assert replayed.created is False
    assert replayed.operation.operation_id == first.operation.operation_id


def test_async_runner_never_claims_synchronous_lease_operation(db_session):
    now = db_now_naive()
    synchronous = SandboxAdminOperation(
        operation_id="sbxop_sync_lease_stop",
        request_id="sync-lease-stop-request",
        operation_type="lease_stop",
        status="pending",
        step="queued",
        created_at=now,
        updated_at=now,
    )
    asynchronous = SandboxAdminOperation(
        operation_id="sbxop_async_set_quota",
        request_id="async-set-quota-request",
        operation_type="set_quota",
        status="pending",
        step="queued",
        created_at=now + timedelta(seconds=1),
        updated_at=now + timedelta(seconds=1),
    )
    db_session.add_all([synchronous, asynchronous])
    db_session.commit()

    claim = _claim_operation(
        db_session,
        worker_id="runner-filter-test",
        lease_seconds=180,
    )

    assert claim is not None
    assert claim.operation_id == asynchronous.operation_id
    db_session.expire_all()
    assert db_session.get(
        SandboxAdminOperation,
        synchronous.operation_id,
    ).status == "pending"


def test_access_upgrade_is_not_active_until_quota_runner_confirms(db_session):
    result = _enqueue_access(
        db_session,
        request_id="access-request-0001",
        session_id="private_session-a",
        capability="assets",
        quota_bytes=64 * MIB,
    )
    db_session.commit()

    grant = db_session.query(SandboxAccessGrant).one()
    binding = db_session.query(WorkspaceQuotaBinding).one()
    runtime_binding = db_session.query(WorkspaceRuntimeQuotaBinding).one()
    operation = result.operation
    assert result.created is True
    assert grant.capability_level == "off"
    assert grant.status == "provisioning"
    assert binding.status == "pending"
    assert binding.applied_quota_bytes == 0
    assert runtime_binding.status == "pending"
    assert runtime_binding.applied_quota_bytes == 0
    assert operation.status == "pending"

    backend = _QuotaBackend()
    runner = SandboxAdminOperationRunner(
        _factory(db_session),
        backend_factory=lambda: backend,
        worker_id="runner-a",
    )
    assert runner.run_once() is True

    db_session.expire_all()
    grant = db_session.query(SandboxAccessGrant).one()
    binding = db_session.query(WorkspaceQuotaBinding).one()
    runtime_binding = db_session.query(WorkspaceRuntimeQuotaBinding).one()
    operation = db_session.get(SandboxAdminOperation, operation.operation_id)
    workspace = db_session.get(Workspace, grant.workspace_id)
    assert operation.status == "succeeded"
    assert operation.step == "completed"
    assert grant.capability_level == "assets"
    assert grant.status == "active"
    assert binding.status == "applied"
    assert binding.applied_quota_bytes == 64 * MIB
    assert runtime_binding.status == "applied"
    assert runtime_binding.applied_quota_bytes == 512 * MIB
    assert workspace.quota_bytes == 64 * MIB
    assert backend.closed is True


def test_access_enqueue_does_not_open_hidden_settings_session(
    db_session,
    monkeypatch,
):
    def reject_hidden_session(*_args, **_kwargs):
        raise AssertionError("Sandbox 管理事务不得调用全局 settings.get")

    monkeypatch.setattr(settings, "get", reject_hidden_session)

    result = _enqueue_access(
        db_session,
        request_id="caller-session-settings-1",
        session_id="private_caller-session-settings",
        capability="workspace",
        quota_bytes=64 * MIB,
    )
    db_session.flush()

    assert result.created is True
    assert result.operation.desired_quota_bytes == 64 * MIB


def test_request_id_is_idempotent_and_rejects_payload_reuse(db_session):
    first = _enqueue_access(
        db_session,
        request_id="idempotent-request-1",
        session_id="private_idempotent",
    )
    db_session.commit()

    second = _enqueue_access(
        db_session,
        request_id="idempotent-request-1",
        session_id="private_idempotent",
    )
    assert second.created is False
    assert second.operation.operation_id == first.operation.operation_id

    with pytest.raises(SandboxAdminRequestError) as conflict:
        _enqueue_access(
            db_session,
            request_id="idempotent-request-1",
            session_id="private_idempotent",
            quota_bytes=96 * MIB,
        )
    assert conflict.value.code == "idempotency_conflict"
    assert conflict.value.status_code == 409


def test_completed_request_retry_wins_over_stale_expected_version(db_session):
    first = _enqueue_access(
        db_session,
        request_id="completed-idempotent-request-1",
        session_id="private_completed-idempotent",
        expected_version=None,
    )
    original_version = first.operation.expected_grant_version - 1
    db_session.commit()
    SandboxAdminOperationRunner(
        _factory(db_session),
        backend_factory=_QuotaBackend,
    ).run_once()
    db_session.expire_all()

    repeated = _enqueue_access(
        db_session,
        request_id="completed-idempotent-request-1",
        session_id="private_completed-idempotent",
        expected_version=original_version,
    )

    assert repeated.created is False
    assert repeated.operation.operation_id == first.operation.operation_id


def test_newer_capability_request_supersedes_older_with_same_quota(db_session):
    first = _enqueue_access(
        db_session,
        request_id="capability-generation-request-1",
        session_id="private_capability-generation",
        capability="workspace",
        quota_bytes=64 * MIB,
    )
    db_session.commit()
    current_version = db_session.query(SandboxAccessGrant).one().version
    second = _enqueue_access(
        db_session,
        request_id="capability-generation-request-2",
        session_id="private_capability-generation",
        capability="assets",
        quota_bytes=64 * MIB,
        expected_version=current_version,
    )
    db_session.commit()
    backend = _QuotaBackend()
    runner = SandboxAdminOperationRunner(
        _factory(db_session),
        backend_factory=lambda: backend,
    )

    assert runner.run_once() is True
    assert runner.run_once() is True

    db_session.expire_all()
    assert db_session.get(
        SandboxAdminOperation,
        first.operation.operation_id,
    ).status == "cancelled"
    assert db_session.get(
        SandboxAdminOperation,
        second.operation.operation_id,
    ).status == "succeeded"
    grant = db_session.query(SandboxAccessGrant).one()
    assert grant.capability_level == "assets"
    assert grant.version == second.operation.expected_grant_version
    assert len(backend.apply_calls) == 1


def test_grant_version_conflict_is_rejected_without_new_operation(db_session):
    _enqueue_access(
        db_session,
        request_id="version-request-0001",
        session_id="private_versioned",
    )
    db_session.commit()

    with pytest.raises(SandboxAdminRequestError) as conflict:
        _enqueue_access(
            db_session,
            request_id="version-request-0002",
            session_id="private_versioned",
            expected_version=999,
        )

    assert conflict.value.code == "grant_version_conflict"
    assert db_session.query(SandboxAdminOperation).count() == 1


def test_project_ids_are_database_allocated_and_unique(db_session):
    _enqueue_access(
        db_session,
        request_id="project-request-0001",
        session_id="private_project-a",
    )
    db_session.commit()
    _enqueue_access(
        db_session,
        request_id="project-request-0002",
        session_id="private_project-b",
    )
    db_session.commit()

    workspace_project_ids = [
        row.project_id
        for row in db_session.query(WorkspaceQuotaBinding)
        .order_by(WorkspaceQuotaBinding.project_id)
        .all()
    ]
    runtime_project_ids = [
        row.project_id
        for row in db_session.query(WorkspaceRuntimeQuotaBinding)
        .order_by(WorkspaceRuntimeQuotaBinding.project_id)
        .all()
    ]
    assert workspace_project_ids == [10000, 10002]
    assert runtime_project_ids == [10001, 10003]
    assert not set(workspace_project_ids) & set(runtime_project_ids)


def test_quota_cannot_shrink_below_current_workspace_usage(db_session):
    access = _enqueue_access(
        db_session,
        request_id="usage-request-0001",
        session_id="private_usage",
        quota_bytes=64 * MIB,
    )
    db_session.commit()
    workspace = db_session.get(Workspace, access.operation.workspace_id)
    workspace.used_bytes = 48 * MIB
    db_session.commit()

    with pytest.raises(SandboxAdminRequestError) as below_usage:
        SandboxAdminService(db_session).enqueue_quota_change(
            request_id="usage-request-0002",
            workspace_id=workspace.id,
            quota_bytes=32 * MIB,
            reason="错误缩容",
            actor="admin-test",
        )

    assert below_usage.value.code == "quota_below_usage"
    assert below_usage.value.status_code == 409


def test_newer_quota_generation_supersedes_older_operation(db_session):
    access = _enqueue_access(
        db_session,
        request_id="supersede-access-1",
        session_id="private_supersede",
        quota_bytes=64 * MIB,
    )
    db_session.commit()
    bootstrap_backend = _QuotaBackend()
    SandboxAdminOperationRunner(
        _factory(db_session),
        backend_factory=lambda: bootstrap_backend,
    ).run_once()

    first = SandboxAdminService(db_session).enqueue_quota_change(
        request_id="supersede-quota-1",
        workspace_id=access.operation.workspace_id,
        quota_bytes=80 * MIB,
        reason="第一次扩容",
        actor="admin-test",
    )
    db_session.commit()
    second = SandboxAdminService(db_session).enqueue_quota_change(
        request_id="supersede-quota-2",
        workspace_id=access.operation.workspace_id,
        quota_bytes=96 * MIB,
        reason="第二次扩容",
        actor="admin-test",
    )
    db_session.commit()

    backend = _QuotaBackend()
    runner = SandboxAdminOperationRunner(
        _factory(db_session),
        backend_factory=lambda: backend,
    )
    assert runner.run_once() is True
    assert runner.run_once() is True

    db_session.expire_all()
    assert db_session.get(
        SandboxAdminOperation,
        first.operation.operation_id,
    ).status == "cancelled"
    assert db_session.get(
        SandboxAdminOperation,
        second.operation.operation_id,
    ).status == "succeeded"
    binding = db_session.get(
        WorkspaceQuotaBinding,
        access.operation.workspace_id,
    )
    assert binding.applied_quota_bytes == 96 * MIB
    assert len(backend.apply_calls) == 1


def test_retryable_failure_survives_runner_restart(db_session):
    access = _enqueue_access(
        db_session,
        request_id="retry-request-0001",
        session_id="private_retry",
    )
    db_session.commit()
    failure = SandboxServiceError(
        SandboxErrorCode.RUNTIME_UNAVAILABLE,
        "配额控制面暂不可用",
        retryable=True,
        stop=False,
    )
    failed_backend = _QuotaBackend(failure=failure)
    first_runner = SandboxAdminOperationRunner(
        _factory(db_session),
        backend_factory=lambda: failed_backend,
        worker_id="runner-before-restart",
    )
    assert first_runner.run_once() is True

    db_session.expire_all()
    operation = db_session.get(
        SandboxAdminOperation,
        access.operation.operation_id,
    )
    assert operation.status == "pending"
    assert operation.step == "retry_wait"
    assert operation.attempt_count == 1
    operation.next_attempt_at = None
    db_session.commit()

    success_backend = _QuotaBackend()
    restarted_runner = SandboxAdminOperationRunner(
        _factory(db_session),
        backend_factory=lambda: success_backend,
        worker_id="runner-after-restart",
    )
    assert restarted_runner.run_once() is True

    db_session.expire_all()
    operation = db_session.get(
        SandboxAdminOperation,
        access.operation.operation_id,
    )
    assert operation.status == "succeeded"
    assert operation.attempt_count == 2
    assert db_session.query(SandboxAccessGrant).one().capability_level == "workspace"


def test_expired_running_operation_is_reclaimed_with_new_lease(db_session):
    access = _enqueue_access(
        db_session,
        request_id="stale-lease-request-1",
        session_id="private_stale-lease",
    )
    db_session.commit()
    operation = db_session.get(
        SandboxAdminOperation,
        access.operation.operation_id,
    )
    operation.status = "running"
    operation.locked_by = "dead-runner"
    operation.lease_token = "expired-token"
    operation.lease_expires_at = db_now_naive() - timedelta(seconds=1)
    db_session.commit()

    claim_db = _factory(db_session)()
    try:
        claim = _claim_operation(
            claim_db,
            worker_id="replacement-runner",
            lease_seconds=180,
        )
    finally:
        claim_db.close()

    assert claim is not None
    assert claim.operation_id == access.operation.operation_id
    assert claim.worker_id == "replacement-runner"
    assert claim.lease_token != "expired-token"
    assert claim.attempt_count == 1


def test_retryable_failure_stops_after_max_attempts(db_session):
    access = _enqueue_access(
        db_session,
        request_id="bounded-retry-request-1",
        session_id="private_bounded-retry",
    )
    db_session.commit()
    operation = db_session.get(
        SandboxAdminOperation,
        access.operation.operation_id,
    )
    operation.max_attempts = 2
    db_session.commit()
    failure = SandboxServiceError(
        SandboxErrorCode.RUNTIME_UNAVAILABLE,
        "配额控制面持续不可用",
        retryable=True,
        stop=False,
    )
    runner = SandboxAdminOperationRunner(
        _factory(db_session),
        backend_factory=lambda: _QuotaBackend(failure=failure),
    )

    assert runner.run_once() is True
    db_session.expire_all()
    operation = db_session.get(
        SandboxAdminOperation,
        access.operation.operation_id,
    )
    assert operation.status == "pending"
    operation.next_attempt_at = None
    db_session.commit()

    assert runner.run_once() is True
    db_session.expire_all()
    operation = db_session.get(
        SandboxAdminOperation,
        access.operation.operation_id,
    )
    assert operation.status == "failed"
    assert operation.attempt_count == 2
    assert operation.next_attempt_at is None
    assert db_session.query(SandboxAccessGrant).one().capability_level == "off"


def test_failed_upgrade_never_enables_requested_capability(db_session):
    access = _enqueue_access(
        db_session,
        request_id="failed-upgrade-01",
        session_id="private_failed-upgrade",
        capability="exec",
    )
    db_session.commit()
    failure = SandboxServiceError(
        SandboxErrorCode.RUNTIME_UNAVAILABLE,
        "配额应用永久失败",
        retryable=False,
    )
    runner = SandboxAdminOperationRunner(
        _factory(db_session),
        backend_factory=lambda: _QuotaBackend(failure=failure),
    )
    assert runner.run_once() is True

    db_session.expire_all()
    operation = db_session.get(
        SandboxAdminOperation,
        access.operation.operation_id,
    )
    grant = db_session.query(SandboxAccessGrant).one()
    assert operation.status == "failed"
    assert grant.capability_level == "off"
    assert grant.status == "error"


def test_capability_off_recycles_active_leases(db_session):
    """关闭授权必须回收该 Workspace 的活动 Lease,不得留容器跑到 TTL。"""
    from core.database import SandboxLease, SandboxRun

    _enqueue_access(
        db_session,
        request_id="off-recycle-grant",
        session_id="private_off-recycle",
        capability="exec",
        execution_profile="developer",
    )
    db_session.commit()
    runner = SandboxAdminOperationRunner(
        _factory(db_session),
        backend_factory=lambda: _QuotaBackend(),
        worker_id="runner-off-setup",
    )
    assert runner.run_once() is True
    db_session.expire_all()
    grant = db_session.query(SandboxAccessGrant).one()
    lease = SandboxLease(
        lease_id="sbxlease_off_recycle",
        lease_key="e" * 64,
        grant_id=str(grant.id),
        chat_stream_id=str(grant.chat_stream_id),
        workspace_id=str(grant.workspace_id),
        profile_id="developer",
        catalog_generation="20260725.1",
        policy_sha256="b" * 64,
        status="active",
        image_digest="sha256:" + "a" * 64,
        controller_epoch="sbxctl_" + "5" * 32,
    )
    run = SandboxRun(
        run_id="sbxrun_off_recycle",
        request_id="sbxreq_off_recycle",
        workspace_id=str(grant.workspace_id),
        lease_id=lease.lease_id,
        profile_id="developer",
        execution_mode="lease",
        process_state="running",
        image_digest="sha256:" + "a" * 64,
        status="running",
    )
    db_session.add_all([lease, run])
    db_session.commit()

    _enqueue_access(
        db_session,
        request_id="off-recycle-request",
        session_id="private_off-recycle",
        capability="off",
        execution_profile="developer",
    )
    db_session.commit()
    off_backend = _QuotaBackend()
    off_runner = SandboxAdminOperationRunner(
        _factory(db_session),
        backend_factory=lambda: off_backend,
        worker_id="runner-off",
    )
    assert off_runner.run_once() is True

    assert [call[0] for call in off_backend.stop_lease_calls] == [
        "sbxlease_off_recycle",
    ]
    db_session.expire_all()
    settled_lease = db_session.get(SandboxLease, "sbxlease_off_recycle")
    settled_run = db_session.get(SandboxRun, "sbxrun_off_recycle")
    assert settled_lease.status == "stopped"
    assert settled_lease.last_error_code == "access_revoked"
    assert settled_run.status == "failed"
    assert settled_run.termination_reason == "access_revoked"
    operation = db_session.query(SandboxAdminOperation).filter_by(
        request_id="off-recycle-request",
    ).one()
    assert operation.status == "succeeded"


def test_lost_lease_cannot_commit_terminal_state(db_session):
    result = _enqueue_access(
        db_session,
        request_id="lease-request-0001",
        session_id="private_lease",
        capability="off",
    )
    db_session.commit()
    factory = _factory(db_session)
    claim_db = factory()
    try:
        claim = _claim_operation(
            claim_db,
            worker_id="runner-lease-a",
            lease_seconds=180,
        )
    finally:
        claim_db.close()
    assert claim is not None

    operation = db_session.get(
        SandboxAdminOperation,
        result.operation.operation_id,
    )
    operation.lease_token = "replacement-fencing-token"
    operation.lease_expires_at = db_now_naive() + timedelta(seconds=180)
    db_session.commit()

    settle_db = factory()
    try:
        with pytest.raises(SandboxOperationLeaseLost):
            _settle_success(settle_db, claim)
    finally:
        settle_db.close()

    db_session.expire_all()
    operation = db_session.get(
        SandboxAdminOperation,
        result.operation.operation_id,
    )
    assert operation.status == "running"
    assert operation.lease_token == "replacement-fencing-token"


def test_sandbox_operation_lease_checks_attempt_generation(db_session):
    result = _enqueue_access(
        db_session,
        request_id="lease-attempt-request-0001",
        session_id="private_lease_attempt",
        capability="off",
    )
    db_session.commit()
    factory = _factory(db_session)
    claim_db = factory()
    try:
        claim = _claim_operation(
            claim_db,
            worker_id="same-runner",
            lease_seconds=180,
        )
    finally:
        claim_db.close()
    assert claim is not None

    operation = db_session.get(
        SandboxAdminOperation,
        result.operation.operation_id,
    )
    operation.attempt_count = int(operation.attempt_count) + 1
    db_session.commit()

    settle_db = factory()
    try:
        with pytest.raises(SandboxOperationLeaseLost):
            _settle_success(settle_db, claim)
    finally:
        settle_db.close()

    db_session.expire_all()
    operation = db_session.get(
        SandboxAdminOperation,
        result.operation.operation_id,
    )
    assert operation.status == "running"
    assert operation.attempt_count == claim.attempt_count + 1

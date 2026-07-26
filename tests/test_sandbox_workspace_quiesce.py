from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from core.database import (
    SandboxAccessGrant,
    SandboxLease,
    SystemSetting,
    WorkspaceMaintenanceState,
    WorkspaceRuntimeQuotaBinding,
)
from core.sandbox.admin_operations import SandboxAdminOperationRunner
from core.sandbox.admin_service import SandboxAdminService
from core.sandbox.access_policy import SandboxAccessPolicy
from core.sandbox.backend import FakeSandboxBackend
from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.execution_profiles import load_execution_profile_registry
from core.sandbox.lease_service import SandboxLeaseService
from core.settings_service import settings
from sandboxd.app import create_app
from tests.test_sandbox_admin_service import (
    MIB,
    _QuotaBackend,
    _enqueue_access,
    _factory,
)
from tests.test_sandbox_lease_reconciler import _LeaseServiceBackend
from tests.test_sandbox_quota_backend import _with_admin_runtime
from tests.test_sandboxd_api import WORKSPACE_ID, WORKSPACE_ID_B
from tests.test_sandboxd_lease_backend import (
    IMAGE_ID,
    _components,
    _ensure,
    _manifest,
)


def test_workspace_maintenance_recycles_only_target_and_does_not_block_b(
    tmp_path,
):
    config, workspace_files, _assets, _docker, _store, backend = (
        _components(tmp_path)
    )
    lease_a = _ensure(
        backend,
        config,
        request_id="lease_request_workspace_a",
        lease_id="sbxlease_workspace_a",
        workspace_id=WORKSPACE_ID,
        quota_generation=1,
    )
    lease_b = _ensure(
        backend,
        config,
        request_id="lease_request_workspace_b",
        lease_id="sbxlease_workspace_b",
        workspace_id=WORKSPACE_ID_B,
        quota_generation=1,
    )

    with workspace_files.maintenance.quota_maintenance(
        WORKSPACE_ID,
        generation=2,
    ):
        stopped = backend.terminate_workspace(
            WORKSPACE_ID,
            reason="quota_reconfigured",
        )
        with pytest.raises(SandboxServiceError) as quiescing:
            _ensure(
                backend,
                config,
                request_id="lease_request_workspace_a_race",
                lease_id="sbxlease_workspace_a_race",
                workspace_id=WORKSPACE_ID,
                quota_generation=2,
            )
        assert quiescing.value.code is SandboxErrorCode.SANDBOX_BUSY
        assert backend.get(lease_b["lease_id"])["running"] is True

    assert stopped["terminated_lease_ids"] == [lease_a["lease_id"]]
    assert backend.get(lease_a["lease_id"])["present"] is False
    assert backend.get(lease_b["lease_id"])["running"] is True
    assert workspace_files.maintenance.state(
        WORKSPACE_ID,
    ).applied_generation == 2
    assert workspace_files.maintenance.state(
        WORKSPACE_ID_B,
    ).quiescing is False


def test_failed_quota_window_stays_quiesced_and_rejects_new_execution(
    tmp_path,
):
    from tests.test_sandboxd_api import _runtime

    token, runtime = _runtime(tmp_path)
    runtime.workspace_files.layout.ensure_roots()
    runtime.workspace_files.ensure_workspace(WORKSPACE_ID)
    runtime.workspace_files.ensure_workspace(WORKSPACE_ID_B)

    with pytest.raises(RuntimeError, match="quota failed"):
        with runtime.workspace_files.maintenance.quota_maintenance(
            WORKSPACE_ID,
            generation=3,
        ):
            raise RuntimeError("quota failed")

    with TestClient(create_app(runtime)) as client:
        blocked = client.post(
            "/v1/runs",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "request_id": "run-quiescing-workspace-a",
                "run_id": "sbxrun_quiescing_workspace_a",
                "workspace_id": WORKSPACE_ID,
                "command": "true",
                "quota_bytes": MIB,
            },
        )
        unaffected = client.post(
            "/v1/runs",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "request_id": "run-active-workspace-b",
                "run_id": "sbxrun_active_workspace_b",
                "workspace_id": WORKSPACE_ID_B,
                "command": "true",
                "quota_bytes": MIB,
            },
        )

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "sandbox_busy"
    assert unaffected.status_code == 200
    assert runtime.workspace_files.maintenance.state(
        WORKSPACE_ID,
    ).quiescing is True


def test_both_workspace_and_runtime_quota_must_verify_before_unquiesce(
    tmp_path,
):
    _normal_token, admin_token, runtime = _with_admin_runtime(tmp_path)
    manager = runtime.quota_manager
    original_inspect = manager.inspect

    def reject_runtime(**payload):
        result = original_inspect(**payload)
        if payload["scope"] == "runtime":
            result["quota_bytes_matches"] = False
            result["verified"] = False
        return result

    manager.inspect = reject_runtime
    body = {
        "request_id": "quota-verify-both-roots-1",
        "workspace_id": WORKSPACE_ID,
        "project_id": 10000,
        "quota_bytes": 64 * MIB,
        "runtime_project_id": 10001,
        "runtime_quota_bytes": 32 * MIB,
        "generation": 4,
    }
    with TestClient(create_app(runtime)) as client:
        failed = client.post(
            "/v1/admin/workspaces/quota/apply",
            headers={
                "Authorization": f"Bearer {admin_token}",
                "X-Nanobot-Request-ID": body["request_id"],
            },
            json=body,
        )
        state_after_failure = runtime.workspace_files.maintenance.state(
            WORKSPACE_ID,
        )
        manager.inspect = original_inspect
        body["request_id"] = "quota-verify-both-roots-2"
        succeeded = client.post(
            "/v1/admin/workspaces/quota/apply",
            headers={
                "Authorization": f"Bearer {admin_token}",
                "X-Nanobot-Request-ID": body["request_id"],
            },
            json=body,
        )
        state_after_success = runtime.workspace_files.maintenance.state(
            WORKSPACE_ID,
        )

    assert failed.status_code == 503
    assert state_after_failure.quiescing is True
    assert state_after_failure.applied_generation == 0
    assert succeeded.status_code == 200
    assert succeeded.json()["data"]["quota_verified"] is True
    assert state_after_success.quiescing is False
    assert state_after_success.applied_generation == 4


def _active_developer_lease(db, *, workspace_id: str, grant_id: str):
    grant = db.get(SandboxAccessGrant, grant_id)
    assert grant is not None
    lease = SandboxLease(
        lease_id=f"sbxlease_quota_{uuid4().hex}",
        lease_key=uuid4().hex * 2,
        grant_id=grant.id,
        chat_stream_id=grant.chat_stream_id,
        workspace_id=workspace_id,
        profile_id="developer",
        catalog_generation="20260725.1",
        policy_sha256="b" * 64,
        status="active",
        image_digest=IMAGE_ID,
        controller_epoch="sbxctl_" + "7" * 32,
    )
    db.add(lease)
    db.commit()
    return lease


def _prepare_developer_workspace(db_session, *, suffix: str):
    access = _enqueue_access(
        db_session,
        request_id=f"quiesce-bootstrap-{suffix}",
        session_id=f"private_quiesce-{suffix}",
        capability="exec",
        quota_bytes=64 * MIB,
    )
    db_session.commit()
    SandboxAdminOperationRunner(
        _factory(db_session),
        backend_factory=_QuotaBackend,
    ).run_once()
    db_session.expire_all()
    grant = db_session.query(SandboxAccessGrant).one()
    grant.execution_profile = "developer"
    grant.version = int(grant.version) + 1
    db_session.commit()
    lease = _active_developer_lease(
        db_session,
        workspace_id=access.operation.workspace_id,
        grant_id=grant.id,
    )
    return access.operation.workspace_id, grant, lease


def test_grant_change_during_quota_window_does_not_rebuild_old_lease(
    db_session,
):
    workspace_id, grant, lease = _prepare_developer_workspace(
        db_session,
        suffix="grant-change",
    )
    operation = SandboxAdminService(db_session).enqueue_quota_change(
        request_id="quiesce-quota-grant-change",
        workspace_id=workspace_id,
        quota_bytes=96 * MIB,
        reason="测试维护期间撤换 Profile",
        actor="admin-test",
    ).operation
    db_session.commit()
    factory = _factory(db_session)

    class ChangingBackend(_QuotaBackend):
        def apply_workspace_quota(self, payload):
            response = super().apply_workspace_quota(payload)
            changing_db = factory()
            try:
                current = changing_db.get(SandboxAccessGrant, grant.id)
                current.execution_profile = "restricted"
                current.version = int(current.version) + 1
                changing_db.commit()
            finally:
                changing_db.close()
            return response

    lease_backend = FakeSandboxBackend()
    runner = SandboxAdminOperationRunner(
        factory,
        backend_factory=ChangingBackend,
        lease_backend_factory=lambda: lease_backend,
    )

    assert runner.run_once() is True

    db_session.expire_all()
    assert db_session.get(SandboxLease, lease.lease_id).status == "stopped"
    assert db_session.get(
        SandboxLease,
        lease.lease_id,
    ).last_error_code == "quota_reconfigured"
    assert db_session.get(
        WorkspaceMaintenanceState,
        workspace_id,
    ).status == "ready"
    assert db_session.get(
        type(operation),
        operation.operation_id,
    ).status == "succeeded"
    assert not [
        call
        for call in lease_backend.calls
        if call[0] == "ensure_lease"
    ]


def test_server_rejects_access_decision_captured_before_workspace_quiesce(
    db_session,
    monkeypatch,
    request,
    tmp_path,
):
    workspace_id, grant, _lease = _prepare_developer_workspace(
        db_session,
        suffix="stale-access",
    )
    runtime_binding = db_session.get(
        WorkspaceRuntimeQuotaBinding,
        workspace_id,
    )
    runtime_binding.desired_quota_bytes = 10 * 1024 * MIB
    runtime_binding.applied_quota_bytes = 10 * 1024 * MIB
    runtime_binding.status = "applied"
    db_session.add_all([
        SystemSetting(key="sandbox.enabled", value="true"),
        SystemSetting(key="sandbox.exec_enabled", value="true"),
    ])
    db_session.commit()
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_INFRASTRUCTURE_ENABLE_ALLOWED",
        "true",
    )
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_SESSION_EXECUTION_ALLOWED",
        "true",
    )
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_DEVELOPER_NETWORK_ALLOWED",
        "true",
    )
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_PROFILE_MANIFEST_FILE",
        str(_manifest(tmp_path)),
    )
    settings.invalidate()
    request.addfinalizer(settings.invalidate)
    access = SandboxAccessPolicy(db_session).evaluate(
        "sandbox_exec",
        platform=grant.platform,
        chat_type=grant.chat_type,
        session_id=grant.external_session_id,
    )
    assert access.allowed is True
    SandboxAdminService(db_session).enqueue_quota_change(
        request_id="quiesce-stale-access-decision",
        workspace_id=workspace_id,
        quota_bytes=96 * MIB,
        reason="制造 quiescing 窗口",
        actor="admin-test",
    )
    db_session.commit()
    backend = _LeaseServiceBackend(
        load_execution_profile_registry(),
    )

    with pytest.raises(SandboxServiceError) as blocked:
        SandboxLeaseService(
            db_session,
            backend,
        ).ensure_for_access(access)

    assert blocked.value.code is SandboxErrorCode.AUTHORIZATION_FAILED
    assert backend.calls == []


def test_unchanged_latest_facts_rebuild_lease_after_both_quotas_verify(
    db_session,
    monkeypatch,
    request,
    tmp_path,
):
    workspace_id, grant, old_lease = _prepare_developer_workspace(
        db_session,
        suffix="rebuild",
    )
    db_session.add_all([
        SystemSetting(key="sandbox.enabled", value="true"),
        SystemSetting(key="sandbox.exec_enabled", value="true"),
    ])
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_INFRASTRUCTURE_ENABLE_ALLOWED",
        "true",
    )
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_SESSION_EXECUTION_ALLOWED",
        "true",
    )
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_DEVELOPER_NETWORK_ALLOWED",
        "true",
    )
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_PROFILE_MANIFEST_FILE",
        str(_manifest(tmp_path)),
    )
    settings.invalidate()
    request.addfinalizer(settings.invalidate)
    operation = SandboxAdminService(db_session).enqueue_quota_change(
        request_id="quiesce-quota-rebuild",
        workspace_id=workspace_id,
        quota_bytes=96 * MIB,
        reason="测试条件重建",
        actor="admin-test",
    ).operation
    db_session.commit()
    registry = load_execution_profile_registry()
    lease_backend = _LeaseServiceBackend(registry)
    runner = SandboxAdminOperationRunner(
        _factory(db_session),
        backend_factory=_QuotaBackend,
        lease_backend_factory=lambda: lease_backend,
    )

    assert runner.run_once() is True

    db_session.expire_all()
    current = (
        db_session.query(SandboxLease)
        .filter(
            SandboxLease.workspace_id == workspace_id,
            SandboxLease.status.in_(
                {"provisioning", "active", "idle", "stopping"},
            ),
        )
        .one()
    )
    state = db_session.get(WorkspaceMaintenanceState, workspace_id)
    runtime_binding = db_session.get(
        WorkspaceRuntimeQuotaBinding,
        workspace_id,
    )
    assert current.lease_id != old_lease.lease_id
    assert current.profile_id == "developer"
    assert state.status == "ready"
    assert state.applied_quota_generation == operation.expected_quota_generation
    assert runtime_binding.status == "applied"
    assert runtime_binding.applied_quota_bytes == 10 * 1024 * MIB
    assert [
        call
        for call in lease_backend.calls
        if call[0] == "ensure_lease"
    ]

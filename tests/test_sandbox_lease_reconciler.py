from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from core.chat_stream_identity import resolve_chat_stream_identity
from core.database import (
    SandboxAccessGrant,
    SandboxControllerState,
    SandboxLease,
    SandboxRun,
    Workspace,
)
from core.sandbox.access_contracts import (
    SandboxAccessDecision,
    SandboxCapability,
)
from core.sandbox.backend import FakeSandboxBackend
from core.sandbox.contracts import (
    SandboxErrorCode,
    SandboxServiceError,
)
from core.sandbox.environment_service import SandboxEnvironmentService
from core.sandbox.execution_profiles import load_execution_profile_registry
from core.sandbox.lease_reconciler import SandboxLeaseReconciler
from core.sandbox.lease_service import SandboxLeaseService
from tests.test_sandboxd_lease_backend import IMAGE_ID, _manifest


def _parents(db_session, *, suffix: str):
    workspace = Workspace(
        id=str(uuid4()),
        platform="qq",
        owner_type="user",
        owner_id=f"lease-reconcile-{suffix}",
        name="default",
        status="active",
        quota_bytes=50 * 1024 * 1024 * 1024,
        used_bytes=0,
    )
    grant = SandboxAccessGrant(
        id=str(uuid4()),
        chat_stream_id=f"qq:lease-reconcile-{suffix}:private",
        platform="qq",
        chat_type="private",
        external_session_id=f"lease-reconcile-{suffix}",
        workspace_id=workspace.id,
        capability_level="exec",
        execution_profile="developer",
        status="active",
        version=1,
    )
    db_session.add(workspace)
    db_session.flush()
    db_session.add(grant)
    db_session.commit()
    return workspace, grant


def _lease(
    workspace,
    grant,
    *,
    lease_id: str,
    status: str = "active",
    controller_epoch: str = "sbxctl_" + "1" * 32,
):
    return SandboxLease(
        lease_id=lease_id,
        lease_key=("a" * 63) + lease_id[-1],
        grant_id=grant.id,
        chat_stream_id=grant.chat_stream_id,
        workspace_id=workspace.id,
        profile_id="developer",
        catalog_generation="20260725.1",
        policy_sha256="b" * 64,
        status=status,
        image_digest=IMAGE_ID,
        controller_epoch=controller_epoch,
    )


def _response(data):
    return {
        "status": "success",
        "summary": "ok",
        "next_actions": [],
        "artifacts": [],
        "data": data,
    }


def _backend(
    *,
    epoch: str,
    recovered=(),
    recycled=(),
    leases=(),
    processes=(),
):
    backend = FakeSandboxBackend()
    backend.set_response(
        "controller_state",
        _response({
            "controller_epoch": epoch,
            "recovered_lease_ids": list(recovered),
            "recovered_process_ids": [],
            "lease_count": len(tuple(leases)),
        }),
    )
    backend.set_response(
        "reconcile_leases",
        _response({
            "controller_epoch": epoch,
            "inspected": len(tuple(leases)),
            "recycled": list(recycled),
            "failed_lease_ids": [],
        }),
    )
    backend.set_response(
        "list_leases",
        _response({"leases": list(leases)}),
    )
    backend.set_response(
        "list_processes",
        _response({
            "controller_epoch": epoch,
            "processes": list(processes),
        }),
    )
    return backend


def _factory(db_session):
    return sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        expire_on_commit=False,
    )


def test_epoch_change_finishes_lease_and_all_active_runs_idempotently(
    db_session,
):
    workspace, grant = _parents(db_session, suffix="epoch")
    lease_id = "sbxlease_server_epoch"
    lease = _lease(workspace, grant, lease_id=lease_id)
    run = SandboxRun(
        run_id="sbxrun_server_epoch",
        request_id="sbxreq_server_epoch",
        workspace_id=workspace.id,
        lease_id=lease_id,
        profile_id="developer",
        execution_mode="lease",
        process_state="running",
        image_digest=IMAGE_ID,
        status="running",
    )
    db_session.add(lease)
    db_session.add(run)
    db_session.commit()
    new_epoch = "sbxctl_" + "2" * 32
    backend = _backend(
        epoch=new_epoch,
        recovered=(lease_id,),
    )
    reconciler = SandboxLeaseReconciler(
        _factory(db_session),
        backend_factory=lambda: backend,
    )

    assert reconciler.run_once() is True
    assert reconciler.run_once() is True

    db_session.expire_all()
    settled_lease = db_session.get(SandboxLease, lease_id)
    settled_run = db_session.get(SandboxRun, run.run_id)
    state = db_session.get(SandboxControllerState, "sandboxd")
    assert settled_lease.status == "failed"
    assert settled_lease.last_error_code == "controller_restarted"
    assert settled_run.status == "failed"
    assert settled_run.process_state == "lost"
    assert settled_run.termination_reason == "controller_restarted"
    assert state.controller_epoch == new_epoch
    assert state.leader_owner == ""
    assert state.leader_token == ""


def test_run_once_projects_reconciled_workspace_usage(db_session):
    workspace, _grant = _parents(db_session, suffix="usage")
    dirty_workspace, _dirty_grant = _parents(db_session, suffix="usage-dirty")
    backend = _backend(epoch="sbxctl_" + "3" * 32)
    backend.set_response(
        "workspace_usage",
        _response({
            "workspaces": [
                {
                    "workspace_id": workspace.id,
                    "workspace_bytes": 12345,
                    "runtime_bytes": 6789,
                    "dirty": False,
                },
                {
                    "workspace_id": dirty_workspace.id,
                    "workspace_bytes": 999,
                    "runtime_bytes": 0,
                    "dirty": True,
                },
            ],
        }),
    )
    reconciler = SandboxLeaseReconciler(
        _factory(db_session),
        backend_factory=lambda: backend,
    )

    assert reconciler.run_once() is True

    db_session.expire_all()
    assert db_session.get(Workspace, workspace.id).used_bytes == 12345
    assert db_session.get(Workspace, dirty_workspace.id).used_bytes == 0


def test_controller_unavailable_preserves_running_ledger_and_records_error(
    db_session,
):
    workspace, grant = _parents(db_session, suffix="unavailable")
    lease = _lease(
        workspace,
        grant,
        lease_id="sbxlease_server_unavailable",
    )
    db_session.add(lease)
    db_session.commit()

    class UnavailableBackend(FakeSandboxBackend):
        def controller_state(self):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "controller unavailable",
                retryable=True,
                stop=False,
            )

    reconciler = SandboxLeaseReconciler(
        _factory(db_session),
        backend_factory=UnavailableBackend,
    )

    assert reconciler.run_once() is False

    db_session.expire_all()
    preserved = db_session.get(SandboxLease, lease.lease_id)
    state = db_session.get(SandboxControllerState, "sandboxd")
    assert preserved.status == "active"
    assert preserved.last_error_code == ""
    assert state.last_error_code == "runtime_unavailable"
    assert state.leader_token == ""


def test_current_controller_fact_activates_provisioning_lease(db_session):
    workspace, grant = _parents(db_session, suffix="active")
    lease = _lease(
        workspace,
        grant,
        lease_id="sbxlease_server_active",
        status="provisioning",
        controller_epoch="",
    )
    db_session.add(lease)
    db_session.commit()
    epoch = "sbxctl_" + "3" * 32
    fact = {
        "lease_id": lease.lease_id,
        "workspace_id": workspace.id,
        "profile_id": "developer",
        "catalog_generation": lease.catalog_generation,
        "policy_sha256": lease.policy_sha256,
        "image_digest": IMAGE_ID,
        "controller_epoch": epoch,
        "status": "idle",
        "present": True,
        "running": True,
        "created_at_unix": 1_785_000_000,
        "last_active_at_unix": 1_785_000_000,
        "idle_expires_at_unix": 1_785_001_800,
        "max_expires_at_unix": 1_785_028_800,
        "active_process_ids": [],
    }
    backend = _backend(epoch=epoch, leases=(fact,))
    reconciler = SandboxLeaseReconciler(
        _factory(db_session),
        backend_factory=lambda: backend,
    )

    assert reconciler.run_once() is True

    db_session.expire_all()
    active = db_session.get(SandboxLease, lease.lease_id)
    assert active.status == "idle"
    assert active.controller_epoch == epoch
    assert active.image_digest == IMAGE_ID
    assert active.reconciled_at is not None
    assert active.idle_expires_at is not None
    assert active.max_expires_at is not None


def test_database_leader_fencing_allows_only_one_reconciler_claim(
    db_session,
):
    factory = _factory(db_session)
    first = SandboxLeaseReconciler(factory, worker_id="worker-first")
    second = SandboxLeaseReconciler(factory, worker_id="worker-second")

    first_claim = first._claim()
    second_claim = second._claim()

    assert first_claim is not None
    assert second_claim is None


class _LeaseServiceBackend(FakeSandboxBackend):
    def __init__(self, registry):
        super().__init__()
        self.registry = registry
        self.controller_epoch = "sbxctl_" + "4" * 32
        self.environment_overrides: dict[str, object] = {}

    def ready(self):
        descriptor = self.registry.descriptors["developer"]
        return _response({
            "controller_epoch": self.controller_epoch,
            "catalog_generation": self.registry.catalog_generation,
            "policy_sha256": self.registry.policy_sha256,
            "profiles": {
                "developer": {
                    "ready": True,
                    "execution_mode": "lease",
                    "image_id": IMAGE_ID,
                    "apparmor_profile": (
                        "nanobot-sandbox-developer"
                    ),
                    "network_policy_id": (
                        descriptor.network_policy_id
                    ),
                    "network_proxy_image_id": (
                        descriptor.network_proxy_image_allowlist[0]
                    ),
                },
            },
        })

    def ensure_lease(self, payload):
        self.calls.append(("ensure_lease", dict(payload)))
        template = SandboxEnvironmentService().template("developer")
        return _response({
            **dict(payload),
            "image_digest": IMAGE_ID,
            "controller_epoch": self.controller_epoch,
            "status": "idle",
            "present": True,
            "running": True,
            "created_at_unix": 1_785_000_000,
            "last_active_at_unix": 1_785_000_000,
            "idle_expires_at_unix": 1_785_001_800,
            "max_expires_at_unix": 1_785_028_800,
            "active_process_ids": [],
            "environment": {
                "ready": True,
                "action": "unchanged",
                "profile_id": "developer",
                "catalog_generation": self.registry.catalog_generation,
                "policy_sha256": self.registry.policy_sha256,
                "image_digest": IMAGE_ID,
                "setup_definition_sha256": (
                    template.setup_definition_sha256
                ),
                "maintenance_definition_sha256": (
                    template.maintenance_definition_sha256
                ),
                "selected_lockfile_hashes": {},
                "last_setup_at": "2026-07-25T00:00:00Z",
                "last_maintenance_at": None,
                **self.environment_overrides,
            },
        })

    def sync_lease_assets(self, lease_id, payload):
        self.calls.append((
            "sync_lease_assets",
            {"lease_id": lease_id, **dict(payload)},
        ))
        return _response({
            "lease_id": lease_id,
            "asset_count": len(payload.get("assets") or []),
            "staged": True,
            "changed": True,
        })


def test_lease_service_maps_canonical_session_and_stages_assets_by_lease(
    db_session,
    tmp_path,
):
    workspace, grant = _parents(db_session, suffix="service")
    registry = load_execution_profile_registry(_manifest(tmp_path))
    backend = _LeaseServiceBackend(registry)
    identity = resolve_chat_stream_identity(
        platform="qq",
        chat_type="private",
        session_id=grant.external_session_id,
    )
    access = SandboxAccessDecision(
        allowed=True,
        code="ok",
        reason="allowed",
        required_capability=SandboxCapability.EXEC,
        granted_capability=SandboxCapability.EXEC,
        identity=identity,
        grant_id=grant.id,
        workspace_id=workspace.id,
        quota_bytes=workspace.quota_bytes,
        execution_profile="developer",
    )
    service = SandboxLeaseService(
        db_session,
        backend,
        profile_registry=registry,
    )
    assets = [{
        "sha256": "c" * 64,
        "storage_key": "blobs/cc/" + "c" * 64,
        "logical_name": "input.txt",
    }]

    first = service.ensure_for_access(
        access,
        authorized_assets=assets,
    )
    second = service.ensure_for_access(
        access,
        authorized_assets=assets,
    )

    assert first.lease_id == second.lease_id
    assert (
        db_session.query(SandboxLease)
        .filter(
            SandboxLease.lease_key == first.lease_key,
            SandboxLease.status.in_(
                {"provisioning", "active", "idle", "stopping"}
            ),
        )
        .count()
        == 1
    )
    stage_calls = [
        payload
        for operation, payload in backend.calls
        if operation == "sync_lease_assets"
    ]
    assert len(stage_calls) == 2
    assert {item["lease_id"] for item in stage_calls} == {
        first.lease_id
    }
    assert all("run_id" not in item for item in stage_calls)
    assert stage_calls[0]["assets"] == assets


def test_lease_service_rejects_environment_definition_drift(
    db_session,
    tmp_path,
):
    workspace, grant = _parents(db_session, suffix="environment-drift")
    registry = load_execution_profile_registry(_manifest(tmp_path))
    backend = _LeaseServiceBackend(registry)
    backend.environment_overrides = {
        "setup_definition_sha256": "f" * 64,
    }
    identity = resolve_chat_stream_identity(
        platform="qq",
        chat_type="private",
        session_id=grant.external_session_id,
    )
    access = SandboxAccessDecision(
        allowed=True,
        code="ok",
        reason="allowed",
        required_capability=SandboxCapability.EXEC,
        granted_capability=SandboxCapability.EXEC,
        identity=identity,
        grant_id=grant.id,
        workspace_id=workspace.id,
        quota_bytes=workspace.quota_bytes,
        execution_profile="developer",
    )

    with pytest.raises(SandboxServiceError) as drift:
        SandboxLeaseService(
            db_session,
            backend,
            profile_registry=registry,
        ).ensure_for_access(access)

    assert drift.value.code is SandboxErrorCode.RUNTIME_UNAVAILABLE
    assert any(
        operation == "stop_lease"
        for operation, _payload in backend.calls
    )
    assert not any(
        operation == "sync_lease_assets"
        for operation, _payload in backend.calls
    )
    lease = db_session.query(SandboxLease).one()
    assert lease.status == "failed"


def test_lease_service_replaces_old_epoch_lease_and_finishes_its_runs(
    db_session,
    tmp_path,
):
    workspace, grant = _parents(db_session, suffix="service-restart")
    registry = load_execution_profile_registry(_manifest(tmp_path))
    backend = _LeaseServiceBackend(registry)
    identity = resolve_chat_stream_identity(
        platform="qq",
        chat_type="private",
        session_id=grant.external_session_id,
    )
    access = SandboxAccessDecision(
        allowed=True,
        code="ok",
        reason="allowed",
        required_capability=SandboxCapability.EXEC,
        granted_capability=SandboxCapability.EXEC,
        identity=identity,
        grant_id=grant.id,
        workspace_id=workspace.id,
        quota_bytes=workspace.quota_bytes,
        execution_profile="developer",
    )
    service = SandboxLeaseService(
        db_session,
        backend,
        profile_registry=registry,
    )
    first = service.ensure_for_access(access)
    first_id = first.lease_id
    db_session.add(SandboxRun(
        run_id="sbxrun_service_restart",
        request_id="sbxreq_service_restart",
        workspace_id=workspace.id,
        lease_id=first_id,
        profile_id="developer",
        execution_mode="lease",
        process_state="running",
        image_digest=IMAGE_ID,
        status="running",
    ))
    db_session.commit()

    backend.controller_epoch = "sbxctl_" + "5" * 32
    second = service.ensure_for_access(access)

    assert second.lease_id != first_id
    assert second.controller_epoch == backend.controller_epoch
    db_session.expire_all()
    old_lease = db_session.get(SandboxLease, first_id)
    old_run = db_session.get(SandboxRun, "sbxrun_service_restart")
    assert old_lease.status == "failed"
    assert old_lease.last_error_code == "controller_restarted"
    assert old_run.status == "failed"
    assert old_run.process_state == "lost"
    assert old_run.termination_reason == "controller_restarted"


def test_lease_service_rejects_identity_inconsistent_with_canonical_key(
    db_session,
    tmp_path,
):
    workspace, grant = _parents(db_session, suffix="identity-mismatch")
    registry = load_execution_profile_registry(_manifest(tmp_path))
    backend = _LeaseServiceBackend(registry)
    identity = resolve_chat_stream_identity(
        platform="qq",
        chat_type="private",
        session_id=grant.external_session_id,
    )
    access = SandboxAccessDecision(
        allowed=True,
        code="ok",
        reason="allowed",
        required_capability=SandboxCapability.EXEC,
        granted_capability=SandboxCapability.EXEC,
        identity=replace(
            identity,
            external_session_id="other-session",
        ),
        grant_id=grant.id,
        workspace_id=workspace.id,
        quota_bytes=workspace.quota_bytes,
        execution_profile="developer",
    )

    with pytest.raises(SandboxServiceError) as rejected:
        SandboxLeaseService(
            db_session,
            backend,
            profile_registry=registry,
        ).ensure_for_access(access)

    assert rejected.value.code is SandboxErrorCode.AUTHORIZATION_FAILED


@pytest.mark.parametrize(
    "invalid_case",
    [
        "controller_epoch",
        "recovered_lease_ids",
        "active_process_ids",
    ],
)
def test_server_reconciler_rejects_malformed_controller_facts(
    db_session,
    invalid_case,
):
    workspace, grant = _parents(
        db_session,
        suffix=f"invalid-{invalid_case}",
    )
    lease = _lease(
        workspace,
        grant,
        lease_id=f"sbxlease_invalid_{invalid_case}",
    )
    db_session.add(lease)
    db_session.commit()
    epoch = "sbxctl_" + "6" * 32
    fact = {
        "lease_id": lease.lease_id,
        "workspace_id": workspace.id,
        "profile_id": "developer",
        "catalog_generation": lease.catalog_generation,
        "policy_sha256": lease.policy_sha256,
        "image_digest": IMAGE_ID,
        "controller_epoch": epoch,
        "status": "idle",
        "present": True,
        "running": True,
        "created_at_unix": 1_785_000_000,
        "last_active_at_unix": 1_785_000_000,
        "idle_expires_at_unix": 1_785_001_800,
        "max_expires_at_unix": 1_785_028_800,
        "active_process_ids": [],
    }
    backend = _backend(epoch=epoch, leases=(fact,))
    if invalid_case == "controller_epoch":
        backend.set_response(
            "controller_state",
            _response({
                "controller_epoch": "sbxctl_invalid",
                "recovered_lease_ids": [],
                "recovered_process_ids": [],
                "lease_count": 1,
            }),
        )
    elif invalid_case == "recovered_lease_ids":
        backend.set_response(
            "controller_state",
            _response({
                "controller_epoch": epoch,
                "recovered_lease_ids": lease.lease_id,
                "recovered_process_ids": [],
                "lease_count": 1,
            }),
        )
    else:
        fact["active_process_ids"] = "sbxrun_invalid_processes"
        backend.set_response(
            "list_leases",
            _response({"leases": [fact]}),
        )
    reconciler = SandboxLeaseReconciler(
        _factory(db_session),
        backend_factory=lambda: backend,
    )

    assert reconciler.run_once() is False

    db_session.expire_all()
    preserved = db_session.get(SandboxLease, lease.lease_id)
    state = db_session.get(SandboxControllerState, "sandboxd")
    assert preserved.status == "active"
    assert state.last_error_code == "runtime_unavailable"


def test_sandboxd_lease_modules_have_no_server_database_reverse_channel():
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "sandboxd/lease_store.py",
        "sandboxd/lease_backend.py",
        "sandboxd/lease_reconciler.py",
        "sandboxd/process_manager.py",
        "sandboxd/process_output.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "core.database" not in source
        assert "SandboxLease" not in source
        assert "SandboxRun" not in source

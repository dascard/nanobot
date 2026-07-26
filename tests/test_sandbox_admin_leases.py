from __future__ import annotations

from datetime import datetime

import pytest

from api.admin import sandbox_routes
from core.database import (
    SandboxAccessGrant,
    SandboxAdminOperation,
    SandboxLease,
    SandboxRun,
    Workspace,
)
from core.sandbox.contracts import success_result
from tests.test_admin_sandbox import (
    IMAGE_ID,
    WORKSPACE_ID,
    _business_rows,
    _client,
    _settings,
)


LEASE_ID = "sbxlease_admin_management"
CONTROLLER_EPOCH = "sbxctl_" + "4" * 32
POLICY_SHA256 = "c" * 64


class _AdminLeaseBackend:
    def __init__(self) -> None:
        self.closed = False
        self.calls: list[tuple[str, str, str]] = []

    def list_leases(self):
        return success_result(
            "Lease 列表",
            data={
                "leases": [{
                    "lease_id": LEASE_ID,
                    "workspace_id": WORKSPACE_ID,
                    "profile_id": "developer",
                    "catalog_generation": "developer-v1",
                    "policy_sha256": POLICY_SHA256,
                    "image_digest": IMAGE_ID,
                    "controller_epoch": CONTROLLER_EPOCH,
                    "quota_generation": 3,
                    "status": "active",
                    "present": True,
                    "running": True,
                    "created_at_unix": 1_785_000_000,
                    "last_active_at_unix": 1_785_000_100,
                    "idle_expires_at_unix": 1_785_001_800,
                    "max_expires_at_unix": 1_785_028_800,
                    "active_process_ids": [
                        "sbxrun_admin_lease_running",
                    ],
                    "command": "不得返回的命令",
                    "stdout": "不得返回的输出",
                    "host_path": "/srv/nanobot/不得返回",
                }],
            },
        )

    def stop_lease(self, lease_id, *, request_id):
        self.calls.append(("stop", lease_id, request_id))
        return success_result(
            "已停止",
            data={
                "lease_id": lease_id,
                "termination_scope": "lease",
                "lease_recycled": True,
                "termination_reason": "admin_lease_stop",
                "affected_process_ids": [
                    "sbxrun_admin_lease_running",
                ],
                "workspace_preserved": True,
                "runtime_preserved": True,
            },
        )

    def destroy_lease(self, lease_id, *, request_id):
        self.calls.append(("destroy", lease_id, request_id))
        return success_result(
            "已销毁",
            data={
                "lease_id": lease_id,
                "termination_scope": "lease",
                "lease_recycled": True,
                "termination_reason": "admin_lease_destroy",
                "affected_process_ids": [
                    "sbxrun_admin_lease_running",
                ],
                "workspace_preserved": True,
                "runtime_preserved": True,
            },
        )

    def recreate_lease(self, lease_id, *, request_id):
        self.calls.append(("recreate", lease_id, request_id))
        return success_result(
            "已重建",
            data={
                "lease_id": lease_id,
                "workspace_id": WORKSPACE_ID,
                "profile_id": "developer",
                "catalog_generation": "developer-v1",
                "policy_sha256": POLICY_SHA256,
                "image_digest": IMAGE_ID,
                "controller_epoch": CONTROLLER_EPOCH,
                "quota_generation": 3,
                "status": "idle",
                "present": True,
                "running": True,
                "created_at_unix": 1_785_000_200,
                "last_active_at_unix": 1_785_000_200,
                "idle_expires_at_unix": 1_785_002_000,
                "max_expires_at_unix": 1_785_029_000,
                "active_process_ids": [],
                "environment": {
                    "ready": True,
                    "action": "maintenance",
                },
            },
        )

    def close(self):
        self.closed = True


def _lease_rows(db_session):
    grant = SandboxAccessGrant(
        id="00000000-0000-0000-0000-000000000010",
        chat_stream_id="qq:private-admin-lease:private",
        platform="qq",
        chat_type="private",
        external_session_id="admin-lease",
        workspace_id=WORKSPACE_ID,
        capability_level="exec",
        execution_profile="developer",
        status="active",
        version=3,
    )
    db_session.add(grant)
    db_session.flush()
    lease = SandboxLease(
        lease_id=LEASE_ID,
        lease_key="d" * 64,
        grant_id=grant.id,
        chat_stream_id=grant.chat_stream_id,
        workspace_id=WORKSPACE_ID,
        profile_id="developer",
        catalog_generation="developer-v1",
        policy_sha256=POLICY_SHA256,
        status="active",
        image_digest=IMAGE_ID,
        controller_epoch=CONTROLLER_EPOCH,
        created_at=datetime(2026, 7, 25, 8, 0, 0),
        last_active_at=datetime(2026, 7, 25, 8, 1, 0),
        idle_expires_at=datetime(2026, 7, 25, 8, 30, 0),
        max_expires_at=datetime(2026, 7, 25, 16, 0, 0),
    )
    db_session.add(lease)
    db_session.flush()
    db_session.add(SandboxRun(
        run_id="sbxrun_admin_lease_running",
        request_id="sbxreq_admin_lease_running",
        workspace_id=WORKSPACE_ID,
        lease_id=LEASE_ID,
        profile_id="developer",
        execution_mode="lease",
        process_state="running",
        image_digest=IMAGE_ID,
        status="running",
    ))
    db_session.commit()
    return grant, lease


def _admin_client(db_session, monkeypatch):
    _settings(db_session)
    _business_rows(db_session)
    _lease_rows(db_session)
    backends: list[_AdminLeaseBackend] = []
    client = _client(db_session, monkeypatch, [])

    def factory(_db):
        backend = _AdminLeaseBackend()
        backends.append(backend)
        return backend

    monkeypatch.setattr(
        sandbox_routes,
        "_sandbox_admin_backend",
        factory,
    )
    return client, backends


def test_admin_lease_list_is_joined_with_safe_business_summary(
    db_session,
    monkeypatch,
):
    client, backends = _admin_client(db_session, monkeypatch)

    response = client.get("/api/v1/admin/sandbox/leases")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item == {
        "lease_id": LEASE_ID,
        "session_summary": "qq:private:7b3c48aa5235",
        "workspace_id": WORKSPACE_ID,
        "profile_id": "developer",
        "status": "active",
        "image_digest": IMAGE_ID,
        "catalog_generation": "developer-v1",
        "policy_sha256": POLICY_SHA256,
        "controller_epoch": CONTROLLER_EPOCH,
        "quota_generation": 3,
        "runtime_present": True,
        "runtime_running": True,
        "active_process_count": 1,
        "last_active_at": "2026-07-25T08:01:00",
        "idle_expires_at": "2026-07-25T08:30:00",
        "max_expires_at": "2026-07-25T16:00:00",
        "last_error_code": "",
        "last_error_summary": "",
    }
    assert "admin-lease" not in response.text
    assert "不得返回" not in response.text
    assert "/srv/nanobot" not in response.text
    assert backends[0].closed is True


@pytest.mark.parametrize(
    ("action", "expected_status", "expected_reason"),
    [
        ("stop", "stopped", "admin_lease_stop"),
        ("destroy", "destroyed", "admin_lease_destroy"),
    ],
)
def test_admin_stop_and_destroy_are_idempotent_and_preserve_data(
    db_session,
    monkeypatch,
    action,
    expected_status,
    expected_reason,
):
    client, backends = _admin_client(db_session, monkeypatch)
    body = {
        "request_id": f"admin-{action}-request-0001",
        "reason": f"{action} 演练",
    }

    first = client.post(
        f"/api/v1/admin/sandbox/leases/{LEASE_ID}/{action}",
        json=body,
    )
    repeated = client.post(
        f"/api/v1/admin/sandbox/leases/{LEASE_ID}/{action}",
        json=body,
    )

    assert first.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json()["replayed"] is True
    assert sum(len(backend.calls) for backend in backends) == 1
    db_session.expire_all()
    lease = db_session.get(SandboxLease, LEASE_ID)
    run = db_session.get(SandboxRun, "sbxrun_admin_lease_running")
    assert lease.status == expected_status
    assert run.status == "cancelled"
    assert run.process_state == "lost"
    assert run.termination_reason == expected_reason
    assert db_session.get(Workspace, WORKSPACE_ID) is not None
    operation = db_session.query(SandboxAdminOperation).filter_by(
        request_id=body["request_id"],
    ).one()
    assert operation.operation_type == f"lease_{action}"
    assert operation.status == "succeeded"


def test_admin_stop_of_already_recycled_lease_settles_idempotently(
    db_session,
    monkeypatch,
):
    class _GoneBackend(_AdminLeaseBackend):
        def stop_lease(self, lease_id, *, request_id):
            self.calls.append(("stop", lease_id, request_id))
            return success_result(
                "控制面上已无该 Lease",
                data={
                    "lease_id": lease_id,
                    "termination_scope": "lease",
                    "lease_recycled": False,
                    "termination_reason": "admin_lease_stop",
                    "affected_process_ids": [],
                    "workspace_preserved": True,
                    "runtime_preserved": True,
                },
            )

    _settings(db_session)
    _business_rows(db_session)
    _lease_rows(db_session)
    backends: list[_AdminLeaseBackend] = []
    client = _client(db_session, monkeypatch, [])

    def factory(_db):
        backend = _GoneBackend()
        backends.append(backend)
        return backend

    monkeypatch.setattr(
        sandbox_routes,
        "_sandbox_admin_backend",
        factory,
    )

    response = client.post(
        f"/api/v1/admin/sandbox/leases/{LEASE_ID}/stop",
        json={
            "request_id": "admin-stop-gone-request-01",
            "reason": "清理已回收 Lease",
        },
    )

    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(SandboxLease, LEASE_ID).status == "stopped"
    run = db_session.get(SandboxRun, "sbxrun_admin_lease_running")
    assert run.status == "cancelled"
    operation = db_session.query(SandboxAdminOperation).filter_by(
        request_id="admin-stop-gone-request-01",
    ).one()
    assert operation.status == "succeeded"


def test_admin_run_cancel_for_lease_process_stops_whole_lease(
    db_session,
    monkeypatch,
):
    client, backends = _admin_client(db_session, monkeypatch)

    response = client.post(
        "/api/v1/admin/sandbox/runs/sbxrun_admin_lease_running/cancel",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["termination_scope"] == "lease"
    assert payload["data"]["lease_id"] == LEASE_ID
    assert payload["data"]["status"] == "cancelled"
    assert [
        call[0]
        for backend in backends
        for call in backend.calls
    ] == ["stop"]
    db_session.expire_all()
    assert db_session.get(SandboxLease, LEASE_ID).status == "stopped"
    assert db_session.get(
        SandboxRun,
        "sbxrun_admin_lease_running",
    ).status == "cancelled"

    repeated = client.post(
        "/api/v1/admin/sandbox/runs/sbxrun_admin_lease_running/cancel",
    )

    assert repeated.status_code == 200
    assert repeated.json()["data"]["status"] == "cancelled"
    assert sum(len(backend.calls) for backend in backends) == 1


def test_admin_recreate_reprepares_environment_and_preserves_lease_identity(
    db_session,
    monkeypatch,
):
    client, backends = _admin_client(db_session, monkeypatch)

    response = client.post(
        f"/api/v1/admin/sandbox/leases/{LEASE_ID}/recreate",
        json={
            "request_id": "admin-recreate-request-0001",
            "reason": "环境重建演练",
        },
    )

    assert response.status_code == 200
    assert response.json()["lease"]["lease_id"] == LEASE_ID
    assert response.json()["lease"]["status"] == "idle"
    assert response.json()["environment_action"] == "maintenance"
    assert backends[0].calls == [(
        "recreate",
        LEASE_ID,
        "admin-recreate-request-0001",
    )]
    db_session.expire_all()
    lease = db_session.get(SandboxLease, LEASE_ID)
    run = db_session.get(SandboxRun, "sbxrun_admin_lease_running")
    assert lease.status == "idle"
    assert run.status == "cancelled"
    assert run.termination_reason == "admin_lease_recreate"
    assert db_session.get(Workspace, WORKSPACE_ID) is not None


def test_access_grant_profile_selection_updates_profile_and_run_projection(
    db_session,
    monkeypatch,
):
    _settings(db_session)
    _business_rows(db_session)
    client = _client(db_session, monkeypatch, [])

    created = client.post(
        "/api/v1/admin/sandbox/access-grants",
        json={
            "request_id": "profile-selection-request-0001",
            "session_id": "private_profile-selection",
            "capability": "exec",
            "execution_profile": "developer",
            "quota_bytes": 64 * 1024 * 1024,
        },
    )
    runs = client.get("/api/v1/admin/sandbox/runs")

    assert created.status_code == 202
    grant = db_session.query(SandboxAccessGrant).one()
    assert grant.execution_profile == "developer"
    listed_grant = client.get(
        "/api/v1/admin/sandbox/access-grants"
    ).json()["items"][0]
    assert listed_grant["execution_profile"] == "developer"
    assert runs.status_code == 200
    running = next(
        item
        for item in runs.json()["items"]
        if item["run_id"] == "sbxrun_running"
    )
    assert {
        "profile_id",
        "execution_mode",
        "lease_id",
        "process_state",
        "stdout_truncated",
        "stderr_truncated",
        "termination_reason",
    } <= set(running)

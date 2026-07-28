from __future__ import annotations

from api.admin import sandbox_routes
from core.database import (
    Asset,
    SandboxLease,
    SandboxRun,
    SystemSetting,
    Workspace,
)
from core.sandbox.contracts import success_result
from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from tests.test_admin_sandbox import (
    WORKSPACE_ID,
    _business_rows,
    _client,
    _settings,
)
from tests.test_sandbox_admin_leases import (
    LEASE_ID,
    _lease_rows,
)


class _KillSwitchAdminBackend:
    def __init__(self, *, failed: bool = False) -> None:
        self.failed = failed
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    def terminate_all_leases(self, *, request_id, reason):
        self.calls.append((request_id, reason))
        return success_result(
            "全量终止",
            data={
                "controller_epoch": "sbxctl_" + "4" * 32,
                "terminated_lease_ids": [] if self.failed else [LEASE_ID],
                "affected_process_ids": (
                    [] if self.failed else ["sbxrun_admin_lease_running"]
                ),
                "failed_lease_ids": [LEASE_ID] if self.failed else [],
            },
        )

    def close(self):
        self.closed = True


class _UnavailableKillSwitchAdminBackend:
    def terminate_all_leases(self, *, request_id, reason):
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "模拟 sandboxd 管理通道不可用",
            retryable=True,
            stop=False,
        )

    def close(self):
        return None


def _kill_client(db_session, monkeypatch, *, failed=False):
    _settings(db_session)
    _business_rows(db_session)
    _lease_rows(db_session)
    regular_backends = []
    admin_backends = []
    client = _client(db_session, monkeypatch, regular_backends)

    def admin_factory(_db):
        backend = _KillSwitchAdminBackend(failed=failed)
        admin_backends.append(backend)
        return backend

    monkeypatch.setattr(
        sandbox_routes,
        "_sandbox_admin_backend",
        admin_factory,
    )
    return client, regular_backends, admin_backends


def test_kill_switch_terminates_leases_and_oneshot_runs_then_settles_ledger(
    db_session,
    monkeypatch,
):
    client, regular_backends, admin_backends = _kill_client(
        db_session,
        monkeypatch,
    )
    body = {
        "request_id": "kill-switch-request-0001",
        "reason": "真实终止演练",
    }

    first = client.post(
        "/api/v1/admin/sandbox/kill-switch",
        json=body,
    )
    repeated = client.post(
        "/api/v1/admin/sandbox/kill-switch",
        json=body,
    )

    assert first.status_code == 200
    assert first.json() == {
        "ok": True,
        "replayed": False,
        "feature": {
            "enabled": False,
            "exec_enabled": False,
            "group_enabled": False,
        },
        "terminated_count": 3,
        "failed_count": 0,
        "terminated_lease_count": 1,
        "terminated_run_count": 2,
        "failed_lease_count": 0,
        "failed_run_count": 0,
        "data_preserved": True,
    }
    assert repeated.status_code == 200
    assert repeated.json()["replayed"] is True
    assert sum(len(item.calls) for item in admin_backends) == 1
    assert sum(len(item.cancelled) for item in regular_backends) == 1
    assert db_session.get(SystemSetting, "sandbox.enabled").value == "false"
    assert (
        db_session.get(SystemSetting, "sandbox.exec_enabled").value
        == "false"
    )
    assert (
        db_session.get(SystemSetting, "sandbox.group_enabled").value
        == "false"
    )
    db_session.expire_all()
    assert db_session.get(SandboxLease, LEASE_ID).status == "stopped"
    assert {
        row.status
        for row in db_session.query(SandboxRun).filter(
            SandboxRun.status == "cancelled"
        )
    } == {"cancelled"}
    assert db_session.query(SandboxRun).filter(
        SandboxRun.status.in_(("pending", "running"))
    ).count() == 0
    assert db_session.query(Workspace).count() == 1
    assert db_session.query(Asset).count() == 1


def test_kill_switch_failure_keeps_switches_off_and_does_not_fake_settlement(
    db_session,
    monkeypatch,
):
    client, _regular, _admin = _kill_client(
        db_session,
        monkeypatch,
        failed=True,
    )

    response = client.post(
        "/api/v1/admin/sandbox/kill-switch",
        json={
            "request_id": "kill-switch-request-failed",
            "reason": "模拟终止失败",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"]["failed_count"] >= 1
    assert db_session.get(SystemSetting, "sandbox.enabled").value == "false"
    assert (
        db_session.get(SystemSetting, "sandbox.exec_enabled").value
        == "false"
    )
    assert (
        db_session.get(SystemSetting, "sandbox.group_enabled").value
        == "false"
    )
    db_session.expire_all()
    assert db_session.get(SandboxLease, LEASE_ID).status == "active"
    assert (
        db_session.get(
            SandboxRun,
            "sbxrun_admin_lease_running",
        ).status
        == "running"
    )
    assert db_session.get(Workspace, WORKSPACE_ID) is not None


def test_kill_switch_cannot_claim_success_when_admin_channel_is_unreachable(
    db_session,
    monkeypatch,
):
    _settings(db_session)
    _business_rows(db_session)
    client = _client(db_session, monkeypatch, [])
    monkeypatch.setattr(
        sandbox_routes,
        "_sandbox_admin_backend",
        lambda _db: _UnavailableKillSwitchAdminBackend(),
    )

    response = client.post(
        "/api/v1/admin/sandbox/kill-switch",
        json={
            "request_id": "kill-switch-admin-unavailable",
            "reason": "模拟控制面失联",
        },
    )

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["failed_count"] == 1
    assert detail["failed_lease_count"] == 1
    assert detail["terminated_run_count"] == 1
    assert db_session.get(SystemSetting, "sandbox.enabled").value == "false"
    assert (
        db_session.get(SystemSetting, "sandbox.exec_enabled").value
        == "false"
    )

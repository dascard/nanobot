from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import admin_routes
from api.admin import sandbox_routes
from api.admin.common import verify_admin
from core.database import (
    AdminAuditLog,
    Asset,
    SandboxRun,
    SystemSetting,
    Workspace,
    WorkspaceAsset,
    get_db,
)
from core.sandbox.contracts import success_result
from core.sandbox.paths import SandboxStorageLayout


WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"
ASSET_SHA256 = "a" * 64
IMAGE_ID = "sha256:" + "b" * 64


class _FakeBackend:
    def __init__(self):
        self.closed = False
        self.cancelled = []

    def health(self):
        return success_result("健康", data={"service": "sandboxd"})

    def ready(self):
        return success_result(
            "就绪",
            data={
                "docker": True,
                "image_id": IMAGE_ID,
                "apparmor_profile": "nanobot-sandbox",
                "disk_used_percent": 21.5,
                "disk_free_bytes": 500 * 1024 * 1024 * 1024,
                "host_path": "/srv/nanobot/不得返回",
            },
        )

    def cancel_run(self, run_id, *, request_id):
        self.cancelled.append((run_id, request_id))
        return success_result(
            "已取消",
            data={
                "run_id": run_id,
                "workspace_id": WORKSPACE_ID,
                "status": "cancelling",
                "stdout": "不得返回的输出正文",
                "host_path": "/srv/nanobot/不得返回",
            },
        )

    def close(self):
        self.closed = True


def _settings(db_session):
    values = {
        "sandbox.enabled": "1",
        "sandbox.exec_enabled": "1",
        "sandbox.group_enabled": "0",
        "sandbox.disk_max_percent": "80",
        "sandbox.disk_min_free_bytes": str(50 * 1024 * 1024 * 1024),
    }
    for key, value in values.items():
        db_session.add(SystemSetting(key=key, value=value))


def _business_rows(db_session):
    workspace = Workspace(
        id=WORKSPACE_ID,
        platform="qq",
        owner_type="user",
        owner_id="10001",
        name="default",
        status="active",
        quota_bytes=2 * 1024 * 1024 * 1024,
        used_bytes=1234,
    )
    asset = Asset(
        sha256=ASSET_SHA256,
        size_bytes=4321,
        media_type="text/plain",
        storage_key=SandboxStorageLayout.asset_storage_key(ASSET_SHA256),
    )
    running = SandboxRun(
        run_id="sbxrun_running",
        request_id="sbxreq_running",
        workspace_id=WORKSPACE_ID,
        trace_id="trace-running",
        agent_run_id="agent-running",
        tool_call_id="tool-running",
        image_digest=IMAGE_ID,
        status="running",
        started_at=datetime(2026, 7, 20, 10, 0, 0),
    )
    failed = SandboxRun(
        run_id="sbxrun_failed",
        request_id="sbxreq_failed",
        workspace_id=WORKSPACE_ID,
        trace_id="trace-failed",
        agent_run_id="agent-failed",
        tool_call_id="tool-failed",
        image_digest=IMAGE_ID,
        status="failed",
        termination_reason="execution_timeout",
        finished_at=datetime(2026, 7, 20, 10, 1, 0),
    )
    # 这些模型没有声明 ORM relationship；先落外键父记录，避免 mapper 导入顺序
    # 改变时 SQLAlchemy 在同一次 flush 中先插入 sandbox_runs。
    db_session.add_all([workspace, asset])
    db_session.flush()
    db_session.add_all([running, failed])
    db_session.flush()
    db_session.add(WorkspaceAsset(
        workspace_id=WORKSPACE_ID,
        asset_sha256=ASSET_SHA256,
        logical_name="inputs/report.txt",
    ))
    db_session.commit()


def _client(db_session, monkeypatch, backends, *, auth_override=True):
    app = FastAPI()
    app.include_router(sandbox_routes.router, prefix="/api/v1/admin")

    def override_get_db():
        yield db_session

    def backend_factory(_db):
        backend = _FakeBackend()
        backends.append(backend)
        return backend

    app.dependency_overrides[get_db] = override_get_db
    if auth_override:
        app.dependency_overrides[verify_admin] = lambda: "admin"
    monkeypatch.setattr(sandbox_routes, "_sandbox_backend", backend_factory)
    return TestClient(app)


def test_admin_sandbox_status_reports_safe_health_usage_and_runs(
    db_session,
    monkeypatch,
):
    _settings(db_session)
    _business_rows(db_session)
    backends = []
    client = _client(db_session, monkeypatch, backends)

    response = client.get("/api/v1/admin/sandbox/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["feature"] == {
        "enabled": True,
        "exec_enabled": True,
        "group_enabled": False,
    }
    assert payload["controller"]["health"]["ok"] is True
    assert payload["controller"]["ready"]["disk_used_percent"] == 21.5
    assert payload["usage"] == {
        "workspace_count": 1,
        "workspace_used_bytes": 1234,
        "workspace_quota_bytes": 2 * 1024 * 1024 * 1024,
        "asset_count": 1,
        "asset_physical_bytes": 4321,
        "asset_link_count": 1,
    }
    assert [item["run_id"] for item in payload["current_runs"]] == [
        "sbxrun_running"
    ]
    assert [item["run_id"] for item in payload["recent_failures"]] == [
        "sbxrun_failed"
    ]
    serialized = response.text
    assert "/srv/nanobot" not in serialized
    assert "不得返回" not in serialized
    assert backends[0].closed is True


def test_admin_sandbox_run_list_and_cancel_are_sanitized_and_audited(
    db_session,
    monkeypatch,
):
    _settings(db_session)
    _business_rows(db_session)
    backends = []
    client = _client(db_session, monkeypatch, backends)

    listed = client.get(
        "/api/v1/admin/sandbox/runs",
        params={"status": "failed", "limit": 10},
    )
    cancelled = client.post(
        "/api/v1/admin/sandbox/runs/sbxrun_running/cancel"
    )

    assert listed.status_code == 200
    assert [item["run_id"] for item in listed.json()["items"]] == [
        "sbxrun_failed"
    ]
    assert cancelled.status_code == 200
    assert cancelled.json()["data"] == {
        "run_id": "sbxrun_running",
        "workspace_id": WORKSPACE_ID,
        "status": "cancelling",
    }
    assert "不得返回" not in cancelled.text
    assert "/srv/nanobot" not in cancelled.text
    assert backends[-1].cancelled == [
        ("sbxrun_running", "admin-cancel-sbxrun_running")
    ]
    audit = db_session.query(AdminAuditLog).filter_by(
        action="sandbox_cancel_run"
    ).one()
    assert audit.target_id == "sbxrun_running"


def test_admin_sandbox_kill_switch_only_disables_features_and_preserves_data(
    db_session,
    monkeypatch,
):
    _settings(db_session)
    _business_rows(db_session)
    backends = []
    client = _client(db_session, monkeypatch, backends)

    response = client.post(
        "/api/v1/admin/sandbox/kill-switch",
        json={"reason": "灰度回滚演练"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "feature": {"enabled": False, "exec_enabled": False},
        "active_run_count": 1,
        "data_preserved": True,
    }
    assert db_session.get(SystemSetting, "sandbox.enabled").value == "0"
    assert db_session.get(SystemSetting, "sandbox.exec_enabled").value == "0"
    assert db_session.query(Workspace).count() == 1
    assert db_session.query(Asset).count() == 1
    assert db_session.query(WorkspaceAsset).count() == 1
    assert db_session.query(SandboxRun).count() == 2
    audit = db_session.query(AdminAuditLog).filter_by(
        action="sandbox_kill_switch"
    ).one()
    assert "灰度回滚演练" in audit.detail_json


def test_admin_sandbox_routes_require_admin_bearer(
    db_session,
    monkeypatch,
):
    _settings(db_session)
    _business_rows(db_session)
    monkeypatch.setattr(admin_routes, "NANOBOT_ADMIN_TOKEN", "admin-secret")
    backends = []
    client = _client(
        db_session,
        monkeypatch,
        backends,
        auth_override=False,
    )

    unauthorized = client.get("/api/v1/admin/sandbox/status")
    authorized = client.get(
        "/api/v1/admin/sandbox/status",
        headers={"Authorization": "Bearer admin-secret"},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert len(backends) == 1

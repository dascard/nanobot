from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import admin_routes
from api.admin import sandbox_routes
from api.admin.common import verify_admin
from core.database import (
    AdminAuditLog,
    Asset,
    ChatLog,
    SandboxAccessGrant,
    SandboxAdminOperation,
    SandboxRun,
    SystemSetting,
    Workspace,
    WorkspaceAsset,
    WorkspaceQuotaBinding,
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
                "apparmor_profile": "nanobot-sandbox-restricted",
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


class _FakeAdminBackend:
    def __init__(self):
        self.closed = False
        self.terminate_calls = []

    def terminate_all_leases(self, *, request_id, reason):
        self.terminate_calls.append((request_id, reason))
        return success_result(
            "全量终止完成",
            data={
                "controller_epoch": "sbxctl_" + "1" * 32,
                "terminated_lease_ids": [],
                "affected_process_ids": [],
                "failed_lease_ids": [],
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
        "infrastructure_enable_allowed": False,
        "session_execution_allowed": False,
        "developer_network_allowed": False,
        "enabled": True,
        "exec_enabled": True,
        "group_enabled": False,
        "group_enabled_editable": True,
    }
    enabled_config = payload["configuration"]["sandbox.enabled"]
    assert enabled_config["database_override"] == {
        "configured": True,
        "value": True,
        "source_allowed": True,
    }
    assert enabled_config["effective"]["value"] is True
    assert enabled_config["effective"]["source"] == "database"
    infrastructure = payload["configuration"][
        "sandbox.infrastructure_enable_allowed"
    ]
    assert infrastructure["hard_ceiling"] is True
    assert infrastructure["database_override"]["source_allowed"] is False
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
    assert payload["limits"] == {
        "workspace_default_quota_bytes": 2 * 1024 * 1024 * 1024,
        "asset_max_bytes": 512 * 1024 * 1024,
        "total_quota_bytes": 10 * 1024 * 1024 * 1024,
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


def test_admin_sandbox_status_can_explain_session_gate(
    db_session,
    monkeypatch,
):
    _settings(db_session)
    _business_rows(db_session)
    client = _client(db_session, monkeypatch, [])

    response = client.get(
        "/api/v1/admin/sandbox/status",
        params={
            "platform": "qq",
            "chat_type": "private",
            "session_id": "private_missing",
            "tool_name": "workspace_read",
        },
    )

    assert response.status_code == 200
    diagnostic = response.json()["session_access"]
    assert diagnostic["required_capability"] == "workspace"
    assert diagnostic["final"] == {
        "allowed": False,
        "reason_code": "sandbox_not_enabled",
        "reason": "Sandbox 基础设施硬上限未允许",
        "granted_capability": "off",
        "execution_profile": "restricted",
        "workspace_configured": False,
    }
    gates = {item["id"]: item for item in diagnostic["gates"]}
    assert gates["infrastructure_ceiling"]["passed"] is False
    assert gates["infrastructure_ceiling"]["hard_ceiling"] is True
    assert gates["sandbox_enabled"]["passed"] is True
    assert gates["session_grant"]["reason_code"] == (
        "sandbox_grant_insufficient"
    )

    partial = client.get(
        "/api/v1/admin/sandbox/status",
        params={"platform": "qq"},
    )
    assert partial.status_code == 422


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


def test_admin_sandbox_kill_switch_terminates_runs_and_preserves_data(
    db_session,
    monkeypatch,
):
    _settings(db_session)
    _business_rows(db_session)
    backends = []
    admin_backends = []
    client = _client(db_session, monkeypatch, backends)

    def admin_factory(_db):
        backend = _FakeAdminBackend()
        admin_backends.append(backend)
        return backend

    monkeypatch.setattr(
        sandbox_routes,
        "_sandbox_admin_backend",
        admin_factory,
    )

    response = client.post(
        "/api/v1/admin/sandbox/kill-switch",
        json={
            "request_id": "legacy-kill-switch-request-1",
            "reason": "灰度回滚演练",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "replayed": False,
        "feature": {
            "enabled": False,
            "exec_enabled": False,
            "group_enabled": False,
        },
        "terminated_count": 1,
        "failed_count": 0,
        "terminated_lease_count": 0,
        "terminated_run_count": 1,
        "failed_lease_count": 0,
        "failed_run_count": 0,
        "data_preserved": True,
    }
    assert db_session.get(SystemSetting, "sandbox.enabled").value == "false"
    assert db_session.get(SystemSetting, "sandbox.exec_enabled").value == "false"
    assert db_session.get(SystemSetting, "sandbox.group_enabled").value == "false"
    assert db_session.query(Workspace).count() == 1
    assert db_session.query(Asset).count() == 1
    assert db_session.query(WorkspaceAsset).count() == 1
    assert db_session.query(SandboxRun).count() == 2
    assert db_session.get(SandboxRun, "sbxrun_running").status == "cancelled"
    assert (
        db_session.get(SandboxRun, "sbxrun_running").termination_reason
        == "kill_switch"
    )
    assert admin_backends[0].terminate_calls == [(
        "legacy-kill-switch-request-1",
        "kill_switch",
    )]
    assert admin_backends[0].closed is True
    assert len(backends[-1].cancelled) == 1
    assert backends[-1].cancelled[0][0] == "sbxrun_running"
    assert backends[-1].cancelled[0][1].startswith("kill_")
    assert backends[-1].closed is True
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


def test_admin_sandbox_feature_enable_respects_host_hard_ceiling(
    db_session,
    monkeypatch,
):
    from core.settings_service import settings

    _settings(db_session)
    db_session.commit()
    backends = []
    client = _client(db_session, monkeypatch, backends)

    monkeypatch.setenv(
        "NANOBOT_SANDBOX_INFRASTRUCTURE_ENABLE_ALLOWED",
        "false",
    )
    settings.invalidate()
    blocked = client.put(
        "/api/v1/admin/sandbox/features",
        json={
            "enabled": True,
            "exec_enabled": False,
            "group_enabled": True,
            "reason": "尝试启用",
        },
    )
    assert blocked.status_code == 409

    monkeypatch.setenv(
        "NANOBOT_SANDBOX_INFRASTRUCTURE_ENABLE_ALLOWED",
        "true",
    )
    settings.invalidate()
    accepted = client.put(
        "/api/v1/admin/sandbox/features",
        json={
            "enabled": True,
            "exec_enabled": False,
            "group_enabled": True,
            "reason": "灰度启用",
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["feature"] == {
        "infrastructure_enable_allowed": True,
        "session_execution_allowed": False,
        "developer_network_allowed": False,
        "enabled": True,
        "exec_enabled": False,
        "group_enabled": True,
    }
    assert db_session.get(SystemSetting, "sandbox.group_enabled").value == "true"

    compatible = client.put(
        "/api/v1/admin/sandbox/features",
        json={
            "enabled": True,
            "exec_enabled": False,
            "reason": "旧客户端保存",
        },
    )
    assert compatible.status_code == 200
    assert compatible.json()["feature"]["group_enabled"] is True

    disabled = client.put(
        "/api/v1/admin/sandbox/features",
        json={
            "enabled": False,
            "exec_enabled": False,
            "reason": "关闭总开关",
        },
    )
    assert disabled.status_code == 200
    assert disabled.json()["feature"]["group_enabled"] is False
    assert db_session.get(SystemSetting, "sandbox.group_enabled").value == "false"
    settings.invalidate()


def test_admin_sandbox_lists_only_real_private_sessions_and_enqueues_one_request(
    db_session,
    monkeypatch,
):
    _settings(db_session)
    db_session.add_all([
        ChatLog(
            user_id="user-a",
            session_id="private_session-a",
            sender_name="会话甲",
            role="user",
            content="私聊",
            meta_json='{"platform":"qq","chat_type":"private"}',
        ),
        ChatLog(
            user_id="user-a",
            session_id="group_7788",
            sender_name="群成员",
            role="user",
            content="群聊",
            meta_json='{"platform":"qq","chat_type":"group"}',
        ),
    ])
    db_session.commit()
    backends = []
    client = _client(db_session, monkeypatch, backends)

    sessions = client.get("/api/v1/admin/sandbox/sessions")
    assert sessions.status_code == 200
    assert [item["chat_stream_id"] for item in sessions.json()["items"]] == [
        "qq:session-a:private",
    ]
    assert sessions.json()["items"][0]["actor_user_id"] == "user-a"

    request = {
        "request_id": "api-access-request-0001",
        "platform": "qq",
        "chat_type": "private",
        "session_id": "private_session-a",
        "capability": "workspace",
        "quota_bytes": 64 * 1024 * 1024,
        "reason": "Web 聚合保存",
    }
    first = client.post(
        "/api/v1/admin/sandbox/access-grants",
        json=request,
    )
    repeated = client.post(
        "/api/v1/admin/sandbox/access-grants",
        json=request,
    )

    assert first.status_code == 202
    assert first.json()["created"] is True
    assert repeated.status_code == 202
    assert repeated.json()["created"] is False
    assert db_session.query(SandboxAdminOperation).count() == 1
    grant = db_session.query(SandboxAccessGrant).one()
    binding = db_session.query(WorkspaceQuotaBinding).one()
    assert grant.chat_stream_id == "qq:session-a:private"
    assert grant.capability_level == "off"
    assert grant.status == "provisioning"
    assert binding.project_id == 10000
    assert binding.status == "pending"

    operation_id = first.json()["operation"]["operation_id"]
    operation = client.get(
        f"/api/v1/admin/sandbox/operations/{operation_id}",
    )
    assert operation.status_code == 200
    assert operation.json()["operation"]["status"] == "pending"
    assert operation.json()["operation"]["expected_quota_generation"] == 1


def test_admin_sandbox_lists_and_enqueues_explicit_group_grant(
    db_session,
    monkeypatch,
):
    _settings(db_session)
    db_session.add(ChatLog(
        user_id="group_7788",
        session_id="group_7788",
        sender_name="群成员甲",
        session_name="测试群聊",
        role="ambient",
        content="群聊消息",
        meta_json=(
            '{"client_meta":{"platform":"qq","chat_type":"group"},'
            '"sender":{"id":"user-group-a"}}'
        ),
    ))
    db_session.commit()
    client = _client(db_session, monkeypatch, [])

    sessions = client.get(
        "/api/v1/admin/sandbox/sessions",
        params={"chat_type": "all"},
    )

    assert sessions.status_code == 200
    assert len(sessions.json()["items"]) == 1
    item = sessions.json()["items"][0]
    assert item["chat_stream_id"] == "qq:7788:group"
    assert item["platform"] == "qq"
    assert item["chat_type"] == "group"
    assert item["session_id"] == "7788"
    assert item["actor_user_id"] == "user-group-a"
    assert item["label"] == "测试群聊"

    created = client.post(
        "/api/v1/admin/sandbox/access-grants",
        json={
            "request_id": "group-access-request-0001",
            "platform": "qq",
            "chat_type": "group",
            "session_id": "group_7788",
            "capability": "workspace",
            "quota_bytes": 64 * 1024 * 1024,
            "reason": "显式授权测试群",
        },
    )

    assert created.status_code == 202
    grant = db_session.query(SandboxAccessGrant).one()
    workspace = db_session.get(Workspace, grant.workspace_id)
    assert grant.chat_stream_id == "qq:7788:group"
    assert grant.chat_type == "group"
    assert workspace.owner_type == "group"
    assert workspace.owner_id == "7788"


def test_admin_sandbox_rejects_trusted_profile_selection(
    db_session,
    monkeypatch,
):
    _settings(db_session)
    db_session.commit()
    client = _client(db_session, monkeypatch, [])

    response = client.post(
        "/api/v1/admin/sandbox/access-grants",
        json={
            "request_id": "trusted-profile-request-1",
            "session_id": "private_trusted-profile",
            "capability": "exec",
            "quota_bytes": 64 * 1024 * 1024,
            "execution_profile": "trusted_developer",
        },
    )

    assert response.status_code == 422
    assert db_session.query(SandboxAccessGrant).count() == 0
    assert db_session.query(SandboxAdminOperation).count() == 0


def test_admin_sandbox_rejects_invalid_or_mismatched_group_identity(
    db_session,
    monkeypatch,
):
    _settings(db_session)
    db_session.commit()
    client = _client(db_session, monkeypatch, [])
    base = {
        "request_id": "invalid-group-request-1",
        "platform": "qq",
        "session_id": "group_7788",
        "capability": "workspace",
        "quota_bytes": 64 * 1024 * 1024,
    }

    invalid_type = client.post(
        "/api/v1/admin/sandbox/access-grants",
        json={**base, "chat_type": "channel"},
    )
    mismatched_prefix = client.post(
        "/api/v1/admin/sandbox/access-grants",
        json={
            **base,
            "request_id": "invalid-group-request-2",
            "chat_type": "group",
            "session_id": "private_7788",
        },
    )

    assert invalid_type.status_code == 422
    assert mismatched_prefix.status_code == 400
    assert db_session.query(SandboxAccessGrant).count() == 0
    assert db_session.query(SandboxAdminOperation).count() == 0


def test_admin_sandbox_quota_change_is_async_and_audited(
    db_session,
    monkeypatch,
):
    _settings(db_session)
    db_session.commit()
    backends = []
    client = _client(db_session, monkeypatch, backends)
    created = client.post(
        "/api/v1/admin/sandbox/access-grants",
        json={
            "request_id": "quota-access-request-1",
            "session_id": "private_quota-session",
            "capability": "workspace",
            "quota_bytes": 64 * 1024 * 1024,
        },
    )
    workspace_id = created.json()["operation"]["workspace_id"]

    changed = client.post(
        f"/api/v1/admin/sandbox/workspaces/{workspace_id}/quota",
        json={
            "request_id": "quota-change-request-1",
            "quota_bytes": 96 * 1024 * 1024,
            "reason": "扩容测试",
        },
    )

    assert changed.status_code == 202
    assert changed.json()["operation"]["status"] == "pending"
    binding = db_session.get(WorkspaceQuotaBinding, workspace_id)
    assert binding.desired_quota_bytes == 96 * 1024 * 1024
    assert binding.applied_quota_bytes == 0
    assert binding.generation == 2
    audit = db_session.query(AdminAuditLog).filter_by(
        action="sandbox_quota_enqueue",
    ).one()
    assert audit.target_id == workspace_id

    audit_response = client.get("/api/v1/admin/sandbox/audit-logs")
    assert audit_response.status_code == 200
    assert "sandbox_quota_enqueue" in {
        item["action"] for item in audit_response.json()["items"]
    }

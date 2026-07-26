from __future__ import annotations

import os
import subprocess

from fastapi.testclient import TestClient

from core.database import (
    SandboxAccessGrant,
    SandboxLease,
    WorkspaceQuotaBinding,
    WorkspaceRuntimeQuotaBinding,
)
from core.sandbox.admin_service import SandboxAdminService
from core.sandbox.contracts import SandboxErrorCode
from sandboxd.app import create_app
from sandboxd.docker_backend import LocalDockerBackend
from sandboxd.quota import ProjectQuotaManager
from tests.test_sandboxd_api import WORKSPACE_ID, _runtime
from tests.test_sandboxd_profile_config import (
    _DockerClient,
    _configured_catalog,
)
from tests.test_sandbox_quota_backend import _with_admin_runtime


MIB = 1024 * 1024


def test_project_quota_capability_uses_read_only_helper_check_and_cache(
    tmp_path,
):
    helper_path = tmp_path / "fixed-quota-helper"
    helper_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    helper_path.chmod(0o700)
    calls: list[tuple[tuple[str, ...], float]] = []

    def command(argv, *, timeout):
        calls.append((tuple(argv), timeout))
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                "project_quota_ready=true\n"
                "workspace_scope=true\n"
                "runtime_scope=true\n"
            ),
            stderr="",
        )

    manager = ProjectQuotaManager(
        data_root=tmp_path / "data",
        helper_path=helper_path,
        command=command,
    )

    assert manager.capability() == {
        "project_quota": True,
        "workspace_scope": True,
        "runtime_scope": True,
    }
    assert manager.capability() == {
        "project_quota": True,
        "workspace_scope": True,
        "runtime_scope": True,
    }
    assert calls == [(
        (
            os.fspath(helper_path),
            "--check-capability",
            "--data-root",
            os.fspath(tmp_path / "data"),
        ),
        30.0,
    )]


def test_runtime_quota_manager_uses_distinct_scope_and_runtime_path(tmp_path):
    data_root = tmp_path / "data"
    helper_path = tmp_path / "fixed-quota-helper"
    helper_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    helper_path.chmod(0o700)
    calls: list[tuple[tuple[str, ...], float]] = []

    def command(argv, *, timeout):
        calls.append((tuple(argv), timeout))
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                "project_quota_verified=true\n"
                "scope=runtime\n"
                "project_id=10001\n"
                f"quota_bytes={32 * MIB}\n"
            ),
            stderr="",
        )

    manager = ProjectQuotaManager(
        data_root=data_root,
        helper_path=helper_path,
        command=command,
    )
    manager.layout.ensure_roots()
    manager.layout.ensure_workspace(WORKSPACE_ID)
    runtime_path = manager.layout.ensure_runtime(WORKSPACE_ID)
    (runtime_path / "cache.bin").write_bytes(b"runtime")

    result = manager.apply(
        workspace_id=WORKSPACE_ID,
        project_id=10001,
        quota_bytes=32 * MIB,
        generation=4,
        scope="runtime",
    )

    assert result == {
        "workspace_id": WORKSPACE_ID,
        "scope": "runtime",
        "project_id": 10001,
        "quota_bytes": 32 * MIB,
        "generation": 4,
        "used_bytes": len(b"runtime"),
        "applied": True,
    }
    argv, _timeout = calls[0]
    assert argv == (
        os.fspath(helper_path),
        "--workspace-id",
        WORKSPACE_ID,
        "--scope",
        "runtime",
        "--project-id",
        "10001",
        "--quota-bytes",
        str(32 * MIB),
        "--data-root",
        os.fspath(data_root),
        "--quiesced",
        "--apply",
    )


def test_quota_api_rejects_reused_project_id_for_workspace_and_runtime(tmp_path):
    _normal_token, admin_token, runtime = _with_admin_runtime(tmp_path)
    body = {
        "request_id": "quota-distinct-projects",
        "workspace_id": WORKSPACE_ID,
        "project_id": 10000,
        "quota_bytes": 64 * MIB,
        "runtime_project_id": 10000,
        "runtime_quota_bytes": 32 * MIB,
        "generation": 1,
    }

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/v1/admin/workspaces/quota/apply",
            headers={
                "Authorization": f"Bearer {admin_token}",
                "X-Nanobot-Request-ID": body["request_id"],
            },
            json=body,
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "authorization_failed"
    assert runtime.quota_manager.apply_calls == []


def test_developer_profile_is_not_ready_without_project_quota_capability(
    tmp_path,
    monkeypatch,
):
    from sandboxd import docker_backend as docker_backend_module

    profiles = tmp_path / "apparmor-profiles"
    profiles.write_text(
        "nanobot-sandbox-restricted (enforce)\n"
        "nanobot-sandbox-developer (enforce)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        docker_backend_module,
        "APPARMOR_PROFILES_PATH",
        profiles,
    )
    from sandboxd.config import SandboxdConfig

    config = SandboxdConfig(
        data_root=tmp_path / "data",
        socket_path=tmp_path / "run" / "sandboxd.sock",
        token_file=tmp_path / "sandboxd.token",
        client_token_path=tmp_path / "run" / "client.token",
        profile_manifest_path=_configured_catalog(tmp_path),
        disk_min_free_bytes=0,
    ).validated()
    backend = LocalDockerBackend(
        config,
        docker_client=_DockerClient(),
        quota_manager=None,
    )
    backend.workspace_files.layout.ensure_roots()

    ready = backend.ready()

    assert ready["project_quota_ready"] is False
    assert ready["profiles"]["restricted"]["ready"] is True
    assert ready["profiles"]["developer"]["ready"] is False
    assert (
        ready["profiles"]["developer"]["error_code"]
        == "project_quota_unavailable"
    )


def test_runtime_edquot_is_reported_with_distinct_stable_error(tmp_path):
    token, runtime = _runtime(tmp_path)

    def execute(**_kwargs):
        return {
            "status": "failed",
            "data": {
                "exit_code": 1,
                "termination_reason": "runtime_quota_exceeded",
            },
        }

    runtime.docker_backend.execute = execute
    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/v1/runs",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "request_id": "runtime-quota-request",
                "run_id": "sbxrun_runtime_quota",
                "workspace_id": WORKSPACE_ID,
                "command": "python fill-cache.py",
                "quota_bytes": 64 * MIB,
            },
        )

    assert response.status_code == 507
    assert response.json()["error"]["code"] == (
        SandboxErrorCode.RUNTIME_QUOTA_EXCEEDED.value
    )
    assert "Runtime" in response.json()["summary"]
    assert LocalDockerBackend._quota_reason(
        "",
        "OSError: [Errno 122] Disk quota exceeded: '/runtime/cache.bin'",
    ) == "runtime_quota_exceeded"


def _enqueue_workspace(db_session, suffix: str):
    result = SandboxAdminService(db_session).enqueue_access_change(
        request_id=f"runtime-binding-{suffix}",
        platform="qq",
        chat_type="private",
        session_id=f"private_runtime-{suffix}",
        capability="exec",
        quota_bytes=64 * MIB,
        expected_version=None,
        reason="Runtime quota 测试",
        actor="admin-test",
    )
    db_session.commit()
    return result.operation.workspace_id


def test_lease_delete_and_recreate_preserve_each_workspace_runtime_binding(
    db_session,
):
    workspace_a = _enqueue_workspace(db_session, "workspace-a")
    workspace_b = _enqueue_workspace(db_session, "workspace-b")
    grant_a = db_session.query(SandboxAccessGrant).filter_by(
        workspace_id=workspace_a,
    ).one()
    binding_a = db_session.get(WorkspaceRuntimeQuotaBinding, workspace_a)
    binding_b = db_session.get(WorkspaceRuntimeQuotaBinding, workspace_b)
    workspace_binding_a = db_session.get(WorkspaceQuotaBinding, workspace_a)
    assert binding_a is not None
    assert binding_b is not None
    assert workspace_binding_a is not None
    original_ids = {
        "workspace": int(workspace_binding_a.project_id),
        "runtime_a": int(binding_a.project_id),
        "runtime_b": int(binding_b.project_id),
    }
    assert len(set(original_ids.values())) == 3

    first = SandboxLease(
        lease_id="sbxlease_runtime_a_1",
        lease_key="runtime-a",
        grant_id=grant_a.id,
        chat_stream_id=grant_a.chat_stream_id,
        workspace_id=workspace_a,
        profile_id="developer",
        catalog_generation="20260725.1",
        policy_sha256="a" * 64,
        status="stopped",
    )
    db_session.add(first)
    db_session.commit()
    db_session.delete(first)
    db_session.commit()
    replacement = SandboxLease(
        lease_id="sbxlease_runtime_a_2",
        lease_key="runtime-a",
        grant_id=grant_a.id,
        chat_stream_id=grant_a.chat_stream_id,
        workspace_id=workspace_a,
        profile_id="developer",
        catalog_generation="20260725.1",
        policy_sha256="a" * 64,
        status="provisioning",
    )
    db_session.add(replacement)
    db_session.commit()

    assert db_session.get(
        WorkspaceRuntimeQuotaBinding,
        workspace_a,
    ).project_id == original_ids["runtime_a"]
    assert db_session.get(
        WorkspaceRuntimeQuotaBinding,
        workspace_b,
    ).project_id == original_ids["runtime_b"]

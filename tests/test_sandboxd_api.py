import os
import stat
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from core.sandbox.contracts import SandboxServiceError
from sandboxd.app import SandboxRuntime, create_app
from sandboxd.auth import TokenAuthenticator
from sandboxd.config import SandboxdConfig
from sandboxd.filesystem import AssetFileService, WorkspaceFileService


IMAGE_ID = "sha256:" + "a" * 64
WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"
WORKSPACE_ID_B = "00000000-0000-0000-0000-000000000002"
WORKSPACE_ID_C = "00000000-0000-0000-0000-000000000003"


class _EmptyContainers:
    def list(self, **_kwargs):
        return []


class _FakeDockerClient:
    containers = _EmptyContainers()


class _FakeDockerBackend:
    def __init__(self):
        self.client = _FakeDockerClient()
        self.executed = []

    def ready(self):
        return {
            "docker": True,
            "image_id": IMAGE_ID,
            "apparmor_profile": "nanobot-sandbox",
            "disk_used_percent": 1.0,
            "disk_free_bytes": 10**12,
        }

    def execute(self, **kwargs):
        self.executed.append(kwargs)
        return {
            "status": "completed",
            "data": {
                "exit_code": 0,
                "termination_reason": "completed",
                "stdout": "ok\n",
                "stderr": "",
                "stdout_bytes": 3,
                "stderr_bytes": 0,
                "stdout_truncated": False,
                "stderr_truncated": False,
                "oom_killed": False,
                "cpu_time_ms": 1,
                "peak_memory_bytes": 1024,
            },
        }

    def cancel(self, run_id):
        return {
            "run_id": run_id,
            "status": "completed",
            "data": {
                "stdout": "SANDBOX_STDOUT_MUST_NOT_RETURN",
                "stderr": "SANDBOX_STDERR_MUST_NOT_RETURN",
                "exit_code": 0,
                "termination_reason": "completed",
            },
        }

    def get(self, run_id):
        return {"run_id": run_id, "status": "completed", "data": {}}


def _runtime(tmp_path):
    token = "t" * 64
    token_file = tmp_path / "sandboxd.token"
    token_file.write_text(token)
    token_file.chmod(0o600)
    config = SandboxdConfig(
        data_root=tmp_path / "data",
        socket_path=tmp_path / "run" / "sandboxd.sock",
        token_file=token_file,
        client_token_path=tmp_path / "run" / "client.token",
        image_reference="nanobot-sandbox-python:test",
        image_allowlist=(IMAGE_ID,),
        workspace_uid=os.getuid(),
        workspace_gid=os.getgid(),
        disk_min_free_bytes=0,
    ).validated()
    workspace_files = WorkspaceFileService(config)
    asset_files = AssetFileService(config)
    docker_backend = _FakeDockerBackend()
    return token, SandboxRuntime(
        config=config,
        authenticator=TokenAuthenticator(token_file, config.client_token_path),
        workspace_files=workspace_files,
        asset_files=asset_files,
        docker_backend=docker_backend,
    )


def test_sandboxd_token_file_fails_closed_on_permissions_and_control_bytes(tmp_path):
    token_file = tmp_path / "sandboxd.token"
    client_token = tmp_path / "run" / "client.token"
    authenticator = TokenAuthenticator(token_file, client_token)

    token_file.write_text("t" * 64, encoding="ascii")
    token_file.chmod(0o644)
    with pytest.raises(SandboxServiceError):
        authenticator.read_token()

    token_file.write_bytes(b"t" * 63 + b"\x01")
    token_file.chmod(0o600)
    with pytest.raises(SandboxServiceError):
        authenticator.read_token()

    token_file.write_bytes(b"t" * 64 + b"\n")
    token_file.chmod(0o600)
    authenticator.prepare_client_token()
    assert authenticator.read_token() == "t" * 64
    assert stat.S_IMODE(client_token.stat().st_mode) == 0o640


def test_sandboxd_requires_bearer_and_supports_safe_file_round_trip(tmp_path):
    token, runtime = _runtime(tmp_path)
    app = create_app(runtime)

    with TestClient(app) as client:
        assert client.get("/v1/healthz").status_code == 403
        headers = {"Authorization": f"Bearer {token}"}
        health = client.get("/v1/healthz", headers=headers)
        assert health.status_code == 200

        ensured = client.post(
            "/v1/workspaces/ensure",
            headers=headers,
            json={"workspace_id": WORKSPACE_ID},
        )
        assert ensured.status_code == 200

        written = client.post(
            "/v1/files/write",
            headers=headers,
            json={
                "workspace_id": WORKSPACE_ID,
                "path": "notes/test.txt",
                "content": "安全文本",
                "overwrite": False,
                "quota_bytes": 1024 * 1024,
            },
        )
        assert written.status_code == 200
        assert written.json()["artifacts"][0]["path"] == "notes/test.txt"

        read = client.post(
            "/v1/files/read",
            headers=headers,
            json={
                "workspace_id": WORKSPACE_ID,
                "path": "notes/test.txt",
                "offset": 0,
                "limit": 1024,
            },
        )
        assert read.status_code == 200
        assert read.json()["data"]["content"] == "安全文本"
        assert str(tmp_path) not in str(read.json())


def test_workspace_write_uses_exact_overwrite_delta_and_shared_mutation_lock(tmp_path):
    _token, runtime = _runtime(tmp_path)
    service = runtime.workspace_files
    service.layout.ensure_roots()
    service.ensure_workspace(WORKSPACE_ID)

    first = service.write_file(
        WORKSPACE_ID,
        path="result.txt",
        content="12345678",
        overwrite=False,
        quota_bytes=10,
    )
    overwritten = service.write_file(
        WORKSPACE_ID,
        path="result.txt",
        content="123456789",
        overwrite=True,
        quota_bytes=10,
    )

    assert first["usage_delta_bytes"] == 8
    assert overwritten["previous_size_bytes"] == 8
    assert overwritten["used_bytes"] == 9
    assert overwritten["usage_delta_bytes"] == 1

    lock = service.acquire_workspace_mutation(WORKSPACE_ID)
    try:
        with pytest.raises(SandboxServiceError) as busy:
            service.write_file(
                WORKSPACE_ID,
                path="other.txt",
                content="x",
                overwrite=False,
                quota_bytes=10,
            )
    finally:
        lock.release()
    assert busy.value.code.value == "sandbox_busy"


def test_workspace_total_quota_accounts_for_active_run_reservations(tmp_path):
    _token, runtime = _runtime(tmp_path)
    config = replace(
        runtime.config,
        workspace_quota_bytes=10,
        total_quota_bytes=10,
    )
    service = WorkspaceFileService(config)
    service.layout.ensure_roots()
    for workspace_id in (WORKSPACE_ID, WORKSPACE_ID_B, WORKSPACE_ID_C):
        service.ensure_workspace(workspace_id)
    service.write_file(
        WORKSPACE_ID,
        path="existing.txt",
        content="12345678",
        overwrite=False,
        quota_bytes=10,
    )

    workspace_lock = service.acquire_workspace_mutation(WORKSPACE_ID_B)
    try:
        usage_before, run_quota = service.reserve_run_capacity(
            "sbxrun_reserved",
            WORKSPACE_ID_B,
            workspace_quota_bytes=10,
        )
        assert usage_before == 0
        assert run_quota == 2
        with pytest.raises(SandboxServiceError) as total_quota:
            service.write_file(
                WORKSPACE_ID_C,
                path="blocked.txt",
                content="x",
                overwrite=False,
                quota_bytes=10,
            )
        assert total_quota.value.code.value == "workspace_quota_exceeded"
    finally:
        service.release_run_capacity("sbxrun_reserved")
        workspace_lock.release()

    written = service.write_file(
        WORKSPACE_ID_C,
        path="allowed.txt",
        content="x",
        overwrite=False,
        quota_bytes=10,
    )
    assert written["used_bytes"] == 1
    assert service.total_workspace_usage() == 9


def test_sandboxd_rejects_model_controlled_docker_fields_without_echo(tmp_path):
    token, runtime = _runtime(tmp_path)
    forbidden = "secret-host-volume:/workspace"

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/v1/runs",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "request_id": "request-1",
                "run_id": "sbxrun_test1",
                "workspace_id": WORKSPACE_ID,
                "command": "true",
                "quota_bytes": 1024 * 1024,
                "image": "attacker/image:latest",
                "network": "host",
                "volume": forbidden,
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "authorization_failed"
    assert forbidden not in response.text
    assert runtime.docker_backend.executed == []


def test_sandboxd_cancel_returns_only_safe_run_summary(tmp_path):
    token, runtime = _runtime(tmp_path)

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/v1/runs/sbxrun_test1/cancel",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )

    assert response.status_code == 200
    assert response.json()["data"]["run_id"] == "sbxrun_test1"
    assert response.json()["data"]["status"] == "completed"
    assert "SANDBOX_STDOUT_MUST_NOT_RETURN" not in response.text
    assert "SANDBOX_STDERR_MUST_NOT_RETURN" not in response.text

from __future__ import annotations

import os
import subprocess

import pytest
from fastapi.testclient import TestClient

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from sandboxd.app import create_app
from sandboxd.auth import TokenAuthenticator
from sandboxd.quota import ProjectQuotaManager
from tests.test_sandboxd_api import WORKSPACE_ID, _runtime


MIB = 1024 * 1024


class _QuotaManager:
    def __init__(self) -> None:
        self.apply_calls: list[dict] = []
        self.inspect_calls: list[dict] = []

    def apply(self, **payload):
        self.apply_calls.append(dict(payload))
        return {**payload, "used_bytes": 0, "applied": True}

    def inspect(self, **payload):
        self.inspect_calls.append(dict(payload))
        return {
            **payload,
            "used_bytes": 0,
            "observed_project_id": payload["project_id"],
            "project_id_matches": True,
        }


def _with_admin_runtime(tmp_path):
    normal_token, runtime = _runtime(tmp_path)
    admin_token = "a" * 64
    admin_token_file = tmp_path / "sandboxd-admin.token"
    admin_token_file.write_text(admin_token, encoding="ascii")
    admin_token_file.chmod(0o600)
    runtime.admin_authenticator = TokenAuthenticator(
        admin_token_file,
        tmp_path / "run" / "admin-client.token",
    )
    runtime.quota_manager = _QuotaManager()
    runtime.workspace_files.layout.ensure_roots()
    runtime.workspace_files.ensure_workspace(WORKSPACE_ID)
    return normal_token, admin_token, runtime


def test_quota_api_requires_independent_admin_token_and_matching_request_id(tmp_path):
    normal_token, admin_token, runtime = _with_admin_runtime(tmp_path)
    body = {
        "request_id": "quota-request-0001",
        "workspace_id": WORKSPACE_ID,
        "project_id": 10000,
        "quota_bytes": 64 * MIB,
        "generation": 1,
    }

    with TestClient(create_app(runtime)) as client:
        normal_ensure = client.post(
            "/v1/admin/workspaces/ensure",
            headers={"Authorization": f"Bearer {normal_token}"},
            json={"workspace_id": WORKSPACE_ID},
        )
        admin_ensure = client.post(
            "/v1/admin/workspaces/ensure",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"workspace_id": WORKSPACE_ID},
        )
        normal = client.post(
            "/v1/admin/workspaces/quota/apply",
            headers={
                "Authorization": f"Bearer {normal_token}",
                "X-Nanobot-Request-ID": body["request_id"],
            },
            json=body,
        )
        mismatched = client.post(
            "/v1/admin/workspaces/quota/apply",
            headers={
                "Authorization": f"Bearer {admin_token}",
                "X-Nanobot-Request-ID": "quota-request-other",
            },
            json=body,
        )
        accepted = client.post(
            "/v1/admin/workspaces/quota/apply",
            headers={
                "Authorization": f"Bearer {admin_token}",
                "X-Nanobot-Request-ID": body["request_id"],
            },
            json=body,
        )

    assert normal_ensure.status_code == 403
    assert admin_ensure.status_code == 200
    assert normal.status_code == 403
    assert mismatched.status_code == 403
    assert accepted.status_code == 200
    assert accepted.json()["data"]["applied"] is True
    assert runtime.quota_manager.apply_calls == [{
        "workspace_id": WORKSPACE_ID,
        "project_id": 10000,
        "quota_bytes": 64 * MIB,
        "generation": 1,
    }]


def test_sandboxd_startup_rejects_equal_normal_and_admin_token(tmp_path):
    normal_token, _admin_token, runtime = _with_admin_runtime(tmp_path)
    runtime.admin_authenticator.token_file.write_text(
        normal_token,
        encoding="ascii",
    )
    runtime.admin_authenticator.token_file.chmod(0o600)

    with pytest.raises(RuntimeError, match="必须不同"):
        with TestClient(create_app(runtime)):
            pass


def test_quota_api_rejects_host_paths_and_unknown_fields_without_echo(tmp_path):
    _normal_token, admin_token, runtime = _with_admin_runtime(tmp_path)
    forbidden_path = "/srv/nanobot/secret-workspace"

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/v1/admin/workspaces/quota/apply",
            headers={
                "Authorization": f"Bearer {admin_token}",
                "X-Nanobot-Request-ID": "quota-request-0002",
            },
            json={
                "request_id": "quota-request-0002",
                "workspace_id": WORKSPACE_ID,
                "project_id": 10000,
                "quota_bytes": 64 * MIB,
                "generation": 1,
                "host_path": forbidden_path,
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "authorization_failed"
    assert forbidden_path not in response.text
    assert runtime.quota_manager.apply_calls == []


def test_project_quota_manager_uses_fixed_argv_and_returns_bounded_metadata(tmp_path):
    data_root = tmp_path / "data"
    helper_path = tmp_path / "fixed-quota-helper"
    helper_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    helper_path.chmod(0o700)
    calls: list[tuple[tuple[str, ...], float]] = []

    def command(argv, *, timeout):
        calls.append((tuple(argv), timeout))
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    manager = ProjectQuotaManager(
        data_root=data_root,
        helper_path=helper_path,
        command=command,
    )
    manager.layout.ensure_roots()
    workspace_path = manager.layout.workspace_data_dir(WORKSPACE_ID)
    workspace_path.mkdir(parents=True)
    (workspace_path / "payload.bin").write_bytes(b"1234")

    result = manager.apply(
        workspace_id=WORKSPACE_ID,
        project_id=10000,
        quota_bytes=64 * MIB,
        generation=3,
    )

    assert result == {
        "workspace_id": WORKSPACE_ID,
        "project_id": 10000,
        "quota_bytes": 64 * MIB,
        "generation": 3,
        "used_bytes": 4,
        "applied": True,
    }
    argv, timeout = calls[0]
    assert argv == (
        os.fspath(helper_path),
        "--workspace-id",
        WORKSPACE_ID,
        "--project-id",
        "10000",
        "--quota-bytes",
        str(64 * MIB),
        "--data-root",
        os.fspath(data_root),
        "--quiesced",
        "--apply",
    )
    assert timeout == 60.0
    assert not any("shell" in item for item in argv)


def test_project_quota_failure_does_not_expose_subprocess_output(tmp_path):
    secret = "/srv/nanobot/host-secret"
    helper_path = tmp_path / "fixed-helper"
    helper_path.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    helper_path.chmod(0o700)

    def command(argv, *, timeout):
        return subprocess.CompletedProcess(
            argv,
            1,
            stdout=secret,
            stderr="root-only-token",
        )

    manager = ProjectQuotaManager(
        data_root=tmp_path / "data",
        helper_path=helper_path,
        command=command,
    )
    manager.layout.ensure_roots()
    manager.layout.workspace_data_dir(WORKSPACE_ID).mkdir(parents=True)

    try:
        manager.apply(
            workspace_id=WORKSPACE_ID,
            project_id=10000,
            quota_bytes=64 * MIB,
            generation=1,
        )
    except Exception as exc:
        message = str(exc)
    else:
        raise AssertionError("预期 project quota 应用失败")

    assert secret not in message
    assert "root-only-token" not in message


@pytest.mark.parametrize("unsafe_kind", ["symlink", "group_writable"])
def test_project_quota_manager_rejects_unsafe_helper(tmp_path, unsafe_kind):
    helper_target = tmp_path / "quota-helper-target"
    helper_target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    helper_target.chmod(0o700)
    if unsafe_kind == "symlink":
        helper_path = tmp_path / "quota-helper-link"
        helper_path.symlink_to(helper_target)
    else:
        helper_path = helper_target
        helper_path.chmod(0o720)
    calls = []

    def command(argv, *, timeout):
        calls.append((argv, timeout))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    manager = ProjectQuotaManager(
        data_root=tmp_path / "data",
        helper_path=helper_path,
        command=command,
    )
    manager.layout.ensure_roots()
    manager.layout.workspace_data_dir(WORKSPACE_ID).mkdir(parents=True)

    with pytest.raises(SandboxServiceError) as raised:
        manager.apply(
            workspace_id=WORKSPACE_ID,
            project_id=10000,
            quota_bytes=64 * MIB,
            generation=1,
        )

    assert raised.value.code is SandboxErrorCode.RUNTIME_UNAVAILABLE
    assert calls == []

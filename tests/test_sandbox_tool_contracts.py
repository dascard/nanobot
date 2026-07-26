from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from core.database import Workspace
from core.sandbox.access_contracts import (
    TOOL_REQUIRED_CAPABILITY,
    SandboxAccessDecision,
    SandboxCapability,
)
from core.sandbox.backend import FakeSandboxBackend
from core.sandbox.client import HttpSandboxdBackend
from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.tool_service import SandboxToolService
from core.tool_registry import SANDBOX_TOOL_NAMES
from tests.test_sandboxd_api import WORKSPACE_ID, _runtime
from sandboxd.app import create_app


NEW_TOOL_NAMES = {
    "sandbox_poll",
    "sandbox_write_stdin",
    "sandbox_terminate",
    "workspace_apply_patch",
}


def _workspace(db_session) -> Workspace:
    row = Workspace(
        id=str(uuid4()),
        platform="qq",
        owner_type="user",
        owner_id="sandbox-contract",
        name="default",
        status="active",
        quota_bytes=32 * 1024 * 1024,
        used_bytes=0,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _access(workspace: Workspace, profile_id: str) -> SandboxAccessDecision:
    return SandboxAccessDecision(
        True,
        "",
        "已授权",
        SandboxCapability.EXEC,
        granted_capability=SandboxCapability.EXEC,
        workspace_id=workspace.id,
        quota_bytes=workspace.quota_bytes,
        execution_profile=profile_id,
        grant_version=1,
        quota_generation=1,
    )


def test_new_sandbox_tools_are_registered_atomically_with_strict_wire_schemas():
    from core.tool_schema_preview import STATIC_TOOL_SCHEMAS, build_tool_schema

    assert set(SANDBOX_TOOL_NAMES) == set(TOOL_REQUIRED_CAPABILITY)
    assert NEW_TOOL_NAMES <= set(SANDBOX_TOOL_NAMES)
    assert set(SANDBOX_TOOL_NAMES) <= set(STATIC_TOOL_SCHEMAS)
    assert (
        TOOL_REQUIRED_CAPABILITY["workspace_apply_patch"]
        is SandboxCapability.WORKSPACE
    )
    for name in {
        "sandbox_poll",
        "sandbox_write_stdin",
        "sandbox_terminate",
    }:
        assert TOOL_REQUIRED_CAPABILITY[name] is SandboxCapability.EXEC

    exec_parameters = build_tool_schema(
        "sandbox_exec",
        include_template_overlay=False,
    )["function"]["parameters"]
    assert set(exec_parameters["properties"]) == {
        "command",
        "cwd",
        "yield_time_ms",
        "timeout_seconds",
    }
    assert exec_parameters["properties"]["timeout_seconds"]["maximum"] == 3600
    assert exec_parameters["properties"]["yield_time_ms"]["maximum"] == 30_000

    forbidden = {
        "run_in_background",
        "workspace_id",
        "owner_id",
        "user_id",
        "execution_profile",
        "github_token",
        "git_credentials",
        "ssh_key",
        "ssh_agent_socket",
        "image",
        "network",
        "volume",
        "devices",
        "capabilities",
    }
    assert "repo_push" not in SANDBOX_TOOL_NAMES
    for name in SANDBOX_TOOL_NAMES:
        parameters = build_tool_schema(
            name,
            include_template_overlay=False,
        )["function"]["parameters"]
        assert parameters["additionalProperties"] is False
        assert not forbidden & set(parameters["properties"])


def test_new_sandbox_tool_concurrency_and_background_contracts():
    from nanobot_kt.tools.sandbox import (
        SandboxExecTool,
        SandboxPollTool,
        SandboxTerminateTool,
        SandboxWriteStdinTool,
        WorkspaceApplyPatchTool,
    )

    assert SandboxExecTool.is_concurrency_safe is False
    assert SandboxPollTool.is_concurrency_safe is True
    assert SandboxWriteStdinTool.is_concurrency_safe is False
    assert SandboxTerminateTool.is_concurrency_safe is False
    assert WorkspaceApplyPatchTool.is_concurrency_safe is False
    for tool_type in (
        SandboxExecTool,
        SandboxPollTool,
        SandboxTerminateTool,
        SandboxWriteStdinTool,
        WorkspaceApplyPatchTool,
    ):
        assert tool_type.supports_background is False


@pytest.mark.parametrize(
    ("profile_id", "timeout_seconds", "maximum"),
    [
        ("restricted", 121, 120),
        ("developer", 1801, 1800),
    ],
)
def test_server_rejects_timeout_above_authorized_profile_before_backend_call(
    db_session,
    monkeypatch,
    profile_id,
    timeout_seconds,
    maximum,
):
    workspace = _workspace(db_session)
    access = _access(workspace, profile_id)
    backend = FakeSandboxBackend()
    service = SandboxToolService(db_session, backend)
    monkeypatch.setattr(
        service,
        "authorize",
        lambda _name, _context: (access, {}),
    )

    with pytest.raises(SandboxServiceError) as raised:
        service.sandbox_exec(
            {
                "command": "true",
                "timeout_seconds": timeout_seconds,
            },
            {},
        )

    assert raised.value.code is SandboxErrorCode.AUTHORIZATION_FAILED
    assert str(maximum) in raised.value.hint
    assert backend.calls == []


def test_developer_exec_dispatches_to_lease_process_service(
    db_session,
    monkeypatch,
):
    workspace = _workspace(db_session)
    access = _access(workspace, "developer")
    backend = FakeSandboxBackend()
    service = SandboxToolService(db_session, backend)
    monkeypatch.setattr(
        service,
        "authorize",
        lambda _name, _context: (access, {}),
    )
    observed = {}

    def _start(current, args, *, authorized_assets):
        observed.update({
            "access": current,
            "args": dict(args),
            "assets": list(authorized_assets),
        })
        return {"status": "success", "data": {"execution_status": "running"}}

    monkeypatch.setattr(service.process_service, "start", _start)

    result = service.sandbox_exec(
        {
            "command": "pytest -q",
            "yield_time_ms": 1000,
            "timeout_seconds": 1800,
        },
        {},
    )

    assert result["data"]["execution_status"] == "running"
    assert observed["access"] is access
    assert observed["args"]["yield_time_ms"] == 1000
    assert observed["assets"] == []
    assert backend.calls == []


def test_process_control_tools_delegate_only_trusted_arguments(
    db_session,
    monkeypatch,
):
    workspace = _workspace(db_session)
    access = _access(workspace, "developer")
    service = SandboxToolService(db_session, FakeSandboxBackend())
    authorized_names = []
    observed = []

    def _authorize(name, _context):
        authorized_names.append(name)
        return access, {}

    monkeypatch.setattr(service, "authorize", _authorize)
    monkeypatch.setattr(
        service.process_service,
        "poll",
        lambda current, process_id, *, cursor: observed.append(
            ("poll", current, process_id, cursor)
        ) or {"status": "success"},
    )
    monkeypatch.setattr(
        service.process_service,
        "write_stdin",
        lambda current, process_id, *, chars: observed.append(
            ("stdin", current, process_id, chars)
        ) or {"status": "success"},
    )
    monkeypatch.setattr(
        service.process_service,
        "terminate",
        lambda current, process_id: observed.append(
            ("terminate", current, process_id)
        ) or {"status": "success"},
    )

    service.sandbox_poll(
        {"process_id": "sbxrun_one", "cursor": "v1:1:2"},
        {"workspace_id": "model-controlled"},
    )
    service.sandbox_write_stdin(
        {"process_id": "sbxrun_one", "chars": "yes\n"},
        {"workspace_id": "model-controlled"},
    )
    service.sandbox_terminate(
        {"process_id": "sbxrun_one"},
        {"workspace_id": "model-controlled"},
    )

    assert authorized_names == [
        "sandbox_poll",
        "sandbox_write_stdin",
        "sandbox_terminate",
    ]
    assert observed == [
        ("poll", access, "sbxrun_one", "v1:1:2"),
        ("stdin", access, "sbxrun_one", "yes\n"),
        ("terminate", access, "sbxrun_one"),
    ]


def test_workspace_apply_patch_is_atomic_strict_and_uses_shared_write_lock(
    tmp_path,
):
    _token, runtime = _runtime(tmp_path)
    service = runtime.workspace_files
    service.layout.ensure_roots()
    service.ensure_workspace(WORKSPACE_ID)
    service.write_file(
        WORKSPACE_ID,
        path="src/example.py",
        content="one\ntwo\nthree\n",
        overwrite=False,
        quota_bytes=1024 * 1024,
    )
    patch = (
        "--- a/src/example.py\n"
        "+++ b/src/example.py\n"
        "@@ -1,3 +1,3 @@\n"
        " one\n"
        "-two\n"
        "+TWO\n"
        " three\n"
    )

    applied = service.apply_patch(
        WORKSPACE_ID,
        path="src/example.py",
        patch=patch,
        quota_bytes=1024 * 1024,
    )
    content = service.filesystem(WORKSPACE_ID).read_bytes(
        "src/example.py",
        offset=0,
        limit=1024,
    )

    assert content == b"one\nTWO\nthree\n"
    assert applied["hunks_applied"] == 1
    assert applied["added_lines"] == 1
    assert applied["removed_lines"] == 1
    assert applied["usage_delta_bytes"] == 0

    with pytest.raises(SandboxServiceError) as mismatch:
        service.apply_patch(
            WORKSPACE_ID,
            path="src/example.py",
            patch="@@ -1,1 +1,1 @@\n-missing\n+changed\n",
            quota_bytes=1024 * 1024,
        )
    assert mismatch.value.code is SandboxErrorCode.AUTHORIZATION_FAILED
    assert service.filesystem(WORKSPACE_ID).read_bytes(
        "src/example.py",
        offset=0,
        limit=1024,
    ) == b"one\nTWO\nthree\n"

    for invalid_path in ("/etc/passwd", "../outside.py", "a/../b.py"):
        with pytest.raises(SandboxServiceError) as invalid:
            service.apply_patch(
                WORKSPACE_ID,
                path=invalid_path,
                patch="@@ -1,1 +1,1 @@\n-one\n+changed\n",
                quota_bytes=1024 * 1024,
            )
        assert invalid.value.code is SandboxErrorCode.INVALID_PATH

    lock = service.acquire_workspace_write(WORKSPACE_ID)
    try:
        with pytest.raises(SandboxServiceError) as busy:
            service.apply_patch(
                WORKSPACE_ID,
                path="src/example.py",
                patch="@@ -1,1 +1,1 @@\n-one\n+changed\n",
                quota_bytes=1024 * 1024,
            )
    finally:
        lock.release()
    assert busy.value.code is SandboxErrorCode.SANDBOX_BUSY


def test_http_backend_uses_fixed_workspace_patch_endpoint(tmp_path):
    token_file = tmp_path / "client.token"
    token_file.write_text("t" * 64, encoding="ascii")
    token_file.chmod(0o640)
    observed = {}

    def handler(request):
        observed.update({
            "method": request.method,
            "path": request.url.path,
            "authorization": request.headers.get("Authorization"),
            "body": request.read().decode(),
        })
        return httpx.Response(
            200,
            json={
                "status": "success",
                "summary": "ok",
                "next_actions": [],
                "artifacts": [],
                "data": {},
            },
        )

    backend = HttpSandboxdBackend(
        socket_path="/unused.sock",
        token_file=str(token_file),
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://sandboxd",
        ),
    )
    backend.apply_patch({
        "workspace_id": WORKSPACE_ID,
        "path": "a.txt",
        "patch": "@@ -1 +1 @@\n-a\n+b\n",
        "quota_bytes": 1024 * 1024,
    })

    assert observed["method"] == "POST"
    assert observed["path"] == "/v1/files/apply-patch"
    assert observed["authorization"] == f"Bearer {'t' * 64}"
    assert '"workspace_id"' in observed["body"]


def test_sandboxd_workspace_patch_endpoint_applies_and_returns_safe_metadata(
    tmp_path,
):
    token, runtime = _runtime(tmp_path)
    runtime.workspace_files.layout.ensure_roots()
    runtime.workspace_files.ensure_workspace(WORKSPACE_ID)
    runtime.workspace_files.write_file(
        WORKSPACE_ID,
        path="hello.txt",
        content="hello\nworld\n",
        overwrite=False,
        quota_bytes=1024 * 1024,
    )

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/v1/files/apply-patch",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "workspace_id": WORKSPACE_ID,
                "path": "hello.txt",
                "patch": (
                    "@@ -1,2 +1,2 @@\n"
                    " hello\n"
                    "-world\n"
                    "+sandbox\n"
                ),
                "quota_bytes": 1024 * 1024,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["path"] == "hello.txt"
    assert body["data"]["hunks_applied"] == 1
    assert body["artifacts"][0]["ref"] == (
        "workspace://current/hello.txt"
    )
    assert str(tmp_path) not in response.text

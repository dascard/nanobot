import json

import httpx
import pytest

from core.sandbox.backend import FakeSandboxBackend, SandboxBackend
from core.sandbox.client import (
    HttpSandboxdAdminBackend,
    HttpSandboxdBackend,
    _read_token_file,
)
from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError


def test_fake_backend_implements_protocol_and_records_calls():
    backend = FakeSandboxBackend()
    backend.set_response("run", {
        "status": "success",
        "summary": "执行完成",
        "next_actions": [],
        "artifacts": [],
        "data": {"run_id": "sbxrun_test"},
    })

    response = backend.run({"request_id": "req_test", "command": "true"})

    assert isinstance(backend, SandboxBackend)
    assert response["data"]["run_id"] == "sbxrun_test"
    assert backend.calls == [
        ("run", {"request_id": "req_test", "command": "true"}),
    ]


def test_http_backend_uses_bearer_and_never_puts_token_in_json(tmp_path):
    token = "t" * 64
    token_file = tmp_path / "client.token"
    token_file.write_text(token)
    token_file.chmod(0o640)
    captured = {}

    def handler(request):
        captured["authorization"] = request.headers["authorization"]
        captured["request_id"] = request.headers["x-nanobot-request-id"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "status": "success",
            "summary": "已创建",
            "next_actions": [],
            "artifacts": [],
            "data": {},
        })

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://sandboxd",
    )
    backend = HttpSandboxdBackend(
        socket_path="/run/nanobot-sandboxd/sandboxd.sock",
        token_file=str(token_file),
        client=client,
    )

    backend.ensure_workspace("00000000-0000-0000-0000-000000000001", request_id="req-1")

    assert captured["authorization"] == f"Bearer {token}"
    assert captured["request_id"] == "req-1"
    assert token not in json.dumps(captured["body"])


def test_admin_backend_uses_dedicated_workspace_ensure_endpoint(tmp_path):
    token = "a" * 64
    token_file = tmp_path / "admin-client.token"
    token_file.write_text(token, encoding="ascii")
    token_file.chmod(0o600)
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["authorization"] = request.headers["authorization"]
        captured["request_id"] = request.headers["x-nanobot-request-id"]
        return httpx.Response(200, json={
            "status": "success",
            "summary": "Workspace 已创建",
            "next_actions": [],
            "artifacts": [],
            "data": {},
        })

    backend = HttpSandboxdAdminBackend(
        socket_path="/run/nanobot-sandboxd/sandboxd.sock",
        token_file=str(token_file),
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://sandboxd",
        ),
    )

    backend.ensure_workspace(
        "00000000-0000-0000-0000-000000000001",
        request_id="admin-ensure-request-1",
    )

    assert captured == {
        "path": "/v1/admin/workspaces/ensure",
        "authorization": f"Bearer {token}",
        "request_id": "admin-ensure-request-1",
    }


def test_admin_quota_request_uses_long_running_timeout_budget(tmp_path):
    token_file = tmp_path / "admin-client.token"
    token_file.write_text("a" * 64, encoding="ascii")
    token_file.chmod(0o600)
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["timeout"] = request.extensions.get("timeout", {})
        return httpx.Response(200, json={
            "status": "success",
            "summary": "配额已应用",
            "next_actions": [],
            "artifacts": [],
            "data": {},
        })

    backend = HttpSandboxdAdminBackend(
        socket_path="/run/nanobot-sandboxd/sandboxd.sock",
        token_file=str(token_file),
        timeout_seconds=15,
        run_timeout_seconds=165,
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://sandboxd",
        ),
    )

    backend.apply_workspace_quota({
        "request_id": "admin-quota-request-1",
        "workspace_id": "00000000-0000-0000-0000-000000000001",
        "project_id": 10000,
        "quota_bytes": 64 * 1024 * 1024,
        "generation": 1,
    })

    assert captured["path"] == "/v1/admin/workspaces/quota/apply"
    assert captured["timeout"]["read"] == 165


def test_http_backend_maps_stable_error_without_exposing_transport(tmp_path):
    token_file = tmp_path / "client.token"
    token_file.write_text("t" * 64)
    token_file.chmod(0o640)

    def handler(_request):
        return httpx.Response(507, json={
            "status": "error",
            "summary": "工作区空间配额已用完",
            "next_actions": [],
            "artifacts": [],
            "error": {
                "code": "workspace_quota_exceeded",
                "retryable": False,
                "hint": "停止重试",
                "stop": True,
            },
        })

    backend = HttpSandboxdBackend(
        socket_path="/secret/host/socket.sock",
        token_file=str(token_file),
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://sandboxd"),
    )

    with pytest.raises(SandboxServiceError) as raised:
        backend.write_file({"workspace_id": "id", "path": "a.txt", "content": "x"})

    assert raised.value.code is SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED
    assert "/secret/host" not in str(raised.value)


def test_client_token_file_rejects_symlink_insecure_mode_and_control_bytes(tmp_path):
    token_file = tmp_path / "client.token"
    token_file.write_bytes(b"t" * 64)
    token_file.chmod(0o644)
    with pytest.raises(SandboxServiceError):
        _read_token_file(token_file)

    token_file.write_bytes(b"t" * 63 + b"\x01")
    token_file.chmod(0o640)
    with pytest.raises(SandboxServiceError):
        _read_token_file(token_file)

    token_file.write_bytes(b"t" * 64 + b"\n")
    token_file.chmod(0o640)
    symlink = tmp_path / "client-link.token"
    symlink.symlink_to(token_file)
    with pytest.raises(SandboxServiceError):
        _read_token_file(symlink)

    assert _read_token_file(token_file) == "t" * 64

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


def test_text_read_falls_back_to_read_only_preview_during_rolling_upgrade(
    tmp_path,
):
    token_file = tmp_path / "client.token"
    token_file.write_text("t" * 64, encoding="ascii")
    token_file.chmod(0o640)
    requested_paths = []

    def handler(request):
        requested_paths.append(request.url.path)
        if request.url.path == "/v1/files/read-text":
            return httpx.Response(404, json={"detail": "Not Found"})
        assert request.url.path == "/v1/files/read"
        return httpx.Response(200, json={
            "status": "success",
            "summary": "文件读取完成",
            "next_actions": [],
            "artifacts": [],
            "data": {
                "protocol_version": "workspace.v2",
                "path": "README.md",
                "size_bytes": 25,
                "content": "     1\t第一行\n     2\t第二行",
                "binary": False,
                "line_truncated": False,
                "output_truncated": False,
                "eof": True,
            },
        })

    backend = HttpSandboxdBackend(
        socket_path="/run/nanobot-sandboxd/sandboxd.sock",
        token_file=str(token_file),
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://sandboxd",
        ),
    )

    result = backend.read_text_file({
        "workspace_id": "00000000-0000-0000-0000-000000000001",
        "path": "README.md",
        "cwd": "",
    })

    assert requested_paths == ["/v1/files/read-text", "/v1/files/read"]
    assert result["data"]["content"] == "第一行\n第二行"
    assert result["data"]["editable"] is False
    assert result["data"]["preview_only"] is True
    assert result["data"]["preview_truncated"] is False
    assert result["data"]["sha256"] == ""
    assert "只读兼容预览" in result["data"]["preview_notice"]


def test_text_read_does_not_hide_declared_file_errors(tmp_path):
    token_file = tmp_path / "client.token"
    token_file.write_text("t" * 64, encoding="ascii")
    token_file.chmod(0o640)
    requested_paths = []

    def handler(request):
        requested_paths.append(request.url.path)
        return httpx.Response(415, json={
            "status": "error",
            "summary": "在线编辑只支持 UTF-8 文本文件",
            "next_actions": [],
            "artifacts": [],
            "error": {
                "code": "unsupported_file_type",
                "retryable": False,
                "hint": "",
                "stop": True,
            },
        })

    backend = HttpSandboxdBackend(
        socket_path="/run/nanobot-sandboxd/sandboxd.sock",
        token_file=str(token_file),
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://sandboxd",
        ),
    )

    with pytest.raises(SandboxServiceError) as raised:
        backend.read_text_file({
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "path": "image.bin",
            "cwd": "",
        })

    assert raised.value.code is SandboxErrorCode.UNSUPPORTED_FILE_TYPE
    assert requested_paths == ["/v1/files/read-text"]


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


def test_admin_lease_methods_use_admin_paths_token_and_fixed_payloads(
    tmp_path,
):
    admin_token = "a" * 64
    token_file = tmp_path / "admin-client.token"
    token_file.write_text(admin_token, encoding="ascii")
    token_file.chmod(0o600)
    captured = []

    def handler(request):
        captured.append({
            "method": request.method,
            "path": request.url.path,
            "authorization": request.headers["authorization"],
            "request_id": request.headers["x-nanobot-request-id"],
            "body": (
                json.loads(request.content)
                if request.content
                else None
            ),
            "timeout": request.extensions.get("timeout", {}),
        })
        return httpx.Response(200, json={
            "status": "success",
            "summary": "ok",
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
    lease_id = "sbxlease_admin_client"

    backend.list_leases()
    backend.stop_lease(
        lease_id,
        request_id="admin_lease_stop_request",
    )
    backend.destroy_lease(
        lease_id,
        request_id="admin_lease_destroy_request",
    )
    backend.recreate_lease(
        lease_id,
        request_id="admin_lease_recreate_request",
    )
    backend.terminate_all_leases(
        request_id="admin_terminate_all_request",
        reason="kill_switch",
    )

    assert [
        (item["method"], item["path"], item["body"])
        for item in captured
    ] == [
        ("GET", "/v1/admin/leases", None),
        (
            "POST",
            f"/v1/admin/leases/{lease_id}/stop",
            {"request_id": "admin_lease_stop_request"},
        ),
        (
            "DELETE",
            f"/v1/admin/leases/{lease_id}",
            {"request_id": "admin_lease_destroy_request"},
        ),
        (
            "POST",
            f"/v1/admin/leases/{lease_id}/recreate",
            {"request_id": "admin_lease_recreate_request"},
        ),
        (
            "POST",
            "/v1/admin/leases/terminate-all",
            {
                "request_id": "admin_terminate_all_request",
                "reason": "kill_switch",
            },
        ),
    ]
    assert all(
        item["authorization"] == f"Bearer {admin_token}"
        for item in captured
    )
    assert captured[3]["timeout"]["read"] == 165


def test_process_http_methods_use_fixed_paths_headers_and_payloads(tmp_path):
    normal_token = "n" * 64
    admin_token = "a" * 64
    normal_token_file = tmp_path / "client.token"
    admin_token_file = tmp_path / "admin-client.token"
    normal_token_file.write_text(normal_token, encoding="ascii")
    admin_token_file.write_text(admin_token, encoding="ascii")
    normal_token_file.chmod(0o600)
    admin_token_file.chmod(0o600)
    captured = []

    def handler(request):
        captured.append({
            "method": request.method,
            "url": str(request.url),
            "authorization": request.headers["authorization"],
            "request_id": request.headers["x-nanobot-request-id"],
            "body": (
                json.loads(request.content)
                if request.content
                else None
            ),
            "timeout": request.extensions.get("timeout", {}),
        })
        return httpx.Response(200, json={
            "status": "success",
            "summary": "ok",
            "next_actions": [],
            "artifacts": [],
            "data": {},
        })

    transport = httpx.MockTransport(handler)
    normal = HttpSandboxdBackend(
        socket_path="/run/nanobot-sandboxd/sandboxd.sock",
        token_file=str(normal_token_file),
        timeout_seconds=15,
        run_timeout_seconds=165,
        client=httpx.Client(
            transport=transport,
            base_url="http://sandboxd",
        ),
    )
    admin = HttpSandboxdAdminBackend(
        socket_path="/run/nanobot-sandboxd/sandboxd.sock",
        token_file=str(admin_token_file),
        client=httpx.Client(
            transport=transport,
            base_url="http://sandboxd",
        ),
    )
    process_id = "sbxrun_http_client_process"
    start_payload = {
        "request_id": process_id,
        "command": "python -m pytest tests/ -v",
        "cwd": "repos/project",
        "yield_time_ms": 10_000,
        "timeout_seconds": 300,
    }
    stdin_payload = {
        "request_id": "sbxstdin_http_client_process",
        "chars": "q\n",
    }

    normal.start_process("sbxlease_http_client", start_payload)
    normal.get_process(process_id, cursor="v1:12:3")
    normal.write_process_stdin(process_id, stdin_payload)
    normal.terminate_process(
        process_id,
        request_id="sbxterm_http_client_process",
    )
    admin.list_processes()

    assert [
        {
            "method": item["method"],
            "url": item["url"],
            "authorization": item["authorization"],
            "body": item["body"],
        }
        for item in captured
    ] == [
        {
            "method": "POST",
            "url": (
                "http://sandboxd/v1/leases/"
                "sbxlease_http_client/processes"
            ),
            "authorization": f"Bearer {normal_token}",
            "body": start_payload,
        },
        {
            "method": "GET",
            "url": (
                "http://sandboxd/v1/processes/"
                f"{process_id}?cursor=v1%3A12%3A3"
            ),
            "authorization": f"Bearer {normal_token}",
            "body": None,
        },
        {
            "method": "POST",
            "url": f"http://sandboxd/v1/processes/{process_id}/stdin",
            "authorization": f"Bearer {normal_token}",
            "body": stdin_payload,
        },
        {
            "method": "POST",
            "url": (
                f"http://sandboxd/v1/processes/{process_id}/terminate"
            ),
            "authorization": f"Bearer {normal_token}",
            "body": {
                "request_id": "sbxterm_http_client_process",
            },
        },
        {
            "method": "GET",
            "url": "http://sandboxd/v1/admin/processes",
            "authorization": f"Bearer {admin_token}",
            "body": None,
        },
    ]
    assert captured[0]["request_id"] == process_id
    assert captured[0]["timeout"]["read"] == 165
    assert captured[1]["request_id"]
    assert captured[2]["request_id"] == stdin_payload["request_id"]
    assert (
        captured[3]["request_id"]
        == "sbxterm_http_client_process"
    )
    assert captured[4]["request_id"]


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

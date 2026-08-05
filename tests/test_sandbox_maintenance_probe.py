from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from sandboxd import maintenance_probe
from sandboxd.maintenance_probe import (
    MaintenanceProbeError,
    assert_quiesced,
    query_execution_state,
)


def _response(data: dict[str, object]) -> bytes:
    body = json.dumps(
        {
            "status": "success",
            "summary": "ok",
            "next_actions": [],
            "artifacts": [],
            "data": data,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return (
        b"HTTP/1.1 200 OK\r\n"
        b"content-type: application/json\r\n"
        + f"content-length: {len(body)}\r\n".encode("ascii")
        + b"connection: close\r\n\r\n"
        + body
    )


def _serve_once(socket_path: Path, response: bytes, observed: list[bytes]) -> threading.Thread:
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.listen(1)

    def serve() -> None:
        try:
            connection, _address = server.accept()
            with connection:
                request = bytearray()
                while b"\r\n\r\n" not in request:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    request.extend(chunk)
                observed.append(bytes(request))
                connection.sendall(response)
        finally:
            server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread


def test_probe_reads_authenticated_uds_quiescence_fact(tmp_path):
    token = "a" * 64
    token_file = tmp_path / "admin.token"
    token_file.write_text(token, encoding="ascii")
    token_file.chmod(0o600)
    socket_path = tmp_path / "sandboxd.sock"
    observed: list[bytes] = []
    thread = _serve_once(
        socket_path,
        _response({
            "quiesced": True,
            "active_container_count": 0,
            "verified_managed_container_count": 0,
            "ambiguous_container_count": 0,
            "active_run_reservation_count": 0,
        }),
        observed,
    )

    state = query_execution_state(
        socket_path=socket_path,
        token_file=token_file,
    )
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert state["quiesced"] is True
    assert len(observed) == 1
    assert observed[0].startswith(
        b"GET /v1/admin/execution-state HTTP/1.1\r\n"
    )
    assert f"Authorization: Bearer {token}\r\n".encode("ascii") in observed[0]
    assert b"/var/run/docker.sock" not in observed[0]


def test_probe_rejects_active_or_inconsistent_execution_facts(tmp_path, monkeypatch):
    monkeypatch.setattr(
        maintenance_probe,
        "query_execution_state",
        lambda **_kwargs: {
            "quiesced": False,
            "active_container_count": 1,
            "verified_managed_container_count": 1,
            "ambiguous_container_count": 0,
            "active_run_reservation_count": 0,
        },
    )
    with pytest.raises(MaintenanceProbeError, match="仍有 Sandbox 执行资源"):
        assert_quiesced(
            socket_path=tmp_path / "unused.sock",
            token_file=tmp_path / "unused.token",
        )

    with pytest.raises(MaintenanceProbeError, match="计数不一致"):
        maintenance_probe._parse_execution_state_response(_response({
            "quiesced": False,
            "active_container_count": 2,
            "verified_managed_container_count": 1,
            "ambiguous_container_count": 0,
            "active_run_reservation_count": 0,
        }))


def test_probe_rejects_symlink_socket_before_sending_token(tmp_path):
    token_file = tmp_path / "admin.token"
    token_file.write_text("a" * 64, encoding="ascii")
    token_file.chmod(0o600)
    target = tmp_path / "not-a-socket"
    target.write_text("x", encoding="utf-8")
    socket_path = tmp_path / "sandboxd.sock"
    socket_path.symlink_to(target)

    with pytest.raises(MaintenanceProbeError, match="Socket 类型无效"):
        query_execution_state(
            socket_path=socket_path,
            token_file=token_file,
        )

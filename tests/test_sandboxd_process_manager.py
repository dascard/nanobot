from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from sandboxd.app import SandboxRuntime, create_app
from sandboxd.auth import TokenAuthenticator
from sandboxd.lease_reconciler import LeaseReconciler
from sandboxd.process_manager import LeaseProcessManager
from tests.test_sandboxd_lease_backend import (
    LEASE_ID,
    WORKSPACE_ID,
    _components,
    _ensure,
)


class _Handle:
    def __init__(self) -> None:
        self.items: queue.Queue[tuple[int, bytes] | None] = queue.Queue()
        self.running = True
        self.exit_code = 0
        self.writes: list[bytes] = []
        self.closed = False
        self._guard = threading.Lock()

    def frames(self):
        while True:
            item = self.items.get(timeout=5)
            if item is None:
                return
            yield item

    def inspect(self):
        with self._guard:
            return {
                "Running": self.running,
                "ExitCode": None if self.running else self.exit_code,
            }

    def write(self, payload: bytes):
        with self._guard:
            if self.closed:
                raise OSError("closed")
            self.writes.append(bytes(payload))
            return len(payload)

    def feed(self, stream: int, payload: bytes) -> None:
        self.items.put((stream, bytes(payload)))

    def finish(self, exit_code: int = 0) -> None:
        with self._guard:
            self.exit_code = int(exit_code)
            self.running = False
        self.items.put(None)

    def close(self) -> None:
        with self._guard:
            if self.closed:
                return
            self.closed = True
        self.items.put(None)


class _Adapter:
    def __init__(self) -> None:
        self.handles: dict[str, _Handle] = {}
        self.calls: list[dict] = []

    def start(self, **kwargs):
        process_id = str(kwargs["process_id"])
        handle = _Handle()
        self.handles[process_id] = handle
        self.calls.append(dict(kwargs))
        return handle


def _manager_components(tmp_path, *, config_transform=None):
    (
        config,
        workspace_files,
        asset_files,
        docker,
        store,
        backend,
    ) = _components(tmp_path)
    _ensure(backend, config)
    manager_config = (
        config_transform(config)
        if config_transform is not None
        else config
    )
    adapter = _Adapter()
    manager = LeaseProcessManager(
        manager_config,
        lease_backend=backend,
        lease_store=store,
        exec_adapter=adapter,
    )
    return (
        manager_config,
        workspace_files,
        asset_files,
        docker,
        store,
        backend,
        adapter,
        manager,
    )


def _wait_for_status(
    manager: LeaseProcessManager,
    process_id: str,
    expected: str,
    *,
    timeout: float = 3,
):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = manager.get(process_id)
        if data["execution_status"] == expected:
            return data
        time.sleep(0.01)
    pytest.fail(f"进程未进入预期状态：{expected}")


def test_process_output_poll_stdin_and_two_commands_share_one_lease(
    tmp_path,
):
    (
        _config,
        _workspace,
        _assets,
        docker,
        _store,
        _backend,
        adapter,
        manager,
    ) = _manager_components(tmp_path)
    first_id = "sbxrun_process_first"
    second_id = "sbxrun_process_second"

    first = manager.start(
        lease_id=LEASE_ID,
        request_id=first_id,
        command="python -m http.server 8000",
        yield_time_ms=0,
        timeout_seconds=30,
    )
    second = manager.start(
        lease_id=LEASE_ID,
        request_id=second_id,
        command="curl http://127.0.0.1:8000/",
        yield_time_ms=0,
        timeout_seconds=30,
    )

    assert first["execution_status"] == "running"
    assert second["execution_status"] == "running"
    assert len(docker.containers.create_calls) == 1
    assert {
        item["process_id"] for item in second["active_processes"]
    } == {first_id, second_id}
    assert adapter.calls[0]["shell_path"] == "/bin/bash"
    assert adapter.calls[0]["cwd"] == ""

    adapter.handles[first_id].feed(1, b"ready\n")
    adapter.handles[first_id].feed(2, b"warning\n")
    deadline = time.monotonic() + 2
    initial = {}
    while time.monotonic() < deadline:
        initial = manager.get(first_id)
        if initial["stdout_delta"] == "ready\n":
            break
        time.sleep(0.01)
    assert initial["stderr_delta"] == "warning\n"
    cursor = initial["next_cursor"]

    adapter.handles[first_id].feed(1, b"next\n")
    deadline = time.monotonic() + 2
    delta = {}
    while time.monotonic() < deadline:
        delta = manager.get(first_id, cursor=cursor)
        if delta["stdout_delta"] == "next\n":
            break
        time.sleep(0.01)
    assert delta["stderr_delta"] == ""

    written = manager.write_stdin(
        first_id,
        request_id="stdin_process_first",
        chars="q\n",
    )
    repeated = manager.write_stdin(
        first_id,
        request_id="stdin_process_first",
        chars="q\n",
    )
    assert written == repeated
    assert written["written_bytes"] == 2
    assert adapter.handles[first_id].writes == [b"q\n"]

    adapter.handles[second_id].finish(0)
    completed = _wait_for_status(manager, second_id, "completed")
    assert completed["exit_code"] == 0
    assert completed["termination_reason"] == "completed"
    assert completed["process_state"] == "exited"
    assert {
        item["process_id"] for item in completed["active_processes"]
    } == {first_id}


def test_terminate_one_process_recycles_whole_lease_and_preserves_data(
    tmp_path,
):
    (
        _config,
        workspace_files,
        _assets,
        _docker,
        store,
        _backend,
        _adapter,
        manager,
    ) = _manager_components(tmp_path)
    workspace_path = workspace_files.layout.workspace_data_dir(
        WORKSPACE_ID
    )
    runtime_path = workspace_files.layout.ensure_runtime(WORKSPACE_ID)
    (workspace_path / "kept.txt").write_text("workspace", encoding="utf-8")
    (runtime_path / "kept.txt").write_text("runtime", encoding="utf-8")
    first_id = "sbxrun_terminate_first"
    second_id = "sbxrun_terminate_second"
    manager.start(
        lease_id=LEASE_ID,
        request_id=first_id,
        command="sleep 30",
        yield_time_ms=0,
        timeout_seconds=30,
    )
    manager.start(
        lease_id=LEASE_ID,
        request_id=second_id,
        command="sleep 30",
        yield_time_ms=0,
        timeout_seconds=30,
    )

    result = manager.terminate(
        first_id,
        request_id="terminate_process_first",
    )
    second = manager.get(second_id)

    assert result["execution_status"] == "cancelled"
    assert result["termination_reason"] == "cancelled"
    assert result["termination_scope"] == "lease"
    assert result["lease_recycled"] is True
    assert result["affected_process_ids"] == sorted(
        [first_id, second_id]
    )
    assert second["execution_status"] == "cancelled"
    assert second["process_state"] == "lost"
    assert store.get(LEASE_ID) is None
    assert (workspace_path / "kept.txt").read_text() == "workspace"
    assert (runtime_path / "kept.txt").read_text() == "runtime"


def test_clean_exec_create_failure_keeps_lease_but_unknown_start_recycles(
    tmp_path,
):
    (
        _config,
        _workspace_files,
        _assets,
        _docker,
        store,
        _backend,
        adapter,
        manager,
    ) = _manager_components(tmp_path)
    first_id = "sbxrun_start_failure_first"
    manager.start(
        lease_id=LEASE_ID,
        request_id=first_id,
        command="python -m http.server 8000",
        yield_time_ms=0,
        timeout_seconds=30,
    )

    def clean_failure(**_kwargs):
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "Docker 无法创建 Sandbox Lease 进程",
            retryable=True,
            stop=False,
        )

    adapter.start = clean_failure
    with pytest.raises(SandboxServiceError):
        manager.start(
            lease_id=LEASE_ID,
            request_id="sbxrun_start_failure_clean",
            command="true",
            yield_time_ms=0,
            timeout_seconds=30,
        )

    assert store.get(LEASE_ID) is not None
    assert manager.get(first_id)["execution_status"] == "running"

    def unknown_failure(**_kwargs):
        error = SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "Docker 无法启动 Sandbox Lease 进程",
            retryable=True,
            stop=False,
        )
        error.exec_state_unknown = True
        raise error

    adapter.start = unknown_failure
    with pytest.raises(SandboxServiceError):
        manager.start(
            lease_id=LEASE_ID,
            request_id="sbxrun_start_failure_unknown",
            command="true",
            yield_time_ms=0,
            timeout_seconds=30,
        )

    assert store.get(LEASE_ID) is None
    recycled = manager.get(first_id)
    assert recycled["lease_recycled"] is True
    assert recycled["termination_reason"] == "lease_recycled"
    assert recycled["process_state"] == "lost"


def test_timeout_and_output_hard_limit_recycle_entire_lease(tmp_path):
    (
        config,
        _workspace,
        _assets,
        _docker,
        _store,
        backend,
        adapter,
        manager,
    ) = _manager_components(tmp_path)
    timeout_id = "sbxrun_timeout_process"
    manager.start(
        lease_id=LEASE_ID,
        request_id=timeout_id,
        command="sleep 30",
        yield_time_ms=0,
        timeout_seconds=1,
    )

    timed_out = _wait_for_status(
        manager,
        timeout_id,
        "failed",
        timeout=3,
    )
    assert timed_out["termination_reason"] == "execution_timeout"
    assert timed_out["lease_recycled"] is True
    repeated = manager.start(
        lease_id=LEASE_ID,
        request_id=timeout_id,
        command="sleep 30",
        yield_time_ms=0,
        timeout_seconds=1,
    )
    assert repeated["execution_status"] == "failed"
    assert repeated["termination_reason"] == "execution_timeout"
    assert repeated["lease_recycled"] is True
    assert list(adapter.handles) == [timeout_id]

    _ensure(
        backend,
        config,
        request_id="lease_request_output",
        lease_id="sbxlease_output_limit",
    )
    limited_config = replace(
        config,
        stdout_limit_bytes=4,
        stderr_limit_bytes=4,
        hard_output_limit_bytes=8,
    ).validated()
    limited_manager = LeaseProcessManager(
        limited_config,
        lease_backend=backend,
        lease_store=backend.store,
        exec_adapter=adapter,
    )
    output_id = "sbxrun_output_limit"
    limited_manager.start(
        lease_id="sbxlease_output_limit",
        request_id=output_id,
        command="yes",
        yield_time_ms=0,
        timeout_seconds=30,
    )
    adapter.handles[output_id].feed(1, b"123456789")

    limited = _wait_for_status(
        limited_manager,
        output_id,
        "failed",
    )
    assert limited["termination_reason"] == "output_limit_exceeded"
    assert limited["stdout_bytes"] == 9
    assert limited["stdout_truncated"] is True
    assert len(limited["stdout_delta"].encode()) <= 4


def test_lease_oom_recycles_all_processes_and_preserves_workspace_runtime(
    tmp_path,
):
    (
        _config,
        workspace_files,
        _assets,
        docker,
        store,
        _backend,
        adapter,
        manager,
    ) = _manager_components(tmp_path)
    workspace_path = workspace_files.layout.workspace_data_dir(
        WORKSPACE_ID
    )
    runtime_path = workspace_files.layout.ensure_runtime(WORKSPACE_ID)
    (workspace_path / "oom-kept.txt").write_text(
        "workspace",
        encoding="utf-8",
    )
    (runtime_path / "oom-kept.txt").write_text(
        "runtime",
        encoding="utf-8",
    )
    first_id = "sbxrun_oom_first"
    second_id = "sbxrun_oom_second"
    manager.start(
        lease_id=LEASE_ID,
        request_id=first_id,
        command="python -c 'bytearray(10**9)'",
        yield_time_ms=0,
        timeout_seconds=30,
    )
    manager.start(
        lease_id=LEASE_ID,
        request_id=second_id,
        command="sleep 30",
        yield_time_ms=0,
        timeout_seconds=30,
    )

    container = docker.containers.values[0]
    container.attrs["State"]["OOMKilled"] = True
    container.attrs["State"]["Running"] = False
    adapter.handles[first_id].finish(137)

    first = _wait_for_status(manager, first_id, "failed")
    second = _wait_for_status(manager, second_id, "failed")
    expected = sorted([first_id, second_id])
    assert first["termination_reason"] == "lease_oom"
    assert first["lease_recycled"] is True
    assert first["affected_process_ids"] == expected
    assert second["termination_reason"] == "lease_oom"
    assert second["lease_recycled"] is True
    assert second["affected_process_ids"] == expected
    assert store.get(LEASE_ID) is None
    assert container.removed is True
    assert (workspace_path / "oom-kept.txt").read_text() == "workspace"
    assert (runtime_path / "oom-kept.txt").read_text() == "runtime"


def test_workspace_quiescing_rejects_new_process_without_blocking_other_state(
    tmp_path,
):
    (
        _config,
        workspace_files,
        _assets,
        _docker,
        _store,
        _backend,
        _adapter,
        manager,
    ) = _manager_components(tmp_path)

    with workspace_files.maintenance.quota_maintenance(
        WORKSPACE_ID,
        generation=2,
    ):
        with pytest.raises(SandboxServiceError) as rejected:
            manager.start(
                lease_id=LEASE_ID,
                request_id="sbxrun_quiesced_process",
                command="true",
                yield_time_ms=0,
                timeout_seconds=30,
            )

    assert rejected.value.code is SandboxErrorCode.SANDBOX_BUSY


def test_process_http_contract_is_strict_and_admin_facts_have_no_bodies(
    tmp_path,
):
    (
        config,
        workspace_files,
        asset_files,
        docker,
        store,
        backend,
        adapter,
        manager,
    ) = _manager_components(tmp_path)
    normal_token = "n" * 64
    admin_token = "a" * 64
    config.token_file.write_text(normal_token, encoding="ascii")
    config.token_file.chmod(0o600)
    config.admin_token_file.write_text(admin_token, encoding="ascii")
    config.admin_token_file.chmod(0o600)

    class _ControlDockerBackend:
        client = docker

        def ready(self):
            return {
                "docker": True,
                "catalog_generation": (
                    config.profile_catalog.catalog_generation
                ),
                "policy_sha256": config.profile_catalog.policy_sha256,
                "profiles": {},
            }

    runtime = SandboxRuntime(
        config=config,
        authenticator=TokenAuthenticator(
            config.token_file,
            config.client_token_path,
        ),
        workspace_files=workspace_files,
        asset_files=asset_files,
        docker_backend=_ControlDockerBackend(),
        admin_authenticator=TokenAuthenticator(
            config.admin_token_file,
            config.admin_client_token_path,
        ),
        lease_store=store,
        lease_backend=backend,
        lease_reconciler=LeaseReconciler(
            backend,
            store,
            interval_seconds=3600,
        ),
        process_manager=manager,
    )
    normal_headers = {
        "Authorization": f"Bearer {normal_token}",
        "X-Nanobot-Request-ID": "sbxrun_http_process",
    }

    with TestClient(create_app(runtime)) as client:
        catalog = config.profile_catalog
        ensured = client.post(
            "/v1/leases/ensure",
            json={
                "request_id": "lease_http_process",
                "lease_id": "sbxlease_http_process",
                "workspace_id": WORKSPACE_ID,
                "profile_id": "developer",
                "catalog_generation": catalog.catalog_generation,
                "policy_sha256": catalog.policy_sha256,
                "quota_generation": 1,
            },
            headers={
                "Authorization": f"Bearer {normal_token}",
                "X-Nanobot-Request-ID": "lease_http_process",
            },
        )
        assert ensured.status_code == 200
        injected = client.post(
            "/v1/leases/sbxlease_http_process/processes",
            json={
                "request_id": "sbxrun_http_process",
                "command": "sleep 30",
                "image": "attacker/image",
            },
            headers=normal_headers,
        )
        assert injected.status_code == 400
        assert "attacker" not in injected.text

        started = client.post(
            "/v1/leases/sbxlease_http_process/processes",
            json={
                "request_id": "sbxrun_http_process",
                "command": "sleep 30",
                "yield_time_ms": 0,
                "timeout_seconds": 30,
            },
            headers=normal_headers,
        )
        assert started.status_code == 200
        adapter.handles["sbxrun_http_process"].feed(1, b"secret-output")

        facts = client.get(
            "/v1/admin/processes",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert facts.status_code == 200
        serialized = json.dumps(facts.json(), sort_keys=True)
        assert "secret-output" not in serialized
        assert '"command"' not in serialized
        assert '"stdout"' not in serialized
        assert '"stderr"' not in serialized

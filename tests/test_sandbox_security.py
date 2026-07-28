"""真实 Docker 隔离矩阵；默认不运行，必须显式设置环境开关。"""

from __future__ import annotations

import copy
import os
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from sandboxd.config import SandboxdConfig
from sandboxd.docker_backend import (
    CONTAINER_PREFIX,
    MANAGED_BY_LABEL,
    MANAGED_LABEL,
    LocalDockerBackend,
    managed_container,
)
from sandboxd.filesystem import AssetFileService, WorkspaceFileService


pytestmark = pytest.mark.skipif(
    os.environ.get("NANOBOT_RUN_DOCKER_TESTS") != "1",
    reason="需要 NANOBOT_RUN_DOCKER_TESTS=1 才运行真实 Docker 隔离测试",
)


WORKSPACE_A = "00000000-0000-0000-0000-000000000001"
WORKSPACE_B = "00000000-0000-0000-0000-000000000002"


class _RecordingContainer:
    def __init__(self, container, snapshots):
        self._container = container
        self._snapshots = snapshots

    def __getattr__(self, name):
        return getattr(self._container, name)

    @property
    def attrs(self):
        return self._container.attrs

    @property
    def name(self):
        return self._container.name

    def reload(self):
        return self._container.reload()

    def start(self):
        self._container.start()
        self._container.reload()
        self._snapshots.append(copy.deepcopy(self._container.attrs))


class _RecordingContainers:
    def __init__(self, containers, snapshots):
        self._containers = containers
        self._snapshots = snapshots

    def create(self, **kwargs):
        return _RecordingContainer(
            self._containers.create(**kwargs),
            self._snapshots,
        )

    def __getattr__(self, name):
        return getattr(self._containers, name)


class _RecordingDockerClient:
    def __init__(self, client):
        self._client = client
        self.snapshots = []
        self.containers = _RecordingContainers(client.containers, self.snapshots)
        self.images = client.images

    def __getattr__(self, name):
        return getattr(self._client, name)


def _security_options(info: dict) -> set[str]:
    values = set()
    for value in info.get("SecurityOptions") or []:
        if isinstance(value, dict):
            values.add(str(value.get("Name") or value.get("name") or ""))
        else:
            values.add(str(value))
    return values


def _assert_host_prerequisites(client, image_reference: str) -> tuple[str, int, int]:
    if not client.ping():
        pytest.fail("Docker Engine ping 失败")
    info = client.info()
    options = _security_options(info)
    if not any("seccomp" in value.lower() for value in options):
        pytest.fail("宿主 Docker 未启用 seccomp，真实 Sandbox 测试失败关闭")
    if not any("apparmor" in value.lower() for value in options):
        pytest.fail("宿主 Docker 未启用 AppArmor，真实 Sandbox 测试失败关闭")

    profile_path = Path("/sys/kernel/security/apparmor/profiles")
    try:
        profiles = profile_path.read_text(encoding="utf-8")
    except OSError as exc:
        pytest.fail(f"无法读取 AppArmor profiles：{type(exc).__name__}")
    if not any(
        line.startswith("nanobot-sandbox-restricted ")
        for line in profiles.splitlines()
    ):
        pytest.fail("nanobot-sandbox-restricted AppArmor profile 尚未加载")
    if os.geteuid() != 0:
        pytest.fail("真实 Sandbox 测试必须以 root 运行，以便安全设置 Workspace UID/GID")

    try:
        image = client.images.get(image_reference)
    except Exception as exc:
        pytest.fail(f"固定 Sandbox 镜像不存在：{type(exc).__name__}")
    image_id = str(image.id or "").lower()
    configured_user = str(image.attrs.get("Config", {}).get("User") or "")
    if configured_user != "10001:10001":
        pytest.fail("Sandbox 镜像默认用户不是固定 10001:10001")
    return image_id, 10001, 10001


def _run(
    backend: LocalDockerBackend,
    command: str,
    *,
    workspace_id: str = WORKSPACE_A,
    timeout_seconds: int = 20,
    run_id: str | None = None,
):
    suffix = uuid.uuid4().hex[:16]
    actual_run_id = run_id or f"sbxrun_security_{suffix}"
    return backend.execute(
        request_id=f"sbxreq_security_{suffix}",
        run_id=actual_run_id,
        workspace_id=workspace_id,
        command=command,
        timeout_seconds=timeout_seconds,
        quota_bytes=64 * 1024 * 1024,
    )


def _assert_inspect_security(snapshot: dict) -> None:
    host = snapshot.get("HostConfig") or {}
    config = snapshot.get("Config") or {}
    mounts = snapshot.get("Mounts") or []
    destinations = {str(item.get("Destination") or "") for item in mounts}
    tmpfs = host.get("Tmpfs") or {}
    security_opt = {str(item) for item in host.get("SecurityOpt") or []}

    assert config.get("User") == "10001:10001"
    assert host.get("ReadonlyRootfs") is True
    assert host.get("NetworkMode") == "none"
    assert set(host.get("CapDrop") or []) == {"ALL"}
    assert host.get("Privileged") is False
    assert host.get("PidsLimit") == 128
    assert host.get("Memory") == 512 * 1024 * 1024
    assert host.get("MemorySwap") == 512 * 1024 * 1024
    assert host.get("NanoCpus") == 1_000_000_000
    assert "no-new-privileges" in security_opt
    assert "apparmor=nanobot-sandbox-restricted" in security_opt
    assert snapshot.get("AppArmorProfile") == "nanobot-sandbox-restricted"
    assert destinations == {"/workspace", "/inputs", "/runtime"}
    assert set(tmpfs) == {"/tmp"}
    assert all(item.get("Destination") != "/var/run/docker.sock" for item in mounts)
    assert not host.get("Devices")
    assert not host.get("PortBindings")
    assert not host.get("ExtraHosts")
    assert host.get("PidMode") in {"", None}
    assert host.get("IpcMode") == "private"
    assert host.get("UTSMode") in {"", None}
    assert "size=134217728" in str(tmpfs["/tmp"])
    assert "noexec" in str(tmpfs["/tmp"])


def test_real_docker_security_matrix(tmp_path):
    """一次运行完整矩阵，任一安全门禁缺失都使整项失败。"""

    import docker

    if not callable(getattr(docker, "from_env", None)):
        pytest.fail(
            "当前解释器未安装 Docker SDK；"
            "请先安装 requirements-sandbox-smoke.lock 或 requirements-test.lock",
        )

    image_reference = os.environ.get(
        "NANOBOT_SANDBOX_TEST_IMAGE",
        "nanobot-sandbox-python:poc-20260720",
    )
    raw_client = docker.from_env(timeout=150)
    preexisting_ids = {
        container.id for container in raw_client.containers.list(all=True)
    }
    image_id, uid, gid = _assert_host_prerequisites(raw_client, image_reference)
    recording_client = _RecordingDockerClient(raw_client)
    data_root = tmp_path / "sandbox-data"
    config = SandboxdConfig(
        data_root=data_root,
        socket_path=tmp_path / "sandboxd.sock",
        token_file=tmp_path / "sandboxd.token",
        client_token_path=tmp_path / "client.token",
        image_reference=image_reference,
        image_allowlist=(image_id,),
        apparmor_profile="nanobot-sandbox-restricted",
        workspace_uid=uid,
        workspace_gid=gid,
        disk_min_free_bytes=0,
    ).validated()
    workspace_files = WorkspaceFileService(config)
    asset_files = AssetFileService(config)
    backend = LocalDockerBackend(
        config,
        docker_client=recording_client,
        workspace_files=workspace_files,
        asset_files=asset_files,
    )
    workspace_files.layout.ensure_roots()

    control_container = None
    try:
        ready = backend.ready()
        assert ready["image_id"] == image_id
        assert ready["apparmor_profile"] == "nanobot-sandbox-restricted"

        workspace_files.ensure_workspace(WORKSPACE_A)
        workspace_files.write_file(
            WORKSPACE_A,
            path="tool-created/input.txt",
            content="file-tool",
            overwrite=False,
            quota_bytes=64 * 1024 * 1024,
        )
        tool_created_root = (
            workspace_files.layout.workspace_data_dir(WORKSPACE_A)
            / "tool-created"
        )
        assert tool_created_root.stat().st_uid == uid
        assert tool_created_root.stat().st_gid == gid
        assert (tool_created_root / "input.txt").stat().st_uid == uid
        assert (tool_created_root / "input.txt").stat().st_gid == gid

        baseline = _run(
            backend,
            """
set -eu
test "$(id -u)" = "10001"
! touch /etc/nanobot-sandbox-write-test 2>/dev/null
test "$(cat /workspace/tool-created/input.txt)" = "file-tool"
printf 'exec-persist' > /workspace/tool-created/exec-persist.txt
printf 'persistent' > /workspace/persistent.txt
test ! -e /var/run/docker.sock
test ! -e /srv/nanobot
python - <<'PY'
import socket
try:
    with socket.socket() as s:
        s.settimeout(0.2)
        assert s.connect_ex(("1.1.1.1", 53)) != 0
except PermissionError:
    print("NETWORK_DENIED_BY_POLICY")
else:
    print("NETWORK_UNREACHABLE")
print("BASELINE_OK")
PY
""",
        )
        baseline_data = baseline["data"]
        assert baseline_data["termination_reason"] == "completed", {
            "exit_code": baseline_data.get("exit_code"),
            "termination_reason": baseline_data.get("termination_reason"),
            "stdout": baseline_data.get("stdout"),
            "stderr": baseline_data.get("stderr"),
        }
        assert "BASELINE_OK" in baseline["data"]["stdout"]
        assert recording_client.snapshots
        _assert_inspect_security(recording_client.snapshots[0])

        persisted = _run(backend, "cat /workspace/persistent.txt")
        assert persisted["data"]["stdout"] == "persistent"
        tool_persisted = _run(
            backend,
            "cat /workspace/tool-created/exec-persist.txt",
        )
        assert tool_persisted["data"]["stdout"] == "exec-persist"
        isolated = _run(
            backend,
            "test ! -e /workspace/persistent.txt && echo OWNER_B_ISOLATED",
            workspace_id=WORKSPACE_B,
        )
        assert "OWNER_B_ISOLATED" in isolated["data"]["stdout"]

        upload = asset_files.open_upload(media_type="text/plain")
        upload.write(b"authorized-input")
        published = upload.finish()
        staged_run_id = f"sbxrun_security_{uuid.uuid4().hex[:16]}"
        asset_files.stage(
            WORKSPACE_A,
            staged_run_id,
            [{
                "sha256": published.sha256,
                "storage_key": published.storage_key,
                "logical_name": "input.txt",
            }],
        )
        staged = _run(
            backend,
            """
set -eu
test "$(cat /inputs/input.txt)" = "authorized-input"
! printf x >> /inputs/input.txt 2>/dev/null
echo INPUTS_READ_ONLY
""",
            run_id=staged_run_id,
        )
        assert "INPUTS_READ_ONLY" in staged["data"]["stdout"]

        pid_limited = _run(
            backend,
            """
python - <<'PY'
import subprocess
processes = []
for _ in range(256):
    try:
        processes.append(subprocess.Popen(["sleep", "30"]))
    except OSError:
        break
print(f"CREATED={len(processes)}")
assert len(processes) < 128
for process in processes:
    process.terminate()
for process in processes:
    process.wait()
PY
""",
        )
        assert pid_limited["data"]["termination_reason"] == "completed"
        assert "CREATED=" in pid_limited["data"]["stdout"]

        oom = _run(
            backend,
            "python -c 'x = bytearray(700 * 1024 * 1024); print(len(x))'",
            timeout_seconds=30,
        )
        assert oom["data"]["termination_reason"] == "process_oom_killed"
        assert oom["data"]["oom_killed"] is True

        timed_out = _run(
            backend,
            "sh -c 'sleep 300 & wait'",
            timeout_seconds=1,
        )
        assert timed_out["data"]["termination_reason"] == "execution_timeout"

        output_limited = _run(
            backend,
            "python -c 'import sys; sys.stdout.write(\"x\" * (2 * 1024 * 1024))'",
            timeout_seconds=20,
        )
        assert output_limited["data"]["termination_reason"] == "output_limit_exceeded"
        assert output_limited["data"]["stdout_truncated"] is True

        before_pressure_creates = len(recording_client.snapshots)
        total_bytes = workspace_files.disk_guard.state().total_bytes
        pressure_config = replace(
            config,
            data_root=tmp_path / "pressure-data",
            disk_min_free_bytes=total_bytes + 1,
        )
        pressure_backend = LocalDockerBackend(
            pressure_config,
            docker_client=recording_client,
        )
        pressure_backend.workspace_files.layout.ensure_roots()
        with pytest.raises(SandboxServiceError) as pressure:
            _run(pressure_backend, "true")
        assert pressure.value.code is SandboxErrorCode.DISK_PRESSURE
        assert len(recording_client.snapshots) == before_pressure_creates

        control_name = f"nanobot-security-control-{uuid.uuid4().hex[:12]}"
        control_container = raw_client.containers.create(
            image=image_id,
            name=control_name,
            command=["/bin/sh", "-lc", "true"],
            labels={MANAGED_LABEL: "true", MANAGED_BY_LABEL: "sandboxd"},
        )
        assert managed_container(control_container) is False
        candidates = raw_client.containers.list(
            all=True,
            filters={
                "label": [
                    f"{MANAGED_LABEL}=true",
                    f"{MANAGED_BY_LABEL}=sandboxd",
                ],
                "name": CONTAINER_PREFIX,
            },
        )
        assert control_container.id not in {item.id for item in candidates}

        for container_id in preexisting_ids:
            assert raw_client.containers.get(container_id).id == container_id
    finally:
        if control_container is not None:
            try:
                control_container.remove(force=True, v=True)
            except Exception:
                pass
        test_managed = raw_client.containers.list(
            all=True,
            filters={
                "label": [
                    f"{MANAGED_LABEL}=true",
                    f"{MANAGED_BY_LABEL}=sandboxd",
                ],
                "name": "nanobot-sbx-sbxrun_security_",
            },
        )
        for container in test_managed:
            if container.id not in preexisting_ids and managed_container(container):
                container.remove(force=True, v=True)
        raw_client.close()

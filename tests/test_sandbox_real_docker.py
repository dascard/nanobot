"""Developer Lease、进程与数据连续性的真实 Docker 验收矩阵。"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from core.sandbox.profile_catalog import DEFAULT_PROFILE_MANIFEST_PATH
from sandboxd.config import SandboxdConfig
from sandboxd.docker_backend import LocalDockerBackend
from sandboxd.filesystem import AssetFileService, WorkspaceFileService
from sandboxd.lease_backend import (
    LEASE_CONTAINER_PREFIX,
    LeaseBackend,
    managed_lease_container,
)
from sandboxd.lease_reconciler import LeaseReconciler
from sandboxd.lease_store import LeaseStore
from sandboxd.network_policy import (
    MANAGED_BY_LABEL,
    MANAGED_LABEL,
    NETWORK_ROLE_LABEL,
    UPLINK_NETWORK_ROLE,
    NetworkPolicyManager,
)
from sandboxd.process_manager import LeaseProcessManager
from sandboxd.quota import ProjectQuotaManager


pytestmark = pytest.mark.skipif(
    os.environ.get("NANOBOT_RUN_DOCKER_TESTS") != "1",
    reason="需要 NANOBOT_RUN_DOCKER_TESTS=1 才运行真实 Docker Lease 测试",
)


def _safe_remove_tree(path: Path, *, expected_parent: Path) -> None:
    try:
        resolved_parent = expected_parent.resolve(strict=True)
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
        resolved.relative_to(resolved_parent)
    except FileNotFoundError:
        return
    if path.is_symlink() or not path.is_dir() or metadata.st_nlink < 1:
        raise AssertionError(f"拒绝清理不安全的测试目录：{path}")
    shutil.rmtree(resolved)


class _RealSandboxHarness:
    def __init__(self) -> None:
        import docker

        if os.geteuid() != 0:
            pytest.fail("真实 Developer Sandbox 测试必须以 root 运行")
        self.suffix = uuid4().hex[:12]
        self.data_root = Path(os.environ.get(
            "NANOBOT_SANDBOX_TEST_DATA_ROOT",
            "/srv/nanobot",
        ))
        self.manifest = Path(os.environ.get(
            "NANOBOT_SANDBOX_PROFILE_MANIFEST_FILE",
            os.fspath(DEFAULT_PROFILE_MANIFEST_PATH),
        ))
        self.quota_helper = Path(os.environ.get(
            "NANOBOT_SANDBOX_QUOTA_HELPER",
            os.fspath(
                Path(__file__).resolve().parents[1]
                / "scripts"
                / "assign-sandbox-project-quota.sh"
            ),
        ))
        self.state_root = Path("/var/tmp") / (
            f"nanobot-sandbox-real-{self.suffix}"
        )
        self.uplink_name = f"nanobot-sbx-smoke-uplink-{self.suffix}"
        self.workspace_ids: set[str] = set()
        self.lease_ids: set[str] = set()
        self.project_ids: set[int] = set()
        self.process_managers: list[LeaseProcessManager] = []
        self.backends: list[LeaseBackend] = []
        self.closed = False

        self.config = SandboxdConfig(
            data_root=self.data_root,
            socket_path=self.state_root / "sandboxd.sock",
            token_file=self.state_root / "client.token",
            client_token_path=self.state_root / "runtime-client.token",
            admin_token_file=self.state_root / "admin.token",
            admin_client_token_path=self.state_root / "runtime-admin.token",
            quota_helper_path=self.quota_helper,
            developer_network_allowed=True,
            egress_uplink_network_name=self.uplink_name,
            profile_manifest_path=self.manifest,
            disk_min_free_bytes=0,
            max_timeout_seconds=1800,
        ).validated()
        self.client = docker.from_env(timeout=1830)
        assert self.client.ping() is True
        self.workspace_files = WorkspaceFileService(self.config)
        self.asset_files = AssetFileService(self.config)
        self.workspace_files.layout.ensure_roots()
        self.quota = ProjectQuotaManager(
            data_root=self.data_root,
            helper_path=self.quota_helper,
            timeout_seconds=60,
        )
        assert self.quota.capability(max_age_seconds=0) == {
            "project_quota": True,
            "workspace_scope": True,
            "runtime_scope": True,
        }
        self.network_policy = NetworkPolicyManager(
            self.config,
            docker_client=self.client,
        )
        self.local_backend = LocalDockerBackend(
            self.config,
            docker_client=self.client,
            workspace_files=self.workspace_files,
            asset_files=self.asset_files,
            quota_manager=self.quota,
            network_policy=self.network_policy,
        )
        self.store = self._new_store()
        self.lease_backend, self.process_manager = self._new_controller(
            self.store,
        )
        self.workspace_id = self.new_workspace()
        self.lease_id = self.new_lease_id("primary")

    def _new_store(self) -> LeaseStore:
        store = LeaseStore(self.state_root / "lease-store")
        store.ensure()
        store.start_controller(now_unix=time.time())
        return store

    def _new_controller(
        self,
        store: LeaseStore,
    ) -> tuple[LeaseBackend, LeaseProcessManager]:
        lease_backend = LeaseBackend(
            self.config,
            docker_client=self.client,
            workspace_files=self.workspace_files,
            asset_files=self.asset_files,
            lease_store=store,
            profile_image_resolver=self.local_backend.require_profile_image,
            network_policy=self.network_policy,
        )
        process_manager = LeaseProcessManager(
            self.config,
            lease_backend=lease_backend,
            lease_store=store,
        )
        self.backends.append(lease_backend)
        self.process_managers.append(process_manager)
        return lease_backend, process_manager

    def new_workspace(self) -> str:
        workspace_id = str(uuid4())
        self.workspace_ids.add(workspace_id)
        result = self.workspace_files.ensure_workspace(workspace_id)
        assert result["ensured"] is True
        return workspace_id

    def new_lease_id(self, label: str) -> str:
        lease_id = f"sbxlease_p16_{label}_{uuid4().hex[:12]}"
        self.lease_ids.add(lease_id)
        return lease_id

    def ensure(
        self,
        *,
        lease_id: str | None = None,
        workspace_id: str | None = None,
        backend: LeaseBackend | None = None,
    ) -> dict[str, object]:
        actual_lease = lease_id or self.lease_id
        actual_workspace = workspace_id or self.workspace_id
        actual_backend = backend or self.lease_backend
        self.lease_ids.add(actual_lease)
        catalog = self.config.profile_catalog
        assert catalog is not None
        return actual_backend.ensure(
            request_id=f"ensure_{uuid4().hex[:20]}",
            lease_id=actual_lease,
            workspace_id=actual_workspace,
            profile_id="developer",
            catalog_generation=catalog.catalog_generation,
            policy_sha256=catalog.policy_sha256,
            quota_generation=1,
        )

    def container(self, lease_id: str | None = None):
        actual = lease_id or self.lease_id
        container = self.client.containers.get(
            f"{LEASE_CONTAINER_PREFIX}{actual}"
        )
        assert managed_lease_container(
            container,
            expected_lease_id=actual,
        )
        return container

    def run(
        self,
        command: str,
        *,
        lease_id: str | None = None,
        manager: LeaseProcessManager | None = None,
        yield_time_ms: int = 30_000,
        timeout_seconds: int = 120,
    ) -> dict[str, object]:
        actual_manager = manager or self.process_manager
        process_id = f"sbxrun_p16_{uuid4().hex[:20]}"
        result = actual_manager.start(
            lease_id=lease_id or self.lease_id,
            request_id=process_id,
            command=command,
            yield_time_ms=yield_time_ms,
            timeout_seconds=timeout_seconds,
        )
        if (
            yield_time_ms == 30_000
            and result.get("execution_status") == "running"
        ):
            completed = self.wait_process(
                actual_manager,
                process_id,
                timeout_seconds=timeout_seconds + 5,
            )
            completed["stdout"] = completed.pop("collected_stdout")
            completed["stderr"] = completed.pop("collected_stderr")
            return completed
        return result

    @staticmethod
    def wait_process(
        manager: LeaseProcessManager,
        process_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout_seconds
        cursor = ""
        stdout = []
        stderr = []
        latest: dict[str, object] = {}
        while time.monotonic() < deadline:
            latest = manager.get(process_id, cursor=cursor)
            stdout.append(str(latest.get("stdout_delta") or ""))
            stderr.append(str(latest.get("stderr_delta") or ""))
            cursor = str(latest.get("next_cursor") or cursor)
            if latest.get("execution_status") != "running":
                latest["collected_stdout"] = "".join(stdout)
                latest["collected_stderr"] = "".join(stderr)
                return latest
            time.sleep(0.1)
        raise AssertionError(f"进程未在期限内结束：{process_id}")

    def apply_quota(
        self,
        *,
        workspace_id: str,
        scope: str,
        quota_bytes: int,
        project_id: int,
    ) -> dict[str, object]:
        self.project_ids.add(project_id)
        result = self.quota.apply(
            workspace_id=workspace_id,
            scope=scope,
            project_id=project_id,
            quota_bytes=quota_bytes,
            generation=1,
        )
        observed = self.quota.inspect(
            workspace_id=workspace_id,
            scope=scope,
            project_id=project_id,
            quota_bytes=quota_bytes,
            generation=1,
        )
        assert observed["verified"] is True
        assert observed["project_id_matches"] is True
        assert observed["quota_bytes_matches"] is True
        return result

    def _clear_project_limits(self) -> None:
        if not self.project_ids:
            return
        mount_target_result = subprocess.run(
            ["findmnt", "-n", "-T", os.fspath(self.data_root), "-o", "TARGET"],
            capture_output=True,
            text=True,
            check=False,
        )
        filesystem_result = subprocess.run(
            ["findmnt", "-n", "-T", os.fspath(self.data_root), "-o", "FSTYPE"],
            capture_output=True,
            text=True,
            check=False,
        )
        if mount_target_result.returncode or filesystem_result.returncode:
            return
        mount_target = mount_target_result.stdout.strip()
        filesystem = filesystem_result.stdout.strip()
        for project_id in sorted(self.project_ids):
            if filesystem == "xfs":
                command = [
                    "xfs_quota",
                    "-x",
                    "-c",
                    f"limit -p bsoft=0 bhard=0 {project_id}",
                    mount_target,
                ]
            elif filesystem == "ext4":
                command = [
                    "setquota",
                    "-P",
                    str(project_id),
                    "0",
                    "0",
                    "0",
                    "0",
                    mount_target,
                ]
            else:
                continue
            subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for lease_id in sorted(self.lease_ids):
            recycled = False
            for backend in reversed(self.backends):
                try:
                    backend.recycle(
                        lease_id,
                        reason="admin_lease_destroy",
                    )
                except Exception:
                    continue
                recycled = True
                break
            if recycled:
                continue
            try:
                container = self.client.containers.get(
                    f"{LEASE_CONTAINER_PREFIX}{lease_id}"
                )
                if managed_lease_container(
                    container,
                    expected_lease_id=lease_id,
                ):
                    container.remove(force=True, v=True)
            except Exception:
                pass
            try:
                self.network_policy.cleanup(lease_id)
            except Exception:
                pass
        for manager in self.process_managers:
            manager.close()

        try:
            uplink = self.client.networks.get(self.uplink_name)
            uplink.reload()
            labels = dict(uplink.attrs.get("Labels") or {})
            if (
                labels.get(MANAGED_LABEL) == "true"
                and labels.get(MANAGED_BY_LABEL) == "sandboxd"
                and labels.get(NETWORK_ROLE_LABEL) == UPLINK_NETWORK_ROLE
                and not dict(uplink.attrs.get("Containers") or {})
            ):
                uplink.remove()
        except Exception:
            pass

        for workspace_id in sorted(self.workspace_ids):
            workspace_parent = (
                self.data_root / "workspaces" / workspace_id[:2]
            )
            _safe_remove_tree(
                workspace_parent / workspace_id,
                expected_parent=workspace_parent,
            )
            try:
                workspace_parent.rmdir()
            except OSError:
                pass
            _safe_remove_tree(
                self.data_root / "runtime" / workspace_id,
                expected_parent=self.data_root / "runtime",
            )
        self._clear_project_limits()
        _safe_remove_tree(
            self.state_root,
            expected_parent=Path("/var/tmp"),
        )
        self.client.close()

    def __enter__(self) -> "_RealSandboxHarness":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()


def _project_id(workspace_id: str, offset: int) -> int:
    raw = UUID(workspace_id).int
    return 1_000_000_000 + (raw % 500_000_000) * 2 + offset


def test_real_docker_lease_lifecycle():
    with _RealSandboxHarness() as harness:
        fact = harness.ensure()
        assert fact["present"] is True
        assert fact["running"] is True
        assert fact["status"] == "idle"
        assert fact["network_ready"] is True
        assert fact["workspace_id"] == harness.workspace_id
        assert fact["profile_id"] == "developer"

        container = harness.container()
        host = container.attrs["HostConfig"]
        assert container.attrs["Config"]["User"] == "10001:10001"
        assert host["ReadonlyRootfs"] is True
        assert host["CapDrop"] == ["ALL"]
        assert set(host["SecurityOpt"]) == {
            "no-new-privileges",
            "apparmor=nanobot-sandbox-developer",
        }
        assert host["Privileged"] is False
        assert not host["Devices"]
        assert not host["PortBindings"]
        destinations = {
            mount["Destination"] for mount in container.attrs["Mounts"]
        }
        assert destinations == {"/workspace", "/inputs", "/runtime"}
        assert all(
            mount["Destination"] != "/var/run/docker.sock"
            for mount in container.attrs["Mounts"]
        )

        stopped = harness.lease_backend.admin_recycle(
            harness.lease_id,
            reason="admin_lease_stop",
        )
        assert stopped["lease_recycled"] is True
        assert stopped["workspace_preserved"] is True
        assert harness.lease_backend.get(harness.lease_id)["present"] is False

        harness.ensure()
        recreated = harness.lease_backend.recreate(
            harness.lease_id,
            request_id=f"recreate_{uuid4().hex[:20]}",
        )
        assert recreated["lease_recycled"] is True
        assert recreated["workspace_preserved"] is True
        assert recreated["runtime_preserved"] is True
        assert harness.container().attrs["State"]["Running"] is True

        destroyed = harness.lease_backend.admin_recycle(
            harness.lease_id,
            reason="admin_lease_destroy",
        )
        assert destroyed["lease_recycled"] is True
        assert destroyed["workspace_preserved"] is True
        assert harness.workspace_files.layout.workspace_data_dir(
            harness.workspace_id
        ).is_dir()


def test_real_docker_process_sessions():
    with _RealSandboxHarness() as harness:
        harness.ensure()
        server = harness.run(
            (
                "printf 'SERVER_READY\\n'; "
                "exec python -m http.server 18080 "
                "--bind 127.0.0.1 --directory /workspace"
            ),
            yield_time_ms=200,
            timeout_seconds=120,
        )
        server_id = str(server["process_id"])
        assert server["execution_status"] == "running"

        deadline = time.monotonic() + 10
        ready_seen = False
        while time.monotonic() < deadline:
            polled = harness.process_manager.get(server_id, cursor="")
            if "SERVER_READY" in str(polled.get("stdout_delta") or ""):
                ready_seen = True
                break
            time.sleep(0.1)
        assert ready_seen is True

        client = harness.run(
            (
                "for attempt in $(seq 1 50); do "
                "if output=$(curl -fsS http://127.0.0.1:18080/); then "
                "printf 'LOOPBACK_OK:%s\\n' \"$output\"; exit 0; fi; "
                "sleep 0.1; done; exit 1"
            ),
        )
        assert client["execution_status"] == "completed", client
        assert "LOOPBACK_OK:" in str(client.get("stdout") or "")

        stdin_process = harness.run(
            (
                "python -c 'import sys; "
                "print(\"STDIN_READY\", flush=True); "
                "print(\"STDIN_VALUE=\" + sys.stdin.readline().strip(), "
                "flush=True)'"
            ),
            yield_time_ms=200,
        )
        stdin_id = str(stdin_process["process_id"])
        assert stdin_process["execution_status"] == "running"
        written = harness.process_manager.write_stdin(
            stdin_id,
            request_id=f"stdin_{uuid4().hex[:20]}",
            chars="真实输入\n",
        )
        assert written["written_bytes"] == len("真实输入\n".encode())
        stdin_done = harness.wait_process(
            harness.process_manager,
            stdin_id,
        )
        assert stdin_done["execution_status"] == "completed"
        assert "STDIN_VALUE=真实输入" in str(stdin_done["collected_stdout"])

        incremental = harness.run(
            (
                "printf 'FIRST_CHUNK\\n'; "
                "sleep 1; "
                "printf 'SECOND_CHUNK\\n'"
            ),
            yield_time_ms=100,
        )
        incremental_id = str(incremental["process_id"])
        first_deadline = time.monotonic() + 5
        first: dict[str, object] = {}
        while time.monotonic() < first_deadline:
            first = harness.process_manager.get(incremental_id, cursor="")
            if "FIRST_CHUNK" in str(first.get("stdout_delta") or ""):
                break
            time.sleep(0.05)
        first_cursor = str(first["next_cursor"])
        assert "FIRST_CHUNK" in str(first["stdout_delta"])
        incremental_done = harness.wait_process(
            harness.process_manager,
            incremental_id,
        )
        assert incremental_done["execution_status"] == "completed"
        tail = harness.process_manager.get(
            incremental_id,
            cursor=first_cursor,
        )
        assert "SECOND_CHUNK" in str(tail["stdout_delta"])

        terminated = harness.process_manager.terminate(
            server_id,
            request_id=f"terminate_{uuid4().hex[:20]}",
        )
        assert terminated["termination_scope"] == "lease"
        assert terminated["lease_recycled"] is True
        assert server_id in terminated["affected_process_ids"]
        assert harness.lease_backend.get(harness.lease_id)["present"] is False

        harness.ensure()
        persisted = harness.run(
            (
                "printf 'workspace-after-restart' > /workspace/restart.txt; "
                "printf 'runtime-after-restart' > /runtime/restart.txt"
            ),
        )
        assert persisted["execution_status"] == "completed"
        old_epoch = harness.store.controller_epoch
        new_store = harness._new_store()
        assert new_store.controller_epoch != old_epoch
        new_backend, _new_process_manager = harness._new_controller(new_store)
        recovery = LeaseReconciler(
            new_backend,
            new_store,
            interval_seconds=15,
        ).recover_previous_controller()
        assert recovery["recovered_lease_ids"] == [harness.lease_id]
        assert recovery["failed_lease_ids"] == []
        assert new_backend.get(harness.lease_id)["present"] is False
        assert (
            harness.workspace_files.layout.workspace_data_dir(
                harness.workspace_id
            )
            / "restart.txt"
        ).read_text(encoding="utf-8") == "workspace-after-restart"
        assert (
            harness.workspace_files.layout.runtime_root
            / harness.workspace_id
            / "restart.txt"
        ).read_text(encoding="utf-8") == "runtime-after-restart"


def test_real_docker_developer_toolchain():
    with _RealSandboxHarness() as harness:
        harness.ensure()
        result = harness.run(
            r"""
set -euo pipefail
git init -q
git config user.email sandbox@example.invalid
git config user.name sandbox-smoke
mkdir -p tests
cat > calculator.py <<'PY'
def add(left: int, right: int) -> int:
    return left + right
PY
cat > tests/test_calculator.py <<'PY'
from calculator import add


def test_add():
    assert add(20, 22) == 42
PY
rg -n 'def add' calculator.py
python -m venv /runtime/venv-p16
/runtime/venv-p16/bin/python -c 'import sys; assert sys.prefix.startswith("/runtime/")'
python -m pytest tests/test_calculator.py -q
cat > package.json <<'JSON'
{
  "name": "nanobot-sandbox-smoke",
  "private": true,
  "scripts": {
    "test": "node -e \"if (20 + 22 !== 42) process.exit(1)\""
  }
}
JSON
npm test --silent
git add calculator.py tests/test_calculator.py package.json
git diff --cached --check
printf 'DEVELOPER_TOOLCHAIN_OK\n'
""",
            timeout_seconds=180,
        )

        assert result["execution_status"] == "completed", result
        assert result["exit_code"] == 0
        assert "1 passed" in str(result.get("stdout") or "")
        assert "DEVELOPER_TOOLCHAIN_OK" in str(result.get("stdout") or "")
        assert (
            harness.workspace_files.layout.runtime_root
            / harness.workspace_id
            / "venv-p16"
            / "bin"
            / "python"
        ).is_file()


def test_real_docker_data_continuity_and_project_quota():
    with _RealSandboxHarness() as harness:
        workspace_b = harness.new_workspace()
        lease_b = harness.new_lease_id("workspace_b")
        harness.ensure(lease_id=lease_b, workspace_id=workspace_b)

        quota_bytes = 16 * 1024 * 1024
        workspace_project = _project_id(harness.workspace_id, 0)
        runtime_project = _project_id(harness.workspace_id, 1)
        applied_workspace = harness.apply_quota(
            workspace_id=harness.workspace_id,
            scope="workspace",
            quota_bytes=quota_bytes,
            project_id=workspace_project,
        )
        assert applied_workspace["applied"] is True
        # B 的 Lease 活跃时，helper 只能检查 A 的 Workspace label。
        assert harness.lease_backend.get(lease_b)["running"] is True
        applied_runtime = harness.apply_quota(
            workspace_id=harness.workspace_id,
            scope="runtime",
            quota_bytes=quota_bytes,
            project_id=runtime_project,
        )
        assert applied_runtime["applied"] is True
        assert harness.lease_backend.get(lease_b)["running"] is True
        harness.lease_backend.recycle(
            lease_b,
            reason="admin_lease_destroy",
        )

        harness.ensure()
        written = harness.run(
            (
                "printf 'workspace-persistent' > /workspace/persistent.txt; "
                "printf 'runtime-persistent' > /runtime/persistent.txt; "
                "printf 'temporary' > /tmp/ephemeral.txt"
            ),
        )
        assert written["execution_status"] == "completed"
        recreated = harness.lease_backend.recreate(
            harness.lease_id,
            request_id=f"recreate_{uuid4().hex[:20]}",
        )
        assert recreated["workspace_preserved"] is True
        assert recreated["runtime_preserved"] is True

        continuity = harness.run(
            (
                "test \"$(cat /workspace/persistent.txt)\" "
                "= workspace-persistent; "
                "test \"$(cat /runtime/persistent.txt)\" "
                "= runtime-persistent; "
                "test ! -e /tmp/ephemeral.txt; "
                "printf 'CONTINUITY_OK\\n'"
            ),
        )
        assert continuity["execution_status"] == "completed", continuity
        assert "CONTINUITY_OK" in str(continuity.get("stdout") or "")

        workspace_b_lease = harness.new_lease_id("isolation_b")
        harness.ensure(
            lease_id=workspace_b_lease,
            workspace_id=workspace_b,
        )
        isolated = harness.run(
            (
                "test ! -e /workspace/persistent.txt; "
                "test ! -e /runtime/persistent.txt; "
                "printf 'WORKSPACE_B_ISOLATED\\n'"
            ),
            lease_id=workspace_b_lease,
        )
        assert isolated["execution_status"] == "completed", isolated
        assert "WORKSPACE_B_ISOLATED" in str(isolated.get("stdout") or "")
        harness.lease_backend.recycle(
            workspace_b_lease,
            reason="admin_lease_destroy",
        )

        quota_enforced = harness.run(
            (
                "rm -f /workspace/quota-overflow.bin; "
                "dd if=/dev/zero of=/workspace/quota-overflow.bin "
                "bs=1048576 count=32 conv=fsync"
            ),
            timeout_seconds=60,
        )
        assert quota_enforced["execution_status"] == "failed", quota_enforced
        assert quota_enforced["exit_code"] not in {None, 0}
        workspace_size = (
            harness.workspace_files.layout.workspace_data_dir(
                harness.workspace_id
            )
            / "quota-overflow.bin"
        ).stat().st_size
        assert workspace_size <= quota_bytes

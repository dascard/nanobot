"""使用 Docker SDK 结构化 API 创建一次性 Sandbox 容器。"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.paths import validate_relative_path, validate_workspace_id
from sandboxd.config import SandboxdConfig
from sandboxd.filesystem import (
    AssetFileService,
    WorkspaceFileService,
    directory_usage,
)
from sandboxd.output_limiter import OutputLimiter


MANAGED_LABEL = "com.nanobot.sandbox"
MANAGED_BY_LABEL = "com.nanobot.managed-by"
WORKSPACE_LABEL = "com.nanobot.workspace-id"
RUN_LABEL = "com.nanobot.run-id"
CONTAINER_PREFIX = "nanobot-sbx-"
APPARMOR_PROFILES_PATH = Path("/sys/kernel/security/apparmor/profiles")


def managed_container(container: Any) -> bool:
    try:
        container.reload()
        labels = dict(container.attrs.get("Config", {}).get("Labels") or {})
        name = str(getattr(container, "name", "") or "")
    except Exception:
        return False
    return (
        name.startswith(CONTAINER_PREFIX)
        and labels.get(MANAGED_LABEL) == "true"
        and labels.get(MANAGED_BY_LABEL) == "sandboxd"
    )


class RunStore:
    """保存在可删除 runtime 中的幂等结果缓存，不写 Nanobot 数据库。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.by_request = root / "by-request"
        self.by_run = root / "by-run"
        self._lock = threading.RLock()

    def ensure(self) -> None:
        for path in (self.root, self.by_request, self.by_run):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(path, 0o700)

    @staticmethod
    def _validate_identifier(value: str, *, prefix: str = "") -> str:
        normalized = str(value or "")
        if (
            not normalized
            or len(normalized) > 64
            or (prefix and not normalized.startswith(prefix))
            or not normalized.replace("_", "").replace("-", "").isalnum()
        ):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 请求标识无效",
            )
        return normalized

    def _request_path(self, request_id: str) -> Path:
        request_id = self._validate_identifier(request_id)
        return self.by_request / f"{hashlib.sha256(request_id.encode()).hexdigest()}.json"

    def _run_path(self, run_id: str) -> Path:
        run_id = self._validate_identifier(run_id, prefix="sbxrun_")
        return self.by_run / f"{run_id}.json"

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                return None
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, ValueError, TypeError):
            return None
        return value if isinstance(value, dict) else None

    def get_by_request(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._read(self._request_path(request_id))
            if value is not None and value.get("request_id") == request_id:
                return value
            return None

    def get_by_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._read(self._run_path(run_id))
            if value is not None and value.get("run_id") == run_id:
                return value
            return None

    def save(self, value: dict[str, Any]) -> None:
        request_id = self._validate_identifier(str(value.get("request_id") or ""))
        run_id = self._validate_identifier(
            str(value.get("run_id") or ""),
            prefix="sbxrun_",
        )
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > 2 * 1024 * 1024:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 运行账本超过安全上限",
            )
        with self._lock:
            self.ensure()
            for target in (self._request_path(request_id), self._run_path(run_id)):
                temp = target.parent / f".{target.name}.{secrets.token_hex(8)}.tmp"
                file_fd = os.open(
                    temp,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                try:
                    os.write(file_fd, encoded)
                    os.fsync(file_fd)
                finally:
                    os.close(file_fd)
                os.replace(temp, target)


class LocalDockerBackend:
    def __init__(
        self,
        config: SandboxdConfig,
        *,
        docker_client: Any | None = None,
        workspace_files: WorkspaceFileService | None = None,
        asset_files: AssetFileService | None = None,
    ) -> None:
        self.config = config.validated()
        if docker_client is None:
            try:
                import docker as docker_sdk

                docker_client = docker_sdk.DockerClient(
                    base_url=config.docker_socket,
                    timeout=config.max_timeout_seconds + 30,
                )
            except Exception as exc:
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Docker Engine 客户端不可用",
                ) from exc
        self.client = docker_client
        self.workspace_files = workspace_files or WorkspaceFileService(config)
        self.asset_files = asset_files or AssetFileService(config)
        self.run_store = RunStore(config.data_root / "runtime" / ".sandboxd-runs")
        self._global_slots = threading.BoundedSemaphore(config.global_concurrency)
        self._active: dict[str, tuple[Any, threading.Event]] = {}
        self._active_guard = threading.RLock()
        self._request_locks: dict[str, threading.Lock] = {}
        self._request_locks_guard = threading.Lock()

    def _request_lock(self, request_id: str) -> threading.Lock:
        with self._request_locks_guard:
            return self._request_locks.setdefault(request_id, threading.Lock())

    @staticmethod
    def _security_options(info: dict[str, Any]) -> set[str]:
        options: set[str] = set()
        for value in info.get("SecurityOptions") or []:
            if isinstance(value, dict):
                options.add(str(value.get("Name") or value.get("name") or ""))
            else:
                options.add(str(value))
        return options

    def _allowed_image(self) -> Any:
        try:
            image = self.client.images.get(self.config.image_reference)
        except Exception as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "固定 Sandbox 镜像不存在，sandboxd 不会自动拉取",
            ) from exc
        image_id = str(getattr(image, "id", "") or "").lower()
        if image_id not in self.config.image_allowlist:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 镜像不在固定 allowlist 中",
            )
        configured_user = str(
            getattr(image, "attrs", {}).get("Config", {}).get("User") or ""
        )
        if configured_user != f"{self.config.workspace_uid}:{self.config.workspace_gid}":
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 镜像默认用户不符合固定策略",
            )
        return image

    def _require_apparmor_profile(self) -> None:
        """确认固定 profile 已加载；securityfs 不可读时同样失败关闭。"""

        expected = f"{self.config.apparmor_profile} "
        try:
            with APPARMOR_PROFILES_PATH.open("r", encoding="utf-8") as handle:
                for index, line in enumerate(handle):
                    if index >= 100_000:
                        break
                    if line.startswith(expected):
                        return
        except (OSError, UnicodeError) as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "无法确认 Sandbox AppArmor profile，Sandbox 按失败关闭处理",
            ) from exc
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "固定 Sandbox AppArmor profile 未加载，Sandbox 按失败关闭处理",
        )

    def ready(self) -> dict[str, Any]:
        try:
            if not self.client.ping():
                raise RuntimeError("docker ping failed")
            info = self.client.info()
        except Exception as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Docker Engine 不可用",
                retryable=True,
                stop=False,
            ) from exc
        security_options = self._security_options(info)
        if not any("apparmor" in option.lower() for option in security_options):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "宿主 Docker 未启用 AppArmor，Sandbox 按失败关闭处理",
            )
        self._require_apparmor_profile()
        image = self._allowed_image()
        disk = self.workspace_files.disk_guard.ensure_available()
        self.workspace_files.layout.ensure_roots()
        self.run_store.ensure()
        return {
            "docker": True,
            "image_id": image.id,
            "apparmor_profile": self.config.apparmor_profile,
            "disk_used_percent": round(disk.used_percent, 2),
            "disk_free_bytes": disk.free_bytes,
        }

    def _container_kwargs(
        self,
        *,
        image: Any,
        run_id: str,
        workspace_id: str,
        command: str,
        cwd: str,
        inputs_path: Path,
    ) -> dict[str, Any]:
        workspace_path = self.workspace_files.layout.workspace_data_dir(workspace_id)
        runtime_path = self.workspace_files.layout.ensure_runtime(workspace_id)
        working_dir = "/workspace" if not cwd else f"/workspace/{cwd}"
        labels = {
            MANAGED_LABEL: "true",
            MANAGED_BY_LABEL: "sandboxd",
            WORKSPACE_LABEL: workspace_id,
            RUN_LABEL: run_id,
        }
        kwargs: dict[str, Any] = {
            "image": image.id,
            "name": f"{CONTAINER_PREFIX}{run_id}",
            "command": ["/bin/sh", "-lc", command],
            "user": f"{self.config.workspace_uid}:{self.config.workspace_gid}",
            "working_dir": working_dir,
            "detach": True,
            "stdin_open": False,
            "tty": False,
            "auto_remove": False,
            "network_disabled": True,
            "network_mode": "none",
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": [
                "no-new-privileges",
                f"apparmor={self.config.apparmor_profile}",
            ],
            "privileged": False,
            "init": True,
            "pids_limit": self.config.pids_limit,
            "mem_limit": self.config.memory_bytes,
            "memswap_limit": self.config.memory_bytes,
            "nano_cpus": self.config.cpu_nano,
            "oom_kill_disable": False,
            "ipc_mode": "private",
            "volumes": {
                str(workspace_path): {"bind": "/workspace", "mode": "rw"},
                str(inputs_path): {"bind": "/inputs", "mode": "ro"},
                str(runtime_path): {"bind": "/runtime", "mode": "rw"},
            },
            "tmpfs": {
                "/tmp": (
                    "rw,nosuid,nodev,noexec,mode=1777,"
                    f"size={self.config.tmpfs_bytes}"
                ),
            },
            "labels": labels,
            "environment": {
                "HOME": "/runtime/home",
                "XDG_CACHE_HOME": "/runtime/cache",
                "PYTHONPYCACHEPREFIX": "/runtime/pycache",
                "PIP_CACHE_DIR": "/runtime/pip-cache",
                "PIP_NO_INDEX": "1",
                "TMPDIR": "/tmp",
            },
            "log_config": {
                "type": "local",
                "config": {
                    "max-size": "1m",
                    "max-file": "1",
                    "compress": "false",
                },
            },
        }
        if self.config.io_device_path:
            kwargs["device_write_bps"] = [{
                "Path": self.config.io_device_path,
                "Rate": self.config.io_write_bps,
            }]
        return kwargs

    @staticmethod
    def _safe_kill(container: Any) -> None:
        if not managed_container(container):
            return
        try:
            container.stop(timeout=2)
        except Exception:
            pass
        try:
            container.reload()
            if bool(container.attrs.get("State", {}).get("Running")):
                container.kill()
        except Exception:
            pass

    @staticmethod
    def _consume_output(container: Any, limiter: OutputLimiter) -> None:
        try:
            stream = container.attach(
                stream=True,
                logs=True,
                stdout=True,
                stderr=True,
                demux=True,
            )
            for item in stream:
                if isinstance(item, tuple):
                    stdout, stderr = item
                else:
                    stdout, stderr = item, None
                if limiter.feed(stdout, stderr):
                    LocalDockerBackend._safe_kill(container)
                    break
        except Exception:
            return

    @staticmethod
    def _stats(container: Any) -> tuple[int, int]:
        try:
            stats = container.stats(stream=False)
            memory = int((stats.get("memory_stats") or {}).get("usage") or 0)
            cpu_ns = int(
                ((stats.get("cpu_stats") or {}).get("cpu_usage") or {}).get(
                    "total_usage",
                )
                or 0
            )
            return memory, cpu_ns // 1_000_000
        except Exception:
            return 0, 0

    def _inspect_result(
        self,
        container: Any,
        *,
        limiter: OutputLimiter,
        forced_reason: str,
        peak_memory_bytes: int,
        cpu_time_ms: int,
    ) -> dict[str, Any]:
        try:
            container.reload()
            state = dict(container.attrs.get("State") or {})
        except Exception:
            state = {}
        output = limiter.snapshot()
        exit_code = state.get("ExitCode")
        oom_killed = bool(state.get("OOMKilled"))
        if output.hard_limit_exceeded:
            termination_reason = "output_limit_exceeded"
        elif forced_reason:
            termination_reason = forced_reason
        elif oom_killed:
            termination_reason = "process_oom_killed"
        elif exit_code == 0:
            termination_reason = "completed"
        else:
            termination_reason = "nonzero_exit"
        return {
            "exit_code": int(exit_code) if isinstance(exit_code, int) else None,
            "termination_reason": termination_reason,
            "oom_killed": oom_killed,
            "cpu_time_ms": cpu_time_ms,
            "peak_memory_bytes": peak_memory_bytes,
            "stdout": output.stdout,
            "stderr": output.stderr,
            "stdout_bytes": output.stdout_bytes,
            "stderr_bytes": output.stderr_bytes,
            "stdout_truncated": output.stdout_truncated,
            "stderr_truncated": output.stderr_truncated,
        }

    def execute(
        self,
        *,
        request_id: str,
        run_id: str,
        workspace_id: str,
        command: str,
        cwd: str = "",
        timeout_seconds: int | None = None,
        quota_bytes: int | None = None,
    ) -> dict[str, Any]:
        request_id = RunStore._validate_identifier(request_id)
        run_id = RunStore._validate_identifier(run_id, prefix="sbxrun_")
        workspace_id = validate_workspace_id(workspace_id)
        normalized_cwd = "/".join(validate_relative_path(cwd, allow_empty=True))
        if not command or len(command.encode("utf-8")) > self.config.max_command_bytes:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 命令为空或超过安全长度上限",
            )
        timeout = min(
            int(timeout_seconds or self.config.default_timeout_seconds),
            self.config.max_timeout_seconds,
        )
        if timeout < 1:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 超时参数无效",
            )

        request_lock = self._request_lock(request_id)
        if not request_lock.acquire(blocking=False):
            raise SandboxServiceError(
                SandboxErrorCode.SANDBOX_BUSY,
                "相同 Sandbox 请求仍在执行",
                retryable=True,
                stop=False,
            )
        try:
            existing = self.run_store.get_by_request(request_id)
            if existing is not None:
                if existing.get("run_id") != run_id:
                    raise SandboxServiceError(
                        SandboxErrorCode.AUTHORIZATION_FAILED,
                        "幂等 request_id 已绑定到其他运行",
                    )
                if existing.get("status") in {"completed", "failed", "cancelled"}:
                    return dict(existing)
                raise SandboxServiceError(
                    SandboxErrorCode.SANDBOX_BUSY,
                    "相同 Sandbox 请求仍在执行",
                    retryable=True,
                    stop=False,
                )

            self.workspace_files.ensure_workspace(workspace_id)
            self.workspace_files.disk_guard.ensure_available()
            image = self._allowed_image()
            requested_quota = min(
                max(1, int(quota_bytes or self.config.workspace_quota_bytes)),
                self.config.workspace_quota_bytes,
            )
            pending = {
                "request_id": request_id,
                "run_id": run_id,
                "workspace_id": workspace_id,
                "image_digest": str(image.id),
                "status": "pending",
                "created_at_unix": time.time(),
            }

            if not self._global_slots.acquire(blocking=False):
                raise SandboxServiceError(
                    SandboxErrorCode.SANDBOX_BUSY,
                    "Sandbox 全局并发已满",
                    retryable=True,
                    stop=False,
                )
            try:
                workspace_lock = self.workspace_files.acquire_workspace_mutation(
                    workspace_id,
                )
            except SandboxServiceError:
                self._global_slots.release()
                raise

            container = None
            cancel_event = threading.Event()
            forced_reason = ""
            limiter = OutputLimiter(
                stdout_limit_bytes=self.config.stdout_limit_bytes,
                stderr_limit_bytes=self.config.stderr_limit_bytes,
                hard_limit_bytes=self.config.hard_output_limit_bytes,
            )
            peak_memory = 0
            cpu_time_ms = 0
            capacity_reserved = False
            try:
                (
                    workspace_usage_before,
                    effective_quota,
                ) = self.workspace_files.reserve_run_capacity(
                    run_id,
                    workspace_id,
                    workspace_quota_bytes=requested_quota,
                )
                capacity_reserved = True
                inputs_path = self.asset_files.stage_path(workspace_id, run_id)
                self.run_store.save(pending)
                kwargs = self._container_kwargs(
                    image=image,
                    run_id=run_id,
                    workspace_id=workspace_id,
                    command=command,
                    cwd=normalized_cwd,
                    inputs_path=inputs_path,
                )
                try:
                    container = self.client.containers.create(**kwargs)
                    if not managed_container(container):
                        raise SandboxServiceError(
                            SandboxErrorCode.RUNTIME_UNAVAILABLE,
                            "Docker 容器标签校验失败",
                        )
                    with self._active_guard:
                        self._active[run_id] = (container, cancel_event)
                    running = dict(pending)
                    running.update({"status": "running", "started_at_unix": time.time()})
                    self.run_store.save(running)
                    container.start()
                except SandboxServiceError:
                    raise
                except Exception as exc:
                    raise SandboxServiceError(
                        SandboxErrorCode.RUNTIME_UNAVAILABLE,
                        "Docker 无法创建或启动 Sandbox 容器",
                        retryable=True,
                        stop=False,
                    ) from exc

                output_thread = threading.Thread(
                    target=self._consume_output,
                    args=(container, limiter),
                    name=f"sandbox-output-{run_id}",
                    daemon=True,
                )
                output_thread.start()
                deadline = time.monotonic() + timeout
                next_usage_check = 0.0
                while True:
                    try:
                        container.reload()
                        running_state = bool(container.attrs.get("State", {}).get("Running"))
                    except Exception:
                        running_state = False
                    if not running_state:
                        break
                    if cancel_event.is_set():
                        forced_reason = "cancelled"
                        self._safe_kill(container)
                        break
                    if limiter.snapshot().hard_limit_exceeded:
                        forced_reason = "output_limit_exceeded"
                        self._safe_kill(container)
                        break
                    if time.monotonic() >= deadline:
                        forced_reason = "execution_timeout"
                        self._safe_kill(container)
                        break
                    now = time.monotonic()
                    if now >= next_usage_check:
                        next_usage_check = now + 0.5
                        workspace_usage = directory_usage(
                            self.workspace_files.filesystem(workspace_id),
                        )
                        runtime_usage = directory_usage(
                            SafeRuntimeFilesystem(
                                self.workspace_files.layout.ensure_runtime(workspace_id),
                            ).filesystem,
                        )
                        if workspace_usage > effective_quota:
                            forced_reason = "workspace_quota_exceeded"
                            self._safe_kill(container)
                            break
                        if runtime_usage > self.config.runtime_quota_bytes:
                            forced_reason = "runtime_quota_exceeded"
                            self._safe_kill(container)
                            break
                        memory, cpu_ms = self._stats(container)
                        peak_memory = max(peak_memory, memory)
                        cpu_time_ms = max(cpu_time_ms, cpu_ms)
                    time.sleep(0.05)

                output_thread.join(timeout=2)
                result_data = self._inspect_result(
                    container,
                    limiter=limiter,
                    forced_reason=forced_reason,
                    peak_memory_bytes=peak_memory,
                    cpu_time_ms=cpu_time_ms,
                )
                workspace_usage_after = directory_usage(
                    self.workspace_files.filesystem(workspace_id),
                )
                result_data["workspace_used_bytes"] = workspace_usage_after
                result_data["workspace_usage_delta_bytes"] = (
                    workspace_usage_after - workspace_usage_before
                )
                status = (
                    "completed"
                    if result_data["termination_reason"] == "completed"
                    else "cancelled"
                    if result_data["termination_reason"] == "cancelled"
                    else "failed"
                )
                result = dict(pending)
                result.update({
                    "status": status,
                    "finished_at_unix": time.time(),
                    "data": result_data,
                })
                self.run_store.save(result)
                return result
            except SandboxServiceError as exc:
                failed = dict(pending)
                failed.update({
                    "status": "failed",
                    "finished_at_unix": time.time(),
                    "error_code": exc.code.value,
                })
                self.run_store.save(failed)
                raise
            finally:
                with self._active_guard:
                    self._active.pop(run_id, None)
                if container is not None and managed_container(container):
                    try:
                        container.remove(force=True, v=True)
                    except Exception:
                        pass
                self.asset_files.cleanup_stage(workspace_id, run_id)
                if capacity_reserved:
                    self.workspace_files.release_run_capacity(run_id)
                workspace_lock.release()
                self._global_slots.release()
        finally:
            request_lock.release()

    def cancel(self, run_id: str) -> dict[str, Any]:
        run_id = RunStore._validate_identifier(run_id, prefix="sbxrun_")
        with self._active_guard:
            active = self._active.get(run_id)
        if active is None:
            existing = self.run_store.get_by_run(run_id)
            if existing is None:
                raise SandboxServiceError(
                    SandboxErrorCode.AUTHORIZATION_FAILED,
                    "Sandbox 运行不存在",
                )
            return existing
        container, cancel_event = active
        if not managed_container(container):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 容器归属校验失败",
            )
        cancel_event.set()
        self._safe_kill(container)
        return {"run_id": run_id, "status": "cancelling"}

    def get(self, run_id: str) -> dict[str, Any]:
        value = self.run_store.get_by_run(run_id)
        if value is None:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 运行不存在",
            )
        return value


class SafeRuntimeFilesystem:
    """让 runtime 用量复用同一套不跟随符号链接的核算。"""

    def __init__(self, root: Path) -> None:
        from core.sandbox.paths import SafeWorkspaceFilesystem

        self.filesystem = SafeWorkspaceFilesystem(root)

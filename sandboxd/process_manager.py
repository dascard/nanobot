"""Lease 内 Docker Exec 会话、增量输出和整 Lease 终止边界。"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.paths import validate_relative_path
from sandboxd.concurrency import (
    ConcurrencyPermit,
    ProfileConcurrencyLimiter,
)
from sandboxd.config import SandboxdConfig
from sandboxd.lease_backend import LeaseBackend
from sandboxd.lease_store import (
    LeaseStore,
    validate_lease_id,
    validate_process_id,
    validate_request_id,
)
from sandboxd.process_output import ProcessOutputBuffer


_TERMINAL_EXECUTION_STATUSES = frozenset({
    "completed",
    "failed",
    "cancelled",
})
_CANCEL_REASONS = frozenset({
    "cancelled",
    "admin_terminated",
    "admin_lease_destroy",
    "admin_lease_recreate",
    "admin_lease_stop",
    "kill_switch",
    "lease_recycled",
    "quota_reconfigured",
})


class DockerExecHandle:
    """Docker SDK hijack socket 的最小双向包装。"""

    def __init__(self, api: Any, exec_id: str, socket: Any) -> None:
        self.api = api
        self.exec_id = str(exec_id)
        self.socket = socket
        self._write_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._closed = False

    def frames(self) -> Iterator[tuple[int, bytes]]:
        from docker.utils.socket import frames_iter

        yield from frames_iter(self.socket, tty=False)

    def inspect(self) -> dict[str, Any]:
        value = self.api.exec_inspect(self.exec_id)
        if not isinstance(value, dict):
            raise RuntimeError("Docker Exec inspect 响应无效")
        return dict(value)

    def write(self, payload: bytes) -> int:
        data = bytes(payload)
        with self._write_lock:
            if self._closed:
                raise OSError("Docker Exec socket 已关闭")
            sendall = getattr(self.socket, "sendall", None)
            if not callable(sendall):
                # Docker SDK 7.1 在 Unix Socket 上返回只读 SocketIO，
                # 双向 hijack socket 位于其 _sock 中。
                sendall = getattr(
                    getattr(self.socket, "_sock", None),
                    "sendall",
                    None,
                )
            if callable(sendall):
                sendall(data)
                return len(data)
            write = getattr(self.socket, "write", None)
            if not callable(write):
                raise OSError("Docker Exec socket 不支持 stdin")
            written = 0
            view = memoryview(data)
            while written < len(view):
                count = write(view[written:])
                if count is None:
                    count = len(view) - written
                if int(count) <= 0:
                    raise OSError("Docker Exec stdin 写入失败")
                written += int(count)
            flush = getattr(self.socket, "flush", None)
            if callable(flush):
                flush()
            return written

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            close = getattr(self.socket, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


class DockerExecAdapter:
    """只构造固定 shell、固定用户和 Workspace 内工作目录。"""

    def __init__(self, config: SandboxdConfig) -> None:
        self.config = config

    def start(
        self,
        *,
        container: Any,
        process_id: str,
        shell_path: str,
        command: str,
        cwd: str,
        allow_stdin: bool,
    ) -> DockerExecHandle:
        del process_id
        api = getattr(getattr(container, "client", None), "api", None)
        if api is None:
            api = getattr(getattr(container, "owner", None), "api", None)
        if api is None:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Docker Exec API 不可用",
                retryable=True,
                stop=False,
            )
        container_id = str(
            getattr(container, "id", "")
            or getattr(container, "name", "")
            or ""
        )
        workdir = "/workspace"
        if cwd:
            workdir = f"/workspace/{cwd}"
        try:
            created = api.exec_create(
                container_id,
                [str(shell_path), "-lc", str(command)],
                stdout=True,
                stderr=True,
                stdin=bool(allow_stdin),
                tty=False,
                privileged=False,
                user=(
                    f"{self.config.workspace_uid}:"
                    f"{self.config.workspace_gid}"
                ),
                workdir=workdir,
            )
            exec_id = (
                str(created.get("Id") or "")
                if isinstance(created, dict)
                else ""
            )
            if not exec_id or len(exec_id) > 128:
                raise RuntimeError("Docker Exec 标识无效")
        except SandboxServiceError:
            raise
        except Exception as exc:
            # exec_create 失败是干净失败：容器内没有新进程。
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Docker 无法创建 Sandbox Lease 进程",
                retryable=True,
                stop=False,
            ) from exc
        try:
            socket = api.exec_start(
                exec_id,
                detach=False,
                tty=False,
                socket=True,
            )
        except Exception as exc:
            error = SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Docker 无法启动 Sandbox Lease 进程",
                retryable=True,
                stop=False,
            )
            # exec_start 已发出，容器内可能已有未被追踪的进程——状态未知，
            # 调用方必须按整 Lease 回收兜底。
            error.exec_state_unknown = True
            raise error from exc
        return DockerExecHandle(api, exec_id, socket)


@dataclass(slots=True)
class ProcessRecord:
    process_id: str
    request_id: str
    request_fingerprint: str
    lease_id: str
    workspace_id: str
    profile_id: str
    controller_epoch: str
    image_digest: str
    quota_generation: int
    timeout_seconds: int
    allow_stdin: bool
    output: ProcessOutputBuffer
    handle: Any
    permit: ConcurrencyPermit
    created_at_unix: float
    started_at_unix: float
    execution_status: str = "running"
    process_state: str = "running"
    exit_code: int | None = None
    termination_reason: str = ""
    finished_at_unix: float = 0.0
    lease_recycled: bool = False
    affected_process_ids: tuple[str, ...] = ()
    cpu_time_ms: int = 0
    peak_memory_bytes: int = 0
    done: threading.Event = field(default_factory=threading.Event)
    stdin_lock: threading.Lock = field(default_factory=threading.Lock)
    stdin_requests: dict[str, tuple[str, dict[str, Any]]] = field(
        default_factory=dict
    )


class LeaseProcessManager:
    """每个 process_id 都归属于唯一当前 controller epoch 的 Lease。"""

    def __init__(
        self,
        config: SandboxdConfig,
        *,
        lease_backend: LeaseBackend,
        lease_store: LeaseStore,
        exec_adapter: Any | None = None,
        concurrency_limiter: ProfileConcurrencyLimiter | None = None,
        clock: Any = time.time,
        monotonic: Any = time.monotonic,
    ) -> None:
        self.config = config.validated()
        self.lease_backend = lease_backend
        self.lease_store = lease_store
        self.exec_adapter = exec_adapter or DockerExecAdapter(self.config)
        catalog = self.config.profile_catalog
        if catalog is None:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Profile catalog 不可用",
            )
        self.concurrency_limiter = (
            concurrency_limiter
            or ProfileConcurrencyLimiter(catalog)
        )
        self.clock = clock
        self.monotonic = monotonic
        self._records: dict[str, ProcessRecord] = {}
        self._request_locks: dict[str, threading.Lock] = {}
        self._request_locks_guard = threading.Lock()
        self._guard = threading.RLock()
        self.lease_backend.add_recycle_observer(
            self.on_lease_recycled
        )

    def _request_lock(self, request_id: str) -> threading.Lock:
        with self._request_locks_guard:
            return self._request_locks.setdefault(
                request_id,
                threading.Lock(),
            )

    @staticmethod
    def _fingerprint(
        *,
        lease_id: str,
        process_id: str,
        command: str,
        cwd: str,
        timeout_seconds: int,
    ) -> str:
        encoded = json.dumps(
            {
                "lease_id": lease_id,
                "process_id": process_id,
                "command_sha256": hashlib.sha256(
                    command.encode("utf-8")
                ).hexdigest(),
                "cwd": cwd,
                "timeout_seconds": int(timeout_seconds),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _record(self, process_id: str) -> ProcessRecord:
        process_id = validate_process_id(process_id)
        with self._guard:
            record = self._records.get(process_id)
        if record is None:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 进程不可访问",
            )
        return record

    @staticmethod
    def _terminal_status(reason: str) -> str:
        if reason == "completed":
            return "completed"
        if reason in _CANCEL_REASONS:
            return "cancelled"
        return "failed"

    def _active_processes(self, lease_id: str) -> list[dict[str, str]]:
        with self._guard:
            values = [
                {
                    "process_id": record.process_id,
                    "state": record.process_state,
                }
                for record in self._records.values()
                if (
                    record.lease_id == lease_id
                    and record.execution_status == "running"
                )
            ]
        return sorted(values, key=lambda item: item["process_id"])

    def _response(
        self,
        record: ProcessRecord,
        *,
        cursor: str = "",
        full: bool,
    ) -> dict[str, Any]:
        output = record.output.snapshot(cursor=cursor, full=full)
        data: dict[str, Any] = {
            "process_id": record.process_id,
            "lease_id": record.lease_id,
            "workspace_id": record.workspace_id,
            "profile_id": record.profile_id,
            "controller_epoch": record.controller_epoch,
            "execution_status": record.execution_status,
            "process_state": record.process_state,
            "exit_code": record.exit_code,
            "termination_reason": record.termination_reason,
            "next_cursor": output.next_cursor,
            "stdout_bytes": output.stdout_bytes,
            "stderr_bytes": output.stderr_bytes,
            "stdout_truncated": output.stdout_truncated,
            "stderr_truncated": output.stderr_truncated,
            "cpu_time_ms": record.cpu_time_ms,
            "peak_memory_bytes": record.peak_memory_bytes,
            "created_at_unix": record.created_at_unix,
            "started_at_unix": record.started_at_unix,
            "finished_at_unix": (
                record.finished_at_unix
                if record.finished_at_unix > 0
                else None
            ),
            "lease_recycled": record.lease_recycled,
            "affected_process_ids": list(
                record.affected_process_ids
            ),
            "active_processes": self._active_processes(
                record.lease_id
            ),
        }
        if record.lease_recycled:
            data["termination_scope"] = "lease"
            data["workspace_preserved"] = True
            data["runtime_preserved"] = True
        if full:
            data["stdout"] = output.stdout
            data["stderr"] = output.stderr
        else:
            data["stdout_delta"] = output.stdout
            data["stderr_delta"] = output.stderr
        return data

    def start(
        self,
        *,
        lease_id: str,
        request_id: str,
        command: str,
        cwd: str = "",
        yield_time_ms: int = 10_000,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        lease_id = validate_lease_id(lease_id)
        request_id = validate_request_id(request_id)
        process_id = validate_process_id(request_id)
        normalized_cwd = "/".join(
            validate_relative_path(cwd, allow_empty=True)
        )
        try:
            command_bytes = str(command).encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 命令编码无效",
            ) from exc
        if not command_bytes or len(command_bytes) > self.config.max_command_bytes:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 命令为空或超过安全长度上限",
            )
        raw_yield = yield_time_ms
        try:
            normalized_yield = int(yield_time_ms)
        except (TypeError, ValueError):
            normalized_yield = -1
        if (
            isinstance(raw_yield, bool)
            or not 0 <= normalized_yield <= 30_000
        ):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox yield 时间无效",
            )

        with self._guard:
            existing = self._records.get(process_id)
        if existing is not None:
            raw_existing_timeout = (
                existing.timeout_seconds
                if timeout_seconds is None
                else timeout_seconds
            )
            try:
                existing_timeout = int(raw_existing_timeout)
            except (TypeError, ValueError):
                existing_timeout = 0
            fingerprint = self._fingerprint(
                lease_id=lease_id,
                process_id=process_id,
                command=str(command),
                cwd=normalized_cwd,
                timeout_seconds=existing_timeout,
            )
            if (
                isinstance(raw_existing_timeout, bool)
                or existing.request_fingerprint != fingerprint
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.AUTHORIZATION_FAILED,
                    "幂等 request_id 已绑定其他 Sandbox 进程请求",
                )
            if normalized_yield > 0:
                existing.done.wait(normalized_yield / 1000)
            return self._response(existing, full=True)

        snapshot = self.lease_store.get(lease_id)
        if (
            snapshot is None
            or snapshot.controller_epoch
            != self.lease_store.controller_epoch
        ):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox Lease 已失效或不能启动新进程",
            )
        profile = self.config.profile(snapshot.profile_id)
        raw_timeout = (
            profile.default_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        try:
            timeout = int(raw_timeout)
        except (TypeError, ValueError):
            timeout = 0
        if (
            isinstance(raw_timeout, bool)
            or not 1 <= timeout <= int(profile.max_timeout_seconds)
        ):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 超时参数超过当前 Profile 上限",
            )
        fingerprint = self._fingerprint(
            lease_id=lease_id,
            process_id=process_id,
            command=str(command),
            cwd=normalized_cwd,
            timeout_seconds=timeout,
        )
        request_lock = self._request_lock(request_id)
        if not request_lock.acquire(blocking=False):
            raise SandboxServiceError(
                SandboxErrorCode.SANDBOX_BUSY,
                "相同 Sandbox 进程请求仍在启动",
                retryable=True,
                stop=False,
            )
        try:
            with self._guard:
                existing = self._records.get(process_id)
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise SandboxServiceError(
                        SandboxErrorCode.AUTHORIZATION_FAILED,
                        "幂等 request_id 已绑定其他 Sandbox 进程请求",
                    )
                if normalized_yield > 0:
                    existing.done.wait(normalized_yield / 1000)
                return self._response(existing, full=True)

            permit = self.concurrency_limiter.acquire(
                profile.profile_id,
                lease_id=lease_id,
            )
            handle: Any | None = None
            record: ProcessRecord | None = None
            try:
                with self.lease_backend.workspace_files.maintenance.execution(
                    snapshot.workspace_id,
                    quota_generation=snapshot.quota_generation,
                ):
                    with self.lease_backend.starting_process(
                        lease_id,
                        process_id,
                    ) as (current, container, current_profile):
                        if (
                            current.workspace_id != snapshot.workspace_id
                            or current.profile_id != snapshot.profile_id
                            or current.quota_generation
                            != snapshot.quota_generation
                        ):
                            raise SandboxServiceError(
                                SandboxErrorCode.AUTHORIZATION_FAILED,
                                "Sandbox Lease 归属在启动期间已变化",
                            )
                        handle = self.exec_adapter.start(
                            container=container,
                            process_id=process_id,
                            shell_path=current_profile.shell_path,
                            command=str(command),
                            cwd=normalized_cwd,
                            allow_stdin=bool(
                                current_profile.allow_stdin
                            ),
                        )
                        now_unix = float(self.clock())
                        record = ProcessRecord(
                            process_id=process_id,
                            request_id=request_id,
                            request_fingerprint=fingerprint,
                            lease_id=lease_id,
                            workspace_id=current.workspace_id,
                            profile_id=current.profile_id,
                            controller_epoch=current.controller_epoch,
                            image_digest=current.image_digest,
                            quota_generation=current.quota_generation,
                            timeout_seconds=timeout,
                            allow_stdin=bool(
                                current_profile.allow_stdin
                            ),
                            output=ProcessOutputBuffer(
                                stdout_limit_bytes=(
                                    self.config.stdout_limit_bytes
                                ),
                                stderr_limit_bytes=(
                                    self.config.stderr_limit_bytes
                                ),
                                hard_limit_bytes=(
                                    self.config.hard_output_limit_bytes
                                ),
                            ),
                            handle=handle,
                            permit=permit,
                            created_at_unix=now_unix,
                            started_at_unix=now_unix,
                        )
                        with self._guard:
                            self._records[process_id] = record
            except Exception as exc:
                # 只有容器内可能已存在未被追踪的进程时才回收整个 Lease：
                # handle 已取得（exec 确实启动）或 exec_start 状态未知。
                # exec_create 干净失败不摧毁同 Lease 的其他活动进程。
                exec_may_be_running = (
                    handle is not None
                    or getattr(exc, "exec_state_unknown", False)
                )
                if exec_may_be_running:
                    try:
                        self.lease_backend.recycle(
                            lease_id,
                            reason="lease_recycled",
                        )
                    except Exception:
                        pass
                if handle is not None:
                    handle.close()
                permit.release()
                if record is not None:
                    with self._guard:
                        self._records.pop(process_id, None)
                raise

            reader = threading.Thread(
                target=self._read_process,
                args=(record,),
                name=f"sandbox-lease-output-{process_id}",
                daemon=True,
            )
            watchdog = threading.Thread(
                target=self._watch_process,
                args=(record,),
                name=f"sandbox-lease-timeout-{process_id}",
                daemon=True,
            )
            reader.start()
            watchdog.start()
            if normalized_yield > 0:
                record.done.wait(normalized_yield / 1000)
            return self._response(record, full=True)
        finally:
            request_lock.release()

    def _watch_process(self, record: ProcessRecord) -> None:
        if record.done.wait(record.timeout_seconds):
            return
        self._recycle_for_process(
            record,
            reason="execution_timeout",
        )

    def _read_process(self, record: ProcessRecord) -> None:
        try:
            for stream, payload in record.handle.frames():
                if record.done.is_set():
                    return
                exceeded = record.output.feed(
                    stdout=payload if int(stream) == 1 else None,
                    stderr=payload if int(stream) == 2 else None,
                )
                if exceeded:
                    self._recycle_for_process(
                        record,
                        reason="output_limit_exceeded",
                    )
                    return
            if record.done.is_set():
                return
            inspection: dict[str, Any] = {}
            for _attempt in range(10):
                inspection = record.handle.inspect()
                if inspection.get("Running") is not True:
                    break
                if record.done.wait(0.05):
                    return
            if inspection.get("Running") is True:
                self._recycle_for_process(
                    record,
                    reason="lease_recycled",
                )
                return
            health = self.lease_backend.process_lease_health(
                record.lease_id
            )
            if health.get("oom_killed") is True:
                self._recycle_for_process(
                    record,
                    reason="lease_oom",
                )
                return
            if (
                health.get("present") is not True
                or health.get("running") is not True
            ):
                self._recycle_for_process(
                    record,
                    reason="lease_recycled",
                )
                return
            exit_code = inspection.get("ExitCode")
            if isinstance(exit_code, bool) or not isinstance(exit_code, int):
                self._recycle_for_process(
                    record,
                    reason="lease_recycled",
                )
                return
            self._complete_process(
                record,
                exit_code=exit_code,
                reason="completed" if exit_code == 0 else "nonzero_exit",
            )
        except Exception:
            if not record.done.is_set():
                self._recycle_for_process(
                    record,
                    reason="lease_recycled",
                )
        finally:
            record.handle.close()

    def _complete_process(
        self,
        record: ProcessRecord,
        *,
        exit_code: int,
        reason: str,
    ) -> None:
        if record.done.is_set():
            return
        snapshot = self.lease_backend.finish_process(
            record.lease_id,
            record.process_id,
        )
        if snapshot is None:
            self._recycle_for_process(
                record,
                reason="lease_recycled",
            )
            return
        with self._guard:
            if record.done.is_set():
                return
            record.exit_code = int(exit_code)
            record.termination_reason = reason
            record.execution_status = self._terminal_status(reason)
            record.process_state = "exited"
            record.finished_at_unix = float(self.clock())
            record.affected_process_ids = (record.process_id,)
            record.done.set()
        try:
            self.lease_backend.workspace_files.usage_ledger.mark_dirty(
                record.workspace_id
            )
        except SandboxServiceError:
            pass
        record.permit.release()
        record.handle.close()

    def _recycle_for_process(
        self,
        record: ProcessRecord,
        *,
        reason: str,
    ) -> dict[str, Any]:
        if record.done.is_set():
            return self._response(record, full=True)
        result = self.lease_backend.recycle(
            record.lease_id,
            reason=reason,
        )
        if not result.get("lease_recycled"):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox Lease 未能完成进程树回收",
                retryable=True,
                stop=False,
            )
        return self._response(record, full=True)

    def on_lease_recycled(
        self,
        lease_id: str,
        reason: str,
        affected_process_ids: tuple[str, ...],
    ) -> None:
        lease_id = validate_lease_id(lease_id)
        normalized_reason = str(reason or "lease_recycled")[:64]
        affected = set(affected_process_ids)
        with self._guard:
            records = [
                record
                for record in self._records.values()
                if (
                    record.lease_id == lease_id
                    and record.execution_status == "running"
                )
            ]
            affected.update(record.process_id for record in records)
            normalized_affected = tuple(sorted(affected))
            for record in records:
                record.execution_status = self._terminal_status(
                    normalized_reason
                )
                record.process_state = "lost"
                record.exit_code = None
                record.termination_reason = normalized_reason
                record.finished_at_unix = float(self.clock())
                record.lease_recycled = True
                record.affected_process_ids = normalized_affected
                record.done.set()
        for record in records:
            try:
                self.lease_backend.workspace_files.usage_ledger.mark_dirty(
                    record.workspace_id
                )
            except SandboxServiceError:
                pass
            record.handle.close()
            record.permit.release()

    def get(self, process_id: str, *, cursor: str = "") -> dict[str, Any]:
        record = self._record(process_id)
        return self._response(record, cursor=cursor, full=False)

    def write_stdin(
        self,
        process_id: str,
        *,
        request_id: str,
        chars: str,
    ) -> dict[str, Any]:
        record = self._record(process_id)
        request_id = validate_request_id(request_id)
        if (
            record.execution_status != "running"
            or not record.allow_stdin
        ):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 进程不可访问",
            )
        try:
            payload = str(chars).encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox stdin 编码无效",
            ) from exc
        if not payload or len(payload) > 64 * 1024:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox stdin 为空或超过安全长度上限",
            )
        fingerprint = hashlib.sha256(payload).hexdigest()
        with record.stdin_lock:
            existing = record.stdin_requests.get(request_id)
            if existing is not None:
                if existing[0] != fingerprint:
                    raise SandboxServiceError(
                        SandboxErrorCode.AUTHORIZATION_FAILED,
                        "幂等 request_id 已绑定其他 stdin 请求",
                    )
                return dict(existing[1])
            if record.execution_status != "running":
                raise SandboxServiceError(
                    SandboxErrorCode.AUTHORIZATION_FAILED,
                    "Sandbox 进程不可访问",
                )
            try:
                written = int(record.handle.write(payload))
            except Exception as exc:
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Sandbox stdin 写入失败",
                    retryable=True,
                    stop=False,
                ) from exc
            response = {
                "process_id": record.process_id,
                "lease_id": record.lease_id,
                "workspace_id": record.workspace_id,
                "profile_id": record.profile_id,
                "controller_epoch": record.controller_epoch,
                "execution_status": record.execution_status,
                "written_bytes": written,
                "active_processes": self._active_processes(
                    record.lease_id
                ),
            }
            record.stdin_requests[request_id] = (
                fingerprint,
                dict(response),
            )
            return response

    def terminate(
        self,
        process_id: str,
        *,
        request_id: str,
    ) -> dict[str, Any]:
        validate_request_id(request_id)
        record = self._record(process_id)
        if record.execution_status == "running":
            self._recycle_for_process(record, reason="cancelled")
        return self._response(record, full=True)

    def list_facts(self) -> list[dict[str, Any]]:
        """管理端只返回账本收敛所需元数据，不含命令和输出正文。"""

        with self._guard:
            records = list(self._records.values())
        facts = []
        for record in records:
            output = record.output.snapshot(full=True)
            facts.append({
                "process_id": record.process_id,
                "lease_id": record.lease_id,
                "workspace_id": record.workspace_id,
                "profile_id": record.profile_id,
                "controller_epoch": record.controller_epoch,
                "execution_status": record.execution_status,
                "process_state": record.process_state,
                "exit_code": record.exit_code,
                "termination_reason": record.termination_reason,
                "created_at_unix": record.created_at_unix,
                "started_at_unix": record.started_at_unix,
                "finished_at_unix": (
                    record.finished_at_unix
                    if record.finished_at_unix > 0
                    else None
                ),
                "stdout_bytes": output.stdout_bytes,
                "stderr_bytes": output.stderr_bytes,
                "stdout_truncated": output.stdout_truncated,
                "stderr_truncated": output.stderr_truncated,
                "cpu_time_ms": record.cpu_time_ms,
                "peak_memory_bytes": record.peak_memory_bytes,
                "lease_recycled": record.lease_recycled,
                "affected_process_ids": list(
                    record.affected_process_ids
                ),
            })
        return sorted(facts, key=lambda item: item["process_id"])

    def close(self) -> None:
        with self._guard:
            records = list(self._records.values())
        for record in records:
            record.handle.close()


__all__ = [
    "DockerExecAdapter",
    "DockerExecHandle",
    "LeaseProcessManager",
    "ProcessRecord",
]

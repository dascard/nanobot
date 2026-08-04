"""sandboxd 的安全文件、磁盘水位和资产 staging。"""

from __future__ import annotations

import base64
import codecs
import fnmatch
import hashlib
import json
import logging
import os
import secrets
import shlex
import shutil
import stat
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pathspec
import regex

from core.sandbox.asset_store import LocalAssetStore
from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.paths import (
    SafeWorkspaceFilesystem,
    SandboxStorageLayout,
    validate_relative_path,
    validate_sha256,
    validate_workspace_id,
)
from sandboxd.config import SandboxdConfig
from sandboxd.lease_store import validate_lease_id
from sandboxd.unified_patch import apply_unified_patch


logger = logging.getLogger("nanobot.sandboxd.filesystem")


WORKSPACE_PROTOCOL_VERSION = 2
MAX_READ_SCAN_BYTES = 32 * 1024 * 1024
MAX_SEARCH_SCAN_BYTES = 32 * 1024 * 1024
MAX_SEARCH_FILES = 2_000
MAX_SEARCH_ENTRIES = 20_000
MAX_SEARCH_SECONDS = 2.0
MAX_REGEX_LINE_SECONDS = 0.02
MAX_RENDERED_LINE_CHARS = 4_000
MAX_EDIT_OPERATIONS = 50
_EDIT_JOURNAL_PREFIX = ".nanobot-workspace-edit-"
_FIXED_IGNORED_DIRECTORIES = frozenset({
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "env",
    "node_modules",
    "venv",
})


def _resolve_workspace_path(
    *,
    cwd: str,
    path: str,
    allow_empty: bool = False,
) -> str:
    cwd_components = validate_relative_path(cwd, allow_empty=True)
    path_components = validate_relative_path(path, allow_empty=allow_empty)
    combined = (*cwd_components, *path_components)
    if not combined and not allow_empty:
        validate_relative_path("", allow_empty=False)
    return "/".join(combined)


def _looks_binary(content: bytes) -> bool:
    """基于文件头内容特征判断二进制，不依赖任意字节切片解码。"""

    sample = content[:8192]
    if b"\x00" in sample:
        return True
    if not sample:
        return False
    controls = sum(
        byte < 32 and byte not in {8, 9, 10, 12, 13}
        for byte in sample
    )
    return controls / len(sample) > 0.10


def _decode_utf8_prefix(content: bytes, *, final: bool) -> str:
    try:
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        return decoder.decode(content, final=final)
    except UnicodeDecodeError as exc:
        raise SandboxServiceError(
            SandboxErrorCode.UNSUPPORTED_FILE_TYPE,
            "Workspace 文件不是合法 UTF-8 文本",
        ) from exc


def _cursor_fingerprint(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _encode_search_cursor(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_search_cursor(
    raw: str,
    *,
    expected_fingerprint: str,
    mode: str,
) -> dict[str, int]:
    if not raw:
        return {"index": 0, "byte_offset": 0, "line": 1}
    try:
        padding = "=" * (-len(raw) % 4)
        value = json.loads(
            base64.urlsafe_b64decode(raw + padding).decode("utf-8")
        )
        if (
            not isinstance(value, dict)
            or value.get("v") != 1
            or value.get("mode") != mode
            or value.get("fingerprint") != expected_fingerprint
        ):
            raise ValueError
        result = {
            "index": int(value.get("index", 0)),
            "byte_offset": int(value.get("byte_offset", 0)),
            "line": int(value.get("line", 1)),
        }
        if (
            result["index"] < 0
            or result["byte_offset"] < 0
            or result["line"] < 1
        ):
            raise ValueError
        return result
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise SandboxServiceError(
            SandboxErrorCode.INVALID_PATH,
            "工作区搜索游标无效或不属于当前查询",
        ) from exc


def _next_search_cursor(
    *,
    mode: str,
    fingerprint: str,
    index: int,
    byte_offset: int = 0,
    line: int = 1,
) -> str:
    return _encode_search_cursor({
        "v": 1,
        "mode": mode,
        "fingerprint": fingerprint,
        "index": int(index),
        "byte_offset": int(byte_offset),
        "line": int(line),
    })


@dataclass(frozen=True)
class DiskState:
    total_bytes: int
    used_bytes: int
    free_bytes: int
    used_percent: float


class DiskGuard:
    def __init__(self, config: SandboxdConfig) -> None:
        self.config = config

    def state(self) -> DiskState:
        try:
            usage = shutil.disk_usage(self.config.data_root)
        except OSError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 数据盘不可用",
                retryable=True,
                stop=False,
            ) from exc
        used_percent = 100.0 if usage.total <= 0 else usage.used * 100.0 / usage.total
        return DiskState(
            total_bytes=int(usage.total),
            used_bytes=int(usage.used),
            free_bytes=int(usage.free),
            used_percent=used_percent,
        )

    def ensure_available(self, *, additional_bytes: int = 0) -> DiskState:
        state = self.state()
        if (
            state.used_percent >= self.config.disk_max_percent
            or state.free_bytes - max(0, int(additional_bytes))
            < self.config.disk_min_free_bytes
        ):
            raise SandboxServiceError(
                SandboxErrorCode.DISK_PRESSURE,
                "磁盘水位已达到保护阈值，暂时拒绝新写入和执行",
                retryable=True,
                hint="等待运维释放容量后再重试",
                stop=False,
            )
        return state


def directory_usage(filesystem: SafeWorkspaceFilesystem, path: str = "") -> int:
    total = 0
    pending = [path]
    visited_directories = 0
    while pending:
        current = pending.pop()
        visited_directories += 1
        if visited_directories > 10_000:
            raise SandboxServiceError(
                SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
                "工作区目录数量超过安全核算上限",
            )
        for entry in filesystem.list_entries(current):
            if entry.type == "file":
                total += entry.size_bytes
            elif entry.type == "directory":
                pending.append(entry.path)
    return total


@dataclass(frozen=True, slots=True)
class WorkspaceUsageSnapshot:
    workspace_id: str
    workspace_bytes: int
    runtime_bytes: int
    dirty: bool


@dataclass(frozen=True, slots=True)
class RunGrowthReservation:
    workspace_id: str
    growth_bytes: int


@dataclass(slots=True)
class WorkspaceMaintenanceGateState:
    generation: int = 0
    applied_generation: int = 0
    quiescing: bool = False
    maintenance_active: bool = False
    active_executions: int = 0


class WorkspaceMaintenanceCoordinator:
    """按 Workspace 隔离执行入口与配额维护窗口。"""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._states: dict[str, WorkspaceMaintenanceGateState] = {}

    def _state_unlocked(
        self,
        workspace_id: str,
    ) -> WorkspaceMaintenanceGateState:
        return self._states.setdefault(
            workspace_id,
            WorkspaceMaintenanceGateState(),
        )

    @contextmanager
    def execution(
        self,
        workspace_id: str,
        *,
        quota_generation: int = 0,
    ) -> Iterator[None]:
        """领取短期执行门禁；quiescing 后的新请求全部快速失败。"""

        workspace_id = validate_workspace_id(workspace_id)
        requested_generation = int(quota_generation or 0)
        with self._condition:
            state = self._state_unlocked(workspace_id)
            if state.quiescing or state.maintenance_active:
                raise SandboxServiceError(
                    SandboxErrorCode.SANDBOX_BUSY,
                    "目标 Workspace 正在进行配额维护",
                    retryable=True,
                    stop=False,
                )
            if (
                requested_generation > 0
                and state.applied_generation > 0
                and requested_generation != state.applied_generation
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.SANDBOX_BUSY,
                    "目标 Workspace 配额 generation 尚未应用",
                    retryable=True,
                    stop=False,
                )
            state.active_executions += 1
        try:
            yield
        finally:
            with self._condition:
                state = self._state_unlocked(workspace_id)
                state.active_executions = max(
                    0,
                    state.active_executions - 1,
                )
                self._condition.notify_all()

    @contextmanager
    def quota_maintenance(
        self,
        workspace_id: str,
        *,
        generation: int,
    ) -> Iterator[None]:
        """关闭目标执行门禁；失败后保持 quiescing，等待显式重试。"""

        workspace_id = validate_workspace_id(workspace_id)
        raw_generation = generation
        try:
            generation = int(generation)
        except (TypeError, ValueError):
            generation = 0
        if isinstance(raw_generation, bool) or generation < 1:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Workspace 配额维护 generation 无效",
            )
        with self._condition:
            state = self._state_unlocked(workspace_id)
            if generation < max(
                state.generation,
                state.applied_generation,
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.AUTHORIZATION_FAILED,
                    "Workspace 配额维护请求已过期",
                )
            state.generation = generation
            state.quiescing = True
            if state.maintenance_active:
                raise SandboxServiceError(
                    SandboxErrorCode.SANDBOX_BUSY,
                    "目标 Workspace 已有配额维护任务",
                    retryable=True,
                    stop=False,
                )
            if state.active_executions:
                raise SandboxServiceError(
                    SandboxErrorCode.SANDBOX_BUSY,
                    "目标 Workspace 仍有执行正在退出",
                    retryable=True,
                    stop=False,
                )
            state.maintenance_active = True
        try:
            yield
        except BaseException:
            with self._condition:
                state = self._state_unlocked(workspace_id)
                state.maintenance_active = False
                state.quiescing = True
                self._condition.notify_all()
            raise
        else:
            with self._condition:
                state = self._state_unlocked(workspace_id)
                state.applied_generation = generation
                state.maintenance_active = False
                state.quiescing = False
                self._condition.notify_all()

    def state(self, workspace_id: str) -> WorkspaceMaintenanceGateState:
        workspace_id = validate_workspace_id(workspace_id)
        with self._condition:
            state = self._state_unlocked(workspace_id)
            return WorkspaceMaintenanceGateState(
                generation=state.generation,
                applied_generation=state.applied_generation,
                quiescing=state.quiescing,
                maintenance_active=state.maintenance_active,
                active_executions=state.active_executions,
            )


class WorkspaceUsageLedger:
    """内存 usage cache；project quota 才是宿主写入的硬边界。"""

    def __init__(self, layout: SandboxStorageLayout) -> None:
        self.layout = layout
        self._workspace_usage: dict[str, int] = {}
        self._runtime_usage: dict[str, int] = {}
        self._dirty: set[str] = set()
        self._revisions: dict[str, int] = {}
        self._guard = threading.RLock()

    def initialize(
        self,
        workspace_id: str,
        *,
        scan_existing: bool,
    ) -> WorkspaceUsageSnapshot:
        workspace_id = validate_workspace_id(workspace_id)
        with self._guard:
            if workspace_id in self._workspace_usage:
                return self._snapshot_unlocked(workspace_id)

        workspace_bytes = 0
        runtime_bytes = 0
        if scan_existing:
            workspace_bytes = directory_usage(SafeWorkspaceFilesystem(
                self.layout.workspace_data_dir(workspace_id),
            ))
            runtime_bytes = directory_usage(SafeWorkspaceFilesystem(
                self.layout.runtime_root / workspace_id,
            ))
        with self._guard:
            self._workspace_usage.setdefault(workspace_id, int(workspace_bytes))
            self._runtime_usage.setdefault(workspace_id, int(runtime_bytes))
            self._revisions.setdefault(workspace_id, 0)
            return self._snapshot_unlocked(workspace_id)

    def _snapshot_unlocked(self, workspace_id: str) -> WorkspaceUsageSnapshot:
        try:
            workspace_bytes = self._workspace_usage[workspace_id]
            runtime_bytes = self._runtime_usage[workspace_id]
        except KeyError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Workspace 用量缓存尚未初始化",
                retryable=True,
                stop=False,
            ) from exc
        return WorkspaceUsageSnapshot(
            workspace_id=workspace_id,
            workspace_bytes=int(workspace_bytes),
            runtime_bytes=int(runtime_bytes),
            dirty=workspace_id in self._dirty,
        )

    def snapshot(self, workspace_id: str) -> WorkspaceUsageSnapshot:
        workspace_id = validate_workspace_id(workspace_id)
        with self._guard:
            return self._snapshot_unlocked(workspace_id)

    def total_workspace_bytes(self) -> int:
        with self._guard:
            return sum(self._workspace_usage.values())

    def adjust_workspace(self, workspace_id: str, delta_bytes: int) -> int:
        workspace_id = validate_workspace_id(workspace_id)
        with self._guard:
            current = self._snapshot_unlocked(workspace_id).workspace_bytes
            updated = max(0, current + int(delta_bytes))
            self._workspace_usage[workspace_id] = updated
            self._revisions[workspace_id] = (
                self._revisions.get(workspace_id, 0) + 1
            )
            return updated

    def mark_dirty(self, workspace_id: str) -> None:
        workspace_id = validate_workspace_id(workspace_id)
        with self._guard:
            self._snapshot_unlocked(workspace_id)
            self._dirty.add(workspace_id)
            self._revisions[workspace_id] = (
                self._revisions.get(workspace_id, 0) + 1
            )

    def reconcile(self, workspace_id: str) -> WorkspaceUsageSnapshot:
        """在执行热路径之外递归扫描，并校正 Workspace/Runtime cache。"""

        workspace_id = validate_workspace_id(workspace_id)
        with self._guard:
            revision_before_scan = self._revisions.get(workspace_id, 0)
        workspace_bytes = directory_usage(SafeWorkspaceFilesystem(
            self.layout.workspace_data_dir(workspace_id),
        ))
        runtime_path = self.layout.runtime_root / workspace_id
        try:
            runtime_metadata = runtime_path.lstat()
        except FileNotFoundError:
            runtime_bytes = 0
        else:
            if (
                not stat.S_ISDIR(runtime_metadata.st_mode)
                or stat.S_ISLNK(runtime_metadata.st_mode)
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Workspace Runtime 目录不可用",
                    retryable=True,
                    stop=False,
                )
            runtime_bytes = directory_usage(SafeWorkspaceFilesystem(runtime_path))
        with self._guard:
            if self._revisions.get(workspace_id, 0) != revision_before_scan:
                self._dirty.add(workspace_id)
                return self._snapshot_unlocked(workspace_id)
            self._workspace_usage[workspace_id] = int(workspace_bytes)
            self._runtime_usage[workspace_id] = int(runtime_bytes)
            self._revisions.setdefault(workspace_id, revision_before_scan)
            self._dirty.discard(workspace_id)
            return self._snapshot_unlocked(workspace_id)

    def discover_workspace_ids(self) -> list[str]:
        self.layout.ensure_roots()
        discovered: list[str] = []
        try:
            with os.scandir(self.layout.workspaces_root) as shards:
                for shard in shards:
                    if not shard.is_dir(follow_symlinks=False):
                        continue
                    with os.scandir(shard.path) as workspaces:
                        for entry in workspaces:
                            if not entry.is_dir(follow_symlinks=False):
                                continue
                            try:
                                workspace_id = validate_workspace_id(entry.name)
                            except SandboxServiceError:
                                continue
                            if workspace_id[:2] == shard.name:
                                discovered.append(workspace_id)
        except OSError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Workspace 周期对账目录发现失败",
                retryable=True,
                stop=False,
            ) from exc
        return sorted(discovered)

    def reconcile_all(self) -> dict[str, int]:
        reconciled = 0
        failed = 0
        for workspace_id in self.discover_workspace_ids():
            try:
                self.reconcile(workspace_id)
            except SandboxServiceError:
                failed += 1
                logger.warning(
                    "Workspace 用量周期对账失败 workspace_id=%s",
                    workspace_id,
                    exc_info=True,
                )
            else:
                reconciled += 1
        return {"reconciled": reconciled, "failed": failed}


class WorkspaceFileService:
    def __init__(self, config: SandboxdConfig) -> None:
        self.config = config
        self.layout = SandboxStorageLayout(config.data_root)
        self.disk_guard = DiskGuard(config)
        self.usage_ledger = WorkspaceUsageLedger(self.layout)
        self.maintenance = WorkspaceMaintenanceCoordinator()
        self._workspace_write_locks: dict[str, threading.Lock] = {}
        self._workspace_write_locks_guard = threading.Lock()
        self._quota_guard = threading.Lock()
        self._run_growth_reservations: dict[str, RunGrowthReservation] = {}
        self._usage_reconciler_stop = threading.Event()
        self._usage_reconciler_thread: threading.Thread | None = None

    def acquire_workspace_write(self, workspace_id: str) -> threading.Lock:
        """只串行化精确文件写操作；命令执行不领取这把锁。"""

        normalized = validate_workspace_id(workspace_id)
        with self._workspace_write_locks_guard:
            lock = self._workspace_write_locks.setdefault(
                normalized,
                threading.Lock(),
            )
        if not lock.acquire(blocking=False):
            raise SandboxServiceError(
                SandboxErrorCode.SANDBOX_BUSY,
                "当前 Workspace 正在执行其他文件写操作",
                retryable=True,
                stop=False,
            )
        return lock

    def acquire_workspace_mutation(self, workspace_id: str) -> threading.Lock:
        """兼容旧调用名；边界已收窄为文件写互斥。"""

        return self.acquire_workspace_write(workspace_id)

    def total_workspace_usage(self) -> int:
        """显式全量核算；只供周期对账、诊断和迁移使用。"""

        self.layout.ensure_roots()
        total = 0
        try:
            with os.scandir(self.layout.workspaces_root) as shards:
                for shard in shards:
                    if not shard.is_dir(follow_symlinks=False):
                        continue
                    with os.scandir(shard.path) as workspaces:
                        for entry in workspaces:
                            if not entry.is_dir(follow_symlinks=False):
                                continue
                            try:
                                workspace_id = validate_workspace_id(entry.name)
                            except SandboxServiceError:
                                continue
                            if workspace_id[:2] != shard.name:
                                continue
                            data_path = Path(entry.path) / "data"
                            metadata = data_path.lstat()
                            if not stat.S_ISDIR(metadata.st_mode):
                                raise OSError("invalid workspace data directory")
                            total += directory_usage(
                                SafeWorkspaceFilesystem(data_path),
                            )
        except SandboxServiceError:
            raise
        except OSError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Workspace 总空间核算失败",
                retryable=True,
                stop=False,
            ) from exc
        return total

    def reserve_run_capacity(
        self,
        run_id: str,
        workspace_id: str,
        *,
        workspace_quota_bytes: int,
    ) -> tuple[int, int]:
        """基于 cache 预留软预算；宿主 project quota 负责硬限制。"""

        workspace_id = validate_workspace_id(workspace_id)
        effective_workspace_quota = min(
            max(1, int(workspace_quota_bytes)),
            self.config.workspace_quota_bytes,
        )
        with self._quota_guard:
            if run_id in self._run_growth_reservations:
                raise SandboxServiceError(
                    SandboxErrorCode.SANDBOX_BUSY,
                    "Sandbox 运行空间仍被占用",
                    retryable=True,
                    stop=False,
                )
            workspace_usage = self.usage_ledger.snapshot(
                workspace_id,
            ).workspace_bytes
            if workspace_usage > effective_workspace_quota:
                raise SandboxServiceError(
                    SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
                    "工作区空间配额已用完",
                )
            total_usage = self.usage_ledger.total_workspace_bytes()
            reserved_growth = sum(
                reservation.growth_bytes
                for reservation in self._run_growth_reservations.values()
            )
            available_growth = (
                self.config.total_quota_bytes
                - total_usage
                - reserved_growth
            )
            if available_growth < 0:
                raise SandboxServiceError(
                    SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
                    "Sandbox 总空间预算已用完",
                )
            workspace_growth = effective_workspace_quota - workspace_usage
            granted_growth = min(workspace_growth, available_growth)
            self._run_growth_reservations[run_id] = RunGrowthReservation(
                workspace_id=workspace_id,
                growth_bytes=granted_growth,
            )
            return workspace_usage, workspace_usage + granted_growth

    def release_run_capacity(self, run_id: str) -> None:
        with self._quota_guard:
            reservation = self._run_growth_reservations.pop(run_id, None)
        if reservation is not None:
            self.usage_ledger.mark_dirty(reservation.workspace_id)

    def usage_snapshot(self, workspace_id: str) -> WorkspaceUsageSnapshot:
        return self.usage_ledger.snapshot(workspace_id)

    def reconcile_workspace_usage(
        self,
        workspace_id: str,
    ) -> WorkspaceUsageSnapshot:
        return self.usage_ledger.reconcile(workspace_id)

    def reconcile_all_usage(self) -> dict[str, int]:
        return self.usage_ledger.reconcile_all()

    def _usage_reconciler_loop(self) -> None:
        interval = float(self.config.usage_reconcile_interval_seconds)
        while not self._usage_reconciler_stop.is_set():
            try:
                result = self.reconcile_all_usage()
                logger.info(
                    "Workspace 用量周期对账完成 reconciled=%d failed=%d",
                    result["reconciled"],
                    result["failed"],
                )
            except Exception:
                logger.error("Workspace 用量周期对账异常", exc_info=True)
            self._usage_reconciler_stop.wait(interval)

    def start_usage_reconciler(self) -> None:
        thread = self._usage_reconciler_thread
        if thread is not None and thread.is_alive():
            return
        self._usage_reconciler_stop.clear()
        self._usage_reconciler_thread = threading.Thread(
            target=self._usage_reconciler_loop,
            name="sandbox-usage-reconciler",
            daemon=True,
        )
        self._usage_reconciler_thread.start()

    def stop_usage_reconciler(self, timeout: float = 5.0) -> None:
        self._usage_reconciler_stop.set()
        thread = self._usage_reconciler_thread
        if thread is not None:
            thread.join(timeout=max(0.0, float(timeout)))
        self._usage_reconciler_thread = None

    @staticmethod
    def _secure_workspace_directory(path: Path, uid: int, gid: int) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            directory_fd = os.open(path, flags)
            try:
                os.fchown(directory_fd, uid, gid)
                os.fchmod(directory_fd, 0o700)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Workspace 目录不可用",
                retryable=True,
                stop=False,
            ) from exc

    def ensure_workspace(self, workspace_id: str) -> dict[str, Any]:
        workspace_id = validate_workspace_id(workspace_id)
        self.disk_guard.ensure_available()
        workspace_candidate = self.layout.workspace_data_dir(workspace_id)
        runtime_candidate = self.layout.runtime_root / workspace_id
        existed = workspace_candidate.exists() or runtime_candidate.exists()
        workspace_path = self.layout.ensure_workspace(workspace_id)
        runtime_path = self.layout.ensure_runtime(workspace_id)
        self._secure_workspace_directory(
            workspace_path,
            self.config.workspace_uid,
            self.config.workspace_gid,
        )
        self._secure_workspace_directory(
            runtime_path,
            self.config.workspace_uid,
            self.config.workspace_gid,
        )
        usage = self.usage_ledger.initialize(
            workspace_id,
            scan_existing=False,
        )
        if existed:
            self.usage_ledger.mark_dirty(workspace_id)
            usage = self.usage_ledger.snapshot(workspace_id)
        return {
            "workspace_id": workspace_id,
            "ensured": True,
            "workspace_used_bytes": usage.workspace_bytes,
            "runtime_used_bytes": usage.runtime_bytes,
            "usage_reconciliation_pending": usage.dirty,
        }

    def filesystem(self, workspace_id: str) -> SafeWorkspaceFilesystem:
        workspace_id = validate_workspace_id(workspace_id)
        return SafeWorkspaceFilesystem(
            self.layout.workspace_data_dir(workspace_id),
            write_uid=self.config.workspace_uid,
            write_gid=self.config.workspace_gid,
        )

    @staticmethod
    def _fd_sha256(file_fd: int, size_bytes: int) -> str:
        digest = hashlib.sha256()
        position = 0
        while position < size_bytes:
            chunk = os.pread(
                file_fd,
                min(1024 * 1024, size_bytes - position),
                position,
            )
            if not chunk:
                break
            digest.update(chunk)
            position += len(chunk)
        if position != size_bytes:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "资产存储暂时不可用",
                retryable=True,
                stop=False,
            )
        return digest.hexdigest()

    def materialize_asset(
        self,
        workspace_id: str,
        *,
        sha256: str,
        storage_key: str,
        path: str,
        quota_bytes: int,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """把尚未登记的 CAS 暂存内容安全写入 owner Workspace。"""

        workspace_id = validate_workspace_id(workspace_id)
        digest = validate_sha256(sha256)
        expected_key = self.layout.asset_storage_key(digest)
        if str(storage_key or "") != expected_key:
            raise SandboxServiceError(
                SandboxErrorCode.ASSET_NOT_AUTHORIZED,
                "暂存资产标识无效",
            )
        normalized_path = "/".join(validate_relative_path(path))
        effective_quota = min(
            max(1, int(quota_bytes)),
            self.config.workspace_quota_bytes,
        )
        source_filesystem = SafeWorkspaceFilesystem(self.layout.assets_root)
        workspace_lock = self.acquire_workspace_write(workspace_id)
        try:
            target_filesystem = self.filesystem(workspace_id)
            with source_filesystem.open_regular_file(expected_key) as (
                source_fd,
                size_bytes,
            ):
                if size_bytes > self.config.asset_max_bytes:
                    raise SandboxServiceError(
                        SandboxErrorCode.ASSET_TOO_LARGE,
                        "资产超过允许的单文件大小上限",
                    )
                if not secrets.compare_digest(
                    self._fd_sha256(source_fd, size_bytes),
                    digest,
                ):
                    raise SandboxServiceError(
                        SandboxErrorCode.RUNTIME_UNAVAILABLE,
                        "资产存储完整性校验失败",
                    )
                previous_size = 0
                try:
                    with target_filesystem.open_regular_file(normalized_path) as (
                        target_fd,
                        current_size,
                    ):
                        previous_size = current_size
                        current_sha256 = self._fd_sha256(
                            target_fd,
                            current_size,
                        )
                    if secrets.compare_digest(current_sha256, digest):
                        current_usage = self.usage_ledger.snapshot(
                            workspace_id
                        ).workspace_bytes
                        return {
                            "path": normalized_path,
                            "sha256": digest,
                            "size_bytes": size_bytes,
                            "previous_size_bytes": previous_size,
                            "used_bytes": current_usage,
                            "usage_delta_bytes": 0,
                            "materialized": True,
                            "idempotent": True,
                        }
                    if not overwrite:
                        raise SandboxServiceError(
                            SandboxErrorCode.EDIT_CONFLICT,
                            "Workspace 目标路径已存在其他内容",
                        )
                except SandboxServiceError as exc:
                    if exc.code is not SandboxErrorCode.INVALID_PATH:
                        raise
                    previous_size = 0

                with self._quota_guard:
                    current_usage = self.usage_ledger.snapshot(
                        workspace_id
                    ).workspace_bytes
                    usage_after = current_usage - previous_size + size_bytes
                    growth_bytes = max(0, usage_after - current_usage)
                    self.disk_guard.ensure_available(
                        additional_bytes=growth_bytes
                    )
                    if usage_after > effective_quota:
                        raise SandboxServiceError(
                            SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
                            "工作区空间配额已用完",
                        )
                    projected_total = (
                        self.usage_ledger.total_workspace_bytes()
                        + sum(
                            reservation.growth_bytes
                            for reservation in self._run_growth_reservations.values()
                        )
                        + growth_bytes
                    )
                    if projected_total > self.config.total_quota_bytes:
                        raise SandboxServiceError(
                            SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
                            "Sandbox 总空间预算已用完",
                        )
                    written = target_filesystem.copy_from_fd(
                        normalized_path,
                        source_fd,
                        size_bytes=size_bytes,
                        max_bytes=self.config.asset_max_bytes,
                        overwrite=bool(overwrite),
                    )
                    observed_usage = self.usage_ledger.adjust_workspace(
                        workspace_id,
                        written.size_bytes - written.previous_size_bytes,
                    )
                with target_filesystem.open_regular_file(normalized_path) as (
                    target_fd,
                    current_size,
                ):
                    observed_digest = self._fd_sha256(target_fd, current_size)
                if current_size != size_bytes or not secrets.compare_digest(
                    observed_digest,
                    digest,
                ):
                    self.usage_ledger.mark_dirty(workspace_id)
                    raise SandboxServiceError(
                        SandboxErrorCode.RUNTIME_UNAVAILABLE,
                        "Workspace 资产写后校验失败",
                    )
            return {
                "path": written.path,
                "sha256": digest,
                "size_bytes": written.size_bytes,
                "previous_size_bytes": written.previous_size_bytes,
                "used_bytes": observed_usage,
                "usage_delta_bytes": (
                    written.size_bytes - written.previous_size_bytes
                ),
                "materialized": True,
                "idempotent": False,
            }
        finally:
            workspace_lock.release()

    def list_files(
        self,
        workspace_id: str,
        *,
        path: str,
        cwd: str = "",
        cursor: str,
        limit: int,
    ) -> dict[str, Any]:
        resolved_path = _resolve_workspace_path(
            cwd=cwd,
            path=path,
            allow_empty=True,
        )
        entries = self.filesystem(workspace_id).list_entries(resolved_path)
        try:
            start = int(cursor or "0")
        except ValueError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.INVALID_PATH,
                "目录分页游标无效",
            ) from exc
        if start < 0:
            raise SandboxServiceError(
                SandboxErrorCode.INVALID_PATH,
                "目录分页游标无效",
            )
        page = entries[start:start + limit]
        next_offset = start + len(page)
        return {
            "protocol_version": WORKSPACE_PROTOCOL_VERSION,
            "entries": [asdict(entry) for entry in page],
            "next_cursor": str(next_offset) if next_offset < len(entries) else "",
            "total_visible": len(entries),
            "cwd": "/".join(validate_relative_path(cwd, allow_empty=True)),
        }

    def read_file(
        self,
        workspace_id: str,
        *,
        path: str,
        cwd: str = "",
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        line_offset = int(offset)
        line_limit = min(max(1, int(limit)), 2_000)
        if line_offset < 0:
            raise SandboxServiceError(
                SandboxErrorCode.INVALID_PATH,
                "文件行偏移不能为负数",
            )
        normalized_path = _resolve_workspace_path(
            cwd=cwd,
            path=path,
        )
        filesystem = self.filesystem(workspace_id)
        size_bytes = filesystem.regular_file_size(normalized_path)
        scan_limit = min(size_bytes, MAX_READ_SCAN_BYTES)
        content = (
            filesystem.read_bytes(
                normalized_path,
                offset=0,
                limit=max(1, scan_limit),
            )
            if size_bytes
            else b""
        )
        if _looks_binary(content):
            return {
                "protocol_version": WORKSPACE_PROTOCOL_VERSION,
                "path": normalized_path,
                "start_offset": line_offset,
                "returned_lines": 0,
                "next_offset": line_offset,
                "total_lines": None,
                "size_bytes": size_bytes,
                "eof": True,
                "binary": True,
                "line_truncated": False,
                "output_truncated": False,
                "content": "",
            }
        scan_complete = scan_limit >= size_bytes
        decode_content = content
        partial_long_line = False
        if not scan_complete and content:
            last_newline = content.rfind(b"\n")
            if last_newline >= 0:
                decode_content = content[:last_newline + 1]
            else:
                partial_long_line = True
        try:
            text = _decode_utf8_prefix(
                decode_content,
                final=scan_complete,
            )
        except SandboxServiceError as exc:
            if exc.code is not SandboxErrorCode.UNSUPPORTED_FILE_TYPE:
                raise
            return {
                "protocol_version": WORKSPACE_PROTOCOL_VERSION,
                "path": normalized_path,
                "start_offset": line_offset,
                "returned_lines": 0,
                "next_offset": line_offset,
                "total_lines": None,
                "size_bytes": size_bytes,
                "eof": True,
                "binary": True,
                "line_truncated": False,
                "output_truncated": False,
                "content": "",
            }
        lines = text.splitlines()
        total_lines = len(lines) if scan_complete else None
        selected = lines[line_offset:line_offset + line_limit]
        rendered: list[str] = []
        truncated_line_numbers: list[int] = []
        rendered_bytes = 0
        consumed_lines = 0
        output_budget_hit = False
        for index, line in enumerate(selected, start=line_offset + 1):
            line_truncated = (
                len(line) > MAX_RENDERED_LINE_CHARS
                or partial_long_line
            )
            visible = (
                line[:MAX_RENDERED_LINE_CHARS] + "…"
                if line_truncated
                else line
            )
            rendered_line = f"{index:>6}\t{visible}"
            encoded_size = len(rendered_line.encode("utf-8")) + (
                1 if rendered else 0
            )
            if rendered_bytes + encoded_size > self.config.max_read_bytes:
                output_budget_hit = True
                break
            rendered.append(rendered_line)
            rendered_bytes += encoded_size
            consumed_lines += 1
            if line_truncated:
                truncated_line_numbers.append(index)
        next_offset = line_offset + consumed_lines
        known_eof = (
            total_lines is not None and next_offset >= total_lines
        )
        scan_truncated = not scan_complete
        return {
            "protocol_version": WORKSPACE_PROTOCOL_VERSION,
            "path": normalized_path,
            "start_offset": line_offset,
            "offset": line_offset,
            "returned_lines": consumed_lines,
            "next_offset": next_offset,
            "total_lines": total_lines,
            "returned_bytes": rendered_bytes,
            "size_bytes": size_bytes,
            "eof": known_eof,
            "binary": False,
            "line_truncated": bool(truncated_line_numbers),
            "truncated_line_numbers": truncated_line_numbers,
            "output_truncated": bool(
                output_budget_hit or scan_truncated
            ),
            "truncation_reason": (
                "output_budget"
                if output_budget_hit
                else "scan_budget"
                if scan_truncated
                else ""
            ),
            "content": "\n".join(rendered),
        }

    def read_text_file(
        self,
        workspace_id: str,
        *,
        path: str,
        cwd: str = "",
    ) -> dict[str, Any]:
        """为受控编辑器返回有大小上限的完整 UTF-8 文本。"""

        normalized_path = _resolve_workspace_path(cwd=cwd, path=path)
        filesystem = self.filesystem(workspace_id)
        size_bytes = filesystem.regular_file_size(normalized_path)
        if size_bytes > self.config.max_write_bytes:
            raise SandboxServiceError(
                SandboxErrorCode.OUTPUT_LIMIT_EXCEEDED,
                "文件超过在线编辑大小上限",
                hint="请在 Sandbox 中使用分块工具处理此文件",
            )
        raw = (
            filesystem.read_bytes(
                normalized_path,
                offset=0,
                limit=max(1, size_bytes),
            )
            if size_bytes
            else b""
        )
        if _looks_binary(raw):
            raise SandboxServiceError(
                SandboxErrorCode.UNSUPPORTED_FILE_TYPE,
                "在线编辑只支持 UTF-8 文本文件",
            )
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.UNSUPPORTED_FILE_TYPE,
                "在线编辑只支持 UTF-8 文本文件",
            ) from exc
        return {
            "protocol_version": WORKSPACE_PROTOCOL_VERSION,
            "path": normalized_path,
            "size_bytes": size_bytes,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "content": content,
        }

    def _load_gitignore(
        self,
        filesystem: SafeWorkspaceFilesystem,
    ) -> pathspec.GitIgnoreSpec | None:
        try:
            size_bytes = filesystem.regular_file_size(".gitignore")
        except SandboxServiceError as exc:
            if exc.code is SandboxErrorCode.INVALID_PATH:
                return None
            raise
        if size_bytes > 256 * 1024:
            raise SandboxServiceError(
                SandboxErrorCode.INVALID_PATTERN,
                "Workspace .gitignore 超过安全解析上限",
            )
        raw = filesystem.read_bytes(
            ".gitignore",
            offset=0,
            limit=max(1, size_bytes),
        )
        if _looks_binary(raw):
            raise SandboxServiceError(
                SandboxErrorCode.INVALID_PATTERN,
                "Workspace .gitignore 不是文本文件",
            )
        text = _decode_utf8_prefix(raw, final=True)
        return pathspec.GitIgnoreSpec.from_lines(text.splitlines())

    @staticmethod
    def _fixed_ignored(path: str, *, is_directory: bool) -> bool:
        components = tuple(part for part in path.split("/") if part)
        if any(part in _FIXED_IGNORED_DIRECTORIES for part in components):
            return True
        if is_directory and path.startswith(".nanobot-"):
            return True
        return False

    def _collect_search_entries(
        self,
        filesystem: SafeWorkspaceFilesystem,
        *,
        root: str,
        max_depth: int | None,
    ) -> tuple[list[Any], int, bool]:
        ignore_spec = self._load_gitignore(filesystem)
        pending: list[tuple[str, int]] = [(root, 0)]
        entries: list[Any] = []
        skipped_ignored = 0
        traversal_truncated = False
        while pending:
            current, depth = pending.pop(0)
            for entry in filesystem.list_entries(current):
                is_directory = entry.type == "directory"
                ignored = self._fixed_ignored(
                    entry.path,
                    is_directory=is_directory,
                )
                if (
                    not ignored
                    and ignore_spec is not None
                    and ignore_spec.match_file(
                        entry.path + ("/" if is_directory else "")
                    )
                ):
                    ignored = True
                if ignored:
                    skipped_ignored += 1
                    continue
                entries.append(entry)
                if len(entries) >= MAX_SEARCH_ENTRIES:
                    traversal_truncated = True
                    return entries, skipped_ignored, traversal_truncated
                if (
                    is_directory
                    and (max_depth is None or depth < max_depth)
                ):
                    pending.append((entry.path, depth + 1))
        return entries, skipped_ignored, traversal_truncated

    def search_files(
        self,
        workspace_id: str,
        *,
        mode: str = "content",
        pattern: str = "",
        path: str,
        glob: str,
        limit: int,
        ignore_case: bool = False,
        max_depth: int | None = None,
        cursor: str = "",
        cwd: str = "",
    ) -> dict[str, Any]:
        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode not in {"content", "files", "tree"}:
            raise SandboxServiceError(
                SandboxErrorCode.INVALID_PATTERN,
                "workspace_search.mode 只允许 content/files/tree",
            )
        normalized_pattern = str(pattern or "")
        if (
            normalized_mode == "content"
            and (
                not normalized_pattern
                or len(normalized_pattern.encode("utf-8")) > 1024
            )
        ):
            raise SandboxServiceError(
                SandboxErrorCode.INVALID_PATTERN,
                "内容搜索正则不能为空且不能超过 1024 字节",
            )
        if len(normalized_pattern.encode("utf-8")) > 1024:
            raise SandboxServiceError(
                SandboxErrorCode.INVALID_PATTERN,
                "文件搜索 pattern 不能超过 1024 字节",
            )
        if len(str(glob or "").encode("utf-8")) > 512:
            raise SandboxServiceError(
                SandboxErrorCode.INVALID_PATTERN,
                "文件 glob 不能超过 512 字节",
            )
        bounded_limit = min(max(1, int(limit)), 200)
        if max_depth is not None and not 0 <= int(max_depth) <= 100:
            raise SandboxServiceError(
                SandboxErrorCode.INVALID_PATTERN,
                "max_depth 必须在 0 到 100 之间",
            )
        resolved_root = _resolve_workspace_path(
            cwd=cwd,
            path=path,
            allow_empty=True,
        )
        query_contract = {
            "mode": normalized_mode,
            "pattern": normalized_pattern,
            "path": resolved_root,
            "glob": str(glob or ""),
            "ignore_case": bool(ignore_case),
            "max_depth": max_depth,
        }
        fingerprint = _cursor_fingerprint(query_contract)
        cursor_state = _decode_search_cursor(
            cursor,
            expected_fingerprint=fingerprint,
            mode=normalized_mode,
        )
        filesystem = self.filesystem(workspace_id)
        entries, skipped_ignored, traversal_truncated = (
            self._collect_search_entries(
                filesystem,
                root=resolved_root,
                max_depth=max_depth,
            )
        )
        items: list[dict[str, Any]] = []
        scanned_files = 0
        scanned_bytes = 0
        skipped_binary_files = 0
        truncation_reason = ""
        next_cursor = ""
        entry_index = cursor_state["index"]

        if normalized_mode in {"files", "tree"}:
            file_pattern = normalized_pattern or str(glob or "") or "*"
            while entry_index < len(entries) and len(items) < bounded_limit:
                entry = entries[entry_index]
                entry_index += 1
                if normalized_mode == "files" and entry.type != "file":
                    continue
                if (
                    file_pattern
                    and file_pattern != "*"
                    and not fnmatch.fnmatch(entry.path, file_pattern)
                    and not fnmatch.fnmatch(
                        entry.path.rsplit("/", 1)[-1],
                        file_pattern,
                    )
                ):
                    continue
                item = {
                    "path": entry.path,
                    "type": entry.type,
                    "truncated": False,
                }
                if entry.type == "file":
                    item["size_bytes"] = entry.size_bytes
                items.append(item)
            if entry_index < len(entries):
                truncation_reason = "result_limit"
                next_cursor = _next_search_cursor(
                    mode=normalized_mode,
                    fingerprint=fingerprint,
                    index=entry_index,
                )
            elif traversal_truncated:
                truncation_reason = "traversal_budget"
            return {
                "protocol_version": WORKSPACE_PROTOCOL_VERSION,
                "items": items,
                "matches": items,
                "scanned_files": 0,
                "scanned_bytes": 0,
                "skipped_binary_files": 0,
                "skipped_ignored_files": skipped_ignored,
                "truncated": bool(
                    truncation_reason or traversal_truncated
                ),
                "truncation_reason": (
                    truncation_reason
                    or ("traversal_budget" if traversal_truncated else "")
                ),
                "next_cursor": next_cursor,
            }

        try:
            flags = regex.IGNORECASE if ignore_case else 0
            expression = regex.compile(normalized_pattern, flags)
        except (regex.error, ValueError) as exc:
            raise SandboxServiceError(
                SandboxErrorCode.INVALID_PATTERN,
                "内容搜索正则无法编译",
            ) from exc

        started_at = time.monotonic()
        byte_offset = cursor_state["byte_offset"]
        line_number = cursor_state["line"]
        while entry_index < len(entries):
            if len(items) >= bounded_limit:
                truncation_reason = "result_limit"
                break
            if scanned_files >= MAX_SEARCH_FILES:
                truncation_reason = "file_budget"
                break
            if scanned_bytes >= MAX_SEARCH_SCAN_BYTES:
                truncation_reason = "byte_budget"
                break
            if time.monotonic() - started_at >= MAX_SEARCH_SECONDS:
                truncation_reason = "time_budget"
                break

            entry = entries[entry_index]
            if entry.type != "file":
                entry_index += 1
                byte_offset = 0
                line_number = 1
                continue
            if (
                glob
                and not fnmatch.fnmatch(entry.path, str(glob))
                and not fnmatch.fnmatch(
                    entry.path.rsplit("/", 1)[-1],
                    str(glob),
                )
            ):
                entry_index += 1
                byte_offset = 0
                line_number = 1
                continue
            scanned_files += 1
            sample = filesystem.read_bytes(
                entry.path,
                offset=0,
                limit=min(max(1, entry.size_bytes), 8192),
            )
            if _looks_binary(sample):
                skipped_binary_files += 1
                entry_index += 1
                byte_offset = 0
                line_number = 1
                continue
            remaining_budget = MAX_SEARCH_SCAN_BYTES - scanned_bytes
            requested = min(
                max(1, remaining_budget),
                max(1, entry.size_bytes - byte_offset),
            )
            raw = filesystem.read_bytes(
                entry.path,
                offset=byte_offset,
                limit=requested,
            )
            eof = byte_offset + len(raw) >= entry.size_bytes
            complete_raw = raw
            long_line_segment = False
            if not eof and raw:
                newline_at = raw.rfind(b"\n")
                if newline_at >= 0:
                    complete_raw = raw[:newline_at + 1]
                else:
                    long_line_segment = True
            try:
                text = _decode_utf8_prefix(
                    complete_raw,
                    final=eof,
                )
            except SandboxServiceError as exc:
                if exc.code is not SandboxErrorCode.UNSUPPORTED_FILE_TYPE:
                    raise
                skipped_binary_files += 1
                entry_index += 1
                byte_offset = 0
                line_number = 1
                continue
            consumed_bytes = len(text.encode("utf-8"))
            scanned_bytes += consumed_bytes
            if not text and not eof:
                truncation_reason = "byte_budget"
                break
            line_chunks = text.splitlines(keepends=True)
            chunk_consumed = 0
            for raw_line in line_chunks:
                visible_line = raw_line.rstrip("\r\n")
                line_bytes = len(raw_line.encode("utf-8"))
                chunk_consumed += line_bytes
                try:
                    matched = expression.search(
                        visible_line,
                        timeout=MAX_REGEX_LINE_SECONDS,
                    ) is not None
                except TimeoutError as exc:
                    raise SandboxServiceError(
                        SandboxErrorCode.INVALID_PATTERN,
                        "内容搜索正则匹配超时",
                    ) from exc
                if matched:
                    text_truncated = (
                        len(visible_line) > MAX_RENDERED_LINE_CHARS
                        or long_line_segment
                    )
                    items.append({
                        "path": entry.path,
                        "line": line_number,
                        "text": (
                            visible_line[:MAX_RENDERED_LINE_CHARS]
                            + ("…" if text_truncated else "")
                        ),
                        "type": "file",
                        "truncated": text_truncated,
                    })
                ends_line = raw_line.endswith(("\n", "\r"))
                next_line_number = (
                    line_number + 1 if ends_line else line_number
                )
                if len(items) >= bounded_limit:
                    byte_offset += chunk_consumed
                    line_number = next_line_number
                    truncation_reason = "result_limit"
                    break
                line_number = next_line_number
            if truncation_reason == "result_limit":
                break
            byte_offset += consumed_bytes
            if eof:
                entry_index += 1
                byte_offset = 0
                line_number = 1
            else:
                truncation_reason = "byte_budget"
                break

        if truncation_reason:
            next_cursor = _next_search_cursor(
                mode=normalized_mode,
                fingerprint=fingerprint,
                index=entry_index,
                byte_offset=byte_offset,
                line=line_number,
            )
        elif traversal_truncated:
            truncation_reason = "traversal_budget"
        return {
            "protocol_version": WORKSPACE_PROTOCOL_VERSION,
            "items": items,
            "matches": items,
            "scanned_files": scanned_files,
            "scanned_bytes": scanned_bytes,
            "skipped_binary_files": skipped_binary_files,
            "skipped_ignored_files": skipped_ignored,
            "truncated": bool(truncation_reason),
            "truncation_reason": truncation_reason,
            "next_cursor": next_cursor,
        }

    def write_file(
        self,
        workspace_id: str,
        *,
        path: str,
        cwd: str = "",
        content: str,
        overwrite: bool,
        expected_sha256: str | None = None,
        quota_bytes: int,
    ) -> dict[str, Any]:
        workspace_id = validate_workspace_id(workspace_id)
        normalized_path = _resolve_workspace_path(
            cwd=cwd,
            path=path,
        )
        workspace_lock = self.acquire_workspace_write(workspace_id)
        try:
            encoded = str(content).encode("utf-8")
            if len(encoded) > self.config.max_write_bytes:
                raise SandboxServiceError(
                    SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
                    "写入内容超过单次允许上限",
                    hint="大文件请使用资产上传接口",
                )
            return self._write_bytes_locked(
                workspace_id,
                path=normalized_path,
                encoded=encoded,
                overwrite=overwrite,
                expected_sha256=expected_sha256,
                quota_bytes=quota_bytes,
            )
        finally:
            workspace_lock.release()

    @staticmethod
    def _diff_header_path(value: str, *, prefix: str) -> str:
        raw = value.rstrip("\r\n")
        if "\t" in raw:
            raw = raw.split("\t", 1)[0]
        raw = raw.strip()
        if raw.startswith('"') and raw.endswith('"'):
            try:
                parts = shlex.split(raw)
            except ValueError as exc:
                raise SandboxServiceError(
                    SandboxErrorCode.EDIT_CONFLICT,
                    "unified diff 文件路径无效",
                ) from exc
            if len(parts) != 1:
                raise SandboxServiceError(
                    SandboxErrorCode.EDIT_CONFLICT,
                    "unified diff 文件路径无效",
                )
            raw = parts[0]
        expected = f"{prefix}/"
        if raw.startswith(expected):
            raw = raw[len(expected):]
        return "/".join(validate_relative_path(raw))

    def _split_unified_diff(
        self,
        patch: str,
    ) -> list[tuple[str, str]]:
        if (
            not isinstance(patch, str)
            or not patch
            or "\x00" in patch
            or len(patch.encode("utf-8")) > self.config.max_write_bytes
        ):
            raise SandboxServiceError(
                SandboxErrorCode.EDIT_CONFLICT,
                "unified diff 为空或超过单次编辑上限",
            )
        lines = patch.splitlines(keepends=True)
        starts = [
            index
            for index, line in enumerate(lines)
            if line.startswith("diff --git ")
        ]
        blocks: list[list[str]]
        if starts:
            if starts[0] != 0:
                raise SandboxServiceError(
                    SandboxErrorCode.EDIT_CONFLICT,
                    "多文件 unified diff 必须使用完整 diff --git 文件头",
                )
            starts.append(len(lines))
            blocks = [
                lines[starts[index]:starts[index + 1]]
                for index in range(len(starts) - 1)
            ]
        else:
            blocks = [lines]

        result: list[tuple[str, str]] = []
        for block in blocks:
            old_path = ""
            new_path = ""
            first = block[0].rstrip("\r\n") if block else ""
            if first.startswith("diff --git "):
                try:
                    parts = shlex.split(first)
                except ValueError as exc:
                    raise SandboxServiceError(
                        SandboxErrorCode.EDIT_CONFLICT,
                        "unified diff 文件头无效",
                    ) from exc
                if len(parts) != 4:
                    raise SandboxServiceError(
                        SandboxErrorCode.EDIT_CONFLICT,
                        "unified diff 文件头无效",
                    )
                old_path = self._diff_header_path(
                    parts[2],
                    prefix="a",
                )
                new_path = self._diff_header_path(
                    parts[3],
                    prefix="b",
                )
            else:
                for line in block:
                    if line.startswith("--- ") and not old_path:
                        old_path = self._diff_header_path(
                            line[4:],
                            prefix="a",
                        )
                    elif line.startswith("+++ ") and not new_path:
                        new_path = self._diff_header_path(
                            line[4:],
                            prefix="b",
                        )
            if not old_path or old_path != new_path:
                raise SandboxServiceError(
                    SandboxErrorCode.EDIT_CONFLICT,
                    "workspace_edit 暂不支持创建、删除或重命名文件",
                )
            result.append((old_path, "".join(block)))
        return result

    def _runtime_filesystem(
        self,
        workspace_id: str,
    ) -> SafeWorkspaceFilesystem:
        return SafeWorkspaceFilesystem(
            self.layout.runtime_root / workspace_id,
            write_uid=self.config.workspace_uid,
            write_gid=self.config.workspace_gid,
        )

    def _read_edit_bytes(
        self,
        filesystem: SafeWorkspaceFilesystem,
        path: str,
    ) -> bytes:
        size_bytes = filesystem.regular_file_size(path)
        if size_bytes > self.config.max_write_bytes:
            raise SandboxServiceError(
                SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
                "目标文件超过单次编辑允许上限",
            )
        return filesystem.read_bytes(
            path,
            offset=0,
            limit=max(1, size_bytes),
        )

    @staticmethod
    def _decode_edit_source(raw: bytes) -> str:
        if _looks_binary(raw):
            raise SandboxServiceError(
                SandboxErrorCode.UNSUPPORTED_FILE_TYPE,
                "workspace_edit 只支持 UTF-8 文本文件",
            )
        return _decode_utf8_prefix(raw, final=True)

    def _write_edit_journal(
        self,
        workspace_id: str,
        *,
        originals: dict[str, bytes],
        finals: dict[str, bytes],
    ) -> str:
        journal_name = f"{_EDIT_JOURNAL_PREFIX}{uuid4().hex}.json"
        payload = {
            "version": 1,
            "files": [
                {
                    "path": path,
                    "original_base64": base64.b64encode(
                        originals[path]
                    ).decode("ascii"),
                    "original_sha256": hashlib.sha256(
                        originals[path]
                    ).hexdigest(),
                    "new_sha256": hashlib.sha256(
                        finals[path]
                    ).hexdigest(),
                }
                for path in sorted(finals)
            ],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self._runtime_filesystem(workspace_id).write_bytes(
            journal_name,
            encoded,
            overwrite=False,
            max_bytes=self.config.max_write_bytes * 4,
        )
        return journal_name

    def _restore_edit_originals(
        self,
        workspace_id: str,
        originals: dict[str, bytes],
    ) -> None:
        filesystem = self.filesystem(workspace_id)
        for path in sorted(originals):
            try:
                previous_size = filesystem.regular_file_size(path)
            except SandboxServiceError:
                previous_size = 0
            written = filesystem.write_bytes(
                path,
                originals[path],
                overwrite=True,
                max_bytes=self.config.max_write_bytes,
            )
            with self._quota_guard:
                self.usage_ledger.adjust_workspace(
                    workspace_id,
                    written.size_bytes - previous_size,
                )

    def _recover_edit_journals_locked(
        self,
        workspace_id: str,
    ) -> str:
        runtime_filesystem = self._runtime_filesystem(workspace_id)
        journals = [
            entry
            for entry in runtime_filesystem.list_entries("")
            if (
                entry.type == "file"
                and entry.path.startswith(_EDIT_JOURNAL_PREFIX)
                and entry.path.endswith(".json")
            )
        ]
        recovery_status = "not_needed"
        for entry in journals:
            if entry.size_bytes > self.config.max_write_bytes * 4:
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Workspace 编辑恢复日志超过安全上限",
                    retryable=False,
                )
            raw = runtime_filesystem.read_bytes(
                entry.path,
                offset=0,
                limit=max(1, entry.size_bytes),
            )
            try:
                payload = json.loads(raw.decode("utf-8"))
                file_rows = payload["files"]
                if payload.get("version") != 1 or not isinstance(
                    file_rows,
                    list,
                ):
                    raise ValueError
                originals: dict[str, bytes] = {}
                old_hashes: dict[str, str] = {}
                new_hashes: dict[str, str] = {}
                for row in file_rows:
                    path = "/".join(
                        validate_relative_path(str(row["path"]))
                    )
                    original = base64.b64decode(
                        str(row["original_base64"]),
                        validate=True,
                    )
                    if (
                        hashlib.sha256(original).hexdigest()
                        != str(row["original_sha256"])
                        or len(original) > self.config.max_write_bytes
                    ):
                        raise ValueError
                    originals[path] = original
                    old_hashes[path] = str(row["original_sha256"])
                    new_hashes[path] = str(row["new_sha256"])
            except (
                KeyError,
                TypeError,
                ValueError,
                UnicodeError,
                json.JSONDecodeError,
            ) as exc:
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Workspace 编辑恢复日志损坏，已停止后续写入",
                ) from exc

            current_hashes: dict[str, str] = {}
            for path in originals:
                current = self._read_edit_bytes(
                    self.filesystem(workspace_id),
                    path,
                )
                current_hashes[path] = hashlib.sha256(
                    current
                ).hexdigest()
            if current_hashes == new_hashes:
                recovery_status = "committed"
            elif any(
                current_hashes[path]
                not in {old_hashes[path], new_hashes[path]}
                for path in current_hashes
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Workspace 编辑恢复发现外部并发修改，已停止自动覆盖",
                    retryable=False,
                )
            else:
                self._restore_edit_originals(
                    workspace_id,
                    originals,
                )
                recovery_status = "rolled_back"
            runtime_filesystem.unlink_regular_file(entry.path)
        return recovery_status

    def _check_edit_quota(
        self,
        workspace_id: str,
        *,
        originals: dict[str, bytes],
        finals: dict[str, bytes],
        quota_bytes: int,
    ) -> None:
        effective_quota = min(
            max(1, int(quota_bytes)),
            self.config.workspace_quota_bytes,
        )
        delta = sum(
            len(finals[path]) - len(originals[path])
            for path in finals
        )
        with self._quota_guard:
            current_usage = self.usage_ledger.snapshot(
                workspace_id
            ).workspace_bytes
            usage_after = current_usage + delta
            growth_bytes = max(0, delta)
            self.disk_guard.ensure_available(
                additional_bytes=growth_bytes
            )
            if usage_after > effective_quota:
                raise SandboxServiceError(
                    SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
                    "工作区空间配额已用完",
                )
            projected_total = (
                self.usage_ledger.total_workspace_bytes()
                + sum(
                    reservation.growth_bytes
                    for reservation
                    in self._run_growth_reservations.values()
                )
                + growth_bytes
            )
            if projected_total > self.config.total_quota_bytes:
                raise SandboxServiceError(
                    SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
                    "Sandbox 总空间预算已用完",
                )

    def edit_files(
        self,
        workspace_id: str,
        *,
        operations: list[dict[str, Any]],
        cwd: str = "",
        quota_bytes: int,
    ) -> dict[str, Any]:
        """原子准备批量精确替换或多文件 unified diff。"""

        workspace_id = validate_workspace_id(workspace_id)
        if (
            not isinstance(operations, list)
            or not 1 <= len(operations) <= MAX_EDIT_OPERATIONS
        ):
            raise SandboxServiceError(
                SandboxErrorCode.EDIT_CONFLICT,
                "workspace_edit.operations 必须包含 1 到 50 项",
            )
        workspace_lock = self.acquire_workspace_write(workspace_id)
        journal_name = ""
        originals: dict[str, bytes] = {}
        finals: dict[str, bytes] = {}
        file_stats: dict[str, dict[str, int]] = {}
        try:
            recovery_status = self._recover_edit_journals_locked(
                workspace_id
            )
            filesystem = self.filesystem(workspace_id)

            def source_for(path: str) -> str:
                if path not in originals:
                    raw = self._read_edit_bytes(filesystem, path)
                    originals[path] = raw
                    finals[path] = raw
                    file_stats[path] = {
                        "replacement_count": 0,
                        "hunks_applied": 0,
                        "added_lines": 0,
                        "removed_lines": 0,
                    }
                return self._decode_edit_source(finals[path])

            for operation in operations:
                if not isinstance(operation, dict):
                    raise SandboxServiceError(
                        SandboxErrorCode.EDIT_CONFLICT,
                        "workspace_edit operation 必须是对象",
                    )
                if "diff" in operation:
                    if set(operation) != {"diff"}:
                        raise SandboxServiceError(
                            SandboxErrorCode.EDIT_CONFLICT,
                            "diff operation 不能混入精确替换字段",
                        )
                    for relative_path, patch in self._split_unified_diff(
                        str(operation.get("diff") or "")
                    ):
                        normalized_path = _resolve_workspace_path(
                            cwd=cwd,
                            path=relative_path,
                        )
                        source = source_for(normalized_path)
                        try:
                            applied = apply_unified_patch(
                                source,
                                patch,
                                path=relative_path,
                            )
                        except SandboxServiceError as exc:
                            if (
                                exc.code
                                is not SandboxErrorCode.AUTHORIZATION_FAILED
                            ):
                                raise
                            raise SandboxServiceError(
                                SandboxErrorCode.EDIT_CONFLICT,
                                "unified diff 与目标文件当前内容不匹配",
                                hint=(
                                    "重新读取目标文件，并基于当前内容生成补丁"
                                ),
                            ) from exc
                        finals[normalized_path] = applied.content.encode(
                            "utf-8"
                        )
                        stats = file_stats[normalized_path]
                        stats["hunks_applied"] += applied.hunk_count
                        stats["added_lines"] += applied.added_lines
                        stats["removed_lines"] += applied.removed_lines
                    continue

                allowed = {"path", "old", "new", "replace_all"}
                if set(operation) - allowed:
                    raise SandboxServiceError(
                        SandboxErrorCode.EDIT_CONFLICT,
                        "精确替换 operation 包含未允许字段",
                    )
                normalized_path = _resolve_workspace_path(
                    cwd=cwd,
                    path=str(operation.get("path") or ""),
                )
                old = operation.get("old")
                new = operation.get("new")
                if not isinstance(old, str) or not old:
                    raise SandboxServiceError(
                        SandboxErrorCode.EDIT_CONFLICT,
                        "workspace_edit.old 必须是非空精确文本",
                    )
                if not isinstance(new, str):
                    raise SandboxServiceError(
                        SandboxErrorCode.EDIT_CONFLICT,
                        "workspace_edit.new 必须是文本",
                    )
                source = source_for(normalized_path)
                count = source.count(old)
                replace_all = operation.get("replace_all") is True
                if count == 0:
                    raise SandboxServiceError(
                        SandboxErrorCode.EDIT_CONFLICT,
                        "workspace_edit.old 在目标文件中没有精确命中",
                    )
                if count > 1 and not replace_all:
                    raise SandboxServiceError(
                        SandboxErrorCode.EDIT_CONFLICT,
                        "workspace_edit.old 命中多处，必须显式设置 replace_all",
                    )
                replacement_count = count if replace_all else 1
                finals[normalized_path] = source.replace(
                    old,
                    new,
                    -1 if replace_all else 1,
                ).encode("utf-8")
                file_stats[normalized_path][
                    "replacement_count"
                ] += replacement_count

            if not finals or all(
                finals[path] == originals[path] for path in finals
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.EDIT_CONFLICT,
                    "workspace_edit 没有产生任何变更",
                )
            if (
                sum(len(value) for value in originals.values())
                > self.config.max_write_bytes
                or sum(len(value) for value in finals.values())
                > self.config.max_write_bytes
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
                    "批量编辑总内容超过单次允许上限",
                )
            self._check_edit_quota(
                workspace_id,
                originals=originals,
                finals=finals,
                quota_bytes=quota_bytes,
            )
            journal_name = self._write_edit_journal(
                workspace_id,
                originals=originals,
                finals=finals,
            )
            results: list[dict[str, Any]] = []
            for path in sorted(finals):
                write_result = self._write_bytes_locked(
                    workspace_id,
                    path=path,
                    encoded=finals[path],
                    overwrite=True,
                    quota_bytes=quota_bytes,
                )
                results.append({
                    **write_result,
                    "old_sha256": hashlib.sha256(
                        originals[path]
                    ).hexdigest(),
                    "new_sha256": hashlib.sha256(
                        finals[path]
                    ).hexdigest(),
                    **file_stats[path],
                })
            for path in finals:
                observed = self._read_edit_bytes(
                    self.filesystem(workspace_id),
                    path,
                )
                if observed != finals[path]:
                    raise SandboxServiceError(
                        SandboxErrorCode.RUNTIME_UNAVAILABLE,
                        "Workspace 编辑写后校验失败",
                    )
            self._runtime_filesystem(
                workspace_id
            ).unlink_regular_file(journal_name)
            journal_name = ""
            return {
                "protocol_version": WORKSPACE_PROTOCOL_VERSION,
                "files": results,
                "file_count": len(results),
                "recovery_status": recovery_status,
            }
        except BaseException:
            if journal_name:
                try:
                    self._restore_edit_originals(
                        workspace_id,
                        originals,
                    )
                    self._runtime_filesystem(
                        workspace_id
                    ).unlink_regular_file(journal_name)
                    journal_name = ""
                except Exception:
                    logger.error(
                        "Workspace 编辑回滚失败 workspace_id=%s",
                        workspace_id,
                        exc_info=True,
                    )
            raise
        finally:
            workspace_lock.release()

    def _write_bytes_locked(
        self,
        workspace_id: str,
        *,
        path: str,
        encoded: bytes,
        overwrite: bool,
        expected_sha256: str | None = None,
        quota_bytes: int,
    ) -> dict[str, Any]:
        """调用方已持有 Workspace 文件写锁。"""

        if len(encoded) > self.config.max_write_bytes:
            raise SandboxServiceError(
                SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
                "写入内容超过单次允许上限",
                hint="大文件请使用资产上传接口",
            )
        effective_quota = min(
            max(1, int(quota_bytes)),
            self.config.workspace_quota_bytes,
        )
        filesystem = self.filesystem(workspace_id)
        if expected_sha256 is not None:
            if not overwrite:
                raise SandboxServiceError(
                    SandboxErrorCode.EDIT_CONFLICT,
                    "带版本保存必须使用覆盖模式",
                )
            try:
                current_size = filesystem.regular_file_size(path)
                if current_size > self.config.max_write_bytes:
                    raise SandboxServiceError(
                        SandboxErrorCode.OUTPUT_LIMIT_EXCEEDED,
                        "文件超过在线编辑大小上限",
                    )
                current = (
                    filesystem.read_bytes(
                        path,
                        offset=0,
                        limit=max(1, current_size),
                    )
                    if current_size
                    else b""
                )
            except SandboxServiceError as exc:
                if exc.code is SandboxErrorCode.INVALID_PATH:
                    raise SandboxServiceError(
                        SandboxErrorCode.EDIT_CONFLICT,
                        "文件已被删除，请刷新目录后重试",
                    ) from exc
                raise
            current_sha256 = hashlib.sha256(current).hexdigest()
            if not secrets.compare_digest(current_sha256, expected_sha256):
                raise SandboxServiceError(
                    SandboxErrorCode.EDIT_CONFLICT,
                    "文件已被其他操作修改，请重新加载后再保存",
                )
        with self._quota_guard:
            current_usage = self.usage_ledger.snapshot(
                workspace_id,
            ).workspace_bytes
            previous_size = 0
            if overwrite:
                try:
                    previous_size = filesystem.regular_file_size(path)
                except SandboxServiceError as exc:
                    if exc.code is not SandboxErrorCode.INVALID_PATH:
                        raise
            usage_after = current_usage - previous_size + len(encoded)
            growth_bytes = max(0, usage_after - current_usage)
            self.disk_guard.ensure_available(additional_bytes=growth_bytes)
            if usage_after > effective_quota:
                raise SandboxServiceError(
                    SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
                    "工作区空间配额已用完",
                )
            projected_total = (
                self.usage_ledger.total_workspace_bytes()
                + sum(
                    reservation.growth_bytes
                    for reservation in self._run_growth_reservations.values()
                )
                + growth_bytes
            )
            if projected_total > self.config.total_quota_bytes:
                raise SandboxServiceError(
                    SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
                    "Sandbox 总空间预算已用完",
                )
            written = filesystem.write_bytes(
                path,
                encoded,
                overwrite=overwrite,
                max_bytes=self.config.max_write_bytes,
            )
            observed_usage = self.usage_ledger.adjust_workspace(
                workspace_id,
                written.size_bytes - written.previous_size_bytes,
            )
        return {
            "path": written.path,
            "size_bytes": written.size_bytes,
            "previous_size_bytes": written.previous_size_bytes,
            "used_bytes": observed_usage,
            "usage_delta_bytes": (
                written.size_bytes - written.previous_size_bytes
            ),
        }

    def apply_patch(
        self,
        workspace_id: str,
        *,
        path: str,
        patch: str,
        quota_bytes: int,
    ) -> dict[str, Any]:
        workspace_id = validate_workspace_id(workspace_id)
        normalized_path = "/".join(validate_relative_path(path))
        if (
            not isinstance(patch, str)
            or len(patch.encode("utf-8")) > self.config.max_write_bytes
        ):
            raise SandboxServiceError(
                SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
                "补丁内容超过单次允许上限",
            )
        workspace_lock = self.acquire_workspace_write(workspace_id)
        try:
            effective_quota = min(
                max(1, int(quota_bytes)),
                self.config.workspace_quota_bytes,
            )
            filesystem = self.filesystem(workspace_id)
            size_bytes = filesystem.regular_file_size(normalized_path)
            if size_bytes > self.config.max_write_bytes:
                raise SandboxServiceError(
                    SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
                    "目标文件超过单次补丁允许上限",
                )
            raw = filesystem.read_bytes(
                normalized_path,
                offset=0,
                limit=max(1, size_bytes),
            )
            try:
                source = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SandboxServiceError(
                    SandboxErrorCode.UNSUPPORTED_FILE_TYPE,
                    "Workspace 补丁只支持 UTF-8 文本文件",
                ) from exc
            applied = apply_unified_patch(
                source,
                patch,
                path=normalized_path,
            )
            result = self._write_bytes_locked(
                workspace_id,
                path=normalized_path,
                encoded=applied.content.encode("utf-8"),
                overwrite=True,
                quota_bytes=effective_quota,
            )
            result.update({
                "hunks_applied": applied.hunk_count,
                "added_lines": applied.added_lines,
                "removed_lines": applied.removed_lines,
            })
            return result
        finally:
            workspace_lock.release()


class AssetFileService:
    def __init__(self, config: SandboxdConfig) -> None:
        self.config = config
        self.layout = SandboxStorageLayout(config.data_root)
        self.store = LocalAssetStore(
            self.layout,
            max_asset_bytes=config.asset_max_bytes,
        )
        self.disk_guard = DiskGuard(config)
        self.staging_root = config.data_root / "runtime" / ".inputs"
        self.manifest_root = config.data_root / "runtime" / ".input-manifests"
        self._stage_locks: dict[str, threading.Lock] = {}
        self._stage_locks_guard = threading.Lock()

    def publish(
        self,
        workspace_id: str,
        *,
        path: str,
        media_type: str,
    ) -> dict[str, Any]:
        self.disk_guard.ensure_available()
        return asdict(self.store.publish(
            workspace_id,
            path,
            media_type=media_type,
        ))

    def open_upload(self, *, media_type: str):
        self.disk_guard.ensure_available()
        return self.store.open_upload(media_type=media_type)

    @staticmethod
    def _validate_run_id(run_id: str) -> str:
        normalized = str(run_id or "")
        if (
            not normalized.startswith("sbxrun_")
            or len(normalized) > 64
            or not normalized.replace("_", "").replace("-", "").isalnum()
        ):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 运行标识无效",
            )
        return normalized

    @staticmethod
    def _asset_manifest(assets: list[dict[str, str]]) -> str:
        normalized = sorted(
            (
                validate_sha256(str(asset.get("sha256") or "")),
                "/".join(validate_relative_path(str(asset.get("logical_name") or ""))),
            )
            for asset in assets
        )
        logical_names = [logical_name for _sha256, logical_name in normalized]
        if len(logical_names) != len(set(logical_names)):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "资产 logical_name 不得重复",
            )
        return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))

    def _stage_lock(self, scope_id: str) -> threading.Lock:
        with self._stage_locks_guard:
            return self._stage_locks.setdefault(scope_id, threading.Lock())

    def stage(
        self,
        workspace_id: str,
        run_id: str,
        assets: list[dict[str, str]],
    ) -> dict[str, Any]:
        workspace_id = validate_workspace_id(workspace_id)
        run_id = self._validate_run_id(run_id)
        manifest = self._asset_manifest(assets)
        self.disk_guard.ensure_available()
        self.staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.manifest_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        workspace_stage_root = self.staging_root / workspace_id
        workspace_stage_root.mkdir(exist_ok=True, mode=0o700)
        final_path = workspace_stage_root / run_id
        manifest_path = self.manifest_root / f"{run_id}.json"
        if final_path.exists():
            try:
                if manifest_path.read_text(encoding="utf-8") != manifest:
                    raise SandboxServiceError(
                        SandboxErrorCode.AUTHORIZATION_FAILED,
                        "同一运行的资产 staging 请求不一致",
                    )
            except OSError as exc:
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "资产 staging 状态不可用",
                ) from exc
            return {"run_id": run_id, "asset_count": len(assets), "staged": True}

        temp_path = workspace_stage_root / f".{run_id}.{secrets.token_hex(8)}.tmp"
        temp_path.mkdir(mode=0o700)
        target_fs = SafeWorkspaceFilesystem(temp_path)
        source_fs = SafeWorkspaceFilesystem(self.layout.assets_root)
        try:
            for asset in assets:
                sha256 = validate_sha256(str(asset.get("sha256") or ""))
                logical_name = "/".join(validate_relative_path(
                    str(asset.get("logical_name") or ""),
                ))
                expected_key = self.layout.asset_storage_key(sha256)
                if str(asset.get("storage_key") or "") != expected_key:
                    raise SandboxServiceError(
                        SandboxErrorCode.ASSET_NOT_AUTHORIZED,
                        "资产不存在或当前 Workspace 无权访问",
                    )
                with source_fs.open_regular_file(expected_key) as (source_fd, size_bytes):
                    target_fs.copy_from_fd(
                        logical_name,
                        source_fd,
                        size_bytes=size_bytes,
                        max_bytes=self.config.asset_max_bytes,
                    )

            for current_root, directories, files in os.walk(temp_path, followlinks=False):
                for filename in files:
                    file_path = Path(current_root) / filename
                    metadata = file_path.lstat()
                    if not stat.S_ISREG(metadata.st_mode):
                        raise SandboxServiceError(
                            SandboxErrorCode.UNSUPPORTED_FILE_TYPE,
                            "资产 staging 只允许普通文件",
                        )
                    os.chown(
                        file_path,
                        self.config.workspace_uid,
                        self.config.workspace_gid,
                        follow_symlinks=False,
                    )
                    os.chmod(file_path, 0o400, follow_symlinks=False)
                for dirname in directories:
                    directory_path = Path(current_root) / dirname
                    os.chown(
                        directory_path,
                        self.config.workspace_uid,
                        self.config.workspace_gid,
                        follow_symlinks=False,
                    )
                    os.chmod(directory_path, 0o500, follow_symlinks=False)
            os.chown(
                temp_path,
                self.config.workspace_uid,
                self.config.workspace_gid,
                follow_symlinks=False,
            )
            os.chmod(temp_path, 0o500, follow_symlinks=False)
            os.replace(temp_path, final_path)
            manifest_path.write_text(manifest, encoding="utf-8")
            os.chmod(manifest_path, 0o600)
        except BaseException:
            shutil.rmtree(temp_path, ignore_errors=True)
            raise
        return {"run_id": run_id, "asset_count": len(assets), "staged": True}

    @staticmethod
    def _make_tree_mutable(root: Path) -> None:
        for current_root, directories, files in os.walk(
            root,
            topdown=True,
            followlinks=False,
        ):
            current = Path(current_root)
            metadata = current.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Lease 资产 staging 目录结构无效",
                )
            os.chmod(current, 0o700, follow_symlinks=False)
            for dirname in directories:
                child = current / dirname
                child_metadata = child.lstat()
                if (
                    not stat.S_ISDIR(child_metadata.st_mode)
                    or stat.S_ISLNK(child_metadata.st_mode)
                ):
                    raise SandboxServiceError(
                        SandboxErrorCode.RUNTIME_UNAVAILABLE,
                        "Lease 资产 staging 目录结构无效",
                    )
            for filename in files:
                child = current / filename
                child_metadata = child.lstat()
                if (
                    not stat.S_ISREG(child_metadata.st_mode)
                    or stat.S_ISLNK(child_metadata.st_mode)
                ):
                    raise SandboxServiceError(
                        SandboxErrorCode.RUNTIME_UNAVAILABLE,
                        "Lease 资产 staging 文件结构无效",
                    )

    def _lease_stage_matches(
        self,
        root: Path,
        assets: list[dict[str, str]],
    ) -> bool:
        expected_sizes: dict[str, int] = {}
        source_fs = SafeWorkspaceFilesystem(self.layout.assets_root)
        try:
            for asset in assets:
                sha256 = validate_sha256(str(asset.get("sha256") or ""))
                logical_name = "/".join(validate_relative_path(
                    str(asset.get("logical_name") or ""),
                ))
                expected_key = self.layout.asset_storage_key(sha256)
                if str(asset.get("storage_key") or "") != expected_key:
                    return False
                with source_fs.open_regular_file(expected_key) as (
                    _source_fd,
                    size_bytes,
                ):
                    expected_sizes[logical_name] = size_bytes

            actual_sizes: dict[str, int] = {}
            for current_root, directories, files in os.walk(
                root,
                topdown=True,
                followlinks=False,
            ):
                current = Path(current_root)
                metadata = current.lstat()
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or stat.S_ISLNK(metadata.st_mode)
                ):
                    return False
                for dirname in directories:
                    child_metadata = (current / dirname).lstat()
                    if (
                        not stat.S_ISDIR(child_metadata.st_mode)
                        or stat.S_ISLNK(child_metadata.st_mode)
                    ):
                        return False
                for filename in files:
                    child = current / filename
                    child_metadata = child.lstat()
                    if (
                        not stat.S_ISREG(child_metadata.st_mode)
                        or stat.S_ISLNK(child_metadata.st_mode)
                    ):
                        return False
                    logical_name = child.relative_to(root).as_posix()
                    actual_sizes[logical_name] = int(
                        child_metadata.st_size
                    )
        except (OSError, SandboxServiceError):
            return False
        return actual_sizes == expected_sizes

    def _seal_stage_tree(self, root: Path) -> None:
        for current_root, directories, files in os.walk(
            root,
            topdown=False,
            followlinks=False,
        ):
            current = Path(current_root)
            for filename in files:
                file_path = current / filename
                metadata = file_path.lstat()
                if not stat.S_ISREG(metadata.st_mode):
                    raise SandboxServiceError(
                        SandboxErrorCode.UNSUPPORTED_FILE_TYPE,
                        "Lease 资产 staging 只允许普通文件",
                    )
                os.chown(
                    file_path,
                    self.config.workspace_uid,
                    self.config.workspace_gid,
                    follow_symlinks=False,
                )
                os.chmod(file_path, 0o400, follow_symlinks=False)
            for dirname in directories:
                directory_path = current / dirname
                metadata = directory_path.lstat()
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or stat.S_ISLNK(metadata.st_mode)
                ):
                    raise SandboxServiceError(
                        SandboxErrorCode.RUNTIME_UNAVAILABLE,
                        "Lease 资产 staging 目录结构无效",
                    )
                os.chown(
                    directory_path,
                    self.config.workspace_uid,
                    self.config.workspace_gid,
                    follow_symlinks=False,
                )
                os.chmod(directory_path, 0o500, follow_symlinks=False)
        os.chown(
            root,
            self.config.workspace_uid,
            self.config.workspace_gid,
            follow_symlinks=False,
        )
        os.chmod(root, 0o500, follow_symlinks=False)

    @staticmethod
    def _clear_stage_tree(root: Path) -> None:
        for child in list(root.iterdir()):
            metadata = child.lstat()
            if stat.S_ISREG(metadata.st_mode):
                child.unlink()
            elif stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                shutil.rmtree(child)
            else:
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Lease 资产 staging 出现未授权文件类型",
                )

    def sync_lease(
        self,
        workspace_id: str,
        lease_id: str,
        assets: list[dict[str, str]],
    ) -> dict[str, Any]:
        """在已挂载目录 inode 内同步 Lease 的完整授权资产集合。"""

        workspace_id = validate_workspace_id(workspace_id)
        lease_id = validate_lease_id(lease_id)
        manifest = self._asset_manifest(assets)
        lock = self._stage_lock(lease_id)
        with lock:
            self.disk_guard.ensure_available()
            final_path = self.lease_stage_path(workspace_id, lease_id)
            self.manifest_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            manifest_path = self.manifest_root / f"{lease_id}.json"
            try:
                if (
                    manifest_path.read_text(encoding="utf-8") == manifest
                    and self._lease_stage_matches(final_path, assets)
                ):
                    return {
                        "lease_id": lease_id,
                        "asset_count": len(assets),
                        "staged": True,
                        "changed": False,
                    }
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Lease 资产 staging 状态不可用",
                ) from exc

            workspace_stage_root = self.staging_root / workspace_id
            temp_path = (
                workspace_stage_root
                / f".{lease_id}.{secrets.token_hex(8)}.tmp"
            )
            temp_path.mkdir(mode=0o700)
            target_fs = SafeWorkspaceFilesystem(temp_path)
            source_fs = SafeWorkspaceFilesystem(self.layout.assets_root)
            try:
                for asset in assets:
                    sha256 = validate_sha256(str(asset.get("sha256") or ""))
                    logical_name = "/".join(validate_relative_path(
                        str(asset.get("logical_name") or ""),
                    ))
                    expected_key = self.layout.asset_storage_key(sha256)
                    if str(asset.get("storage_key") or "") != expected_key:
                        raise SandboxServiceError(
                            SandboxErrorCode.ASSET_NOT_AUTHORIZED,
                            "资产不存在或当前 Workspace 无权访问",
                        )
                    with source_fs.open_regular_file(expected_key) as (
                        source_fd,
                        size_bytes,
                    ):
                        target_fs.copy_from_fd(
                            logical_name,
                            source_fd,
                            size_bytes=size_bytes,
                            max_bytes=self.config.asset_max_bytes,
                        )
                self._seal_stage_tree(temp_path)

                self._make_tree_mutable(final_path)
                self._clear_stage_tree(final_path)
                self._make_tree_mutable(temp_path)
                for child in list(temp_path.iterdir()):
                    os.replace(child, final_path / child.name)
                self._seal_stage_tree(final_path)
                manifest_path.write_text(manifest, encoding="utf-8")
                os.chmod(manifest_path, 0o600)
            except BaseException:
                try:
                    self._seal_stage_tree(final_path)
                except BaseException:
                    pass
                raise
            finally:
                shutil.rmtree(temp_path, ignore_errors=True)
        return {
            "lease_id": lease_id,
            "asset_count": len(assets),
            "staged": True,
            "changed": True,
        }

    def stage_path(self, workspace_id: str, run_id: str) -> Path:
        workspace_id = validate_workspace_id(workspace_id)
        run_id = self._validate_run_id(run_id)
        path = self.staging_root / workspace_id / run_id
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            path.mkdir(parents=True, mode=0o500)
            os.chown(path, self.config.workspace_uid, self.config.workspace_gid)
            metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "资产 staging 状态不可用",
            )
        return path

    def lease_stage_path(self, workspace_id: str, lease_id: str) -> Path:
        workspace_id = validate_workspace_id(workspace_id)
        lease_id = validate_lease_id(lease_id)
        path = self.staging_root / workspace_id / lease_id
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            path.mkdir(parents=True, mode=0o500)
            os.chown(path, self.config.workspace_uid, self.config.workspace_gid)
            metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Lease 资产 staging 状态不可用",
            )
        return path

    def cleanup_stage(self, workspace_id: str, run_id: str) -> None:
        path = self.staging_root / validate_workspace_id(workspace_id) / self._validate_run_id(run_id)
        if path.exists():
            try:
                self._make_tree_mutable(path)
            except SandboxServiceError:
                return
        shutil.rmtree(path, ignore_errors=True)
        try:
            (self.manifest_root / f"{run_id}.json").unlink(missing_ok=True)
        except OSError:
            pass

    def cleanup_lease_stage(self, workspace_id: str, lease_id: str) -> None:
        path = (
            self.staging_root
            / validate_workspace_id(workspace_id)
            / validate_lease_id(lease_id)
        )
        if path.exists():
            try:
                self._make_tree_mutable(path)
            except SandboxServiceError:
                return
        shutil.rmtree(path, ignore_errors=True)
        try:
            (self.manifest_root / f"{lease_id}.json").unlink(missing_ok=True)
        except OSError:
            pass

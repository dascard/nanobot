"""sandboxd 的安全文件、磁盘水位和资产 staging。"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import secrets
import shutil
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

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

    def list_files(
        self,
        workspace_id: str,
        *,
        path: str,
        cursor: str,
        limit: int,
    ) -> dict[str, Any]:
        entries = self.filesystem(workspace_id).list_entries(path)
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
            "entries": [asdict(entry) for entry in page],
            "next_cursor": str(next_offset) if next_offset < len(entries) else "",
            "total_visible": len(entries),
        }

    def read_file(
        self,
        workspace_id: str,
        *,
        path: str,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        bounded_limit = min(int(limit), self.config.max_read_bytes)
        filesystem = self.filesystem(workspace_id)
        content = filesystem.read_bytes(path, offset=offset, limit=bounded_limit)
        size_bytes = filesystem.regular_file_size(path)
        try:
            text = content.decode("utf-8")
            binary = "\x00" in text
        except UnicodeDecodeError:
            text = ""
            binary = True
        return {
            "path": "/".join(validate_relative_path(path)),
            "offset": int(offset),
            "returned_bytes": len(content),
            "size_bytes": size_bytes,
            "eof": int(offset) + len(content) >= size_bytes,
            "binary": binary,
            "content": "" if binary else text,
        }

    def search_files(
        self,
        workspace_id: str,
        *,
        query: str,
        path: str,
        glob: str,
        limit: int,
    ) -> dict[str, Any]:
        if not query or len(query.encode("utf-8")) > 1024:
            raise SandboxServiceError(
                SandboxErrorCode.INVALID_PATH,
                "搜索关键词无效",
            )
        filesystem = self.filesystem(workspace_id)
        root_components = validate_relative_path(path, allow_empty=True)
        pending = ["/".join(root_components)]
        matches: list[dict[str, Any]] = []
        scanned_files = 0
        while pending and len(matches) < limit and scanned_files < 2000:
            current = pending.pop(0)
            for entry in filesystem.list_entries(current):
                if entry.type == "directory":
                    pending.append(entry.path)
                    continue
                if entry.type != "file" or (glob and not fnmatch.fnmatch(entry.path, glob)):
                    continue
                scanned_files += 1
                content = filesystem.read_bytes(
                    entry.path,
                    offset=0,
                    limit=min(entry.size_bytes, 1024 * 1024),
                )
                try:
                    text = content.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                if "\x00" in text:
                    continue
                for line_number, line in enumerate(text.splitlines(), start=1):
                    if query in line:
                        matches.append({
                            "path": entry.path,
                            "line": line_number,
                            "text": line[:300],
                        })
                        if len(matches) >= limit:
                            break
        return {
            "matches": matches,
            "scanned_files": scanned_files,
            "truncated": bool(pending) or scanned_files >= 2000,
        }

    def write_file(
        self,
        workspace_id: str,
        *,
        path: str,
        content: str,
        overwrite: bool,
        quota_bytes: int,
    ) -> dict[str, Any]:
        workspace_id = validate_workspace_id(workspace_id)
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
                path=path,
                encoded=encoded,
                overwrite=overwrite,
                quota_bytes=quota_bytes,
            )
        finally:
            workspace_lock.release()

    def _write_bytes_locked(
        self,
        workspace_id: str,
        *,
        path: str,
        encoded: bytes,
        overwrite: bool,
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

"""sandboxd 的安全文件、磁盘水位和资产 staging。"""

from __future__ import annotations

import fnmatch
import json
import os
import secrets
import shutil
import stat
import threading
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


class WorkspaceFileService:
    def __init__(self, config: SandboxdConfig) -> None:
        self.config = config
        self.layout = SandboxStorageLayout(config.data_root)
        self.disk_guard = DiskGuard(config)
        self._workspace_locks: dict[str, threading.Lock] = {}
        self._workspace_locks_guard = threading.Lock()
        self._quota_guard = threading.Lock()
        self._run_growth_reservations: dict[str, int] = {}

    def acquire_workspace_mutation(self, workspace_id: str) -> threading.Lock:
        """让文件写入和容器执行共享同一个 Workspace 互斥边界。"""

        normalized = validate_workspace_id(workspace_id)
        with self._workspace_locks_guard:
            lock = self._workspace_locks.setdefault(normalized, threading.Lock())
        if not lock.acquire(blocking=False):
            raise SandboxServiceError(
                SandboxErrorCode.SANDBOX_BUSY,
                "当前 Workspace 正在执行其他写操作",
                retryable=True,
                stop=False,
            )
        return lock

    def total_workspace_usage(self) -> int:
        """核算 sandboxd 管理根目录下全部 Workspace 普通文件大小。"""

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
        """为一次容器运行预留最大增长量，返回运行前占用和本次上限。"""

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
            workspace_usage = directory_usage(self.filesystem(workspace_id))
            if workspace_usage > effective_workspace_quota:
                raise SandboxServiceError(
                    SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
                    "工作区空间配额已用完",
                )
            total_usage = self.total_workspace_usage()
            reserved_growth = sum(self._run_growth_reservations.values())
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
            self._run_growth_reservations[run_id] = granted_growth
            return workspace_usage, workspace_usage + granted_growth

    def release_run_capacity(self, run_id: str) -> None:
        with self._quota_guard:
            self._run_growth_reservations.pop(run_id, None)

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
        return {"workspace_id": workspace_id, "ensured": True}

    def filesystem(self, workspace_id: str) -> SafeWorkspaceFilesystem:
        workspace_id = validate_workspace_id(workspace_id)
        return SafeWorkspaceFilesystem(self.layout.workspace_data_dir(workspace_id))

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
        workspace_lock = self.acquire_workspace_mutation(workspace_id)
        try:
            encoded = str(content).encode("utf-8")
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
                current_usage = directory_usage(filesystem)
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
                    self.total_workspace_usage()
                    + sum(self._run_growth_reservations.values())
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
                observed_usage = directory_usage(filesystem)
            return {
                "path": written.path,
                "size_bytes": written.size_bytes,
                "previous_size_bytes": written.previous_size_bytes,
                "used_bytes": observed_usage,
                "usage_delta_bytes": observed_usage - current_usage,
            }
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
        return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))

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

    def cleanup_stage(self, workspace_id: str, run_id: str) -> None:
        path = self.staging_root / validate_workspace_id(workspace_id) / self._validate_run_id(run_id)
        shutil.rmtree(path, ignore_errors=True)
        try:
            (self.manifest_root / f"{run_id}.json").unlink(missing_ok=True)
        except OSError:
            pass

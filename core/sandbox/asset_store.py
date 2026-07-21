"""不依赖 Nanobot 数据库的宿主不可变 Asset Store。"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
from uuid import uuid4

from core.sandbox.contracts import (
    PublishedAsset,
    SandboxErrorCode,
    SandboxServiceError,
)
from core.sandbox.paths import SafeWorkspaceFilesystem, SandboxStorageLayout


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_MEDIA_TYPE_RE = re.compile(
    r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+"
)


def asset_runtime_error() -> SandboxServiceError:
    return SandboxServiceError(
        SandboxErrorCode.RUNTIME_UNAVAILABLE,
        "资产存储暂时不可用",
        retryable=True,
        hint="稍后重试；持续失败时联系运维检查 sandboxd",
        stop=False,
    )


def safe_media_type(value: str) -> str:
    normalized = str(value or "application/octet-stream").strip().lower()
    if len(normalized) > 255 or not _MEDIA_TYPE_RE.fullmatch(normalized):
        return "application/octet-stream"
    return normalized


def _hash_open_file(file_fd: int, *, max_bytes: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    position = 0
    while True:
        chunk = os.pread(file_fd, 1024 * 1024, position)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise SandboxServiceError(
                SandboxErrorCode.ASSET_TOO_LARGE,
                "资产超过允许的单文件大小上限",
                hint="请拆分文件或联系运维调整配额",
            )
        digest.update(chunk)
        position += len(chunk)
    return digest.hexdigest(), total


def _asset_os_error(exc: OSError) -> SandboxServiceError:
    if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)}:
        return SandboxServiceError(
            SandboxErrorCode.DISK_PRESSURE,
            "磁盘水位已达到保护阈值，暂时拒绝新资产",
            retryable=True,
            stop=False,
        )
    return asset_runtime_error()


class AssetUploadWriter:
    """sandboxd 流式上传写入器；临时文件只在完成后按内容摘要原子链接。"""

    def __init__(self, store: "LocalAssetStore", media_type: str) -> None:
        self.store = store
        self.media_type = safe_media_type(media_type)
        self.digest = hashlib.sha256()
        self.total = 0
        self.temp_name = f"upload-{uuid4().hex}.tmp"
        self.temp_dir_fd = -1
        self.temp_fd = -1
        self.temp_exists = False
        self.finished = False
        try:
            temp_dir = self.store.layout.ensure_asset_temp()
            self.temp_dir_fd = os.open(
                temp_dir,
                os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
            )
            self.temp_fd = os.open(
                self.temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
                0o440,
                dir_fd=self.temp_dir_fd,
            )
            self.temp_exists = True
        except OSError as exc:
            self.abort()
            raise _asset_os_error(exc) from exc

    def write(self, chunk: bytes) -> None:
        if self.finished or self.temp_fd < 0 or not isinstance(chunk, bytes):
            raise asset_runtime_error()
        if not chunk:
            return
        new_total = self.total + len(chunk)
        if new_total > self.store.max_asset_bytes:
            raise SandboxServiceError(
                SandboxErrorCode.ASSET_TOO_LARGE,
                "资产超过允许的单文件大小上限",
                hint="请拆分文件或联系运维调整配额",
            )
        try:
            view = memoryview(chunk)
            written = 0
            while written < len(view):
                count = os.write(self.temp_fd, view[written:])
                if count <= 0:
                    raise OSError(errno.EIO, "short asset write")
                written += count
        except OSError as exc:
            raise _asset_os_error(exc) from exc
        self.digest.update(chunk)
        self.total = new_total

    def finish(self) -> PublishedAsset:
        if self.finished or self.temp_fd < 0 or self.temp_dir_fd < 0:
            raise asset_runtime_error()
        try:
            os.fsync(self.temp_fd)
            os.fchmod(self.temp_fd, 0o440)
            os.close(self.temp_fd)
            self.temp_fd = -1
            sha256 = self.digest.hexdigest()
            target_parent = self.store.layout.ensure_asset_parent(sha256)
            target_dir_fd = os.open(
                target_parent,
                os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
            )
            try:
                try:
                    os.link(
                        self.temp_name,
                        sha256,
                        src_dir_fd=self.temp_dir_fd,
                        dst_dir_fd=target_dir_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    existing_fd = os.open(
                        sha256,
                        os.O_RDONLY | _NOFOLLOW | _CLOEXEC,
                        dir_fd=target_dir_fd,
                    )
                    try:
                        metadata = os.fstat(existing_fd)
                        if not stat.S_ISREG(metadata.st_mode):
                            raise asset_runtime_error()
                        existing_hash, existing_size = _hash_open_file(
                            existing_fd,
                            max_bytes=self.store.max_asset_bytes,
                        )
                        if existing_hash != sha256 or existing_size != self.total:
                            raise asset_runtime_error()
                    finally:
                        os.close(existing_fd)
                os.fsync(target_dir_fd)
            finally:
                os.close(target_dir_fd)

            os.unlink(self.temp_name, dir_fd=self.temp_dir_fd)
            self.temp_exists = False
            os.fsync(self.temp_dir_fd)
            self.finished = True
            result = PublishedAsset(
                sha256=sha256,
                size_bytes=self.total,
                media_type=self.media_type,
                storage_key=self.store.layout.asset_storage_key(sha256),
            )
            self.abort()
            return result
        except SandboxServiceError:
            self.abort()
            raise
        except OSError as exc:
            self.abort()
            raise _asset_os_error(exc) from exc

    def abort(self) -> None:
        if self.temp_fd >= 0:
            try:
                os.close(self.temp_fd)
            except OSError:
                pass
            self.temp_fd = -1
        if self.temp_exists and self.temp_dir_fd >= 0:
            try:
                os.unlink(self.temp_name, dir_fd=self.temp_dir_fd)
            except OSError:
                pass
            self.temp_exists = False
        if self.temp_dir_fd >= 0:
            try:
                os.close(self.temp_dir_fd)
            except OSError:
                pass
            self.temp_dir_fd = -1


class LocalAssetStore:
    """sandboxd 侧本地 Asset Store；Nanobot Server 不应直接实例化。"""

    def __init__(
        self,
        layout: SandboxStorageLayout,
        *,
        max_asset_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        self.layout = layout
        self.max_asset_bytes = int(max_asset_bytes)

    def open_upload(
        self,
        *,
        media_type: str = "application/octet-stream",
    ) -> AssetUploadWriter:
        return AssetUploadWriter(self, media_type)

    def publish(
        self,
        workspace_id: str,
        relative_path: str,
        *,
        media_type: str = "application/octet-stream",
    ) -> PublishedAsset:
        workspace_root = self.layout.workspace_data_dir(workspace_id)
        workspace_fs = SafeWorkspaceFilesystem(workspace_root)
        temp_dir = self.layout.ensure_asset_temp()
        temp_name = f"publish-{uuid4().hex}.tmp"
        temp_fd = -1
        temp_dir_fd = -1
        temp_exists = False

        try:
            with workspace_fs.open_regular_file(relative_path) as (
                source_fd,
                source_size,
            ):
                if source_size > self.max_asset_bytes:
                    raise SandboxServiceError(
                        SandboxErrorCode.ASSET_TOO_LARGE,
                        "资产超过允许的单文件大小上限",
                        hint="请拆分文件或联系运维调整配额",
                    )
                temp_dir_fd = os.open(
                    temp_dir,
                    os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
                )
                temp_fd = os.open(
                    temp_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
                    0o440,
                    dir_fd=temp_dir_fd,
                )
                temp_exists = True
                digest = hashlib.sha256()
                total = 0
                position = 0
                while True:
                    chunk = os.pread(source_fd, 1024 * 1024, position)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.max_asset_bytes:
                        raise SandboxServiceError(
                            SandboxErrorCode.ASSET_TOO_LARGE,
                            "资产超过允许的单文件大小上限",
                            hint="请拆分文件或联系运维调整配额",
                        )
                    view = memoryview(chunk)
                    written = 0
                    while written < len(view):
                        written += os.write(temp_fd, view[written:])
                    digest.update(chunk)
                    position += len(chunk)
                os.fsync(temp_fd)
                os.fchmod(temp_fd, 0o440)
                os.close(temp_fd)
                temp_fd = -1

            sha256 = digest.hexdigest()
            target_parent = self.layout.ensure_asset_parent(sha256)
            target_dir_fd = os.open(
                target_parent,
                os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
            )
            try:
                try:
                    os.link(
                        temp_name,
                        sha256,
                        src_dir_fd=temp_dir_fd,
                        dst_dir_fd=target_dir_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    existing_fd = os.open(
                        sha256,
                        os.O_RDONLY | _NOFOLLOW | _CLOEXEC,
                        dir_fd=target_dir_fd,
                    )
                    try:
                        metadata = os.fstat(existing_fd)
                        if not stat.S_ISREG(metadata.st_mode):
                            raise asset_runtime_error()
                        existing_hash, existing_size = _hash_open_file(
                            existing_fd,
                            max_bytes=self.max_asset_bytes,
                        )
                        if existing_hash != sha256 or existing_size != total:
                            raise asset_runtime_error()
                    finally:
                        os.close(existing_fd)
                os.fsync(target_dir_fd)
            finally:
                os.close(target_dir_fd)

            os.unlink(temp_name, dir_fd=temp_dir_fd)
            temp_exists = False
            os.fsync(temp_dir_fd)
            return PublishedAsset(
                sha256=sha256,
                size_bytes=total,
                media_type=safe_media_type(media_type),
                storage_key=self.layout.asset_storage_key(sha256),
            )
        except SandboxServiceError:
            raise
        except OSError as exc:
            raise _asset_os_error(exc) from exc
        finally:
            if temp_fd >= 0:
                os.close(temp_fd)
            if temp_exists and temp_dir_fd >= 0:
                try:
                    os.unlink(temp_name, dir_fd=temp_dir_fd)
                except OSError:
                    pass
            if temp_dir_fd >= 0:
                os.close(temp_dir_fd)

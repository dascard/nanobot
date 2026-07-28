"""供 sandboxd 使用的目录 FD 安全文件操作。"""

from __future__ import annotations

import errno
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import UUID, uuid4

from core.sandbox.contracts import (
    FileEntry,
    SandboxErrorCode,
    SandboxServiceError,
    WrittenFile,
)


MAX_RELATIVE_PATH_BYTES = 4096
MAX_PATH_COMPONENT_BYTES = 255
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


def validate_relative_path(
    value: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    """按 POSIX 语义校验模型可见相对路径。"""

    if not isinstance(value, str):
        raise _invalid_path()
    if "\x00" in value or "\\" in value:
        raise _invalid_path()
    if value in {"", "."}:
        if allow_empty:
            return ()
        raise _invalid_path()
    if value.startswith("/") or value.endswith("/"):
        raise _invalid_path()
    try:
        raw_size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise _invalid_path() from exc
    if raw_size > MAX_RELATIVE_PATH_BYTES:
        raise _invalid_path()

    components = value.split("/")
    for component in components:
        if component in {"", ".", ".."}:
            raise _invalid_path()
        try:
            component_size = len(component.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise _invalid_path() from exc
        if component_size > MAX_PATH_COMPONENT_BYTES:
            raise _invalid_path()
    return tuple(components)


def validate_workspace_id(value: str) -> str:
    try:
        parsed = UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise SandboxServiceError(
            SandboxErrorCode.AUTHORIZATION_FAILED,
            "Workspace 标识无效",
        ) from exc
    canonical = str(parsed)
    if str(value) != canonical:
        raise SandboxServiceError(
            SandboxErrorCode.AUTHORIZATION_FAILED,
            "Workspace 标识无效",
        )
    return canonical


def validate_sha256(value: str) -> str:
    normalized = str(value or "").lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise SandboxServiceError(
            SandboxErrorCode.ASSET_NOT_AUTHORIZED,
            "资产不存在或当前 Workspace 无权访问",
        )
    return normalized


def _invalid_path() -> SandboxServiceError:
    return SandboxServiceError(
        SandboxErrorCode.INVALID_PATH,
        "工作区相对路径无效或不可安全访问",
        hint="只使用 /workspace 下不含 .. 的相对路径",
    )


def _map_os_error(exc: OSError) -> SandboxServiceError:
    if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)}:
        return SandboxServiceError(
            SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
            "工作区空间配额已用完",
            hint="停止重试，并告知用户需要清理或扩容",
        )
    if exc.errno in {
        errno.EACCES,
        errno.EPERM,
        errno.ENOENT,
        errno.ENOTDIR,
        errno.EISDIR,
        errno.ELOOP,
        errno.EEXIST,
        errno.ENXIO,
        errno.ENODEV,
        errno.ENAMETOOLONG,
    }:
        return _invalid_path()
    return SandboxServiceError(
        SandboxErrorCode.RUNTIME_UNAVAILABLE,
        "Sandbox 文件服务暂时不可用",
        retryable=True,
        hint="稍后重试；持续失败时联系运维检查 sandboxd",
        stop=False,
    )


def _open_directory_path(path: Path) -> int:
    try:
        return os.open(path, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC)
    except OSError as exc:
        raise _map_os_error(exc) from exc


@contextmanager
def _directory_fd(
    root: Path,
    components: tuple[str, ...],
    *,
    create: bool = False,
    created_owner: tuple[int, int] | None = None,
) -> Iterator[int]:
    current_fd = _open_directory_path(root)
    try:
        for component in components:
            created = False
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=current_fd)
                    created = True
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise _map_os_error(exc) from exc
            next_fd: int | None = None
            try:
                next_fd = os.open(
                    component,
                    os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
                    dir_fd=current_fd,
                )
                if created and created_owner is not None:
                    os.fchown(next_fd, *created_owner)
                    os.fchmod(next_fd, 0o700)
            except OSError as exc:
                if next_fd is not None:
                    os.close(next_fd)
                raise _map_os_error(exc) from exc
            os.close(current_fd)
            current_fd = next_fd
        yield current_fd
    finally:
        os.close(current_fd)


def _entry_type(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "other"


class SandboxStorageLayout:
    """把 UUID/hash 映射为宿主目录；owner ID 永不进入路径。"""

    def __init__(self, base_dir: str | os.PathLike[str]) -> None:
        self.base_dir = Path(base_dir)
        self.workspaces_root = self.base_dir / "workspaces"
        self.assets_root = self.base_dir / "assets"
        self.asset_blobs_root = self.assets_root / "sha256"
        self.runtime_root = self.base_dir / "runtime"

    def ensure_roots(self) -> None:
        """只对受信运维配置的固定根目录执行创建。"""

        try:
            self.base_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
            for path in (
                self.workspaces_root,
                self.assets_root,
                self.asset_blobs_root,
                self.runtime_root,
            ):
                path.mkdir(exist_ok=True, mode=0o750)
        except OSError as exc:
            raise _map_os_error(exc) from exc

    def ensure_workspace(self, workspace_id: str) -> Path:
        workspace_id = validate_workspace_id(workspace_id)
        self.ensure_roots()
        components = (workspace_id[:2], workspace_id, "data")
        with _directory_fd(self.workspaces_root, components, create=True):
            pass
        return self.workspaces_root.joinpath(*components)

    def workspace_data_dir(self, workspace_id: str) -> Path:
        workspace_id = validate_workspace_id(workspace_id)
        return self.workspaces_root / workspace_id[:2] / workspace_id / "data"

    def ensure_runtime(self, workspace_id: str) -> Path:
        workspace_id = validate_workspace_id(workspace_id)
        self.ensure_roots()
        with _directory_fd(self.runtime_root, (workspace_id,), create=True):
            pass
        return self.runtime_root / workspace_id

    def ensure_asset_temp(self) -> Path:
        self.ensure_roots()
        with _directory_fd(self.asset_blobs_root, (".tmp",), create=True):
            pass
        return self.asset_blobs_root / ".tmp"

    def ensure_asset_parent(self, sha256: str) -> Path:
        sha256 = validate_sha256(sha256)
        self.ensure_roots()
        components = (sha256[:2], sha256[2:4])
        with _directory_fd(self.asset_blobs_root, components, create=True):
            pass
        return self.asset_blobs_root.joinpath(*components)

    @staticmethod
    def asset_storage_key(sha256: str) -> str:
        sha256 = validate_sha256(sha256)
        return f"sha256/{sha256[:2]}/{sha256[2:4]}/{sha256}"


class SafeWorkspaceFilesystem:
    """所有模型路径都通过 dir_fd 与 O_NOFOLLOW 访问。"""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        write_uid: int | None = None,
        write_gid: int | None = None,
    ) -> None:
        if (write_uid is None) != (write_gid is None):
            raise ValueError("写入 UID/GID 必须同时提供")
        if write_uid is None or write_gid is None:
            self._write_owner: tuple[int, int] | None = None
        else:
            self._write_owner = (write_uid, write_gid)
        if self._write_owner is not None and any(
            value < 0 for value in self._write_owner
        ):
            raise ValueError("写入 UID/GID 不能为负数")
        self.root = Path(root)

    @contextmanager
    def _parent_fd(
        self,
        relative_path: str,
        *,
        create: bool = False,
    ) -> Iterator[tuple[int, str, tuple[str, ...]]]:
        components = validate_relative_path(relative_path)
        with _directory_fd(
            self.root,
            components[:-1],
            create=create,
            created_owner=self._write_owner,
        ) as parent_fd:
            yield parent_fd, components[-1], components

    @contextmanager
    def open_regular_file(self, relative_path: str) -> Iterator[tuple[int, int]]:
        with self._parent_fd(relative_path) as (parent_fd, name, _components):
            try:
                file_fd = os.open(
                    name,
                    os.O_RDONLY | _NOFOLLOW | _CLOEXEC | _NONBLOCK,
                    dir_fd=parent_fd,
                )
                metadata = os.fstat(file_fd)
                if not stat.S_ISREG(metadata.st_mode):
                    os.close(file_fd)
                    raise SandboxServiceError(
                        SandboxErrorCode.UNSUPPORTED_FILE_TYPE,
                        "只允许访问工作区中的普通文件",
                    )
            except SandboxServiceError:
                raise
            except OSError as exc:
                raise _map_os_error(exc) from exc
            try:
                yield file_fd, int(metadata.st_size)
            finally:
                os.close(file_fd)

    def list_entries(self, relative_path: str = "") -> list[FileEntry]:
        components = validate_relative_path(relative_path, allow_empty=True)
        display_prefix = "/".join(components)
        with _directory_fd(self.root, components) as directory_fd:
            try:
                names = sorted(os.listdir(directory_fd))
            except OSError as exc:
                raise _map_os_error(exc) from exc
            entries: list[FileEntry] = []
            for name in names:
                try:
                    metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    name.encode("utf-8")
                except (OSError, UnicodeEncodeError):
                    continue
                path = f"{display_prefix}/{name}" if display_prefix else name
                entries.append(FileEntry(
                    path=path,
                    type=_entry_type(metadata.st_mode),
                    size_bytes=int(metadata.st_size) if stat.S_ISREG(metadata.st_mode) else 0,
                    modified_at_ns=int(metadata.st_mtime_ns),
                ))
            return entries

    def read_bytes(self, relative_path: str, *, offset: int, limit: int) -> bytes:
        if offset < 0 or limit <= 0:
            raise _invalid_path()
        with self.open_regular_file(relative_path) as (file_fd, size_bytes):
            if offset >= size_bytes:
                return b""
            remaining = min(limit, size_bytes - offset)
            chunks: list[bytes] = []
            position = offset
            while remaining > 0:
                try:
                    chunk = os.pread(file_fd, min(remaining, 64 * 1024), position)
                except OSError as exc:
                    raise _map_os_error(exc) from exc
                if not chunk:
                    break
                chunks.append(chunk)
                position += len(chunk)
                remaining -= len(chunk)
            return b"".join(chunks)

    def regular_file_size(self, relative_path: str) -> int:
        with self.open_regular_file(relative_path) as (_file_fd, size_bytes):
            return size_bytes

    def write_bytes(
        self,
        relative_path: str,
        content: bytes,
        *,
        overwrite: bool,
        max_bytes: int,
    ) -> WrittenFile:
        if not isinstance(content, bytes) or len(content) > max_bytes:
            raise SandboxServiceError(
                SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
                "写入内容超过单次允许上限",
                hint="大文件请使用资产上传接口",
            )
        with self._parent_fd(relative_path, create=True) as (
            parent_fd,
            name,
            components,
        ):
            previous_size = 0
            try:
                previous = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                previous = None
            except OSError as exc:
                raise _map_os_error(exc) from exc
            if previous is not None:
                if not stat.S_ISREG(previous.st_mode) or not overwrite:
                    raise _invalid_path()
                previous_size = int(previous.st_size)

            temp_name = f".nanobot-write-{uuid4().hex}.tmp"
            temp_created = False
            try:
                temp_fd = os.open(
                    temp_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
                    0o600,
                    dir_fd=parent_fd,
                )
                temp_created = True
                try:
                    view = memoryview(content)
                    written = 0
                    while written < len(view):
                        written += os.write(temp_fd, view[written:])
                    os.fsync(temp_fd)
                    if self._write_owner is not None:
                        os.fchown(temp_fd, *self._write_owner)
                        os.fchmod(temp_fd, 0o600)
                finally:
                    os.close(temp_fd)

                if overwrite:
                    os.replace(
                        temp_name,
                        name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                    )
                    temp_created = False
                else:
                    os.link(
                        temp_name,
                        name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                    os.unlink(temp_name, dir_fd=parent_fd)
                    temp_created = False
                os.fsync(parent_fd)
            except SandboxServiceError:
                raise
            except OSError as exc:
                raise _map_os_error(exc) from exc
            finally:
                if temp_created:
                    try:
                        os.unlink(temp_name, dir_fd=parent_fd)
                    except OSError:
                        pass

        normalized_path = "/".join(components)
        return WrittenFile(
            path=normalized_path,
            size_bytes=len(content),
            previous_size_bytes=previous_size,
        )

    def copy_from_fd(
        self,
        relative_path: str,
        source_fd: int,
        *,
        size_bytes: int,
        max_bytes: int,
        overwrite: bool = False,
    ) -> WrittenFile:
        """把已安全打开的普通文件流式复制到相对路径。"""

        if size_bytes < 0 or size_bytes > max_bytes:
            raise SandboxServiceError(
                SandboxErrorCode.ASSET_TOO_LARGE,
                "资产超过允许的单文件大小上限",
            )
        with self._parent_fd(relative_path, create=True) as (
            parent_fd,
            name,
            components,
        ):
            previous_size = 0
            try:
                previous = os.stat(
                    name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                previous = None
            except OSError as exc:
                raise _map_os_error(exc) from exc
            if previous is not None:
                if not stat.S_ISREG(previous.st_mode) or not overwrite:
                    raise _invalid_path()
                previous_size = int(previous.st_size)

            temp_name = f".nanobot-copy-{uuid4().hex}.tmp"
            temp_created = False
            copied = 0
            try:
                temp_fd = os.open(
                    temp_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
                    0o400,
                    dir_fd=parent_fd,
                )
                temp_created = True
                try:
                    while copied < size_bytes:
                        chunk = os.pread(
                            source_fd,
                            min(1024 * 1024, size_bytes - copied),
                            copied,
                        )
                        if not chunk:
                            break
                        view = memoryview(chunk)
                        written = 0
                        while written < len(view):
                            written += os.write(temp_fd, view[written:])
                        copied += len(chunk)
                    if copied != size_bytes:
                        raise SandboxServiceError(
                            SandboxErrorCode.RUNTIME_UNAVAILABLE,
                            "资产存储暂时不可用",
                            retryable=True,
                            stop=False,
                        )
                    os.fsync(temp_fd)
                    if self._write_owner is not None:
                        os.fchown(temp_fd, *self._write_owner)
                    os.fchmod(temp_fd, 0o400)
                finally:
                    os.close(temp_fd)
                if overwrite:
                    os.replace(
                        temp_name,
                        name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                    )
                    temp_created = False
                else:
                    os.link(
                        temp_name,
                        name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                    os.unlink(temp_name, dir_fd=parent_fd)
                    temp_created = False
                os.fsync(parent_fd)
            except SandboxServiceError:
                raise
            except OSError as exc:
                raise _map_os_error(exc) from exc
            finally:
                if temp_created:
                    try:
                        os.unlink(temp_name, dir_fd=parent_fd)
                    except OSError:
                        pass
        return WrittenFile(
            path="/".join(components),
            size_bytes=copied,
            previous_size_bytes=previous_size,
        )

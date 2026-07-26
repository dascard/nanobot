"""Prompt flow 文件的安全路径、共享写锁与原子替换。"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
import tempfile
import threading
from typing import Iterator


class FlowStorageError(ValueError):
    """Prompt flow 存储路径或文件类型不安全。"""


_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def assert_no_symlink_components(path: Path) -> Path:
    """逐级 lstat 路径，拒绝任何现存的符号链接组件。"""
    absolute = _absolute_without_resolving(Path(path))
    current = Path(absolute.anchor)
    parts = absolute.parts[1:] if absolute.anchor else absolute.parts
    for index, part in enumerate(parts):
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise FlowStorageError(f"无法检查 flow 存储路径: {current}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise FlowStorageError(f"flow 存储路径不能包含符号链接: {current}")
        if index < len(parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            raise FlowStorageError(f"flow 存储路径祖先不是目录: {current}")
    return absolute


def ensure_directory_without_symlinks(path: Path) -> Path:
    """在创建前后检查目录链，避免沿祖先 symlink 写出配置根。"""
    absolute = assert_no_symlink_components(path)
    absolute.mkdir(parents=True, exist_ok=True)
    assert_no_symlink_components(absolute)
    metadata = absolute.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise FlowStorageError(f"flow 存储路径不是目录: {absolute}")
    return absolute


def read_regular_bytes(path: Path, *, missing_ok: bool = False) -> bytes | None:
    """非阻塞打开并读取普通文件，避免 FIFO/设备路径挂住进程。"""
    absolute = assert_no_symlink_components(Path(path))
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(absolute, flags)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    except OSError as exc:
        raise FlowStorageError(f"无法安全打开 Prompt 存储文件: {absolute}") from exc

    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise FlowStorageError(
                f"Prompt 存储输入必须是普通文件: {absolute}"
            )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    except FlowStorageError:
        raise
    except OSError as exc:
        raise FlowStorageError(f"无法安全读取 Prompt 存储文件: {absolute}") from exc
    finally:
        os.close(descriptor)


def _thread_lock_for(path: Path) -> threading.RLock:
    key = str(_absolute_without_resolving(path))
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def flow_write_lock(target: Path) -> Iterator[None]:
    """对同一 flow 文件同时施加线程锁和进程间排他锁。"""
    target = _absolute_without_resolving(Path(target))
    parent = ensure_directory_without_symlinks(target.parent)
    lock_path = parent / f".{target.name}.lock"
    thread_lock = _thread_lock_for(target)
    with thread_lock:
        assert_no_symlink_components(lock_path)
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise FlowStorageError(f"flow 锁文件不是普通文件: {lock_path}")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


@contextmanager
def _template_governance_lock(
    runtime_root: Path,
    *,
    exclusive: bool,
) -> Iterator[None]:
    """对一次完整模板快照施加跨线程、跨进程治理锁。"""
    runtime_root = _absolute_without_resolving(Path(runtime_root))
    lock_parent = ensure_directory_without_symlinks(runtime_root.parent)
    lock_path = lock_parent / ".prompt-template-governance.lock"
    thread_lock = _thread_lock_for(lock_path)
    with thread_lock:
        assert_no_symlink_components(lock_path)
        common_flags = (
            getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        if exclusive:
            descriptor = os.open(
                lock_path,
                os.O_RDWR | os.O_CREAT | common_flags,
                0o600,
            )
        else:
            try:
                descriptor = os.open(
                    lock_path,
                    os.O_RDONLY | common_flags,
                )
            except FileNotFoundError:
                try:
                    initializer = os.open(
                        lock_path,
                        os.O_RDWR | os.O_CREAT | common_flags,
                        0o600,
                    )
                except OSError as exc:
                    raise FlowStorageError(
                        "共享读取前必须由可写 Runtime 身份初始化模板治理锁"
                    ) from exc
                os.close(initializer)
                descriptor = os.open(
                    lock_path,
                    os.O_RDONLY | common_flags,
                )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise FlowStorageError(f"模板治理锁文件不是普通文件: {lock_path}")
            fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


@contextmanager
def template_governance_read_lock(runtime_root: Path) -> Iterator[None]:
    """持有一次完整模板读取所需的共享治理锁。"""
    with _template_governance_lock(runtime_root, exclusive=False):
        yield


@contextmanager
def template_governance_write_lock(runtime_root: Path) -> Iterator[None]:
    """持有模板 provision、管理写入或迁移所需的排他治理锁。"""
    with _template_governance_lock(runtime_root, exclusive=True):
        yield


def fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_replace_bytes(target: Path, data: bytes) -> None:
    """在目标同目录落盘并原子替换；调用方应持有共享 flow 写锁。"""
    target = _absolute_without_resolving(Path(target))
    parent = ensure_directory_without_symlinks(target.parent)
    assert_no_symlink_components(target)
    mode = 0o600
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(metadata.st_mode):
            raise FlowStorageError("runtime flow 必须是普通文件")
        mode = stat.S_IMODE(metadata.st_mode)

    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(target)
        fsync_directory(parent)
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_remove_regular_file(target: Path) -> bool:
    """拒绝 symlink/非普通文件并持久删除；不存在时返回 False。"""
    target = _absolute_without_resolving(Path(target))
    parent = ensure_directory_without_symlinks(target.parent)
    assert_no_symlink_components(target)
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(metadata.st_mode):
        raise FlowStorageError(f"待删除路径必须是普通文件: {target}")
    target.unlink()
    fsync_directory(parent)
    return True

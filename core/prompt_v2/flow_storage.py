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

"""SQLite 在线一致快照服务。"""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path


logger = logging.getLogger("nanobot.sqlite_backup")


def _reserve_snapshot_path(
    target_path: Path | None,
    *,
    temp_dir: str | Path | None,
) -> Path:
    if target_path is None:
        fd, raw_path = tempfile.mkstemp(
            prefix="nanobot-snapshot-",
            suffix=".db",
            dir=temp_dir,
        )
        snapshot_path = Path(raw_path)
    else:
        snapshot_path = target_path
        fd = os.open(snapshot_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)

    try:
        os.close(fd)
    except BaseException:
        snapshot_path.unlink(missing_ok=True)
        raise
    return snapshot_path


def _remove_partial_snapshot(snapshot_path: Path) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        partial_path = Path(f"{snapshot_path}{suffix}")
        try:
            partial_path.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "Failed to remove partial SQLite snapshot file: %s",
                partial_path,
                exc_info=True,
            )


def create_sqlite_snapshot(
    source_path: str | Path,
    target_path: str | Path | None = None,
    *,
    temp_dir: str | Path | None = None,
) -> Path:
    """使用 SQLite backup API 创建由调用方负责删除的一致快照。"""
    source = Path(source_path).resolve()
    if not source.exists():
        raise FileNotFoundError(source)

    target = Path(target_path).resolve() if target_path is not None else None
    if target is not None and source == target:
        raise ValueError("SQLite snapshot source and target must be different")

    snapshot_path = _reserve_snapshot_path(target, temp_dir=temp_dir)
    source_uri = f"{source.as_uri()}?mode=ro"
    try:
        with (
            closing(sqlite3.connect(source_uri, uri=True)) as source_conn,
            closing(sqlite3.connect(snapshot_path)) as target_conn,
        ):
            source_conn.backup(target_conn, pages=256, sleep=0.05)
    except BaseException:
        _remove_partial_snapshot(snapshot_path)
        raise

    return snapshot_path

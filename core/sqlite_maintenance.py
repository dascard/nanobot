"""SQLite WAL 观测与单进程受控 checkpoint。"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.engine import Engine

from core.database import sqlite_path_from_database_url


logger = logging.getLogger("nanobot.sqlite_maintenance")


def _positive_float_env(name: str, default: float) -> float:
    try:
        return max(0.1, float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(float(os.environ.get(name, str(default)))))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class SQLiteCheckpointResult:
    checked_at: str
    wal_size_bytes: int
    busy: int
    log_pages: int
    checkpointed_pages: int
    mode: str
    error: str = ""


class SQLiteMaintenanceWorker:
    """唯一 maintenance owner；仅对文件型 SQLite 执行 PASSIVE checkpoint。"""

    def __init__(
        self,
        *,
        engine: Engine | None = None,
        database_url: str | None = None,
        interval_seconds: float | None = None,
        truncate_threshold_bytes: int | None = None,
    ) -> None:
        if engine is None:
            # 测试和进程启动代码可能在模块导入后替换全局 engine。默认值必须在
            # 调用时解析，不能通过函数默认参数永久捕获旧连接池。
            from core import database

            engine = database.engine
        self.engine = engine
        self.database_url = str(database_url or engine.url)
        self.database_path = sqlite_path_from_database_url(self.database_url)
        self.interval_seconds = (
            _positive_float_env("SQLITE_CHECKPOINT_INTERVAL_SECONDS", 60.0)
            if interval_seconds is None
            else max(0.1, float(interval_seconds))
        )
        self.truncate_threshold_bytes = (
            _positive_int_env("SQLITE_WAL_TRUNCATE_THRESHOLD_BYTES", 256 * 1024 * 1024)
            if truncate_threshold_bytes is None
            else max(1, int(truncate_threshold_bytes))
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot_lock = threading.Lock()
        self._last_result: SQLiteCheckpointResult | None = None

    @property
    def enabled(self) -> bool:
        return self.database_path is not None

    def start(self) -> None:
        if not self.enabled:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="nanobot-sqlite-maintenance",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout_seconds: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout_seconds)))
        self._thread = None

    def snapshot(self) -> dict[str, Any]:
        with self._snapshot_lock:
            result = self._last_result
        return {
            "enabled": self.enabled,
            "database_path": self.database_path or "",
            "running": bool(self._thread is not None and self._thread.is_alive()),
            "last_result": asdict(result) if result is not None else None,
        }

    def run_once(self) -> SQLiteCheckpointResult:
        """先 PASSIVE；仅当已完全追平且超过水位时尝试 TRUNCATE。"""

        checked_at = datetime.now().isoformat(timespec="seconds")
        wal_size = self._wal_size_bytes()
        if not self.enabled:
            result = SQLiteCheckpointResult(
                checked_at=checked_at,
                wal_size_bytes=0,
                busy=0,
                log_pages=0,
                checkpointed_pages=0,
                mode="disabled",
            )
            self._store_result(result)
            return result

        try:
            busy, log_pages, checkpointed_pages = self._checkpoint("PASSIVE")
            mode = "passive"
            if (
                wal_size >= self.truncate_threshold_bytes
                and busy == 0
                and checkpointed_pages >= log_pages
            ):
                busy, log_pages, checkpointed_pages = self._checkpoint("TRUNCATE")
                mode = "truncate"
                wal_size = self._wal_size_bytes()
            result = SQLiteCheckpointResult(
                checked_at=checked_at,
                wal_size_bytes=wal_size,
                busy=busy,
                log_pages=log_pages,
                checkpointed_pages=checkpointed_pages,
                mode=mode,
            )
        except Exception as exc:
            result = SQLiteCheckpointResult(
                checked_at=checked_at,
                wal_size_bytes=wal_size,
                busy=0,
                log_pages=0,
                checkpointed_pages=0,
                mode="error",
                error=f"{type(exc).__name__}: {exc}",
            )
            logger.warning("SQLite WAL maintenance failed: %s", result.error)
        self._store_result(result)
        return result

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.run_once()
            self._stop_event.wait(self.interval_seconds)

    def _checkpoint(self, mode: str) -> tuple[int, int, int]:
        raw_connection = self.engine.raw_connection()
        try:
            cursor = raw_connection.cursor()
            try:
                cursor.execute(f"PRAGMA wal_checkpoint({mode})")
                row = cursor.fetchone()
            finally:
                cursor.close()
        finally:
            raw_connection.close()
        if row is None or len(row) < 3:
            raise RuntimeError("wal_checkpoint 未返回完整状态")
        return int(row[0]), int(row[1]), int(row[2])

    def _wal_size_bytes(self) -> int:
        if not self.database_path:
            return 0
        try:
            return os.path.getsize(f"{self.database_path}-wal")
        except OSError:
            return 0

    def _store_result(self, result: SQLiteCheckpointResult) -> None:
        with self._snapshot_lock:
            self._last_result = result


_worker_lock = threading.Lock()
_active_worker: SQLiteMaintenanceWorker | None = None


def start_sqlite_maintenance(
    *,
    engine: Engine | None = None,
    database_url: str | None = None,
) -> SQLiteMaintenanceWorker:
    """启动本进程唯一的 WAL maintenance owner。"""

    global _active_worker
    with _worker_lock:
        if _active_worker is not None:
            return _active_worker
        worker = SQLiteMaintenanceWorker(
            engine=engine,
            database_url=database_url,
        )
        worker.start()
        _active_worker = worker
        return worker


def stop_sqlite_maintenance(worker: SQLiteMaintenanceWorker | None = None) -> None:
    global _active_worker
    with _worker_lock:
        current = _active_worker
        if current is None:
            return
        if worker is not None and worker is not current:
            return
        _active_worker = None
    current.stop()


def sqlite_maintenance_snapshot() -> dict[str, Any]:
    with _worker_lock:
        worker = _active_worker
    if worker is None:
        return {"enabled": False, "running": False, "last_result": None}
    return worker.snapshot()

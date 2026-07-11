"""SQLite 锁竞争重试工具。"""

from __future__ import annotations

import os
import time
from typing import Any
from collections.abc import Callable

from sqlalchemy.exc import OperationalError


SQLITE_LOCK_MESSAGES = (
    "database is locked",
    "database table is locked",
    "database is busy",
)


def is_sqlite_locked_error(exc: BaseException) -> bool:
    if not isinstance(exc, OperationalError):
        return False
    texts = [str(exc).lower()]
    original = getattr(exc, "orig", None)
    if original is not None:
        texts.append(str(original).lower())
    return any(message in text for text in texts for message in SQLITE_LOCK_MESSAGES)


def _int_env(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return int(default)


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def run_sqlite_locked_retry(
    operation: Callable[[], Any],
    *,
    rollback: Callable[[], Any] | None = None,
    label: str = "",
    logger: Any = None,
    attempts: int | None = None,
    base_delay_seconds: float | None = None,
) -> Any:
    max_attempts = max(1, int(attempts or _int_env("SQLITE_LOCK_RETRY_ATTEMPTS", 4)))
    base_delay = max(0.0, float(
        base_delay_seconds
        if base_delay_seconds is not None
        else _float_env("SQLITE_LOCK_RETRY_BASE_DELAY_SECONDS", 0.05)
    ))
    for attempt in range(1, max_attempts + 1):
        started = time.monotonic()
        try:
            return operation()
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if not is_sqlite_locked_error(exc) or attempt >= max_attempts:
                raise
            if rollback is not None:
                try:
                    rollback()
                except BaseException as rollback_exc:
                    add_note = getattr(exc, "add_note", None)
                    if callable(add_note):
                        try:
                            add_note(
                                "SQLite 锁重试前 rollback 失败: "
                                f"{type(rollback_exc).__name__}: {rollback_exc}"
                            )
                        except BaseException:
                            pass
                    raise exc from rollback_exc
            delay = base_delay * (2 ** (attempt - 1))
            if logger is not None:
                next_attempt = attempt + 1
                log_method_name = "warning" if next_attempt >= max_attempts else "info"
                log_method = getattr(logger, log_method_name, None)
                if callable(log_method):
                    log_method(
                        "[SQLite] write locked; retrying label=%s attempt=%d/%d elapsed_ms=%d delay=%.3fs",
                        label or "write",
                        next_attempt,
                        max_attempts,
                        elapsed_ms,
                        delay,
                    )
            if delay > 0:
                time.sleep(delay)
    return operation()

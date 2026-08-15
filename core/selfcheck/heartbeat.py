"""跨进程 Worker 心跳的安全写入辅助。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from core.db.models.selfcheck import WorkerHeartbeat
from core.registry.validation import validate_identifier
from core.time_utils import db_now_naive


_LOGGER = logging.getLogger("nanobot.selfcheck.heartbeat")
_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_SENSITIVE_KEY_TOKENS = frozenset({
    "content",
    "credential",
    "key",
    "message",
    "password",
    "path",
    "payload",
    "query",
    "secret",
    "token",
})


def _safe_metadata(metadata: Mapping[str, object] | None) -> dict[str, object]:
    safe: dict[str, object] = {}
    for raw_key, value in (metadata or {}).items():
        key = str(raw_key or "").strip().lower()
        if (
            not key
            or len(key) > 64
            or any(token in key for token in _SENSITIVE_KEY_TOKENS)
        ):
            continue
        if isinstance(value, bool) or value is None:
            safe[key] = value
        elif isinstance(value, int):
            safe[key] = value
        elif isinstance(value, float):
            safe[key] = round(value, 6)
        elif isinstance(value, str) and len(value) <= 128:
            safe[key] = value
    return safe


def _error_code(value: str) -> str:
    code = str(value or "").strip().lower()
    if not code:
        return ""
    if _ERROR_CODE_RE.fullmatch(code) is None:
        return "worker_cycle_failed"
    return code


def record_worker_cycle(
    db: Session,
    *,
    worker_id: str,
    instance_id: str,
    mode: str,
    success: bool,
    now: datetime | None = None,
    error_code: str = "",
    metadata: Mapping[str, object] | None = None,
) -> WorkerHeartbeat:
    """在调用方事务内 upsert 一次循环事实，不保存正文或异常堆栈。"""

    resolved_worker_id = validate_identifier(
        worker_id,
        field_name="worker_heartbeat.worker_id",
    )
    resolved_instance_id = str(instance_id or "").strip()
    resolved_mode = str(mode or "").strip()
    if not resolved_instance_id or len(resolved_instance_id) > 128:
        raise ValueError("worker heartbeat instance_id 非法")
    if not resolved_mode or len(resolved_mode) > 32:
        raise ValueError("worker heartbeat mode 非法")
    observed_at = now or db_now_naive()
    row = db.get(WorkerHeartbeat, resolved_worker_id)
    if row is None:
        row = WorkerHeartbeat(
            worker_id=resolved_worker_id,
            instance_id=resolved_instance_id,
            mode=resolved_mode,
            state="running",
            cycle_count=0,
            success_count=0,
            failure_count=0,
            started_at=observed_at,
            last_seen_at=observed_at,
        )
        db.add(row)
    elif row.instance_id != resolved_instance_id:
        row.instance_id = resolved_instance_id
        row.started_at = observed_at
        row.cycle_count = 0
        row.success_count = 0
        row.failure_count = 0
        row.last_success_at = None
        row.last_error_at = None
        row.last_error_code = ""

    row.mode = resolved_mode
    row.state = "running"
    row.last_seen_at = observed_at
    row.cycle_count = int(row.cycle_count or 0) + 1
    if success:
        row.success_count = int(row.success_count or 0) + 1
        row.last_success_at = observed_at
    else:
        row.failure_count = int(row.failure_count or 0) + 1
        row.last_error_at = observed_at
        row.last_error_code = _error_code(error_code) or "worker_cycle_failed"
    row.metadata_json = json.dumps(
        _safe_metadata(metadata),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    db.flush()
    return row


def record_worker_cycle_with_factory(
    session_factory: Callable[[], Session],
    **kwargs: Any,
) -> bool:
    """Worker 循环使用的短事务包装；心跳失败不伪装业务循环结果。"""

    db = session_factory()
    try:
        record_worker_cycle(db, **kwargs)
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        _LOGGER.warning(
            "Worker heartbeat write failed worker=%s error_type=%s",
            kwargs.get("worker_id", "unknown"),
            type(exc).__name__,
        )
        return False
    finally:
        db.close()


__all__ = ["record_worker_cycle", "record_worker_cycle_with_factory"]

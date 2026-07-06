"""Web Search provider 调用统计。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from core.database import WebSearchProviderUsage


COUNTABLE_FAILURE_CODES = {
    "provider_auth_failed",
    "provider_rate_limited",
    "provider_timeout",
    "provider_bad_response",
    "empty_results",
}


def _empty_usage(provider_id: str) -> dict[str, Any]:
    return {
        "provider_id": provider_id,
        "total_calls": 0,
        "success_calls": 0,
        "failure_calls": 0,
        "last_called_at": None,
        "last_success_at": None,
        "last_error_at": None,
        "last_error_code": "",
        "last_duration_ms": 0,
    }


def _dt(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ", timespec="seconds") if value else None


def usage_to_dict(row: WebSearchProviderUsage | None, provider_id: str) -> dict[str, Any]:
    if row is None:
        return _empty_usage(provider_id)
    return {
        "provider_id": provider_id,
        "total_calls": int(row.total_calls or 0),
        "success_calls": int(row.success_calls or 0),
        "failure_calls": int(row.failure_calls or 0),
        "last_called_at": _dt(row.last_called_at),
        "last_success_at": _dt(row.last_success_at),
        "last_error_at": _dt(row.last_error_at),
        "last_error_code": row.last_error_code or "",
        "last_duration_ms": int(row.last_duration_ms or 0),
    }


def get_provider_usage(db: Session, provider_id: str) -> dict[str, Any]:
    row = db.query(WebSearchProviderUsage).filter_by(provider_id=provider_id).first()
    return usage_to_dict(row, provider_id)

def record_provider_usage(
    db: Session | None,
    provider_id: str,
    *,
    ok: bool,
    error_code: str = "",
    duration_ms: int = 0,
) -> dict[str, Any] | None:
    if db is None or not provider_id:
        return None
    if not ok and error_code and error_code not in COUNTABLE_FAILURE_CODES:
        return get_provider_usage(db, provider_id)

    now = datetime.now()
    row = db.query(WebSearchProviderUsage).filter_by(provider_id=provider_id).first()
    if row is None:
        row = WebSearchProviderUsage(provider_id=provider_id)
        db.add(row)

    row.total_calls = int(row.total_calls or 0) + 1
    row.last_called_at = now
    row.last_duration_ms = int(duration_ms or 0)
    row.updated_at = now
    if ok:
        row.success_calls = int(row.success_calls or 0) + 1
        row.last_success_at = now
    else:
        row.failure_calls = int(row.failure_calls or 0) + 1
        row.last_error_at = now
        row.last_error_code = error_code or ""

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return usage_to_dict(row, provider_id)

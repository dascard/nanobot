"""主动外呼幂等键的纯身份策略。"""

from __future__ import annotations

import hashlib
from datetime import datetime


def _key_anchor_text(anchor: datetime) -> str:
    normalized = anchor
    if normalized.tzinfo is not None:
        normalized = normalized.replace(tzinfo=None)
    return normalized.replace(microsecond=0).isoformat()


def outreach_key(
    user_id: str,
    anchor: datetime,
    *,
    forced: bool = False,
    attempt: int = 1,
) -> str:
    operation = f"forced:{max(1, int(attempt))}" if forced else "judge"
    raw = f"{user_id}:{_key_anchor_text(anchor)}:{operation}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"outreach:{user_id}:{digest}"


def post_clear_pending_key(
    *,
    user_id: str,
    idempotency_key: str,
    clear_at: datetime,
) -> str:
    """为历史清除后的同 key 调度派生不覆盖旧审计的稳定业务键。"""

    raw = (
        f"{user_id}\0{idempotency_key}\0"
        f"{clear_at.isoformat(timespec='microseconds')}\0post-clear-pending"
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"outreach:{user_id}:{digest}"


__all__ = ["outreach_key", "post_clear_pending_key"]

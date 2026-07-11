"""聊天断连 outbox 的异步投递服务。"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from core.chat_delivery_outbox import (
    claim_chat_delivery,
    claim_due_chat_delivery,
    decode_chat_delivery_envelope,
    enqueue_chat_delivery,
    mark_chat_delivery_ambiguous,
    mark_chat_delivery_delivered,
    mark_chat_delivery_failed,
    recover_stale_chat_deliveries,
)
from core.database import ChatDeliveryOutbox
from core.inbound_idempotency import InboundClaimKey


ChatDeliveryPublisher = Callable[
    [str, str, dict[str, Any]],
    Awaitable[bool | None],
]
SessionFactory = Callable[[], Session]
DEFAULT_ATTEMPT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class ChatDeliveryAttemptResult:
    row_id: int
    status: str
    attempted: bool
    error: str = ""


@dataclass(frozen=True)
class _ClaimedChatDelivery:
    row_id: int
    target_type: str
    target_id: str
    envelope_json: str


def _session_factory_or_default(
    session_factory: SessionFactory | None,
) -> SessionFactory:
    if session_factory is not None:
        return session_factory
    from core.database import SessionLocal

    return SessionLocal


def _validate_attempt_window(
    *,
    attempt_timeout_seconds: int | float,
    lease_seconds: int | float,
) -> float:
    if type(attempt_timeout_seconds) not in (int, float) or not math.isfinite(
        float(attempt_timeout_seconds)
    ) or float(attempt_timeout_seconds) <= 0:
        raise ValueError("attempt_timeout_seconds 必须是有限正数")
    if type(lease_seconds) not in (int, float) or not math.isfinite(
        float(lease_seconds)
    ) or float(lease_seconds) <= float(attempt_timeout_seconds):
        raise ValueError("lease_seconds 必须严格大于投递尝试超时")
    return float(attempt_timeout_seconds)


def _existing_result(
    session_factory: SessionFactory,
    row_id: int,
) -> ChatDeliveryAttemptResult:
    with session_factory() as db:
        row = db.get(ChatDeliveryOutbox, int(row_id))
        if row is None:
            raise RuntimeError("delivery outbox 记录不存在")
        return ChatDeliveryAttemptResult(
            row_id=int(row.id),
            status=str(row.status),
            attempted=False,
            error=str(row.last_error or ""),
        )


def _claim_attempt(
    session_factory: SessionFactory,
    *,
    row_id: int | None,
    owner_token: str,
    now: datetime | None,
    lease_seconds: int | float,
) -> _ClaimedChatDelivery | ChatDeliveryAttemptResult | None:
    with session_factory() as db:
        recover_stale_chat_deliveries(db, now=now)
        if row_id is None:
            row = claim_due_chat_delivery(
                db,
                owner_token=owner_token,
                now=now,
                lease_seconds=lease_seconds,
            )
        else:
            row = claim_chat_delivery(
                db,
                row_id=int(row_id),
                owner_token=owner_token,
                now=now,
                lease_seconds=lease_seconds,
            )
        claimed = (
            None
            if row is None
            else _ClaimedChatDelivery(
                row_id=int(row.id),
                target_type=str(row.target_type),
                target_id=str(row.target_id),
                envelope_json=str(row.envelope_json),
            )
        )
        db.commit()
        if claimed is not None:
            return claimed
        if row_id is None:
            return None
        existing = db.get(ChatDeliveryOutbox, int(row_id))
        if existing is None:
            raise RuntimeError("delivery outbox 记录不存在")
        return ChatDeliveryAttemptResult(
            row_id=int(existing.id),
            status=str(existing.status),
            attempted=False,
            error=str(existing.last_error or ""),
        )


def _enqueue_delivery(
    session_factory: SessionFactory,
    *,
    key: InboundClaimKey,
    target_type: str,
    target_id: str,
    envelope: Mapping[str, Any],
    now: datetime | None,
) -> ChatDeliveryAttemptResult:
    with session_factory() as db:
        row = enqueue_chat_delivery(
            db,
            key=key,
            target_type=target_type,
            target_id=target_id,
            envelope=envelope,
            now=now,
        )
        result = ChatDeliveryAttemptResult(
            row_id=int(row.id),
            status=str(row.status),
            attempted=False,
            error=str(row.last_error or ""),
        )
        db.commit()
        return result


def _settle_attempt(
    session_factory: SessionFactory,
    *,
    row_id: int,
    owner_token: str,
    status: str,
    error: str,
    now: datetime | None,
) -> bool:
    with session_factory() as db:
        if status == "delivered":
            settled = mark_chat_delivery_delivered(
                db,
                row_id=row_id,
                owner_token=owner_token,
                now=now,
            )
        elif status == "failed":
            settled = mark_chat_delivery_failed(
                db,
                row_id=row_id,
                owner_token=owner_token,
                error=error,
                now=now,
            )
        elif status == "ambiguous":
            settled = mark_chat_delivery_ambiguous(
                db,
                row_id=row_id,
                owner_token=owner_token,
                error=error,
                now=now,
            )
        else:
            raise ValueError(f"未知 delivery 结算状态: {status}")
        db.commit()
        return bool(settled)


async def deliver_chat_delivery(
    *,
    publisher: ChatDeliveryPublisher,
    session_factory: SessionFactory | None = None,
    row_id: int | None = None,
    now: datetime | None = None,
    owner_token: str | None = None,
    lease_seconds: int | float = 60,
    attempt_timeout_seconds: int | float = DEFAULT_ATTEMPT_TIMEOUT_SECONDS,
) -> ChatDeliveryAttemptResult | None:
    """领取并投递一条 outbox，网络调用前后都使用独立短事务。"""

    if not callable(publisher):
        raise TypeError("publisher 必须可调用")
    attempt_timeout = _validate_attempt_window(
        attempt_timeout_seconds=attempt_timeout_seconds,
        lease_seconds=lease_seconds,
    )
    factory = _session_factory_or_default(session_factory)
    owner = str(owner_token or uuid4().hex)
    claimed = await asyncio.to_thread(
        _claim_attempt,
        factory,
        row_id=row_id,
        owner_token=owner,
        now=now,
        lease_seconds=lease_seconds,
    )
    if claimed is None or isinstance(claimed, ChatDeliveryAttemptResult):
        return claimed
    claimed_row_id = claimed.row_id
    target_type = claimed.target_type
    target_id = claimed.target_id
    envelope_json = claimed.envelope_json

    try:
        envelope = decode_chat_delivery_envelope(envelope_json)
    except ValueError as exc:
        status = "failed"
        error = str(exc)
    else:
        try:
            published = await asyncio.wait_for(
                publisher(target_type, target_id, envelope),
                timeout=attempt_timeout,
            )
        except Exception as exc:
            status = "ambiguous"
            error = str(exc) or type(exc).__name__
        else:
            if published is True:
                status = "delivered"
                error = ""
            elif published is False:
                status = "failed"
                error = "publisher 明确返回 False"
            else:
                status = "ambiguous"
                error = "publisher 返回不确定结果"

    settled = await asyncio.to_thread(
        _settle_attempt,
        factory,
        row_id=claimed_row_id,
        owner_token=owner,
        status=status,
        error=error,
        now=now,
    )
    if not settled:
        current = await asyncio.to_thread(
            _existing_result,
            factory,
            claimed_row_id,
        )
        return ChatDeliveryAttemptResult(
            row_id=current.row_id,
            status=current.status,
            attempted=True,
            error="投递结算时已失去 owner",
        )
    return ChatDeliveryAttemptResult(
        row_id=claimed_row_id,
        status=status,
        attempted=True,
        error=error,
    )


async def enqueue_chat_response_delivery(
    *,
    key: InboundClaimKey,
    target_type: str,
    target_id: str,
    envelope: Mapping[str, Any],
    session_factory: SessionFactory | None = None,
    now: datetime | None = None,
) -> ChatDeliveryAttemptResult:
    """幂等持久登记一条 delivery，不执行 publisher。"""

    factory = _session_factory_or_default(session_factory)
    return await asyncio.to_thread(
        _enqueue_delivery,
        factory,
        key=key,
        target_type=target_type,
        target_id=target_id,
        envelope=envelope,
        now=now,
    )


async def enqueue_and_deliver_chat_response(
    *,
    key: InboundClaimKey,
    target_type: str,
    target_id: str,
    envelope: Mapping[str, Any],
    publisher: ChatDeliveryPublisher,
    session_factory: SessionFactory | None = None,
    now: datetime | None = None,
    owner_token: str | None = None,
    lease_seconds: int | float = 60,
    attempt_timeout_seconds: int | float = DEFAULT_ATTEMPT_TIMEOUT_SECONDS,
) -> ChatDeliveryAttemptResult:
    """先持久登记，再立即尝试投递同一条 outbox。"""

    factory = _session_factory_or_default(session_factory)
    registration = await enqueue_chat_response_delivery(
        key=key,
        target_type=target_type,
        target_id=target_id,
        envelope=envelope,
        session_factory=factory,
        now=now,
    )

    result = await deliver_chat_delivery(
        publisher=publisher,
        session_factory=factory,
        row_id=registration.row_id,
        now=now,
        owner_token=owner_token,
        lease_seconds=lease_seconds,
        attempt_timeout_seconds=attempt_timeout_seconds,
    )
    if result is None:
        raise RuntimeError("指定 delivery 未返回处理结果")
    return result

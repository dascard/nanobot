"""聊天断连投递 outbox 的同步状态机。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core.db.models.inbound import ChatDeliveryOutbox
from core.fencing import positive_seconds
from core.inbound_idempotency import InboundClaimKey


BACKOFF_BASE_SECONDS = 30
BACKOFF_MAX_SECONDS = 30 * 60
_TARGET_TYPES = frozenset({"private", "group"})


class ChatDeliveryConflictError(ValueError):
    """同一 claim identity 已存在内容不同的投递。"""


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"非法 JSON 常量: {value}")


def _utc_naive(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if not isinstance(value, datetime):
        raise TypeError("now 必须是 datetime 或 null")
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _canonical_json_object(value: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    if not isinstance(value, Mapping):
        raise TypeError("envelope 必须是 JSON object")
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    decoded = json.loads(encoded, parse_constant=_reject_json_constant)
    if type(decoded) is not dict:
        raise TypeError("envelope 必须是 JSON object")
    return decoded, encoded


def decode_chat_delivery_envelope(payload: Any) -> dict[str, Any]:
    if type(payload) is not str:
        raise ValueError("envelope_json 必须是字符串")
    try:
        decoded = json.loads(
            payload,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("envelope_json 损坏") from exc
    if type(decoded) is not dict:
        raise ValueError("envelope_json 根节点必须是 object")
    _canonical_json_object(decoded)
    return decoded


def _normalize_target(target_type: str, target_id: str) -> tuple[str, str]:
    if type(target_type) is not str:
        raise TypeError("target_type 必须是字符串")
    normalized_type = target_type.strip().lower()
    if normalized_type not in _TARGET_TYPES:
        raise ValueError("target_type 仅支持 private/group")
    if type(target_id) is not str:
        raise TypeError("target_id 必须是字符串")
    normalized_id = target_id.strip()
    if not normalized_id or len(normalized_id) > 255:
        raise ValueError("target_id 必须为 1-255 字符")
    return normalized_type, normalized_id


def chat_delivery_key(key: InboundClaimKey) -> str:
    if type(key) is not InboundClaimKey:
        raise TypeError("key 必须是 InboundClaimKey")
    identity = json.dumps(
        {
            "platform": key.platform,
            "chat_type": key.chat_type,
            "session_id": key.session_id,
            "message_id": key.message_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _assert_same_delivery(
    row: ChatDeliveryOutbox,
    *,
    key: InboundClaimKey,
    delivery_key: str,
    target_type: str,
    target_id: str,
    envelope_json: str,
) -> None:
    actual = (
        row.delivery_key,
        row.platform,
        row.chat_type,
        row.session_id,
        row.message_id,
        row.target_type,
        row.target_id,
        row.envelope_json,
    )
    expected = (
        delivery_key,
        key.platform,
        key.chat_type,
        key.session_id,
        key.message_id,
        target_type,
        target_id,
        envelope_json,
    )
    if actual != expected:
        raise ChatDeliveryConflictError(
            "同一 claim identity 的 delivery payload 不一致"
        )


def enqueue_chat_delivery(
    db: Session,
    *,
    key: InboundClaimKey,
    target_type: str,
    target_id: str,
    envelope: Mapping[str, Any],
    now: datetime | None = None,
) -> ChatDeliveryOutbox:
    """幂等登记 delivery；不提交事务，也不重置已有状态。"""

    if type(key) is not InboundClaimKey:
        raise TypeError("key 必须是 InboundClaimKey")
    normalized_target_type, normalized_target_id = _normalize_target(
        target_type,
        target_id,
    )
    _decoded, envelope_json = _canonical_json_object(envelope)
    delivery_key = chat_delivery_key(key)
    current = _utc_naive(now)
    db.execute(
        sqlite_insert(ChatDeliveryOutbox)
        .values(
            delivery_key=delivery_key,
            platform=key.platform,
            chat_type=key.chat_type,
            session_id=key.session_id,
            message_id=key.message_id,
            target_type=normalized_target_type,
            target_id=normalized_target_id,
            envelope_json=envelope_json,
            status="pending",
            owner_token="",
            lease_expires_at=None,
            attempt_count=0,
            next_attempt_at=current,
            last_error="",
            created_at=current,
            updated_at=current,
            delivered_at=None,
        )
        .on_conflict_do_nothing()
    )
    existing = (
        db.query(ChatDeliveryOutbox)
        .filter(or_(
            ChatDeliveryOutbox.delivery_key == delivery_key,
            (
                (ChatDeliveryOutbox.platform == key.platform)
                & (ChatDeliveryOutbox.chat_type == key.chat_type)
                & (ChatDeliveryOutbox.session_id == key.session_id)
                & (ChatDeliveryOutbox.message_id == key.message_id)
            ),
        ))
        .order_by(ChatDeliveryOutbox.id.asc())
        .all()
    )
    if existing:
        if len(existing) != 1:
            raise ChatDeliveryConflictError(
                "同一 claim identity 存在多个 delivery 候选"
            )
        row = existing[0]
        _assert_same_delivery(
            row,
            key=key,
            delivery_key=delivery_key,
            target_type=normalized_target_type,
            target_id=normalized_target_id,
            envelope_json=envelope_json,
        )
        return row
    raise RuntimeError("delivery outbox 原子登记后未找到记录")


def _normalize_owner_token(owner_token: str) -> str:
    if type(owner_token) is not str:
        raise TypeError("owner_token 必须是字符串")
    normalized = owner_token.strip()
    if not normalized or len(normalized) > 64:
        raise ValueError("owner_token 必须为 1-64 字符")
    return normalized


def _normalize_lease_seconds(value: int | float) -> float:
    try:
        return positive_seconds(value, field_name="lease_seconds")
    except (TypeError, ValueError) as exc:
        raise ValueError("lease_seconds 必须是有限正数") from exc


def _claim_chat_delivery_candidate(
    db: Session,
    *,
    row_id: int,
    previous_status: str,
    owner_token: str,
    current: datetime,
    lease_seconds: int | float,
) -> ChatDeliveryOutbox | None:
    updated = (
        db.query(ChatDeliveryOutbox)
        .filter(
            ChatDeliveryOutbox.id == int(row_id),
            ChatDeliveryOutbox.status == previous_status,
            or_(
                ChatDeliveryOutbox.next_attempt_at.is_(None),
                ChatDeliveryOutbox.next_attempt_at <= current,
            ),
        )
        .update(
            {
                ChatDeliveryOutbox.status: "sending",
                ChatDeliveryOutbox.owner_token: owner_token,
                ChatDeliveryOutbox.lease_expires_at: current
                + timedelta(seconds=float(lease_seconds)),
                ChatDeliveryOutbox.attempt_count: (
                    ChatDeliveryOutbox.attempt_count + 1
                ),
                ChatDeliveryOutbox.updated_at: current,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        db.expire_all()
        return None
    db.flush()
    db.expire_all()
    return db.get(ChatDeliveryOutbox, int(row_id))


def claim_chat_delivery(
    db: Session,
    *,
    row_id: int,
    owner_token: str,
    now: datetime | None = None,
    lease_seconds: int | float = 60,
) -> ChatDeliveryOutbox | None:
    """按 id 条件领取到期 delivery；调用方负责提交。"""

    owner = _normalize_owner_token(owner_token)
    lease_seconds = _normalize_lease_seconds(lease_seconds)
    current = _utc_naive(now)
    candidate = db.get(ChatDeliveryOutbox, int(row_id))
    if (
        candidate is None
        or candidate.status not in {"pending", "ambiguous"}
        or (
            candidate.next_attempt_at is not None
            and candidate.next_attempt_at > current
        )
    ):
        return None
    return _claim_chat_delivery_candidate(
        db,
        row_id=int(candidate.id),
        previous_status=str(candidate.status),
        owner_token=owner,
        current=current,
        lease_seconds=lease_seconds,
    )


def claim_due_chat_delivery(
    db: Session,
    *,
    owner_token: str,
    now: datetime | None = None,
    lease_seconds: int | float = 60,
) -> ChatDeliveryOutbox | None:
    """条件领取一条到期 delivery；调用方负责提交。"""

    owner = _normalize_owner_token(owner_token)
    lease_seconds = _normalize_lease_seconds(lease_seconds)
    current = _utc_naive(now)
    candidate = (
        db.query(ChatDeliveryOutbox)
        .filter(ChatDeliveryOutbox.status.in_(("pending", "ambiguous")))
        .filter(or_(
            ChatDeliveryOutbox.next_attempt_at.is_(None),
            ChatDeliveryOutbox.next_attempt_at <= current,
        ))
        .order_by(
            ChatDeliveryOutbox.next_attempt_at.asc(),
            ChatDeliveryOutbox.id.asc(),
        )
        .first()
    )
    if candidate is None:
        return None
    return _claim_chat_delivery_candidate(
        db,
        row_id=int(candidate.id),
        previous_status=str(candidate.status),
        owner_token=owner,
        current=current,
        lease_seconds=lease_seconds,
    )


def _settle_chat_delivery(
    db: Session,
    *,
    row_id: int,
    owner_token: str,
    values: dict[Any, Any],
) -> bool:
    owner = _normalize_owner_token(owner_token)
    updated = (
        db.query(ChatDeliveryOutbox)
        .filter(
            ChatDeliveryOutbox.id == int(row_id),
            ChatDeliveryOutbox.status == "sending",
            ChatDeliveryOutbox.owner_token == owner,
        )
        .update(values, synchronize_session=False)
    )
    db.flush()
    db.expire_all()
    return updated == 1


def mark_chat_delivery_delivered(
    db: Session,
    *,
    row_id: int,
    owner_token: str,
    now: datetime | None = None,
) -> bool:
    current = _utc_naive(now)
    return _settle_chat_delivery(
        db,
        row_id=row_id,
        owner_token=owner_token,
        values={
            ChatDeliveryOutbox.status: "delivered",
            ChatDeliveryOutbox.owner_token: "",
            ChatDeliveryOutbox.lease_expires_at: None,
            ChatDeliveryOutbox.next_attempt_at: None,
            ChatDeliveryOutbox.last_error: "",
            ChatDeliveryOutbox.updated_at: current,
            ChatDeliveryOutbox.delivered_at: current,
        },
    )


def mark_chat_delivery_failed(
    db: Session,
    *,
    row_id: int,
    owner_token: str,
    error: Any,
    now: datetime | None = None,
) -> bool:
    current = _utc_naive(now)
    return _settle_chat_delivery(
        db,
        row_id=row_id,
        owner_token=owner_token,
        values={
            ChatDeliveryOutbox.status: "failed",
            ChatDeliveryOutbox.owner_token: "",
            ChatDeliveryOutbox.lease_expires_at: None,
            ChatDeliveryOutbox.next_attempt_at: None,
            ChatDeliveryOutbox.last_error: str(error or "")[:1000],
            ChatDeliveryOutbox.updated_at: current,
            ChatDeliveryOutbox.delivered_at: None,
        },
    )


def _backoff_seconds(attempt_count: int) -> int:
    exponent = max(0, int(attempt_count) - 1)
    return min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2 ** exponent))


def recover_stale_chat_deliveries(
    db: Session,
    *,
    now: datetime | None = None,
) -> int:
    """将租约已过期的 sending 转为可重试 ambiguous。"""

    current = _utc_naive(now)
    candidates = (
        db.query(
            ChatDeliveryOutbox.id,
            ChatDeliveryOutbox.owner_token,
            ChatDeliveryOutbox.lease_expires_at,
            ChatDeliveryOutbox.attempt_count,
        )
        .filter(
            ChatDeliveryOutbox.status == "sending",
            ChatDeliveryOutbox.lease_expires_at.is_not(None),
            ChatDeliveryOutbox.lease_expires_at <= current,
        )
        .order_by(ChatDeliveryOutbox.id.asc())
        .all()
    )
    recovered = 0
    for row_id, owner_token, lease_expires_at, attempt_count in candidates:
        updated = (
            db.query(ChatDeliveryOutbox)
            .filter(
                ChatDeliveryOutbox.id == int(row_id),
                ChatDeliveryOutbox.status == "sending",
                ChatDeliveryOutbox.owner_token == str(owner_token),
                ChatDeliveryOutbox.lease_expires_at == lease_expires_at,
                ChatDeliveryOutbox.lease_expires_at <= current,
            )
            .update(
                {
                    ChatDeliveryOutbox.status: "ambiguous",
                    ChatDeliveryOutbox.owner_token: "",
                    ChatDeliveryOutbox.lease_expires_at: None,
                    ChatDeliveryOutbox.next_attempt_at: current
                    + timedelta(seconds=_backoff_seconds(attempt_count)),
                    ChatDeliveryOutbox.last_error: "投递租约过期，结果不确定",
                    ChatDeliveryOutbox.updated_at: current,
                    ChatDeliveryOutbox.delivered_at: None,
                },
                synchronize_session=False,
            )
        )
        recovered += int(updated)
    db.flush()
    db.expire_all()
    return recovered


def mark_chat_delivery_ambiguous(
    db: Session,
    *,
    row_id: int,
    owner_token: str,
    error: Any,
    now: datetime | None = None,
) -> bool:
    owner = _normalize_owner_token(owner_token)
    current = _utc_naive(now)
    row = (
        db.query(ChatDeliveryOutbox)
        .filter(
            ChatDeliveryOutbox.id == int(row_id),
            ChatDeliveryOutbox.status == "sending",
            ChatDeliveryOutbox.owner_token == owner,
        )
        .first()
    )
    if row is None:
        return False
    return _settle_chat_delivery(
        db,
        row_id=row_id,
        owner_token=owner,
        values={
            ChatDeliveryOutbox.status: "ambiguous",
            ChatDeliveryOutbox.owner_token: "",
            ChatDeliveryOutbox.lease_expires_at: None,
            ChatDeliveryOutbox.next_attempt_at: current
            + timedelta(seconds=_backoff_seconds(row.attempt_count)),
            ChatDeliveryOutbox.last_error: str(error or "")[:1000],
            ChatDeliveryOutbox.updated_at: current,
            ChatDeliveryOutbox.delivered_at: None,
        },
    )

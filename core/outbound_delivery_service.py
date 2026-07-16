"""独立出站 worker 的事务编排与 QQ payload 合同。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import urlsplit

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.outbound_delivery import (
    cancel_invalid_delivery_before_send,
    DeliveryClaimHandle,
    DeliverySettlementResult,
    claim_due_outbox,
    claim_legacy_direct_outbox,
    expire_stale_delivery_leases,
    mark_delivery_request_started,
    settle_delivery_attempt,
)
from core.outbound_transport import DeliveryOutcome, resolve_qq_push_token
from core.qq_outbound_renderer import render_qq_outbound_envelope


QQ_ENDPOINT_KEY = "qq_push"
QQ_PAYLOAD_CONTRACT = "qq-envelope-v1"
DEFAULT_PUSH_URL = "http://172.17.0.1:8082/nanobot/push"
DEFAULT_PUSH_TIMEOUT_SECONDS = 180.0
DEFAULT_BATCH_SIZE = 20
DEFAULT_LEASE_SECONDS = 240.0
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
DELIVERY_LEASE_SETTLEMENT_MARGIN_SECONDS = 30.0
SETTLEMENT_MAX_ATTEMPTS = 3
SETTLEMENT_RETRY_BASE_SECONDS = 0.05
NETWORK_RETRY_BASE_SECONDS = 1.0
NETWORK_RETRY_CAP_SECONDS = 300.0
MIN_RETRY_DELAY_SECONDS = 0.001


class OutboundTransport(Protocol):
    async def __call__(
        self,
        request: "OutboundTransportRequest",
    ) -> DeliveryOutcome: ...


class DeliveryContractError(ValueError):
    """持久化目标或 payload 不符合显式出口合同。"""


@dataclass(frozen=True, slots=True)
class OutboundWorkerConfig:
    """独立 worker 的进程级只读配置。"""

    push_url: str = field(repr=False)
    push_token: str = field(repr=False)
    push_timeout_seconds: float
    endpoint_config_revision: str
    batch_size: int = DEFAULT_BATCH_SIZE
    lease_seconds: float = DEFAULT_LEASE_SECONDS
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        push_url = str(self.push_url or "").strip()
        parsed = urlsplit(push_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("QQBOT_PUSH_URL 必须是有效的 HTTP(S) URL")
        object.__setattr__(self, "push_url", push_url)

        push_token = resolve_qq_push_token(
            {"NANOBOT_PUSH_TOKEN": self.push_token}
        )
        object.__setattr__(self, "push_token", push_token)

        revision = str(self.endpoint_config_revision or "").strip()
        if not revision or len(revision) > 128:
            raise ValueError("NANOBOT_QQ_PUSH_CONFIG_REVISION 必须是非空短文本")
        object.__setattr__(self, "endpoint_config_revision", revision)

        _require_positive_number(
            self.push_timeout_seconds,
            name="QQBOT_PUSH_TIMEOUT",
        )
        _require_positive_integer(
            self.batch_size,
            name="NANOBOT_OUTBOUND_BATCH_SIZE",
        )
        _require_positive_number(
            self.lease_seconds,
            name="NANOBOT_OUTBOUND_LEASE_SECONDS",
        )
        _require_positive_number(
            self.poll_interval_seconds,
            name="NANOBOT_OUTBOUND_POLL_INTERVAL",
        )
        minimum_lease = (
            float(self.push_timeout_seconds)
            + DELIVERY_LEASE_SETTLEMENT_MARGIN_SECONDS
        )
        if float(self.lease_seconds) <= minimum_lease:
            raise ValueError(
                "NANOBOT_OUTBOUND_LEASE_SECONDS 必须大于 QQBOT_PUSH_TIMEOUT "
                "并预留结算时间"
            )

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "OutboundWorkerConfig":
        source = os.environ if environ is None else environ
        return cls(
            push_url=source.get("QQBOT_PUSH_URL", DEFAULT_PUSH_URL),
            push_token=resolve_qq_push_token(source),
            push_timeout_seconds=_env_float(
                source,
                "QQBOT_PUSH_TIMEOUT",
                DEFAULT_PUSH_TIMEOUT_SECONDS,
            ),
            endpoint_config_revision=source.get(
                "NANOBOT_QQ_PUSH_CONFIG_REVISION",
                "1",
            ),
            batch_size=_env_int(
                source,
                "NANOBOT_OUTBOUND_BATCH_SIZE",
                DEFAULT_BATCH_SIZE,
            ),
            lease_seconds=_env_float(
                source,
                "NANOBOT_OUTBOUND_LEASE_SECONDS",
                DEFAULT_LEASE_SECONDS,
            ),
            poll_interval_seconds=_env_float(
                source,
                "NANOBOT_OUTBOUND_POLL_INTERVAL",
                DEFAULT_POLL_INTERVAL_SECONDS,
            ),
        )


@dataclass(frozen=True, slots=True)
class OutboundTransportRequest:
    push_url: str = field(repr=False)
    target_type: str
    target_id: str = field(repr=False)
    message: str = field(repr=False)
    timeout_seconds: float
    payload_sha256: str
    outbox_id: int
    attempt_no: int
    now: datetime


@dataclass(frozen=True, slots=True)
class OutboundDeliveryWorkResult:
    outbox_id: int
    attempt_id: int
    attempt_no: int
    payload_sha256: str
    outbox_status: str
    run_status: str


@dataclass(frozen=True, slots=True)
class _DecodedDelivery:
    target_type: str
    target_id: str = field(repr=False)
    message: str = field(repr=False)


def _require_positive_number(value: Any, *, name: str) -> float:
    if type(value) not in {int, float}:
        raise ValueError(f"{name} 必须是有限正数")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{name} 必须是有限正数")
    return normalized


def _require_positive_integer(value: Any, *, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} 必须是正整数")
    return value


def _env_float(
    environ: Mapping[str, str],
    name: str,
    default: float,
) -> float:
    try:
        return float(environ.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是有限正数") from exc


def _env_int(
    environ: Mapping[str, str],
    name: str,
    default: int,
) -> int:
    try:
        return int(environ.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是正整数") from exc


def _utc_naive(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current
    return current.astimezone(timezone.utc).replace(tzinfo=None)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse_canonical_object(raw: str, *, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise DeliveryContractError(f"{label} 不是有效 JSON object") from exc
    if not isinstance(parsed, dict):
        raise DeliveryContractError(f"{label} 必须是 JSON object")
    try:
        canonical = _canonical_json(parsed)
    except (TypeError, ValueError, RecursionError) as exc:
        raise DeliveryContractError(f"{label} 不能规范化") from exc
    if canonical != raw:
        raise DeliveryContractError(f"{label} 不是 canonical JSON")
    return parsed


def _nonempty_text(value: Any, *, label: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise DeliveryContractError(f"{label} 必须是字符串")
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise DeliveryContractError(f"{label} 长度非法")
    return normalized


def _decode_delivery(claim: DeliveryClaimHandle) -> _DecodedDelivery:
    if claim.endpoint_key != QQ_ENDPOINT_KEY:
        raise DeliveryContractError("endpoint_key 与 worker 不匹配")
    if claim.payload_contract_fingerprint != QQ_PAYLOAD_CONTRACT:
        raise DeliveryContractError("payload contract 版本不受支持")
    digest = hashlib.sha256(claim.payload_json.encode("utf-8")).hexdigest()
    if digest != claim.payload_sha256:
        raise DeliveryContractError("payload 完整性校验失败")

    destination = _parse_canonical_object(
        claim.destination_snapshot_json,
        label="destination snapshot",
    )
    target_type = _nonempty_text(
        destination.get("target_type"),
        label="target_type",
        max_length=16,
    )
    if target_type not in {"private", "group"}:
        raise DeliveryContractError("target_type 不受 QQ 出口支持")
    if target_type != claim.target_type:
        raise DeliveryContractError("target_type 快照不一致")
    target_id = _nonempty_text(
        destination.get("target_id"),
        label="target_id",
        max_length=128,
    )

    envelope = _parse_canonical_object(claim.payload_json, label="payload")
    has_messages = isinstance(envelope.get("messages"), list)
    has_reply = isinstance(envelope.get("reply"), str)
    if not has_messages and not has_reply:
        raise DeliveryContractError("QQ envelope 缺少 messages/reply")
    rendered = render_qq_outbound_envelope(envelope, allow_base64=False)
    if not rendered.message.strip():
        raise DeliveryContractError("QQ envelope 渲染结果为空")
    return _DecodedDelivery(
        target_type=target_type,
        target_id=target_id,
        message=rendered.message,
    )


def _run_transaction(
    session_factory: Callable[[], Session],
    operation: Callable[[Session], Any],
) -> Any:
    db = session_factory()
    try:
        result = operation(db)
        db.commit()
        return result
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


async def _transaction(
    session_factory: Callable[[], Session],
    operation: Callable[[Session], Any],
) -> Any:
    return await asyncio.to_thread(_run_transaction, session_factory, operation)


def _settlement_outcome(category: str) -> str:
    if category == "success":
        return "succeeded"
    if category == "transient":
        return "transient_failure"
    if category == "ambiguous":
        return "ambiguous"
    if category in {
        "endpoint",
        "destination",
        "payload",
        "payload_contract",
    }:
        return "permanent_failure"
    return "ambiguous"


def _retry_at(
    *,
    now: datetime,
    request_started_count: int,
    retry_after_seconds: int | None,
    jitter: Callable[[float], float],
) -> datetime:
    if retry_after_seconds is not None:
        delay = min(
            NETWORK_RETRY_CAP_SECONDS,
            max(0.0, float(retry_after_seconds)),
        )
    else:
        exponent = min(16, max(0, int(request_started_count) - 1))
        maximum = min(
            NETWORK_RETRY_CAP_SECONDS,
            NETWORK_RETRY_BASE_SECONDS * (2**exponent),
        )
        sampled = float(jitter(maximum))
        if not math.isfinite(sampled):
            sampled = maximum
        delay = min(maximum, max(0.0, sampled))
    return now + timedelta(seconds=max(MIN_RETRY_DELAY_SECONDS, delay))


async def _settle_with_retry(
    *,
    session_factory: Callable[[], Session],
    command: dict[str, Any],
    retry_sleep: Callable[[float], Awaitable[None]],
) -> DeliverySettlementResult:
    for index in range(SETTLEMENT_MAX_ATTEMPTS):
        try:
            return await _transaction(
                session_factory,
                lambda db: settle_delivery_attempt(db, **command),
            )
        except SQLAlchemyError:
            if index + 1 >= SETTLEMENT_MAX_ATTEMPTS:
                raise
            await retry_sleep(SETTLEMENT_RETRY_BASE_SECONDS * (2**index))
    raise AssertionError("unreachable")


def _work_result(
    claim: DeliveryClaimHandle,
    settlement: DeliverySettlementResult,
) -> OutboundDeliveryWorkResult:
    return OutboundDeliveryWorkResult(
        outbox_id=claim.outbox_id,
        attempt_id=claim.attempt_id,
        attempt_no=claim.attempt_no,
        payload_sha256=claim.payload_sha256,
        outbox_status=settlement.outbox_status,
        run_status=settlement.run_status,
    )


async def _deliver_claim(
    *,
    claim: DeliveryClaimHandle,
    session_factory: Callable[[], Session],
    transport: OutboundTransport,
    config: OutboundWorkerConfig,
    current: datetime,
    completion_now_is_fixed: bool,
    clock_source: Callable[[], datetime],
    jitter_source: Callable[[float], float],
    settlement_retry_sleep: Callable[[float], Awaitable[None]],
) -> OutboundDeliveryWorkResult:
    preflight = await _transaction(
        session_factory,
        lambda db: cancel_invalid_delivery_before_send(
            db,
            outbox_id=claim.outbox_id,
            attempt_id=claim.attempt_id,
            worker_owner=claim.worker_owner,
            lease_token=claim.lease_token,
            now=current,
        ),
    )
    if preflight is not None:
        return _work_result(claim, preflight)
    try:
        decoded = _decode_delivery(claim)
    except DeliveryContractError:
        settlement = await _settle_with_retry(
            session_factory=session_factory,
            command={
                "outbox_id": claim.outbox_id,
                "attempt_id": claim.attempt_id,
                "worker_owner": claim.worker_owner,
                "lease_token": claim.lease_token,
                "outcome": "permanent_failure",
                "transport_phase": "allocated",
                "http_status": None,
                "result_category": "payload_contract",
                "error_type": "delivery_contract_invalid",
                "safe_summary": "持久化投递合同无效",
                "duration_ms": None,
                "retry_at": None,
                "circuit_scope_type": None,
                "now": current,
            },
            retry_sleep=settlement_retry_sleep,
        )
        return _work_result(claim, settlement)

    def start_request_if_source_is_valid(db: Session):
        cancelled = cancel_invalid_delivery_before_send(
            db,
            outbox_id=claim.outbox_id,
            attempt_id=claim.attempt_id,
            worker_owner=claim.worker_owner,
            lease_token=claim.lease_token,
            now=current,
        )
        if cancelled is not None:
            return cancelled
        return mark_delivery_request_started(
            db,
            outbox_id=claim.outbox_id,
            attempt_id=claim.attempt_id,
            worker_owner=claim.worker_owner,
            lease_token=claim.lease_token,
            now=current,
        )

    request_started = await _transaction(
        session_factory,
        start_request_if_source_is_valid,
    )
    if isinstance(request_started, DeliverySettlementResult):
        return _work_result(claim, request_started)
    request = OutboundTransportRequest(
        push_url=config.push_url,
        target_type=decoded.target_type,
        target_id=decoded.target_id,
        message=decoded.message,
        timeout_seconds=float(config.push_timeout_seconds),
        payload_sha256=claim.payload_sha256,
        outbox_id=claim.outbox_id,
        attempt_no=claim.attempt_no,
        now=current,
    )
    try:
        delivery_outcome = await transport(request)
    except Exception:
        delivery_outcome = DeliveryOutcome(
            category="ambiguous",
            error_type="transport_wrapper_error",
            status_code=None,
            retry_after_seconds=None,
            duration_ms=0,
            safe_summary="出站传输封装失败",
            transport_phase="write",
        )
    completed_at = _utc_naive(
        current if completion_now_is_fixed else clock_source()
    )
    retry_at = None
    if delivery_outcome.category == "transient":
        retry_at = _retry_at(
            now=completed_at,
            request_started_count=request_started.request_started_count,
            retry_after_seconds=delivery_outcome.retry_after_seconds,
            jitter=jitter_source,
        )
    settlement = await _settle_with_retry(
        session_factory=session_factory,
        command={
            "outbox_id": claim.outbox_id,
            "attempt_id": claim.attempt_id,
            "worker_owner": claim.worker_owner,
            "lease_token": claim.lease_token,
            "outcome": _settlement_outcome(delivery_outcome.category),
            "transport_phase": delivery_outcome.transport_phase,
            "http_status": delivery_outcome.status_code,
            "result_category": delivery_outcome.category,
            "error_type": delivery_outcome.error_type,
            "safe_summary": delivery_outcome.safe_summary,
            "duration_ms": delivery_outcome.duration_ms,
            "retry_at": retry_at,
            "circuit_scope_type": None,
            "now": completed_at,
        },
        retry_sleep=settlement_retry_sleep,
    )
    return _work_result(claim, settlement)


async def deliver_outbound_once(
    *,
    session_factory: Callable[[], Session],
    transport: OutboundTransport,
    config: OutboundWorkerConfig,
    worker_owner: str,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
    jitter: Callable[[float], float] | None = None,
    settlement_retry_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> OutboundDeliveryWorkResult | None:
    """恢复过期租约并投递一条到期 QQ outbox。"""

    owner = str(worker_owner or "").strip()
    if not owner or len(owner) > 128:
        raise ValueError("worker_owner 必须是 1-128 字符")
    clock_source = clock or (lambda: datetime.now(timezone.utc))
    current = _utc_naive(now if now is not None else clock_source())
    jitter_source = jitter or (lambda maximum: random.uniform(0.0, maximum))

    await _transaction(
        session_factory,
        lambda db: expire_stale_delivery_leases(
            db,
            endpoint_key=QQ_ENDPOINT_KEY,
            now=current,
        ),
    )
    claim = await _transaction(
        session_factory,
        lambda db: claim_due_outbox(
            db,
            worker_owner=owner,
            lease_seconds=config.lease_seconds,
            endpoint_config_revision=config.endpoint_config_revision,
            endpoint_key=QQ_ENDPOINT_KEY,
            now=current,
        ),
    )
    if claim is None:
        return None
    return await _deliver_claim(
        claim=claim,
        session_factory=session_factory,
        transport=transport,
        config=config,
        current=current,
        completion_now_is_fixed=now is not None and clock is None,
        clock_source=clock_source,
        jitter_source=jitter_source,
        settlement_retry_sleep=settlement_retry_sleep,
    )


async def deliver_legacy_outbound_once(
    *,
    session_factory: Callable[[], Session],
    transport: OutboundTransport,
    config: OutboundWorkerConfig,
    outbox_id: int,
    worker_owner: str,
    writer_owner: str,
    writer_token: str,
    writer_protocol_version: int,
    writer_lease_seconds: float,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
    jitter: Callable[[float], float] | None = None,
    settlement_retry_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> OutboundDeliveryWorkResult | None:
    """领取并投递一条指定 legacy leaf；独立 worker 不会走此入口。"""

    owner = str(worker_owner or "").strip()
    if not owner or len(owner) > 128:
        raise ValueError("worker_owner 必须是 1-128 字符")
    clock_source = clock or (lambda: datetime.now(timezone.utc))
    current = _utc_naive(now if now is not None else clock_source())
    jitter_source = jitter or (lambda maximum: random.uniform(0.0, maximum))
    await _transaction(
        session_factory,
        lambda db: expire_stale_delivery_leases(
            db,
            endpoint_key=QQ_ENDPOINT_KEY,
            now=current,
        ),
    )
    claim = await _transaction(
        session_factory,
        lambda db: claim_legacy_direct_outbox(
            db,
            outbox_id=outbox_id,
            worker_owner=owner,
            lease_seconds=config.lease_seconds,
            writer_owner=writer_owner,
            writer_token=writer_token,
            writer_protocol_version=writer_protocol_version,
            writer_lease_seconds=writer_lease_seconds,
            endpoint_key=QQ_ENDPOINT_KEY,
            endpoint_config_revision=config.endpoint_config_revision,
            now=current,
        ),
    )
    if claim is None:
        return None
    return await _deliver_claim(
        claim=claim,
        session_factory=session_factory,
        transport=transport,
        config=config,
        current=current,
        completion_now_is_fixed=now is not None and clock is None,
        clock_source=clock_source,
        jitter_source=jitter_source,
        settlement_retry_sleep=settlement_retry_sleep,
    )

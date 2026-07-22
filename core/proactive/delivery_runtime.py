"""主动外呼 legacy-direct 投递兼容通道。"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.database import SessionLocal
from core.db.models.outbound import OutboundDeliveryOutbox, OutboundRun
from core.outbound import service as outbound_service
from core.outbound_delivery_service import (
    CustomTransportConfig,
    LegacyWriterLeaseSnapshot,
    LegacyWriterTakeover,
    OutboundDeliveryWorkResult,
    OutboundTransport,
    OutboundWorkerConfig,
    deliver_legacy_outbound_once,
    snapshot_live_legacy_writer,
)
from core.proactive.config import (
    OUTREACH_ENDPOINT_KEY,
    OUTREACH_SOURCE_TYPE,
    OUTREACH_WRITER_LEASE_SECONDS,
)
from core.proactive.runtime_identity import get_proactive_process_identity
from core.proactive.runtime_support import (
    _legacy_delivery_outcome,
    _outreach_session_factory,
    _utc_naive,
)


logger = logging.getLogger("nanobot.proactive.delivery_runtime")


async def _deliver_legacy_outreach_leaf(
    *,
    session: Session,
    outbox_id: int,
    publisher: Callable[[str, str, str], Any],
    endpoint_config_revision: str,
    now: datetime | None,
) -> None:
    process_identity = get_proactive_process_identity()
    execution_config = CustomTransportConfig(
        endpoint_config_revision=endpoint_config_revision,
    )

    async def deliver(transport) -> None:
        await deliver_legacy_outbound_once(
            session_factory=_outreach_session_factory(session),
            transport=transport,
            config=execution_config,
            outbox_id=outbox_id,
            worker_owner=process_identity.owner,
            writer_owner=process_identity.owner,
            writer_token=process_identity.writer_token,
            writer_protocol_version=outbound_service.OUTBOUND_PROTOCOL_VERSION,
            writer_lease_seconds=OUTREACH_WRITER_LEASE_SECONDS,
            now=now,
        )

    async def legacy_transport(request):
        result = publisher(
            request.target_type,
            request.target_id,
            request.message,
        )
        if inspect.isawaitable(result):
            result = await result
        return _legacy_delivery_outcome(result)

    await deliver(legacy_transport)


async def drain_due_legacy_proactive_outboxes(
    *,
    session_factory: Callable[[], Session] | None = None,
    worker_config: OutboundWorkerConfig | None = None,
    transport: OutboundTransport | None = None,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
    jitter: Callable[[float], float] | None = None,
    worker_owner: str | None = None,
    stop_event: Any | None = None,
    takeover_writer: LegacyWriterTakeover | None = None,
    limit: int | None = None,
) -> list[OutboundDeliveryWorkResult]:
    """恢复主动外呼中安全到期的 legacy leaf。"""

    if now is not None and clock is not None:
        raise ValueError("now 与 clock 不能同时提供")
    factory = session_factory or SessionLocal
    resolved_worker = worker_config or OutboundWorkerConfig.from_env()
    batch_limit = resolved_worker.batch_size if limit is None else limit
    if type(batch_limit) is not int or not 1 <= batch_limit <= 1000:
        raise ValueError("limit 必须是 1-1000 的整数")
    clock_source = clock or (lambda: datetime.now(timezone.utc))
    current = _utc_naive(now if now is not None else clock_source())
    discovery = factory()
    writer_snapshot: LegacyWriterLeaseSnapshot | None = None
    try:
        outbound_service.terminalize_expired_outboxes(
            discovery,
            endpoint_key=OUTREACH_ENDPOINT_KEY,
            source_type=OUTREACH_SOURCE_TYPE,
            delivery_mode="legacy_direct",
            now=current,
        )
        discovery.commit()
        writer_snapshot = snapshot_live_legacy_writer(
            discovery,
            source_type=OUTREACH_SOURCE_TYPE,
            now=current,
        )
        outbox_ids = [
            int(row[0])
            for row in (
                discovery.query(OutboundDeliveryOutbox.id)
                .join(
                    OutboundRun,
                    OutboundRun.id == OutboundDeliveryOutbox.run_id,
                )
                .filter(
                    OutboundRun.source_type == OUTREACH_SOURCE_TYPE,
                    OutboundRun.delivery_mode == "legacy_direct",
                    OutboundRun.active_outbox_id == OutboundDeliveryOutbox.id,
                    OutboundDeliveryOutbox.endpoint_key == OUTREACH_ENDPOINT_KEY,
                    OutboundDeliveryOutbox.status.in_((
                        "pending",
                        "retry_wait",
                        "blocked",
                    )),
                    OutboundDeliveryOutbox.request_started_count
                    < OutboundDeliveryOutbox.max_attempts,
                    OutboundDeliveryOutbox.retry_deadline_at > current,
                    or_(
                        OutboundDeliveryOutbox.status.in_((
                            "pending",
                            "blocked",
                        )),
                        (
                            (
                                OutboundDeliveryOutbox.status == "retry_wait"
                            )
                            & (
                                OutboundDeliveryOutbox.next_attempt_at <= current
                            )
                        ),
                    ),
                )
                .order_by(
                    OutboundDeliveryOutbox.next_attempt_at.asc(),
                    OutboundDeliveryOutbox.id.asc(),
                )
                .limit(batch_limit)
                .all()
            )
        ]
    except Exception:
        discovery.rollback()
        raise
    finally:
        discovery.close()

    if writer_snapshot is not None:
        writer_owner = writer_snapshot.writer_owner
        writer_token = writer_snapshot.writer_token
        writer_protocol_version = writer_snapshot.protocol_version
        expected_writer_version = writer_snapshot.writer_version
    elif takeover_writer is not None:
        writer_owner = takeover_writer.writer_owner
        writer_token = takeover_writer.writer_token
        writer_protocol_version = outbound_service.OUTBOUND_PROTOCOL_VERSION
        expected_writer_version = None
    else:
        return []
    delivery_owner = str(worker_owner or writer_owner).strip()

    async def deliver_batch(
        resolved_transport: OutboundTransport,
    ) -> list[OutboundDeliveryWorkResult]:
        results: list[OutboundDeliveryWorkResult] = []
        for outbox_id in outbox_ids:
            if stop_event is not None and stop_event.is_set():
                break
            try:
                result = await deliver_legacy_outbound_once(
                    session_factory=factory,
                    transport=resolved_transport,
                    config=resolved_worker,
                    outbox_id=outbox_id,
                    worker_owner=delivery_owner,
                    writer_owner=writer_owner,
                    writer_token=writer_token,
                    writer_protocol_version=writer_protocol_version,
                    writer_lease_seconds=OUTREACH_WRITER_LEASE_SECONDS,
                    expected_writer_version=expected_writer_version,
                    now=now,
                    clock=clock,
                    jitter=jitter,
                )
            except Exception as exc:
                logger.error(
                    "主动外呼 legacy leaf 投递失败 outbox_id=%s error_type=%s",
                    outbox_id,
                    type(exc).__name__,
                )
                continue
            if result is not None:
                results.append(result)
        return results

    if transport is not None:
        return await deliver_batch(transport)
    if not outbox_ids:
        return []

    import aiohttp

    from core.outbound_transport import deliver_qq_push_with_session

    async with aiohttp.ClientSession() as http_session:
        async def qq_transport(request):
            return await deliver_qq_push_with_session(
                http_session,
                push_url=request.push_url,
                push_token=resolved_worker.push_token,
                target_type=request.target_type,
                target_id=request.target_id,
                message=request.message,
                timeout_seconds=request.timeout_seconds,
            )

        return await deliver_batch(qq_transport)



__all__ = ["drain_due_legacy_proactive_outboxes"]

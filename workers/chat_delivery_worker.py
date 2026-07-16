"""聊天断连持久投递 worker。"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import uuid4

import aiohttp
from sqlalchemy.orm import Session

from core.chat_delivery_service import (
    ChatDeliveryPublisher,
    deliver_chat_delivery,
)


logger = logging.getLogger("nanobot.chat_delivery.worker")
DEFAULT_BATCH_SIZE = 20
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
_schema_ready = False


def _ensure_schema_ready() -> None:
    global _schema_ready
    if _schema_ready:
        return
    from core.database import init_db

    init_db()
    _schema_ready = True


def _session_factory_or_default(
    session_factory: Callable[[], Session] | None,
) -> Callable[[], Session]:
    if session_factory is not None:
        return session_factory
    _ensure_schema_ready()
    from core.database import SessionLocal

    return SessionLocal


@asynccontextmanager
async def _publisher_scope(
    publisher: ChatDeliveryPublisher | None,
) -> AsyncIterator[ChatDeliveryPublisher]:
    if publisher is not None:
        yield publisher
        return

    from core.daily_digest import push_envelope_to_qq_outcome_with_session
    from core.outbound_transport import delivery_outcome_to_legacy

    async with aiohttp.ClientSession() as session:
        async def worker_publisher(
            target_type: str,
            target_id: str,
            envelope: dict[str, Any],
        ) -> bool | None:
            outcome = await push_envelope_to_qq_outcome_with_session(
                session,
                target_type,
                target_id,
                envelope,
            )
            if outcome is None:
                return False
            return delivery_outcome_to_legacy(outcome)

        yield worker_publisher


async def _run_once_with_publisher(
    *,
    publisher: ChatDeliveryPublisher,
    session_factory: Callable[[], Session],
    owner: str,
    now: datetime | None,
    limit: int,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, int]:
    stats = {
        "processed": 0,
        "delivered": 0,
        "failed": 0,
        "ambiguous": 0,
    }
    while stats["processed"] < limit:
        if should_stop is not None and should_stop():
            break
        owner_prefix = str(owner or "chat-delivery-worker").strip()
        owner_token = f"{owner_prefix[:31]}:{uuid4().hex}"
        result = await deliver_chat_delivery(
            publisher=publisher,
            session_factory=session_factory,
            now=now,
            owner_token=owner_token,
        )
        if result is None:
            break
        stats["processed"] += 1
        if result.status in {"delivered", "failed", "ambiguous"}:
            stats[result.status] += 1
        else:
            logger.warning(
                "Chat delivery settlement returned unexpected status: row=%s status=%s",
                result.row_id,
                result.status,
            )
    return stats


async def run_once_async(
    *,
    publisher: ChatDeliveryPublisher | None = None,
    session_factory: Callable[[], Session] | None = None,
    owner: str = "chat-delivery-worker",
    now: datetime | None = None,
    limit: int = DEFAULT_BATCH_SIZE,
) -> dict[str, int]:
    """处理一轮到期投递；注入 session factory 时由调用方保证 schema。"""

    factory = _session_factory_or_default(session_factory)
    batch_size = max(1, int(limit))
    async with _publisher_scope(publisher) as resolved_publisher:
        return await _run_once_with_publisher(
            publisher=resolved_publisher,
            session_factory=factory,
            owner=owner,
            now=now,
            limit=batch_size,
        )


async def run_forever_async(
    stop_event: Any,
    *,
    publisher: ChatDeliveryPublisher | None = None,
    session_factory: Callable[[], Session] | None = None,
    owner: str = "chat-delivery-worker",
    interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    limit: int = DEFAULT_BATCH_SIZE,
) -> None:
    """轮询 outbox；收到停止信号后不再领取新任务。"""

    factory = _session_factory_or_default(session_factory)
    poll_interval = max(0.05, float(interval))
    batch_size = max(1, int(limit))
    logger.info("Chat delivery worker started interval=%ss", poll_interval)
    async with _publisher_scope(publisher) as resolved_publisher:
        while not stop_event.is_set():
            try:
                stats = await _run_once_with_publisher(
                    publisher=resolved_publisher,
                    session_factory=factory,
                    owner=owner,
                    now=None,
                    limit=batch_size,
                    should_stop=stop_event.is_set,
                )
                if stats["processed"]:
                    logger.info("Chat delivery worker processed: %s", stats)
            except Exception as exc:
                logger.exception("Chat delivery worker loop error: %s", exc)
            remaining = poll_interval
            while remaining > 0 and not stop_event.is_set():
                step = min(0.25, remaining)
                await asyncio.sleep(step)
                remaining -= step
    logger.info("Chat delivery worker stopped")


def _run_coroutine_in_new_loop(coroutine) -> Any:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coroutine)
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


def run_until_stopped(
    stop_event: Any,
    *,
    owner: str = "chat-delivery-worker",
    interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    limit: int = DEFAULT_BATCH_SIZE,
) -> None:
    _run_coroutine_in_new_loop(
        run_forever_async(
            stop_event,
            owner=owner,
            interval=interval,
            limit=limit,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Nanobot chat delivery worker")
    parser.add_argument("--loop", action="store_true", help="常驻轮询聊天投递 outbox")
    parser.add_argument("--interval", type=float, default=DEFAULT_POLL_INTERVAL_SECONDS)
    parser.add_argument("--limit", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--owner", default="chat-delivery-worker")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.loop:
        import threading

        run_until_stopped(
            threading.Event(),
            owner=args.owner,
            interval=args.interval,
            limit=args.limit,
        )
        return
    result = _run_coroutine_in_new_loop(
        run_once_async(owner=args.owner, limit=args.limit)
    )
    logger.info("Chat delivery worker once: %s", result)


if __name__ == "__main__":
    main()

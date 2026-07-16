"""独立通用出站投递 worker。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import uuid4

import aiohttp
from sqlalchemy.orm import Session

from core.outbound_delivery_service import (
    OutboundTransport,
    OutboundTransportRequest,
    OutboundWorkerConfig,
    deliver_outbound_once,
)
from core.outbound_transport import deliver_qq_push_with_session


logger = logging.getLogger("nanobot.outbound_delivery.worker")
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
async def _transport_scope(
    transport: OutboundTransport | None,
    *,
    push_token: str,
) -> AsyncIterator[OutboundTransport]:
    if transport is not None:
        yield transport
        return

    async with aiohttp.ClientSession() as session:
        async def qq_transport(
            request: OutboundTransportRequest,
        ):
            return await deliver_qq_push_with_session(
                session,
                push_url=request.push_url,
                push_token=push_token,
                target_type=request.target_type,
                target_id=request.target_id,
                message=request.message,
                timeout_seconds=request.timeout_seconds,
            )

        yield qq_transport


def _empty_stats() -> dict[str, int]:
    return {
        "processed": 0,
        "delivered": 0,
        "retry_wait": 0,
        "failed": 0,
        "blocked": 0,
        "ambiguous": 0,
        "cancelled": 0,
    }


async def _run_batch(
    *,
    transport: OutboundTransport,
    session_factory: Callable[[], Session],
    config: OutboundWorkerConfig,
    owner: str,
    stop_event: Any | None,
    now: datetime | None,
    limit: int,
) -> dict[str, int]:
    stats = _empty_stats()
    while stats["processed"] < limit:
        if stop_event is not None and stop_event.is_set():
            break
        result = await deliver_outbound_once(
            session_factory=session_factory,
            transport=transport,
            config=config,
            worker_owner=owner,
            now=now,
        )
        if result is None:
            break
        stats["processed"] += 1
        if result.outbox_status in stats:
            stats[result.outbox_status] += 1
    return stats


async def run_once_async(
    *,
    transport: OutboundTransport | None = None,
    session_factory: Callable[[], Session] | None = None,
    config: OutboundWorkerConfig | None = None,
    owner: str = "outbound-delivery-worker",
    stop_event: Any | None = None,
    now: datetime | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    """处理一个有界批次；已开始的投递会在停止信号后完成结算。"""

    resolved_config = config or OutboundWorkerConfig.from_env()
    batch_size = resolved_config.batch_size if limit is None else int(limit)
    if batch_size <= 0:
        raise ValueError("limit 必须是正整数")
    factory = _session_factory_or_default(session_factory)
    async with _transport_scope(
        transport,
        push_token=resolved_config.push_token,
    ) as resolved_transport:
        return await _run_batch(
            transport=resolved_transport,
            session_factory=factory,
            config=resolved_config,
            owner=owner,
            stop_event=stop_event,
            now=now,
            limit=batch_size,
        )


async def _wait_for_stop(stop_event: asyncio.Event, timeout: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=timeout)
    except TimeoutError:
        return


async def run_forever_async(
    stop_event: asyncio.Event,
    *,
    transport: OutboundTransport | None = None,
    session_factory: Callable[[], Session] | None = None,
    config: OutboundWorkerConfig | None = None,
    owner: str = "outbound-delivery-worker",
) -> None:
    """常驻轮询；停止后不领取新记录，并等待当前记录完成。"""

    resolved_config = config or OutboundWorkerConfig.from_env()
    factory = _session_factory_or_default(session_factory)
    logger.info(
        "Outbound delivery worker started owner=%s batch_size=%s",
        owner,
        resolved_config.batch_size,
    )
    async with _transport_scope(
        transport,
        push_token=resolved_config.push_token,
    ) as resolved_transport:
        while not stop_event.is_set():
            try:
                stats = await _run_batch(
                    transport=resolved_transport,
                    session_factory=factory,
                    config=resolved_config,
                    owner=owner,
                    stop_event=stop_event,
                    now=None,
                    limit=resolved_config.batch_size,
                )
                if stats["processed"]:
                    logger.info("Outbound delivery worker processed: %s", stats)
            except Exception as exc:
                logger.error(
                    "Outbound delivery worker loop failed error_type=%s",
                    type(exc).__name__,
                )
            if not stop_event.is_set():
                await _wait_for_stop(
                    stop_event,
                    resolved_config.poll_interval_seconds,
                )
    logger.info("Outbound delivery worker stopped")


def _stable_owner() -> str:
    host = socket.gethostname().strip() or "unknown-host"
    return f"{host[:63]}:{uuid4().hex}"[:128]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Nanobot outbound delivery worker")
    parser.add_argument("--loop", action="store_true", help="常驻轮询通用出站 outbox")
    parser.add_argument("--owner", default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--lease-seconds", type=float, default=None)
    parser.add_argument("--poll-interval", type=float, default=None)
    return parser


def _config_with_cli_overrides(
    args: argparse.Namespace,
) -> OutboundWorkerConfig:
    config = OutboundWorkerConfig.from_env()
    changes = {}
    if args.batch_size is not None:
        changes["batch_size"] = args.batch_size
    if args.lease_seconds is not None:
        changes["lease_seconds"] = args.lease_seconds
    if args.poll_interval is not None:
        changes["poll_interval_seconds"] = args.poll_interval
    return replace(config, **changes) if changes else config


async def _main_async(args: argparse.Namespace) -> int:
    config = _config_with_cli_overrides(args)
    owner = str(args.owner or _stable_owner()).strip()
    if not owner or len(owner) > 128:
        raise ValueError("owner 必须是 1-128 字符")
    if not args.loop:
        stats = await run_once_async(config=config, owner=owner)
        logger.info("Outbound delivery worker once: %s", stats)
        return 0

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass
    await run_forever_async(stop_event, config=config, owner=owner)
    return 0


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


def main(argv: list[str] | None = None) -> int:
    from bootstrap.logging_config import configure_logging

    configure_logging()
    args = _parser().parse_args(argv)
    return int(_run_coroutine_in_new_loop(_main_async(args)))


if __name__ == "__main__":
    raise SystemExit(main())

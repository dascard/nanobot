"""Session summary worker 入口。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from app.session_memory import config
from app.session_memory.jobs import (
    SessionSummaryJobLease,
    claim_summary_job,
    fetch_pending_summary_jobs,
    recover_stale_running_jobs,
    session_summary_job_lease,
)
from app.session_memory.llm_summarizer import (
    process_claimed_session_summary_job_short_transactions,
    process_claimed_session_summary_job_short_transactions_async,
)
from core.database import SessionLocal, init_db

logger = logging.getLogger("nanobot.session_summary.worker")

_schema_ready = False


def default_worker_owner() -> str:
    """为每个 worker 实例生成不可复用的 owner。"""

    host = socket.gethostname().strip() or "unknown-host"
    return f"{host[:63]}:{os.getpid()}:{uuid4().hex}"[:128]


def _resolved_owner(owner: str | None) -> str:
    value = str(owner or default_worker_owner()).strip()
    if not value or len(value) > 128:
        raise ValueError("owner 必须是 1-128 字符")
    return value


def _ensure_schema_ready() -> None:
    """worker 自己确保 schema/migration 已就绪，不依赖 Web 进程启动顺序。"""
    global _schema_ready
    if _schema_ready:
        return
    init_db()
    _schema_ready = True


def _claim_next_job(
    *,
    owner: str,
    limit: int | None = None,
) -> tuple[SessionSummaryJobLease | None, int]:
    db = SessionLocal()
    try:
        recovered = recover_stale_running_jobs(db, limit=limit)
        jobs = fetch_pending_summary_jobs(db, limit=limit)
        claimed_lease: SessionSummaryJobLease | None = None
        for job in jobs:
            claimed = claim_summary_job(db, int(job.id or 0), owner=owner)
            if claimed is not None:
                claimed_lease = session_summary_job_lease(claimed)
                break
        db.commit()
        return claimed_lease, recovered
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_once(
    *,
    owner: str | None = None,
    limit: int | None = None,
    summarizer: Callable[[list[dict[str, str]]], Any] | None = None,
) -> dict[str, int]:
    _ensure_schema_ready()
    resolved_owner = _resolved_owner(owner)
    max_jobs = max(1, int(limit or config.SESSION_SUMMARY_JOB_BATCH_SIZE))
    stats = {"processed": 0, "done": 0, "failed": 0, "recovered": 0}
    while stats["processed"] < max_jobs:
        lease, recovered = _claim_next_job(
            owner=resolved_owner,
            limit=max_jobs,
        )
        stats["recovered"] += recovered
        if lease is None:
            break

        stats["processed"] += 1
        ok = process_claimed_session_summary_job_short_transactions(
            SessionLocal,
            lease=lease,
            summarizer=summarizer,
        )
        if ok:
            stats["done"] += 1
        else:
            stats["failed"] += 1
    return stats


async def run_once_async(
    *,
    owner: str | None = None,
    limit: int | None = None,
    summarizer: Callable[[list[dict[str, str]]], Any] | None = None,
) -> dict[str, int]:
    _ensure_schema_ready()
    resolved_owner = _resolved_owner(owner)
    max_jobs = max(1, int(limit or config.SESSION_SUMMARY_JOB_BATCH_SIZE))
    stats = {"processed": 0, "done": 0, "failed": 0, "recovered": 0}
    while stats["processed"] < max_jobs:
        lease, recovered = _claim_next_job(
            owner=resolved_owner,
            limit=max_jobs,
        )
        stats["recovered"] += recovered
        if lease is None:
            break

        stats["processed"] += 1
        ok = await process_claimed_session_summary_job_short_transactions_async(
            SessionLocal,
            lease=lease,
            summarizer=summarizer,
        )
        if ok:
            stats["done"] += 1
        else:
            stats["failed"] += 1
    return stats


def run_forever(
    *,
    owner: str | None = None,
    limit: int | None = None,
    interval: float = 10.0,
) -> None:
    resolved_owner = _resolved_owner(owner)
    _run_coroutine_in_new_loop(
        run_forever_async(
            owner=resolved_owner,
            limit=limit,
            interval=interval,
        )
    )


async def run_forever_async(
    *,
    owner: str | None = None,
    limit: int | None = None,
    interval: float = 10.0,
    stop_event: Any | None = None,
) -> None:
    resolved_owner = _resolved_owner(owner)
    logger.info("session summary worker async loop started interval=%ss", interval)
    while stop_event is None or not stop_event.is_set():
        try:
            stats = await run_once_async(owner=resolved_owner, limit=limit)
            if stats.get("processed"):
                logger.info("session summary worker processed: %s", stats)
        except Exception as exc:
            logger.exception("session summary worker loop error: %s", exc)
        remaining = max(1.0, float(interval or 10.0))
        while remaining > 0 and (stop_event is None or not stop_event.is_set()):
            step = min(1.0, remaining)
            await asyncio.sleep(step)
            remaining -= step
    logger.info("session summary worker async loop stopped")


def _run_coroutine_in_new_loop(coro) -> Any:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
            shutdown_default_executor = getattr(loop, "shutdown_default_executor", None)
            if shutdown_default_executor is not None:
                loop.run_until_complete(shutdown_default_executor())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


def run_until_stopped(
    stop_event,
    *,
    owner: str | None = None,
    interval: float = 10.0,
) -> None:
    resolved_owner = _resolved_owner(owner)
    _run_coroutine_in_new_loop(
        run_forever_async(
            owner=resolved_owner,
            interval=interval,
            stop_event=stop_event,
        )
    )


def main() -> None:
    from bootstrap.model_runtime import (
        start_model_runtime,
        stop_model_runtime,
    )
    from core.telemetry.runtime import (
        start_telemetry_runtime,
        stop_telemetry_runtime,
    )

    parser = argparse.ArgumentParser(description="Nanobot session summary worker")
    parser.add_argument("--loop", action="store_true", help="常驻轮询 pending session summary jobs")
    parser.add_argument("--interval", type=float, default=10.0, help="loop 模式轮询间隔秒数")
    parser.add_argument("--limit", type=int, default=None, help="每轮最多处理的 job 数")
    parser.add_argument("--owner", default=None, help="写入 locked_by 的 worker 标识")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    telemetry_handle = start_telemetry_runtime()
    try:
        start_model_runtime()
        try:
            if args.loop:
                run_forever(
                    owner=args.owner,
                    limit=args.limit,
                    interval=args.interval,
                )
                return
            result = _run_coroutine_in_new_loop(
                run_once_async(owner=args.owner, limit=args.limit)
            )
            logger.info("session summary worker once: %s", result)
        finally:
            stop_model_runtime()
    finally:
        stop_telemetry_runtime(telemetry_handle)


if __name__ == "__main__":
    main()

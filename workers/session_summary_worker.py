"""Session summary worker 入口。"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Callable
from typing import Any

from app.session_memory import config
from app.session_memory.jobs import claim_summary_job, fetch_pending_summary_jobs, recover_stale_running_jobs
from app.session_memory.llm_summarizer import (
    process_claimed_session_summary_job_short_transactions,
    process_claimed_session_summary_job_short_transactions_async,
)
from core.database import SessionLocal, init_db

logger = logging.getLogger("nanobot.session_summary.worker")

_schema_ready = False


def _ensure_schema_ready() -> None:
    """worker 自己确保 schema/migration 已就绪，不依赖 Web 进程启动顺序。"""
    global _schema_ready
    if _schema_ready:
        return
    init_db()
    _schema_ready = True


def _claim_next_job(*, owner: str, limit: int | None = None) -> tuple[int | None, int]:
    db = SessionLocal()
    try:
        recovered = recover_stale_running_jobs(db, limit=limit)
        jobs = fetch_pending_summary_jobs(db, limit=limit)
        claimed_id: int | None = None
        for job in jobs:
            claimed = claim_summary_job(db, int(job.id or 0), owner=owner)
            if claimed is not None:
                claimed_id = int(claimed.id or 0)
                break
        db.commit()
        return claimed_id, recovered
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_once(
    *,
    owner: str = "session-summary-worker",
    limit: int | None = None,
    summarizer: Callable[[list[dict[str, str]]], Any] | None = None,
) -> dict[str, int]:
    _ensure_schema_ready()
    max_jobs = max(1, int(limit or config.SESSION_SUMMARY_JOB_BATCH_SIZE))
    stats = {"processed": 0, "done": 0, "failed": 0, "recovered": 0}
    while stats["processed"] < max_jobs:
        job_id, recovered = _claim_next_job(owner=owner, limit=max_jobs)
        stats["recovered"] += recovered
        if not job_id:
            break

        stats["processed"] += 1
        ok = process_claimed_session_summary_job_short_transactions(
            SessionLocal,
            job_id=job_id,
            summarizer=summarizer,
            owner=owner,
        )
        if ok:
            stats["done"] += 1
        else:
            stats["failed"] += 1
    return stats


async def run_once_async(
    *,
    owner: str = "session-summary-worker",
    limit: int | None = None,
    summarizer: Callable[[list[dict[str, str]]], Any] | None = None,
) -> dict[str, int]:
    _ensure_schema_ready()
    max_jobs = max(1, int(limit or config.SESSION_SUMMARY_JOB_BATCH_SIZE))
    stats = {"processed": 0, "done": 0, "failed": 0, "recovered": 0}
    while stats["processed"] < max_jobs:
        job_id, recovered = _claim_next_job(owner=owner, limit=max_jobs)
        stats["recovered"] += recovered
        if not job_id:
            break

        stats["processed"] += 1
        ok = await process_claimed_session_summary_job_short_transactions_async(
            SessionLocal,
            job_id=job_id,
            summarizer=summarizer,
            owner=owner,
        )
        if ok:
            stats["done"] += 1
        else:
            stats["failed"] += 1
    return stats


def run_forever(
    *,
    owner: str = "session-summary-worker",
    limit: int | None = None,
    interval: float = 10.0,
) -> None:
    _run_coroutine_in_new_loop(
        run_forever_async(owner=owner, limit=limit, interval=interval)
    )


async def run_forever_async(
    *,
    owner: str = "session-summary-worker",
    limit: int | None = None,
    interval: float = 10.0,
    stop_event: Any | None = None,
) -> None:
    logger.info("session summary worker async loop started interval=%ss", interval)
    while stop_event is None or not stop_event.is_set():
        try:
            stats = await run_once_async(owner=owner, limit=limit)
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


def run_until_stopped(stop_event, *, owner: str = "session-summary-worker", interval: float = 10.0) -> None:
    _run_coroutine_in_new_loop(
        run_forever_async(owner=owner, interval=interval, stop_event=stop_event)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Nanobot session summary worker")
    parser.add_argument("--loop", action="store_true", help="常驻轮询 pending session summary jobs")
    parser.add_argument("--interval", type=float, default=10.0, help="loop 模式轮询间隔秒数")
    parser.add_argument("--limit", type=int, default=None, help="每轮最多处理的 job 数")
    parser.add_argument("--owner", default="session-summary-worker", help="写入 locked_by 的 worker 标识")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    if args.loop:
        run_forever(owner=args.owner, limit=args.limit, interval=args.interval)
        return
    result = _run_coroutine_in_new_loop(run_once_async(owner=args.owner, limit=args.limit))
    logger.info("session summary worker once: %s", result)


if __name__ == "__main__":
    main()

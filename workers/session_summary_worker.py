"""Session summary worker 入口。"""

from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Callable
from typing import Any

from app.session_memory import config
from app.session_memory.jobs import claim_summary_job, fetch_pending_summary_jobs, recover_stale_running_jobs
from app.session_memory.llm_summarizer import process_claimed_session_summary_job_short_transactions
from core.database import SessionLocal

logger = logging.getLogger("nanobot.session_summary.worker")


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


def run_forever(
    *,
    owner: str = "session-summary-worker",
    limit: int | None = None,
    interval: float = 10.0,
) -> None:
    logger.info("session summary worker loop started interval=%ss", interval)
    while True:
        try:
            stats = run_once(owner=owner, limit=limit)
            if stats.get("processed"):
                logger.info("session summary worker processed: %s", stats)
        except Exception as exc:
            logger.exception("session summary worker loop error: %s", exc)
        time.sleep(max(1.0, float(interval or 10.0)))


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
    result = run_once(owner=args.owner, limit=args.limit)
    logger.info("session summary worker once: %s", result)


if __name__ == "__main__":
    main()

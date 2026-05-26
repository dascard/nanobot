"""Session summary worker 入口。"""

from __future__ import annotations

import argparse
import logging
import time

from app.session_memory.llm_summarizer import run_session_summary_worker_once
from core.database import SessionLocal

logger = logging.getLogger("nanobot.session_summary.worker")


def run_once(*, owner: str = "session-summary-worker", limit: int | None = None) -> dict[str, int]:
    db = SessionLocal()
    try:
        stats = run_session_summary_worker_once(db, owner=owner, limit=limit)
        db.commit()
        return stats
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


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

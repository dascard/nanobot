"""Session summary worker 入口。"""

from __future__ import annotations

import logging

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


if __name__ == "__main__":
    result = run_once()
    logger.info("session summary worker once: %s", result)

"""后台采样调度——定期扫日志和 DB 新数据，写入 EvalCandidate。"""
from __future__ import annotations

import logging
import os

from core.async_bridge import run_awaitable_sync
from core.database import SessionLocal

logger = logging.getLogger("nanobot.eval.scheduler")


class CandidateGate:
    """带 per-suite 待标注上限的候选插入器——上限内才落库,防止单一 suite 无限积压。"""

    def __init__(self, db, max_pending: int):
        from core.eval_sampling.store import count_pending_by_suite

        self._db = db
        self._max = max(0, int(max_pending or 0))
        self._pending = count_pending_by_suite(db) if self._max > 0 else {}
        self.skipped: dict[str, int] = {}
        self.created = 0

    def insert(self, candidate: dict) -> bool:
        from core.eval_sampling.store import upsert_candidate

        suite = str(candidate.get("suite", ""))
        if self._max > 0 and self._pending.get(suite, 0) >= self._max:
            self.skipped[suite] = self.skipped.get(suite, 0) + 1
            return False
        if upsert_candidate(self._db, candidate):
            if self._max > 0:
                self._pending[suite] = self._pending.get(suite, 0) + 1
            self.created += 1
            return True
        return False


async def run_sampling_cycle():
    """单轮采样：日志 + DB。"""
    from core.settings_service import settings
    from core.eval_sampling.store import get_cursor, save_cursor
    from core.eval_sampling.log_sampler import sample_log_file
    from core.eval_sampling.db_sampler import sample_chatlog_replies, sample_timing_events, sample_memory_learning

    db = SessionLocal()
    gate = None
    created = 0
    try:
        gate = CandidateGate(
            db, settings.get_int("eval.sample_max_pending_per_suite", 200)
        )
        # 日志采样
        if settings.get_bool("eval.sample_log_enabled", True):
            log_path = settings.get_str("eval.log_path", "data/nanobot.log")
            if os.path.isfile(log_path):
                cursor = get_cursor(db, "log", log_path)
                offset = cursor.get("byte_offset", 0)
                start_line = cursor.get("line_no", 0)
                candidates, new_cursor = sample_log_file(
                    log_path, start_offset=offset, start_line=start_line,
                    limit=settings.get_int("eval.sample_limit_per_cycle", 100))
                for c in candidates:
                    if gate.insert(c):
                        created += 1
                save_cursor(db, "log", log_path, new_cursor)

        # DB 采样
        if settings.get_bool("eval.sample_db_enabled", True):
            cursors = {
                "chatlog_replies": get_cursor(db, "db", "chatlog_replies"),
                "timing_events": get_cursor(db, "db", "timing_events"),
                "group_learning_slang": get_cursor(
                    db,
                    "db",
                    "group_learning_slang",
                ),
                "group_learning_expression": get_cursor(
                    db,
                    "db",
                    "group_learning_expression",
                ),
            }

            items = sample_chatlog_replies(db, after_id=cursors["chatlog_replies"].get("after_id", 0), limit=30)
            for item in items:
                if gate.insert(item):
                    created += 1
            if items:
                cursors["chatlog_replies"] = {"after_id": int(items[-1]["source_ref"].split(":")[-1])}

            items = sample_timing_events(db, after_id=cursors["timing_events"].get("after_id", 0), limit=30)
            for item in items:
                if gate.insert(item):
                    created += 1
            if items:
                cursors["timing_events"] = {"after_id": int(items[-1].get("source_ref", ":0").split(":")[-1])}

            # 新群学习 slang/expression 候选分开推进游标。
            items = sample_memory_learning(
                db,
                after_latest=cursors[
                    "group_learning_slang"
                ].get("after_id", 0),
                candidate_type="slang",
                limit=30,
            )
            for item in items:
                if gate.insert(item):
                    created += 1
            if items:
                max_id = max(int(i["source_ref"].split(":")[-1]) for i in items)
                cursors["group_learning_slang"] = {
                    "after_id": max_id
                }

            items = sample_memory_learning(
                db,
                after_latest=cursors[
                    "group_learning_expression"
                ].get("after_id", 0),
                candidate_type="expression",
                limit=30,
            )
            for item in items:
                if gate.insert(item):
                    created += 1
            if items:
                max_id = max(int(i["source_ref"].split(":")[-1]) for i in items)
                cursors["group_learning_expression"] = {
                    "after_id": max_id
                }

            for cursor_key in cursors:
                save_cursor(db, "db", cursor_key, cursors[cursor_key])

        if created:
            logger.info(f"[EvalSample] cycle done, created={created}")
        if gate is not None and gate.skipped:
            logger.info(f"[EvalSample] per-suite pending cap reached, skipped={gate.skipped}")
    except Exception as e:
        logger.error(f"[EvalSample] cycle failed: {e}")
    finally:
        db.close()
    return created


def eval_sampling_scheduler(stop_event):
    """后台采样调度线程——定期扫描日志和 DB。"""
    from core.settings_service import settings

    logger.info("[EvalSample] scheduler started")
    interval = max(60, settings.get_int("eval.sample_interval_sec", 600))
    while not stop_event.wait(timeout=interval):
        try:
            created = run_awaitable_sync(run_sampling_cycle())
            if created:
                logger.debug(f"[EvalSample] cycle created {created} candidates")
        except Exception as e:
            logger.error(f"[EvalSample] scheduler error: {e}")
    logger.info("[EvalSample] scheduler stopped")

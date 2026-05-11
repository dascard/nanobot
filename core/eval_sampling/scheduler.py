"""后台采样调度——定期扫日志和 DB 新数据，写入 EvalCandidate。"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from core.database import SessionLocal

logger = logging.getLogger("nanobot.eval.scheduler")

async def run_sampling_cycle():
    """单轮采样：日志 + DB。"""
    from core.settings_service import settings
    from core.eval_sampling.store import upsert_candidate, get_cursor, save_cursor
    from core.eval_sampling.log_sampler import sample_log_file
    from core.eval_sampling.db_sampler import sample_chatlog_replies, sample_timing_events, sample_memory_learning

    db = SessionLocal()
    created = 0
    try:
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
                    if upsert_candidate(db, c):
                        created += 1
                save_cursor(db, "log", log_path, new_cursor)

        # DB 采样
        if settings.get_bool("eval.sample_db_enabled", True):
            cursors = {
                "chatlog_replies": get_cursor(db, "db", "chatlog_replies"),
                "timing_events": get_cursor(db, "db", "timing_events"),
                "memory_jargon": get_cursor(db, "db", "memory_jargon"),
                "memory_expression": get_cursor(db, "db", "memory_expression"),
            }

            items = sample_chatlog_replies(db, after_id=cursors["chatlog_replies"].get("after_id", 0), limit=30)
            for item in items:
                if upsert_candidate(db, item):
                    created += 1
            if items:
                cursors["chatlog_replies"] = {"after_id": int(items[-1]["source_ref"].split(":")[-1])}

            items = sample_timing_events(db, after_id=cursors["timing_events"].get("after_id", 0), limit=30)
            for item in items:
                if upsert_candidate(db, item):
                    created += 1
            if items:
                cursors["timing_events"] = {"after_id": int(items[-1].get("source_ref", ":0").split(":")[-1])}

            # JargonMemory 和 ExpressionMemory 分开采样
            items = sample_memory_learning(db, after_latest=cursors["memory_jargon"].get("after_id", 0),
                                            table="jargon", limit=30)
            for item in items:
                if upsert_candidate(db, item):
                    created += 1
            if items:
                max_id = max(int(i["source_ref"].split(":")[-1]) for i in items)
                cursors["memory_jargon"] = {"after_id": max_id}

            items = sample_memory_learning(db, after_latest=cursors["memory_expression"].get("after_id", 0),
                                            table="expression", limit=30)
            for item in items:
                if upsert_candidate(db, item):
                    created += 1
            if items:
                max_id = max(int(i["source_ref"].split(":")[-1]) for i in items)
                cursors["memory_expression"] = {"after_id": max_id}

            for cursor_key in cursors:
                save_cursor(db, "db", cursor_key, cursors[cursor_key])

        if created:
            logger.info(f"[EvalSample] cycle done, created={created}")
    except Exception as e:
        logger.error(f"[EvalSample] cycle failed: {e}")
    finally:
        db.close()
    return created

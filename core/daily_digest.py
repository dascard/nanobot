"""
Daily digest pipeline for progressive-disclosure memory management.

Design:
- Level 0: rich daily digest (high-detail)
- Level 1: compressed digest (mid-detail)
- Level 2: compact digest (low-detail)

This keeps conversation memory queryable in SQL while allowing incremental reveal.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import List

from sqlalchemy import and_

from config import DAILY_DIGEST_HOUR
from core.compaction import run_autocompact_circuit_breaker
from core.database import SessionLocal, ChatLog, MemoryDigest

logger = logging.getLogger("nanobot.daily_digest")

TOPIC_KEYWORDS = {
    "model_release": ["发布", "release", "new model", "版本", "更新"],
    "pricing": ["价格", "pricing", "free", "试用", "优惠", "token", "白嫖", "便宜"],
    "tooling": ["sdk", "api", "插件", "workflow", "agent", "tool"],
    "community": ["reddit", "x.com", "twitter", "论坛", "社区", "评论"],
}

MODEL_HINTS = [
    "qwen", "deepseek", "kimi", "gpt", "claude", "gemini", "llama", "mistral", "hunyuan", "glm",
    "通义", "豆包", "混元", "智谱", "阶跃", "minimax",
]


def _to_day(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _next_run_delay_seconds(now: datetime, run_hour: int) -> int:
    run_hour = max(0, min(23, int(run_hour)))
    target = now.replace(hour=run_hour, minute=5, second=0, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    return max(60, int((target - now).total_seconds()))


def _format_log_line(log: ChatLog) -> str:
    ts = log.created_at.strftime("%H:%M") if log.created_at else "--:--"
    sender = (log.sender_name or "").strip()
    role = (log.role or "unknown").strip()
    content = (log.content or "").replace("\n", " ").strip()
    if len(content) > 280:
        content = content[:280] + "..."
    who = sender or role
    return f"[{ts}] {role}({who}): {content}"


def _build_progressive_layers(lines: List[str]) -> tuple[str, str, str]:
    # Layered compaction inspired by progressive disclosure memory pattern.
    level0 = run_autocompact_circuit_breaker(lines, max_length=9000)
    level1 = run_autocompact_circuit_breaker(level0.splitlines(), max_length=3500)
    level2 = run_autocompact_circuit_breaker(level1.splitlines(), max_length=1500)
    return level0, level1, level2


def _extract_structured_tags(logs: List[ChatLog]) -> dict:
    raw_text = "\n".join([(x.content or "") for x in logs]).lower()

    topics = []
    for topic, keys in TOPIC_KEYWORDS.items():
        if any(k in raw_text for k in keys):
            topics.append(topic)

    models = sorted({m for m in MODEL_HINTS if m in raw_text})
    value_signal_score = 0
    for k in TOPIC_KEYWORDS["pricing"]:
        if k in raw_text:
            value_signal_score += 1

    users = sum(1 for x in logs if (x.role or "") == "user")
    assistants = sum(1 for x in logs if (x.role or "") == "assistant")
    ambient = sum(1 for x in logs if (x.role or "") == "ambient")

    return {
        "topics": topics,
        "model_hints": models,
        "value_signal_score": value_signal_score,
        "message_stats": {
            "total": len(logs),
            "user": users,
            "assistant": assistants,
            "ambient": ambient,
        },
    }


def _already_digested(db, session_id: str, digest_date: str) -> bool:
    exists = (
        db.query(MemoryDigest.id)
        .filter(
            and_(
                MemoryDigest.session_id == session_id,
                MemoryDigest.digest_date == digest_date,
                MemoryDigest.level == 2,
            )
        )
        .first()
    )
    return exists is not None


def generate_daily_digest_for_date(target_date: str, user_id: str | None = None) -> int:
    """
    Summarize one day of chat logs into 3 progressive layers.

    Returns number of sessions summarized.
    """
    db = SessionLocal()
    created = 0
    try:
        base_query = db.query(ChatLog).filter(ChatLog.created_at.isnot(None))
        if user_id:
            base_query = base_query.filter(ChatLog.user_id == user_id)

        all_logs = base_query.order_by(ChatLog.id.asc()).all()

        by_session: dict[str, List[ChatLog]] = {}
        for log in all_logs:
            if _to_day(log.created_at) != target_date:
                continue
            sid = (log.session_id or "").strip()
            if not sid:
                continue
            by_session.setdefault(sid, []).append(log)

        for session_id, logs in by_session.items():
            if not logs:
                continue
            if _already_digested(db, session_id, target_date):
                continue

            lines = [_format_log_line(x) for x in logs]
            level0, level1, level2 = _build_progressive_layers(lines)

            start_id = logs[0].id
            end_id = logs[-1].id
            uid = logs[0].user_id or ""
            tags = _extract_structured_tags(logs)
            meta = {
                "source_log_count": len(logs),
                "source_date": target_date,
                "session_id": session_id,
                "tags": tags,
            }

            d0 = MemoryDigest(
                user_id=uid,
                session_id=session_id,
                digest_date=target_date,
                level=0,
                parent_id=None,
                content=level0,
                meta_json=json.dumps(meta, ensure_ascii=False),
                source_start_log_id=start_id,
                source_end_log_id=end_id,
            )
            db.add(d0)
            db.flush()

            d1 = MemoryDigest(
                user_id=uid,
                session_id=session_id,
                digest_date=target_date,
                level=1,
                parent_id=d0.id,
                content=level1,
                meta_json=json.dumps(meta, ensure_ascii=False),
                source_start_log_id=start_id,
                source_end_log_id=end_id,
            )
            db.add(d1)
            db.flush()

            d2 = MemoryDigest(
                user_id=uid,
                session_id=session_id,
                digest_date=target_date,
                level=2,
                parent_id=d1.id,
                content=level2,
                meta_json=json.dumps(meta, ensure_ascii=False),
                source_start_log_id=start_id,
                source_end_log_id=end_id,
            )
            db.add(d2)
            created += 1

        db.commit()
        if created > 0:
            logger.info(f"Daily digest generated for {created} session(s), date={target_date}")
        return created
    except Exception:
        db.rollback()
        logger.exception(f"Daily digest failed for date={target_date}")
        return 0
    finally:
        db.close()


def run_daily_digest_once() -> int:
    # Summarize yesterday by default so the day is complete.
    yesterday = _to_day(datetime.now() - timedelta(days=1))
    return generate_daily_digest_for_date(yesterday)


def daily_digest_scheduler(stop_event: threading.Event) -> None:
    """Background scheduler: run once at configured hour every day."""
    logger.info(f"Daily digest scheduler started, run_hour={DAILY_DIGEST_HOUR}")

    # Startup catch-up once.
    run_daily_digest_once()

    while not stop_event.is_set():
        now = datetime.now()
        delay = _next_run_delay_seconds(now, DAILY_DIGEST_HOUR)

        # Sleep in short chunks so shutdown remains responsive.
        slept = 0
        while slept < delay and not stop_event.is_set():
            step = min(30, delay - slept)
            time.sleep(step)
            slept += step

        if stop_event.is_set():
            break

        run_daily_digest_once()

    logger.info("Daily digest scheduler stopped")

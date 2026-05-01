"""
Daily digest + scheduled task pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import List

import aiohttp
from sqlalchemy import and_

from config import DAILY_DIGEST_HOUR
from core.compaction import run_autocompact_circuit_breaker
from core.database import ChatLog, MemoryDigest, ScheduledTask, SessionLocal

logger = logging.getLogger("nanobot.daily_digest")

# QQbot / NoneBot 侧默认监听 8082；若部署环境不同，可通过环境变量覆盖。
QQBOT_PUSH_URL = os.environ.get("QQBOT_PUSH_URL", "http://172.17.0.1:8082/nanobot/push")

TOPIC_KEYWORDS = {
    "model_release": ["发布", "release", "new model", "版本", "更新"],
    "pricing": ["价格", "pricing", "free", "试用", "优惠", "token", "白嫖", "便宜"],
    "tooling": ["sdk", "api", "插件", "workflow", "agent", "tool"],
    "community": ["reddit", "x.com", "twitter", "论坛", "社区", "评论"],
}

MODEL_HINTS = [
    "qwen",
    "deepseek",
    "kimi",
    "gpt",
    "claude",
    "gemini",
    "llama",
    "mistral",
    "hunyuan",
    "glm",
    "通义",
    "豆包",
    "混元",
    "智谱",
    "阶跃",
    "minimax",
]


def _to_day(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _next_run_delay_seconds(now: datetime, run_hour: int) -> int:
    run_hour = max(0, min(23, int(run_hour)))
    target = now.replace(hour=run_hour, minute=5, second=0, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    return max(60, int((target - now).total_seconds()))


def _normalize_group_session_id(group_id: str) -> str:
    group_id = str(group_id or "").strip()
    if not group_id:
        return ""
    return group_id if group_id.startswith("group_") else f"group_{group_id}"


def _normalize_chatlog_session_id(session_id: str, user_id: str = "") -> str:
    sid = (session_id or "").strip()
    uid = (user_id or "").strip()
    if uid.startswith("group_"):
        return sid if sid.startswith("group_") else uid
    return sid


def _group_push_target_id(session_id: str) -> str:
    session_id = _normalize_group_session_id(session_id)
    return session_id.removeprefix("group_")


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
            sid = _normalize_chatlog_session_id(log.session_id, log.user_id)
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
            logger.info(
                f"Daily digest generated for {created} session(s), date={target_date}"
            )
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


# ── QQ 推送 ──


async def push_to_qq(target_type: str, target_id: str, message: str) -> bool:
    """推送消息到 QQ（通过 qqbot 的 /nanobot/push 端点）。"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                QQBOT_PUSH_URL,
                json={
                    "target_type": target_type,
                    "target_id": target_id,
                    "message": message,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    logger.info(
                        f"Push OK: {target_type}/{target_id} len={len(message)}"
                    )
                    return True
                logger.warning(
                    f"Push failed: status={resp.status}, body={await resp.text()}"
                )
                return False
    except Exception as e:
        logger.error(f"Push error: {e}")
        return False


# ── 定时任务调度 ──


async def _generate_task_message(task: ScheduledTask) -> str | None:
    """用 LLM 根据模板生成推送内容（带 10 分钟超时保护）。"""
    from clients.new_api_client import NewAPIClient
    from config import NEW_API_BASE_URL, NEW_API_KEY

    try:
        client = NewAPIClient(
            api_key=NEW_API_KEY, base_url=NEW_API_BASE_URL, timeout=60
        )
        messages = [{"role": "user", "content": task.prompt_template}]
        resp = await asyncio.wait_for(
            client.chat_completion(messages=messages, model_tier="fast"),
            timeout=600,
        )
        if isinstance(resp, dict) and "choices" in resp:
            return resp["choices"][0]["message"]["content"]
        # 返回体无 choices——可能是 API 错误或 free 模型被限流
        error_detail = resp.get("error", {}) if isinstance(resp, dict) else "null"
        logger.error(
            f"Task [{task.name}] LLM returned no choices: "
            f"resp_type={type(resp).__name__}, "
            f"error={str(error_detail)[:200]}, "
            f"keys={list(resp.keys()) if isinstance(resp, dict) else 'N/A'}"
        )
    except asyncio.TimeoutError:
        logger.error(f"Task [{task.name}] LLM call timed out (10min)")
    except Exception as e:
        logger.exception(f"Task [{task.name}] LLM call failed: {e}")
    return None


async def run_scheduled_tasks() -> int:
    """检查并执行到期的定时任务。返回执行数。"""
    db = SessionLocal()
    executed = 0
    try:
        now = datetime.now()
        tasks = db.query(ScheduledTask).filter(ScheduledTask.enabled == 1).all()

        for task in tasks:
            if not _should_run(task, now):
                continue

            logger.info(f"Running scheduled task: {task.name}")
            content = await _generate_task_message(task)
            if not content:
                logger.warning(
                    f"Task [{task.name}] skipped: LLM returned empty/no content"
                )
                continue

            ok = await push_to_qq(task.target_type, task.target_id, content)
            if ok:
                task.last_run_at = now
                db.commit()
                executed += 1
                logger.info(f"Task [{task.name}] completed and pushed")
            else:
                logger.error(f"Task [{task.name}] push_to_qq failed")
    except Exception as e:
        logger.exception(f"Scheduled tasks runner failed: {e}")
    finally:
        db.close()
    return executed


def _should_run(task: ScheduledTask, now: datetime) -> bool:
    """简单 cron 匹配（只支持分 时 日 月 周）。"""
    try:
        parts = (task.cron_expr or "").strip().split()
        if len(parts) != 5:
            return False
        minute, hour, day, month, dow = parts
        if not _match(now.minute, minute):
            return False
        if not _match(now.hour, hour):
            return False
        if not _match(now.day, day):
            return False
        if not _match(now.month, month):
            return False
        if not _match(now.isoweekday(), dow):
            return False
        # 避免同一分钟内重复执行
        if task.last_run_at and (now - task.last_run_at).total_seconds() < 60:
            return False
        return True
    except Exception:
        return False


def _match(value: int, expr: str) -> bool:
    if expr == "*":
        return True
    for part in expr.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-")
            if int(lo) <= value <= int(hi):
                return True
        elif "/" in part and part.startswith("*/"):
            step = int(part[2:])
            if value % step == 0:
                return True
        elif part.isdigit() and int(part) == value:
            return True
    return False


def scheduled_task_runner(stop_event: threading.Event) -> None:
    """后台线程：每分钟检查一次定时任务。"""
    logger.info("Scheduled task runner started")
    while not stop_event.is_set():
        try:
            asyncio.run(run_scheduled_tasks())
        except Exception as e:
            logger.error(f"Scheduled task tick failed: {e}")
        # Sleep 60 seconds (check once a minute)
        for _ in range(60):
            if stop_event.is_set():
                break
            time.sleep(1)
    logger.info("Scheduled task runner stopped")

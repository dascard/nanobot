"""主动情感外呼运行时基础能力。"""

from __future__ import annotations

import json
import hashlib
import logging
import math
import os
import random
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from clients.classifier_client import call_model_route, strip_think_blocks
from core.async_bridge import run_awaitable_sync
from core.daily_digest import push_to_qq
from core.database import ChatLog, Persona, ProactiveOutreachLog, SessionLocal
from core.identity import _configured_super_user_ids
from core.settings_service import settings

DEFAULT_ACTIVE_HOURS = set(range(8, 23))
ACTIVE_HOURS_MIN_SAMPLES = 5
DEFAULT_MIN_INTERVAL_MIN = 30
DEFAULT_MAX_CHECK_INTERVAL_MIN = 1440
DEFAULT_MAX_SILENCE_MIN = 2880
DEFAULT_SURGE_MIN_PROB = 0.1
DEFAULT_SURGE_MAX_PROB = 0.6
OUTREACH_JUDGE_MAX_TOKENS = 240
OUTREACH_GENERATOR_MAX_TOKENS = 320
logger = logging.getLogger("nanobot.proactive_outreach")

RECENT_THREADS_PROMPT = """从最近对话中提炼主动外呼可自然跟进的点。

输入是 JSON 数组，每项含 role/content/created_at。
只提炼 1-3 个"未完话题 / 近期事件 / 可自然跟进的点"。
不要泛泛总结，不要编造对话里没有的事。

输出 JSON 数组，例如:["接口联调卡住，晚点继续看","晚上可能去夜跑"]。"""

OUTREACH_JUDGE_PROMPT = """你在判断:此刻是否值得主动给你最亲密的朋友发一条消息。

上下文:{grounding_json}
(优先看 recent_threads、now、hours_since_last_user_message、last_user_message、days_since_last_outreach；原始 recent_messages 只作补充)
(可能包含:你上次打算这时找 ta 聊的事 = {next_intent})

原则:
- 你是主动的一方,像真人朋友——想到了就找 ta,不必等 ta 开口
- 优先围绕 recent_threads 里的近期可跟进点判断,再结合 persona 和 last_user_message
- 有具体的、扎根上下文的话题才发(ta 提过的事、你注意到的近况)
- 刚聊过(hours_since_last_user_message 很小)、深夜、没有真正话题只想刷存在感时,倾向不发
- 发完或不发都自己决定几小时后再考虑(next_check_in_hours)

输出 JSON:{"should_reach_out": bool, "reason": str, "next_check_in_hours": number, "next_intent": str}"""

OUTREACH_GENERATOR_PROMPT = """你是 nanobot,要给最亲密的朋友主动发一条消息。

上下文:{grounding_json}
理由:{reason}

要求:
- 优先从 recent_threads 或 persona 里挑一个具体锚点展开,不要泛泛问候
- 扎根上下文,提到 ta 说过的具体事或你的近况感受
- 避免与上次主动消息(last_outreach.message)重复话题或措辞
- 温暖自然,像真的想起 ta 了
- 可以表达你自己的状态和情绪(想 ta、无聊、看到什么想分享)
- 结尾不必催回复

长度 2-5 句,只输出正文。"""


@contextmanager
def _session_scope(db: Session | None = None) -> Iterator[Session]:
    if db is not None:
        yield db
        return

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _json_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _iso_or_empty(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _parse_next_check_candidate(data: dict[str, Any], *, now: datetime) -> datetime | None:
    hours_value = data.get("next_check_in_hours")
    if hours_value is not None:
        try:
            hours = float(hours_value)
        except (TypeError, ValueError):
            hours = math.nan
        if math.isfinite(hours):
            return now + timedelta(hours=hours)
    return _parse_iso_datetime(data.get("next_check_at"))


def _clamp_next_check_at(
    candidate: datetime | None,
    *,
    now: datetime,
    min_interval_min: int,
    max_check_interval_min: int,
) -> datetime:
    lower = now + timedelta(minutes=max(1, int(min_interval_min)))
    upper = now + timedelta(minutes=max(1, int(max_check_interval_min)))
    if candidate is None:
        return lower
    if candidate < lower:
        return lower
    if candidate > upper:
        return upper
    return candidate


def _extract_json_object(raw: str) -> dict[str, Any]:
    cleaned = strip_think_blocks(raw or "")
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start < 0 or end <= start:
        return {}
    try:
        value = json.loads(cleaned[start:end])
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _grounding_json(grounding: dict[str, Any]) -> str:
    return json.dumps(grounding, ensure_ascii=False, default=str)


def _weekday_label(value: datetime) -> str:
    return ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][value.weekday()]


def _time_period_label(value: datetime) -> str:
    hour = value.hour
    if hour < 5:
        return "深夜"
    if hour < 8:
        return "清晨"
    if hour < 12:
        return "上午"
    if hour < 17:
        return "午后"
    if hour < 19:
        return "傍晚"
    return "夜晚"


def _hours_since(now: datetime, past: datetime | None) -> float | None:
    if past is None:
        return None
    return max(0.0, (now - past).total_seconds() / 3600.0)


def _truncate_text(value: str | None, max_chars: int = 160) -> str:
    text = (value or "").strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."


def _extract_json_array(raw: str | None) -> list[Any]:
    if not raw:
        return []
    cleaned = strip_think_blocks(str(raw))
    start = cleaned.find("[")
    end = cleaned.rfind("]") + 1
    if start < 0 or end <= start:
        return []
    try:
        value = json.loads(cleaned[start:end])
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _key_anchor_text(anchor: datetime) -> str:
    normalized = anchor
    if normalized.tzinfo is not None:
        normalized = normalized.replace(tzinfo=None)
    return normalized.replace(microsecond=0).isoformat()


def _outreach_key(user_id: str, anchor: datetime, *, forced: bool = False) -> str:
    raw = f"{user_id}:{_key_anchor_text(anchor)}:{'forced' if forced else 'judge'}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"outreach:{user_id}:{digest}"


def extract_recent_threads(
    recent_messages: list[dict[str, Any]],
    *,
    llm_call: Callable[..., str] | None = None,
) -> list[str]:
    """从最近对话中提炼可自然跟进的话题；失败时降级为空。"""

    compact_messages = [
        {
            "role": str(item.get("role") or ""),
            "content": _truncate_text(str(item.get("content") or ""), 240),
            "created_at": str(item.get("created_at") or ""),
        }
        for item in recent_messages
        if str(item.get("content") or "").strip()
    ]
    if not compact_messages:
        return []
    if llm_call is None and os.environ.get("NANOBOT_TESTING") == "1":
        return []

    caller = llm_call or call_model_route
    try:
        raw = caller(
            route_key="timing_proactive",
            system_prompt=RECENT_THREADS_PROMPT,
            user_message=json.dumps(compact_messages, ensure_ascii=False),
            max_tokens=180,
            temperature=0,
        )
    except Exception:
        return []

    threads: list[str] = []
    seen: set[str] = set()
    for item in _extract_json_array(raw):
        text = _truncate_text(str(item), 120)
        if text and text not in seen:
            seen.add(text)
            threads.append(text)
        if len(threads) >= 3:
            break
    return threads


def build_outreach_grounding(
    user_id: str,
    *,
    db: Session | None = None,
    recent_limit: int = 20,
    now: datetime | None = None,
    thread_extractor: Callable[[list[dict[str, Any]]], list[str]] | None = None,
) -> dict[str, Any]:
    """组装主动外呼 Judge/Generator 共用的 grounding。"""

    current = now or datetime.now()
    with _session_scope(db) as session:
        persona = session.query(Persona).filter(Persona.user_id == user_id).first()
        recent_rows = (
            session.query(ChatLog)
            .filter(ChatLog.user_id == user_id)
            .order_by(ChatLog.created_at.desc(), ChatLog.id.desc())
            .limit(max(1, int(recent_limit)))
            .all()
        )
        recent_messages = [
            {
                "role": row.role or "",
                "content": row.content or "",
                "created_at": _iso_or_empty(row.created_at),
                "session_id": row.session_id or "",
                "sender_name": row.sender_name or "",
            }
            for row in reversed(recent_rows)
        ]
        last_user_row = (
            session.query(ChatLog)
            .filter(ChatLog.user_id == user_id)
            .filter(ChatLog.role == "user")
            .filter(ChatLog.created_at.isnot(None))
            .order_by(ChatLog.created_at.desc(), ChatLog.id.desc())
            .first()
        )

        last_outreach = (
            session.query(ProactiveOutreachLog)
            .filter(ProactiveOutreachLog.user_id == user_id)
            .order_by(ProactiveOutreachLog.created_at.desc(), ProactiveOutreachLog.id.desc())
            .first()
        )
        last_user_hours = _hours_since(current, last_user_row.created_at if last_user_row else None)
        last_outreach_hours = _hours_since(
            current,
            last_outreach.created_at if last_outreach else None,
        )
        # TODO: personas 当前偏稳定画像；未来可在画像生成链路增加
        # recent_event / open_thread 时效性 fact，减少实时 recent_threads 提炼依赖。
        recent_threads = (
            thread_extractor(recent_messages)
            if thread_extractor is not None
            else extract_recent_threads(recent_messages)
        )

        return {
            "user_id": user_id,
            "now": {
                "iso": current.isoformat(),
                "weekday": _weekday_label(current),
                "period": _time_period_label(current),
                "hour": current.hour,
            },
            "persona": _json_object(persona.persona_json if persona else "{}"),
            "recent_messages": recent_messages,
            "recent_threads": recent_threads,
            "hours_since_last_user_message": last_user_hours,
            "last_user_message": {
                "content": _truncate_text(last_user_row.content if last_user_row else ""),
                "created_at": _iso_or_empty(last_user_row.created_at if last_user_row else None),
                "hours_ago": last_user_hours,
            } if last_user_row else None,
            "days_since_last_outreach": (
                last_outreach_hours / 24.0 if last_outreach_hours is not None else None
            ),
            "next_intent": last_outreach.next_intent if last_outreach else "",
            "last_outreach": {
                "status": last_outreach.status or "",
                "forced": bool(last_outreach.forced),
                "message": last_outreach.message or "",
                "judge_reason": last_outreach.judge_reason or "",
                "next_check_at": _iso_or_empty(last_outreach.next_check_at),
                "created_at": _iso_or_empty(last_outreach.created_at),
            } if last_outreach else None,
        }


def active_hours(
    user_id: str,
    *,
    db: Session | None = None,
    min_samples: int = ACTIVE_HOURS_MIN_SAMPLES,
) -> set[int]:
    """从用户历史消息小时分布推断活跃小时；样本不足时返回保守默认。"""

    with _session_scope(db) as session:
        rows = (
            session.query(ChatLog.created_at)
            .filter(ChatLog.user_id == user_id)
            .filter(ChatLog.created_at.isnot(None))
            .all()
        )

    hours = [row[0].hour for row in rows if row[0] is not None]
    if len(hours) < min_samples:
        return set(DEFAULT_ACTIVE_HOURS)
    return set(hours)


def judge_outreach(
    grounding: dict[str, Any],
    *,
    now: datetime | None = None,
    min_interval_min: int = 30,
    max_check_interval_min: int = 1440,
) -> dict[str, Any]:
    """判断是否应主动外呼，并钳制模型给出的下次检查时间。"""

    current = now or datetime.now()
    grounding_text = _grounding_json(grounding)
    prompt = (
        OUTREACH_JUDGE_PROMPT
        .replace("{grounding_json}", grounding_text)
        .replace("{next_intent}", str(grounding.get("next_intent") or ""))
    )
    try:
        raw = call_model_route(
            route_key="timing_proactive",
            system_prompt=prompt,
            user_message=grounding_text,
            max_tokens=OUTREACH_JUDGE_MAX_TOKENS,
            temperature=0,
        )
        data = _extract_json_object(raw)
        candidate = _parse_next_check_candidate(data, now=current)
        next_check_at = _clamp_next_check_at(
            candidate,
            now=current,
            min_interval_min=min_interval_min,
            max_check_interval_min=max_check_interval_min,
        )
        return {
            "should_reach_out": bool(data.get("should_reach_out", False)),
            "reason": str(data.get("reason") or "")[:500],
            "next_check_at": next_check_at.isoformat(),
            "next_intent": str(data.get("next_intent") or "")[:500],
            "raw": str(raw)[:1000],
            "error_type": None,
        }
    except Exception as exc:
        next_check_at = _clamp_next_check_at(
            None,
            now=current,
            min_interval_min=min_interval_min,
            max_check_interval_min=max_check_interval_min,
        )
        return {
            "should_reach_out": False,
            "reason": f"主动外呼 Judge 不可用: {exc}",
            "next_check_at": next_check_at.isoformat(),
            "next_intent": str(grounding.get("next_intent") or "")[:500],
            "raw": "",
            "error_type": "model_error",
        }


def generate_outreach_message(grounding: dict[str, Any], reason: str) -> str:
    """生成主动外呼 DM 正文。"""

    grounding_text = _grounding_json(grounding)
    prompt = OUTREACH_GENERATOR_PROMPT.format(
        grounding_json=grounding_text,
        reason=reason,
    )
    raw = call_model_route(
        route_key="reply",
        system_prompt=prompt,
        user_message=grounding_text,
        max_tokens=OUTREACH_GENERATOR_MAX_TOKENS,
        temperature=0.7,
    )
    return strip_think_blocks(raw).strip()


async def deliver_outreach_once(
    *,
    user_id: str,
    idempotency_key: str,
    grounding: dict[str, Any],
    judge_should: bool,
    judge_reason: str,
    next_check_at: datetime | None,
    next_intent: str,
    message: str,
    forced: bool,
    db: Session | None = None,
    schedule_row_id: int | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """幂等发送一条主动外呼 DM，并记录状态机。"""

    with _session_scope(db) as session:
        existing = (
            session.query(ProactiveOutreachLog)
            .filter(ProactiveOutreachLog.idempotency_key == idempotency_key)
            .first()
        )
        if existing is not None and existing.status != "pending":
            return {
                "status": "skipped_duplicate",
                "log_id": existing.id,
                "forced": bool(existing.forced),
            }

        row = existing
        if row is None and schedule_row_id is not None:
            candidate = session.get(ProactiveOutreachLog, schedule_row_id)
            if (
                candidate is not None
                and candidate.user_id == user_id
                and candidate.status == "pending"
            ):
                row = candidate

        if row is None:
            row = ProactiveOutreachLog(user_id=user_id, status="pending")
            session.add(row)

        row.idempotency_key = idempotency_key
        row.grounding_json = _grounding_json(grounding)
        row.judge_should = judge_should
        row.judge_reason = judge_reason
        row.next_check_at = next_check_at
        row.next_intent = next_intent
        row.message = message
        row.forced = forced
        row.created_at = created_at or row.created_at or datetime.now()
        row.status = "sending"
        session.commit()
        session.refresh(row)

        try:
            ok = await push_to_qq("private", user_id, message)
        except Exception:
            row.status = "failed"
            session.commit()
            raise
        row.status = "sent" if ok else "failed"
        session.commit()
        session.refresh(row)

        return {
            "status": row.status,
            "log_id": row.id,
            "forced": bool(row.forced),
        }


def _current_pending_schedule(session: Session, user_id: str) -> ProactiveOutreachLog | None:
    return (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .filter(ProactiveOutreachLog.status == "pending")
        .order_by(ProactiveOutreachLog.created_at.desc(), ProactiveOutreachLog.id.desc())
        .first()
    )


def _current_schedule_row(session: Session, user_id: str) -> ProactiveOutreachLog | None:
    return (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .filter(ProactiveOutreachLog.status.in_(("pending", "sending")))
        .order_by(ProactiveOutreachLog.created_at.desc(), ProactiveOutreachLog.id.desc())
        .first()
    )


def _last_sent_outreach(session: Session, user_id: str) -> ProactiveOutreachLog | None:
    return (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .filter(ProactiveOutreachLog.status == "sent")
        .order_by(ProactiveOutreachLog.created_at.desc(), ProactiveOutreachLog.id.desc())
        .first()
    )


def _last_effective_outreach(session: Session, user_id: str) -> ProactiveOutreachLog | None:
    return (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .filter(ProactiveOutreachLog.status.in_(("sent", "sending")))
        .order_by(ProactiveOutreachLog.created_at.desc(), ProactiveOutreachLog.id.desc())
        .first()
    )


def _first_outreach_attempt(session: Session, user_id: str) -> ProactiveOutreachLog | None:
    return (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .order_by(ProactiveOutreachLog.created_at.asc(), ProactiveOutreachLog.id.asc())
        .first()
    )


def _silence_anchor(
    session: Session,
    user_id: str,
    *,
    last_effective: ProactiveOutreachLog | None = None,
) -> ProactiveOutreachLog | None:
    return last_effective if last_effective is not None else _first_outreach_attempt(session, user_id)


def _silence_floor_hit(
    session: Session,
    user_id: str,
    *,
    current: datetime,
    max_silence_min: int,
    last_sent: ProactiveOutreachLog | None = None,
) -> bool:
    anchor = _silence_anchor(session, user_id, last_effective=last_sent)
    return (
        anchor is not None
        and anchor.created_at is not None
        and current - anchor.created_at >= timedelta(minutes=max_silence_min)
    )


def _latest_next_check_at(session: Session, user_id: str) -> datetime | None:
    current_schedule = _current_schedule_row(session, user_id)
    if current_schedule is not None and current_schedule.next_check_at is not None:
        return current_schedule.next_check_at
    row = (
        session.query(ProactiveOutreachLog.next_check_at)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .filter(ProactiveOutreachLog.next_check_at.isnot(None))
        .order_by(ProactiveOutreachLog.created_at.desc(), ProactiveOutreachLog.id.desc())
        .first()
    )
    return row[0] if row and row[0] is not None else None


def _last_interaction_at(session: Session, user_id: str) -> datetime | None:
    row = (
        session.query(ChatLog.created_at)
        .filter(ChatLog.user_id == user_id)
        .filter(ChatLog.role == "user")
        .filter(ChatLog.created_at.isnot(None))
        .order_by(ChatLog.created_at.desc(), ChatLog.id.desc())
        .first()
    )
    return row[0] if row and row[0] is not None else None


def _surge_probability(
    *,
    last_interaction_at: datetime | None,
    now: datetime,
    min_prob: float,
    max_prob: float,
    ramp_minutes: int,
) -> float:
    lower = max(0.0, min(1.0, float(min_prob)))
    upper = max(0.0, min(1.0, float(max_prob)))
    if upper < lower:
        lower, upper = upper, lower
    if ramp_minutes <= 0 or last_interaction_at is None:
        return upper
    elapsed_min = max(0.0, (now - last_interaction_at).total_seconds() / 60.0)
    ratio = min(1.0, elapsed_min / float(ramp_minutes))
    return lower + (upper - lower) * ratio


def _upsert_pending_schedule(
    session: Session,
    *,
    user_id: str,
    idempotency_key: str,
    grounding: dict[str, Any],
    judge_reason: str,
    next_check_at: datetime | None,
    next_intent: str,
) -> ProactiveOutreachLog:
    row = _current_pending_schedule(session, user_id)
    if row is None:
        row = ProactiveOutreachLog(user_id=user_id, status="pending")
        session.add(row)
    row.idempotency_key = idempotency_key
    row.grounding_json = _grounding_json(grounding)
    row.judge_should = False
    row.judge_reason = judge_reason
    row.next_check_at = next_check_at
    row.next_intent = next_intent
    row.message = ""
    row.forced = False
    row.status = "pending"
    session.commit()
    session.refresh(row)
    return row


async def run_outreach_once(
    user_id: str,
    *,
    db: Session | None = None,
    now: datetime | None = None,
    min_interval_min: int = DEFAULT_MIN_INTERVAL_MIN,
    max_check_interval_min: int = DEFAULT_MAX_CHECK_INTERVAL_MIN,
    max_silence_min: int = DEFAULT_MAX_SILENCE_MIN,
) -> dict[str, Any]:
    """执行一次主动外呼检查；超过最长沉默窗口时强制开口。"""

    current = now or datetime.now()
    with _session_scope(db) as session:
        last_effective = _last_effective_outreach(session, user_id)
        force = _silence_floor_hit(
            session,
            user_id,
            current=current,
            max_silence_min=max_silence_min,
            last_sent=last_effective,
        )
        if last_effective is not None and last_effective.created_at is not None and not force:
            elapsed = current - last_effective.created_at
            if timedelta(0) <= elapsed < timedelta(minutes=min_interval_min):
                return {
                    "status": "skipped_min_interval",
                    "minutes_since_last": int(elapsed.total_seconds() // 60),
                }

        schedule_row = _current_schedule_row(session, user_id)
        if schedule_row is not None and schedule_row.status == "sending" and not force:
            return {
                "status": "skipped_duplicate",
                "log_id": schedule_row.id,
                "forced": bool(schedule_row.forced),
            }
        latest_next_check_at = _latest_next_check_at(session, user_id)
        schedule_anchor = (
            schedule_row.next_check_at
            if schedule_row is not None and schedule_row.next_check_at is not None
            else latest_next_check_at
        ) or current
        grounding = build_outreach_grounding(user_id, db=session)

        if force:
            reason = "超过最长沉默窗口，主动问候一次"
            message = generate_outreach_message(grounding, reason)
            anchor = _silence_anchor(session, user_id, last_effective=last_effective)
            force_anchor = anchor.created_at if anchor is not None and anchor.created_at is not None else current
            return await deliver_outreach_once(
                user_id=user_id,
                idempotency_key=_outreach_key(user_id, force_anchor, forced=True),
                grounding=grounding,
                judge_should=True,
                judge_reason=reason,
                next_check_at=current + timedelta(minutes=max(1, min_interval_min)),
                next_intent="",
                message=message,
                forced=True,
                db=session,
                created_at=current,
            )

        judge = judge_outreach(
            grounding,
            now=current,
            min_interval_min=min_interval_min,
            max_check_interval_min=max_check_interval_min,
        )
        next_check_at = _parse_iso_datetime(judge.get("next_check_at"))
        if not judge.get("should_reach_out"):
            row = _upsert_pending_schedule(
                session,
                user_id=user_id,
                idempotency_key=_outreach_key(user_id, next_check_at or schedule_anchor, forced=False),
                grounding=grounding,
                judge_reason=str(judge.get("reason") or ""),
                next_check_at=next_check_at,
                next_intent=str(judge.get("next_intent") or ""),
            )
            return {"status": "pending", "forced": False, "log_id": row.id}

        reason = str(judge.get("reason") or "")
        message = generate_outreach_message(grounding, reason)
        return await deliver_outreach_once(
            user_id=user_id,
            idempotency_key=_outreach_key(user_id, schedule_anchor, forced=False),
            grounding=grounding,
            judge_should=True,
            judge_reason=reason,
            next_check_at=next_check_at,
            next_intent=str(judge.get("next_intent") or ""),
            message=message,
            forced=False,
            db=session,
            schedule_row_id=(
                schedule_row.id
                if schedule_row is not None and schedule_row.status == "pending"
                else None
            ),
            created_at=current,
        )


async def run_outreach_due_once(
    user_id: str,
    *,
    db: Session | None = None,
    now: datetime | None = None,
    min_interval_min: int | None = None,
    max_check_interval_min: int | None = None,
    max_silence_min: int | None = None,
    surge_min_prob: float | None = None,
    surge_max_prob: float | None = None,
    random_fn: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """执行一次到期外呼检查；安静时段直接跳过。"""

    current = now or datetime.now()
    hours = active_hours(user_id, db=db)
    if current.hour not in hours:
        return {"status": "skipped_quiet_hours", "hour": current.hour}

    effective_max_silence_min = (
        max_silence_min if max_silence_min is not None else DEFAULT_MAX_SILENCE_MIN
    )
    with _session_scope(db) as session:
        last_effective = _last_effective_outreach(session, user_id)
        silence_floor_hit = _silence_floor_hit(
            session,
            user_id,
            current=current,
            max_silence_min=effective_max_silence_min,
            last_sent=last_effective,
        )
        next_check_at = _latest_next_check_at(session, user_id)
        if not silence_floor_hit and next_check_at is not None and next_check_at > current:
            probability = _surge_probability(
                last_interaction_at=_last_interaction_at(session, user_id),
                now=current,
                min_prob=surge_min_prob
                if surge_min_prob is not None
                else DEFAULT_SURGE_MIN_PROB,
                max_prob=surge_max_prob
                if surge_max_prob is not None
                else DEFAULT_SURGE_MAX_PROB,
                ramp_minutes=effective_max_silence_min,
            )
            roll = (random_fn or random.random)()
            if roll >= probability:
                return {
                    "status": "skipped_not_due",
                    "next_check_at": next_check_at.isoformat(),
                    "surge_probability": probability,
                    "surge_roll": roll,
                }

    return await run_outreach_once(
        user_id,
        db=db,
        now=current,
        min_interval_min=min_interval_min
        if min_interval_min is not None
        else DEFAULT_MIN_INTERVAL_MIN,
        max_check_interval_min=max_check_interval_min
        if max_check_interval_min is not None
        else DEFAULT_MAX_CHECK_INTERVAL_MIN,
        max_silence_min=max_silence_min
        if max_silence_min is not None
        else effective_max_silence_min,
    )


def proactive_outreach_scheduler(stop_event: threading.Event) -> None:
    """后台主动外呼调度器。"""

    logger.info("Proactive outreach scheduler started.")
    while not stop_event.is_set():
        try:
            user_ids = sorted(_configured_super_user_ids())
            if not user_ids:
                logger.info("Proactive outreach skipped: no superuser configured.")
            for user_id in user_ids:
                if stop_event.is_set():
                    break
                run_awaitable_sync(run_outreach_due_once(
                    user_id,
                    min_interval_min=settings.get_int(
                        "proactive_outreach.min_interval_min",
                        DEFAULT_MIN_INTERVAL_MIN,
                    ),
                    max_check_interval_min=settings.get_int(
                        "proactive_outreach.max_check_interval_min",
                        DEFAULT_MAX_CHECK_INTERVAL_MIN,
                    ),
                    max_silence_min=settings.get_int(
                        "proactive_outreach.max_silence_min",
                        DEFAULT_MAX_SILENCE_MIN,
                    ),
                    surge_min_prob=settings.get_float(
                        "proactive_outreach.surge_min_prob",
                        DEFAULT_SURGE_MIN_PROB,
                    ),
                    surge_max_prob=settings.get_float(
                        "proactive_outreach.surge_max_prob",
                        DEFAULT_SURGE_MAX_PROB,
                    ),
                ))
        except Exception as exc:
            logger.exception("Proactive outreach scheduler error: %s", exc)

        interval_min = settings.get_int("proactive_outreach.fallback_interval_min", 120)
        stop_event.wait(timeout=max(60, interval_min * 60))

    logger.info("Proactive outreach scheduler stopped.")

"""主动情感外呼运行时基础能力。"""

from __future__ import annotations

import json
import hashlib
import inspect
import logging
import math
import os
import random
import secrets
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, exists, or_, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from clients.classifier_client import (
    ModelRouteResponse,
    call_model_route_response,
    strip_think_blocks,
)
from core.async_bridge import run_awaitable_sync
from core import outbound_delivery
from core.message_envelope import build_chat_response_envelope
from core.proactive_diagnostics import (
    OutreachModelContractError,
    generation_failure_from_exception,
    judgement_failure_for_type,
)
from core.proactive_candidate import (
    DEFAULT_ACTIVE_HOURS,
    DEFAULT_MAX_CHECK_INTERVAL_MIN,
    DEFAULT_MAX_SILENCE_MIN,
    DEFAULT_MIN_INTERVAL_MIN,
    DEFAULT_SURGE_MAX_PROB,
    DEFAULT_SURGE_MIN_PROB,
    calculate_outreach_surge_probability,
    evaluate_outreach_due_gate,
    evaluate_outreach_min_interval_gate,
)
from core.database import (
    ConversationTurn,
    Persona,
    ProactiveOutreachLease,
    ProactiveOutreachLog,
    SessionLocal,
    User,
)
from core.identity import get_super_user_ids
from core.outbound_delivery_service import (
    CustomTransportConfig,
    LegacyWriterLeaseSnapshot,
    LegacyWriterTakeover,
    OutboundDeliveryWorkResult,
    OutboundTransport,
    OutboundWorkerConfig,
    deliver_legacy_outbound_once,
    snapshot_live_legacy_writer,
)
from core.settings_service import settings
from core.sqlite_retry import run_sqlite_locked_retry

ACTIVE_HOURS_MIN_SAMPLES = 5
DEFAULT_EVALUATION_LEASE_SECONDS = 900
DEFAULT_SENDING_AMBIGUITY_MINUTES = 30
DEFAULT_AMBIGUOUS_HOLD_MIN = 120
OUTREACH_LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
FORCED_FALLBACK_MESSAGE = (
    "突然想起你了。最近过得怎么样？有空的话，和我说说这几天吧。"
)
ACTIVE_HOUR_WINDOW_RADIUS = 1
KNOWN_OUTREACH_STATES = frozenset({
    "pending",
    "candidate",
    "sending",
    "queued",
    "delivering",
    "retry_wait",
    "sent",
    "sent_after_ambiguous_replay",
    "failed",
    "blocked",
    "evaluation_error",
    "ambiguous",
    "cancelled",
    "legacy_ambiguous_hold",
})
OUTREACH_SOURCE_TYPE = "proactive_outreach"
OUTREACH_ENDPOINT_KEY = "qq_push"
OUTREACH_PAYLOAD_CONTRACT = "qq-envelope-v1"
OUTREACH_CLAIM_LEASE_SECONDS = 900.0
OUTREACH_WRITER_LEASE_SECONDS = 900.0
OUTREACH_DEFAULT_MAX_ATTEMPTS = 3
OUTREACH_DEFAULT_RETRY_DEADLINE_SECONDS = 86400.0
OUTREACH_SCHEDULER_POLL_SECONDS = 60.0
_OUTREACH_PROCESS_OWNER = (
    f"proactive-outreach:{os.getpid()}:{secrets.token_hex(16)}"
)[:128]
_OUTREACH_WRITER_TOKEN = secrets.token_hex(32)
MODEL_GROUNDING_RECENT_MESSAGE_LIMIT = 8
MODEL_GROUNDING_TEXT_LIMIT = 480
MODEL_GROUNDING_MESSAGE_TEXT_LIMIT = 240
logger = logging.getLogger("nanobot.proactive_outreach")
# 兼容现有调用方显式注入旧三态 publisher；生产默认保持为空并走结构化 transport。
push_to_qq: Callable[[str, str, str], Any] | None = None


@dataclass(frozen=True, slots=True)
class _GeneratedOutreachWork:
    log_id: int
    run_id: int
    generation_attempt_id: int
    owner: str
    claim_token: str
    delivery_mode: str
    idempotency_key: str
    input_grounding: dict[str, Any]
    persisted_grounding: dict[str, Any]
    judge: dict[str, Any]
    kind: str
    forced: bool
    created_at: datetime
    source_revision: str
    destination_snapshot: dict[str, Any]
    destination_fingerprint: str
    endpoint_revision: str

RECENT_THREADS_PROMPT = """从最近对话中提炼主动外呼可自然跟进的点。

输入是 JSON 数组，每项含 role/content/created_at。
只提炼 1-3 个"未完话题 / 近期事件 / 可自然跟进的点"。
不要泛泛总结，不要编造对话里没有的事。

输出 JSON 数组，例如:["接口联调卡住，晚点继续看","晚上可能去夜跑"]。"""

OUTREACH_JUDGE_PROMPT = """你在判断:此刻是否值得主动给你最亲密的朋友发一条消息。

你会在用户消息中收到一份精简 grounding JSON。
优先看 recent_threads、recent_threads_diagnostics、now、hours_since_last_user_message、last_user_message、days_since_last_outreach、next_intent、last_outreach；原始 recent_messages 只作补充。recent_threads_diagnostics.status=error 表示提炼失败，不等于用户没有可跟进内容。

原则:
- 你是主动的一方,像真人朋友——想到了就找 ta,不必等 ta 开口
- 优先围绕 recent_threads 里的近期可跟进点判断,再结合 persona 和 last_user_message
- 有具体的、扎根上下文的话题才发(ta 提过的事、你注意到的近况)
- 刚聊过(hours_since_last_user_message 很小)、深夜、没有真正话题只想刷存在感时,倾向不发
- 发完或不发都自己决定几小时后再考虑(next_check_in_hours)
- 普通跟进选择 outreach_kind=message；明确值得先查资料再分享时选择 research
- 选择 research 时 research_query 必须具体、可搜索，且扎根当前 grounding

输出 JSON:{"should_reach_out": bool, "reason": str, "next_check_in_hours": number, "next_intent": str, "outreach_kind": "message|research", "research_query": str}"""

OUTREACH_GENERATOR_PROMPT = """你是 nanobot,要给最亲密的朋友主动发一条消息。

你会在用户消息中收到一份精简 grounding JSON。
理由:{reason}

要求:
- 优先从 recent_threads 或 persona 里挑一个具体锚点展开,不要泛泛问候；若 recent_threads_diagnostics.status=error，只能改用最近消息或 persona 中已有的事实
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


def _acquire_evaluation_lease(
    session: Session,
    *,
    user_id: str,
    now: datetime,
    lease_seconds: int = DEFAULT_EVALUATION_LEASE_SECONDS,
) -> str | None:
    owner_token = secrets.token_hex(32)

    def operation() -> str | None:
        try:
            # 先取得 SQLite 写锁，再冻结租约时刻；否则锁等待会直接吞掉租期。
            session.execute(text("BEGIN IMMEDIATE"))
            attempt_now = max(now, datetime.now())
            lease_expires_at = attempt_now + timedelta(
                seconds=max(1, int(lease_seconds))
            )
            insert_statement = (
                sqlite_insert(ProactiveOutreachLease)
                .values(
                    user_id=user_id,
                    owner_token=owner_token,
                    lease_expires_at=lease_expires_at,
                    created_at=attempt_now,
                    updated_at=attempt_now,
                )
                .on_conflict_do_nothing(index_elements=["user_id"])
            )
            takeover_statement = (
                update(ProactiveOutreachLease)
                .where(
                    ProactiveOutreachLease.user_id == user_id,
                    ProactiveOutreachLease.lease_expires_at <= attempt_now,
                )
                .values(
                    owner_token=owner_token,
                    lease_expires_at=lease_expires_at,
                    updated_at=attempt_now,
                )
            )
            acquired = session.execute(insert_statement).rowcount == 1
            if not acquired:
                acquired = session.execute(takeover_statement).rowcount == 1
            if not acquired:
                session.rollback()
                return None
            session.commit()
            return owner_token
        except BaseException:
            session.rollback()
            raise

    return run_sqlite_locked_retry(
        operation,
        rollback=session.rollback,
        label="acquire proactive outreach lease",
    )


def _release_evaluation_lease(
    session: Session,
    *,
    user_id: str,
    owner_token: str,
) -> None:
    statement = delete(ProactiveOutreachLease).where(
        ProactiveOutreachLease.user_id == user_id,
        ProactiveOutreachLease.owner_token == owner_token,
    )

    def operation() -> None:
        try:
            session.execute(statement)
            session.commit()
        except BaseException:
            session.rollback()
            raise

    run_sqlite_locked_retry(
        operation,
        rollback=session.rollback,
        label="release proactive outreach lease",
    )


def _evaluation_lease_is_owned(
    session: Session,
    *,
    user_id: str,
    owner_token: str,
    now: datetime | None = None,
) -> bool:
    current = now or datetime.now()
    return bool(
        session.query(ProactiveOutreachLease.user_id)
        .filter(ProactiveOutreachLease.user_id == user_id)
        .filter(ProactiveOutreachLease.owner_token == owner_token)
        .filter(ProactiveOutreachLease.lease_expires_at > current)
        .first()
    )


def _fence_evaluation_write(
    session: Session,
    *,
    user_id: str,
    owner_token: str | None,
) -> bool:
    """在同一写事务中确认评估租约，阻止旧 owner 持久化评估产物。"""

    if owner_token is None:
        return True

    def operation() -> bool:
        fence_at = datetime.now()
        matched = (
            session.query(ProactiveOutreachLease)
            .filter(ProactiveOutreachLease.user_id == user_id)
            .filter(ProactiveOutreachLease.owner_token == owner_token)
            .filter(ProactiveOutreachLease.lease_expires_at > fence_at)
            .update(
                {ProactiveOutreachLease.updated_at: fence_at},
                synchronize_session=False,
            )
        )
        return matched == 1

    return bool(
        run_sqlite_locked_retry(
            operation,
            rollback=session.rollback,
            label="fence proactive outreach evaluation write",
        )
    )


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


def _coerce_model_response(value: Any) -> ModelRouteResponse:
    if isinstance(value, ModelRouteResponse):
        return value
    # 仅兼容显式注入的旧字符串 caller；生产客户端始终返回带停止原因的结构体。
    return ModelRouteResponse(
        content=strip_think_blocks(str(value or "")),
        reasoning_content="",
        finish_reason="stop",
        usage={},
        raw_response={},
    )


def _model_contract_error(
    response: ModelRouteResponse,
    *,
    now: datetime,
    min_interval_min: int,
    max_check_interval_min: int,
    error_type: str,
    reason: str,
) -> dict[str, Any]:
    next_check_at = _clamp_next_check_at(
        None,
        now=now,
        min_interval_min=min_interval_min,
        max_check_interval_min=max_check_interval_min,
    )
    return {
        "should_reach_out": None,
        "reason": reason[:500],
        "next_check_at": next_check_at.isoformat(),
        "next_intent": "",
        "outreach_kind": "message",
        "research_query": "",
        "raw": response.content[:1000],
        "reasoning_content": response.reasoning_content[:1000],
        "finish_reason": response.finish_reason,
        "usage": response.usage,
        "error_type": error_type,
    }


def _parse_outreach_judge_contract(
    response: ModelRouteResponse,
    *,
    now: datetime,
    min_interval_min: int,
    max_check_interval_min: int,
) -> dict[str, Any]:
    finish_reason = (response.finish_reason or "").strip().lower()
    if finish_reason != "stop":
        error_type = "model_truncated" if finish_reason == "length" else "model_finish_error"
        finish_label = finish_reason or "<missing>"
        return _model_contract_error(
            response,
            now=now,
            min_interval_min=min_interval_min,
            max_check_interval_min=max_check_interval_min,
            error_type=error_type,
            reason=f"主动外呼 Judge 非正常结束: {finish_label}",
        )

    cleaned = strip_think_blocks(response.content or "").strip()
    if not cleaned:
        return _model_contract_error(
            response,
            now=now,
            min_interval_min=min_interval_min,
            max_check_interval_min=max_check_interval_min,
            error_type="empty_response",
            reason="主动外呼 Judge 返回空正文",
        )
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return _model_contract_error(
            response,
            now=now,
            min_interval_min=min_interval_min,
            max_check_interval_min=max_check_interval_min,
            error_type="contract_error",
            reason="主动外呼 Judge 返回的 JSON 不完整或无效",
        )
    if not isinstance(data, dict):
        return _model_contract_error(
            response,
            now=now,
            min_interval_min=min_interval_min,
            max_check_interval_min=max_check_interval_min,
            error_type="contract_error",
            reason="主动外呼 Judge 根节点必须是 JSON 对象",
        )

    required_fields = {
        "should_reach_out",
        "reason",
        "next_intent",
        "outreach_kind",
        "research_query",
    }
    if not required_fields.issubset(data):
        return _model_contract_error(
            response,
            now=now,
            min_interval_min=min_interval_min,
            max_check_interval_min=max_check_interval_min,
            error_type="contract_error",
            reason="主动外呼 Judge 缺少必需字段",
        )

    should_reach_out = data.get("should_reach_out")
    reason = data.get("reason")
    next_intent = data.get("next_intent")
    if (
        not isinstance(should_reach_out, bool)
        or not isinstance(reason, str)
        or not isinstance(next_intent, str)
    ):
        return _model_contract_error(
            response,
            now=now,
            min_interval_min=min_interval_min,
            max_check_interval_min=max_check_interval_min,
            error_type="contract_error",
            reason="主动外呼 Judge 字段类型不符合契约",
        )

    candidate: datetime | None = None
    if "next_check_in_hours" in data:
        hours_value = data.get("next_check_in_hours")
        if type(hours_value) in (int, float):
            hours = float(hours_value)
            if math.isfinite(hours) and hours > 0:
                candidate = now + timedelta(hours=hours)
    elif "next_check_at" in data:
        next_check_at_value = data.get("next_check_at")
        if isinstance(next_check_at_value, str):
            candidate = _parse_iso_datetime(next_check_at_value)
    if candidate is None:
        return _model_contract_error(
            response,
            now=now,
            min_interval_min=min_interval_min,
            max_check_interval_min=max_check_interval_min,
            error_type="contract_error",
            reason="主动外呼 Judge 缺少有效的下次检查时间",
        )

    outreach_kind_value = data.get("outreach_kind")
    research_query = data.get("research_query")
    outreach_kind = (
        outreach_kind_value.strip().lower()
        if isinstance(outreach_kind_value, str)
        else ""
    )
    if outreach_kind not in {"message", "research"} or not isinstance(research_query, str):
        return _model_contract_error(
            response,
            now=now,
            min_interval_min=min_interval_min,
            max_check_interval_min=max_check_interval_min,
            error_type="contract_error",
            reason="主动外呼 Judge 的 outreach_kind/research_query 不符合契约",
        )
    if should_reach_out and outreach_kind == "research" and not research_query.strip():
        return _model_contract_error(
            response,
            now=now,
            min_interval_min=min_interval_min,
            max_check_interval_min=max_check_interval_min,
            error_type="contract_error",
            reason="研究型主动外呼缺少 research_query",
        )

    next_check_at = _clamp_next_check_at(
        candidate,
        now=now,
        min_interval_min=min_interval_min,
        max_check_interval_min=max_check_interval_min,
    )
    return {
        "should_reach_out": should_reach_out,
        "reason": reason[:500],
        "next_check_at": next_check_at.isoformat(),
        "next_intent": next_intent[:500],
        "outreach_kind": outreach_kind,
        "research_query": research_query.strip()[:1000],
        "raw": response.content[:1000],
        "reasoning_content": response.reasoning_content[:1000],
        "finish_reason": response.finish_reason,
        "usage": response.usage,
        "error_type": None,
    }


def _grounding_json(grounding: dict[str, Any]) -> str:
    return json.dumps(grounding, ensure_ascii=False, default=str)


def _compact_model_value(
    value: Any,
    *,
    max_chars: int = MODEL_GROUNDING_TEXT_LIMIT,
    max_items: int = 12,
    depth: int = 0,
) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        if depth >= 4:
            return _truncate_text(_grounding_json(value), max_chars)
        return {
            str(key): _compact_model_value(
                nested,
                max_chars=max_chars,
                max_items=max_items,
                depth=depth + 1,
            )
            for key, nested in list(value.items())[:max_items]
        }
    if isinstance(value, list):
        return [
            _compact_model_value(
                item,
                max_chars=max_chars,
                max_items=max_items,
                depth=depth + 1,
            )
            for item in value[:max_items]
        ]
    return _truncate_text(str(value), max_chars)


def _compact_recent_message_for_model(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"content": _truncate_text(str(item), MODEL_GROUNDING_MESSAGE_TEXT_LIMIT)}
    compact: dict[str, Any] = {}
    for key in ("role", "content", "created_at", "sender_name"):
        if key not in item:
            continue
        limit = MODEL_GROUNDING_MESSAGE_TEXT_LIMIT if key == "content" else MODEL_GROUNDING_TEXT_LIMIT
        compact[key] = _compact_model_value(item.get(key), max_chars=limit)
    return compact


def _grounding_json_for_model(grounding: dict[str, Any]) -> str:
    compact: dict[str, Any] = {}
    for key, value in grounding.items():
        if key == "recent_messages":
            continue
        limit = MODEL_GROUNDING_MESSAGE_TEXT_LIMIT if key in {"last_user_message", "last_outreach"} else MODEL_GROUNDING_TEXT_LIMIT
        compact[key] = _compact_model_value(value, max_chars=limit)

    recent_messages = grounding.get("recent_messages")
    if isinstance(recent_messages, list) and recent_messages:
        compact["recent_messages"] = [
            _compact_recent_message_for_model(item)
            for item in recent_messages[-MODEL_GROUNDING_RECENT_MESSAGE_LIMIT:]
        ]
    return _grounding_json(compact)


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


def _outreach_key(
    user_id: str,
    anchor: datetime,
    *,
    forced: bool = False,
    attempt: int = 1,
) -> str:
    operation = f"forced:{max(1, int(attempt))}" if forced else "judge"
    raw = f"{user_id}:{_key_anchor_text(anchor)}:{operation}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"outreach:{user_id}:{digest}"


def extract_recent_threads(
    recent_messages: list[dict[str, Any]],
    *,
    llm_call: Callable[..., Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> list[str]:
    """从最近对话中提炼可自然跟进的话题；失败时降级为空。"""

    def record(**values: Any) -> None:
        if diagnostics is None:
            return
        diagnostics.clear()
        diagnostics.update(values)

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
        record(status="empty_input")
        return []
    if llm_call is None and os.environ.get("NANOBOT_TESTING") == "1":
        record(status="skipped", reason="testing")
        return []

    caller = llm_call or call_model_route_response
    try:
        response = _coerce_model_response(caller(
            route_key="outreach_extract",
            system_prompt=RECENT_THREADS_PROMPT,
            user_message=json.dumps(compact_messages, ensure_ascii=False),
        ))
        if response.finish_reason not in (None, "stop"):
            record(
                status="error",
                error_type=(
                    "model_truncated"
                    if response.finish_reason == "length"
                    else "model_finish_error"
                ),
                finish_reason=response.finish_reason,
            )
            return []
        raw = response.content
    except Exception:
        record(
            status="error",
            error_type="model_error",
        )
        return []

    cleaned = strip_think_blocks(raw or "").strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, list):
        record(
            status="error",
            error_type="contract_error",
            finish_reason=response.finish_reason,
        )
        return []

    threads: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        text = _truncate_text(str(item), 120)
        if text and text not in seen:
            seen.add(text)
            threads.append(text)
        if len(threads) >= 3:
            break
    record(
        status="success",
        finish_reason=response.finish_reason,
        thread_count=len(threads),
    )
    return threads


def _history_clear_at(session: Session, user_id: str) -> datetime | None:
    row = session.query(User.history_clear_at).filter(User.id == user_id).first()
    return row[0] if row and row[0] is not None else None


def _cancelled_row_is_reusable(
    session: Session,
    *,
    row: ProactiveOutreachLog | None,
    user_id: str,
    generation_at: datetime,
) -> bool:
    """只让新的合法评估代际复用从未发布的 cancelled 行。"""

    if (
        row is None
        or row.user_id != user_id
        or row.status != "cancelled"
        or row.outbound_run_id is not None
    ):
        return False
    clear_at = _history_clear_at(session, user_id)
    return clear_at is None or generation_at > clear_at


def _post_clear_pending_key(
    *,
    user_id: str,
    idempotency_key: str,
    clear_at: datetime,
) -> str:
    """为历史清除后的同 key 调度派生稳定且不覆盖旧审计的业务键。"""

    raw = (
        f"{user_id}\0{idempotency_key}\0"
        f"{clear_at.isoformat(timespec='microseconds')}\0post-clear-pending"
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"outreach:{user_id}:{digest}"


def _private_conversation_query(
    session: Session,
    user_id: str,
    *,
    roles: tuple[str, ...],
):
    query = (
        session.query(ConversationTurn)
        .filter(ConversationTurn.user_id == user_id)
        .filter(ConversationTurn.session_id.like(r"private\_%", escape="\\"))
        .filter(ConversationTurn.role.in_(roles))
        .filter(ConversationTurn.created_at.isnot(None))
    )
    clear_at = _history_clear_at(session, user_id)
    if clear_at is not None:
        query = query.filter(ConversationTurn.created_at > clear_at)
    return query


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
            _private_conversation_query(
                session,
                user_id,
                roles=("user", "assistant", "model"),
            )
            .order_by(ConversationTurn.created_at.desc(), ConversationTurn.id.desc())
            .limit(max(1, int(recent_limit)))
            .all()
        )
        recent_messages = [
            {
                "role": "assistant" if row.role == "model" else (row.role or ""),
                "content": row.content or "",
                "created_at": _iso_or_empty(row.created_at),
                "session_id": row.session_id or "",
                "sender_name": "",
            }
            for row in reversed(recent_rows)
        ]
        last_user_row = (
            _private_conversation_query(session, user_id, roles=("user",))
            .order_by(ConversationTurn.created_at.desc(), ConversationTurn.id.desc())
            .first()
        )

        outreach_state_query = session.query(ProactiveOutreachLog).filter(
            ProactiveOutreachLog.user_id == user_id
        )
        clear_at = _history_clear_at(session, user_id)
        if clear_at is not None:
            outreach_state_query = outreach_state_query.filter(
                ProactiveOutreachLog.created_at > clear_at
            )
        latest_outreach_state = outreach_state_query.order_by(
            ProactiveOutreachLog.created_at.desc(),
            ProactiveOutreachLog.id.desc(),
        ).first()
        last_outreach = outreach_state_query.filter(
            ProactiveOutreachLog.status.in_(("sent", "sending"))
        ).order_by(
            ProactiveOutreachLog.created_at.desc(),
            ProactiveOutreachLog.id.desc(),
        ).first()
        last_user_hours = _hours_since(current, last_user_row.created_at if last_user_row else None)
        last_outreach_hours = _hours_since(
            current,
            last_outreach.created_at if last_outreach else None,
        )
        # TODO: personas 当前偏稳定画像；未来可在画像生成链路增加
        # recent_event / open_thread 时效性 fact，减少实时 recent_threads 提炼依赖。
        recent_thread_diagnostics: dict[str, Any] = {}
        if thread_extractor is not None:
            recent_threads = thread_extractor(recent_messages)
            recent_thread_diagnostics.update({
                "status": "success",
                "source": "custom",
                "thread_count": len(recent_threads),
            })
        else:
            recent_threads = extract_recent_threads(
                recent_messages,
                diagnostics=recent_thread_diagnostics,
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
            "recent_threads_diagnostics": recent_thread_diagnostics,
            "hours_since_last_user_message": last_user_hours,
            "last_user_message": {
                "content": _truncate_text(last_user_row.content if last_user_row else ""),
                "created_at": _iso_or_empty(last_user_row.created_at if last_user_row else None),
                "hours_ago": last_user_hours,
            } if last_user_row else None,
            "days_since_last_outreach": (
                last_outreach_hours / 24.0 if last_outreach_hours is not None else None
            ),
            "next_intent": (
                latest_outreach_state.next_intent
                if latest_outreach_state is not None
                else ""
            ),
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
            _private_conversation_query(session, user_id, roles=("user",))
            .with_entities(ConversationTurn.created_at)
            .all()
        )

    sample_hours = [row[0].hour for row in rows if row[0] is not None]
    if len(sample_hours) < min_samples:
        return set(DEFAULT_ACTIVE_HOURS)
    observed_hours = set(sample_hours)
    return {
        (hour + offset) % 24
        for hour in observed_hours
        for offset in range(-ACTIVE_HOUR_WINDOW_RADIUS, ACTIVE_HOUR_WINDOW_RADIUS + 1)
    }


def judge_outreach(
    grounding: dict[str, Any],
    *,
    now: datetime | None = None,
    min_interval_min: int = 30,
    max_check_interval_min: int = 1440,
    model_call: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """判断是否应主动外呼，并钳制模型给出的下次检查时间。"""

    current = now or datetime.now()
    grounding_text = _grounding_json_for_model(grounding)
    prompt = OUTREACH_JUDGE_PROMPT
    try:
        response = _coerce_model_response((model_call or call_model_route_response)(
            route_key="outreach_judge",
            system_prompt=prompt,
            user_message=grounding_text,
        ))
        return _parse_outreach_judge_contract(
            response,
            now=current,
            min_interval_min=min_interval_min,
            max_check_interval_min=max_check_interval_min,
        )
    except Exception:
        diagnostic = judgement_failure_for_type("model_error")
        next_check_at = _clamp_next_check_at(
            None,
            now=current,
            min_interval_min=min_interval_min,
            max_check_interval_min=max_check_interval_min,
        )
        return {
            "should_reach_out": None,
            "reason": diagnostic.summary,
            "next_check_at": next_check_at.isoformat(),
            "next_intent": str(grounding.get("next_intent") or "")[:500],
            "outreach_kind": "message",
            "research_query": "",
            "raw": "",
            "error_type": diagnostic.error_type,
        }


def generate_outreach_message(
    grounding: dict[str, Any],
    reason: str,
    *,
    model_call: Callable[..., Any] | None = None,
) -> str:
    """生成主动外呼 DM 正文。"""

    grounding_text = _grounding_json_for_model(grounding)
    prompt = OUTREACH_GENERATOR_PROMPT.replace("{reason}", str(reason)[:500])
    payload = {
        "grounding": json.loads(grounding_text),
        "decision": {"reason": str(reason)[:500]},
    }
    response = _coerce_model_response((model_call or call_model_route_response)(
        route_key="outreach_generate",
        system_prompt=prompt,
        user_message=json.dumps(payload, ensure_ascii=False),
    ))
    finish_reason = (response.finish_reason or "").strip().lower()
    if finish_reason != "stop":
        error_type = "model_truncated" if finish_reason == "length" else "model_finish_error"
        finish_label = finish_reason or "<missing>"
        raise OutreachModelContractError(
            f"主动外呼 Generator 非正常结束: {finish_label}",
            error_type=error_type,
        )
    message = strip_think_blocks(response.content or "").strip()
    if not message:
        raise OutreachModelContractError(
            "主动外呼 Generator 返回空正文",
            error_type="empty_response",
        )
    return message


def _positive_env_number(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是有限正数") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} 必须是有限正数")
    return value


def _positive_env_integer(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是正整数") from exc
    if value <= 0:
        raise ValueError(f"{name} 必须是正整数")
    return value


def _outreach_endpoint_revision() -> str:
    revision = os.getenv("NANOBOT_QQ_PUSH_CONFIG_REVISION", "1").strip()
    if not revision or len(revision) > 128:
        raise ValueError("NANOBOT_QQ_PUSH_CONFIG_REVISION 必须是非空短文本")
    return revision


def _outreach_session_factory(session: Session):
    return sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=session.get_bind(),
    )


def _utc_naive(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current
    return current.astimezone(timezone.utc).replace(tzinfo=None)


def _outbound_occurrence_utc(value: datetime | None = None) -> datetime:
    """把主动外呼的上海本地业务时间转换为 Outbound 的 UTC naive 时间。"""

    current = value or datetime.now(OUTREACH_LOCAL_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=OUTREACH_LOCAL_TIMEZONE)
    return current.astimezone(timezone.utc).replace(tzinfo=None)


def _legacy_delivery_outcome(value: object):
    from core.outbound_transport import DeliveryOutcome

    if value is True:
        return DeliveryOutcome(
            category="success",
            error_type="",
            status_code=200,
            retry_after_seconds=None,
            duration_ms=0,
            safe_summary="投递成功",
            transport_phase="response_received",
        )
    if value is False:
        return DeliveryOutcome(
            category="payload",
            error_type="delivery_rejected",
            status_code=400,
            retry_after_seconds=None,
            duration_ms=0,
            safe_summary="远端拒绝本次投递",
            transport_phase="response_received",
        )
    return DeliveryOutcome(
        category="ambiguous",
        error_type="publish_outcome_unknown",
        status_code=None,
        retry_after_seconds=None,
        duration_ms=0,
        safe_summary="远端投递结果未知",
        transport_phase="write",
    )


async def _deliver_legacy_outreach_leaf(
    *,
    session: Session,
    outbox_id: int,
    publisher: Callable[[str, str, str], Any],
    endpoint_config_revision: str,
    now: datetime | None,
) -> None:
    execution_config = CustomTransportConfig(
        endpoint_config_revision=endpoint_config_revision,
    )

    async def deliver(transport) -> None:
        await deliver_legacy_outbound_once(
            session_factory=_outreach_session_factory(session),
            transport=transport,
            config=execution_config,
            outbox_id=outbox_id,
            worker_owner=_OUTREACH_PROCESS_OWNER,
            writer_owner=_OUTREACH_PROCESS_OWNER,
            writer_token=_OUTREACH_WRITER_TOKEN,
            writer_protocol_version=outbound_delivery.OUTBOUND_PROTOCOL_VERSION,
            writer_lease_seconds=OUTREACH_WRITER_LEASE_SECONDS,
            now=now,
        )

    async def legacy_transport(request):
        result = publisher(
            request.target_type,
            request.target_id,
            request.message,
        )
        if inspect.isawaitable(result):
            result = await result
        return _legacy_delivery_outcome(result)

    await deliver(legacy_transport)


async def drain_due_legacy_proactive_outboxes(
    *,
    session_factory: Callable[[], Session] | None = None,
    worker_config: OutboundWorkerConfig | None = None,
    transport: OutboundTransport | None = None,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
    jitter: Callable[[float], float] | None = None,
    worker_owner: str | None = None,
    stop_event: Any | None = None,
    takeover_writer: LegacyWriterTakeover | None = None,
    limit: int | None = None,
) -> list[OutboundDeliveryWorkResult]:
    """恢复主动外呼中安全到期的 legacy leaf。"""

    if now is not None and clock is not None:
        raise ValueError("now 与 clock 不能同时提供")
    factory = session_factory or SessionLocal
    resolved_worker = worker_config or OutboundWorkerConfig.from_env()
    batch_limit = resolved_worker.batch_size if limit is None else limit
    if type(batch_limit) is not int or not 1 <= batch_limit <= 1000:
        raise ValueError("limit 必须是 1-1000 的整数")
    clock_source = clock or (lambda: datetime.now(timezone.utc))
    current = _utc_naive(now if now is not None else clock_source())
    discovery = factory()
    writer_snapshot: LegacyWriterLeaseSnapshot | None = None
    try:
        outbound_delivery.terminalize_expired_outboxes(
            discovery,
            endpoint_key=OUTREACH_ENDPOINT_KEY,
            source_type=OUTREACH_SOURCE_TYPE,
            delivery_mode="legacy_direct",
            now=current,
        )
        discovery.commit()
        writer_snapshot = snapshot_live_legacy_writer(
            discovery,
            source_type=OUTREACH_SOURCE_TYPE,
            now=current,
        )
        outbox_ids = [
            int(row[0])
            for row in (
                discovery.query(outbound_delivery.OutboundDeliveryOutbox.id)
                .join(
                    outbound_delivery.OutboundRun,
                    outbound_delivery.OutboundRun.id
                    == outbound_delivery.OutboundDeliveryOutbox.run_id,
                )
                .filter(
                    outbound_delivery.OutboundRun.source_type
                    == OUTREACH_SOURCE_TYPE,
                    outbound_delivery.OutboundRun.delivery_mode
                    == "legacy_direct",
                    outbound_delivery.OutboundRun.active_outbox_id
                    == outbound_delivery.OutboundDeliveryOutbox.id,
                    outbound_delivery.OutboundDeliveryOutbox.endpoint_key
                    == OUTREACH_ENDPOINT_KEY,
                    outbound_delivery.OutboundDeliveryOutbox.status.in_((
                        "pending",
                        "retry_wait",
                        "blocked",
                    )),
                    outbound_delivery.OutboundDeliveryOutbox.request_started_count
                    < outbound_delivery.OutboundDeliveryOutbox.max_attempts,
                    outbound_delivery.OutboundDeliveryOutbox.retry_deadline_at
                    > current,
                    or_(
                        outbound_delivery.OutboundDeliveryOutbox.status.in_((
                            "pending",
                            "blocked",
                        )),
                        (
                            (
                                outbound_delivery.OutboundDeliveryOutbox.status
                                == "retry_wait"
                            )
                            & (
                                outbound_delivery.OutboundDeliveryOutbox.next_attempt_at
                                <= current
                            )
                        ),
                    ),
                )
                .order_by(
                    outbound_delivery.OutboundDeliveryOutbox.next_attempt_at.asc(),
                    outbound_delivery.OutboundDeliveryOutbox.id.asc(),
                )
                .limit(batch_limit)
                .all()
            )
        ]
    except Exception:
        discovery.rollback()
        raise
    finally:
        discovery.close()

    if writer_snapshot is not None:
        writer_owner = writer_snapshot.writer_owner
        writer_token = writer_snapshot.writer_token
        writer_protocol_version = writer_snapshot.protocol_version
        expected_writer_version = writer_snapshot.writer_version
    elif takeover_writer is not None:
        writer_owner = takeover_writer.writer_owner
        writer_token = takeover_writer.writer_token
        writer_protocol_version = outbound_delivery.OUTBOUND_PROTOCOL_VERSION
        expected_writer_version = None
    else:
        return []
    delivery_owner = str(worker_owner or writer_owner).strip()

    async def deliver_batch(
        resolved_transport: OutboundTransport,
    ) -> list[OutboundDeliveryWorkResult]:
        results: list[OutboundDeliveryWorkResult] = []
        for outbox_id in outbox_ids:
            if stop_event is not None and stop_event.is_set():
                break
            try:
                result = await deliver_legacy_outbound_once(
                    session_factory=factory,
                    transport=resolved_transport,
                    config=resolved_worker,
                    outbox_id=outbox_id,
                    worker_owner=delivery_owner,
                    writer_owner=writer_owner,
                    writer_token=writer_token,
                    writer_protocol_version=writer_protocol_version,
                    writer_lease_seconds=OUTREACH_WRITER_LEASE_SECONDS,
                    expected_writer_version=expected_writer_version,
                    now=now,
                    clock=clock,
                    jitter=jitter,
                )
            except Exception as exc:
                logger.error(
                    "主动外呼 legacy leaf 投递失败 outbox_id=%s error_type=%s",
                    outbox_id,
                    type(exc).__name__,
                )
                continue
            if result is not None:
                results.append(result)
        return results

    if transport is not None:
        return await deliver_batch(transport)
    if not outbox_ids:
        return []

    import aiohttp

    from core.outbound_transport import deliver_qq_push_with_session

    async with aiohttp.ClientSession() as http_session:
        async def qq_transport(request):
            return await deliver_qq_push_with_session(
                http_session,
                push_url=request.push_url,
                push_token=resolved_worker.push_token,
                target_type=request.target_type,
                target_id=request.target_id,
                message=request.message,
                timeout_seconds=request.timeout_seconds,
            )

        return await deliver_batch(qq_transport)


def _outreach_result(
    session: Session,
    *,
    log_id: int,
    run_id: int,
    outbox_id: int | None,
    forced: bool,
    deduplicated: bool,
) -> dict[str, Any]:
    session.expire_all()
    row = session.get(ProactiveOutreachLog, int(log_id))
    outbox = (
        session.get(outbound_delivery.OutboundDeliveryOutbox, int(outbox_id))
        if outbox_id is not None
        else None
    )
    status = str(row.status) if row is not None else "state_error"
    if outbox is not None and outbox.status == "pending":
        status = "queued"
    result = {
        "status": status,
        "log_id": int(log_id),
        "run_id": int(run_id),
        "outbox_id": int(outbox_id) if outbox_id is not None else None,
        "forced": bool(forced),
        "deduplicated": bool(deduplicated),
    }
    if outbox is not None and str(outbox.last_error_type or "").strip():
        result["error_type"] = str(outbox.last_error_type)
    return result


def _blocked_outreach_without_outbox_is_current(
    session: Session,
    row: ProactiveOutreachLog | None,
) -> bool:
    if (
        row is None
        or str(row.status or "") != "blocked"
        or row.outbound_run_id is None
    ):
        return False
    run = session.get(outbound_delivery.OutboundRun, int(row.outbound_run_id))
    return bool(
        run is not None
        and str(run.status or "") == "blocked"
        and run.active_outbox_id is None
        and outbound_delivery.proactive_outreach_linkage_is_current(
            session,
            run_id=int(run.id),
        )
    )


def _generated_row_facts(
    row: ProactiveOutreachLog,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    try:
        grounding = json.loads(str(row.grounding_json or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise outbound_delivery.OutboundConflictError(
            "主动外呼 generated grounding 无法解析"
        ) from exc
    if type(grounding) is not dict:
        raise outbound_delivery.OutboundConflictError(
            "主动外呼 generated grounding 必须是 JSON object"
        )
    if outbound_delivery.PROACTIVE_GENERATION_METADATA_KEY not in grounding:
        return None
    metadata = outbound_delivery.proactive_generation_metadata(grounding)
    return grounding, metadata


def _unacquired_generated_result(
    session: Session,
    *,
    row: ProactiveOutreachLog,
    run_id: int,
) -> dict[str, Any]:
    session.expire_all()
    current_row = session.get(ProactiveOutreachLog, int(row.id))
    run = session.get(outbound_delivery.OutboundRun, int(run_id))
    if current_row is None or run is None:
        return {
            "status": "state_error",
            "error_type": "generated_run_missing",
            "log_id": int(row.id),
            "forced": bool(row.forced),
        }
    if run.active_outbox_id is not None:
        return _outreach_result(
            session,
            log_id=int(current_row.id),
            run_id=int(run.id),
            outbox_id=int(run.active_outbox_id),
            forced=bool(current_row.forced),
            deduplicated=True,
        )
    if str(run.status or "") in {"claimed", "generating"}:
        return {
            "status": "skipped_in_progress",
            "log_id": int(current_row.id),
            "run_id": int(run.id),
            "forced": bool(current_row.forced),
        }
    if str(run.status or "") == "failed":
        return {
            "status": "generation_error",
            "error_type": str(run.failure_type or "generation_failed"),
            "reason": str(run.failure_summary or ""),
            "log_id": int(current_row.id),
            "run_id": int(run.id),
            "forced": bool(current_row.forced),
        }
    return _outreach_result(
        session,
        log_id=int(current_row.id),
        run_id=int(run.id),
        outbox_id=None,
        forced=bool(current_row.forced),
        deduplicated=True,
    )


def _claim_generated_row_locked(
    session: Session,
    *,
    row: ProactiveOutreachLog,
    runtime_at: datetime,
) -> tuple[_GeneratedOutreachWork | None, dict[str, Any] | None]:
    facts = _generated_row_facts(row)
    if facts is None:
        raise outbound_delivery.OutboundConflictError(
            "主动外呼 generated 候选缺少冻结标记"
        )
    persisted_grounding, metadata = facts
    if row.id is None or row.created_at is None:
        raise outbound_delivery.OutboundConflictError(
            "主动外呼 generated 候选缺少持久身份"
        )
    log_id = int(row.id)
    idempotency_key = str(row.idempotency_key or "").strip()
    if not idempotency_key:
        raise outbound_delivery.OutboundConflictError(
            "主动外呼 generated 候选缺少幂等键"
        )
    created_at = row.created_at
    kind = str(metadata["kind"])
    forced = kind == "forced"
    input_grounding = dict(metadata["input_grounding"])
    judge = dict(metadata["judge"])
    snapshot = outbound_delivery.proactive_outreach_generated_source_snapshot(row)
    source_revision = (
        outbound_delivery.proactive_outreach_generated_source_revision(row)
    )
    occurrence_key = outbound_delivery.proactive_outreach_occurrence_key(
        log_id=log_id,
        idempotency_key=idempotency_key,
        source_revision=source_revision,
    )
    destination_snapshot = {
        "target_type": "private",
        "target_id": str(row.user_id),
    }
    destination_fingerprint = (
        outbound_delivery.proactive_outreach_destination_fingerprint(
            str(row.user_id)
        )
    )
    endpoint_revision = _outreach_endpoint_revision()
    claim = outbound_delivery.claim_outbound_run(
        session,
        source_type=OUTREACH_SOURCE_TYPE,
        source_id=str(log_id),
        occurrence_key=occurrence_key,
        source_revision=source_revision,
        source_snapshot=snapshot,
        destination_snapshot=destination_snapshot,
        target_type="private",
        task_kind=outbound_delivery.PROACTIVE_GENERATED_TASK_KIND,
        scheduled_for=_outbound_occurrence_utc(created_at),
        trigger_type="forced" if forced else "judge",
        owner=_OUTREACH_PROCESS_OWNER,
        claim_lease_seconds=OUTREACH_CLAIM_LEASE_SECONDS,
        writer_owner=_OUTREACH_PROCESS_OWNER,
        writer_token=_OUTREACH_WRITER_TOKEN,
        writer_protocol_version=outbound_delivery.OUTBOUND_PROTOCOL_VERSION,
        writer_lease_seconds=OUTREACH_WRITER_LEASE_SECONDS,
        endpoint_key=OUTREACH_ENDPOINT_KEY,
        destination_fingerprint=destination_fingerprint,
        endpoint_config_revision=endpoint_revision,
        payload_contract_fingerprint=OUTREACH_PAYLOAD_CONTRACT,
        now=runtime_at,
    )
    if not claim.acquired:
        return None, _unacquired_generated_result(
            session,
            row=row,
            run_id=claim.run_id,
        )
    generation = outbound_delivery.start_generation_attempt(
        session,
        run_id=claim.run_id,
        owner=claim.owner,
        claim_token=claim.claim_token,
        writer_owner=_OUTREACH_PROCESS_OWNER,
        writer_token=_OUTREACH_WRITER_TOKEN,
        writer_protocol_version=outbound_delivery.OUTBOUND_PROTOCOL_VERSION,
        endpoint_key=OUTREACH_ENDPOINT_KEY,
        destination_fingerprint=destination_fingerprint,
        endpoint_config_revision=endpoint_revision,
        payload_contract_fingerprint=OUTREACH_PAYLOAD_CONTRACT,
        now=runtime_at,
    )
    if generation.status != "started" or generation.attempt_id is None:
        return None, _outreach_result(
            session,
            log_id=log_id,
            run_id=claim.run_id,
            outbox_id=None,
            forced=forced,
            deduplicated=False,
        )
    return _GeneratedOutreachWork(
        log_id=log_id,
        run_id=claim.run_id,
        generation_attempt_id=int(generation.attempt_id),
        owner=claim.owner,
        claim_token=claim.claim_token,
        delivery_mode=claim.delivery_mode,
        idempotency_key=idempotency_key,
        input_grounding=input_grounding,
        persisted_grounding=persisted_grounding,
        judge=judge,
        kind=kind,
        forced=forced,
        created_at=created_at,
        source_revision=source_revision,
        destination_snapshot=destination_snapshot,
        destination_fingerprint=destination_fingerprint,
        endpoint_revision=endpoint_revision,
    ), None


def _resume_generated_outreach(
    session: Session,
    *,
    row: ProactiveOutreachLog,
    user_id: str,
    evaluation_owner_token: str | None,
) -> tuple[_GeneratedOutreachWork | None, dict[str, Any] | None]:
    try:
        runtime_at = outbound_delivery.lock_outbound_source_control(
            session,
            source_type=OUTREACH_SOURCE_TYPE,
            now=None,
        )
        if not _fence_evaluation_write(
            session,
            user_id=user_id,
            owner_token=evaluation_owner_token,
        ):
            session.rollback()
            return None, {
                "status": "lease_lost",
                "log_id": int(row.id),
                "forced": bool(row.forced),
            }
        current = session.get(ProactiveOutreachLog, int(row.id))
        if current is None or current.user_id != user_id:
            session.rollback()
            return None, {
                "status": "stale_schedule",
                "log_id": int(row.id),
                "forced": bool(row.forced),
            }
        clear_at = _history_clear_at(session, user_id)
        if (
            clear_at is not None
            and current.created_at is not None
            and current.created_at <= clear_at
        ):
            session.rollback()
            return None, {
                "status": "cancelled_history_clear",
                "log_id": int(current.id),
                "forced": bool(current.forced),
            }
        work, result = _claim_generated_row_locked(
            session,
            row=current,
            runtime_at=runtime_at,
        )
        session.commit()
        return work, result
    except BaseException:
        session.rollback()
        raise


def _prepare_generated_outreach(
    session: Session,
    *,
    user_id: str,
    idempotency_key: str,
    grounding: dict[str, Any],
    judge: dict[str, Any],
    kind: str,
    next_check_at: datetime | None,
    next_intent: str,
    created_at: datetime,
    schedule_row_id: int | None,
    evaluation_owner_token: str | None,
) -> tuple[_GeneratedOutreachWork | None, dict[str, Any] | None]:
    try:
        runtime_at = outbound_delivery.lock_outbound_source_control(
            session,
            source_type=OUTREACH_SOURCE_TYPE,
            now=None,
        )
        if not _fence_evaluation_write(
            session,
            user_id=user_id,
            owner_token=evaluation_owner_token,
        ):
            session.rollback()
            return None, {
                "status": "lease_lost",
                "log_id": schedule_row_id,
                "forced": kind == "forced",
            }
        clear_at = _history_clear_at(session, user_id)
        if clear_at is not None and created_at <= clear_at:
            session.rollback()
            return None, {
                "status": "cancelled_history_clear",
                "log_id": schedule_row_id,
                "forced": kind == "forced",
            }
        existing = (
            session.query(ProactiveOutreachLog)
            .filter(ProactiveOutreachLog.idempotency_key == idempotency_key)
            .first()
        )
        if existing is not None:
            existing_facts = _generated_row_facts(existing)
            if existing_facts is not None and existing.outbound_run_id is not None:
                if existing.status not in {"candidate", "blocked"}:
                    conflict_result = _pending_schedule_conflict_result(
                        session,
                        row=existing,
                        user_id=user_id,
                    )
                    session.rollback()
                    return None, conflict_result
                work, result = _claim_generated_row_locked(
                    session,
                    row=existing,
                    runtime_at=runtime_at,
                )
                session.commit()
                return work, result
            if existing.status not in {"pending", "candidate", "cancelled"}:
                session.rollback()
                return None, _pending_schedule_conflict_result(
                    session,
                    row=existing,
                    user_id=user_id,
                )
        schedule_row = (
            session.get(ProactiveOutreachLog, int(schedule_row_id))
            if schedule_row_id is not None
            else None
        )
        row = existing or schedule_row
        if existing is not None and schedule_row is not None:
            if int(existing.id) != int(schedule_row.id):
                if _cancelled_row_is_reusable(
                    session,
                    row=existing,
                    user_id=user_id,
                    generation_at=created_at,
                ):
                    if schedule_row.status == "pending":
                        schedule_row.status = "cancelled"
                    row = existing
                else:
                    session.rollback()
                    return None, _pending_schedule_conflict_result(
                        session,
                        row=existing,
                        user_id=user_id,
                    )
        if row is None:
            row = ProactiveOutreachLog(user_id=user_id)
            session.add(row)
        if row.user_id != user_id:
            raise outbound_delivery.OutboundConflictError(
                "主动外呼 generated 候选用户不一致"
            )
        if row.status == "cancelled" and not _cancelled_row_is_reusable(
            session,
            row=row,
            user_id=user_id,
            generation_at=created_at,
        ):
            session.rollback()
            return None, {
                "status": "cancelled_history_clear",
                "log_id": int(row.id),
                "forced": kind == "forced",
            }
        persisted_grounding = (
            outbound_delivery.prepare_proactive_generation_grounding(
                grounding=grounding,
                kind=kind,
                judge=judge,
            )
        )
        row.idempotency_key = idempotency_key
        row.grounding_json = _grounding_json(persisted_grounding)
        row.judge_should = True
        row.judge_reason = str(judge.get("reason") or "")
        row.next_check_at = next_check_at
        row.next_intent = next_intent
        row.message = ""
        row.forced = kind == "forced"
        row.status = "candidate"
        row.outbound_run_id = None
        row.created_at = created_at
        session.flush()
        work, result = _claim_generated_row_locked(
            session,
            row=row,
            runtime_at=runtime_at,
        )
        session.commit()
        return work, result
    except BaseException:
        session.rollback()
        raise


def _generated_lease_lost_result(
    work: _GeneratedOutreachWork,
) -> dict[str, Any]:
    return {
        "status": "lease_lost",
        "log_id": work.log_id,
        "run_id": work.run_id,
        "forced": work.forced,
    }


def _fail_generated_outreach(
    session: Session,
    *,
    work: _GeneratedOutreachWork,
    user_id: str,
    error_type: str,
    error_summary: str,
    evaluation_owner_token: str | None,
    research_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        runtime_at = outbound_delivery.lock_outbound_source_control(
            session,
            source_type=OUTREACH_SOURCE_TYPE,
            now=None,
        )
        if not _fence_evaluation_write(
            session,
            user_id=user_id,
            owner_token=evaluation_owner_token,
        ):
            session.rollback()
            return _generated_lease_lost_result(work)
        row = session.get(ProactiveOutreachLog, work.log_id)
        if row is None or row.outbound_run_id != work.run_id:
            session.rollback()
            return _generated_lease_lost_result(work)
        clear_at = _history_clear_at(session, user_id)
        history_cleared = clear_at is not None and work.created_at <= clear_at
        settled_error_type = "history_cleared" if history_cleared else error_type
        settled_summary = "用户历史已清除" if history_cleared else error_summary
        outbound_delivery.fail_outbound_generation(
            session,
            run_id=work.run_id,
            generation_attempt_id=work.generation_attempt_id,
            owner=work.owner,
            claim_token=work.claim_token,
            error_type=settled_error_type,
            error_summary=settled_summary,
            now=runtime_at,
        )
        session.expire_all()
        failed_row = session.get(ProactiveOutreachLog, work.log_id)
        if failed_row is None or failed_row.outbound_run_id != work.run_id:
            raise outbound_delivery.OutboundFencingError(
                "生成失败后的主动外呼来源已失效"
            )
        output_grounding = dict(work.persisted_grounding)
        output_grounding["generation_error"] = {
            "error_type": settled_error_type,
            "reason": str(settled_summary or "")[:500],
        }
        if research_payload:
            output_grounding["research"] = research_payload
        values: dict[Any, Any] = {
            ProactiveOutreachLog.grounding_json: _grounding_json(
                output_grounding
            )
        }
        if history_cleared:
            values[ProactiveOutreachLog.status] = "cancelled"
        updated = (
            session.query(ProactiveOutreachLog)
            .filter(
                ProactiveOutreachLog.id == work.log_id,
                ProactiveOutreachLog.user_id == user_id,
                ProactiveOutreachLog.idempotency_key == work.idempotency_key,
                ProactiveOutreachLog.outbound_run_id == work.run_id,
                ProactiveOutreachLog.status == "failed",
                ProactiveOutreachLog.message == "",
                ProactiveOutreachLog.created_at == work.created_at,
            )
            .update(values, synchronize_session=False)
        )
        if updated != 1:
            raise outbound_delivery.OutboundFencingError(
                "生成失败输出 CAS 已失效"
            )
        session.commit()
        if history_cleared:
            return {
                "status": "cancelled_history_clear",
                "log_id": work.log_id,
                "run_id": work.run_id,
                "forced": work.forced,
            }
        return {
            "status": "generation_error",
            "error_type": error_type,
            "reason": str(error_summary or "")[:500],
            "log_id": work.log_id,
            "run_id": work.run_id,
            "forced": work.forced,
        }
    except outbound_delivery.OutboundFencingError:
        session.rollback()
        return _generated_lease_lost_result(work)
    except BaseException:
        session.rollback()
        raise


async def _commit_generated_outreach(
    session: Session,
    *,
    work: _GeneratedOutreachWork,
    user_id: str,
    message: str,
    evaluation_owner_token: str | None,
    publisher: Callable[[str, str, str], Any] | None,
    research_payload: dict[str, Any] | None = None,
    forced_fallback: dict[str, Any] | None = None,
    generation_error_type: str = "",
    generation_error_summary: str = "",
    model_trace_id: str = "",
) -> dict[str, Any]:
    cleaned_message = strip_think_blocks(str(message or "")).strip()
    if not cleaned_message:
        return _fail_generated_outreach(
            session,
            work=work,
            user_id=user_id,
            error_type="contract_error",
            error_summary="主动外呼正文为空",
            evaluation_owner_token=evaluation_owner_token,
            research_payload=research_payload,
        )
    try:
        runtime_at = outbound_delivery.lock_outbound_source_control(
            session,
            source_type=OUTREACH_SOURCE_TYPE,
            now=None,
        )
        if not _fence_evaluation_write(
            session,
            user_id=user_id,
            owner_token=evaluation_owner_token,
        ):
            session.rollback()
            return _generated_lease_lost_result(work)
        row = session.get(ProactiveOutreachLog, work.log_id)
        if row is None or row.outbound_run_id != work.run_id:
            session.rollback()
            return _generated_lease_lost_result(work)
        clear_at = _history_clear_at(session, user_id)
        if clear_at is not None and work.created_at <= clear_at:
            session.rollback()
            return _fail_generated_outreach(
                session,
                work=work,
                user_id=user_id,
                error_type="history_cleared",
                error_summary="用户历史已清除",
                evaluation_owner_token=evaluation_owner_token,
                research_payload=research_payload,
            )
        envelope = build_chat_response_envelope(
            status="ok",
            answer=cleaned_message,
            meta={
                "platform": "qq",
                "chat_type": "private",
                "source": OUTREACH_SOURCE_TYPE,
                "log_id": work.log_id,
            },
        )
        queued = outbound_delivery.commit_generated_outbox(
            session,
            run_id=work.run_id,
            generation_attempt_id=work.generation_attempt_id,
            owner=work.owner,
            claim_token=work.claim_token,
            idempotency_key=(
                outbound_delivery.proactive_outreach_delivery_key(
                    log_id=work.log_id,
                    idempotency_key=work.idempotency_key,
                    source_revision=work.source_revision,
                )
            ),
            destination_snapshot=work.destination_snapshot,
            destination_fingerprint=work.destination_fingerprint,
            target_type="private",
            endpoint_key=OUTREACH_ENDPOINT_KEY,
            payload=envelope,
            max_attempts=_positive_env_integer(
                "NANOBOT_OUTBOUND_MAX_ATTEMPTS",
                OUTREACH_DEFAULT_MAX_ATTEMPTS,
            ),
            retry_deadline_at=(
                runtime_at
                + timedelta(seconds=_positive_env_number(
                    "NANOBOT_OUTBOUND_RETRY_DEADLINE_SECONDS",
                    OUTREACH_DEFAULT_RETRY_DEADLINE_SECONDS,
                ))
            ),
            endpoint_config_revision=work.endpoint_revision,
            payload_contract_fingerprint=OUTREACH_PAYLOAD_CONTRACT,
            model_trace_id=model_trace_id,
            generation_error_type=generation_error_type,
            generation_error_summary=generation_error_summary,
            now=runtime_at,
        )
        if queued.outbox_id is None:
            session.commit()
            result = _outreach_result(
                session,
                log_id=work.log_id,
                run_id=work.run_id,
                outbox_id=None,
                forced=work.forced,
                deduplicated=not queued.created,
            )
            session.rollback()
            return result
        if queued.created:
            output_grounding = dict(work.persisted_grounding)
            if research_payload:
                output_grounding["research"] = research_payload
            if forced_fallback:
                output_grounding["forced_fallback"] = forced_fallback
            session.expire_all()
            queued_row = session.get(ProactiveOutreachLog, work.log_id)
            if queued_row is None:
                raise outbound_delivery.OutboundFencingError(
                    "generated outbox 入队后来源丢失"
                )
            values: dict[Any, Any] = {
                ProactiveOutreachLog.grounding_json: _grounding_json(
                    output_grounding
                ),
                ProactiveOutreachLog.message: cleaned_message,
            }
            if forced_fallback:
                values[ProactiveOutreachLog.judge_reason] = (
                    f"{queued_row.judge_reason or ''}；"
                    "Generator 契约失败，使用服务端安全兜底"
                )[:1000]
            updated = (
                session.query(ProactiveOutreachLog)
                .filter(
                    ProactiveOutreachLog.id == work.log_id,
                    ProactiveOutreachLog.user_id == user_id,
                    ProactiveOutreachLog.idempotency_key
                    == work.idempotency_key,
                    ProactiveOutreachLog.outbound_run_id == work.run_id,
                    ProactiveOutreachLog.status == "queued",
                    ProactiveOutreachLog.grounding_json
                    == _grounding_json(work.persisted_grounding),
                    ProactiveOutreachLog.message == "",
                    ProactiveOutreachLog.created_at == work.created_at,
                )
                .update(values, synchronize_session=False)
            )
            if updated != 1:
                raise outbound_delivery.OutboundFencingError(
                    "generated 候选输出 CAS 已失效"
                )
        session.commit()
    except outbound_delivery.OutboundFencingError:
        session.rollback()
        return _generated_lease_lost_result(work)
    except BaseException:
        session.rollback()
        raise

    if (
        work.delivery_mode == "legacy_direct"
        and queued.outbox_id is not None
        and publisher is not None
    ):
        await _deliver_legacy_outreach_leaf(
            session=session,
            outbox_id=int(queued.outbox_id),
            publisher=publisher,
            endpoint_config_revision=work.endpoint_revision,
            now=None,
        )
    result = _outreach_result(
        session,
        log_id=work.log_id,
        run_id=work.run_id,
        outbox_id=queued.outbox_id,
        forced=work.forced,
        deduplicated=not queued.created,
    )
    session.rollback()
    return result


async def _run_generated_outreach(
    session: Session,
    *,
    work: _GeneratedOutreachWork,
    user_id: str,
    generator_fn: Callable[..., str],
    research_fn: Callable[..., Any] | None,
    publisher: Callable[[str, str, str], Any] | None,
    evaluation_owner_token: str | None,
) -> dict[str, Any]:
    if work.kind == "forced":
        reason = str(work.judge.get("reason") or "")
        try:
            message = generator_fn(work.input_grounding, reason)
        except OutreachModelContractError as exc:
            diagnostic = generation_failure_from_exception(exc)
            error_type = diagnostic.error_type
            error_reason = diagnostic.summary
            logger.warning(
                "主动外呼 forced Generator 契约失败，使用服务端安全兜底 "
                "user_id=%s error_type=%s",
                user_id,
                error_type,
            )
            return await _commit_generated_outreach(
                session,
                work=work,
                user_id=user_id,
                message=FORCED_FALLBACK_MESSAGE,
                evaluation_owner_token=evaluation_owner_token,
                publisher=publisher,
                forced_fallback={
                    "error_type": error_type,
                    "reason": error_reason,
                },
                generation_error_type=error_type,
                generation_error_summary=error_reason,
            )
        except Exception as exc:
            diagnostic = generation_failure_from_exception(exc)
            return _fail_generated_outreach(
                session,
                work=work,
                user_id=user_id,
                error_type=diagnostic.error_type,
                error_summary=diagnostic.summary,
                evaluation_owner_token=evaluation_owner_token,
            )
        return await _commit_generated_outreach(
            session,
            work=work,
            user_id=user_id,
            message=message,
            evaluation_owner_token=evaluation_owner_token,
            publisher=publisher,
        )

    from core.proactive_candidate import materialize_outreach_candidate

    evaluation = await materialize_outreach_candidate(
        user_id=user_id,
        request_id=work.idempotency_key,
        grounding=work.input_grounding,
        judge=work.judge,
        generator_fn=generator_fn,
        research_fn=research_fn,
        context_summary=_grounding_json_for_model(work.input_grounding),
    )
    status = str(evaluation.get("status") or "")
    research_payload = (
        dict(evaluation.get("research") or {})
        if isinstance(evaluation.get("research"), dict)
        else {}
    )
    if status == "candidate":
        return await _commit_generated_outreach(
            session,
            work=work,
            user_id=user_id,
            message=str(evaluation.get("message") or ""),
            evaluation_owner_token=evaluation_owner_token,
            publisher=publisher,
            research_payload=research_payload or None,
            model_trace_id=str(evaluation.get("model_trace_id") or "")[:128],
        )
    error_type = str(
        evaluation.get("error_type")
        or evaluation.get("reason_code")
        or "contract_error"
    )
    error_reason = str(
        evaluation.get("reason")
        or evaluation.get("error")
        or evaluation.get("reason_code")
        or "主动外呼正文生成失败"
    )[:500]
    failed = _fail_generated_outreach(
        session,
        work=work,
        user_id=user_id,
        error_type=error_type,
        error_summary=error_reason,
        evaluation_owner_token=evaluation_owner_token,
        research_payload=research_payload or None,
    )
    if status != "research_blocked" or failed.get("status") != "generation_error":
        return failed
    next_check_at = _parse_iso_datetime(work.judge.get("next_check_at"))
    pending_grounding = dict(work.input_grounding)
    if research_payload:
        pending_grounding["research"] = research_payload
    row = _upsert_pending_schedule(
        session,
        user_id=user_id,
        idempotency_key=_outreach_key(
            user_id,
            next_check_at or work.created_at,
            forced=False,
        ),
        grounding=pending_grounding,
        judge_reason=str(work.judge.get("reason") or ""),
        next_check_at=next_check_at,
        next_intent=str(work.judge.get("next_intent") or ""),
        created_at=work.created_at,
        evaluation_owner_token=evaluation_owner_token,
    )
    if row is None:
        return _generated_lease_lost_result(work)
    if row.status != "pending":
        return _pending_schedule_conflict_result(
            session,
            row=row,
            user_id=user_id,
        )
    return {
        "status": "research_blocked",
        "reason_code": str(
            evaluation.get("reason_code") or "runtime_error"
        ),
        "log_id": int(row.id),
        "failed_log_id": work.log_id,
        "run_id": work.run_id,
        "forced": False,
    }


async def _enqueue_outreach_outbox(
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
    db: Session | None,
    schedule_row_id: int | None,
    schedule_expected_idempotency_key: str | None,
    created_at: datetime | None,
    delivered_at: datetime | None,
    publisher: Callable[[str, str, str], Any] | None,
    evaluation_owner_token: str | None,
) -> dict[str, Any]:
    source_occurrence_at = delivered_at or created_at or datetime.now()
    endpoint_revision = _outreach_endpoint_revision()
    cleaned_message = strip_think_blocks(str(message or "")).strip()
    if not cleaned_message:
        return {
            "status": "generation_error",
            "error_type": "contract_error",
            "forced": bool(forced),
        }
    research_payload = grounding.get("research")
    if isinstance(research_payload, dict):
        from core.proactive_research import (
            normalize_research_publication_text,
            validate_research_publication_text,
        )

        research_sources = list(research_payload.get("sources") or [])
        cleaned_message = normalize_research_publication_text(
            cleaned_message,
            research_sources,
        )
        publication_error = validate_research_publication_text(
            cleaned_message,
            research_sources,
        )
        if publication_error:
            return {
                "status": "generation_error",
                "error_type": publication_error,
                "forced": bool(forced),
            }

    with _session_scope(db) as session:
        try:
            runtime_at = outbound_delivery.lock_outbound_source_control(
                session,
                source_type=OUTREACH_SOURCE_TYPE,
                now=None,
            )
            existing = (
                session.query(ProactiveOutreachLog)
                .filter(ProactiveOutreachLog.idempotency_key == idempotency_key)
                .first()
            )
            reusable_cancelled = _cancelled_row_is_reusable(
                session,
                row=existing,
                user_id=user_id,
                generation_at=created_at or source_occurrence_at,
            )
            reusable_blocked = _blocked_outreach_without_outbox_is_current(
                session,
                existing,
            )
            if (
                existing is not None
                and existing.status not in {"pending", "candidate"}
                and not reusable_cancelled
                and not reusable_blocked
            ):
                if existing.status in {
                    "sending",
                    "queued",
                    "delivering",
                    "retry_wait",
                    "sent",
                    "sent_after_ambiguous_replay",
                    "failed",
                    "blocked",
                    "ambiguous",
                    "legacy_ambiguous_hold",
                }:
                    session.rollback()
                    return {
                        "status": "skipped_duplicate",
                        "log_id": existing.id,
                        "forced": bool(existing.forced),
                    }
                if existing.status == "cancelled":
                    clear_at = _history_clear_at(session, user_id)
                    status = (
                        "cancelled_history_clear"
                        if clear_at is not None
                        and (
                            existing.created_at is None
                            or existing.created_at <= clear_at
                        )
                        else "stale_schedule"
                    )
                    session.rollback()
                    return {
                        "status": status,
                        "log_id": existing.id,
                        "forced": bool(existing.forced),
                    }
                session.rollback()
                return {
                    "status": "state_error",
                    "error_type": "unknown_delivery_state",
                    "log_id": existing.id,
                    "forced": bool(existing.forced),
                }

            row = existing
            if row is None and schedule_row_id is not None:
                candidate = session.get(ProactiveOutreachLog, schedule_row_id)
                expected_schedule_key = (
                    schedule_expected_idempotency_key
                    if schedule_expected_idempotency_key is not None
                    else idempotency_key
                )
                if (
                    candidate is not None
                    and candidate.user_id == user_id
                    and candidate.status in {"pending", "candidate"}
                    and candidate.idempotency_key == expected_schedule_key
                ):
                    row = candidate
                else:
                    session.rollback()
                    return {
                        "status": "stale_schedule",
                        "log_id": schedule_row_id,
                        "forced": (
                            bool(candidate.forced)
                            if candidate is not None
                            else bool(forced)
                        ),
                    }
            claim_created_at = (
                created_at
                if created_at is not None
                else (
                    row.created_at
                    if row is not None
                    else source_occurrence_at
                )
            )
            if row is None:
                row = ProactiveOutreachLog(
                    user_id=user_id,
                    status="pending",
                    created_at=claim_created_at,
                )
                session.add(row)
                session.flush()

            row_id = int(row.id)
            expected_status = str(row.status or "")
            expected_key = row.idempotency_key
            expected_created_at = row.created_at
            expected_run_id = row.outbound_run_id
            claim_query = (
                session.query(ProactiveOutreachLog)
                .filter(
                    ProactiveOutreachLog.id == row_id,
                    ProactiveOutreachLog.user_id == user_id,
                    ProactiveOutreachLog.status == expected_status,
                )
            )
            claim_query = claim_query.filter(
                ProactiveOutreachLog.idempotency_key.is_(None)
                if expected_key is None
                else ProactiveOutreachLog.idempotency_key == expected_key
            )
            claim_query = claim_query.filter(
                ProactiveOutreachLog.created_at.is_(None)
                if expected_created_at is None
                else ProactiveOutreachLog.created_at == expected_created_at
            )
            claim_query = claim_query.filter(
                ProactiveOutreachLog.outbound_run_id.is_(None)
                if expected_run_id is None
                else ProactiveOutreachLog.outbound_run_id == expected_run_id
            )
            if claim_created_at is None:
                claim_query = claim_query.filter(
                    ~exists().where(
                        User.id == user_id,
                        User.history_clear_at.isnot(None),
                    )
                )
            else:
                claim_query = claim_query.filter(
                    ~exists().where(
                        User.id == user_id,
                        User.history_clear_at.isnot(None),
                        User.history_clear_at >= claim_created_at,
                    )
                )
            if evaluation_owner_token is not None:
                claim_query = claim_query.filter(
                    exists().where(
                        ProactiveOutreachLease.user_id == user_id,
                        ProactiveOutreachLease.owner_token
                        == evaluation_owner_token,
                        ProactiveOutreachLease.lease_expires_at > datetime.now(),
                    )
                )
            source_created_at = (
                delivered_at or claim_created_at or source_occurrence_at
            )
            outbound_occurrence_at = _outbound_occurrence_utc(source_created_at)
            claim_values = {
                ProactiveOutreachLog.idempotency_key: idempotency_key,
                ProactiveOutreachLog.grounding_json: _grounding_json(grounding),
                ProactiveOutreachLog.judge_should: judge_should,
                ProactiveOutreachLog.judge_reason: judge_reason,
                ProactiveOutreachLog.next_check_at: next_check_at,
                ProactiveOutreachLog.next_intent: next_intent,
                ProactiveOutreachLog.message: cleaned_message,
                ProactiveOutreachLog.forced: forced,
                ProactiveOutreachLog.created_at: source_created_at,
                ProactiveOutreachLog.status: (
                    "blocked" if reusable_blocked else "candidate"
                ),
                ProactiveOutreachLog.outbound_run_id: (
                    expected_run_id if reusable_blocked else None
                ),
            }
            claimed = claim_query.update(
                claim_values,
                synchronize_session=False,
            )
            if claimed != 1:
                session.rollback()
                current = session.get(ProactiveOutreachLog, row_id)
                clear_at = _history_clear_at(session, user_id)
                if clear_at is not None and (
                    claim_created_at is None or claim_created_at <= clear_at
                ):
                    if current is not None and current.status in {
                        "pending",
                        "candidate",
                    }:
                        current.status = "cancelled"
                        session.commit()
                    else:
                        session.rollback()
                    return {
                        "status": "cancelled_history_clear",
                        "log_id": row_id,
                        "forced": (
                            bool(current.forced)
                            if current is not None
                            else bool(forced)
                        ),
                    }
                if (
                    evaluation_owner_token is not None
                    and not _evaluation_lease_is_owned(
                        session,
                        user_id=user_id,
                        owner_token=evaluation_owner_token,
                    )
                ):
                    session.rollback()
                    return {
                        "status": "lease_lost",
                        "log_id": row_id if current is not None else None,
                        "forced": (
                            bool(current.forced)
                            if current is not None
                            else bool(forced)
                        ),
                    }
                session.rollback()
                return {
                    "status": "skipped_duplicate",
                    "log_id": row_id,
                    "forced": bool(current.forced) if current is not None else bool(forced),
                }

            session.flush()
            session.expire_all()
            row = session.get(ProactiveOutreachLog, row_id)
            if row is None:
                raise RuntimeError("主动外呼 candidate CAS 后记录丢失")
            snapshot = outbound_delivery.proactive_outreach_source_snapshot(row)
            source_revision = outbound_delivery.proactive_outreach_source_revision(row)
            occurrence_key = outbound_delivery.proactive_outreach_occurrence_key(
                log_id=row_id,
                idempotency_key=idempotency_key,
                source_revision=source_revision,
            )
            destination_snapshot = {
                "target_type": "private",
                "target_id": user_id,
            }
            destination_fingerprint = (
                outbound_delivery.proactive_outreach_destination_fingerprint(user_id)
            )
            envelope = build_chat_response_envelope(
                status="ok",
                answer=cleaned_message,
                meta={
                    "platform": "qq",
                    "chat_type": "private",
                    "source": OUTREACH_SOURCE_TYPE,
                    "log_id": row_id,
                },
            )
            claim = outbound_delivery.claim_outbound_run(
                session,
                source_type=OUTREACH_SOURCE_TYPE,
                source_id=str(row_id),
                occurrence_key=occurrence_key,
                source_revision=source_revision,
                source_snapshot=snapshot,
                destination_snapshot=destination_snapshot,
                target_type="private",
                task_kind="proactive_outreach_prepared",
                scheduled_for=outbound_occurrence_at,
                trigger_type="forced" if forced else "judge",
                owner=_OUTREACH_PROCESS_OWNER,
                claim_lease_seconds=OUTREACH_CLAIM_LEASE_SECONDS,
                writer_owner=_OUTREACH_PROCESS_OWNER,
                writer_token=_OUTREACH_WRITER_TOKEN,
                writer_protocol_version=outbound_delivery.OUTBOUND_PROTOCOL_VERSION,
                writer_lease_seconds=OUTREACH_WRITER_LEASE_SECONDS,
                endpoint_key=OUTREACH_ENDPOINT_KEY,
                destination_fingerprint=destination_fingerprint,
                endpoint_config_revision=endpoint_revision,
                payload_contract_fingerprint=OUTREACH_PAYLOAD_CONTRACT,
                now=runtime_at,
            )
            if not claim.acquired:
                session.commit()
                run = session.get(outbound_delivery.OutboundRun, claim.run_id)
                outbox_id = (
                    int(run.active_outbox_id)
                    if run is not None and run.active_outbox_id is not None
                    else None
                )
                result = _outreach_result(
                    session,
                    log_id=row_id,
                    run_id=claim.run_id,
                    outbox_id=outbox_id,
                    forced=forced,
                    deduplicated=True,
                )
                session.rollback()
                return result
            queued = outbound_delivery.commit_prepared_outbox(
                session,
                run_id=claim.run_id,
                owner=claim.owner,
                claim_token=claim.claim_token,
                idempotency_key=(
                    outbound_delivery.proactive_outreach_delivery_key(
                        log_id=row_id,
                        idempotency_key=idempotency_key,
                        source_revision=source_revision,
                    )
                ),
                destination_snapshot=destination_snapshot,
                destination_fingerprint=destination_fingerprint,
                target_type="private",
                endpoint_key=OUTREACH_ENDPOINT_KEY,
                payload=envelope,
                max_attempts=_positive_env_integer(
                    "NANOBOT_OUTBOUND_MAX_ATTEMPTS",
                    OUTREACH_DEFAULT_MAX_ATTEMPTS,
                ),
                retry_deadline_at=(
                    runtime_at
                    + timedelta(seconds=_positive_env_number(
                        "NANOBOT_OUTBOUND_RETRY_DEADLINE_SECONDS",
                        OUTREACH_DEFAULT_RETRY_DEADLINE_SECONDS,
                    ))
                ),
                endpoint_config_revision=endpoint_revision,
                payload_contract_fingerprint=OUTREACH_PAYLOAD_CONTRACT,
                now=runtime_at,
            )
            if (
                evaluation_owner_token is not None
                and not _evaluation_lease_is_owned(
                    session,
                    user_id=user_id,
                    owner_token=evaluation_owner_token,
                )
            ):
                session.rollback()
                return {
                    "status": "lease_lost",
                    "log_id": row_id,
                    "forced": bool(forced),
                }
            session.commit()
        except IntegrityError:
            session.rollback()
            current = (
                session.query(ProactiveOutreachLog)
                .filter(ProactiveOutreachLog.idempotency_key == idempotency_key)
                .first()
            )
            if current is None:
                session.rollback()
                raise
            result = {
                "status": "skipped_duplicate",
                "log_id": int(current.id),
                "forced": bool(current.forced),
            }
            session.rollback()
            return result
        except BaseException:
            session.rollback()
            raise

        if (
            claim.delivery_mode == "legacy_direct"
            and queued.outbox_id is not None
            and publisher is not None
        ):
            await _deliver_legacy_outreach_leaf(
                session=session,
                outbox_id=int(queued.outbox_id),
                publisher=publisher,
                endpoint_config_revision=endpoint_revision,
                now=None,
            )
        result = _outreach_result(
            session,
            log_id=row_id,
            run_id=queued.run_id,
            outbox_id=queued.outbox_id,
            forced=forced,
            deduplicated=not queued.created,
        )
        session.rollback()
        return result


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
    schedule_expected_idempotency_key: str | None = None,
    created_at: datetime | None = None,
    delivered_at: datetime | None = None,
    publisher: Callable[[str, str, str], Any] | None = None,
    evaluation_owner_token: str | None = None,
) -> dict[str, Any]:
    """幂等持久化一条主动外呼候选，并按 cutover 模式转交 worker。"""

    return await _enqueue_outreach_outbox(
        user_id=user_id,
        idempotency_key=idempotency_key,
        grounding=grounding,
        judge_should=judge_should,
        judge_reason=judge_reason,
        next_check_at=next_check_at,
        next_intent=next_intent,
        message=message,
        forced=forced,
        db=db,
        schedule_row_id=schedule_row_id,
        schedule_expected_idempotency_key=schedule_expected_idempotency_key,
        created_at=created_at,
        delivered_at=delivered_at,
        publisher=publisher,
        evaluation_owner_token=evaluation_owner_token,
    )


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
        .filter(
            ProactiveOutreachLog.status.in_(
                (
                    "pending",
                    "candidate",
                    "sending",
                    "queued",
                    "delivering",
                    "retry_wait",
                    "blocked",
                )
            )
        )
        .order_by(ProactiveOutreachLog.created_at.desc(), ProactiveOutreachLog.id.desc())
        .first()
    )


def _latest_outreach_row(session: Session, user_id: str) -> ProactiveOutreachLog | None:
    return (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .order_by(ProactiveOutreachLog.created_at.desc(), ProactiveOutreachLog.id.desc())
        .first()
    )


def _last_sent_outreach(session: Session, user_id: str) -> ProactiveOutreachLog | None:
    return (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .filter(
            ProactiveOutreachLog.status.in_(
                ("sent", "sent_after_ambiguous_replay")
            )
        )
        .order_by(ProactiveOutreachLog.created_at.desc(), ProactiveOutreachLog.id.desc())
        .first()
    )


def _last_effective_outreach(session: Session, user_id: str) -> ProactiveOutreachLog | None:
    return (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .filter(
            ProactiveOutreachLog.status.in_(
                (
                    "sending",
                    "queued",
                    "delivering",
                    "retry_wait",
                    "sent",
                    "sent_after_ambiguous_replay",
                )
            )
        )
        .order_by(ProactiveOutreachLog.created_at.desc(), ProactiveOutreachLog.id.desc())
        .first()
    )


def _last_failed_forced_outreach(
    session: Session,
    user_id: str,
) -> ProactiveOutreachLog | None:
    return (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .filter(ProactiveOutreachLog.forced.is_(True))
        .filter(ProactiveOutreachLog.status.in_(("failed", "evaluation_error")))
        .order_by(ProactiveOutreachLog.created_at.desc(), ProactiveOutreachLog.id.desc())
        .first()
    )


def _next_forced_attempt(session: Session, user_id: str) -> int:
    completed_attempts = (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .filter(ProactiveOutreachLog.forced.is_(True))
        .count()
    )
    return int(completed_attempts) + 1


def _first_outreach_attempt(session: Session, user_id: str) -> ProactiveOutreachLog | None:
    return (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .filter(ProactiveOutreachLog.status != "cancelled")
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
        .filter(ProactiveOutreachLog.status != "cancelled")
        .filter(ProactiveOutreachLog.next_check_at.isnot(None))
        .order_by(ProactiveOutreachLog.created_at.desc(), ProactiveOutreachLog.id.desc())
        .first()
    )
    return row[0] if row and row[0] is not None else None


def _cancel_pre_clear_schedules(session: Session, user_id: str) -> int:
    clear_at = _history_clear_at(session, user_id)
    if clear_at is None:
        return 0
    rows = (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.user_id == user_id)
        .filter(
            ProactiveOutreachLog.status.in_(
                ("pending", "candidate", "evaluation_error")
            )
        )
        .filter(ProactiveOutreachLog.created_at <= clear_at)
        .all()
    )
    for row in rows:
        row.status = "cancelled"
    if rows:
        session.commit()
    return len(rows)


def _last_interaction_at(session: Session, user_id: str) -> datetime | None:
    row = (
        _private_conversation_query(session, user_id, roles=("user",))
        .with_entities(ConversationTurn.created_at)
        .order_by(ConversationTurn.created_at.desc(), ConversationTurn.id.desc())
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
    return calculate_outreach_surge_probability(
        last_interaction_at=last_interaction_at,
        now=now,
        min_prob=min_prob,
        max_prob=max_prob,
        ramp_minutes=ramp_minutes,
    )


def _upsert_pending_schedule(
    session: Session,
    *,
    user_id: str,
    idempotency_key: str,
    grounding: dict[str, Any],
    judge_reason: str,
    next_check_at: datetime | None,
    next_intent: str,
    created_at: datetime | None = None,
    evaluation_owner_token: str | None = None,
) -> ProactiveOutreachLog | None:
    if not _fence_evaluation_write(
        session,
        user_id=user_id,
        owner_token=evaluation_owner_token,
    ):
        session.rollback()
        return None
    generation_at = created_at or datetime.now()
    row = _current_pending_schedule(session, user_id)
    conflicting = (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.idempotency_key == idempotency_key)
        .first()
    )
    clear_at = _history_clear_at(session, user_id)
    if (
        conflicting is not None
        and conflicting.user_id == user_id
        and conflicting.status == "cancelled"
        and conflicting.outbound_run_id is not None
        and clear_at is not None
        and generation_at > clear_at
    ):
        idempotency_key = _post_clear_pending_key(
            user_id=user_id,
            idempotency_key=idempotency_key,
            clear_at=clear_at,
        )
        conflicting = (
            session.query(ProactiveOutreachLog)
            .filter(ProactiveOutreachLog.idempotency_key == idempotency_key)
            .first()
        )
    reset_created_at = False
    replacement_created_at: datetime | None = None
    if conflicting is not None and (
        row is None or int(conflicting.id) != int(row.id)
    ):
        reusable_cancelled = _cancelled_row_is_reusable(
            session,
            row=conflicting,
            user_id=user_id,
            generation_at=generation_at,
        )
        if reusable_cancelled:
            if row is not None:
                replacement_created_at = row.created_at
                row.status = "cancelled"
            row = conflicting
            reset_created_at = True
        elif conflicting is not None:
            conflicting_id = int(conflicting.id)
            session.rollback()
            return session.get(ProactiveOutreachLog, conflicting_id)
    elif row is None:
        row = ProactiveOutreachLog(user_id=user_id, status="pending")
        session.add(row)
        reset_created_at = True
    row.idempotency_key = idempotency_key
    row.grounding_json = _grounding_json(grounding)
    row.judge_should = False
    row.judge_reason = judge_reason
    row.next_check_at = next_check_at
    row.next_intent = next_intent
    row.message = ""
    row.forced = False
    row.status = "pending"
    if reset_created_at or row.created_at is None:
        row.created_at = replacement_created_at or created_at or datetime.now()
    session.commit()
    session.refresh(row)
    return row


def _record_evaluation_error(
    session: Session,
    *,
    user_id: str,
    idempotency_key: str,
    phase: str,
    grounding: dict[str, Any],
    error_type: str,
    reason: str,
    next_check_at: datetime | None,
    next_intent: str,
    created_at: datetime,
    forced: bool,
    evaluation_owner_token: str | None,
) -> ProactiveOutreachLog | None:
    """记录失败评估，既保留审计证据，也建立最长沉默起算点。"""

    if not _fence_evaluation_write(
        session,
        user_id=user_id,
        owner_token=evaluation_owner_token,
    ):
        session.rollback()
        return None
    audit_key = f"{idempotency_key}:evaluation:{phase}"
    row = (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.idempotency_key == audit_key)
        .first()
    )
    if row is not None and row.status != "evaluation_error":
        session.rollback()
        return row
    if row is None:
        row = ProactiveOutreachLog(
            user_id=user_id,
            idempotency_key=audit_key,
            created_at=created_at,
        )
        session.add(row)
    audit_grounding = dict(grounding)
    audit_grounding["evaluation_error"] = {
        "phase": phase,
        "error_type": error_type,
        "reason": reason[:500],
    }
    row.grounding_json = _grounding_json(audit_grounding)
    row.judge_should = None
    row.judge_reason = f"{phase}:{error_type}: {reason}"[:1000]
    row.next_check_at = next_check_at
    row.next_intent = next_intent[:500]
    row.message = ""
    row.forced = forced
    row.status = "evaluation_error"
    if row.created_at is None:
        row.created_at = created_at
    session.commit()
    session.refresh(row)
    return row


def _pending_schedule_conflict_result(
    session: Session,
    *,
    row: ProactiveOutreachLog,
    user_id: str,
) -> dict[str, Any]:
    status = str(row.status or "")
    if status in {
        "sending",
        "queued",
        "delivering",
        "retry_wait",
        "sent",
        "sent_after_ambiguous_replay",
        "failed",
        "blocked",
        "ambiguous",
        "legacy_ambiguous_hold",
    }:
        return {
            "status": "skipped_duplicate",
            "log_id": row.id,
            "forced": bool(row.forced),
        }
    if status == "cancelled":
        clear_at = _history_clear_at(session, user_id)
        return {
            "status": (
                "cancelled_history_clear"
                if clear_at is not None
                and (row.created_at is None or row.created_at <= clear_at)
                else "stale_schedule"
            ),
            "log_id": row.id,
            "forced": bool(row.forced),
        }
    return {
        "status": "state_error",
        "error_type": "unknown_delivery_state",
        "delivery_state": status,
        "log_id": row.id,
        "forced": bool(row.forced),
    }


def _upsert_research_candidate(
    session: Session,
    *,
    user_id: str,
    idempotency_key: str,
    grounding: dict[str, Any],
    judge_reason: str,
    next_check_at: datetime | None,
    next_intent: str,
    message: str,
    created_at: datetime,
    evaluation_owner_token: str | None = None,
) -> ProactiveOutreachLog | None:
    if not _fence_evaluation_write(
        session,
        user_id=user_id,
        owner_token=evaluation_owner_token,
    ):
        session.rollback()
        return None
    existing = (
        session.query(ProactiveOutreachLog)
        .filter(ProactiveOutreachLog.idempotency_key == idempotency_key)
        .first()
    )
    reusable_cancelled = _cancelled_row_is_reusable(
        session,
        row=existing,
        user_id=user_id,
        generation_at=created_at,
    )
    if (
        existing is not None
        and existing.status not in {"pending", "candidate"}
        and not reusable_cancelled
    ):
        session.rollback()
        return existing
    row = existing or _current_pending_schedule(session, user_id)
    if row is None:
        row = (
            session.query(ProactiveOutreachLog)
            .filter(ProactiveOutreachLog.idempotency_key == idempotency_key)
            .filter(ProactiveOutreachLog.status == "candidate")
            .first()
        )
    if row is None:
        row = ProactiveOutreachLog(user_id=user_id)
        session.add(row)
    row.idempotency_key = idempotency_key
    row.grounding_json = _grounding_json(grounding)
    row.judge_should = True
    row.judge_reason = judge_reason
    row.next_check_at = next_check_at
    row.next_intent = next_intent
    row.message = strip_think_blocks(str(message or "")).strip()
    row.forced = False
    row.status = "candidate"
    row.created_at = created_at
    session.commit()
    session.refresh(row)
    return row


async def _run_outreach_once_acquired(
    user_id: str,
    *,
    db: Session | None = None,
    now: datetime | None = None,
    min_interval_min: int = DEFAULT_MIN_INTERVAL_MIN,
    max_check_interval_min: int = DEFAULT_MAX_CHECK_INTERVAL_MIN,
    max_silence_min: int = DEFAULT_MAX_SILENCE_MIN,
    ambiguous_hold_min: int = DEFAULT_AMBIGUOUS_HOLD_MIN,
    judge_fn: Callable[..., dict[str, Any]] | None = None,
    generator_fn: Callable[..., str] | None = None,
    research_fn: Callable[..., Any] | None = None,
    thread_extractor: Callable[[list[dict[str, Any]]], list[str]] | None = None,
    publisher: Callable[[str, str, str], Any] | None = None,
    evaluation_owner_token: str | None = None,
    evaluation_generation_at: datetime | None = None,
) -> dict[str, Any]:
    """执行一次主动外呼检查；超过最长沉默窗口时强制开口。"""

    current = now or datetime.now()
    generation_at = evaluation_generation_at or current
    outbound_generation_at = _outbound_occurrence_utc(generation_at)
    with _session_scope(db) as session:
        _cancel_pre_clear_schedules(session, user_id)
        resumable_row = _current_schedule_row(session, user_id)
        if (
            resumable_row is not None
            and resumable_row.outbound_run_id is not None
            and resumable_row.status in {"candidate", "blocked"}
        ):
            try:
                generated_facts = _generated_row_facts(resumable_row)
            except outbound_delivery.OutboundConflictError as exc:
                session.rollback()
                return {
                    "status": "state_error",
                    "error_type": "generated_source_invalid",
                    "reason": str(exc)[:300],
                    "log_id": int(resumable_row.id),
                    "forced": bool(resumable_row.forced),
                }
            if generated_facts is not None:
                work, recovery_result = _resume_generated_outreach(
                    session,
                    row=resumable_row,
                    user_id=user_id,
                    evaluation_owner_token=evaluation_owner_token,
                )
                if recovery_result is not None:
                    return recovery_result
                if work is None:
                    raise RuntimeError("generated 恢复未返回工作或结果")
                effective_research_fn = research_fn
                if work.kind == "research" and effective_research_fn is None:
                    from core.proactive_research import run_proactive_research

                    effective_research_fn = run_proactive_research
                return await _run_generated_outreach(
                    session,
                    work=work,
                    user_id=user_id,
                    generator_fn=generator_fn or generate_outreach_message,
                    research_fn=effective_research_fn,
                    publisher=publisher,
                    evaluation_owner_token=evaluation_owner_token,
                )
        try:
            generation_gate = outbound_delivery.check_outbound_generation_gate(
                session,
                source_type=OUTREACH_SOURCE_TYPE,
                occurrence_at=outbound_generation_at,
                endpoint_key=OUTREACH_ENDPOINT_KEY,
                destination_fingerprint=(
                    outbound_delivery.proactive_outreach_destination_fingerprint(
                        user_id
                    )
                ),
                endpoint_config_revision=_outreach_endpoint_revision(),
                payload_contract_fingerprint=OUTREACH_PAYLOAD_CONTRACT,
                now=None,
            )
        except outbound_delivery.OutboundSafetyError as exc:
            session.rollback()
            return {
                "status": "skipped_delivery_control",
                "error_type": "delivery_control_unavailable",
                "reason": str(exc)[:300],
                "forced": False,
            }
        session.rollback()
        if not generation_gate.allowed:
            return {
                "status": (
                    "skipped_circuit"
                    if generation_gate.reason_type == "circuit_open"
                    else "skipped_delivery_control"
                ),
                "error_type": generation_gate.reason_type,
                "reason": generation_gate.reason_summary,
                "forced": False,
            }
        _cancel_pre_clear_schedules(session, user_id)
        latest_row = _latest_outreach_row(session, user_id)
        latest_status = str(latest_row.status or "") if latest_row is not None else ""
        if latest_row is not None and latest_status not in KNOWN_OUTREACH_STATES:
            return {
                "status": "state_error",
                "error_type": "unknown_delivery_state",
                "delivery_state": latest_status,
                "log_id": latest_row.id,
                "forced": bool(latest_row.forced),
            }
        clear_at = _history_clear_at(session, user_id)
        if (
            latest_row is not None
            and latest_status == "ambiguous"
            and latest_row.created_at is not None
            and (clear_at is None or latest_row.created_at > clear_at)
        ):
            hold_until = latest_row.created_at + timedelta(
                minutes=max(0, int(ambiguous_hold_min))
            )
            if current < hold_until:
                return {
                    "status": "skipped_ambiguous",
                    "next_check_at": hold_until.isoformat(),
                    "log_id": latest_row.id,
                    "forced": bool(latest_row.forced),
                }
        schedule_row = _current_schedule_row(session, user_id)
        if _blocked_outreach_without_outbox_is_current(session, schedule_row):
            candidate_message = strip_think_blocks(
                schedule_row.message or ""
            ).strip()
            if not candidate_message:
                return {
                    "status": "state_error",
                    "error_type": "blocked_candidate_empty",
                    "log_id": schedule_row.id,
                    "forced": bool(schedule_row.forced),
                }
            return await deliver_outreach_once(
                user_id=user_id,
                idempotency_key=str(schedule_row.idempotency_key or ""),
                grounding=_json_object(schedule_row.grounding_json),
                judge_should=bool(schedule_row.judge_should),
                judge_reason=schedule_row.judge_reason or "",
                next_check_at=schedule_row.next_check_at,
                next_intent=schedule_row.next_intent or "",
                message=candidate_message,
                forced=bool(schedule_row.forced),
                db=session,
                schedule_row_id=schedule_row.id,
                schedule_expected_idempotency_key=(
                    schedule_row.idempotency_key
                ),
                created_at=schedule_row.created_at,
                publisher=publisher,
                evaluation_owner_token=evaluation_owner_token,
            )
        if schedule_row is not None and schedule_row.status in {
            "queued",
            "delivering",
            "retry_wait",
            "blocked",
        }:
            return {
                "status": "skipped_duplicate",
                "log_id": schedule_row.id,
                "forced": bool(schedule_row.forced),
            }
        if (
            schedule_row is not None
            and schedule_row.status == "sending"
            and schedule_row.outbound_run_id is None
        ):
            stale_cutoff = current - timedelta(
                minutes=DEFAULT_SENDING_AMBIGUITY_MINUTES
            )
            if (
                schedule_row.created_at is None
                or schedule_row.created_at > stale_cutoff
            ):
                return {
                    "status": "skipped_duplicate",
                    "log_id": schedule_row.id,
                    "forced": bool(schedule_row.forced),
                }
            if not _fence_evaluation_write(
                session,
                user_id=user_id,
                owner_token=evaluation_owner_token,
            ):
                session.rollback()
                return {
                    "status": "lease_lost",
                    "log_id": schedule_row.id,
                    "forced": bool(schedule_row.forced),
                }
            retired = (
                session.query(ProactiveOutreachLog)
                .filter(ProactiveOutreachLog.id == schedule_row.id)
                .filter(ProactiveOutreachLog.user_id == user_id)
                .filter(ProactiveOutreachLog.status == "sending")
                .filter(ProactiveOutreachLog.created_at == schedule_row.created_at)
                .update(
                    {ProactiveOutreachLog.status: "ambiguous"},
                    synchronize_session=False,
                )
            )
            if retired != 1:
                session.rollback()
                return {
                    "status": "skipped_duplicate",
                    "log_id": schedule_row.id,
                    "forced": bool(schedule_row.forced),
                }
            session.commit()
            schedule_row = None
        last_effective = _last_effective_outreach(session, user_id)
        first_attempt = _first_outreach_attempt(session, user_id)
        interval_decision = evaluate_outreach_min_interval_gate(
            now=current,
            last_effective_at=last_effective.created_at
            if last_effective is not None
            else None,
            first_attempt_at=first_attempt.created_at
            if first_attempt is not None
            else None,
            min_interval_min=min_interval_min,
            max_silence_min=max_silence_min,
        )
        force = bool(interval_decision["forced"])
        if interval_decision["status"] == "skipped_min_interval":
            return {
                "status": "skipped_min_interval",
                "minutes_since_last": int(interval_decision["minutes_since_last"]),
            }

        if schedule_row is not None and schedule_row.status == "candidate":
            candidate_message = strip_think_blocks(schedule_row.message or "").strip()
            if candidate_message:
                return await deliver_outreach_once(
                    user_id=user_id,
                    idempotency_key=schedule_row.idempotency_key,
                    grounding=_json_object(schedule_row.grounding_json),
                    judge_should=True,
                    judge_reason=schedule_row.judge_reason or "",
                    next_check_at=schedule_row.next_check_at,
                    next_intent=schedule_row.next_intent or "",
                    message=candidate_message,
                    forced=False,
                    db=session,
                    schedule_row_id=schedule_row.id,
                    created_at=schedule_row.created_at or current,
                    delivered_at=current,
                    publisher=publisher,
                    evaluation_owner_token=evaluation_owner_token,
                )
            schedule_row.status = "cancelled"
            session.commit()
            schedule_row = None
        latest_next_check_at = _latest_next_check_at(session, user_id)
        schedule_anchor = (
            schedule_row.next_check_at
            if schedule_row is not None and schedule_row.next_check_at is not None
            else latest_next_check_at
        ) or current
        grounding_kwargs: dict[str, Any] = {"db": session, "now": current}
        if thread_extractor is not None:
            grounding_kwargs["thread_extractor"] = thread_extractor
        grounding = build_outreach_grounding(user_id, **grounding_kwargs)

        if force:
            last_failed_forced = _last_failed_forced_outreach(session, user_id)
            if (
                last_failed_forced is not None
                and last_failed_forced.next_check_at is not None
                and current < last_failed_forced.next_check_at
            ):
                return {
                    "status": "skipped_retry_backoff",
                    "next_check_at": last_failed_forced.next_check_at.isoformat(),
                    "forced": True,
                }
            reason = "超过最长沉默窗口，主动问候一次"
            anchor = _silence_anchor(session, user_id, last_effective=last_effective)
            force_anchor = (
                anchor.created_at
                if anchor is not None and anchor.created_at is not None
                else current
            )
            forced_attempt = _next_forced_attempt(session, user_id)
            forced_key = _outreach_key(
                user_id,
                force_anchor,
                forced=True,
                attempt=forced_attempt,
            )
            next_check_at = current + timedelta(
                minutes=max(1, min_interval_min)
            )
            forced_judge = {
                "should_reach_out": True,
                "reason": reason,
                "next_check_at": next_check_at.isoformat(),
                "next_intent": "",
                "outreach_kind": "message",
                "research_query": "",
                "error_type": None,
            }
            work, preparation_result = _prepare_generated_outreach(
                session,
                user_id=user_id,
                idempotency_key=forced_key,
                grounding=grounding,
                judge=forced_judge,
                kind="forced",
                next_check_at=next_check_at,
                next_intent="",
                created_at=generation_at,
                schedule_row_id=None,
                evaluation_owner_token=evaluation_owner_token,
            )
            if preparation_result is not None:
                return preparation_result
            if work is None:
                raise RuntimeError("forced generated 准备未返回工作或结果")
            return await _run_generated_outreach(
                session,
                work=work,
                user_id=user_id,
                generator_fn=generator_fn or generate_outreach_message,
                research_fn=None,
                publisher=publisher,
                evaluation_owner_token=evaluation_owner_token,
            )

        from core.proactive_candidate import evaluate_outreach_judgement
        from core.proactive_research import run_proactive_research

        idempotency_key = _outreach_key(user_id, schedule_anchor, forced=False)
        evaluation = evaluate_outreach_judgement(
            grounding=grounding,
            now=current,
            judge_fn=judge_fn or judge_outreach,
            min_interval_min=min_interval_min,
            max_check_interval_min=max_check_interval_min,
        )
        judge = dict(evaluation.get("judge") or {})
        next_check_at = _parse_iso_datetime(judge.get("next_check_at"))
        status = str(evaluation.get("status") or "")
        if status == "judge_error":
            error_type = str(
                evaluation.get("error_type") or "contract_error"
            )
            error_reason = str(evaluation.get("reason") or "")[:500]
            row = _record_evaluation_error(
                session,
                user_id=user_id,
                idempotency_key=idempotency_key,
                phase="judge",
                grounding=grounding,
                error_type=error_type,
                reason=error_reason,
                next_check_at=next_check_at,
                next_intent=str(judge.get("next_intent") or ""),
                created_at=generation_at,
                forced=False,
                evaluation_owner_token=evaluation_owner_token,
            )
            if row is None:
                return {
                    "status": "lease_lost",
                    "log_id": None,
                    "forced": False,
                }
            return {
                "status": "judge_error",
                "error_type": error_type,
                "reason": error_reason,
                "next_check_at": _iso_or_empty(next_check_at),
                "log_id": row.id,
                "forced": False,
            }
        if status == "no_candidate":
            row = _upsert_pending_schedule(
                session,
                user_id=user_id,
                idempotency_key=_outreach_key(user_id, next_check_at or schedule_anchor, forced=False),
                grounding=grounding,
                judge_reason=str(judge.get("reason") or ""),
                next_check_at=next_check_at,
                next_intent=str(judge.get("next_intent") or ""),
                created_at=generation_at,
                evaluation_owner_token=evaluation_owner_token,
            )
            if row is None:
                return {
                    "status": "lease_lost",
                    "log_id": None,
                    "forced": False,
                }
            if row.status != "pending":
                return _pending_schedule_conflict_result(
                    session,
                    row=row,
                    user_id=user_id,
                )
            return {"status": "pending", "forced": False, "log_id": row.id}

        if status != "generation_required":
            error_reason = f"未知候选状态: {status}"[:500]
            row = _record_evaluation_error(
                session,
                user_id=user_id,
                idempotency_key=idempotency_key,
                phase="candidate_contract",
                grounding=grounding,
                error_type="contract_error",
                reason=error_reason,
                next_check_at=next_check_at,
                next_intent=str(judge.get("next_intent") or ""),
                created_at=generation_at,
                forced=False,
                evaluation_owner_token=evaluation_owner_token,
            )
            if row is None:
                return {
                    "status": "lease_lost",
                    "log_id": None,
                    "forced": False,
                }
            return {
                "status": "judge_error",
                "error_type": "contract_error",
                "reason": error_reason,
                "next_check_at": _iso_or_empty(next_check_at),
                "log_id": row.id,
                "forced": False,
            }
        kind = (
            "research"
            if str(judge.get("outreach_kind") or "message") == "research"
            else "message"
        )
        work, preparation_result = _prepare_generated_outreach(
            session,
            user_id=user_id,
            idempotency_key=idempotency_key,
            grounding=grounding,
            judge=judge,
            kind=kind,
            next_check_at=next_check_at,
            next_intent=str(judge.get("next_intent") or ""),
            created_at=generation_at,
            schedule_row_id=(
                int(schedule_row.id)
                if schedule_row is not None and schedule_row.status == "pending"
                else None
            ),
            evaluation_owner_token=evaluation_owner_token,
        )
        if preparation_result is not None:
            return preparation_result
        if work is None:
            raise RuntimeError("generated 准备未返回工作或结果")
        return await _run_generated_outreach(
            session,
            work=work,
            user_id=user_id,
            generator_fn=generator_fn or generate_outreach_message,
            research_fn=(
                research_fn or run_proactive_research
                if kind == "research"
                else None
            ),
            publisher=publisher,
            evaluation_owner_token=evaluation_owner_token,
        )


async def run_outreach_once(
    user_id: str,
    *,
    db: Session | None = None,
    now: datetime | None = None,
    min_interval_min: int = DEFAULT_MIN_INTERVAL_MIN,
    max_check_interval_min: int = DEFAULT_MAX_CHECK_INTERVAL_MIN,
    max_silence_min: int = DEFAULT_MAX_SILENCE_MIN,
    ambiguous_hold_min: int = DEFAULT_AMBIGUOUS_HOLD_MIN,
    judge_fn: Callable[..., dict[str, Any]] | None = None,
    generator_fn: Callable[..., str] | None = None,
    research_fn: Callable[..., Any] | None = None,
    thread_extractor: Callable[[list[dict[str, Any]]], list[str]] | None = None,
    publisher: Callable[[str, str, str], Any] | None = None,
) -> dict[str, Any]:
    """获取按用户评估租约后执行一次主动外呼检查。"""

    current = now or datetime.now()
    with _session_scope(db) as session:
        lease_acquired_at = datetime.now()
        owner_token = _acquire_evaluation_lease(
            session,
            user_id=user_id,
            now=lease_acquired_at,
        )
        if owner_token is None:
            return {"status": "skipped_in_progress", "forced": False}
        clear_at = _history_clear_at(session, user_id)
        evaluation_generation_at = current
        if clear_at is not None and evaluation_generation_at <= clear_at:
            evaluation_generation_at = max(
                lease_acquired_at,
                clear_at + timedelta(microseconds=1),
            )
        try:
            return await _run_outreach_once_acquired(
                user_id,
                db=session,
                now=current,
                min_interval_min=min_interval_min,
                max_check_interval_min=max_check_interval_min,
                max_silence_min=max_silence_min,
                ambiguous_hold_min=ambiguous_hold_min,
                judge_fn=judge_fn,
                generator_fn=generator_fn,
                research_fn=research_fn,
                thread_extractor=thread_extractor,
                publisher=publisher,
                evaluation_owner_token=owner_token,
                evaluation_generation_at=evaluation_generation_at,
            )
        except BaseException:
            session.rollback()
            raise
        finally:
            try:
                _release_evaluation_lease(
                    session,
                    user_id=user_id,
                    owner_token=owner_token,
                )
            except Exception:
                logger.exception(
                    "释放主动外呼评估租约失败，等待租约 TTL 自动失效: user_id=%s",
                    user_id,
                )


async def run_outreach_dry_run_once(
    user_id: str,
    *,
    db: Session | None = None,
    now: datetime | None = None,
    min_interval_min: int = DEFAULT_MIN_INTERVAL_MIN,
    max_check_interval_min: int = DEFAULT_MAX_CHECK_INTERVAL_MIN,
    judge_fn: Callable[..., dict[str, Any]] | None = None,
    generator_fn: Callable[..., str] | None = None,
    research_fn: Callable[..., Any] | None = None,
    thread_extractor: Callable[[list[dict[str, Any]]], list[str]] | None = None,
) -> dict[str, Any]:
    """只评估候选，不创建外呼记录，也不持有 publisher。"""

    current = now or datetime.now()
    with _session_scope(db) as session:
        grounding_kwargs: dict[str, Any] = {"db": session, "now": current}
        if thread_extractor is not None:
            grounding_kwargs["thread_extractor"] = thread_extractor
        grounding = build_outreach_grounding(user_id, **grounding_kwargs)

        from core.proactive_candidate import evaluate_outreach_candidate
        from core.proactive_research import run_proactive_research

        return await evaluate_outreach_candidate(
            user_id=user_id,
            request_id=f"dry-run:{user_id}:{current.replace(microsecond=0).isoformat()}",
            grounding=grounding,
            now=current,
            judge_fn=judge_fn or judge_outreach,
            generator_fn=generator_fn or generate_outreach_message,
            research_fn=research_fn or run_proactive_research,
            context_summary=_grounding_json_for_model(grounding),
            min_interval_min=min_interval_min,
            max_check_interval_min=max_check_interval_min,
        )


async def run_outreach_due_once(
    user_id: str,
    *,
    db: Session | None = None,
    now: datetime | None = None,
    min_interval_min: int | None = None,
    max_check_interval_min: int | None = None,
    max_silence_min: int | None = None,
    ambiguous_hold_min: int | None = None,
    surge_min_prob: float | None = None,
    surge_max_prob: float | None = None,
    random_fn: Callable[[], float] | None = None,
    publisher: Callable[[str, str, str], Any] | None = None,
) -> dict[str, Any]:
    """执行一次到期外呼检查；安静时段直接跳过。"""

    current = now or datetime.now()
    hours = active_hours(user_id, db=db)
    effective_max_silence_min = (
        max_silence_min if max_silence_min is not None else DEFAULT_MAX_SILENCE_MIN
    )
    with _session_scope(db) as session:
        _cancel_pre_clear_schedules(session, user_id)
        last_effective = _last_effective_outreach(session, user_id)
        first_attempt = _first_outreach_attempt(session, user_id)
        next_check_at = _latest_next_check_at(session, user_id)
        last_interaction_at = _last_interaction_at(session, user_id)
        policy_kwargs = {
            "now": current,
            "active_hours": frozenset(hours),
            "last_effective_at": last_effective.created_at
            if last_effective is not None
            else None,
            "first_attempt_at": first_attempt.created_at
            if first_attempt is not None
            else None,
            "last_interaction_at": last_interaction_at,
            "next_check_at": next_check_at,
            "max_silence_min": effective_max_silence_min,
            "surge_min_prob": surge_min_prob
            if surge_min_prob is not None
            else DEFAULT_SURGE_MIN_PROB,
            "surge_max_prob": surge_max_prob
            if surge_max_prob is not None
            else DEFAULT_SURGE_MAX_PROB,
        }
        decision = evaluate_outreach_due_gate(**policy_kwargs)
        if decision["status"] == "surge_roll_required":
            roll = (random_fn or random.random)()
            decision = evaluate_outreach_due_gate(
                **policy_kwargs,
                surge_roll=roll,
            )
        if decision["status"] == "skipped_quiet_hours":
            return {"status": "skipped_quiet_hours", "hour": current.hour}
        if decision["status"] == "skipped_not_due":
            return {
                "status": "skipped_not_due",
                "next_check_at": str(decision["next_check_at"]),
                "surge_probability": float(decision["surge_probability"]),
                "surge_roll": float(decision["surge_roll"]),
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
        ambiguous_hold_min=(
            ambiguous_hold_min
            if ambiguous_hold_min is not None
            else DEFAULT_AMBIGUOUS_HOLD_MIN
        ),
        publisher=publisher,
    )


def proactive_outreach_scheduler(stop_event: threading.Event) -> None:
    """后台主动外呼调度器。"""

    logger.info("Proactive outreach scheduler started.")
    next_evaluation_at = 0.0
    while not stop_event.is_set():
        enabled = False
        try:
            enabled = settings.get_bool("proactive_outreach.enabled", False)
            monotonic_now = time.monotonic()
            if not enabled:
                next_evaluation_at = 0.0
            elif monotonic_now >= next_evaluation_at:
                interval_min = settings.get_int(
                    "proactive_outreach.fallback_interval_min",
                    120,
                )
                next_evaluation_at = monotonic_now + max(
                    60.0,
                    float(interval_min) * 60.0,
                )
                user_ids = sorted(get_super_user_ids())
                if not user_ids:
                    logger.info(
                        "Proactive outreach skipped: no superuser configured."
                    )
                for user_id in user_ids:
                    if stop_event.is_set():
                        break
                    result = run_awaitable_sync(run_outreach_due_once(
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
                        ambiguous_hold_min=settings.get_int(
                            "proactive_outreach.ambiguous_hold_min",
                            DEFAULT_AMBIGUOUS_HOLD_MIN,
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
                    status = str((result or {}).get("status") or "")
                    if status in {
                        "judge_error",
                        "generation_error",
                        "research_blocked",
                        "ambiguous",
                        "lease_lost",
                        "state_error",
                    }:
                        logger.warning(
                            "Proactive outreach evaluation did not produce a normal "
                            "schedule: user_id=%s status=%s error_type=%s "
                            "reason_code=%s log_id=%s",
                            user_id,
                            status,
                            str((result or {}).get("error_type") or ""),
                            str((result or {}).get("reason_code") or ""),
                            (result or {}).get("log_id"),
                        )
        except Exception as exc:
            logger.exception("Proactive outreach scheduler error: %s", exc)

        wait_seconds = OUTREACH_SCHEDULER_POLL_SECONDS
        if enabled and next_evaluation_at > 0:
            wait_seconds = min(
                wait_seconds,
                max(0.1, next_evaluation_at - time.monotonic()),
            )
        stop_event.wait(timeout=wait_seconds)

    logger.info("Proactive outreach scheduler stopped.")

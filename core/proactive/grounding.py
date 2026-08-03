"""主动外呼 Grounding 的 Port 与生产组合。"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session

from core.database import SessionLocal
from core.db.models.chat import ConversationTurn
from core.db.models.persona import Persona, PersonaFact
from core.db.models.proactive import ProactiveOutreachLog
from core.model_provider.response_normalization import strip_think_blocks
from core.model_provider.route_runtime import call_model_route_response
from core.proactive.model_policy import coerce_model_response
from core.proactive.prompt_policy import invoke_outreach_task
from core.proactive.repository import history_clear_at, private_conversation_query
from core.proactive.serialization import (
    MODEL_GROUNDING_RECENT_OUTREACH_LIMIT,
    PERSISTED_RECENT_OUTREACH_TEXT_LIMIT,
    hours_since,
    iso_or_empty,
    json_object,
    time_period_label,
    truncate_text,
    weekday_label,
)
from core.proactive_candidate import DEFAULT_ACTIVE_HOURS


ACTIVE_HOURS_MIN_SAMPLES = 5
ACTIVE_HOUR_WINDOW_RADIUS = 1
RECENT_THREAD_STATUSES = frozenset({
    "open",
    "completed",
    "dismissed",
    "unknown",
})


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


def extract_recent_threads(
    recent_messages: list[dict[str, Any]],
    *,
    llm_call: Callable[..., Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """从最近对话中提炼带生命周期与证据位置的话题。"""

    def record(**values: Any) -> None:
        if diagnostics is None:
            return
        diagnostics.clear()
        diagnostics.update(values)

    compact_messages: list[dict[str, Any]] = []
    for item in recent_messages:
        if not str(item.get("content") or "").strip():
            continue
        compact_messages.append({
            "message_index": len(compact_messages),
            "role": str(item.get("role") or ""),
            "content": truncate_text(str(item.get("content") or ""), 240),
            "created_at": str(item.get("created_at") or ""),
        })
    if not compact_messages:
        record(status="empty_input")
        return []
    if llm_call is None and os.environ.get("NANOBOT_TESTING") == "1":
        record(status="skipped", reason="testing")
        return []

    caller = llm_call or call_model_route_response
    try:
        response = coerce_model_response(invoke_outreach_task(
            caller,
            task_key="outreach_extract",
            payload=json.dumps(compact_messages, ensure_ascii=False),
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
        record(status="error", error_type="model_error")
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

    threads: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in parsed:
        if not isinstance(item, dict):
            continue
        topic_value = item.get("topic")
        status_value = item.get("status")
        indexes_value = item.get("evidence_message_indexes")
        if (
            not isinstance(topic_value, str)
            or not isinstance(status_value, str)
            or not isinstance(indexes_value, list)
        ):
            continue
        topic = truncate_text(topic_value, 120)
        status = status_value.strip().lower()
        if not topic or status not in RECENT_THREAD_STATUSES:
            continue
        evidence_indexes = sorted({
            index
            for index in indexes_value
            if type(index) is int and 0 <= index < len(compact_messages)
        })
        if not evidence_indexes or not any(
            compact_messages[index].get("role") == "user"
            for index in evidence_indexes
        ):
            continue
        dedupe_key = (topic, status)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        latest_index = evidence_indexes[-1]
        threads.append({
            "evidence_id": f"recent_thread:{len(threads)}",
            "topic": topic,
            "status": status,
            "evidence_message_indexes": evidence_indexes,
            "updated_at": str(
                compact_messages[latest_index].get("created_at") or ""
            ),
        })
        if len(threads) >= 3:
            break
    record(
        status="success",
        finish_reason=response.finish_reason,
        thread_count=len(threads),
    )
    return threads


def _is_outbound_delivery_context_turn(row: ConversationTurn) -> bool:
    meta = json_object(row.meta_json)
    return str(meta.get("kind") or "") == "outbound_delivery_summary"


class OutreachGroundingPort(Protocol):
    def build(
        self,
        user_id: str,
        *,
        db: Session | None = None,
        recent_limit: int = 20,
        now: datetime | None = None,
        thread_extractor: Callable[[list[dict[str, Any]]], list[Any]] | None = None,
    ) -> dict[str, Any]: ...

    def active_hours(
        self,
        user_id: str,
        *,
        db: Session | None = None,
        min_samples: int = ACTIVE_HOURS_MIN_SAMPLES,
    ) -> set[int]: ...


class SqlAlchemyOutreachGroundingService:
    """使用当前关系模型构造最小、有界 Grounding 的生产服务。"""

    def build(
        self,
        user_id: str,
        *,
        db: Session | None = None,
        recent_limit: int = 20,
        now: datetime | None = None,
        thread_extractor: Callable[[list[dict[str, Any]]], list[Any]] | None = None,
    ) -> dict[str, Any]:
        current = now or datetime.now()
        with _session_scope(db) as session:
            persona = session.query(Persona).filter(
                Persona.user_id == user_id,
                Persona.status == "active",
            ).first()
            persona_fact_rows = (
                session.query(PersonaFact)
                .filter(
                    PersonaFact.user_id == user_id,
                    PersonaFact.status == "active",
                    PersonaFact.inject_policy == "auto",
                )
                .order_by(
                    PersonaFact.last_seen.desc(),
                    PersonaFact.id.desc(),
                )
                .limit(12)
                .all()
            )
            recent_candidates = (
                private_conversation_query(
                    session,
                    user_id,
                    roles=("user", "assistant", "model"),
                )
                .order_by(
                    ConversationTurn.created_at.desc(),
                    ConversationTurn.id.desc(),
                )
                .limit(max(20, min(500, int(recent_limit) * 4)))
                .all()
            )
            recent_rows = [
                row
                for row in recent_candidates
                if not _is_outbound_delivery_context_turn(row)
            ][:max(1, int(recent_limit))]
            recent_messages = [
                {
                    "role": "assistant" if row.role == "model" else (row.role or ""),
                    "content": row.content or "",
                    "created_at": iso_or_empty(row.created_at),
                    "session_id": row.session_id or "",
                    "sender_name": "",
                }
                for row in reversed(recent_rows)
            ]
            last_user_row = (
                private_conversation_query(session, user_id, roles=("user",))
                .order_by(
                    ConversationTurn.created_at.desc(),
                    ConversationTurn.id.desc(),
                )
                .first()
            )

            outreach_state_query = session.query(ProactiveOutreachLog).filter(
                ProactiveOutreachLog.user_id == user_id
            )
            clear_at = history_clear_at(session, user_id)
            if clear_at is not None:
                outreach_state_query = outreach_state_query.filter(
                    ProactiveOutreachLog.created_at > clear_at
                )
            latest_outreach_state = outreach_state_query.order_by(
                ProactiveOutreachLog.created_at.desc(),
                ProactiveOutreachLog.id.desc(),
            ).first()
            recent_outreach_rows = outreach_state_query.filter(
                ProactiveOutreachLog.status.in_((
                    "sending",
                    "ambiguous",
                    "sent",
                    "sent_after_ambiguous_replay",
                ))
            ).order_by(
                ProactiveOutreachLog.created_at.desc(),
                ProactiveOutreachLog.id.desc(),
            ).limit(MODEL_GROUNDING_RECENT_OUTREACH_LIMIT).all()
            last_outreach = recent_outreach_rows[0] if recent_outreach_rows else None
            last_user_hours = hours_since(
                current,
                last_user_row.created_at if last_user_row else None,
            )
            last_outreach_hours = hours_since(
                current,
                last_outreach.created_at if last_outreach else None,
            )
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
                    "weekday": weekday_label(current),
                    "period": time_period_label(current),
                    "hour": current.hour,
                },
                "persona": json_object(persona.persona_json if persona else "{}"),
                "persona_facts": [
                    {
                        "evidence_id": f"persona_fact:{row.id}",
                        "content": truncate_text(row.content or "", 240),
                        "fact_type": row.fact_type or "",
                        "confidence": row.confidence or "",
                        "evidence_count": int(row.evidence_count or 0),
                        "last_seen": iso_or_empty(row.last_seen),
                    }
                    for row in persona_fact_rows
                    if str(row.content or "").strip()
                ],
                "recent_messages": recent_messages,
                "recent_threads": recent_threads,
                "recent_threads_diagnostics": recent_thread_diagnostics,
                "hours_since_last_user_message": last_user_hours,
                "last_user_message": (
                    {
                        "content": truncate_text(
                            last_user_row.content if last_user_row else ""
                        ),
                        "created_at": iso_or_empty(
                            last_user_row.created_at if last_user_row else None
                        ),
                        "hours_ago": last_user_hours,
                    }
                    if last_user_row
                    else None
                ),
                "days_since_last_outreach": (
                    last_outreach_hours / 24.0
                    if last_outreach_hours is not None
                    else None
                ),
                "next_intent": (
                    latest_outreach_state.next_intent
                    if latest_outreach_state is not None
                    else ""
                ),
                "recent_outreaches": [
                    {
                        "status": row.status or "",
                        "forced": bool(row.forced),
                        "message": truncate_text(
                            row.message or "",
                            PERSISTED_RECENT_OUTREACH_TEXT_LIMIT,
                        ),
                        "next_intent": truncate_text(row.next_intent or ""),
                        "created_at": iso_or_empty(row.created_at),
                    }
                    for row in reversed(recent_outreach_rows)
                ],
                "last_outreach": (
                    {
                        "status": last_outreach.status or "",
                        "forced": bool(last_outreach.forced),
                        "message": last_outreach.message or "",
                        "judge_reason": last_outreach.judge_reason or "",
                        "next_check_at": iso_or_empty(last_outreach.next_check_at),
                        "created_at": iso_or_empty(last_outreach.created_at),
                    }
                    if last_outreach
                    else None
                ),
            }

    def active_hours(
        self,
        user_id: str,
        *,
        db: Session | None = None,
        min_samples: int = ACTIVE_HOURS_MIN_SAMPLES,
    ) -> set[int]:
        with _session_scope(db) as session:
            rows = (
                private_conversation_query(session, user_id, roles=("user",))
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
            for offset in range(
                -ACTIVE_HOUR_WINDOW_RADIUS,
                ACTIVE_HOUR_WINDOW_RADIUS + 1,
            )
        }


DEFAULT_OUTREACH_GROUNDING: OutreachGroundingPort = (
    SqlAlchemyOutreachGroundingService()
)


def build_outreach_grounding(
    user_id: str,
    *,
    db: Session | None = None,
    recent_limit: int = 20,
    now: datetime | None = None,
    thread_extractor: Callable[[list[dict[str, Any]]], list[Any]] | None = None,
) -> dict[str, Any]:
    return DEFAULT_OUTREACH_GROUNDING.build(
        user_id,
        db=db,
        recent_limit=recent_limit,
        now=now,
        thread_extractor=thread_extractor,
    )


def active_hours(
    user_id: str,
    *,
    db: Session | None = None,
    min_samples: int = ACTIVE_HOURS_MIN_SAMPLES,
) -> set[int]:
    return DEFAULT_OUTREACH_GROUNDING.active_hours(
        user_id,
        db=db,
        min_samples=min_samples,
    )


__all__ = [
    "ACTIVE_HOURS_MIN_SAMPLES",
    "DEFAULT_OUTREACH_GROUNDING",
    "OutreachGroundingPort",
    "SqlAlchemyOutreachGroundingService",
    "active_hours",
    "build_outreach_grounding",
    "extract_recent_threads",
]

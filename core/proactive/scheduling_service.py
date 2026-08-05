"""主动外呼到期、静默窗口与早期 surge 调度服务。"""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from core.outbound.contracts import PROACTIVE_GENERATION_METADATA_KEY
from core.proactive.config import (
    DEFAULT_AMBIGUOUS_HOLD_MIN,
    DEFAULT_DAILY_DELIVERY_QUOTA,
    DEFAULT_MAX_CHECK_INTERVAL_MIN,
    DEFAULT_MAX_SILENCE_MIN,
    DEFAULT_MIN_INTERVAL_MIN,
    DEFAULT_REPEAT_TOPIC_COOLDOWN_MIN,
    DEFAULT_SURGE_MAX_PROB,
    DEFAULT_SURGE_MIN_PROB,
)
from core.proactive.grounding import active_hours
from core.proactive.model_policy import parse_iso_datetime
from core.proactive.orchestrator import run_outreach_once
from core.proactive.repository import (
    cancel_pre_clear_schedules as _cancel_pre_clear_schedules,
    first_outreach_attempt as _first_outreach_attempt,
    last_effective_outreach as _last_effective_outreach,
    last_interaction_at as _last_interaction_at,
    latest_next_check_at as _latest_next_check_at,
    latest_outreach_row as _latest_outreach_row,
)
from core.proactive.runtime_support import session_scope as _session_scope
from core.proactive.serialization import json_object
from core.proactive.topic_policy import generation_input_grounding
from core.proactive_candidate import evaluate_outreach_due_gate
from core.trigger_runtime import TriggerKind


def _last_evaluation_at(row: Any) -> datetime | None:
    if row is None:
        return None
    persisted = json_object(getattr(row, "grounding_json", None))
    grounding = generation_input_grounding(
        persisted,
        metadata_key=PROACTIVE_GENERATION_METADATA_KEY,
    )
    now_block = grounding.get("now")
    evaluated_at = (
        parse_iso_datetime(now_block.get("iso"))
        if isinstance(now_block, dict)
        else None
    )
    created_at = getattr(row, "created_at", None)
    if evaluated_at is None:
        return created_at
    if created_at is None:
        return evaluated_at
    return max(created_at, evaluated_at)


async def run_outreach_due_once(
    user_id: str,
    *,
    db: Session | None = None,
    now: datetime | None = None,
    min_interval_min: int | None = None,
    max_check_interval_min: int | None = None,
    max_silence_min: int | None = None,
    ambiguous_hold_min: int | None = None,
    repeat_topic_cooldown_min: int | None = None,
    daily_delivery_quota: int | None = None,
    allow_early_surge: bool = False,
    surge_min_prob: float | None = None,
    surge_max_prob: float | None = None,
    random_fn: Callable[[], float] | None = None,
    publisher: Callable[[str, str, str], Any] | None = None,
    trigger_kind: TriggerKind | str = TriggerKind.HEARTBEAT,
    outreach_runner: Callable[..., Any] = run_outreach_once,
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
        latest_evaluation = _latest_outreach_row(session, user_id)
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
            "last_evaluation_at": _last_evaluation_at(latest_evaluation),
            "last_interaction_at": last_interaction_at,
            "next_check_at": next_check_at,
            "max_silence_min": effective_max_silence_min,
            "surge_min_prob": surge_min_prob
            if surge_min_prob is not None
            else DEFAULT_SURGE_MIN_PROB,
            "surge_max_prob": surge_max_prob
            if surge_max_prob is not None
            else DEFAULT_SURGE_MAX_PROB,
            "allow_early_surge": bool(allow_early_surge),
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
                "surge_roll": (
                    float(decision["surge_roll"])
                    if decision.get("surge_roll") is not None
                    else None
                ),
            }

    return await outreach_runner(
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
        repeat_topic_cooldown_min=(
            repeat_topic_cooldown_min
            if repeat_topic_cooldown_min is not None
            else DEFAULT_REPEAT_TOPIC_COOLDOWN_MIN
        ),
        daily_delivery_quota=(
            daily_delivery_quota
            if daily_delivery_quota is not None
            else DEFAULT_DAILY_DELIVERY_QUOTA
        ),
        publisher=publisher,
        trigger_kind=trigger_kind,
        trigger_source_ref=(
            f"proactive-due:{TriggerKind(trigger_kind).value}:"
            f"{user_id}:{current.isoformat()}"
        ),
        trigger_idempotency_key=(
            f"proactive-due:{TriggerKind(trigger_kind).value}:"
            f"{user_id}:{current.isoformat()}"
        ),
        trigger_evaluated_status=str(decision.get("status") or "allowed"),
    )



__all__ = ["run_outreach_due_once"]

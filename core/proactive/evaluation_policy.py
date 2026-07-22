"""主动外呼重复主题、沉默窗口与 surge 组合策略。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from core.db.models.proactive import ProactiveOutreachLog
from core.outbound.contracts import PROACTIVE_GENERATION_METADATA_KEY
from core.proactive.repository import (
    first_outreach_attempt as _first_outreach_attempt,
    last_sent_outreach as _last_sent_outreach,
)
from core.proactive.scheduling_policy import (
    silence_anchor as _policy_silence_anchor,
    silence_floor_hit as _policy_silence_floor_hit,
    surge_probability,
)
from core.proactive.serialization import json_object as _json_object
from core.proactive.topic_policy import repeated_topic_guard as _topic_guard


def repeated_topic_guard(
    session: Session,
    *,
    user_id: str,
    grounding: dict[str, Any],
    judge: dict[str, Any],
    now: datetime,
    cooldown_min: int,
) -> dict[str, Any] | None:
    previous = _last_sent_outreach(session, user_id)
    return _topic_guard(
        previous,
        previous_grounding=(
            _json_object(previous.grounding_json) if previous is not None else {}
        ),
        grounding=grounding,
        judge=judge,
        now=now,
        cooldown_min=cooldown_min,
        metadata_key=PROACTIVE_GENERATION_METADATA_KEY,
    )


def silence_floor_hit(
    session: Session,
    user_id: str,
    *,
    current: datetime,
    max_silence_min: int,
    last_sent: ProactiveOutreachLog | None = None,
) -> bool:
    return _policy_silence_floor_hit(
        _first_outreach_attempt(session, user_id),
        current=current,
        max_silence_min=max_silence_min,
        last_effective=last_sent,
    )


def silence_anchor(
    session: Session,
    user_id: str,
    *,
    last_effective: ProactiveOutreachLog | None = None,
):
    return _policy_silence_anchor(
        _first_outreach_attempt(session, user_id),
        last_effective=last_effective,
    )


def surge_probability_for_user(
    *,
    last_interaction_at: datetime | None,
    now: datetime,
    min_prob: float,
    max_prob: float,
    ramp_minutes: int,
) -> float:
    return surge_probability(
        last_interaction_at=last_interaction_at,
        now=now,
        min_prob=min_prob,
        max_prob=max_prob,
        ramp_minutes=ramp_minutes,
    )



__all__ = [
    "repeated_topic_guard",
    "silence_anchor",
    "silence_floor_hit",
    "surge_probability_for_user",
]

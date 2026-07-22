"""主动外呼调度时间的纯策略。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from core.proactive_candidate import calculate_outreach_surge_probability


class CreatedAtRecord(Protocol):
    created_at: datetime | None


def silence_anchor(
    first_attempt: CreatedAtRecord | None,
    *,
    last_effective: CreatedAtRecord | None = None,
) -> CreatedAtRecord | None:
    return last_effective if last_effective is not None else first_attempt


def silence_floor_hit(
    first_attempt: CreatedAtRecord | None,
    *,
    current: datetime,
    max_silence_min: int,
    last_effective: CreatedAtRecord | None = None,
) -> bool:
    anchor = silence_anchor(first_attempt, last_effective=last_effective)
    return (
        anchor is not None
        and anchor.created_at is not None
        and current - anchor.created_at >= timedelta(minutes=max_silence_min)
    )


def surge_probability(
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


__all__ = ["silence_anchor", "silence_floor_hit", "surge_probability"]

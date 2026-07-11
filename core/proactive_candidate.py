"""主动外呼的纯候选评估内核，不包含数据库和发布依赖。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Callable

from clients.classifier_client import strip_think_blocks
from core.proactive_research import ResearchBudget, ResearchRequest


DEFAULT_ACTIVE_HOURS = frozenset(range(8, 23))
DEFAULT_MIN_INTERVAL_MIN = 30
DEFAULT_MAX_CHECK_INTERVAL_MIN = 1440
DEFAULT_MAX_SILENCE_MIN = 2880
DEFAULT_SURGE_MIN_PROB = 0.1
DEFAULT_SURGE_MAX_PROB = 0.6


def calculate_outreach_surge_probability(
    *,
    last_interaction_at: datetime | None,
    now: datetime,
    min_prob: float,
    max_prob: float,
    ramp_minutes: int,
) -> float:
    """按最近用户互动距今时长计算临时提前检查概率。"""

    lower = max(0.0, min(1.0, float(min_prob)))
    upper = max(0.0, min(1.0, float(max_prob)))
    if upper < lower:
        lower, upper = upper, lower
    if ramp_minutes <= 0 or last_interaction_at is None:
        return upper
    elapsed_min = max(0.0, (now - last_interaction_at).total_seconds() / 60.0)
    ratio = min(1.0, elapsed_min / float(ramp_minutes))
    return lower + (upper - lower) * ratio


def _silence_floor_reached(
    *,
    now: datetime,
    last_effective_at: datetime | None,
    first_attempt_at: datetime | None,
    max_silence_min: int,
) -> bool:
    anchor = last_effective_at or first_attempt_at
    return bool(
        anchor is not None
        and now - anchor >= timedelta(minutes=max_silence_min)
    )


def evaluate_outreach_due_gate(
    *,
    now: datetime,
    active_hours: set[int] | frozenset[int] = DEFAULT_ACTIVE_HOURS,
    last_effective_at: datetime | None = None,
    first_attempt_at: datetime | None = None,
    last_interaction_at: datetime | None = None,
    next_check_at: datetime | None = None,
    max_silence_min: int = DEFAULT_MAX_SILENCE_MIN,
    surge_min_prob: float = DEFAULT_SURGE_MIN_PROB,
    surge_max_prob: float = DEFAULT_SURGE_MAX_PROB,
    surge_roll: float | None = None,
) -> dict[str, Any]:
    """复现生产 due 阶段：安静时段、强制兜底、next-check 与 surge。"""

    if now.hour not in active_hours:
        return {"status": "skipped_quiet_hours", "forced": False}

    forced = _silence_floor_reached(
        now=now,
        last_effective_at=last_effective_at,
        first_attempt_at=first_attempt_at,
        max_silence_min=max_silence_min,
    )
    if forced:
        return {
            "status": "forced",
            "forced": True,
            "surge_probability": 0.0,
            "surge_roll": surge_roll,
        }

    probability = 0.0
    if next_check_at is not None and next_check_at > now:
        probability = calculate_outreach_surge_probability(
            last_interaction_at=last_interaction_at,
            now=now,
            min_prob=surge_min_prob,
            max_prob=surge_max_prob,
            ramp_minutes=max_silence_min,
        )
        if surge_roll is None:
            return {
                "status": "surge_roll_required",
                "forced": False,
                "surge_probability": probability,
            }
        if surge_roll >= probability:
            return {
                "status": "skipped_not_due",
                "forced": False,
                "next_check_at": next_check_at.isoformat(),
                "surge_probability": probability,
                "surge_roll": surge_roll,
            }
    return {
        "status": "due",
        "forced": False,
        "surge_probability": probability,
        "surge_roll": surge_roll,
    }


def evaluate_outreach_min_interval_gate(
    *,
    now: datetime,
    last_effective_at: datetime | None = None,
    first_attempt_at: datetime | None = None,
    min_interval_min: int = DEFAULT_MIN_INTERVAL_MIN,
    max_silence_min: int = DEFAULT_MAX_SILENCE_MIN,
) -> dict[str, Any]:
    """复现生产 run-once 阶段的最长沉默和最小间隔门禁。"""

    forced = _silence_floor_reached(
        now=now,
        last_effective_at=last_effective_at,
        first_attempt_at=first_attempt_at,
        max_silence_min=max_silence_min,
    )
    if not forced and last_effective_at is not None:
        elapsed = now - last_effective_at
        if timedelta(0) <= elapsed < timedelta(minutes=min_interval_min):
            return {
                "status": "skipped_min_interval",
                "forced": False,
                "minutes_since_last": int(elapsed.total_seconds() // 60),
            }
    return {
        "status": "forced" if forced else "due",
        "forced": forced,
    }


async def evaluate_outreach_candidate(
    *,
    user_id: str,
    request_id: str,
    grounding: dict[str, Any],
    now: datetime,
    judge_fn: Callable[..., dict[str, Any]],
    generator_fn: Callable[..., str],
    research_fn: Callable[..., Any] | None = None,
    context_summary: str | None = None,
    min_interval_min: int = 30,
    max_check_interval_min: int = 1440,
) -> dict[str, Any]:
    """把 grounding 评估为 no-candidate、错误或可发布候选。"""

    try:
        judge = judge_fn(
            grounding,
            now=now,
            min_interval_min=min_interval_min,
            max_check_interval_min=max_check_interval_min,
        )
    except Exception as exc:
        return {
            "status": "judge_error",
            "would_publish": False,
            "error_type": "model_error",
            "reason": str(exc)[:500],
            "judge": {},
        }
    if judge.get("error_type") or judge.get("should_reach_out") is None:
        return {
            "status": "judge_error",
            "would_publish": False,
            "error_type": str(judge.get("error_type") or "contract_error"),
            "reason": str(judge.get("reason") or "")[:500],
            "judge": judge,
        }
    if not judge.get("should_reach_out"):
        return {
            "status": "no_candidate",
            "would_publish": False,
            "judge": judge,
        }

    if str(judge.get("outreach_kind") or "message") == "research":
        if research_fn is None:
            return {
                "status": "research_blocked",
                "would_publish": False,
                "reason_code": "runner_unavailable",
                "judge": judge,
            }
        request = ResearchRequest(
            request_id=request_id,
            query=str(judge.get("research_query") or "").strip(),
            user_id=user_id,
            context_summary=context_summary
            or json.dumps(grounding, ensure_ascii=False, default=str),
            budget=ResearchBudget(),
        )
        try:
            result = await research_fn(request)
        except Exception as exc:
            return {
                "status": "research_blocked",
                "would_publish": False,
                "reason_code": "runtime_error",
                "error": str(exc)[:1000],
                "judge": judge,
            }
        research_payload = result.to_dict()
        if result.status != "draft_ready":
            return {
                "status": "research_blocked",
                "would_publish": False,
                "reason_code": result.reason_code,
                "judge": judge,
                "research": research_payload,
            }
        message = strip_think_blocks(result.draft or "").strip()
        if not message:
            return {
                "status": "generation_error",
                "would_publish": False,
                "error_type": "contract_error",
                "reason": "主动研究返回空草稿",
                "judge": judge,
                "research": research_payload,
            }
        from core.proactive_research import (
            normalize_research_publication_text,
            validate_research_publication_text,
        )

        message = normalize_research_publication_text(message, result.sources)
        publication_error = validate_research_publication_text(
            message,
            result.sources,
        )
        if publication_error:
            return {
                "status": "research_blocked",
                "would_publish": False,
                "reason_code": publication_error,
                "judge": judge,
                "research": research_payload,
            }
        return {
            "status": "candidate",
            "would_publish": True,
            "message": message,
            "judge": judge,
            "research": research_payload,
        }

    try:
        message = generator_fn(grounding, str(judge.get("reason") or ""))
    except Exception as exc:
        return {
            "status": "generation_error",
            "would_publish": False,
            "error_type": str(getattr(exc, "error_type", "contract_error")),
            "reason": str(exc)[:500],
            "judge": judge,
        }
    message = strip_think_blocks(str(message or "")).strip()
    if not message:
        return {
            "status": "generation_error",
            "would_publish": False,
            "error_type": "contract_error",
            "judge": judge,
        }
    return {
        "status": "candidate",
        "would_publish": True,
        "message": message,
        "judge": judge,
    }

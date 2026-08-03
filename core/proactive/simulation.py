"""主动外呼无副作用候选模拟服务。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from core.proactive.config import DEFAULT_MAX_CHECK_INTERVAL_MIN, DEFAULT_MIN_INTERVAL_MIN
from core.proactive.grounding import build_outreach_grounding
from core.proactive.model_service import generate_outreach_message, judge_outreach
from core.proactive.runtime_support import session_scope as _session_scope
from core.proactive.serialization import (
    grounding_json_for_model as _grounding_json_for_model,
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
    thread_extractor: Callable[[list[dict[str, Any]]], list[Any]] | None = None,
    grounding_builder: Callable[..., dict[str, Any]] = build_outreach_grounding,
    judge_service: Callable[..., dict[str, Any]] = judge_outreach,
    generator_service: Callable[..., str] = generate_outreach_message,
) -> dict[str, Any]:
    """只评估候选，不创建外呼记录，也不持有 publisher。"""

    current = now or datetime.now()
    with _session_scope(db) as session:
        grounding_kwargs: dict[str, Any] = {"db": session, "now": current}
        if thread_extractor is not None:
            grounding_kwargs["thread_extractor"] = thread_extractor
        grounding = grounding_builder(user_id, **grounding_kwargs)

        from core.proactive_candidate import evaluate_outreach_candidate
        from core.proactive_research import run_proactive_research

        return await evaluate_outreach_candidate(
            user_id=user_id,
            request_id=f"dry-run:{user_id}:{current.replace(microsecond=0).isoformat()}",
            grounding=grounding,
            now=current,
            judge_fn=judge_fn or judge_service,
            generator_fn=generator_fn or generator_service,
            research_fn=research_fn or run_proactive_research,
            context_summary=_grounding_json_for_model(grounding),
            min_interval_min=min_interval_min,
            max_check_interval_min=max_check_interval_min,
        )



__all__ = ["run_outreach_dry_run_once"]

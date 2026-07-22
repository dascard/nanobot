"""主动外呼 Judge 与正文 Generator 的 Prompt Runtime 服务。"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from core.model_provider.route_runtime import call_model_route_response
from core.proactive.model_policy import (
    clamp_next_check_at as _clamp_next_check_at,
    coerce_model_response as _coerce_model_response,
    parse_generator_message as _parse_generator_message,
    parse_outreach_judge_contract as _parse_outreach_judge_contract,
)
from core.proactive.prompt_policy import invoke_outreach_task
from core.proactive.serialization import (
    grounding_json_for_model as _grounding_json_for_model,
)
from core.proactive_diagnostics import judgement_failure_for_type


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
    try:
        response = _coerce_model_response(invoke_outreach_task(
            model_call or call_model_route_response,
            task_key="outreach_judge",
            payload=grounding_text,
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
    payload = {
        "grounding": json.loads(grounding_text),
        "decision": {"reason": str(reason)[:500]},
    }
    response = _coerce_model_response(invoke_outreach_task(
        model_call or call_model_route_response,
        task_key="outreach_generate",
        payload=json.dumps(payload, ensure_ascii=False),
    ))
    return _parse_generator_message(response)



__all__ = ["generate_outreach_message", "judge_outreach"]

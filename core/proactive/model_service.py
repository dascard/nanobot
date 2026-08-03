"""主动外呼 Judge 与正文 Generator 的 Prompt Runtime 服务。"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from core.model_provider.route_runtime import call_model_route_response
from core.proactive.model_policy import (
    clamp_next_check_at as _clamp_next_check_at,
    coerce_model_response as _coerce_model_response,
    parse_generator_message as _parse_generator_message,
    parse_outreach_judge_contract as _parse_outreach_judge_contract,
    parse_outreach_quality_contract as _parse_outreach_quality_contract,
)
from core.proactive.prompt_policy import invoke_outreach_task
from core.proactive.serialization import (
    grounding_json_for_model as _grounding_json_for_model,
)
from core.proactive_diagnostics import judgement_failure_for_type


_GENERATION_DECISION_FIELDS = (
    "should_reach_out",
    "reason",
    "next_check_at",
    "next_intent",
    "outreach_kind",
    "research_query",
    "topic_type",
    "topic",
    "evidence_ids",
    "error_type",
)


def _generation_decision_for_model(
    decision: Mapping[str, Any] | str,
) -> dict[str, Any]:
    if not isinstance(decision, Mapping):
        return {"reason": str(decision)[:500]}
    compact: dict[str, Any] = {}
    for key in _GENERATION_DECISION_FIELDS:
        if key not in decision:
            continue
        value = decision.get(key)
        compact[key] = value[:1000] if isinstance(value, str) else value
    return compact


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
        decision = _parse_outreach_judge_contract(
            response,
            now=current,
            min_interval_min=min_interval_min,
            max_check_interval_min=max_check_interval_min,
        )
        if (
            decision.get("should_reach_out")
            and not _decision_evidence_is_valid(grounding, decision)
        ):
            decision.update({
                "should_reach_out": None,
                "reason": "主动外呼 Judge 选择了无效或已关闭的事实依据",
                "topic_type": "none",
                "topic": "",
                "evidence_ids": [],
                "error_type": "contract_error",
            })
        return decision
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
            "topic_type": "none",
            "topic": "",
            "evidence_ids": [],
            "raw": "",
            "error_type": diagnostic.error_type,
        }


def _decision_evidence_is_valid(
    grounding: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> bool:
    topic_type = str(decision.get("topic_type") or "")
    selected = {
        str(item).strip()
        for item in decision.get("evidence_ids") or []
        if str(item).strip()
    }
    if not selected:
        return False

    eligible: set[str] = set()
    if topic_type == "follow_up":
        for item in grounding.get("recent_threads") or []:
            if not isinstance(item, Mapping) or item.get("status") != "open":
                continue
            evidence_id = str(item.get("evidence_id") or "").strip()
            if evidence_id:
                eligible.add(evidence_id)
    elif topic_type == "discovery":
        for item in grounding.get("persona_facts") or []:
            if not isinstance(item, Mapping):
                continue
            evidence_id = str(item.get("evidence_id") or "").strip()
            if evidence_id:
                eligible.add(evidence_id)
    elif topic_type == "status_check":
        for item in grounding.get("verified_actions") or []:
            if not isinstance(item, Mapping):
                continue
            evidence_id = str(item.get("evidence_id") or "").strip()
            if evidence_id:
                eligible.add(evidence_id)
    return bool(eligible) and selected.issubset(eligible)


def generate_outreach_message(
    grounding: dict[str, Any],
    decision: Mapping[str, Any] | str,
    *,
    model_call: Callable[..., Any] | None = None,
) -> str:
    """根据完整选题决策生成主动外呼 DM 正文。"""

    grounding_text = _grounding_json_for_model(grounding)
    payload = {
        "grounding": json.loads(grounding_text),
        "decision": _generation_decision_for_model(decision),
    }
    caller = model_call or call_model_route_response
    response = _coerce_model_response(invoke_outreach_task(
        caller,
        task_key="outreach_generate",
        payload=json.dumps(payload, ensure_ascii=False),
    ))
    message = _parse_generator_message(response)
    review = review_outreach_message(
        grounding,
        decision,
        message,
        model_call=caller,
    )
    if not review["approved"]:
        from core.proactive_diagnostics import OutreachModelContractError

        raise OutreachModelContractError(
            "主动外呼正文未通过质量复核",
            error_type="quality_rejected",
        )
    return message


def review_outreach_message(
    grounding: dict[str, Any],
    decision: Mapping[str, Any] | str,
    candidate: str,
    *,
    model_call: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """在投递前复核正文的事实依据、话题状态和重复性。"""

    payload = {
        "grounding": json.loads(_grounding_json_for_model(grounding)),
        "decision": _generation_decision_for_model(decision),
        "candidate": str(candidate)[:4000],
    }
    response = _coerce_model_response(invoke_outreach_task(
        model_call or call_model_route_response,
        task_key="outreach_quality",
        payload=json.dumps(payload, ensure_ascii=False),
    ))
    return _parse_outreach_quality_contract(response)



__all__ = [
    "generate_outreach_message",
    "judge_outreach",
    "review_outreach_message",
]

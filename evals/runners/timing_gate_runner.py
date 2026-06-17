"""TimingGate suite runner——验证 action/reason/parse_error 分布。"""
from __future__ import annotations

from dataclasses import asdict

from evals.schema import EvalCase, EvalOutput


def _model_hint_from_input(inp: dict):
    data = inp.get("model_hint")
    if not isinstance(data, dict):
        return None
    action = str(data.get("action") or "").strip()
    if not action:
        return None
    from core.timing_score import TimingModelHint

    return TimingModelHint(
        action=action,
        confidence=float(data.get("confidence", 0.0) or 0.0),
        raw=str(data.get("raw") or ""),
        reason=str(data.get("reason") or ""),
    )


def run_timing_gate_case(case: EvalCase) -> EvalOutput:
    inp = case.input
    out = EvalOutput(case_id=case.id, suite=case.suite, raw=dict(inp))

    action = inp.get("action", "")
    if not action:
        from core.timing_score import decide_timing

        trigger_reason = str(inp.get("trigger_reason") or "").strip()
        decision = decide_timing(
            text=str(inp.get("text") or inp.get("message") or ""),
            is_group=bool(inp.get("is_group", True)),
            is_private=bool(inp.get("is_private", False)),
            is_at_bot=bool(inp.get("is_at_bot", trigger_reason == "at_bot")),
            is_reply_to_bot=bool(inp.get("is_reply_to_bot", trigger_reason == "reply_to_bot")),
            bot_name_mentioned=bool(
                inp.get("bot_name_mentioned", trigger_reason in {"bot_name_mentioned", "mentioned"})
            ),
            direct_call=bool(inp.get("direct_call", trigger_reason == "direct_call")),
            is_directed_to_other=bool(inp.get("is_directed_to_other", False)),
            is_other_bot=bool(inp.get("is_other_bot", False)),
            has_files=bool(inp.get("has_files", False)),
            linger_score=float(inp.get("linger_score", 0.0) or 0.0),
            force_direct_score=float(inp.get("force_direct_score", 0.0) or 0.0),
            min_interval_active=bool(inp.get("min_interval_active", False)),
            min_interval_remaining=float(inp.get("min_interval_remaining", 0.0) or 0.0),
            model_hint=_model_hint_from_input(inp),
        )
        out.timing_action = decision.action
        out.should_reply = decision.action == "continue"
        out.raw["scoring"] = asdict(decision)
        return out

    out.timing_action = action

    if action == "continue":
        out.should_reply = True
    elif action == "no_reply":
        out.should_reply = False

    if action not in ("continue", "wait", "no_reply", ""):
        out.errors.append(f"invalid timing action: {action}")

    reason = str(inp.get("trigger_reason", ""))
    if "parse_error" in reason.lower():
        out.errors.append("parse_error in timing event")

    return out

"""TimingGate suite runner——验证 action/reason/parse_error 分布。"""
from __future__ import annotations

from evals.schema import EvalCase, EvalOutput


def run_timing_gate_case(case: EvalCase) -> EvalOutput:
    inp = case.input
    out = EvalOutput(case_id=case.id, suite=case.suite, raw=dict(inp))

    action = inp.get("action", "")
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

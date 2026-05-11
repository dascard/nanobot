"""Moderation suite runner——block rules、no_reply/no_learn/no_context。"""
from __future__ import annotations

from evals.schema import EvalCase, EvalOutput


def run_moderation_case(case: EvalCase) -> EvalOutput:
    inp = case.input
    out = EvalOutput(case_id=case.id, suite=case.suite, raw=dict(inp))

    matched_rule = inp.get("matched_rule", {})
    user_block_rule = inp.get("user_block_rule", {})

    # 屏蔽词规则
    if matched_rule:
        out.db_writes["no_reply"] = bool(matched_rule.get("no_reply", False))
        out.db_writes["no_learn"] = bool(matched_rule.get("no_learn", False))
        out.db_writes["no_context"] = bool(matched_rule.get("no_context", False))
        out.db_writes["in_context"] = not bool(matched_rule.get("no_context", False))
        out.db_writes["jargon_created"] = not bool(matched_rule.get("no_learn", False))
        out.db_writes["expression_created"] = not bool(matched_rule.get("no_learn", False))
        out.should_reply = not bool(matched_rule.get("no_reply", False))

    # 屏蔽用户规则
    if user_block_rule:
        enabled = user_block_rule.get("enabled", False)
        if enabled:
            out.db_writes["chatlog_written"] = True
            out.db_writes["conversation_turn_written"] = False
            out.db_writes["no_reply"] = True
            out.db_writes["no_learn"] = True
            out.db_writes["no_context"] = True
            out.db_writes["in_context"] = False
            out.db_writes["jargon_created"] = False
            out.db_writes["expression_created"] = False
            out.should_reply = False

    return out

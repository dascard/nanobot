"""Memory learning suite runner——调用正式群学习 Rule Registry。"""
from __future__ import annotations

from evals.schema import EvalCase, EvalOutput


def run_memory_case(case: EvalCase) -> EvalOutput:
    inp = case.input
    out = EvalOutput(case_id=case.id, suite=case.suite, raw=dict(inp))
    message = inp.get("message", "")

    from core.group_learning import dry_run_learning_rules

    dry_run = dry_run_learning_rules(str(message or ""))
    jargon_candidates = [
        item
        for item in dry_run.matches
        if item.candidate_type == "slang"
    ]
    expression_candidates = [
        item
        for item in dry_run.matches
        if item.candidate_type == "expression"
    ]

    out.db_writes["jargon_created"] = bool(jargon_candidates)
    out.db_writes["jargon_terms"] = [
        item.canonical_content
        for item in jargon_candidates
    ]
    out.db_writes["expression_created"] = bool(
        expression_candidates
    )
    out.db_writes["expression_terms"] = [
        item.canonical_content
        for item in expression_candidates
    ]
    out.db_writes["in_context"] = True

    return out

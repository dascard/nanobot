"""Memory/Jargon learning suite runner——调用真实的 expression_learner 过滤函数。"""
from __future__ import annotations

from evals.schema import EvalCase, EvalOutput


def run_memory_case(case: EvalCase) -> EvalOutput:
    inp = case.input
    out = EvalOutput(case_id=case.id, suite=case.suite, raw=dict(inp))
    message = inp.get("message", "")

    # 调用真实 _extract_jargon_candidates：它按定义句式匹配，不会误学纯数字/符号
    from core.expression_learner import _extract_jargon_candidates, _extract_expression_candidates

    jargon_candidates = _extract_jargon_candidates([{"content": message}])
    expr_candidates = _extract_expression_candidates([{"content": message}])

    out.db_writes["jargon_created"] = len(jargon_candidates) > 0
    out.db_writes["jargon_terms"] = [c.get("term", "") for c in jargon_candidates]
    out.db_writes["expression_created"] = len(expr_candidates) > 0
    out.db_writes["expression_terms"] = [c.get("expression", "") for c in expr_candidates]
    out.db_writes["in_context"] = True

    return out

"""Memory/Jargon learning suite runner。"""
from __future__ import annotations

from evals.schema import EvalCase, EvalOutput


def run_memory_case(case: EvalCase) -> EvalOutput:
    inp = case.input
    out = EvalOutput(case_id=case.id, suite=case.suite, raw=dict(inp))

    message = inp.get("message", "")
    forbidden_terms = inp.get("expected", {}).get("forbidden_terms", []) or case.expected.get("forbidden_terms", [])

    # jargon 过滤：纯符号/数字不应被学习
    should_create_jargon = True
    import re
    # 简单规则：包含中文/字母且不全是符号/数字
    has_text = bool(re.search(r"[a-zA-Z一-鿿]", message))
    is_pure_number = bool(re.match(r"^[\d\.\s×xX\*\+\-\=%,，。、:：;；]+$", message))

    if not has_text or is_pure_number:
        should_create_jargon = False

    # 检查 forbidden_terms
    for term in forbidden_terms:
        if term in message:
            should_create_jargon = False
            break

    out.db_writes["jargon_created"] = should_create_jargon
    out.db_writes["expression_created"] = False  # 也是符号/数字不应学习
    out.db_writes["in_context"] = True

    # 如果 case 明确指定 expected，用 expected 覆盖
    exp = case.expected
    if "should_create_jargon" in exp:
        out.db_writes["jargon_created"] = exp["should_create_jargon"]
    if "should_create_expression" in exp:
        out.db_writes["expression_created"] = exp["should_create_expression"]

    return out

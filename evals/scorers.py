"""硬评分器——只做 deterministic checks，不做 LLM judge。"""
from __future__ import annotations

from typing import Any

from evals.schema import EvalCase, EvalOutput


def _compare_expected_dict(
    errors: list[str],
    prefix: str,
    expected: dict[str, Any],
    actual: Any,
) -> None:
    if not isinstance(actual, dict):
        errors.append(f"{prefix} missing or not dict: expected=dict actual={type(actual).__name__}")
        return
    for key, expected_value in expected.items():
        path = f"{prefix}.{key}"
        if key not in actual:
            errors.append(f"{path} missing")
            continue
        actual_value = actual.get(key)
        if isinstance(expected_value, dict):
            _compare_expected_dict(errors, path, expected_value, actual_value)
        elif actual_value != expected_value:
            errors.append(f"{path} mismatch: expected={expected_value} actual={actual_value}")


def score_case(case: EvalCase, output: EvalOutput) -> dict:
    errors: list[str] = []
    exp = case.expected

    # 如果 runner 报告了错误，自动 fail
    if output.errors:
        errors.append(f"runner errors: {output.errors}")

    # should_reply
    if "should_reply" in exp:
        actual = bool(output.should_reply) if output.should_reply is not None else bool(output.reply_text)
        expected = bool(exp["should_reply"])
        if actual != expected:
            errors.append(f"should_reply mismatch: expected={expected} actual={actual}")

    # timing_action
    if "timing_action" in exp:
        actual = str(output.timing_action or "")
        expected = str(exp["timing_action"] or "")
        if actual != expected:
            errors.append(f"timing_action mismatch: expected={expected} actual={actual}")

    # scoring breakdown
    if "scoring" in exp:
        expected_scoring = exp["scoring"]
        if isinstance(expected_scoring, dict):
            _compare_expected_dict(
                errors,
                "scoring",
                expected_scoring,
                output.raw.get("scoring") if isinstance(output.raw, dict) else None,
            )
        else:
            errors.append("scoring expected value must be a dict")

    # forbidden_tools
    for tool in exp.get("forbidden_tools", []):
        if tool in output.tool_calls:
            errors.append(f"forbidden tool used: {tool}")

    # required_tools
    for tool in exp.get("required_tools", []):
        if tool not in output.tool_calls:
            errors.append(f"required tool missing: {tool}")

    # send_mode
    if "send_mode" in exp:
        actual = output.reply_meta.get("send_mode")
        if actual != exp["send_mode"]:
            errors.append(f"send_mode mismatch: expected={exp['send_mode']} actual={actual}")

    # reply_to_message_id
    if "reply_to_message_id" in exp:
        actual = str(output.reply_meta.get("reply_to_message_id") or "")
        if actual != str(exp["reply_to_message_id"]):
            errors.append("reply_to_message_id mismatch")

    # mentions
    if "mentions" in exp:
        actual = output.reply_meta.get("mentions") or []
        expected_mentions = set(str(m) for m in exp["mentions"])
        actual_mentions = set(str(m) for m in actual)
        missing = expected_mentions - actual_mentions
        if missing:
            errors.append(f"missing mentions: {missing}")

    # must_contain — handle both list and single string values
    must_contain = exp.get("must_contain", [])
    if isinstance(must_contain, str):
        must_contain = [must_contain]
    for text in must_contain:
        haystack = str(output.raw)
        if text not in haystack:
            errors.append(f"missing required text: {text}")

    # must_not_contain — handle both list and single string values
    must_not_contain = exp.get("must_not_contain", [])
    if isinstance(must_not_contain, str):
        must_not_contain = [must_not_contain]
    for text in must_not_contain:
        haystack = str(output.raw)
        if text in haystack:
            errors.append(f"forbidden text present: {text}")

    # http_status
    if "http_status" in exp:
        if output.http_status != exp["http_status"]:
            errors.append(f"http_status mismatch: expected={exp['http_status']} actual={output.http_status}")

    # content_type_prefix
    if "content_type_prefix" in exp:
        if not str(output.content_type or "").startswith(exp["content_type_prefix"]):
            errors.append(f"content_type mismatch: expected prefix={exp['content_type_prefix']} actual={output.content_type}")

    # forbidden_terms — check jargon_terms / expression_terms in db_writes
    forbidden_terms = exp.get("forbidden_terms", [])
    if isinstance(forbidden_terms, str):
        forbidden_terms = [forbidden_terms]
    for term in forbidden_terms:
        jargon_terms = output.db_writes.get("jargon_terms", [])
        expr_terms = output.db_writes.get("expression_terms", [])
        if term in jargon_terms or term in expr_terms:
            errors.append(f"forbidden term learned: {term}")

    # should_create_jargon
    if "should_create_jargon" in exp:
        actual = output.db_writes.get("jargon_created", False)
        if actual != exp["should_create_jargon"]:
            errors.append(f"should_create_jargon mismatch: expected={exp['should_create_jargon']} actual={actual}")

    # should_create_expression
    if "should_create_expression" in exp:
        actual = output.db_writes.get("expression_created", False)
        if actual != exp["should_create_expression"]:
            errors.append(f"should_create_expression mismatch: expected={exp['should_create_expression']} actual={actual}")

    # no_reply / no_learn / no_context
    for key in ("no_reply", "no_learn", "no_context"):
        if key in exp:
            actual = output.db_writes.get(key)
            if actual != exp[key]:
                errors.append(f"{key} mismatch: expected={exp[key]} actual={actual}")

    # should_enter_context
    if "should_enter_context" in exp:
        actual = output.db_writes.get("in_context", False)
        if actual != exp["should_enter_context"]:
            errors.append(f"should_enter_context mismatch: expected={exp['should_enter_context']} actual={actual}")

    # should_write_chatlog
    if "should_write_chatlog" in exp:
        actual = output.db_writes.get("chatlog_written", False)
        if actual != exp["should_write_chatlog"]:
            errors.append(f"should_write_chatlog mismatch: expected={exp['should_write_chatlog']} actual={actual}")

    # should_write_conversation_turn
    if "should_write_conversation_turn" in exp:
        actual = output.db_writes.get("conversation_turn_written", False)
        if actual != exp["should_write_conversation_turn"]:
            errors.append(f"should_write_conversation_turn mismatch")

    # model_used
    if "model_used" in exp and output.model_used != exp["model_used"]:
        errors.append(f"model_used mismatch: expected={exp['model_used']} actual={output.model_used}")

    # must_not_use (models)
    for model in exp.get("must_not_use", []):
        if output.model_used == model:
            errors.append(f"forbidden model used: {model}")

    # should_call_auto_routing
    if "should_call_auto_routing" in exp:
        actual = output.raw.get("auto_routing_called", False)
        if actual != exp["should_call_auto_routing"]:
            errors.append(f"should_call_auto_routing mismatch: expected={exp['should_call_auto_routing']} actual={actual}")

    return {
        "passed": not errors,
        "score": 1.0 if not errors else 0.0,
        "errors": errors,
    }

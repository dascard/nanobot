"""Eval expected 字段契约。"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


SCOREABLE_EXPECTED_KEYS = frozenset({
    "should_reply",
    "timing_action",
    "scoring",
    "forbidden_tools",
    "required_tools",
    "send_mode",
    "reply_to_message_id",
    "mentions",
    "must_contain",
    "must_not_contain",
    "http_status",
    "content_type_prefix",
    "forbidden_terms",
    "should_create_jargon",
    "should_create_expression",
    "no_reply",
    "no_learn",
    "no_context",
    "should_enter_context",
    "should_write_chatlog",
    "should_write_conversation_turn",
    "model_used",
    "must_not_use",
    "should_call_auto_routing",
    "served_sticker_id",
    "send_source",
})


def validate_expected_contract(suite: str, expected: Mapping[str, Any]) -> None:
    if not expected:
        raise ValueError("expected must not be empty")
    if expected.get("needs_label"):
        raise ValueError("expected must not contain needs_label=true")

    unknown = sorted(str(key) for key in expected if key not in SCOREABLE_EXPECTED_KEYS)
    if unknown:
        raise ValueError(f"expected contains unscored keys for suite={suite}: {unknown}")

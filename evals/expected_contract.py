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


EXPECTED_FIELD_SCHEMA: dict[str, dict[str, Any]] = {
    "should_reply": {"type": "boolean", "label": "是否应该回复"},
    "timing_action": {
        "type": "enum",
        "values": ["continue", "wait", "no_reply"],
        "label": "TimingGate 动作",
    },
    "scoring": {"type": "object", "label": "评分明细", "advanced": True},
    "forbidden_tools": {"type": "string_list", "label": "禁止工具"},
    "required_tools": {"type": "string_list", "label": "必需工具"},
    "send_mode": {
        "type": "enum",
        "values": ["normal", "quote", "mention"],
        "label": "发送方式",
    },
    "reply_to_message_id": {"type": "string_or_number", "label": "引用消息 ID"},
    "mentions": {"type": "array", "label": "提及目标"},
    "must_contain": {"type": "string_list", "label": "必须包含文本"},
    "must_not_contain": {"type": "string_list", "label": "禁止包含文本"},
    "http_status": {"type": "integer", "label": "HTTP 状态码"},
    "content_type_prefix": {"type": "string", "label": "Content-Type 前缀"},
    "forbidden_terms": {"type": "string_list", "label": "禁止学习词"},
    "should_create_jargon": {"type": "boolean", "label": "应创建黑话"},
    "should_create_expression": {"type": "boolean", "label": "应创建表达"},
    "no_reply": {"type": "boolean", "label": "不应回复"},
    "no_learn": {"type": "boolean", "label": "不应学习"},
    "no_context": {"type": "boolean", "label": "不应入上下文"},
    "should_enter_context": {"type": "boolean", "label": "应进入上下文"},
    "should_write_chatlog": {"type": "boolean", "label": "应写 ChatLog"},
    "should_write_conversation_turn": {
        "type": "boolean",
        "label": "应写 ConversationTurn",
    },
    "model_used": {"type": "string", "label": "应使用模型"},
    "must_not_use": {"type": "string_list", "label": "禁止模型"},
    "should_call_auto_routing": {"type": "boolean", "label": "应调用自动路由"},
    "served_sticker_id": {"type": "string_or_number", "label": "服务贴纸 ID"},
    "send_source": {"type": "string", "label": "发送来源"},
}


SUITE_EXPECTED_PRESETS: dict[str, dict[str, list[str]]] = {
    "timing_gate": {"fields": ["timing_action", "should_reply", "scoring"]},
    "group_reply": {
        "fields": [
            "should_reply",
            "required_tools",
            "forbidden_tools",
            "send_mode",
            "reply_to_message_id",
            "mentions",
            "must_contain",
            "must_not_contain",
        ],
    },
    "reply_contract": {
        "fields": [
            "should_reply",
            "required_tools",
            "forbidden_tools",
            "send_mode",
            "reply_to_message_id",
            "mentions",
            "must_contain",
            "must_not_contain",
        ],
    },
    "rendering_contract": {
        "fields": [
            "should_reply",
            "send_mode",
            "reply_to_message_id",
            "mentions",
            "must_contain",
            "must_not_contain",
        ],
    },
    "memory_learning": {
        "fields": [
            "no_learn",
            "should_create_jargon",
            "should_create_expression",
            "forbidden_terms",
        ],
    },
    "model_routing": {
        "fields": ["model_used", "must_not_use", "should_call_auto_routing"],
    },
    "moderation": {
        "fields": [
            "no_reply",
            "no_learn",
            "no_context",
            "should_enter_context",
            "should_write_chatlog",
            "should_write_conversation_turn",
        ],
    },
    "sticker": {
        "fields": [
            "http_status",
            "content_type_prefix",
            "served_sticker_id",
            "send_source",
        ],
    },
}


DEPRECATED_EXPECTED_KEYS = frozenset({
    "expected_action",
    "should_learn",
    "quality",
    "category",
    "meaning",
    "delay_seconds",
    "reason",
})


def expected_contract_payload() -> dict[str, Any]:
    """返回 Admin WebUI 可消费的 expected 字段契约。"""
    return {
        "scoreable_keys": sorted(SCOREABLE_EXPECTED_KEYS),
        "field_schema": {
            key: EXPECTED_FIELD_SCHEMA[key]
            for key in sorted(SCOREABLE_EXPECTED_KEYS)
        },
        "suite_presets": SUITE_EXPECTED_PRESETS,
        "deprecated_keys": sorted(DEPRECATED_EXPECTED_KEYS),
    }


def _validate_type(key: str, value: Any, schema: Mapping[str, Any]) -> None:
    field_type = schema.get("type")
    if field_type == "boolean" and not isinstance(value, bool):
        raise ValueError(f"expected.{key} must be boolean")
    if field_type == "string" and not isinstance(value, str):
        raise ValueError(f"expected.{key} must be string")
    if field_type == "integer" and (
        not isinstance(value, int) or isinstance(value, bool)
    ):
        raise ValueError(f"expected.{key} must be integer")
    if field_type == "string_or_number" and (
        isinstance(value, bool) or not isinstance(value, (str, int, float))
    ):
        raise ValueError(f"expected.{key} must be string or number")
    if field_type == "string_list":
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            raise ValueError(f"expected.{key} must be list[str]")
    if field_type == "array" and not isinstance(value, list):
        raise ValueError(f"expected.{key} must be array")
    if field_type == "object" and not isinstance(value, Mapping):
        raise ValueError(f"expected.{key} must be object")
    if field_type == "enum":
        values = schema.get("values") or []
        if not isinstance(value, str) or value not in values:
            raise ValueError(f"expected.{key} must be one of {values}")


def validate_expected_contract(suite: str, expected: Mapping[str, Any]) -> None:
    if not expected:
        raise ValueError("expected must not be empty")
    if expected.get("needs_label"):
        raise ValueError("expected must not contain needs_label=true")

    deprecated = sorted(str(key) for key in expected if key in DEPRECATED_EXPECTED_KEYS)
    if deprecated:
        raise ValueError(
            f"expected contains deprecated UI keys for suite={suite}: {deprecated}"
        )

    unknown = sorted(str(key) for key in expected if key not in SCOREABLE_EXPECTED_KEYS)
    if unknown:
        raise ValueError(f"expected contains unscored keys for suite={suite}: {unknown}")

    for key, value in expected.items():
        _validate_type(str(key), value, EXPECTED_FIELD_SCHEMA[str(key)])

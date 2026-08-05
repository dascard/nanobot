"""主动外呼 grounding 的有界序列化策略。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


MODEL_GROUNDING_RECENT_MESSAGE_LIMIT = 8
MODEL_GROUNDING_RECENT_OUTREACH_LIMIT = 5
MODEL_GROUNDING_TEXT_LIMIT = 480
MODEL_GROUNDING_MESSAGE_TEXT_LIMIT = 240
MODEL_GROUNDING_OUTREACH_TEXT_LIMIT = 600
PERSISTED_RECENT_OUTREACH_TEXT_LIMIT = 800


def json_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def iso_or_empty(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def grounding_json(grounding: dict[str, Any]) -> str:
    return json.dumps(grounding, ensure_ascii=False, default=str)


def truncate_text(value: str | None, max_chars: int = 160) -> str:
    text = (value or "").strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."


def _compact_model_value(
    value: Any,
    *,
    max_chars: int = MODEL_GROUNDING_TEXT_LIMIT,
    max_items: int = 12,
    depth: int = 0,
) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        if depth >= 4:
            return truncate_text(grounding_json(value), max_chars)
        return {
            str(key): _compact_model_value(
                nested,
                max_chars=max_chars,
                max_items=max_items,
                depth=depth + 1,
            )
            for key, nested in list(value.items())[:max_items]
        }
    if isinstance(value, list):
        return [
            _compact_model_value(
                item,
                max_chars=max_chars,
                max_items=max_items,
                depth=depth + 1,
            )
            for item in value[:max_items]
        ]
    return truncate_text(str(value), max_chars)


def _compact_recent_message_for_model(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {
            "content": truncate_text(
                str(item),
                MODEL_GROUNDING_MESSAGE_TEXT_LIMIT,
            )
        }
    compact: dict[str, Any] = {}
    for key in ("role", "content", "created_at", "sender_name"):
        if key not in item:
            continue
        limit = (
            MODEL_GROUNDING_MESSAGE_TEXT_LIMIT
            if key == "content"
            else MODEL_GROUNDING_TEXT_LIMIT
        )
        compact[key] = _compact_model_value(item.get(key), max_chars=limit)
    return compact


def _compact_recent_outreach_for_model(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {
            "message": truncate_text(
                str(item),
                MODEL_GROUNDING_OUTREACH_TEXT_LIMIT,
            )
        }
    compact: dict[str, Any] = {}
    for key in ("status", "forced", "message", "next_intent", "created_at"):
        if key not in item:
            continue
        limit = (
            MODEL_GROUNDING_OUTREACH_TEXT_LIMIT
            if key == "message"
            else MODEL_GROUNDING_TEXT_LIMIT
        )
        compact[key] = _compact_model_value(item.get(key), max_chars=limit)
    return compact


def grounding_json_for_model(grounding: dict[str, Any]) -> str:
    """将 grounding 限长后序列化，避免把历史全文送入模型。"""

    compact: dict[str, Any] = {}
    for key, value in grounding.items():
        if key == "_trigger_runtime":
            continue
        if key in {"recent_messages", "recent_outreaches"}:
            continue
        if key == "last_outreach":
            limit = MODEL_GROUNDING_OUTREACH_TEXT_LIMIT
        elif key == "last_user_message":
            limit = MODEL_GROUNDING_MESSAGE_TEXT_LIMIT
        else:
            limit = MODEL_GROUNDING_TEXT_LIMIT
        compact[key] = _compact_model_value(value, max_chars=limit)

    recent_messages = grounding.get("recent_messages")
    if isinstance(recent_messages, list) and recent_messages:
        compact["recent_messages"] = [
            _compact_recent_message_for_model(item)
            for item in recent_messages[-MODEL_GROUNDING_RECENT_MESSAGE_LIMIT:]
        ]
    recent_outreaches = grounding.get("recent_outreaches")
    if isinstance(recent_outreaches, list) and recent_outreaches:
        compact["recent_outreaches"] = [
            _compact_recent_outreach_for_model(item)
            for item in recent_outreaches[-MODEL_GROUNDING_RECENT_OUTREACH_LIMIT:]
        ]
    return grounding_json(compact)


def weekday_label(value: datetime) -> str:
    return [
        "星期一",
        "星期二",
        "星期三",
        "星期四",
        "星期五",
        "星期六",
        "星期日",
    ][value.weekday()]


def time_period_label(value: datetime) -> str:
    hour = value.hour
    if hour < 5:
        return "深夜"
    if hour < 8:
        return "清晨"
    if hour < 12:
        return "上午"
    if hour < 17:
        return "午后"
    if hour < 19:
        return "傍晚"
    return "夜晚"


def hours_since(now: datetime, past: datetime | None) -> float | None:
    if past is None:
        return None
    return max(0.0, (now - past).total_seconds() / 3600.0)


__all__ = [
    "MODEL_GROUNDING_MESSAGE_TEXT_LIMIT",
    "MODEL_GROUNDING_OUTREACH_TEXT_LIMIT",
    "MODEL_GROUNDING_RECENT_MESSAGE_LIMIT",
    "MODEL_GROUNDING_RECENT_OUTREACH_LIMIT",
    "MODEL_GROUNDING_TEXT_LIMIT",
    "PERSISTED_RECENT_OUTREACH_TEXT_LIMIT",
    "grounding_json",
    "grounding_json_for_model",
    "hours_since",
    "iso_or_empty",
    "json_object",
    "time_period_label",
    "truncate_text",
    "weekday_label",
]

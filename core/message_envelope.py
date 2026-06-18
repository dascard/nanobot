"""响应信封构造工具。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_HTML_PREFIXES = ("<article", "<!doctype", "<html")
_REPLY_META_KEYS = {
    "send_mode",
    "reply_to_message_id",
    "mentions",
    "quote",
    "at_sender",
}
_TEXTUAL_MESSAGE_TYPES = {"text", "html"}


def _clean_dict(data: Mapping[str, Any] | None) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    if not isinstance(data, Mapping):
        return cleaned
    for key, value in data.items():
        if value is None or value == "":
            continue
        cleaned[str(key)] = value
    return cleaned


def is_html_reply(reply: str) -> bool:
    text = str(reply or "").lstrip().lower()
    return text.startswith(_HTML_PREFIXES)


def build_text_messages(reply: str) -> list[dict[str, str]]:
    text = str(reply or "")
    if not text.strip():
        return []
    message_type = "html" if is_html_reply(text) else "text"
    return [{"type": message_type, "text": text}]


def sanitize_reply_meta(reply_meta: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(reply_meta, Mapping):
        return {}
    return {
        key: reply_meta[key]
        for key in _REPLY_META_KEYS
        if key in reply_meta and reply_meta[key] is not None
    }


def build_chat_response_envelope(
    *,
    status: str,
    answer: str = "",
    reply_meta: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    reply = str(answer or "")
    return {
        "status": str(status or "ok"),
        "reply": reply,
        "messages": build_text_messages(reply),
        "reply_meta": sanitize_reply_meta(reply_meta),
        "meta": _clean_dict(meta),
    }


def _status_for_group_action(action: str) -> str:
    normalized = str(action or "").strip() or "no_reply"
    if normalized == "continue":
        return "ok"
    if normalized in {"wait", "no_reply"}:
        return normalized
    return normalized


def build_group_response_envelope(
    *,
    action: str,
    reply: str = "",
    reply_meta: Mapping[str, Any] | None = None,
    generation: int | None = None,
    reason: str = "",
    delay_seconds: int | float | None = None,
    diagnostics: Mapping[str, Any] | None = None,
    duplicate_reply: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    response_meta = _clean_dict(meta)
    response_meta.update(
        _clean_dict(
            {
                "generation": generation,
                "reason": reason,
                "delay_seconds": delay_seconds,
                "diagnostics": dict(diagnostics)
                if isinstance(diagnostics, Mapping)
                else None,
                "duplicate_reply": dict(duplicate_reply)
                if isinstance(duplicate_reply, Mapping)
                else None,
            }
        )
    )
    normalized_action = str(action or "no_reply")
    text = str(reply or "")
    return {
        "status": _status_for_group_action(normalized_action),
        "action": normalized_action,
        "reply": text,
        "messages": build_text_messages(text),
        "reply_meta": sanitize_reply_meta(reply_meta),
        "meta": response_meta,
    }


def envelope_to_message(envelope: Mapping[str, Any] | None) -> str:
    if not isinstance(envelope, Mapping):
        return ""
    reply = str(envelope.get("reply") or "")
    if reply.strip():
        return reply
    raw_messages = envelope.get("messages") or []
    if not isinstance(raw_messages, list):
        return ""
    parts: list[str] = []
    for item in raw_messages:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") not in _TEXTUAL_MESSAGE_TYPES:
            continue
        text = str(item.get("text") or "")
        if text:
            parts.append(text)
    return "\n".join(parts)

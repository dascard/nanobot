"""最终回复工具的框架无关 wire contract。"""

from __future__ import annotations

import json
from typing import Any


REPLY_MARKER = "NANOBOT_REPLY_OUTPUT"
ALLOWED_SEND_MODES = frozenset(
    {"normal", "quote", "mention", "quote_and_mention"}
)


def build_reply_payload(
    content: str,
    *,
    reply_to_message_id: Any = None,
    mentions: Any = None,
    quote: bool = False,
    at_sender: bool = False,
    send_mode: str = "normal",
) -> dict[str, Any]:
    """构造 Runtime 可识别的最终回复协议。"""

    text = str(content or "").strip()
    if isinstance(mentions, list):
        clean_mentions = [
            value
            for value in (str(item).strip()[:20] for item in mentions)
            if value.isdigit()
        ][:10]
    else:
        clean_mentions = []

    mode = str(send_mode or "normal")
    if mode not in ALLOWED_SEND_MODES:
        mode = "normal"

    return {
        REPLY_MARKER: {
            "content": text,
            "reply_to_message_id": (
                str(reply_to_message_id or "")[:50] or None
            ),
            "mentions": clean_mentions,
            "quote": bool(quote),
            "at_sender": bool(at_sender),
            "send_mode": mode,
        }
    }


def build_reply_output(content: str, **kwargs: Any) -> str:
    return json.dumps(
        build_reply_payload(content, **kwargs),
        ensure_ascii=False,
    )


def build_no_reply_output(reason: object) -> str:
    return json.dumps(
        {
            REPLY_MARKER: {
                "content": "",
                "no_reply": True,
                "reason": str(reason or "").strip()[:200],
            }
        },
        ensure_ascii=False,
    )


__all__ = [
    "ALLOWED_SEND_MODES",
    "REPLY_MARKER",
    "build_no_reply_output",
    "build_reply_output",
    "build_reply_payload",
]

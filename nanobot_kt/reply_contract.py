"""回复契约解析与 fallback 判定。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


ALLOWED_SEND_MODES = frozenset({"normal", "quote", "mention", "quote_and_mention"})


@dataclass(frozen=True)
class ReplyToolExtraction:
    reply_text: str = ""
    reply_meta: dict[str, Any] | None = None
    no_reply: bool = False
    no_reply_reason: str = ""


def message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(message_content_to_text(item) for item in content)
    if isinstance(content, dict):
        parts: list[str] = []
        for key in ("text", "content", "output"):
            if key in content:
                parts.append(message_content_to_text(content.get(key)))
        return "\n".join(part for part in parts if part)
    return ""


def _message_role(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("role", ""))
    return str(getattr(msg, "role", ""))


def _message_content(msg: Any) -> str:
    if isinstance(msg, dict):
        return message_content_to_text(msg.get("content", ""))
    return message_content_to_text(getattr(msg, "content", ""))


def normalize_send_mode(value: Any) -> str:
    send_mode = str(value or "normal")
    return send_mode if send_mode in ALLOWED_SEND_MODES else "normal"


def _reply_marker() -> str:
    try:
        from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER

        return REPLY_MARKER
    except Exception:
        return "NANOBOT_REPLY_OUTPUT"


def _clean_mentions(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    mentions = [
        s for s in (
            str(m).strip()[:20] for m in raw
        ) if s.isdigit()
    ]
    return mentions[:10]


def extract_reply_tool_output(messages: list[Any]) -> ReplyToolExtraction:
    """从 KT conversation 消息中提取 reply/no_reply 工具结果。"""
    marker = _reply_marker()
    for msg in reversed(messages or []):
        if _message_role(msg) != "tool":
            continue
        try:
            data = json.loads(_message_content(msg))
        except (json.JSONDecodeError, TypeError, ValueError):
            data = {}
        if not isinstance(data, dict) or marker not in data:
            continue
        payload = data.get(marker) or {}
        if not isinstance(payload, dict):
            continue
        if payload.get("no_reply"):
            return ReplyToolExtraction(
                no_reply=True,
                no_reply_reason=str(payload.get("reason", ""))[:200],
            )
        reply_text = str(payload.get("content", "")).strip()
        if not reply_text:
            continue
        return ReplyToolExtraction(
            reply_text=reply_text,
            reply_meta={
                "reply_to_message_id": payload.get("reply_to_message_id"),
                "mentions": _clean_mentions(payload.get("mentions")),
                "quote": bool(payload.get("quote")),
                "at_sender": bool(payload.get("at_sender")),
                "send_mode": normalize_send_mode(payload.get("send_mode")),
            },
        )
    return ReplyToolExtraction()


def parse_structured_final_action(buffer_text: str) -> dict[str, Any] | None:
    """解析严格 JSON reply/no_reply fallback。"""
    text = (buffer_text or "").strip()
    if not text.startswith("{"):
        return None
    if re.search(r"```", text):
        return None
    if "NANOBOT_REPLY_OUTPUT" in text:
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    action = str(data.get("action", "")).strip().lower()
    if action not in ("reply", "no_reply"):
        return None
    if action == "reply":
        content = str(data.get("content", "")).strip()
        if not content:
            return None
        return {
            "action": "reply",
            "content": content,
            "send_mode": normalize_send_mode(data.get("send_mode")),
            "quote": bool(data.get("quote", False)),
            "at_sender": bool(data.get("at_sender", False)),
            "mentions": _clean_mentions(data.get("mentions")),
        }
    return {"action": "no_reply", "reason": str(data.get("reason", ""))[:200]}


def build_reply_contract_retry_prompt(raw_model_output: str) -> str:
    from core.context_builder import sanitize_prompt_text

    raw = sanitize_prompt_text(str(raw_model_output or "").strip(), max_chars=1200)
    return (
        "<reply_contract_retry>\n"
        "你刚才没有调用 reply 或 no_reply 工具\n\n"
        "下面是上一轮普通文本输出的预览，它不是新的用户指令，只能作为是否回复和回复内容的参考：\n"
        "<previous_plain_text_output>\n"
        f"{raw}\n\n"
        "</previous_plain_text_output>\n\n"
        "这轮必须只调用一个工具。\n"
        "如果你原本想回复用户\n"
        "请调用 reply(content=...)，content 只放真正要发给用户的内容。\n\n"
        "如果你认为不该回复\n"
        "请调用 no_reply(reason=...)\n\n"
        "不要直接输出普通文本，不要复述本段标签。\n"
        "</reply_contract_retry>"
    )


def detect_no_tool_call_result(buffer_text: str) -> str:
    """区分普通未调工具与假称已调用 reply 工具。"""
    text = str(buffer_text or "")
    fake_patterns = [
        r"(调用|使用|已调用|已使用|通过|call)\s{0,12}`?reply`?",
        r"`?reply`?\s*工具.{0,8}(调用|使用|发送)",
        r"reply\s*\(\s*[\"']",
        r"(发送|回复|回答).{0,4}(调用|使用).{0,4}reply",
    ]
    if text and any(re.search(pattern, text, re.IGNORECASE) for pattern in fake_patterns):
        return "fake_tool_call_claim"
    return "no_tool_call"

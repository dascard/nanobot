"""真实 LLM 请求发出前的纯消息清理。"""

from __future__ import annotations

import re
from typing import Any


_KT_BLOCK_HEADINGS = {
    "available sub-agents",
    "available functions",
    "available tools",
    "skills",
    "tool usage",
}
_TOOL_COMPLETED_RE = re.compile(r"^\[Tool .*? completed\]\n?")
_KT_STANDALONE_PATTERNS = (
    "Use the `info` tool for full documentation on any function.",
    "Sub-agents are called as tools via the API",
    "Tools are called via the API's native function calling mechanism.",
    'You may ONLY call tools listed in the "Available Functions" section above.',
)
_H2_RE = re.compile(r"^\s*##\s+(.+?)\s*$")
_H3_RE = re.compile(r"^\s*###\s+(.+?)\s*$")


def _heading_key(line: str) -> str:
    match = _H2_RE.match(line)
    return match.group(1).strip().lower() if match else ""


def _is_kt_block_heading(line: str) -> bool:
    return _heading_key(line) in _KT_BLOCK_HEADINGS


def _is_background_heading(line: str) -> bool:
    match = _H3_RE.match(line)
    return bool(match and match.group(1).strip().lower() == "background execution")


def _strip_system_text(text: str) -> str:
    kept: list[str] = []
    skip = False
    skip_h3_background = False
    for line in text.splitlines():
        if _is_kt_block_heading(line):
            skip = True
            skip_h3_background = False
            continue
        if _is_background_heading(line):
            skip = True
            skip_h3_background = True
            continue
        if skip:
            if _H2_RE.match(line):
                if _is_kt_block_heading(line):
                    skip_h3_background = False
                    continue
                skip = False
                skip_h3_background = False
            elif skip_h3_background and _H3_RE.match(line):
                skip = False
                skip_h3_background = False
            else:
                continue
        if any(pattern in line for pattern in _KT_STANDALONE_PATTERNS):
            continue
        kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def strip_kt_framework_tool_docs(messages: Any) -> list[Any]:
    if not isinstance(messages, (list, tuple)):
        return messages
    sanitized: list[Any] = []
    for message in messages:
        if not isinstance(message, dict):
            sanitized.append(message)
            continue
        role = str(message.get("role") or "")
        content = message.get("content")
        if role in ("user", "tool") and isinstance(content, str):
            if _TOOL_COMPLETED_RE.match(content.strip()):
                continue
        if role != "system" or not isinstance(content, str):
            sanitized.append(dict(message))
            continue
        next_message = dict(message)
        next_message["content"] = _strip_system_text(content)
        sanitized.append(next_message)
    return sanitized


def sanitize_payload_messages(payload: dict[str, Any]) -> dict[str, Any]:
    if "messages" not in payload:
        return dict(payload)
    sanitized = dict(payload)
    sanitized["messages"] = strip_kt_framework_tool_docs(payload.get("messages"))
    return sanitized


def sanitize_sdk_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    return sanitize_payload_messages(dict(kwargs))

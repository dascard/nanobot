"""真实 LLM 请求发出前的消息清理。"""

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
    if not match:
        return ""
    return match.group(1).strip().lower()


def _is_kt_block_heading(line: str) -> bool:
    return _heading_key(line) in _KT_BLOCK_HEADINGS


def _is_background_heading(line: str) -> bool:
    match = _H3_RE.match(line)
    return bool(match and match.group(1).strip().lower() == "background execution")


def _strip_system_text(text: str) -> str:
    lines = text.splitlines()
    kept: list[str] = []
    skip = False
    skip_h3_background = False

    for line in lines:
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

    cleaned = "\n".join(kept)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def strip_kt_framework_tool_docs(messages: Any) -> list[Any]:
    """移除 system message 中由 KT 自动注入的英文工具说明段落。

    只处理 system role，避免误删用户引用或工具结果。
    """
    if not isinstance(messages, list):
        return messages

    sanitized: list[Any] = []
    for msg in messages:
        if not isinstance(msg, dict):
            sanitized.append(msg)
            continue
        role = str(msg.get("role") or "")
        content = msg.get("content")
        if role != "system" or not isinstance(content, str):
            sanitized.append(dict(msg))
            continue
        cleaned = _strip_system_text(content)
        next_msg = dict(msg)
        next_msg["content"] = cleaned
        sanitized.append(next_msg)
    return sanitized


def sanitize_payload_messages(payload: dict[str, Any]) -> dict[str, Any]:
    """返回清理过 messages 的 payload 副本。"""
    if "messages" not in payload:
        return dict(payload)
    sanitized = dict(payload)
    sanitized["messages"] = strip_kt_framework_tool_docs(payload.get("messages"))
    return sanitized


def sanitize_sdk_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    return sanitize_payload_messages(dict(kwargs))

"""回复契约解析与 fallback 判定。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterator

from core.tool_contracts.reply import (
    ALLOWED_SEND_MODES,
    REPLY_MARKER,
)
from core.tool_contracts.rich_output import (
    RICH_OUTPUT_MARKER,
    RICH_REPORT_HTML_MARKERS,
    RICH_REPORT_TOOLS,
    build_rich_output,
)



@dataclass(frozen=True)
class VerifiedToolOutput:
    tool_name: str
    tool_call_id: str
    content: str
    message_index: int


@dataclass(frozen=True)
class ReplyToolExtraction:
    reply_text: str = ""
    reply_meta: dict[str, Any] | None = None
    no_reply: bool = False
    no_reply_reason: str = ""
    tool_name: str = ""
    tool_call_id: str = ""


@dataclass(frozen=True)
class RichTerminalOutput:
    html: str
    tool_name: str
    tool_call_id: str
    report_kind: str
    message_index: int


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
    from core.context_compaction import unwrap_tool_result_content

    if isinstance(msg, dict):
        content = message_content_to_text(msg.get("content", ""))
    else:
        content = message_content_to_text(getattr(msg, "content", ""))
    return unwrap_tool_result_content(content)


def _message_field(msg: Any, key: str, default: Any = None) -> Any:
    if isinstance(msg, dict):
        return msg.get(key, default)
    return getattr(msg, key, default)


def _nested_field(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _declared_tool_calls(msg: Any) -> list[tuple[str, str]]:
    raw_calls = _message_field(msg, "tool_calls", None)
    if not isinstance(raw_calls, (list, tuple)):
        return []

    declared: list[tuple[str, str]] = []
    for raw_call in raw_calls:
        call_id = str(
            _nested_field(raw_call, "id", "")
            or _nested_field(raw_call, "call_id", "")
            or ""
        ).strip()
        function = _nested_field(raw_call, "function", None)
        tool_name = str(
            _nested_field(function, "name", "")
            or _nested_field(raw_call, "name", "")
            or ""
        ).strip()
        if call_id and tool_name:
            declared.append((call_id, tool_name))
    return declared


def iter_verified_tool_outputs(messages: list[Any]) -> Iterator[VerifiedToolOutput]:
    """按消息顺序产出具有完整 KT 调用来源的工具结果。

    只有前置 ``assistant.tool_calls`` 声明、结果 ``name`` 与声明一致、且
    ``tool_call_id`` 首次消费时才可信。新的 user/system/assistant 消息会关闭
    上一批尚未消费的声明，避免孤儿结果跨轮次重新关联。
    """

    pending: dict[str, str] = {}
    seen_call_ids: set[str] = set()
    invalid_call_ids: set[str] = set()

    for index, msg in enumerate(messages or []):
        role = _message_role(msg)
        if role == "assistant":
            pending.clear()
            batch_ids: set[str] = set()
            for call_id, tool_name in _declared_tool_calls(msg):
                if call_id in batch_ids or call_id in seen_call_ids:
                    invalid_call_ids.add(call_id)
                    pending.pop(call_id, None)
                    continue
                batch_ids.add(call_id)
                seen_call_ids.add(call_id)
                pending[call_id] = tool_name
            continue
        if role in {"user", "system"}:
            pending.clear()
            continue
        if role != "tool":
            continue

        tool_call_id = str(_message_field(msg, "tool_call_id", "") or "").strip()
        if not tool_call_id or tool_call_id in invalid_call_ids:
            continue

        declared_name = pending.pop(tool_call_id, None)
        if not declared_name:
            continue
        tool_name = str(_message_field(msg, "name", "") or "").strip()
        if not tool_name or declared_name != tool_name:
            continue

        yield VerifiedToolOutput(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            content=_message_content(msg),
            message_index=index,
        )


def normalize_send_mode(value: Any) -> str:
    send_mode = str(value or "normal")
    return send_mode if send_mode in ALLOWED_SEND_MODES else "normal"


def _reply_marker() -> str:
    return REPLY_MARKER


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
    for output in reversed(list(iter_verified_tool_outputs(messages))):
        if output.tool_name not in {"reply", "no_reply"}:
            continue
        try:
            data = json.loads(output.content)
        except (json.JSONDecodeError, TypeError, ValueError):
            data = {}
        if not isinstance(data, dict) or marker not in data:
            continue
        payload = data.get(marker) or {}
        if not isinstance(payload, dict):
            continue
        if output.tool_name == "no_reply":
            if payload.get("no_reply") is not True:
                continue
            if str(payload.get("content", "") or "").strip():
                continue
            return ReplyToolExtraction(
                no_reply=True,
                no_reply_reason=str(payload.get("reason", ""))[:200],
                tool_name=output.tool_name,
                tool_call_id=output.tool_call_id,
            )
        if payload.get("no_reply"):
            continue
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
            tool_name=output.tool_name,
            tool_call_id=output.tool_call_id,
        )
    return ReplyToolExtraction()


def build_rich_tool_result(html: str, *, report_kind: str) -> Any:
    from nanobot_kt.optional_tool_api import ToolResult

    return ToolResult(
        output=build_rich_output(html, report_kind=report_kind),
        exit_code=0,
    )


def extract_rich_terminal_output(
    messages: list[Any],
    *,
    allowed_report_kinds: tuple[str, ...] | None = None,
) -> RichTerminalOutput | None:
    """提取经工具身份校验的富 HTML 终结结果。"""

    allowed = set(allowed_report_kinds or RICH_REPORT_TOOLS)
    for output in reversed(list(iter_verified_tool_outputs(messages))):
        expected_kind = RICH_REPORT_TOOLS.get(output.tool_name)
        if not expected_kind or expected_kind not in allowed:
            continue
        try:
            data = json.loads(output.content)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        payload = data.get(RICH_OUTPUT_MARKER)
        if not isinstance(payload, dict):
            continue
        version = payload.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version != 1:
            continue
        if payload.get("content_type") != "text/html":
            continue
        report_kind = str(payload.get("report_kind", "") or "").strip()
        if report_kind != expected_kind:
            continue
        html = str(payload.get("html", "") or "").strip()
        html_lower = html.lower()
        required_marker = RICH_REPORT_HTML_MARKERS[report_kind]
        if (
            not html_lower.startswith(("<!doctype html", "<html", "<article"))
            or required_marker not in html
        ):
            continue
        return RichTerminalOutput(
            html=html,
            tool_name=output.tool_name,
            tool_call_id=output.tool_call_id,
            report_kind=report_kind,
            message_index=output.message_index,
        )
    return None


def count_final_action_tool_calls(messages: list[Any]) -> dict[str, int]:
    """统计真实 final action marker；不统计普通文本里提到的 reply 字样。"""
    marker = _reply_marker()
    reply_count = 0
    no_reply_count = 0
    for output in iter_verified_tool_outputs(messages):
        if output.tool_name not in {"reply", "no_reply"}:
            continue
        try:
            data = json.loads(output.content)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        payload = data.get(marker)
        if isinstance(payload, dict):
            if (
                output.tool_name == "no_reply"
                and payload.get("no_reply") is True
                and not str(payload.get("content", "") or "").strip()
            ):
                no_reply_count += 1
            elif (
                output.tool_name == "reply"
                and not payload.get("no_reply")
                and str(payload.get("content") or "").strip()
            ):
                reply_count += 1
    return {
        "reply_tool_call_count": reply_count,
        "no_reply_tool_call_count": no_reply_count,
        "structured_fallback_count": 0,
        "total_final_action_count": reply_count + no_reply_count,
    }


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
    action = str(data.get("action") or data.get("final_action") or "").strip().lower()
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

"""LLM 请求发出前的非阻塞 lint 与可观测性提取。"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any


_FRAMEWORK_MARKERS = {
    "available_functions": "Available Functions",
    "available_sub_agents": "Available Sub-Agents",
    "skills": "## Skills",
    "tool_usage": "## Tool Usage",
    "background_execution": "Background Execution",
}

_HEADING_RE = re.compile(r"^\s{0,3}#{1,4}\s+(.+?)\s*$", re.MULTILINE)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tool_name(tool: Any) -> str:
    if not isinstance(tool, dict):
        return ""
    function = tool.get("function")
    if isinstance(function, dict) and function.get("name"):
        return str(function.get("name") or "")
    if tool.get("name"):
        return str(tool.get("name") or "")
    return ""


def extract_actual_sent_tools(request: dict[str, Any]) -> list[str]:
    """从 OpenAI-compatible payload 中提取实际发送的 tools 名称。"""
    tools = request.get("tools") if isinstance(request, dict) else []
    if not isinstance(tools, (list, tuple)):
        return []
    names = [_tool_name(tool) for tool in tools]
    return [name for name in names if name]


def _detect_framework_markers(text: str) -> list[str]:
    return [key for key, marker in _FRAMEWORK_MARKERS.items() if marker in text]


def infer_message_sources(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给最终 messages 做轻量来源推断，供日志排查使用。"""
    sources: list[dict[str, Any]] = []
    for index, msg in enumerate(messages):
        if not isinstance(msg, dict):
            content = _as_text(msg)
            role = ""
        else:
            content = _as_text(msg.get("content"))
            role = _as_text(msg.get("role"))
        explicit_source = ""
        if isinstance(msg, dict):
            explicit_source = _as_text(msg.get("_nanobot_source") or msg.get("source"))

        source = explicit_source
        if not source:
            if role == "system" and "[RuntimeTool]" in content:
                source = "legacy_runtime_tool_prompt"
            elif role == "system" and _detect_framework_markers(content):
                source = "kt_framework_tools_doc"
            elif role == "system" and content.lstrip().startswith("## 交互定位"):
                source = "base_system_prompt"
            elif role == "system" and "<identity_context>" in content:
                source = "identity_context"
            elif role == "system" and "<persona_reference" in content:
                source = "persona_reference"
            elif role == "system" and "## 私聊行为" in content:
                source = "private_behavior"
            elif role == "system" and (
                "本轮只随口接一句" in content
                or "本轮简短处理" in content
                or "本轮认真处理" in content
            ):
                source = "effort_constraint"
            elif role == "system" and "群聊行为" in content:
                source = "group_rules"
            elif role == "system" and "群聊上下文使用规则" in content:
                source = "group_context_rules"
            elif role == "system" and "<conversation_context>" in content:
                source = "conversation_context_header"
            elif role == "system" and "<history_context>" in content:
                source = "history_context_header"
            elif role == "system" and "<group_recent_context>" in content:
                source = "group_recent_context"
            elif role == "system" and "<group_memory_context" in content:
                source = "group_memory_context"
            elif role == "system" and ("<runtime_context>" in content or "运行时上下文" in content):
                source = "runtime_context"
            elif role in {"system", "user"} and (
                "<reply_contract_retry>" in content
                or "你刚才没有调用 reply 或 no_reply 工具" in content
            ):
                source = "reply_contract_retry"
            elif role == "system":
                source = "unknown_system"
            elif role == "user" and ("[Tool None completed]" in content or content.startswith("[Tool ")):
                source = "internal_tool_completion"
            elif role == "user" and "系统生成的上下文提示，不是用户发言" in content:
                source = "history_gap_marker"
            elif role == "tool":
                source = "tool_result"
            elif role == "assistant":
                source = "assistant"
            elif role == "user":
                source = "user"
            else:
                source = role or "unknown"

        sources.append({
            "index": index,
            "role": role,
            "source": source,
            "chars": len(content),
            "sha256": _sha256(content),
            "preview": content[:160],
        })
    return sources


def _add_issue(
    issues: list[dict[str, Any]],
    severity: str,
    code: str,
    message: str,
    **details: Any,
) -> None:
    issue: dict[str, Any] = {
        "severity": severity,
        "code": code,
        "message": message,
    }
    if details:
        issue["details"] = details
    issues.append(issue)


def lint_llm_request(request: Any) -> dict[str, Any]:
    """对真实待发送 payload 做非阻塞 lint。

    返回结构会直接写入 LLMApiRequestLog.request_lint_json。
    """
    payload = request if isinstance(request, dict) else {}
    raw_message_value = payload.get("messages") or []
    raw_messages = (
        list(raw_message_value)
        if isinstance(raw_message_value, (list, tuple))
        else []
    )
    messages = [msg for msg in raw_messages if isinstance(msg, dict)]
    raw_tool_value = payload.get("tools") or []
    raw_tools = (
        list(raw_tool_value)
        if isinstance(raw_tool_value, (list, tuple))
        else []
    )
    from core.prompt_v2.request_metrics import calculate_request_metrics

    payload_metrics = calculate_request_metrics(
        messages=raw_messages,
        tools=raw_tools,
    )
    actual_sent_tools = extract_actual_sent_tools(payload)
    runtime_enabled = list(actual_sent_tools)
    runtime_disabled: list[str] = []
    message_sources = infer_message_sources(messages)
    issues: list[dict[str, Any]] = []

    system_contents = [
        _as_text(msg.get("content"))
        for msg in messages
        if _as_text(msg.get("role")) == "system"
    ]
    duplicates = [content for content, count in Counter(system_contents).items() if content and count > 1]
    if duplicates:
        _add_issue(
            issues,
            "P1",
            "system_exact_duplicate",
            "存在完全重复的 system message。",
            duplicate_count=len(duplicates),
        )

    heading_counts: Counter[str] = Counter()
    for content in system_contents:
        heading_counts.update(h.strip() for h in _HEADING_RE.findall(content) if h.strip())
    duplicate_headings = sorted([heading for heading, count in heading_counts.items() if count > 1])
    if duplicate_headings:
        _add_issue(
            issues,
            "P1",
            "system_heading_duplicate",
            "system message 中存在重复标题。",
            headings=duplicate_headings[:20],
        )

    legacy_runtime_tool_indexes = [
        src["index"]
        for src in message_sources
        if src["source"] == "legacy_runtime_tool_prompt"
    ]
    if legacy_runtime_tool_indexes:
        _add_issue(
            issues,
            "P0",
            "legacy_runtime_tool_prompt_present",
            "最终 Prompt 中仍包含已废弃的 RuntimeTool 说明。",
            indexes=legacy_runtime_tool_indexes[:20],
        )

    unknown_system_indexes = [
        src["index"] for src in message_sources
        if src["role"] == "system" and src["source"] == "unknown_system"
    ]
    if unknown_system_indexes:
        _add_issue(
            issues,
            "P2",
            "unknown_system_source",
            "存在无法推断来源的 system message。",
            indexes=unknown_system_indexes[:20],
        )

    has_nanobot_runtime = any("<runtime_context>" in _as_text(msg.get("content")) for msg in messages)
    has_current_user_input = any(
        _as_text(msg.get("role")) == "user"
        and "<user_input>" in _as_text(msg.get("content"))
        and "</user_input>" in _as_text(msg.get("content"))
        for msg in messages
    )
    has_reply_contract_retry = any(
        "<reply_contract_retry>" in _as_text(msg.get("content"))
        for msg in messages
    )
    if has_nanobot_runtime and not has_current_user_input and not has_reply_contract_retry:
        _add_issue(
            issues,
            "P0",
            "missing_current_user_input",
            "Nanobot 运行时请求缺少当前 <user_input> user message。",
        )

    framework_markers: set[str] = set()
    for msg in messages:
        content = _as_text(msg.get("content"))
        markers = _detect_framework_markers(content)
        framework_markers.update(markers)
        if markers:
            _add_issue(
                issues,
                "P0",
                "kt_framework_tool_docs",
                "KT 自动工具说明进入最终 prompt。",
                markers=markers,
            )

    for src in message_sources:
        if src["role"] != "user":
            continue
        if src["source"] == "internal_tool_completion":
            _add_issue(
                issues,
                "P0",
                "internal_tool_message_as_user",
                "内部工具完成消息以 user role 注入。",
                index=src["index"],
            )
        elif src["source"] == "reply_contract_retry":
            continue
        elif src["source"] == "history_gap_marker":
            _add_issue(
                issues,
                "P1",
                "history_gap_marker_as_user",
                "历史 gap marker 以 user role 注入。",
                index=src["index"],
            )

    severity_counts = {
        "P0": sum(1 for issue in issues if issue["severity"] == "P0"),
        "P1": sum(1 for issue in issues if issue["severity"] == "P1"),
        "P2": sum(1 for issue in issues if issue["severity"] == "P2"),
    }
    return {
        "ok": severity_counts["P0"] == 0,
        "severity_counts": severity_counts,
        "issues": issues,
        "message_sources": message_sources,
        "actual_sent_tools": actual_sent_tools,
        "runtime_enabled_tools": runtime_enabled,
        "runtime_disabled_tools": runtime_disabled,
        "framework_injected_tools": sorted(framework_markers),
        "payload_metrics": payload_metrics.to_dict(),
    }

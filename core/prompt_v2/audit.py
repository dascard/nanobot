from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PromptAuditResult:
    ok: bool
    issues: list[str]


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_content_text(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(_content_text(v) for v in value.values())
    return str(value or "")


def audit_prompt_plan(plan) -> PromptAuditResult:
    contents = [_content_text(m.get("content", "")) for m in plan.messages or []]
    joined = "\n".join(contents)
    issues: list[str] = []

    user_input_count = sum("<user_input>" in c for c in contents)
    if user_input_count != 1:
        issues.append(f"current user input must appear once, got {user_input_count}")

    runtime_tool_count = sum("[RuntimeTool]" in c for c in contents)
    if runtime_tool_count != 1:
        issues.append(f"runtime_tool_prompt must appear once, got {runtime_tool_count}")

    persona_count = sum("<persona_reference" in c for c in contents)
    if persona_count != 1:
        issues.append(f"persona_reference must appear once, got {persona_count}")

    if str(plan.chat_type) == "group":
        if "## 群聊行为" not in joined:
            issues.append("group plan missing group policy")
        if "## 私聊行为" in joined:
            issues.append("group plan contains private policy")
    elif str(plan.chat_type) == "private":
        if "## 私聊行为" not in joined:
            issues.append("private plan missing private policy")
        if "## 群聊行为" in joined or "## 群聊发言时机" in joined:
            issues.append("private plan contains group policy")

    return PromptAuditResult(ok=not issues, issues=issues)

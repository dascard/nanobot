"""Persona 候选提取 Prompt 和日志格式化 helper。"""

from __future__ import annotations

from typing import Any

from core.context_builder import sanitize_prompt_text
from core.moderation import is_no_learn_meta


def get_candidate_extraction_system_prompt() -> str:
    """读取 Persona Candidate 的唯一 system task template。"""

    from core.prompt_v2.task_templates import render_task_prompt

    return render_task_prompt("tasks/persona_candidate_system", {})


def filter_user_messages(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """过滤出允许学习的 role=user 消息。"""

    return [
        log
        for log in logs
        if str(log.get("role", "")).strip().lower() == "user"
        and not is_no_learn_meta(log.get("meta_json"))
    ]


def format_candidate_logs(logs: list[dict[str, Any]]) -> str:
    """把用户日志格式化成带真实 log_id 的候选提取输入。"""

    lines: list[str] = []
    for log in filter_user_messages(logs):
        log_id = log.get("id", log.get("log_id", ""))
        created_at = str(log.get("created_at") or "").strip()
        content = sanitize_prompt_text(log.get("content") or "", 500).strip()
        if not content:
            continue
        if created_at:
            lines.append(f"[log_id={log_id}][{created_at}] user: {content}")
        else:
            lines.append(f"[log_id={log_id}] user: {content}")
    return "\n".join(lines)


def build_candidate_extraction_prompt(
    facts_summary: str,
    logs_text: str | list[dict[str, Any]],
) -> str:
    """通过 Task Contract 构造候选提取 user prompt。"""

    formatted_logs = (
        format_candidate_logs(logs_text)
        if isinstance(logs_text, list)
        else str(logs_text or "")
    )
    from core.prompt_v2.task_templates import render_task_prompt

    return render_task_prompt(
        "tasks/memory_extract",
        {
            "conversation": formatted_logs,
            "existing_memory": str(facts_summary or ""),
        },
    )


__all__ = [
    "build_candidate_extraction_prompt",
    "filter_user_messages",
    "format_candidate_logs",
    "get_candidate_extraction_system_prompt",
]

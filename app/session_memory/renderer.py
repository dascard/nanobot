"""Rolling session summary prompt renderer。"""

from __future__ import annotations

from html import escape

from app.session_memory.config import ROLLING_SUMMARY_MAX_CHARS
from core.context_builder import sanitize_prompt_text
from core.db.models.session_memory import RollingSessionSummary
from app.session_memory.rolling_summary import (
    normalize_summary_source_type,
    summary_covered_until,
)


def render_rolling_summary_context(summary: RollingSessionSummary | None) -> str:
    if summary is None:
        return ""
    text = sanitize_prompt_text(summary.summary_text or "", max_chars=ROLLING_SUMMARY_MAX_CHARS)
    if not text.strip():
        return ""
    summary_kind = sanitize_prompt_text(
        str(getattr(summary, "summary_kind", "") or "deterministic_fallback"),
        max_chars=80,
    )
    source_type = normalize_summary_source_type(
        getattr(summary, "source_type", "conversation_turn")
    )
    return (
        f'<rolling_session_summary session_id="{escape(summary.session_id or "", quote=True)}" '
        f'summary_kind="{escape(summary_kind, quote=True)}" '
        f'source_type="{escape(source_type, quote=True)}" '
        f'covered_until_source_id="{summary_covered_until(summary)}">\n'
        "以下是当前 session 中短期原文窗口之外的旧上下文摘要。\n"
        "它不包含最近原文窗口，也不包含本轮用户输入。\n"
        "如果它与 recent raw window 或 current user_input 冲突，以后者为准。\n\n"
        f"{escape(text, quote=False)}\n"
        "</rolling_session_summary>"
    )

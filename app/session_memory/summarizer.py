"""Rolling summary 生成器。

第一版提供确定性摘要，避免在同步 prompt 构建链路里依赖外部 LLM。
后续可以在同一接口后面接入异步 LLM 预计算。
"""

from __future__ import annotations

from typing import Any, Sequence

from core.database import ConversationTurn, RollingSessionSummary
from app.session_memory import config
from app.session_memory.windowing import estimate_tokens, safe_meta


_URL_RE = r"https?://[^\s，。！？；、)）\]>]+"


def _format_turn_line(turn: ConversationTurn, *, max_chars: int = 220) -> str:
    from core.context_builder import sanitize_prompt_text

    meta = safe_meta(turn.meta_json)
    sender = str(meta.get("sender_name") or meta.get("sender_id") or "").strip()
    ts = turn.created_at.strftime("%Y-%m-%d %H:%M:%S") if turn.created_at else ""
    content = sanitize_prompt_text(turn.content or "", max_chars=max_chars).strip()
    prefix = f"[turn_id={turn.id}][{ts}][{turn.role}]"
    if sender:
        prefix += f"[{sender}]"
    return f"{prefix} {content}".strip()


def _strip_sender_prefix(text: str) -> str:
    import re

    value = str(text or "").strip()
    match = re.match(r"^\[([^\]]{1,80})\]\s*[:：]?\s*(.*)$", value, re.DOTALL)
    if match:
        return match.group(2).strip()
    return value


def _strip_turn_metadata(text: str) -> str:
    import re

    value = str(text or "")
    value = re.sub(r"\[turn_id=\d+\]\[[^\]]*\]\[(?:user|assistant)\]\[[^\]]{1,80}\]\s*", "", value)
    value = re.sub(r"\[turn_id=\d+\]\[[^\]]*\]\[(?:user|assistant)\]\s*", "", value)
    value = re.sub(r"\[turn_id=\d+\]", "", value)
    return value.strip()


def _redact_urls(text: str) -> str:
    import re

    value = re.sub(_URL_RE, "链接", str(text or ""), flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _clean_summary_memory_text(text: str) -> str:
    value = _strip_turn_metadata(text)
    value = _redact_urls(value)
    value = "\n".join(line.strip() for line in value.splitlines())
    return value.strip()


def _format_turn_snippet(turn: ConversationTurn, *, max_chars: int = 160) -> str:
    from core.context_builder import sanitize_prompt_text

    content = sanitize_prompt_text(turn.content or "", max_chars=max_chars).strip()
    content = _strip_sender_prefix(_clean_summary_memory_text(content))
    return " ".join(content.split())


def _truncate_text(text: str, max_chars: int, *, suffix: str = "\n...[摘要截断]") -> str:
    if len(text) <= max_chars:
        return text
    cap = max(0, max_chars - len(suffix))
    return text[:cap].rstrip() + suffix


def _compact_previous_and_pending(
    *,
    previous_text: str,
    pending_turns: Sequence[ConversationTurn],
    max_chars: int,
) -> str:
    previous = _truncate_text(_clean_summary_memory_text(previous_text), 600, suffix="\n...[旧摘要截断]")
    user_lines = [
        _format_turn_snippet(turn, max_chars=120)
        for turn in pending_turns
        if turn.role == "user"
    ][-6:]
    assistant_lines = [
        _format_turn_snippet(turn, max_chars=120)
        for turn in pending_turns
        if turn.role == "assistant"
    ][-4:]

    parts: list[str] = ["代码兜底摘要：以下为自动压缩的对话要点，建议等待或手动生成 LLM 摘要提升质量。"]
    if previous:
        parts.append("此前已知:\n" + previous)
    if user_lines:
        parts.append("新增用户侧要点:\n" + "\n".join(f"- {line}" for line in user_lines if line))
    if assistant_lines:
        parts.append("新增助手侧结论:\n" + "\n".join(f"- {line}" for line in assistant_lines if line))

    summary = "\n\n".join(parts).strip()
    if len(summary) <= max_chars:
        return summary

    # 如果仍超长，优先保留新增要点，进一步压缩旧摘要。
    parts = ["代码兜底摘要：以下为自动压缩的对话要点，建议等待或手动生成 LLM 摘要提升质量。"]
    if previous:
        parts.append("此前已知:\n" + _truncate_text(_clean_summary_memory_text(previous_text), 300, suffix="\n...[旧摘要截断]"))
    if user_lines:
        parts.append("新增用户侧要点:\n" + "\n".join(f"- {line}" for line in user_lines if line))
    if assistant_lines:
        parts.append("新增助手侧结论:\n" + "\n".join(f"- {line}" for line in assistant_lines if line))
    return _truncate_text("\n\n".join(parts).strip(), max_chars)


def build_rolling_summary_payload(
    *,
    previous_summary: RollingSessionSummary | None,
    pending_turns: Sequence[ConversationTurn],
) -> dict[str, Any]:
    previous_text = str(getattr(previous_summary, "summary_text", "") or "").strip()
    user_lines = [_format_turn_snippet(turn, max_chars=180) for turn in pending_turns if turn.role == "user"]
    assistant_lines = [
        _format_turn_snippet(turn, max_chars=180)
        for turn in pending_turns
        if turn.role == "assistant"
    ]
    source_text = "\n".join(user_lines[-6:] + assistant_lines[-4:])
    summary = _compact_previous_and_pending(
        previous_text=previous_text,
        pending_turns=pending_turns,
        max_chars=config.ROLLING_SUMMARY_MAX_CHARS,
    )

    open_threads = []
    if user_lines:
        open_threads.append(user_lines[-1])
    decisions = assistant_lines[-2:]
    keywords = _extract_keywords(source_text)

    return {
        "summary": summary,
        "open_threads": open_threads,
        "decisions": decisions,
        "important_user_requests": user_lines[-4:],
        "artifacts": [],
        "warnings": [],
        "evidence_turn_ids": [int(turn.id) for turn in pending_turns if getattr(turn, "id", None)],
        "keywords": keywords,
        "quality": {
            "score": 0.72 if pending_turns else 0.0,
            "issues": [],
            "source_token_estimate": sum(estimate_tokens(turn.content or "") for turn in pending_turns),
        },
    }


def render_summary_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    summary = str(payload.get("summary") or "").strip()
    if summary:
        parts.append(summary)
    for title, key in [
        ("未解决/延续事项", "open_threads"),
        ("已确认结论", "decisions"),
        ("重要用户请求", "important_user_requests"),
        ("关键词", "keywords"),
    ]:
        values = payload.get(key)
        if not isinstance(values, list) or not values:
            continue
        lines = [str(item).strip() for item in values if str(item).strip()]
        if lines:
            parts.append(f"{title}:\n" + "\n".join(f"- {line}" for line in lines))
    return "\n\n".join(parts).strip()


def _extract_keywords(text: str) -> list[str]:
    import re

    candidates = re.findall(r"[A-Za-z][A-Za-z0-9_+-]{2,}|[\u4e00-\u9fff]{2,8}", text)
    seen: set[str] = set()
    result: list[str] = []
    for item in candidates:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if len(result) >= 8:
            break
    return result

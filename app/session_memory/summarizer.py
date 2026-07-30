"""Rolling summary 生成器。

第一版提供确定性摘要，避免在同步 prompt 构建链路里依赖外部 LLM。
后续可以在同一接口后面接入异步 LLM 预计算。
"""

from __future__ import annotations

import json
from typing import Any
from collections.abc import Sequence

from core.db.models.chat import ConversationTurn
from core.db.models.session_memory import RollingSessionSummary
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


def _strip_deterministic_fallback_scaffolding(text: str) -> str:
    """移除旧版 fallback 自我描述，避免每轮递归继承同一段样板。"""

    import re

    value = str(text or "")
    value = re.sub(
        r"代码兜底摘要：仅继承上次摘要正文；本轮新增内容见结构化字段，\s*"
        r"建议等待或手动生成 LLM 摘要提升质量。",
        "",
        value,
    )
    value = re.sub(r"(?m)^\s*此前已知:\s*$", "", value)
    value = re.sub(
        r"(?m)^\s*本轮新增\s+\d+\s+条消息"
        r"（用户\s+\d+\s+条、助手\s+\d+\s+条）。\s*$",
        "",
        value,
    )
    value = re.sub(r"\n{3,}", "\n\n", value)
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
    previous = _strip_deterministic_fallback_scaffolding(
        _clean_summary_memory_text(previous_text)
    )
    if previous:
        return _truncate_text(previous, max_chars)
    if pending_turns:
        return "近期对话事实已保留在下方结构化字段中。"
    return ""


def _previous_summary_payload(
    previous_summary: RollingSessionSummary | None,
) -> dict[str, Any]:
    if previous_summary is None:
        return {}
    raw = str(getattr(previous_summary, "summary_json", "") or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, dict):
            if str(parsed.get("summary") or "").strip():
                return parsed
            legacy_text = str(getattr(previous_summary, "summary_text", "") or "").strip()
            return {**parsed, "summary": legacy_text} if legacy_text else parsed
    legacy_text = str(getattr(previous_summary, "summary_text", "") or "").strip()
    return {"summary": legacy_text} if legacy_text else {}


def _merge_text_items(*groups: Any, limit: int) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        if not isinstance(group, (list, tuple)):
            continue
        for item in group:
            text = _clean_summary_memory_text(str(item or ""))
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
    return merged[-limit:]


def build_rolling_summary_payload(
    *,
    previous_summary: RollingSessionSummary | None,
    pending_turns: Sequence[ConversationTurn],
) -> dict[str, Any]:
    previous_payload = _previous_summary_payload(previous_summary)
    previous_text = str(previous_payload.get("summary") or "").strip()
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

    current_open_threads = user_lines[-1:] if user_lines else []
    open_threads = _merge_text_items(
        previous_payload.get("open_threads"),
        current_open_threads,
        limit=6,
    )
    decisions = _merge_text_items(
        previous_payload.get("decisions"),
        assistant_lines[-2:],
        limit=8,
    )
    important_user_requests = _merge_text_items(
        previous_payload.get("important_user_requests"),
        user_lines[-4:],
        limit=8,
    )
    keywords = _merge_text_items(
        previous_payload.get("keywords"),
        _extract_keywords(source_text),
        limit=8,
    )

    return {
        "summary": summary,
        "open_threads": open_threads,
        "decisions": decisions,
        "important_user_requests": important_user_requests,
        "artifacts": _merge_text_items(previous_payload.get("artifacts"), limit=8),
        "warnings": _merge_text_items(previous_payload.get("warnings"), limit=8),
        "evidence_turn_ids": [int(turn.id) for turn in pending_turns if getattr(turn, "id", None)],
        "keywords": keywords,
        "quality": {
            "score": 0.45 if pending_turns else 0.0,
            "issues": ["deterministic_fallback", "llm_summary_pending"],
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

"""MemoryDigest v2 结构化摘要构建器。"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from core.database import ChatLog

from .quality import build_quality
from .renderer import render_digest_levels

logger = logging.getLogger("nanobot.memory_digest.builder")


@dataclass(frozen=True)
class MemoryDigestBuildResult:
    status: str
    meta: dict[str, Any]
    level_contents: dict[int, str]


_HTML_SIGNATURES = ("<!doctype", "<html", "<article", "<div class=", "```html")
_SKIP_ROLES = {"tool", "model", "system", "function"}
_URL_RE = re.compile(r"https?://[^\s，。！？；、)）\]>]+", re.IGNORECASE)
_COMMAND_WORDS = {
    "签到",
    "打卡",
    "钓鱼",
    "千连钓鱼",
    "万连钓鱼",
    "抽卡",
    "十连抽卡",
    "千连抽卡",
    "万连抽卡",
    "宠物",
    "宠物系统",
}
_STOPWORDS = {
    "这个",
    "那个",
    "今天",
    "一下",
    "可以",
    "不是",
    "就是",
    "感觉",
    "群里",
    "讨论",
    "效果",
    "图片",
    "http",
    "https",
    "www",
    "com",
    "video",
}


def _safe_meta(meta_json: str | None) -> dict[str, Any]:
    try:
        data = json.loads(meta_json or "{}")
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.debug(
            "[MemoryDigest] invalid meta_json ignored len=%d: %s",
            len(str(meta_json or "")),
            exc,
            exc_info=True,
        )
        return {}


def _is_html_blob(content: str) -> bool:
    c = (content or "").strip().lower()
    return any(c.startswith(sig) for sig in _HTML_SIGNATURES)


def _html_label(content: str) -> str:
    c = (content or "").lower()
    if "news-brief" in c or "日报" in c or "daily" in c:
        return f"[AI日报 {len(content)} chars]"
    if "group_analysis" in c or "群聊分析" in c:
        return f"[群聊分析报告 {len(content)} chars]"
    m = re.search(r"<title>(.*?)</title>", c, re.IGNORECASE)
    if m:
        return f"[HTML报告: {m.group(1)[:40]} {len(content)} chars]"
    return f"[HTML内容 {len(content)} chars]"


def _normalize_short(content: str) -> str:
    return re.sub(r"\s+", "", str(content or "")).strip().lower()


def _message_body(content: str, sender_name: str = "") -> str:
    text = str(content or "").strip()
    if not text:
        return ""
    match = re.match(r"^\[([^\]]{1,80})\]\s*[:：]\s*(.*)$", text, re.DOTALL)
    if not match:
        return text
    sender = str(sender_name or "").strip()
    prefix = match.group(1).strip()
    body = match.group(2).strip()
    if not sender or prefix == sender or prefix in sender or sender in prefix:
        return body
    return body


def _strip_urls(text: str) -> str:
    value = _URL_RE.sub("", str(text or ""))
    value = re.sub(r"\s+", " ", value)
    return value.strip(" \t\r\n-_:：，。！？；、【】[]()（）")


def _clean_digest_content(content: str) -> str:
    value = _strip_urls(content)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _content_tokens(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_.+-]*|[\u4e00-\u9fff]{2,}", text or "")
    cleaned = []
    for token in tokens:
        value = token.strip()
        if not value:
            continue
        if re.fullmatch(r"[A-Za-z]", value):
            continue
        if re.fullmatch(r"[A-Za-z0-9_.+-]+", value) and "." in value and len(value) > 18:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", value) and len(value) > 14:
            continue
        if value.lower() in _STOPWORDS or value in _STOPWORDS:
            continue
        cleaned.append(value)
    return cleaned


def _detail_score(row: dict[str, Any]) -> float:
    content = str(row.get("content") or "").strip()
    normalized = _normalize_short(content)
    if not content:
        return -1.0
    if MemoryDigestBuilder._is_image_placeholder(normalized):
        return -1.0
    if re.fullmatch(r"[\d\s.。!！?？哈啊哦嗯呃~～]+", content):
        return -0.5

    tokens = _content_tokens(content)
    score = min(len(content), 160) / 160
    if "?" in content or "？" in content:
        score += 0.35
    if any(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.+-]*", token) for token in tokens):
        score += 0.18
    if any(re.fullmatch(r"[\u4e00-\u9fff]{4,}", token) for token in tokens):
        score += 0.18
    if len(tokens) <= 1 and len(normalized) < 12:
        score -= 0.25
    return score


class MemoryDigestBuilder:
    """从 ChatLog 构造 schema_version=2 的三层摘要。

    第一版采用确定性结构化摘要，避免 LLM JSON 失败时写入 active 脏摘要。
    """

    def __init__(self, *, max_details: int = 10):
        self.max_details = max(3, int(max_details))

    def build(
        self,
        *,
        user_id: str,
        session_id: str,
        digest_date: str,
        logs: Iterable[ChatLog],
    ) -> MemoryDigestBuildResult:
        log_rows = list(logs)
        filtered_by_reason: Counter[str] = Counter()
        excluded_noise: list[dict[str, Any]] = []
        valid: list[dict[str, Any]] = []
        seen_short: set[str] = set()

        for log in log_rows:
            reason = self._skip_reason(log, seen_short)
            if reason:
                filtered_by_reason[reason] += 1
                excluded_noise.append({
                    "log_id": getattr(log, "id", None),
                    "reason": reason,
                })
                continue
            valid.append(self._format_valid_log(log))

        source_stats = {
            "raw_log_count": len(log_rows),
            "valid_log_count": len(valid),
            "filtered_count": len(log_rows) - len(valid),
            "noise_ratio": round((len(log_rows) - len(valid)) / max(1, len(log_rows)), 4),
            "filtered_by_reason": dict(filtered_by_reason),
        }

        base_meta: dict[str, Any] = {
            "schema_version": 2,
            "digest_date": digest_date,
            "session_id": session_id,
            "user_id": user_id,
            "source_stats": source_stats,
            "excluded_noise": excluded_noise[:80],
        }

        if not valid:
            meta = {
                **base_meta,
                "status": "skipped",
                "preview": {"brief": "", "keywords": [], "participants": []},
                "long_summary": {
                    "topic_flow": "",
                    "important_details": [],
                    "conclusions": [],
                    "open_loops": [],
                },
                "recall_cards": [],
                "quality": build_quality(
                    score=0,
                    issues=["no_valid_logs"],
                    should_inject_preview=False,
                ),
            }
            return MemoryDigestBuildResult(
                status="skipped",
                meta=meta,
                level_contents={0: "", 1: "", 2: ""},
            )

        keywords = self._rank_keywords(valid)
        participants = self._participants(valid)
        detail_rows = sorted(valid, key=_detail_score, reverse=True)
        details = [self._build_detail_text(row) for row in detail_rows[: self.max_details] if row["content"]]
        topic_label = "、".join(keywords[:4]) if keywords else "当天聊天内容"
        topic_flow = f"当天主要围绕 {topic_label} 展开，包含 {len(valid)} 条有效消息。"
        brief = f"群聊讨论了 {topic_label}。" if keywords else "群聊产生了一组可召回摘要。"
        evidence_ids = [row["log_id"] for row in valid if row.get("log_id")][:8]
        recall_cards = [{
            "card_id": "card_1",
            "type": "episode_topic",
            "text": self._build_card_text(topic_label, details[:3]),
            "keywords": keywords[:8],
            "importance": min(0.95, 0.55 + min(0.35, len(valid) * 0.03)),
            "evidence_log_ids": evidence_ids,
        }]
        seen_card_text = {recall_cards[0]["text"]}
        for index, row in enumerate(detail_rows[:3], start=2):
            detail = self._build_detail_text(row)
            if not detail:
                continue
            card_text = self._build_detail_card_text(topic_label, detail)
            if card_text in seen_card_text:
                continue
            seen_card_text.add(card_text)
            recall_cards.append({
                "card_id": f"card_{index}",
                "type": "episode_detail",
                "text": card_text,
                "keywords": keywords[:8],
                "importance": min(0.95, 0.50 + max(0.0, _detail_score(row)) * 0.35),
                "evidence_log_ids": [row["log_id"]] if row.get("log_id") else [],
            })
        meta = {
            **base_meta,
            "status": "active",
            "preview": {
                "brief": brief,
                "keywords": keywords[:12],
                "participants": participants[:12],
            },
            "long_summary": {
                "topic_flow": topic_flow,
                "important_details": details,
                "conclusions": [],
                "open_loops": [],
            },
            "recall_cards": recall_cards,
            "quality": build_quality(
                score=0.72 if len(valid) < 3 else 0.82,
                issues=[],
                should_inject_preview=True,
            ),
        }
        return MemoryDigestBuildResult(
            status="active",
            meta=meta,
            level_contents=render_digest_levels(meta),
        )

    def _skip_reason(self, log: ChatLog, seen_short: set[str]) -> str | None:
        role = str(getattr(log, "role", "") or "").strip()
        if role in _SKIP_ROLES:
            return "role"

        meta = _safe_meta(getattr(log, "meta_json", "") or "")
        if any(bool(meta.get(flag)) for flag in ("no_context", "internal", "no_learn")):
            return "meta_flag"

        content = _message_body(
            str(getattr(log, "content", "") or "").strip(),
            str(getattr(log, "sender_name", "") or "").strip(),
        )
        if not content:
            return "empty"
        cleaned_content = _clean_digest_content(content)
        if _URL_RE.search(content) and not cleaned_content:
            return "url_only"
        normalized = _normalize_short(content)
        if self._is_image_placeholder(normalized):
            return "image_placeholder"
        if self._is_bot_command(normalized):
            return "bot_command"
        if len(normalized) <= 16:
            if normalized in seen_short:
                return "duplicate_short"
            seen_short.add(normalized)
        return None

    @staticmethod
    def _is_image_placeholder(normalized: str) -> bool:
        if re.fullmatch(r"\[图片[:：]\d+张\]", normalized):
            return True
        if re.fullmatch(r"\[image(:placeholder)?\]", normalized):
            return True
        if re.fullmatch(r"\[cq:image,[^\]]+\]", normalized):
            return True
        return False

    @staticmethod
    def _is_bot_command(normalized: str) -> bool:
        if normalized in _COMMAND_WORDS:
            return True
        return bool(re.fullmatch(r"(十|百|千|万|\d+)?连?(钓鱼|抽卡)", normalized))

    @staticmethod
    def _format_valid_log(log: ChatLog) -> dict[str, Any]:
        role = str(getattr(log, "role", "") or "unknown").strip()
        sender = str(getattr(log, "sender_name", "") or "").strip()
        content = _message_body(str(getattr(log, "content", "") or "").strip(), sender)
        if role == "assistant" and _is_html_blob(content):
            content = _html_label(content)
        else:
            content = _clean_digest_content(content)
        content = re.sub(r"\s+", " ", content)
        if len(content) > 500:
            content = content[:500] + "..."
        ts = getattr(log, "created_at", None)
        time_label = ts.strftime("%H:%M") if ts else "--:--"
        return {
            "log_id": getattr(log, "id", None),
            "time": time_label,
            "role": role,
            "sender": sender or role,
            "content": content,
            "line": f"[log_id={getattr(log, 'id', '')}][{time_label}] {sender or role}: {content}",
        }

    @staticmethod
    def _rank_keywords(valid: list[dict[str, Any]]) -> list[str]:
        counts: Counter[str] = Counter()
        original_case: dict[str, str] = {}
        sender_tokens = {
            token.lower() if re.fullmatch(r"[A-Za-z0-9_.+-]+", token) else token
            for row in valid
            for token in _content_tokens(str(row.get("sender") or ""))
        }
        for row in valid:
            for token in _content_tokens(row.get("content") or ""):
                key = token.lower() if re.fullmatch(r"[A-Za-z0-9_.+-]+", token) else token
                if key in sender_tokens:
                    continue
                counts[key] += 1
                original_case.setdefault(key, token)
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        return [original_case[key] for key, _ in ranked[:16]]

    @staticmethod
    def _participants(valid: list[dict[str, Any]]) -> list[str]:
        seen: dict[str, None] = {}
        for row in valid:
            name = str(row.get("sender") or "").strip()
            if name and name not in seen:
                seen[name] = None
        return list(seen.keys())

    @staticmethod
    def _build_detail_text(row: dict[str, Any]) -> str:
        sender = str(row.get("sender") or "").strip()
        content = str(row.get("content") or "").strip()
        if not sender or sender == str(row.get("role") or "").strip():
            return content[:160]
        return f"{sender} 提到：{content}"[:160]

    @staticmethod
    def _build_card_text(topic_label: str, details: list[str]) -> str:
        selected = [detail for detail in details if detail][:3]
        if selected:
            return (f"群里讨论了 {topic_label}；要点：" + "；".join(selected))[:160]
        return f"群里讨论了 {topic_label}。"[:160]

    @staticmethod
    def _build_detail_card_text(topic_label: str, detail: str) -> str:
        return f"{topic_label}：{detail}"[:160]

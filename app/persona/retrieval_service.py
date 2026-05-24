"""用户画像检索服务。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session


TYPE_LIMITS = {
    "stable_preference": 3,
    "interaction_style": 2,
    "stable_background": 2,
    "long_term_project": 2,
}
TYPE_PRIOR = {
    "stable_preference": 0.90,
    "interaction_style": 0.85,
    "stable_background": 0.60,
    "long_term_project": 0.70,
}
DEFAULT_MAX_ITEMS = 6
DEFAULT_MAX_CHARS = 900


@dataclass
class PersonaSelection:
    selected: list[Any] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    score_components: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def selected_ids(self) -> list[int]:
        return [int(getattr(row, "id", 0) or 0) for row in self.selected if getattr(row, "id", 0)]


def _tokens(text: str) -> set[str]:
    raw = str(text or "").lower()
    words = set(re.findall(r"[a-z0-9_]{2,}", raw))
    cjk = [ch for ch in raw if "\u4e00" <= ch <= "\u9fff"]
    grams = set()
    for size in (2, 3, 4):
        grams.update("".join(cjk[i:i + size]) for i in range(max(0, len(cjk) - size + 1)))
    return {token for token in words | grams if token.strip()}


def _lexical_relevance(memory_text: str, query_text: str) -> float:
    memory_tokens = _tokens(memory_text)
    query_tokens = _tokens(query_text)
    if not memory_tokens or not query_tokens:
        return 0.0
    overlap = len(memory_tokens & query_tokens)
    if overlap <= 0:
        return 0.0
    return min(1.0, overlap / max(3, min(len(memory_tokens), len(query_tokens))))


def _confidence_score(label: str) -> float:
    return {
        "确认": 1.0,
        "可能": 0.65,
        "待确认": 0.25,
        "归档": 0.0,
    }.get(str(label or ""), 0.5)


def _recency_weight(last_seen: datetime | None) -> float:
    if not last_seen:
        return 0.0
    days = max(0.0, (datetime.now() - last_seen).total_seconds() / 86400)
    return max(0.0, min(1.0, math.exp(-days / 45.0)))


def _evidence_weight(count: int) -> float:
    return min(1.0, max(0.0, float(count or 0) / 5.0))


class PersonaRetrievalService:
    def __init__(self, db: Session):
        self.db = db

    def select(
        self,
        *,
        user_id: str,
        current_user_input: str = "",
        recent_messages: list[dict[str, Any]] | None = None,
        max_items: int = DEFAULT_MAX_ITEMS,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> PersonaSelection:
        from core.database import PersonaFact

        recent_text = "\n".join(str(item.get("content") or "") for item in (recent_messages or []))
        query_text = f"{current_user_input}\n{recent_text}".strip()
        rows = (
            self.db.query(PersonaFact)
            .filter(PersonaFact.user_id == user_id)
            .order_by(PersonaFact.evidence_count.desc(), PersonaFact.last_seen.desc(), PersonaFact.id.asc())
            .limit(120)
            .all()
        )

        selection = PersonaSelection()
        candidates: list[tuple[float, Any]] = []
        type_counts: dict[str, int] = {}

        for row in rows:
            memory_type = str(getattr(row, "memory_type", "") or "stable_preference")
            relevance = _lexical_relevance(row.content, query_text)
            components = self._score_components(row, memory_type, relevance)
            reason = self._skip_reason(row)
            if reason:
                components["skip_reason"] = reason
                selection.score_components[str(row.id)] = components
                selection.skipped.append({"id": row.id, "reason": reason})
                continue
            selection.score_components[str(row.id)] = components
            candidates.append((components["final"], row))

        for _, row in sorted(candidates, key=lambda item: item[0], reverse=True):
            memory_type = str(getattr(row, "memory_type", "") or "stable_preference")
            if len(selection.selected) >= max_items:
                selection.score_components[str(row.id)]["skip_reason"] = "max_items"
                selection.skipped.append({"id": row.id, "reason": "max_items"})
                continue
            if type_counts.get(memory_type, 0) >= TYPE_LIMITS.get(memory_type, 2):
                selection.score_components[str(row.id)]["skip_reason"] = "type_limit"
                selection.skipped.append({"id": row.id, "reason": "type_limit"})
                continue
            if self._would_exceed_budget(selection.selected + [row], max_chars):
                selection.score_components[str(row.id)]["skip_reason"] = "over_budget"
                selection.skipped.append({"id": row.id, "reason": "over_budget"})
                continue
            selection.selected.append(row)
            type_counts[memory_type] = type_counts.get(memory_type, 0) + 1

        return selection

    def _score_components(self, row: Any, memory_type: str, relevance: float) -> dict[str, float]:
        components = {
            "confidence": _confidence_score(getattr(row, "confidence", "")),
            "evidence": _evidence_weight(int(getattr(row, "evidence_count", 0) or 0)),
            "recency": _recency_weight(getattr(row, "last_seen", None)),
            "relevance": relevance,
            "type_prior": TYPE_PRIOR.get(memory_type, 0.45),
        }
        final = (
            components["confidence"] * 0.30
            + components["evidence"] * 0.20
            + components["recency"] * 0.10
            + components["relevance"] * 0.25
            + components["type_prior"] * 0.15
        )
        components["final"] = round(final, 6)
        return components

    def _would_exceed_budget(self, rows: list[Any], max_chars: int) -> bool:
        if int(max_chars or 0) <= 0:
            return False
        from app.persona.renderer import render_persona_context

        return len(render_persona_context("", rows)) > int(max_chars)

    def _skip_reason(self, row: Any) -> str:
        status = str(getattr(row, "status", "") or "")
        policy = str(getattr(row, "inject_policy", "") or "")
        if policy == "never":
            return "inject_policy_never"
        if status != "active" or policy != "auto":
            return "not_active_auto"
        if str(getattr(row, "confidence", "") or "") == "归档":
            return "archived_confidence"
        if int(getattr(row, "evidence_count", 0) or 0) < 2:
            return "low_evidence"
        return ""

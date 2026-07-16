"""群体记忆检索服务。

第一版不依赖 embedding，用规则打分和类型限额保证注入结果可解释。
"""

from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from core.group_runtime.ids import normalize_group_session_id
from core.semantic.reranker import SemanticCandidate
from core.time_utils import db_now_naive


TYPE_LIMITS = {
    "style": 2,
    "topic": 3,
    "preference": 2,
    "relationship": 2,
    "event": 2,
    "slang": 2,
}
TYPE_PRIOR = {
    "style": 0.80,
    "topic": 0.75,
    "preference": 0.65,
    "relationship": 0.45,
    "event": 0.45,
    "slang": 0.40,
}
STRICT_RELEVANCE_TYPES = {"relationship", "event", "slang"}
DEFAULT_MIN_RELEVANCE = 0.05
STRICT_MIN_RELEVANCE = 0.18
AUTO_INJECT_MEMORY_TYPES = {"topic", "preference"}
_RERANKER_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="group-memory-reranker",
)


class GroupMemoryRerankerTimeout(TimeoutError):
    """群记忆 reranker 超过当前请求的硬时限。"""


@dataclass
class GroupMemorySelection:
    selected: list[Any] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    score_components: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def selected_ids(self) -> list[int]:
        return [int(getattr(row, "id", 0) or 0) for row in self.selected if getattr(row, "id", 0)]


def _safe_evidence_ids(raw: str) -> list[int]:
    try:
        value = json.loads(raw or "[]")
    except Exception:
        return []
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _safe_meta(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


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


def _recency_weight(last_seen: datetime | None) -> float:
    if not last_seen:
        return 0.0
    days = max(0.0, (db_now_naive() - last_seen).total_seconds() / 86400)
    return max(0.0, min(1.0, math.exp(-days / 30.0)))


def _evidence_weight(count: int) -> float:
    return min(1.0, max(0.0, float(count or 0) / 5.0))


class GroupMemoryRetrievalService:
    def __init__(
        self,
        db: Session,
        *,
        reranker_provider: Any = None,
        min_reranker: float = 0.55,
        max_sql_candidates: int = 2000,
        reranker_timeout_ms: int | None = None,
    ):
        self.db = db
        self.reranker_provider = reranker_provider
        self.min_reranker = float(min_reranker)
        self.max_sql_candidates = int(max_sql_candidates)
        self.reranker_timeout_ms = (
            max(1, int(reranker_timeout_ms))
            if reranker_timeout_ms is not None
            else None
        )

    def select(
        self,
        *,
        group_id: str,
        current_user_input: str = "",
        recent_messages: list[dict[str, Any]] | None = None,
        max_items: int = 10,
        max_chars: int = 1200,
    ) -> GroupMemorySelection:
        from core.database import GroupMemory

        norm = normalize_group_session_id(group_id)
        recent_text = "\n".join(str(item.get("content") or "") for item in (recent_messages or []))
        query_text = f"{current_user_input}\n{recent_text}".strip()
        rows = (
            self.db.query(GroupMemory)
            .filter(GroupMemory.group_id == norm)
            .order_by(GroupMemory.confidence.desc(), GroupMemory.last_seen.desc(), GroupMemory.id.asc())
            .limit(max(1, self.max_sql_candidates))
            .all()
        )
        reranker_scores = self._reranker_scores(query_text, rows)

        candidates: list[tuple[float, Any]] = []
        selection = GroupMemorySelection()
        type_counts: dict[str, int] = {}

        for row in rows:
            memory_type = str(row.memory_type or "")
            relevance = _lexical_relevance(row.content, query_text)
            components = self._score_components(row, memory_type, relevance)
            reason = self._skip_reason(row)
            if reason:
                components["skip_reason"] = reason
                selection.score_components[str(row.id)] = components
                selection.skipped.append({"id": row.id, "reason": reason})
                continue
            if self.reranker_provider is not None:
                reranker = reranker_scores.get(int(row.id), 0.0)
                components["reranker"] = reranker
                if reranker < self.min_reranker:
                    components["skip_reason"] = "low_reranker"
                    selection.score_components[str(row.id)] = components
                    selection.skipped.append({"id": row.id, "reason": "low_reranker"})
                    continue
                components["final"] = round(components["final"] * 0.65 + reranker * 0.35, 6)
            min_relevance = STRICT_MIN_RELEVANCE if memory_type in STRICT_RELEVANCE_TYPES else DEFAULT_MIN_RELEVANCE
            if self.reranker_provider is None and relevance < min_relevance and memory_type != "style":
                components["skip_reason"] = "low_relevance"
                selection.score_components[str(row.id)] = components
                selection.skipped.append({"id": row.id, "reason": "low_relevance"})
                continue
            selection.score_components[str(row.id)] = components
            candidates.append((components["final"], row))

        for _, row in sorted(candidates, key=lambda item: item[0], reverse=True):
            memory_type = str(row.memory_type or "")
            if len(selection.selected) >= max_items:
                selection.score_components[str(row.id)]["skip_reason"] = "max_items"
                selection.skipped.append({"id": row.id, "reason": "max_items"})
                continue
            if type_counts.get(memory_type, 0) >= TYPE_LIMITS.get(memory_type, 2):
                selection.score_components[str(row.id)]["skip_reason"] = "type_limit"
                selection.skipped.append({"id": row.id, "reason": "type_limit"})
                continue
            if self._would_exceed_render_budget(norm, selection.selected + [row], max_chars):
                selection.score_components[str(row.id)]["skip_reason"] = "over_budget"
                selection.skipped.append({"id": row.id, "reason": "over_budget"})
                continue
            selection.selected.append(row)
            type_counts[memory_type] = type_counts.get(memory_type, 0) + 1

        return selection

    def _reranker_scores(self, query_text: str, rows: list[Any]) -> dict[int, float]:
        if self.reranker_provider is None or not rows:
            return {}
        candidates = [
            SemanticCandidate(
                candidate_id=f"group_memory:{row.id}:memory",
                source_type="group_memory",
                title=f"群体记忆：{row.memory_type}",
                text=row.content or "",
                metadata={
                    "memory_type": row.memory_type,
                    "evidence_short_summary": _safe_meta(getattr(row, "meta_json", "")).get("evidence_short_summary", ""),
                },
            )
            for row in rows
        ]
        try:
            if self.reranker_timeout_ms is None:
                results = self.reranker_provider.rerank(query_text, candidates, top_k=50)
            else:
                future = _RERANKER_EXECUTOR.submit(
                    self.reranker_provider.rerank,
                    query_text,
                    candidates,
                    top_k=50,
                )
                try:
                    results = future.result(timeout=self.reranker_timeout_ms / 1000.0)
                except FutureTimeoutError as exc:
                    future.cancel()
                    raise GroupMemoryRerankerTimeout(
                        f"group memory reranker exceeded {self.reranker_timeout_ms}ms"
                    ) from exc
        except GroupMemoryRerankerTimeout:
            raise
        except Exception:
            return {}
        scores: dict[int, float] = {}
        for result in results:
            parts = str(result.candidate_id).split(":")
            if len(parts) >= 2 and parts[1].isdigit():
                scores[int(parts[1])] = float(result.score or 0.0)
        return scores

    def _score_components(self, row: Any, memory_type: str, relevance: float) -> dict[str, float]:
        components = {
            "confidence": max(0.0, min(1.0, float(getattr(row, "confidence", 0) or 0))),
            "decay": max(0.0, min(1.0, float(getattr(row, "decay_score", 0) or 0))),
            "evidence": _evidence_weight(int(getattr(row, "evidence_count", 0) or 0)),
            "recency": _recency_weight(getattr(row, "last_seen", None)),
            "relevance": relevance,
            "type_prior": TYPE_PRIOR.get(memory_type, 0.35),
        }
        final = (
            components["confidence"] * 0.30
            + components["decay"] * 0.15
            + components["evidence"] * 0.15
            + components["recency"] * 0.10
            + components["relevance"] * 0.25
            + components["type_prior"] * 0.05
        )
        components["final"] = round(final, 6)
        return components

    def _would_exceed_render_budget(self, group_id: str, rows: list[Any], max_chars: int) -> bool:
        from app.group_memory.renderer import render_group_memory_context

        if int(max_chars or 0) <= 0:
            return False
        return len(render_group_memory_context(group_id, rows)) > int(max_chars)

    def _skip_reason(self, row: Any) -> str:
        if str(getattr(row, "status", "") or "") != "active":
            return "inactive_status"
        policy = str(getattr(row, "inject_policy", "") or "auto")
        if policy == "manual_only":
            return "manual_only"
        if policy == "never":
            return "never"
        if policy != "auto":
            return "invalid_policy"
        if str(getattr(row, "memory_type", "") or "") not in AUTO_INJECT_MEMORY_TYPES:
            return "subjective_type_requires_review"
        if float(getattr(row, "confidence", 0) or 0) < 0.55:
            return "low_confidence"
        if float(getattr(row, "decay_score", 0) or 0) < 0.3:
            return "low_decay"
        if not _safe_evidence_ids(getattr(row, "evidence_log_ids_json", "") or ""):
            return "no_evidence"
        return ""

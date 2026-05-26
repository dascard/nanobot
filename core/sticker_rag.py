"""Sticker RAG 检索服务。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from core.database import SemanticIndexItem, StickerMemory
from core.semantic.reranker import SemanticCandidate
from core.semantic.retriever import (
    fts_rowids_for_query,
    lexical_overlap_score,
    load_recall_rows,
    semantic_score_for_row,
)
from core.semantic.scoring import passes_relevance_gate, weighted_score
from core.sticker_memory import (
    GLOBAL_STICKER_STREAM_ID,
    is_sticker_replyable,
    normalize_sticker_stream_id,
    sticker_has_text_index_payload,
    sticker_to_dict,
)


RELEVANCE_WEIGHTS = {"reranker": 0.75, "semantic": 0.15, "lexical": 0.10}
FINAL_WEIGHTS = {
    "relevance": 0.86,
    "usage": 0.07,
    "source_prior": 0.05,
    "recency": 0.02,
}


@dataclass
class _StickerCandidate:
    row: SemanticIndexItem
    sticker: StickerMemory
    lexical: float | None
    semantic: float | None
    reranker: float | None = None
    raw_reranker: float | None = None

    @property
    def candidate_id(self) -> str:
        return f"{self.row.source_type}:{self.row.source_id}:{self.row.source_sub_id}"


def _query_vector(query: str, embedding_provider: Any) -> list[float] | None:
    if embedding_provider is None:
        return None
    try:
        return [float(item) for item in embedding_provider.embed([query])[0]]
    except Exception:
        return None


def _scope_ids(*, group_id: str = "", chat_stream_id: str = "", include_global: bool = True) -> set[str]:
    if not group_id and not chat_stream_id:
        return set()
    stream_id = normalize_sticker_stream_id(group_id=group_id, chat_stream_id=chat_stream_id)
    ids = {stream_id}
    if include_global:
        ids.add(GLOBAL_STICKER_STREAM_ID)
    return ids


def _usage_score(row: StickerMemory) -> float:
    return min(1.0, max(0.0, float(row.usage_count or 0) / 20.0))


def _safe_sticker_id(value: str) -> int | None:
    try:
        return int(str(value or "").strip())
    except Exception:
        return None


class StickerRagService:
    def __init__(
        self,
        db: Session,
        *,
        embedding_provider: Any = None,
        reranker_provider: Any = None,
        min_reranker: float = 0.45,
    ):
        self.db = db
        self.embedding_provider = embedding_provider
        self.reranker_provider = reranker_provider
        self.min_reranker = float(min_reranker)

    def has_index(self) -> bool:
        return (
            self.db.query(SemanticIndexItem.id)
            .filter(SemanticIndexItem.source_type == "sticker")
            .filter(SemanticIndexItem.status == "active")
            .limit(1)
            .first()
            is not None
        )

    def query(
        self,
        query: str,
        *,
        group_id: str = "",
        chat_stream_id: str = "",
        include_global: bool = True,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        rowids = fts_rowids_for_query(self.db, query)
        index_rows = load_recall_rows(
            self.db,
            source_types={"sticker"},
            limit=400,
        )
        sticker_ids = [
            sticker_id
            for row in index_rows
            if (sticker_id := _safe_sticker_id(row.source_id)) is not None
        ]
        if not sticker_ids:
            return []

        stickers = {
            int(row.id): row
            for row in (
                self.db.query(StickerMemory)
                .filter(StickerMemory.id.in_(sticker_ids))
                .all()
            )
        }
        scope = _scope_ids(
            group_id=group_id,
            chat_stream_id=chat_stream_id,
            include_global=include_global,
        )
        query_vector = _query_vector(query, self.embedding_provider)
        candidates: list[_StickerCandidate] = []
        for row in index_rows:
            sticker_id = _safe_sticker_id(row.source_id)
            if sticker_id is None:
                continue
            sticker = stickers.get(sticker_id)
            if sticker is None or not self._passes_hard_gate(sticker, scope):
                continue
            lexical = 1.0 if row.id in rowids else lexical_overlap_score(query, row.lexical_text or row.text or "")
            semantic = semantic_score_for_row(
                row,
                query_vector=query_vector,
                embedding_provider=self.embedding_provider,
            )
            if lexical > 0 or (semantic is not None and semantic >= 0.10):
                candidates.append(_StickerCandidate(
                    row=row,
                    sticker=sticker,
                    lexical=lexical,
                    semantic=semantic,
                ))

        candidates.sort(key=self._pre_score, reverse=True)
        candidates = candidates[:80]
        self._apply_reranker(query, candidates)
        degraded = self.reranker_provider is None
        gated = [
            item for item in candidates
            if passes_relevance_gate(
                {"reranker": item.reranker, "semantic": item.semantic, "lexical": item.lexical},
                degraded=degraded,
                min_reranker=self.min_reranker,
            )
        ]
        ranked = sorted(gated, key=self._final_score, reverse=True)
        return [self._result_item(item) for item in ranked[: max(1, int(limit))]]

    def _passes_hard_gate(self, row: StickerMemory, scope: set[str]) -> bool:
        if str(row.status or "") != "active":
            return False
        if str(row.dedupe_status or "") == "duplicate" or row.duplicate_of_id is not None:
            return False
        if str(row.describe_status or "") != "ok":
            return False
        if scope and str(row.chat_stream_id or "") not in scope:
            return False
        return sticker_has_text_index_payload(row) and is_sticker_replyable(row)

    def _pre_score(self, item: _StickerCandidate) -> float:
        return weighted_score(
            {"semantic": item.semantic, "lexical": item.lexical},
            {"semantic": 0.60, "lexical": 0.40},
        )

    def _apply_reranker(self, query: str, candidates: list[_StickerCandidate]) -> None:
        if self.reranker_provider is None or not candidates:
            return
        rerank_inputs = [
            SemanticCandidate(
                candidate_id=item.candidate_id,
                source_type="sticker",
                title=item.sticker.name or item.row.title or "",
                text=item.row.embedding_text or item.row.text or "",
                metadata={
                    "tags": item.row.meta_json and _safe_meta_list(item.row.meta_json, "tags"),
                    "emotions": item.row.meta_json and _safe_meta_list(item.row.meta_json, "emotions"),
                    "scenario": item.row.meta_json and _safe_meta_text(item.row.meta_json, "scenario"),
                },
            )
            for item in candidates
        ]
        reranked = self.reranker_provider.rerank(query, rerank_inputs, top_k=50)
        scores = {item.candidate_id: item for item in reranked}
        for item in candidates:
            score = scores.get(item.candidate_id)
            if score is not None:
                item.reranker = score.score
                item.raw_reranker = score.raw_score

    def _final_score(self, item: _StickerCandidate) -> float:
        relevance = weighted_score(
            {"reranker": item.reranker, "semantic": item.semantic, "lexical": item.lexical},
            RELEVANCE_WEIGHTS,
        )
        return weighted_score(
            {
                "relevance": relevance,
                "usage": _usage_score(item.sticker),
                "source_prior": item.row.source_prior or 0.0,
                "recency": 0.5,
            },
            FINAL_WEIGHTS,
        )

    def _result_item(self, item: _StickerCandidate) -> dict[str, Any]:
        final = self._final_score(item)
        return sticker_to_dict(item.sticker) | {
            "score": final,
            "score_breakdown": {
                "lexical": item.lexical,
                "semantic": item.semantic,
                "reranker": item.reranker,
                "raw_reranker": item.raw_reranker,
                "usage": _usage_score(item.sticker),
                "final": final,
            },
        }


def _safe_meta(raw: str | None) -> dict[str, Any]:
    import json

    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _safe_meta_list(raw: str | None, key: str) -> list[str]:
    value = _safe_meta(raw).get(key)
    return [str(item) for item in value] if isinstance(value, list) else []


def _safe_meta_text(raw: str | None, key: str) -> str:
    return str(_safe_meta(raw).get(key) or "").strip()

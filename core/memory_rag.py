"""Memory RAG 查询服务。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from core.database import SemanticIndexItem
from core.semantic.reranker import SemanticCandidate
from core.semantic.retriever import (
    fts_recall_hits,
    lexical_overlap_score,
    load_recall_rows,
    load_recall_rows_by_ids,
    semantic_score_for_row,
)
from core.semantic.scoring import passes_relevance_gate, weighted_score


RELEVANCE_WEIGHTS = {"reranker": 0.70, "semantic": 0.20, "lexical": 0.10}
FINAL_WEIGHTS = {
    "relevance": 0.75,
    "quality": 0.08,
    "trust": 0.07,
    "recency": 0.05,
    "source_prior": 0.05,
}


@dataclass
class _Candidate:
    row: SemanticIndexItem
    lexical: float | None
    semantic: float | None
    reranker: float | None = None
    raw_reranker: float | None = None

    @property
    def candidate_id(self) -> str:
        return f"{self.row.source_type}:{self.row.source_id}:{self.row.source_sub_id}"


def _source_types(source: str) -> set[str]:
    if source == "digest":
        return {"memory_digest"}
    if source == "session_summary":
        return {"session_summary"}
    return {"memory_digest", "session_summary"}


def _trust_score(trust_level: str) -> float:
    return {
        "high": 1.0,
        "medium": 0.7,
        "low": 0.35,
    }.get(str(trust_level or "").lower(), 0.5)


def _safe_json(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _query_vector(query: str, embedding_provider: Any) -> list[float] | None:
    if embedding_provider is None:
        return None
    try:
        return [float(item) for item in embedding_provider.embed([query])[0]]
    except Exception:
        return None


class MemoryRagService:
    def __init__(
        self,
        db: Session,
        *,
        embedding_provider: Any = None,
        reranker_provider: Any = None,
        allow_degraded: bool = True,
    ):
        self.db = db
        self.embedding_provider = embedding_provider
        self.reranker_provider = reranker_provider
        self.allow_degraded = allow_degraded

    def query(
        self,
        query: str,
        *,
        source: str = "all",
        user_id: str = "",
        session_id: str = "",
        limit: int = 5,
    ) -> dict[str, Any]:
        source_types = _source_types(source)
        fts_hits = fts_recall_hits(
            self.db,
            query,
            source_types=source_types,
            user_id=user_id,
            session_id=session_id,
            limit=200,
        )
        lexical_by_id = {hit.item_id: hit.lexical_score for hit in fts_hits}
        rows = load_recall_rows(
            self.db,
            source_types=source_types,
            user_id=user_id,
            session_id=session_id,
            limit=400,
        )
        rows_by_id = {int(row.id): row for row in rows}
        missing_fts_ids = [hit.item_id for hit in fts_hits if hit.item_id not in rows_by_id]
        rows_by_id.update(load_recall_rows_by_ids(self.db, missing_fts_ids))
        fts_ordered = [rows_by_id[hit.item_id] for hit in fts_hits if hit.item_id in rows_by_id]
        recent_rows = [row for row in rows if int(row.id) not in {int(item.id) for item in fts_ordered}]
        rows = fts_ordered + recent_rows
        query_vector = _query_vector(query, self.embedding_provider)
        candidates: list[_Candidate] = []
        fts_candidate_count = 0
        semantic_hits = 0
        for row in rows:
            lexical = lexical_by_id.get(int(row.id))
            if lexical is None:
                lexical = lexical_overlap_score(query, row.lexical_text or row.text or "")
            if lexical > 0:
                fts_candidate_count += 1
            semantic = semantic_score_for_row(
                row,
                query_vector=query_vector,
                embedding_provider=self.embedding_provider,
            )
            if semantic is not None and semantic > 0:
                semantic_hits += 1
            if lexical > 0 or (semantic is not None and semantic >= 0.10):
                candidates.append(_Candidate(row=row, lexical=lexical, semantic=semantic))

        candidates.sort(key=lambda item: self._pre_score(item), reverse=True)
        candidates = candidates[:80]
        degraded = self.reranker_provider is None
        fallback_reason = "reranker_unavailable" if degraded else ""

        if self.reranker_provider is not None and candidates:
            rerank_inputs = [
                SemanticCandidate(
                    candidate_id=item.candidate_id,
                    source_type=item.row.source_type,
                    title=item.row.title or "",
                    text=item.row.embedding_text or item.row.text or "",
                    metadata=_safe_json(item.row.meta_json),
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

        gated = [
            item for item in candidates
            if passes_relevance_gate(
                {"reranker": item.reranker, "semantic": item.semantic, "lexical": item.lexical},
                degraded=degraded,
            )
        ]
        parent_items = self._group_by_parent(gated, limit=limit)
        return {
            "query": query,
            "source": source,
            "degraded": degraded,
            "fallback_reason": fallback_reason,
            "stats": {
                "fts_candidates": len(fts_hits),
                "lexical_candidates": fts_candidate_count,
                "embedding_candidates": semantic_hits,
                "merged_candidates": len(candidates),
                "reranker_candidates": len(candidates[:50]) if self.reranker_provider else 0,
                "final_items": len(parent_items),
            },
            "items": parent_items,
        }

    def _pre_score(self, item: _Candidate) -> float:
        return weighted_score(
            {"semantic": item.semantic, "lexical": item.lexical},
            {"semantic": 0.65, "lexical": 0.35},
        )

    def _final_score(self, item: _Candidate) -> float:
        relevance = weighted_score(
            {"reranker": item.reranker, "semantic": item.semantic, "lexical": item.lexical},
            RELEVANCE_WEIGHTS,
        )
        return weighted_score(
            {
                "relevance": relevance,
                "quality": item.row.quality_score or 0.0,
                "trust": _trust_score(item.row.trust_level),
                "recency": 0.5,
                "source_prior": item.row.source_prior or 0.0,
            },
            FINAL_WEIGHTS,
        )

    def _card_dict(self, item: _Candidate) -> dict[str, Any]:
        final = self._final_score(item)
        return {
            "candidate_id": item.candidate_id,
            "source_type": item.row.source_type,
            "source_id": item.row.source_id,
            "source_sub_id": item.row.source_sub_id,
            "title": item.row.title,
            "text": item.row.text,
            "lexical": item.lexical,
            "semantic": item.semantic,
            "reranker": item.reranker,
            "final_score": final,
            "score_breakdown": {
                "lexical": item.lexical,
                "semantic": item.semantic,
                "reranker": item.reranker,
                "final": final,
            },
        }

    def _group_by_parent(self, candidates: list[_Candidate], *, limit: int) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str], list[_Candidate]] = {}
        for item in candidates:
            groups.setdefault((item.row.source_type, item.row.source_id), []).append(item)

        parents: list[dict[str, Any]] = []
        for (source_type, source_id), items in groups.items():
            cards = sorted((self._card_dict(item) for item in items), key=lambda card: card["final_score"], reverse=True)
            top_cards = cards[:2]
            parent_score = top_cards[0]["final_score"] if top_cards else 0.0
            best_card = top_cards[0] if top_cards else {}
            parent: dict[str, Any] = {
                "source_type": source_type,
                "source": "digest" if source_type == "memory_digest" else "session_summary",
                "source_id": source_id,
                "parent_score": parent_score,
                "source_prior": max(float(item.row.source_prior or 0.0) for item in items),
                "matched_cards": top_cards,
                "score_breakdown": {
                    "best_card": best_card.get("score_breakdown", {}),
                    "matched_cards": [card["score_breakdown"] for card in top_cards],
                },
            }
            if source_type == "memory_digest":
                parent["digest_id"] = int(source_id) if str(source_id).isdigit() else source_id
            if source_type == "session_summary":
                parent["summary_id"] = int(source_id) if str(source_id).isdigit() else source_id
            parents.append(parent)

        parents.sort(key=lambda item: item["parent_score"], reverse=True)
        return parents[: max(1, int(limit))]

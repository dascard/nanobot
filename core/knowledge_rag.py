"""Knowledge Library RAG 查询服务。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from core.database import KnowledgeChunk, KnowledgeDocument, SemanticIndexItem
from core.semantic.reranker import SemanticCandidate
from core.semantic.retriever import (
    fts_rowids_for_query,
    lexical_overlap_score,
    load_recall_rows,
    semantic_score_for_row,
)
from core.semantic.scoring import passes_relevance_gate, weighted_score


TRUST_ORDER = {"low": 1, "medium": 2, "high": 3}
TRUST_SCORE = {"low": 0.35, "medium": 0.70, "high": 1.0}
RELEVANCE_WEIGHTS = {"reranker": 0.70, "semantic": 0.20, "lexical": 0.10}
FINAL_WEIGHTS = {
    "relevance": 0.78,
    "trust": 0.10,
    "source_prior": 0.08,
    "recency": 0.04,
}


@dataclass
class _KnowledgeCandidate:
    row: SemanticIndexItem
    citation: dict[str, Any]
    document: KnowledgeDocument | None
    lexical: float | None
    semantic: float | None
    reranker: float | None = None
    raw_reranker: float | None = None

    @property
    def candidate_id(self) -> str:
        return f"{self.row.source_type}:{self.row.source_id}:{self.row.source_sub_id}"


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


def _trust_level(value: str | None) -> str:
    text = str(value or "medium").lower()
    return text if text in TRUST_ORDER else "medium"


def _passes_trust(value: str, minimum: str) -> bool:
    minimum = _trust_level(minimum)
    return TRUST_ORDER[_trust_level(value)] >= TRUST_ORDER[minimum]


def _has_valid_citation(citation: dict[str, Any]) -> bool:
    if not isinstance(citation, dict) or not citation:
        return False
    if str(citation.get("url") or "").strip():
        return bool(str(citation.get("title") or "").strip() and str(citation.get("trust_level") or "").strip())
    return all(str(citation.get(key) or "").strip() for key in ("document_id", "chunk_id", "title", "trust_level"))


def _safe_int(value: str) -> int | None:
    try:
        return int(str(value or "").strip())
    except Exception:
        return None


class KnowledgeRagService:
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

    def query(
        self,
        query: str,
        *,
        limit: int = 5,
        min_trust_level: str = "low",
        published_after: str = "",
        published_before: str = "",
    ) -> dict[str, Any]:
        rowids = fts_rowids_for_query(self.db, query)
        rows = load_recall_rows(self.db, source_types={"knowledge"}, limit=600)
        documents = self._load_documents(rows)
        query_vector = _query_vector(query, self.embedding_provider)
        candidates: list[_KnowledgeCandidate] = []
        skipped_no_citation = 0
        skipped_filter = 0
        for row in rows:
            meta = _safe_json(row.meta_json)
            citation = meta.get("citation") if isinstance(meta.get("citation"), dict) else {}
            if not _has_valid_citation(citation):
                skipped_no_citation += 1
                continue
            document = documents.get(_safe_int(row.source_id) or -1)
            if document is not None and str(document.status or "active") != "active":
                skipped_filter += 1
                continue
            if not self._passes_filters(row, citation, document, min_trust_level, published_after, published_before):
                skipped_filter += 1
                continue
            lexical = 1.0 if row.id in rowids else lexical_overlap_score(query, row.lexical_text or row.text or "")
            semantic = semantic_score_for_row(
                row,
                query_vector=query_vector,
                embedding_provider=self.embedding_provider,
            )
            if lexical > 0 or (semantic is not None and semantic >= 0.10):
                candidates.append(_KnowledgeCandidate(
                    row=row,
                    citation=citation,
                    document=document,
                    lexical=lexical,
                    semantic=semantic,
                ))

        candidates.sort(key=self._pre_score, reverse=True)
        candidates = candidates[:100]
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
        items = [self._result_item(item) for item in ranked[: max(1, int(limit))]]
        return {
            "query": query,
            "source": "knowledge",
            "degraded": degraded,
            "fallback_reason": "reranker_unavailable" if degraded else "",
            "stats": {
                "fts_candidates": len(rowids),
                "merged_candidates": len(candidates),
                "reranker_candidates": len(candidates[:100]) if self.reranker_provider else 0,
                "final_items": len(items),
                "skipped_no_citation": skipped_no_citation,
                "skipped_filter": skipped_filter,
            },
            "items": items,
        }

    def expand(self, *, document_id: int | str, chunk_id: str, max_chars: int = 1200) -> dict[str, Any]:
        row = (
            self.db.query(KnowledgeChunk)
            .filter(KnowledgeChunk.document_id == int(document_id))
            .filter(KnowledgeChunk.chunk_id == str(chunk_id))
            .filter(KnowledgeChunk.status == "active")
            .first()
        )
        if row is None:
            raise ValueError("knowledge chunk not found")
        citation = _safe_json(row.citation_json)
        text = str(row.text or "")[: max(1, int(max_chars))]
        return {
            "document_id": int(row.document_id),
            "chunk_id": row.chunk_id,
            "title": row.title or citation.get("title") or "",
            "text": text,
            "citation": citation,
            "trust_level": row.trust_level or citation.get("trust_level") or "medium",
        }

    def _load_documents(self, rows: list[SemanticIndexItem]) -> dict[int, KnowledgeDocument]:
        ids = sorted({doc_id for row in rows if (doc_id := _safe_int(row.source_id)) is not None})
        if not ids:
            return {}
        return {
            int(row.id): row
            for row in self.db.query(KnowledgeDocument).filter(KnowledgeDocument.id.in_(ids)).all()
        }

    def _passes_filters(
        self,
        row: SemanticIndexItem,
        citation: dict[str, Any],
        document: KnowledgeDocument | None,
        min_trust_level: str,
        published_after: str,
        published_before: str,
    ) -> bool:
        trust = _trust_level(
            (document.trust_level if document is not None else "")
            or citation.get("trust_level")
            or row.trust_level
        )
        if not _passes_trust(trust, min_trust_level):
            return False
        published_at = str(
            (document.published_at if document is not None else "")
            or citation.get("published_at")
            or ""
        )
        if published_after and published_at and published_at < str(published_after):
            return False
        if published_before and published_at and published_at > str(published_before):
            return False
        return True

    def _pre_score(self, item: _KnowledgeCandidate) -> float:
        return weighted_score(
            {"semantic": item.semantic, "lexical": item.lexical},
            {"semantic": 0.65, "lexical": 0.35},
        )

    def _apply_reranker(self, query: str, candidates: list[_KnowledgeCandidate]) -> None:
        if self.reranker_provider is None or not candidates:
            return
        rerank_inputs = [
            SemanticCandidate(
                candidate_id=item.candidate_id,
                source_type="knowledge",
                title=item.row.title or "",
                text=item.row.embedding_text or item.row.text or "",
                metadata=item.citation,
            )
            for item in candidates
        ]
        reranked = self.reranker_provider.rerank(query, rerank_inputs, top_k=100)
        scores = {item.candidate_id: item for item in reranked}
        for item in candidates:
            score = scores.get(item.candidate_id)
            if score is not None:
                item.reranker = score.score
                item.raw_reranker = score.raw_score

    def _final_score(self, item: _KnowledgeCandidate) -> float:
        relevance = weighted_score(
            {"reranker": item.reranker, "semantic": item.semantic, "lexical": item.lexical},
            RELEVANCE_WEIGHTS,
        )
        trust = _trust_level(
            (item.document.trust_level if item.document is not None else "")
            or item.citation.get("trust_level")
            or item.row.trust_level
        )
        return weighted_score(
            {
                "relevance": relevance,
                "trust": TRUST_SCORE[trust],
                "source_prior": item.row.source_prior or 0.0,
                "recency": 0.5,
            },
            FINAL_WEIGHTS,
        )

    def _result_item(self, item: _KnowledgeCandidate) -> dict[str, Any]:
        final = self._final_score(item)
        document_id = _safe_int(item.row.source_id)
        return {
            "candidate_id": item.candidate_id,
            "document_id": document_id if document_id is not None else item.row.source_id,
            "chunk_id": item.row.source_sub_id,
            "title": item.row.title,
            "text": item.row.text,
            "citation": item.citation,
            "trust_level": _trust_level(item.citation.get("trust_level") or item.row.trust_level),
            "score": final,
            "score_breakdown": {
                "lexical": item.lexical,
                "semantic": item.semantic,
                "reranker": item.reranker,
                "raw_reranker": item.raw_reranker,
                "trust": TRUST_SCORE[_trust_level(item.citation.get("trust_level") or item.row.trust_level)],
                "final": final,
            },
        }

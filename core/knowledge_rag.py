"""Knowledge Library RAG 查询服务。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from core.database import KnowledgeChunk, KnowledgeDocument, SemanticIndexItem
from core.semantic.reranker import SemanticCandidate
from core.semantic.retriever import (
    fts_recall_hits,
    lexical_overlap_score,
    load_recall_rows,
    load_recall_rows_by_ids,
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
        source_type: str = "",
        domain: str = "",
        date_start: str = "",
        date_end: str = "",
        published_after: str = "",
        published_before: str = "",
        include_debug: bool = False,
    ) -> dict[str, Any]:
        published_after = str(published_after or date_start or "")
        published_before = str(published_before or date_end or "")
        fts_hits = fts_recall_hits(self.db, query, source_types={"knowledge"}, limit=300)
        lexical_by_id = {hit.item_id: hit.lexical_score for hit in fts_hits}
        bm25_by_id = {hit.item_id: hit.bm25_raw for hit in fts_hits}
        rows = load_recall_rows(self.db, source_types={"knowledge"}, limit=600)
        rows_by_id = {int(row.id): row for row in rows}
        missing_fts_ids = [hit.item_id for hit in fts_hits if hit.item_id not in rows_by_id]
        rows_by_id.update(load_recall_rows_by_ids(self.db, missing_fts_ids))
        fts_ordered = [rows_by_id[hit.item_id] for hit in fts_hits if hit.item_id in rows_by_id]
        fts_ids = {int(row.id) for row in fts_ordered}
        rows = fts_ordered + [row for row in rows if int(row.id) not in fts_ids]
        documents = self._load_documents(rows)
        debug_stages: dict[str, Any] | None = None
        if include_debug:
            debug_stages = {
                "sql_filters": {
                    "source_types": ["knowledge"],
                    "status": "active",
                    "visibility": "recall",
                    "min_trust_level": min_trust_level,
                    "source_type": source_type,
                    "domain": domain,
                    "published_after": published_after,
                    "published_before": published_before,
                    "citation_required": True,
                },
                "fts_hits": [
                    {
                        "item_id": hit.item_id,
                        "bm25_raw": hit.bm25_raw,
                        "lexical_score": hit.lexical_score,
                        "candidate_id": self._row_candidate_id(rows_by_id.get(hit.item_id)),
                    }
                    for hit in fts_hits
                    if hit.item_id in rows_by_id
                ],
                "embedding_hits": [],
                "merged_candidates": [],
                "reranker_input_pairs": [],
                "final_candidates": [],
                "relevance_gate": [],
                "skipped": {"no_citation": 0, "filter": 0},
            }
        query_vector = _query_vector(query, self.embedding_provider)
        candidates: list[_KnowledgeCandidate] = []
        skipped_no_citation = 0
        skipped_filter = 0
        semantic_hits = 0
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
            if not self._passes_filters(
                row,
                citation,
                document,
                min_trust_level,
                published_after,
                published_before,
                source_type,
                domain,
            ):
                skipped_filter += 1
                continue
            lexical = lexical_by_id.get(int(row.id))
            if lexical is None:
                lexical = lexical_overlap_score(query, row.lexical_text or row.text or "")
            semantic = semantic_score_for_row(
                row,
                query_vector=query_vector,
                embedding_provider=self.embedding_provider,
            )
            if semantic is not None and semantic > 0:
                semantic_hits += 1
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
        if debug_stages is not None:
            debug_stages["embedding_hits"] = [
                self._debug_candidate(item, bm25_by_id=bm25_by_id)
                for item in candidates
                if item.semantic is not None and item.semantic > 0
            ]
            debug_stages["merged_candidates"] = [
                self._debug_candidate(item, bm25_by_id=bm25_by_id)
                for item in candidates
            ]
            debug_stages["skipped"] = {
                "no_citation": skipped_no_citation,
                "filter": skipped_filter,
            }
        rerank_inputs = self._apply_reranker(query, candidates)
        if debug_stages is not None:
            debug_stages["reranker_input_pairs"] = [
                {
                    "candidate_id": candidate.candidate_id,
                    "source_type": candidate.source_type,
                    "query": query,
                    "title": candidate.title,
                    "text": candidate.text,
                    "metadata": candidate.metadata,
                }
                for candidate in rerank_inputs
            ]
        degraded = self.reranker_provider is None
        gated: list[_KnowledgeCandidate] = []
        gate_debug: list[dict[str, Any]] = []
        for item in candidates:
            passed = passes_relevance_gate(
                {"reranker": item.reranker, "semantic": item.semantic, "lexical": item.lexical},
                degraded=degraded,
                min_reranker=self.min_reranker,
            )
            if passed:
                gated.append(item)
            if debug_stages is not None:
                gate_debug.append({
                    "candidate_id": item.candidate_id,
                    "passed": passed,
                    "degraded": degraded,
                    "components": {
                        "reranker": item.reranker,
                        "semantic": item.semantic,
                        "lexical": item.lexical,
                    },
                })
        ranked = sorted(gated, key=self._final_score, reverse=True)
        items = [self._result_item(item) for item in ranked[: max(1, int(limit))]]
        if debug_stages is not None:
            debug_stages["merged_candidates"] = [
                self._debug_candidate(item, bm25_by_id=bm25_by_id)
                for item in candidates
            ]
            debug_stages["final_candidates"] = [
                self._debug_candidate(item, bm25_by_id=bm25_by_id)
                for item in ranked[: max(1, int(limit))]
            ]
            debug_stages["relevance_gate"] = gate_debug
        result = {
            "query": query,
            "source": "knowledge",
            "degraded": degraded,
            "fallback_reason": "reranker_unavailable" if degraded else "",
            "stats": {
                "fts_candidates": len(fts_hits),
                "embedding_candidates": semantic_hits,
                "merged_candidates": len(candidates),
                "reranker_candidates": len(candidates[:100]) if self.reranker_provider else 0,
                "final_items": len(items),
                "skipped_no_citation": skipped_no_citation,
                "skipped_filter": skipped_filter,
            },
            "items": items,
        }
        if debug_stages is not None:
            result["debug_trace"] = debug_stages
        return result

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
        source_type: str,
        domain: str,
    ) -> bool:
        meta = _safe_json(row.meta_json)
        trust = _trust_level(
            (document.trust_level if document is not None else "")
            or citation.get("trust_level")
            or row.trust_level
        )
        if not _passes_trust(trust, min_trust_level):
            return False
        wanted_source_type = str(source_type or "").strip()
        if wanted_source_type:
            actual_source_type = str(
                (document.document_kind if document is not None else "")
                or citation.get("document_kind")
                or meta.get("document_kind")
                or ""
            ).strip()
            if actual_source_type != wanted_source_type:
                return False
        wanted_domain = str(domain or "").strip()
        if wanted_domain:
            actual_domain = str(
                (document.domain if document is not None else "")
                or citation.get("domain")
                or meta.get("domain")
                or ""
            ).strip()
            if actual_domain != wanted_domain:
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

    @staticmethod
    def _row_candidate_id(row: SemanticIndexItem | None) -> str:
        if row is None:
            return ""
        return f"{row.source_type}:{row.source_id}:{row.source_sub_id}"

    def _apply_reranker(self, query: str, candidates: list[_KnowledgeCandidate]) -> list[SemanticCandidate]:
        if self.reranker_provider is None or not candidates:
            return []
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
        return rerank_inputs

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

    def _debug_candidate(self, item: _KnowledgeCandidate, *, bm25_by_id: dict[int, float]) -> dict[str, Any]:
        result = self._result_item(item)
        return {
            "candidate_id": item.candidate_id,
            "item_id": item.row.id,
            "source_type": "knowledge",
            "document_id": result.get("document_id"),
            "chunk_id": result.get("chunk_id"),
            "title": result.get("title") or "",
            "text": result.get("text") or "",
            "citation": result.get("citation") or {},
            "trust_level": result.get("trust_level") or "",
            "bm25_raw": bm25_by_id.get(int(item.row.id)),
            "lexical_score": item.lexical,
            "semantic_score": item.semantic,
            "reranker_score": item.reranker,
            "raw_reranker": item.raw_reranker,
            "final_score": result.get("score"),
            "score_breakdown": result.get("score_breakdown") | {
                "bm25_raw": bm25_by_id.get(int(item.row.id)),
            },
            "skipped_reason": "",
        }

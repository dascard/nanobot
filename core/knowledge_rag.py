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
    has_vector_recall_rows,
    lexical_overlap_score,
    load_recall_rows,
    load_recall_rows_by_ids,
    semantic_score_for_row,
    vector_recall_hits,
)
from core.semantic.scoring import passes_relevance_gate, recency_score, weighted_score


TRUST_ORDER = {"low": 1, "medium": 2, "high": 3}
TRUST_SCORE = {"low": 0.35, "medium": 0.70, "high": 1.0}
RELEVANCE_WEIGHTS = {"reranker": 0.70, "semantic": 0.20, "lexical": 0.10}
FINAL_WEIGHTS = {
    "relevance": 0.78,
    "trust": 0.10,
    "source_prior": 0.08,
    "recency": 0.04,
}
RECENCY_HALF_LIFE_DAYS = 180.0


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


@dataclass
class _KnowledgeRecallResult:
    rows: list[SemanticIndexItem]
    rows_by_id: dict[int, SemanticIndexItem]
    fts_hits: list[Any]
    vector_hits: list[Any]
    lexical_by_id: dict[int, float]
    bm25_by_id: dict[int, float]
    semantic_by_id: dict[int, float]
    query_vector: list[float] | None


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


def _merge_recall_rows(*groups: list[SemanticIndexItem]) -> list[SemanticIndexItem]:
    rows: list[SemanticIndexItem] = []
    seen: set[int] = set()
    for group in groups:
        for row in group:
            row_id = int(row.id)
            if row_id in seen:
                continue
            seen.add(row_id)
            rows.append(row)
    return rows


class KnowledgeRagService:
    def __init__(
        self,
        db: Session,
        *,
        embedding_provider: Any = None,
        reranker_provider: Any = None,
        min_reranker: float = 0.45,
        readonly: bool = False,
    ):
        self.db = db
        self.embedding_provider = embedding_provider
        self.reranker_provider = reranker_provider
        self.min_reranker = float(min_reranker)
        self.readonly = bool(readonly)

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
        recall = self._recall(query)
        documents = self._load_documents(recall.rows)
        debug_trace = (
            self._build_debug_trace(
                recall=recall,
                min_trust_level=min_trust_level,
                source_type=source_type,
                domain=domain,
                published_after=published_after,
                published_before=published_before,
            )
            if include_debug
            else None
        )
        candidates, skipped = self._filter_candidates(
            query,
            recall=recall,
            documents=documents,
            min_trust_level=min_trust_level,
            published_after=published_after,
            published_before=published_before,
            source_type=source_type,
            domain=domain,
        )
        self._update_candidate_debug(
            debug_trace,
            candidates=candidates,
            bm25_by_id=recall.bm25_by_id,
            skipped=skipped,
        )
        self._rerank(query, candidates, debug_trace)
        degraded = self.reranker_provider is None
        gated = self._apply_relevance_gate(candidates, degraded=degraded, debug_trace=debug_trace)
        ranked = sorted(gated, key=self._final_score, reverse=True)
        return self._build_result(
            query,
            ranked=ranked,
            candidates=candidates,
            recall=recall,
            skipped=skipped,
            limit=limit,
            degraded=degraded,
            debug_trace=debug_trace,
        )

    def _recall(self, query: str) -> _KnowledgeRecallResult:
        has_vector_rows = has_vector_recall_rows(
            self.db,
            source_types={"knowledge"},
            ensure_schema=not self.readonly,
        )
        query_vector = _query_vector(query, self.embedding_provider) if has_vector_rows else None
        fts_hits = fts_recall_hits(
            self.db,
            query,
            source_types={"knowledge"},
            limit=300,
            ensure_schema=not self.readonly,
        )
        lexical_by_id = {hit.item_id: hit.lexical_score for hit in fts_hits}
        bm25_by_id = {hit.item_id: hit.bm25_raw for hit in fts_hits}
        vector_hits = vector_recall_hits(
            self.db,
            query_vector=query_vector,
            source_types={"knowledge"},
            limit=300,
            ensure_schema=not self.readonly,
        )
        semantic_by_id = {hit.item_id: hit.semantic_score for hit in vector_hits}
        recent_rows = load_recall_rows(
            self.db,
            source_types={"knowledge"},
            limit=600,
            ensure_schema=not self.readonly,
        )
        rows_by_id = {int(row.id): row for row in recent_rows}
        recall_ids = [hit.item_id for hit in fts_hits] + [hit.item_id for hit in vector_hits]
        missing_ids = [item_id for item_id in recall_ids if item_id not in rows_by_id]
        rows_by_id.update(load_recall_rows_by_ids(
            self.db,
            missing_ids,
            ensure_schema=not self.readonly,
        ))
        fts_ordered = [rows_by_id[hit.item_id] for hit in fts_hits if hit.item_id in rows_by_id]
        vector_ordered = [rows_by_id[hit.item_id] for hit in vector_hits if hit.item_id in rows_by_id]
        rows = _merge_recall_rows(fts_ordered, vector_ordered, recent_rows)
        return _KnowledgeRecallResult(
            rows=rows,
            rows_by_id=rows_by_id,
            fts_hits=fts_hits,
            vector_hits=vector_hits,
            lexical_by_id=lexical_by_id,
            bm25_by_id=bm25_by_id,
            semantic_by_id=semantic_by_id,
            query_vector=query_vector,
        )

    def _build_debug_trace(
        self,
        *,
        recall: _KnowledgeRecallResult,
        min_trust_level: str,
        source_type: str,
        domain: str,
        published_after: str,
        published_before: str,
    ) -> dict[str, Any]:
        return {
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
                    "candidate_id": self._row_candidate_id(recall.rows_by_id.get(hit.item_id)),
                }
                for hit in recall.fts_hits
                if hit.item_id in recall.rows_by_id
            ],
            "vector_hits": [
                {
                    "item_id": hit.item_id,
                    "semantic_score": hit.semantic_score,
                    "candidate_id": self._row_candidate_id(recall.rows_by_id.get(hit.item_id)),
                }
                for hit in recall.vector_hits
                if hit.item_id in recall.rows_by_id
            ],
            "embedding_hits": [],
            "merged_candidates": [],
            "reranker_input_pairs": [],
            "final_candidates": [],
            "relevance_gate": [],
            "skipped": {"no_citation": 0, "filter": 0},
        }

    def _filter_candidates(
        self,
        query: str,
        *,
        recall: _KnowledgeRecallResult,
        documents: dict[int, KnowledgeDocument],
        min_trust_level: str,
        published_after: str,
        published_before: str,
        source_type: str,
        domain: str,
    ) -> tuple[list[_KnowledgeCandidate], dict[str, int]]:
        candidates: list[_KnowledgeCandidate] = []
        skipped_no_citation = 0
        skipped_filter = 0
        semantic_hits = 0
        for row in recall.rows:
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
            lexical = recall.lexical_by_id.get(int(row.id))
            if lexical is None:
                lexical = lexical_overlap_score(query, row.lexical_text or row.text or "")
            semantic = recall.semantic_by_id.get(int(row.id))
            if semantic is None:
                semantic = semantic_score_for_row(row, query_vector=recall.query_vector, embedding_provider=None)
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
        return candidates[:100], {
            "skipped_no_citation": skipped_no_citation,
            "skipped_filter": skipped_filter,
            "semantic_hits": semantic_hits,
        }

    def _update_candidate_debug(
        self,
        debug_trace: dict[str, Any] | None,
        *,
        candidates: list[_KnowledgeCandidate],
        bm25_by_id: dict[int, float],
        skipped: dict[str, int],
    ) -> None:
        if debug_trace is None:
            return
        debug_trace["embedding_hits"] = [
            self._debug_candidate(item, bm25_by_id=bm25_by_id)
            for item in candidates
            if item.semantic is not None and item.semantic > 0
        ]
        debug_trace["merged_candidates"] = [
            self._debug_candidate(item, bm25_by_id=bm25_by_id)
            for item in candidates
        ]
        debug_trace["skipped"] = {
            "no_citation": skipped["skipped_no_citation"],
            "filter": skipped["skipped_filter"],
        }

    def _rerank(
        self,
        query: str,
        candidates: list[_KnowledgeCandidate],
        debug_trace: dict[str, Any] | None,
    ) -> None:
        rerank_inputs = self._apply_reranker(query, candidates)
        if debug_trace is not None:
            debug_trace["reranker_input_pairs"] = [
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

    def _apply_relevance_gate(
        self,
        candidates: list[_KnowledgeCandidate],
        *,
        degraded: bool,
        debug_trace: dict[str, Any] | None,
    ) -> list[_KnowledgeCandidate]:
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
            if debug_trace is not None:
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
        if debug_trace is not None:
            debug_trace["relevance_gate"] = gate_debug
        return gated

    def _build_result(
        self,
        query: str,
        *,
        ranked: list[_KnowledgeCandidate],
        candidates: list[_KnowledgeCandidate],
        recall: _KnowledgeRecallResult,
        skipped: dict[str, int],
        limit: int,
        degraded: bool,
        debug_trace: dict[str, Any] | None,
    ) -> dict[str, Any]:
        capped_limit = max(1, int(limit))
        items = [self._result_item(item) for item in ranked[:capped_limit]]
        if debug_trace is not None:
            debug_trace["merged_candidates"] = [
                self._debug_candidate(item, bm25_by_id=recall.bm25_by_id)
                for item in candidates
            ]
            debug_trace["final_candidates"] = [
                self._debug_candidate(item, bm25_by_id=recall.bm25_by_id)
                for item in ranked[:capped_limit]
            ]
        result = {
            "query": query,
            "source": "knowledge",
            "degraded": degraded,
            "fallback_reason": "reranker_unavailable" if degraded else "",
            "stats": {
                "fts_candidates": len(recall.fts_hits),
                "vector_candidates": len(recall.vector_hits),
                "embedding_candidates": skipped["semantic_hits"],
                "merged_candidates": len(candidates),
                "reranker_candidates": len(candidates[:100]) if self.reranker_provider else 0,
                "final_items": len(items),
                "skipped_no_citation": skipped["skipped_no_citation"],
                "skipped_filter": skipped["skipped_filter"],
            },
            "items": items,
        }
        if debug_trace is not None:
            result["debug_trace"] = debug_trace
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
        if (published_after or published_before) and not published_at:
            return False
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

    def _recency_score(self, item: _KnowledgeCandidate) -> float:
        event_at = None
        if item.document is not None:
            event_at = item.document.latest_seen or item.document.updated_at
        event_at = event_at or item.row.source_updated_at or item.row.updated_at or item.row.indexed_at
        return recency_score(event_at, half_life_days=RECENCY_HALF_LIFE_DAYS)

    def _final_score(self, item: _KnowledgeCandidate, *, recency: float | None = None) -> float:
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
                "recency": recency if recency is not None else self._recency_score(item),
            },
            FINAL_WEIGHTS,
        )

    def _result_item(self, item: _KnowledgeCandidate) -> dict[str, Any]:
        recency = self._recency_score(item)
        final = self._final_score(item, recency=recency)
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
                "recency": recency,
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

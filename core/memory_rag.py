"""Memory RAG 查询服务。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from core.database import SemanticIndexItem
from core.semantic.adapters import is_recallable_memory_digest_meta
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


RELEVANCE_WEIGHTS = {"reranker": 0.70, "semantic": 0.20, "lexical": 0.10}
MIN_FALLBACK_RERANK_LEXICAL = 0.50
FINAL_WEIGHTS = {
    "relevance": 0.75,
    "quality": 0.08,
    "trust": 0.07,
    "recency": 0.05,
    "source_prior": 0.05,
}
RECENCY_HALF_LIFE_DAYS = 60.0


@dataclass
class _Candidate:
    row: SemanticIndexItem
    lexical: float | None
    semantic: float | None
    reranker: float | None = None
    raw_reranker: float | None = None
    reranker_skip_reason: str = ""

    @property
    def candidate_id(self) -> str:
        return f"{self.row.source_type}:{self.row.source_id}:{self.row.source_sub_id}"


@dataclass
class _MemoryRecallResult:
    rows: list[SemanticIndexItem]
    rows_by_id: dict[int, SemanticIndexItem]
    fts_hits: list[Any]
    vector_hits: list[Any]
    lexical_by_id: dict[int, float]
    bm25_by_id: dict[int, float]
    semantic_by_id: dict[int, float]
    query_vector: list[float] | None


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


def _collect_digest_row_ids(items: list[_Candidate]) -> list[int]:
    """从候选集的 meta_json 中收集去重的 digest_row_id。"""
    row_ids: list[int] = []
    seen: set[int] = set()
    for item in items:
        meta = _safe_json(item.row.meta_json)
        rid = meta.get("digest_row_id")
        if isinstance(rid, int) and rid > 0 and rid not in seen:
            seen.add(rid)
            row_ids.append(rid)
    return row_ids


def _collect_summary_document_ids(items: list[_Candidate]) -> list[int]:
    document_ids: list[int] = []
    seen: set[int] = set()
    for item in items:
        document_id = _safe_json(item.row.meta_json).get("document_id")
        try:
            normalized = int(document_id or 0)
        except (TypeError, ValueError):
            continue
        if normalized > 0 and normalized not in seen:
            seen.add(normalized)
            document_ids.append(normalized)
    return document_ids


def _query_vector(query: str, embedding_provider: Any) -> list[float] | None:
    if embedding_provider is None:
        return None
    try:
        return [float(item) for item in embedding_provider.embed([query])[0]]
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


class MemoryRagService:
    def __init__(
        self,
        db: Session,
        *,
        embedding_provider: Any = None,
        reranker_provider: Any = None,
        allow_degraded: bool = True,
        readonly: bool = False,
    ):
        self.db = db
        self.embedding_provider = embedding_provider
        self.reranker_provider = reranker_provider
        self.allow_degraded = allow_degraded
        self.readonly = bool(readonly)

    def query(
        self,
        query: str,
        *,
        source: str = "all",
        user_id: str = "",
        session_id: str = "",
        limit: int = 5,
        include_debug: bool = False,
    ) -> dict[str, Any]:
        source_types = _source_types(source)
        recall = self._recall(
            query,
            source_types=source_types,
            user_id=user_id,
            session_id=session_id,
        )
        debug_trace = (
            self._build_debug_trace(
                recall=recall,
                source_types=source_types,
                user_id=user_id,
                session_id=session_id,
            )
            if include_debug
            else None
        )
        candidates, counters = self._filter_candidates(query, recall=recall)
        self._update_candidate_debug(
            debug_trace,
            candidates=candidates,
            bm25_by_id=recall.bm25_by_id,
        )
        degraded = self.reranker_provider is None
        rerank_candidates = self._prepare_rerank_candidates(
            candidates,
            bm25_by_id=recall.bm25_by_id,
        )
        reranker_latency_ms = self._rerank(
            query,
            candidates=candidates,
            rerank_candidates=rerank_candidates,
            debug_trace=debug_trace,
        )
        gated = self._apply_relevance_gate(candidates, degraded=degraded, debug_trace=debug_trace)
        parent_items = self._group_by_parent(gated, limit=limit)
        return self._build_result(
            query,
            source=source,
            parent_items=parent_items,
            candidates=candidates,
            recall=recall,
            rerank_candidates=rerank_candidates,
            reranker_latency_ms=reranker_latency_ms,
            counters=counters,
            degraded=degraded,
            debug_trace=debug_trace,
        )

    def _recall(
        self,
        query: str,
        *,
        source_types: set[str],
        user_id: str,
        session_id: str,
    ) -> _MemoryRecallResult:
        has_vector_rows = has_vector_recall_rows(
            self.db,
            source_types=source_types,
            user_id=user_id,
            session_id=session_id,
            ensure_schema=not self.readonly,
        )
        query_vector = _query_vector(query, self.embedding_provider) if has_vector_rows else None
        fts_hits = fts_recall_hits(
            self.db,
            query,
            source_types=source_types,
            user_id=user_id,
            session_id=session_id,
            limit=200,
            ensure_schema=not self.readonly,
        )
        lexical_by_id = {hit.item_id: hit.lexical_score for hit in fts_hits}
        bm25_by_id = {hit.item_id: hit.bm25_raw for hit in fts_hits}
        vector_hits = vector_recall_hits(
            self.db,
            query_vector=query_vector,
            source_types=source_types,
            user_id=user_id,
            session_id=session_id,
            limit=200,
            ensure_schema=not self.readonly,
        )
        semantic_by_id = {hit.item_id: hit.semantic_score for hit in vector_hits}
        recent_rows = load_recall_rows(
            self.db,
            source_types=source_types,
            user_id=user_id,
            session_id=session_id,
            limit=400,
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
        return _MemoryRecallResult(
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
        recall: _MemoryRecallResult,
        source_types: set[str],
        user_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        return {
            "sql_filters": {
                "source_types": sorted(source_types),
                "user_id": user_id,
                "session_id": session_id,
                "status": "active",
                "visibility": "recall",
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
        }

    def _filter_candidates(
        self,
        query: str,
        *,
        recall: _MemoryRecallResult,
    ) -> tuple[list[_Candidate], dict[str, int]]:
        candidates: list[_Candidate] = []
        fts_candidate_count = 0
        semantic_hits = 0
        for row in recall.rows:
            if (
                row.source_type == "memory_digest"
                and not is_recallable_memory_digest_meta(_safe_json(row.meta_json))
            ):
                continue
            lexical = recall.lexical_by_id.get(int(row.id))
            if lexical is None:
                lexical = lexical_overlap_score(query, row.lexical_text or row.text or "")
            if lexical > 0:
                fts_candidate_count += 1
            semantic = recall.semantic_by_id.get(int(row.id))
            if semantic is None:
                semantic = semantic_score_for_row(row, query_vector=recall.query_vector, embedding_provider=None)
            if semantic is not None and semantic > 0:
                semantic_hits += 1
            if lexical > 0 or (semantic is not None and semantic >= 0.10):
                candidates.append(_Candidate(row=row, lexical=lexical, semantic=semantic))

        candidates.sort(key=lambda item: self._pre_score(item), reverse=True)
        return candidates[:80], {
            "fts_candidate_count": fts_candidate_count,
            "semantic_hits": semantic_hits,
        }

    def _update_candidate_debug(
        self,
        debug_trace: dict[str, Any] | None,
        *,
        candidates: list[_Candidate],
        bm25_by_id: dict[int, float],
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

    def _prepare_rerank_candidates(
        self,
        candidates: list[_Candidate],
        *,
        bm25_by_id: dict[int, float],
    ) -> list[_Candidate]:
        rerank_candidates: list[_Candidate] = []
        if self.reranker_provider is None:
            return rerank_candidates
        for item in candidates:
            item.reranker_skip_reason = self._reranker_skip_reason(item, bm25_by_id=bm25_by_id)
            if not item.reranker_skip_reason:
                rerank_candidates.append(item)
        if len(rerank_candidates) > 50:
            for item in rerank_candidates[50:]:
                item.reranker_skip_reason = "reranker_budget"
            rerank_candidates = rerank_candidates[:50]
        return rerank_candidates

    def _rerank(
        self,
        query: str,
        *,
        candidates: list[_Candidate],
        rerank_candidates: list[_Candidate],
        debug_trace: dict[str, Any] | None,
    ) -> int:
        if self.reranker_provider is None or not rerank_candidates:
            return 0
        rerank_inputs = [
            SemanticCandidate(
                candidate_id=item.candidate_id,
                source_type=item.row.source_type,
                title=item.row.title or "",
                text=item.row.embedding_text or item.row.text or "",
                metadata=_safe_json(item.row.meta_json),
            )
            for item in rerank_candidates
        ]
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
        reranker_started = time.perf_counter()
        reranked = self.reranker_provider.rerank(query, rerank_inputs, top_k=50)
        reranker_latency_ms = int((time.perf_counter() - reranker_started) * 1000)
        if debug_trace is not None:
            debug_trace.setdefault("timings", {})["reranker_latency_ms"] = reranker_latency_ms
        scores = {item.candidate_id: item for item in reranked}
        for item in candidates:
            score = scores.get(item.candidate_id)
            if score is not None:
                item.reranker = score.score
                item.raw_reranker = score.raw_score
        return reranker_latency_ms

    def _apply_relevance_gate(
        self,
        candidates: list[_Candidate],
        *,
        degraded: bool,
        debug_trace: dict[str, Any] | None,
    ) -> list[_Candidate]:
        gated: list[_Candidate] = []
        gate_debug: list[dict[str, Any]] = []
        for item in candidates:
            passed = passes_relevance_gate(
                {"reranker": item.reranker, "semantic": item.semantic, "lexical": item.lexical},
                degraded=degraded,
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
        source: str,
        parent_items: list[dict[str, Any]],
        candidates: list[_Candidate],
        recall: _MemoryRecallResult,
        rerank_candidates: list[_Candidate],
        reranker_latency_ms: int,
        counters: dict[str, int],
        degraded: bool,
        debug_trace: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if debug_trace is not None:
            debug_trace["merged_candidates"] = [
                self._debug_candidate(item, bm25_by_id=recall.bm25_by_id)
                for item in candidates
            ]
            final_candidate_ids = {
                card["candidate_id"]
                for parent in parent_items
                for card in parent["matched_cards"]
            }
            debug_trace["final_candidates"] = [
                self._debug_candidate(item, bm25_by_id=recall.bm25_by_id)
                for item in candidates
                if item.candidate_id in final_candidate_ids
            ]
        result = {
            "query": query,
            "source": source,
            "degraded": degraded,
            "fallback_reason": "reranker_unavailable" if degraded else "",
            "stats": {
                "fts_candidates": len(recall.fts_hits),
                "vector_candidates": len(recall.vector_hits),
                "lexical_candidates": counters["fts_candidate_count"],
                "embedding_candidates": counters["semantic_hits"],
                "merged_candidates": len(candidates),
                "reranker_candidates": len(rerank_candidates) if self.reranker_provider else 0,
                "reranker_latency_ms": reranker_latency_ms,
                "final_items": len(parent_items),
            },
            "items": parent_items,
        }
        if debug_trace is not None:
            result["debug_trace"] = debug_trace
        return result

    @staticmethod
    def _row_candidate_id(row: SemanticIndexItem | None) -> str:
        if row is None:
            return ""
        return f"{row.source_type}:{row.source_id}:{row.source_sub_id}"

    def _pre_score(self, item: _Candidate) -> float:
        return weighted_score(
            {"semantic": item.semantic, "lexical": item.lexical},
            {"semantic": 0.65, "lexical": 0.35},
        )

    def _recency_score(self, item: _Candidate) -> float:
        event_at = item.row.source_updated_at or item.row.updated_at or item.row.indexed_at
        return recency_score(event_at, half_life_days=RECENCY_HALF_LIFE_DAYS)

    def _final_score(self, item: _Candidate, *, recency: float | None = None) -> float:
        relevance = weighted_score(
            {"reranker": item.reranker, "semantic": item.semantic, "lexical": item.lexical},
            RELEVANCE_WEIGHTS,
        )
        return weighted_score(
            {
                "relevance": relevance,
                "quality": item.row.quality_score or 0.0,
                "trust": _trust_score(item.row.trust_level),
                "recency": recency if recency is not None else self._recency_score(item),
                "source_prior": item.row.source_prior or 0.0,
            },
            FINAL_WEIGHTS,
        )

    def _reranker_skip_reason(self, item: _Candidate, *, bm25_by_id: dict[int, float]) -> str:
        if int(item.row.id) in bm25_by_id:
            return ""
        if item.semantic is not None and item.semantic >= 0.10:
            return ""
        if item.lexical is not None and item.lexical >= MIN_FALLBACK_RERANK_LEXICAL:
            return ""
        return "weak_lexical_fallback"

    def _card_dict(self, item: _Candidate) -> dict[str, Any]:
        recency = self._recency_score(item)
        final = self._final_score(item, recency=recency)
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
                "recency": recency,
                "final": final,
            },
        }

    def _debug_candidate(self, item: _Candidate, *, bm25_by_id: dict[int, float]) -> dict[str, Any]:
        card = self._card_dict(item)
        return {
            "candidate_id": item.candidate_id,
            "item_id": item.row.id,
            "source_type": item.row.source_type,
            "source_id": item.row.source_id,
            "source_sub_id": item.row.source_sub_id,
            "title": item.row.title,
            "text": item.row.text,
            "bm25_raw": bm25_by_id.get(int(item.row.id)),
            "lexical_score": item.lexical,
            "semantic_score": item.semantic,
            "reranker_score": item.reranker,
            "raw_reranker": item.raw_reranker,
            "final_score": card["final_score"],
            "score_breakdown": card["score_breakdown"] | {
                "raw_reranker": item.raw_reranker,
                "bm25_raw": bm25_by_id.get(int(item.row.id)),
            },
            "skipped_reason": item.reranker_skip_reason,
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
                digest_row_ids = _collect_digest_row_ids(items)
                parent["digest_id"] = digest_row_ids[0] if digest_row_ids else source_id
                parent["digest_source_id"] = source_id
                parent["matched_digest_row_ids"] = digest_row_ids
            if source_type == "session_summary":
                document_ids = _collect_summary_document_ids(items)
                parent["summary_id"] = (
                    document_ids[0]
                    if document_ids
                    else int(source_id) if str(source_id).isdigit() else source_id
                )
            parents.append(parent)

        parents.sort(key=lambda item: item["parent_score"], reverse=True)
        return parents[: max(1, int(limit))]

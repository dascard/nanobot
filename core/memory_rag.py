"""Memory RAG 查询服务。"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from core.database import SemanticIndexItem
from core.memory_governance import MemoryDataScopeFilter
from core.retrieval import (
    AllowAllCitationPolicy,
    CitationEvaluation,
    RankingOutcome,
    RerankOutcome,
    RetrievalCandidates,
    RetrievalPipeline,
    RetrievalPipelineState,
    RetrievalRequest,
)
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


logger = logging.getLogger("nanobot.memory_rag")

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
        scope_filter: MemoryDataScopeFilter | None = None,
    ) -> dict[str, Any]:
        scoped_user_ids = tuple(scope_filter.user_ids) if scope_filter else ()
        scoped_session_ids = (
            tuple(scope_filter.session_ids) if scope_filter else ()
        )
        request = RetrievalRequest(
            query=query,
            limit=max(1, int(limit)),
            include_debug=include_debug,
            options={
                "domain": "memory",
                "source": source,
                "source_types": frozenset(_source_types(source)),
                "user_id": user_id,
                "session_id": session_id,
                "user_ids": scoped_user_ids,
                "session_ids": scoped_session_ids,
            },
        )
        return self._build_pipeline().execute(request)

    def _build_pipeline(self) -> RetrievalPipeline:
        return RetrievalPipeline(
            candidate_source=_MemoryCandidateSource(self),
            citation_policy=AllowAllCitationPolicy(),
            filter_policy=_MemoryFilterPolicy(self),
            scoring_policy=_MemoryScoringPolicy(self),
            reranker=_MemoryReranker(self),
            budget_policy=_MemoryBudgetPolicy(self),
            debug_trace_sink=_MemoryDebugTraceSink(self),
            result_builder=_MemoryResultBuilder(self),
            provider_id="memory_rag",
        )

    def _recall(
        self,
        query: str,
        *,
        source_types: set[str],
        user_id: str,
        session_id: str,
        user_ids: set[str] | None = None,
        session_ids: set[str] | None = None,
    ) -> _MemoryRecallResult:
        has_vector_rows = has_vector_recall_rows(
            self.db,
            source_types=source_types,
            user_id=user_id,
            session_id=session_id,
            user_ids=user_ids,
            session_ids=session_ids,
            ensure_schema=not self.readonly,
        )
        query_vector = _query_vector(query, self.embedding_provider) if has_vector_rows else None
        fts_hits = fts_recall_hits(
            self.db,
            query,
            source_types=source_types,
            user_id=user_id,
            session_id=session_id,
            user_ids=user_ids,
            session_ids=session_ids,
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
            user_ids=user_ids,
            session_ids=session_ids,
            limit=200,
            ensure_schema=not self.readonly,
        )
        semantic_by_id = {hit.item_id: hit.semantic_score for hit in vector_hits}
        recent_rows = load_recall_rows(
            self.db,
            source_types=source_types,
            user_id=user_id,
            session_id=session_id,
            user_ids=user_ids,
            session_ids=session_ids,
            limit=400,
            ensure_schema=not self.readonly,
        )
        rows_by_id = {int(row.id): row for row in recent_rows}
        recall_ids = [hit.item_id for hit in fts_hits] + [hit.item_id for hit in vector_hits]
        missing_ids = [item_id for item_id in recall_ids if item_id not in rows_by_id]
        rows_by_id.update(load_recall_rows_by_ids(
            self.db,
            missing_ids,
            source_types=source_types,
            user_ids=user_ids,
            session_ids=session_ids,
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
        user_ids: set[str] | None = None,
        session_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "sql_filters": {
                "source_types": sorted(source_types),
                "user_id": user_id or sorted(user_ids or set()),
                "session_id": session_id or sorted(session_ids or set()),
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

        return candidates, {
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
    ) -> tuple[int, list[dict[str, Any]]]:
        if self.reranker_provider is None or not rerank_candidates:
            return 0, []
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
        debug_inputs = [
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
        scores = {item.candidate_id: item for item in reranked}
        for item in candidates:
            score = scores.get(item.candidate_id)
            if score is not None:
                item.reranker = score.score
                item.raw_reranker = score.raw_score
        return reranker_latency_ms, debug_inputs

    def _apply_relevance_gate(
        self,
        candidates: list[_Candidate],
        *,
        degraded: bool,
    ) -> tuple[list[_Candidate], list[dict[str, Any]]]:
        gated: list[_Candidate] = []
        gate_debug: list[dict[str, Any]] = []
        for item in candidates:
            passed = passes_relevance_gate(
                {"reranker": item.reranker, "semantic": item.semantic, "lexical": item.lexical},
                degraded=degraded,
            )
            if passed:
                gated.append(item)
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
        return gated, gate_debug

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
        fallback_reason: str = "",
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
            "fallback_reason": (
                (fallback_reason or "reranker_unavailable") if degraded else ""
            ),
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


@dataclass(frozen=True, slots=True)
class _MemoryCandidateSource:
    service: MemoryRagService

    def recall(self, request: RetrievalRequest) -> _MemoryRecallResult:
        return self.service._recall(
            request.query,
            source_types=set(request.options["source_types"]),
            user_id=str(request.options.get("user_id") or ""),
            session_id=str(request.options.get("session_id") or ""),
            user_ids=set(request.options.get("user_ids") or ()),
            session_ids=set(request.options.get("session_ids") or ()),
        )


@dataclass(frozen=True, slots=True)
class _MemoryFilterPolicy:
    service: MemoryRagService

    def filter_candidates(
        self,
        request: RetrievalRequest,
        citation: CitationEvaluation[_MemoryRecallResult],
    ) -> RetrievalCandidates[_Candidate]:
        candidates, counters = self.service._filter_candidates(
            request.query,
            recall=citation.source,
        )
        return RetrievalCandidates(
            items=tuple(candidates),
            metadata={
                **counters,
                "bm25_by_id": citation.source.bm25_by_id,
            },
        )


@dataclass(frozen=True, slots=True)
class _MemoryScoringPolicy:
    service: MemoryRagService

    def pre_rank(
        self,
        request: RetrievalRequest,
        candidates: RetrievalCandidates[_Candidate],
    ) -> RetrievalCandidates[_Candidate]:
        ranked = tuple(
            sorted(candidates.items, key=self.service._pre_score, reverse=True)
        )
        return candidates.with_items(ranked)

    def final_rank(
        self,
        request: RetrievalRequest,
        outcome: RerankOutcome[_Candidate],
    ) -> RankingOutcome[_Candidate]:
        degraded = bool(outcome.metadata["degraded"])
        gated, gate_debug = self.service._apply_relevance_gate(
            list(outcome.items),
            degraded=degraded,
        )
        return RankingOutcome(
            items=tuple(gated),
            metadata={
                "degraded": degraded,
                "relevance_gate": gate_debug,
            },
        )


@dataclass(frozen=True, slots=True)
class _MemoryReranker:
    service: MemoryRagService

    def rerank(
        self,
        request: RetrievalRequest,
        candidates: RetrievalCandidates[_Candidate],
    ) -> RerankOutcome[_Candidate]:
        all_candidates = list(candidates.items)
        bm25_by_id = dict(candidates.metadata["bm25_by_id"])
        degraded = self.service.reranker_provider is None
        fallback_reason = "reranker_unavailable" if degraded else ""
        reranker_candidates: list[_Candidate] = []
        reranker_latency_ms = 0
        debug_inputs: list[dict[str, Any]] = []
        if not degraded:
            reranker_candidates = self.service._prepare_rerank_candidates(
                all_candidates,
                bm25_by_id=bm25_by_id,
            )
            # reranker 服务运行中不可用（HTTP 超时/宕机、本地模型加载失败）
            # 时按运行时降级处理，交由 relevance gate 的 degraded 分支继续
            # 排序；是否允许降级由工具层按 allow_degraded 决定。
            try:
                reranker_latency_ms, debug_inputs = self.service._rerank(
                    request.query,
                    candidates=all_candidates,
                    rerank_candidates=reranker_candidates,
                )
            except Exception:
                logger.warning(
                    "[MemoryRag] reranker 调用失败，按运行时降级继续",
                    exc_info=True,
                )
                degraded = True
                fallback_reason = "reranker_error"
                reranker_candidates = []
                reranker_latency_ms = 0
                debug_inputs = []
        return RerankOutcome(
            items=tuple(all_candidates),
            reranker_items=tuple(reranker_candidates),
            metadata={
                "degraded": degraded,
                "fallback_reason": fallback_reason,
                "reranker_latency_ms": reranker_latency_ms,
                "reranker_input_pairs": debug_inputs,
                "reranker_executed": bool(debug_inputs),
            },
        )


@dataclass(frozen=True, slots=True)
class _MemoryBudgetPolicy:
    service: MemoryRagService

    def limit_candidates(
        self,
        request: RetrievalRequest,
        candidates: RetrievalCandidates[_Candidate],
    ) -> RetrievalCandidates[_Candidate]:
        return candidates.with_items(candidates.items[:80])

    def select_results(
        self,
        request: RetrievalRequest,
        ranking: RankingOutcome[_Candidate],
    ) -> list[dict[str, Any]]:
        return self.service._group_by_parent(
            list(ranking.items),
            limit=request.limit,
        )


@dataclass(frozen=True, slots=True)
class _MemoryDebugTraceSink:
    service: MemoryRagService

    def start(
        self,
        request: RetrievalRequest,
        source: _MemoryRecallResult,
        citation: CitationEvaluation[_MemoryRecallResult],
    ) -> dict[str, Any] | None:
        if not request.include_debug:
            return None
        return self.service._build_debug_trace(
            recall=source,
            source_types=set(request.options["source_types"]),
            user_id=str(request.options.get("user_id") or ""),
            session_id=str(request.options.get("session_id") or ""),
            user_ids=set(request.options.get("user_ids") or ()),
            session_ids=set(request.options.get("session_ids") or ()),
        )

    def candidates_ready(
        self,
        trace: object | None,
        candidates: RetrievalCandidates[_Candidate],
    ) -> None:
        if not isinstance(trace, dict):
            return
        self.service._update_candidate_debug(
            trace,
            candidates=list(candidates.items),
            bm25_by_id=dict(candidates.metadata["bm25_by_id"]),
        )

    def rerank_complete(
        self,
        trace: object | None,
        outcome: RerankOutcome[_Candidate],
    ) -> None:
        if not isinstance(trace, dict):
            return
        trace["reranker_input_pairs"] = list(
            outcome.metadata["reranker_input_pairs"]
        )
        if outcome.metadata["reranker_executed"]:
            trace.setdefault("timings", {})["reranker_latency_ms"] = int(
                outcome.metadata["reranker_latency_ms"]
            )

    def ranking_complete(
        self,
        trace: object | None,
        ranking: RankingOutcome[_Candidate],
    ) -> None:
        if isinstance(trace, dict):
            trace["relevance_gate"] = list(ranking.metadata["relevance_gate"])

    def finish(
        self,
        trace: object | None,
        selected: list[dict[str, Any]],
    ) -> None:
        return None


@dataclass(frozen=True, slots=True)
class _MemoryResultBuilder:
    service: MemoryRagService

    def build(
        self,
        state: RetrievalPipelineState[
            _MemoryRecallResult,
            _Candidate,
            list[dict[str, Any]],
            dict[str, Any],
        ],
    ) -> dict[str, Any]:
        debug_trace = (
            state.debug_trace if isinstance(state.debug_trace, dict) else None
        )
        return self.service._build_result(
            state.request.query,
            source=str(state.request.options.get("source") or "all"),
            parent_items=state.selected,
            candidates=list(state.candidates.items),
            recall=state.source,
            rerank_candidates=list(state.rerank_outcome.reranker_items),
            reranker_latency_ms=int(
                state.rerank_outcome.metadata["reranker_latency_ms"]
            ),
            counters=dict(state.candidates.metadata),
            degraded=bool(state.rerank_outcome.metadata["degraded"]),
            fallback_reason=str(
                state.rerank_outcome.metadata.get("fallback_reason") or ""
            ),
            debug_trace=debug_trace,
        )

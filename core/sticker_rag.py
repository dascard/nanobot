"""Sticker RAG 检索服务。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy.orm import Session

from core.database import SemanticIndexItem, StickerMemory
from core.retrieval import (
    AllowAllCitationPolicy,
    RankingOutcome,
    RerankOutcome,
    RetrievalCandidates,
    RetrievalPipeline,
    RetrievalPipelineState,
    RetrievalRequest,
)
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
RECENCY_HALF_LIFE_DAYS = 30.0
STICKER_QUERY_STOPWORDS = (
    "表情包",
    "表情",
    "贴纸",
    "图片",
    "动图",
    "sticker",
    "emoji",
)
MAX_RERANK_INPUTS = 10


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


@dataclass(frozen=True, slots=True)
class _StickerRecallResult:
    recall_query: str
    query_vector: list[float] | None
    fts_hits: tuple[Any, ...]
    vector_hits: tuple[Any, ...]
    index_rows: tuple[SemanticIndexItem, ...]
    rows_by_id: Mapping[int, SemanticIndexItem]
    lexical_by_id: Mapping[int, float]
    bm25_by_id: Mapping[int, float]
    semantic_by_id: Mapping[int, float]


def _query_vector(query: str, embedding_provider: Any) -> list[float] | None:
    if embedding_provider is None:
        return None
    try:
        return [float(item) for item in embedding_provider.embed([query])[0]]
    except Exception:
        return None


def _scope_ids(
    *, group_id: str = "", chat_stream_id: str = "", include_global: bool = True
) -> set[str]:
    if not group_id and not chat_stream_id:
        return set()
    stream_id = normalize_sticker_stream_id(
        group_id=group_id, chat_stream_id=chat_stream_id
    )
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


def _normalize_sticker_query(query: str) -> str:
    normalized = str(query or "").strip()
    for token in STICKER_QUERY_STOPWORDS:
        normalized = normalized.replace(token, " ")
    normalized = " ".join(normalized.split())
    return normalized or str(query or "").strip()


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


class StickerRagService:
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
        include_debug: bool = False,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        request = RetrievalRequest(
            query=_normalize_sticker_query(query),
            limit=max(1, int(limit)),
            include_debug=include_debug,
            options={
                "domain": "sticker",
                "group_id": group_id,
                "chat_stream_id": chat_stream_id,
                "include_global": bool(include_global),
            },
        )
        return self._build_pipeline().execute(request)

    def _build_pipeline(self) -> RetrievalPipeline:
        return RetrievalPipeline(
            candidate_source=_StickerCandidateSource(self),
            citation_policy=AllowAllCitationPolicy(),
            filter_policy=_StickerFilterPolicy(self),
            scoring_policy=_StickerScoringPolicy(self),
            reranker=_StickerReranker(self),
            budget_policy=_StickerBudgetPolicy(self),
            debug_trace_sink=_StickerDebugTraceSink(self),
            result_builder=_StickerResultBuilder(self),
            provider_id="sticker",
        )

    def _passes_hard_gate(self, row: StickerMemory, scope: set[str]) -> bool:
        return self._hard_gate_reason(row, scope) == ""

    def _hard_gate_reason(self, row: StickerMemory, scope: set[str]) -> str:
        if str(row.status or "") != "active":
            return "inactive_status"
        if (
            str(row.dedupe_status or "") == "duplicate"
            or row.duplicate_of_id is not None
        ):
            return "duplicate"
        if str(row.describe_status or "") != "ok":
            return "describe_not_ok"
        if scope and str(row.chat_stream_id or "") not in scope:
            return "out_of_scope"
        if not sticker_has_text_index_payload(row):
            return "missing_text_index_payload"
        if not is_sticker_replyable(row):
            return "unreplyable"
        return ""

    @staticmethod
    def _row_candidate_id(row: SemanticIndexItem | None) -> str:
        if row is None:
            return ""
        return f"{row.source_type}:{row.source_id}:{row.source_sub_id}"

    @staticmethod
    def _debug_sql_filters(
        group_id: str, chat_stream_id: str, include_global: bool
    ) -> dict[str, Any]:
        return {
            "source_types": ["sticker"],
            "status": "active",
            "visibility": "recall",
            "dedupe_status": "not_duplicate",
            "duplicate_of_id": None,
            "describe_status": "ok",
            "replyable": True,
            "group_id": group_id,
            "chat_stream_id": chat_stream_id,
            "include_global": include_global,
        }

    def _pre_score(self, item: _StickerCandidate) -> float:
        return weighted_score(
            {"semantic": item.semantic, "lexical": item.lexical},
            {"semantic": 0.60, "lexical": 0.40},
        )

    def _reranker_candidates(
        self, candidates: list[_StickerCandidate], *, limit: int
    ) -> list[_StickerCandidate]:
        if self.reranker_provider is None:
            return []
        max_inputs = min(MAX_RERANK_INPUTS, max(int(limit) * 2, int(limit)))
        return candidates[: max(1, max_inputs)]

    def _apply_reranker(
        self, query: str, candidates: list[_StickerCandidate]
    ) -> list[SemanticCandidate]:
        if self.reranker_provider is None or not candidates:
            return []
        rerank_inputs = [
            SemanticCandidate(
                candidate_id=item.candidate_id,
                source_type="sticker",
                title=item.sticker.name or item.row.title or "",
                text=item.row.embedding_text or item.row.text or "",
                metadata={
                    "tags": item.row.meta_json
                    and _safe_meta_list(item.row.meta_json, "tags"),
                    "emotions": item.row.meta_json
                    and _safe_meta_list(item.row.meta_json, "emotions"),
                    "scenario": item.row.meta_json
                    and _safe_meta_text(item.row.meta_json, "scenario"),
                },
            )
            for item in candidates
        ]
        reranked = self.reranker_provider.rerank(
            query, rerank_inputs, top_k=len(rerank_inputs)
        )
        scores = {item.candidate_id: item for item in reranked}
        for item in candidates:
            score = scores.get(item.candidate_id)
            if score is not None:
                item.reranker = score.score
                item.raw_reranker = score.raw_score
        return rerank_inputs

    def _recency_score(self, item: _StickerCandidate) -> float:
        event_at = (
            item.sticker.last_used
            or item.sticker.last_seen
            or item.sticker.created_at
            or item.row.source_updated_at
            or item.row.updated_at
            or item.row.indexed_at
        )
        return recency_score(event_at, half_life_days=RECENCY_HALF_LIFE_DAYS)

    def _final_score(
        self, item: _StickerCandidate, *, recency: float | None = None
    ) -> float:
        relevance = weighted_score(
            {
                "reranker": item.reranker,
                "semantic": item.semantic,
                "lexical": item.lexical,
            },
            RELEVANCE_WEIGHTS,
        )
        return weighted_score(
            {
                "relevance": relevance,
                "usage": _usage_score(item.sticker),
                "source_prior": item.row.source_prior or 0.0,
                "recency": recency
                if recency is not None
                else self._recency_score(item),
            },
            FINAL_WEIGHTS,
        )

    def _result_item(self, item: _StickerCandidate) -> dict[str, Any]:
        recency = self._recency_score(item)
        final = self._final_score(item, recency=recency)
        return sticker_to_dict(item.sticker) | {
            "score": final,
            "score_breakdown": {
                "lexical": item.lexical,
                "semantic": item.semantic,
                "reranker": item.reranker,
                "raw_reranker": item.raw_reranker,
                "usage": _usage_score(item.sticker),
                "recency": recency,
                "final": final,
            },
        }

    def _debug_candidate(
        self, item: _StickerCandidate, *, bm25_by_id: dict[int, float]
    ) -> dict[str, Any]:
        result = self._result_item(item)
        return {
            "candidate_id": item.candidate_id,
            "item_id": item.row.id,
            "id": result.get("id"),
            "source_type": "sticker",
            "title": result.get("name") or "",
            "text": result.get("description") or "",
            "reply_token": result.get("reply_token") or "",
            "send_code": result.get("send_code") or "",
            "tags": result.get("tags") or [],
            "emotions": result.get("emotions") or [],
            "bm25_raw": bm25_by_id.get(int(item.row.id)),
            "lexical_score": item.lexical,
            "semantic_score": item.semantic,
            "reranker_score": item.reranker,
            "raw_reranker": item.raw_reranker,
            "final_score": result.get("score"),
            "score_breakdown": result.get("score_breakdown")
            | {
                "bm25_raw": bm25_by_id.get(int(item.row.id)),
            },
            "skipped_reason": "",
        }


@dataclass(frozen=True, slots=True)
class _StickerCandidateSource:
    service: StickerRagService

    def recall(self, request: RetrievalRequest) -> _StickerRecallResult:
        has_vector_rows = has_vector_recall_rows(
            self.service.db,
            source_types={"sticker"},
            ensure_schema=not self.service.readonly,
        )
        query_vector = (
            _query_vector(request.query, self.service.embedding_provider)
            if has_vector_rows
            else None
        )
        fts_hits = fts_recall_hits(
            self.service.db,
            request.query,
            source_types={"sticker"},
            limit=200,
            ensure_schema=not self.service.readonly,
        )
        lexical_by_id = {hit.item_id: hit.lexical_score for hit in fts_hits}
        bm25_by_id = {hit.item_id: hit.bm25_raw for hit in fts_hits}
        vector_hits = vector_recall_hits(
            self.service.db,
            query_vector=query_vector,
            source_types={"sticker"},
            limit=200,
            ensure_schema=not self.service.readonly,
        )
        semantic_by_id = {hit.item_id: hit.semantic_score for hit in vector_hits}
        recent_rows = load_recall_rows(
            self.service.db,
            source_types={"sticker"},
            limit=400,
            ensure_schema=not self.service.readonly,
        )
        rows_by_id = {int(row.id): row for row in recent_rows}
        recall_ids = [hit.item_id for hit in fts_hits] + [
            hit.item_id for hit in vector_hits
        ]
        missing_ids = [item_id for item_id in recall_ids if item_id not in rows_by_id]
        rows_by_id.update(
            load_recall_rows_by_ids(
                self.service.db,
                missing_ids,
                ensure_schema=not self.service.readonly,
            )
        )
        fts_ordered = [
            rows_by_id[hit.item_id] for hit in fts_hits if hit.item_id in rows_by_id
        ]
        vector_ordered = [
            rows_by_id[hit.item_id] for hit in vector_hits if hit.item_id in rows_by_id
        ]
        return _StickerRecallResult(
            recall_query=request.query,
            query_vector=query_vector,
            fts_hits=tuple(fts_hits),
            vector_hits=tuple(vector_hits),
            index_rows=tuple(
                _merge_recall_rows(fts_ordered, vector_ordered, recent_rows)
            ),
            rows_by_id=rows_by_id,
            lexical_by_id=lexical_by_id,
            bm25_by_id=bm25_by_id,
            semantic_by_id=semantic_by_id,
        )


@dataclass(frozen=True, slots=True)
class _StickerFilterPolicy:
    service: StickerRagService

    def filter_candidates(
        self,
        request: RetrievalRequest,
        citation,
    ) -> RetrievalCandidates[_StickerCandidate]:
        recall: _StickerRecallResult = citation.source
        sticker_ids = [
            sticker_id
            for row in recall.index_rows
            if (sticker_id := _safe_sticker_id(row.source_id)) is not None
        ]
        stickers = (
            {
                int(row.id): row
                for row in self.service.db.query(StickerMemory)
                .filter(StickerMemory.id.in_(sticker_ids))
                .all()
            }
            if sticker_ids
            else {}
        )
        scope = _scope_ids(
            group_id=str(request.options.get("group_id") or ""),
            chat_stream_id=str(request.options.get("chat_stream_id") or ""),
            include_global=bool(request.options.get("include_global", True)),
        )
        candidates: list[_StickerCandidate] = []
        hard_gate: list[dict[str, Any]] = []
        semantic_hits = 0
        for row in recall.index_rows:
            sticker_id = _safe_sticker_id(row.source_id)
            if sticker_id is None:
                continue
            sticker = stickers.get(sticker_id)
            skip_reason = (
                "missing_sticker"
                if sticker is None
                else self.service._hard_gate_reason(sticker, scope)
            )
            if sticker is not None:
                hard_gate.append(
                    {
                        "candidate_id": self.service._row_candidate_id(row),
                        "item_id": row.id,
                        "sticker_id": sticker.id,
                        "chat_stream_id": sticker.chat_stream_id,
                        "status": sticker.status,
                        "dedupe_status": sticker.dedupe_status,
                        "duplicate_of_id": sticker.duplicate_of_id,
                        "describe_status": sticker.describe_status,
                        "replyable": is_sticker_replyable(sticker),
                        "has_text_index_payload": sticker_has_text_index_payload(
                            sticker
                        ),
                        "passed": not skip_reason,
                        "skip_reason": skip_reason,
                    }
                )
            if skip_reason or sticker is None:
                continue
            lexical = recall.lexical_by_id.get(int(row.id))
            if lexical is None:
                lexical = lexical_overlap_score(
                    recall.recall_query,
                    row.lexical_text or row.text or "",
                )
            semantic = recall.semantic_by_id.get(int(row.id))
            if semantic is None:
                semantic = semantic_score_for_row(
                    row,
                    query_vector=recall.query_vector,
                    embedding_provider=None,
                )
            if semantic is not None and semantic > 0:
                semantic_hits += 1
            if lexical > 0 or (semantic is not None and semantic >= 0.10):
                candidates.append(
                    _StickerCandidate(
                        row=row,
                        sticker=sticker,
                        lexical=lexical,
                        semantic=semantic,
                    )
                )
        return RetrievalCandidates(
            items=tuple(candidates),
            metadata={
                "hard_gate": tuple(hard_gate),
                "semantic_hits": semantic_hits,
                "bm25_by_id": dict(recall.bm25_by_id),
            },
        )


@dataclass(frozen=True, slots=True)
class _StickerScoringPolicy:
    service: StickerRagService

    def pre_rank(
        self,
        request: RetrievalRequest,
        candidates: RetrievalCandidates[_StickerCandidate],
    ) -> RetrievalCandidates[_StickerCandidate]:
        del request
        return candidates.with_items(
            tuple(
                sorted(
                    candidates.items,
                    key=self.service._pre_score,
                    reverse=True,
                )
            )
        )

    def final_rank(
        self,
        request: RetrievalRequest,
        outcome: RerankOutcome[_StickerCandidate],
    ) -> RankingOutcome[_StickerCandidate]:
        del request
        degraded = bool(outcome.metadata.get("degraded"))
        gated: list[_StickerCandidate] = []
        gate_debug: list[dict[str, Any]] = []
        for item in outcome.items:
            passed = passes_relevance_gate(
                {
                    "reranker": item.reranker,
                    "semantic": item.semantic,
                    "lexical": item.lexical,
                },
                degraded=degraded,
                min_reranker=self.service.min_reranker,
            )
            if passed:
                gated.append(item)
            gate_debug.append(
                {
                    "candidate_id": item.candidate_id,
                    "passed": passed,
                    "degraded": degraded,
                    "components": {
                        "reranker": item.reranker,
                        "semantic": item.semantic,
                        "lexical": item.lexical,
                    },
                }
            )
        return RankingOutcome(
            items=tuple(sorted(gated, key=self.service._final_score, reverse=True)),
            metadata={
                **dict(outcome.metadata),
                "all_candidates": outcome.items,
                "relevance_gate": tuple(gate_debug),
            },
        )


@dataclass(frozen=True, slots=True)
class _StickerReranker:
    service: StickerRagService

    def rerank(
        self,
        request: RetrievalRequest,
        candidates: RetrievalCandidates[_StickerCandidate],
    ) -> RerankOutcome[_StickerCandidate]:
        all_candidates = list(candidates.items)
        reranker_candidates = self.service._reranker_candidates(
            all_candidates,
            limit=request.limit,
        )
        reranker_inputs = self.service._apply_reranker(
            request.query,
            reranker_candidates,
        )
        return RerankOutcome(
            items=tuple(all_candidates),
            reranker_items=tuple(reranker_candidates),
            metadata={
                **dict(candidates.metadata),
                "degraded": self.service.reranker_provider is None,
                "reranker_inputs": tuple(reranker_inputs),
                "reranker_candidate_count": len(reranker_candidates),
            },
        )


@dataclass(frozen=True, slots=True)
class _StickerBudgetPolicy:
    service: StickerRagService

    def limit_candidates(
        self,
        request: RetrievalRequest,
        candidates: RetrievalCandidates[_StickerCandidate],
    ) -> RetrievalCandidates[_StickerCandidate]:
        del request
        return candidates.with_items(candidates.items[:80])

    def select_results(
        self,
        request: RetrievalRequest,
        ranking: RankingOutcome[_StickerCandidate],
    ) -> list[dict[str, Any]]:
        return [
            self.service._result_item(item) for item in ranking.items[: request.limit]
        ]


@dataclass(frozen=True, slots=True)
class _StickerDebugTraceSink:
    service: StickerRagService

    def start(self, request, source: _StickerRecallResult, citation):
        del citation
        if not request.include_debug:
            return None
        return {
            "sql_filters": self.service._debug_sql_filters(
                str(request.options.get("group_id") or ""),
                str(request.options.get("chat_stream_id") or ""),
                bool(request.options.get("include_global", True)),
            ),
            "fts_hits": [
                {
                    "item_id": hit.item_id,
                    "bm25_raw": hit.bm25_raw,
                    "lexical_score": hit.lexical_score,
                    "candidate_id": self.service._row_candidate_id(
                        source.rows_by_id.get(hit.item_id)
                    ),
                }
                for hit in source.fts_hits
                if hit.item_id in source.rows_by_id
            ],
            "hard_gate": [],
            "vector_hits": [
                {
                    "item_id": hit.item_id,
                    "semantic_score": hit.semantic_score,
                    "candidate_id": self.service._row_candidate_id(
                        source.rows_by_id.get(hit.item_id)
                    ),
                }
                for hit in source.vector_hits
                if hit.item_id in source.rows_by_id
            ],
            "embedding_hits": [],
            "merged_candidates": [],
            "reranker_input_pairs": [],
            "final_candidates": [],
            "relevance_gate": [],
        }

    def candidates_ready(self, trace, candidates) -> None:
        if not isinstance(trace, dict):
            return
        bm25_by_id = dict(candidates.metadata.get("bm25_by_id", {}))
        trace["hard_gate"] = list(candidates.metadata.get("hard_gate", ()))
        trace["embedding_hits"] = [
            self.service._debug_candidate(item, bm25_by_id=bm25_by_id)
            for item in candidates.items
            if item.semantic is not None and item.semantic > 0
        ]
        trace["merged_candidates"] = [
            self.service._debug_candidate(item, bm25_by_id=bm25_by_id)
            for item in candidates.items
        ]

    def rerank_complete(self, trace, outcome) -> None:
        if not isinstance(trace, dict):
            return
        trace["reranker_input_pairs"] = [
            {
                "candidate_id": candidate.candidate_id,
                "source_type": candidate.source_type,
                "query": "",
                "title": candidate.title,
                "text": candidate.text,
                "metadata": candidate.metadata,
            }
            for candidate in outcome.metadata.get("reranker_inputs", ())
        ]

    def ranking_complete(self, trace, ranking) -> None:
        if not isinstance(trace, dict):
            return
        bm25_by_id = dict(ranking.metadata.get("bm25_by_id", {}))
        trace["merged_candidates"] = [
            self.service._debug_candidate(item, bm25_by_id=bm25_by_id)
            for item in ranking.metadata.get("all_candidates", ())
        ]
        trace["final_candidates"] = [
            self.service._debug_candidate(item, bm25_by_id=bm25_by_id)
            for item in ranking.items
        ]
        trace["relevance_gate"] = list(ranking.metadata.get("relevance_gate", ()))

    def finish(self, trace, selected) -> None:
        return None


@dataclass(frozen=True, slots=True)
class _StickerResultBuilder:
    service: StickerRagService

    def build(
        self,
        state: RetrievalPipelineState[
            _StickerRecallResult,
            _StickerCandidate,
            list[dict[str, Any]],
            list[dict[str, Any]] | dict[str, Any],
        ],
    ) -> list[dict[str, Any]] | dict[str, Any]:
        if not state.request.include_debug:
            return state.selected
        degraded = bool(state.rerank_outcome.metadata.get("degraded"))
        return {
            "items": state.selected,
            "degraded": degraded,
            "fallback_reason": "reranker_unavailable" if degraded else "",
            "stats": {
                "fts_candidates": len(state.source.fts_hits),
                "vector_candidates": len(state.source.vector_hits),
                "embedding_candidates": int(
                    state.candidates.metadata.get("semantic_hits") or 0
                ),
                "merged_candidates": len(state.candidates.items),
                "reranker_candidates": int(
                    state.rerank_outcome.metadata.get("reranker_candidate_count") or 0
                ),
                "reranker_input_limit": MAX_RERANK_INPUTS,
                "normalized_query": state.source.recall_query,
                "final_items": len(state.selected),
            },
            "debug_trace": state.debug_trace,
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

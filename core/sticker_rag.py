"""Sticker RAG 检索服务。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from core.database import SemanticIndexItem, StickerMemory
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
        recall_query = _normalize_sticker_query(query)
        has_vector_rows = has_vector_recall_rows(
            self.db,
            source_types={"sticker"},
            ensure_schema=not self.readonly,
        )
        query_vector = _query_vector(recall_query, self.embedding_provider) if has_vector_rows else None
        fts_hits = fts_recall_hits(
            self.db,
            recall_query,
            source_types={"sticker"},
            limit=200,
            ensure_schema=not self.readonly,
        )
        lexical_by_id = {hit.item_id: hit.lexical_score for hit in fts_hits}
        bm25_by_id = {hit.item_id: hit.bm25_raw for hit in fts_hits}
        vector_hits = vector_recall_hits(
            self.db,
            query_vector=query_vector,
            source_types={"sticker"},
            limit=200,
            ensure_schema=not self.readonly,
        )
        semantic_by_id = {hit.item_id: hit.semantic_score for hit in vector_hits}
        recent_rows = load_recall_rows(
            self.db,
            source_types={"sticker"},
            limit=400,
            ensure_schema=not self.readonly,
        )
        rows_by_id = {int(row.id): row for row in recent_rows}
        recall_ids = [hit.item_id for hit in fts_hits] + [hit.item_id for hit in vector_hits]
        missing_fts_ids = [item_id for item_id in recall_ids if item_id not in rows_by_id]
        rows_by_id.update(load_recall_rows_by_ids(
            self.db,
            missing_fts_ids,
            ensure_schema=not self.readonly,
        ))
        fts_ordered = [rows_by_id[hit.item_id] for hit in fts_hits if hit.item_id in rows_by_id]
        vector_ordered = [rows_by_id[hit.item_id] for hit in vector_hits if hit.item_id in rows_by_id]
        index_rows = _merge_recall_rows(fts_ordered, vector_ordered, recent_rows)
        sticker_ids = [
            sticker_id
            for row in index_rows
            if (sticker_id := _safe_sticker_id(row.source_id)) is not None
        ]
        if not sticker_ids:
            if include_debug:
                return {
                    "items": [],
                    "degraded": self.reranker_provider is None,
                    "fallback_reason": "reranker_unavailable" if self.reranker_provider is None else "",
                    "stats": {
                        "fts_candidates": len(fts_hits),
                        "vector_candidates": len(vector_hits),
                        "merged_candidates": 0,
                        "reranker_candidates": 0,
                        "reranker_input_limit": MAX_RERANK_INPUTS,
                        "normalized_query": recall_query,
                        "final_items": 0,
                    },
                    "debug_trace": {
                        "sql_filters": self._debug_sql_filters(group_id, chat_stream_id, include_global),
                        "fts_hits": [],
                        "hard_gate": [],
                        "vector_hits": [],
                        "embedding_hits": [],
                        "merged_candidates": [],
                        "reranker_input_pairs": [],
                        "final_candidates": [],
                        "relevance_gate": [],
                    },
                }
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
        debug_stages: dict[str, Any] | None = None
        if include_debug:
            debug_stages = {
                "sql_filters": self._debug_sql_filters(group_id, chat_stream_id, include_global),
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
                "hard_gate": [],
                "vector_hits": [
                    {
                        "item_id": hit.item_id,
                        "semantic_score": hit.semantic_score,
                        "candidate_id": self._row_candidate_id(rows_by_id.get(hit.item_id)),
                    }
                    for hit in vector_hits
                    if hit.item_id in rows_by_id
                ],
                "embedding_hits": [],
                "merged_candidates": [],
                "reranker_input_pairs": [],
                "final_candidates": [],
                "relevance_gate": [],
            }
        candidates: list[_StickerCandidate] = []
        semantic_hits = 0
        for row in index_rows:
            sticker_id = _safe_sticker_id(row.source_id)
            if sticker_id is None:
                continue
            sticker = stickers.get(sticker_id)
            skip_reason = "missing_sticker" if sticker is None else self._hard_gate_reason(sticker, scope)
            if debug_stages is not None and sticker is not None:
                debug_stages["hard_gate"].append({
                    "candidate_id": self._row_candidate_id(row),
                    "item_id": row.id,
                    "sticker_id": sticker.id,
                    "chat_stream_id": sticker.chat_stream_id,
                    "status": sticker.status,
                    "dedupe_status": sticker.dedupe_status,
                    "duplicate_of_id": sticker.duplicate_of_id,
                    "describe_status": sticker.describe_status,
                    "replyable": is_sticker_replyable(sticker),
                    "has_text_index_payload": sticker_has_text_index_payload(sticker),
                    "passed": not skip_reason,
                    "skip_reason": skip_reason,
                })
            if skip_reason:
                continue
            lexical = lexical_by_id.get(int(row.id))
            if lexical is None:
                lexical = lexical_overlap_score(recall_query, row.lexical_text or row.text or "")
            semantic = semantic_by_id.get(int(row.id))
            if semantic is None:
                semantic = semantic_score_for_row(row, query_vector=query_vector, embedding_provider=None)
            if semantic is not None and semantic > 0:
                semantic_hits += 1
            if lexical > 0 or (semantic is not None and semantic >= 0.10):
                candidates.append(_StickerCandidate(
                    row=row,
                    sticker=sticker,
                    lexical=lexical,
                    semantic=semantic,
                ))

        candidates.sort(key=self._pre_score, reverse=True)
        candidates = candidates[:80]
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
        rerank_candidates = self._reranker_candidates(candidates, limit=limit)
        rerank_inputs = self._apply_reranker(recall_query, rerank_candidates)
        if debug_stages is not None:
            debug_stages["reranker_input_pairs"] = [
                {
                    "candidate_id": candidate.candidate_id,
                    "source_type": candidate.source_type,
                    "query": recall_query,
                    "title": candidate.title,
                    "text": candidate.text,
                    "metadata": candidate.metadata,
                }
                for candidate in rerank_inputs
            ]
        degraded = self.reranker_provider is None
        gated: list[_StickerCandidate] = []
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
            return {
                "items": items,
                "degraded": degraded,
                "fallback_reason": "reranker_unavailable" if degraded else "",
                "stats": {
                    "fts_candidates": len(fts_hits),
                    "vector_candidates": len(vector_hits),
                    "embedding_candidates": semantic_hits,
                    "merged_candidates": len(candidates),
                    "reranker_candidates": len(rerank_candidates),
                    "reranker_input_limit": MAX_RERANK_INPUTS,
                    "normalized_query": recall_query,
                    "final_items": len(items),
                },
                "debug_trace": debug_stages,
            }
        return items

    def _passes_hard_gate(self, row: StickerMemory, scope: set[str]) -> bool:
        return self._hard_gate_reason(row, scope) == ""

    def _hard_gate_reason(self, row: StickerMemory, scope: set[str]) -> str:
        if str(row.status or "") != "active":
            return "inactive_status"
        if str(row.dedupe_status or "") == "duplicate" or row.duplicate_of_id is not None:
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
    def _debug_sql_filters(group_id: str, chat_stream_id: str, include_global: bool) -> dict[str, Any]:
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

    def _reranker_candidates(self, candidates: list[_StickerCandidate], *, limit: int) -> list[_StickerCandidate]:
        if self.reranker_provider is None:
            return []
        max_inputs = min(MAX_RERANK_INPUTS, max(int(limit) * 2, int(limit)))
        return candidates[:max(1, max_inputs)]

    def _apply_reranker(self, query: str, candidates: list[_StickerCandidate]) -> list[SemanticCandidate]:
        if self.reranker_provider is None or not candidates:
            return []
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
        reranked = self.reranker_provider.rerank(query, rerank_inputs, top_k=len(rerank_inputs))
        scores = {item.candidate_id: item for item in reranked}
        for item in candidates:
            score = scores.get(item.candidate_id)
            if score is not None:
                item.reranker = score.score
                item.raw_reranker = score.raw_score
        return rerank_inputs

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

    def _debug_candidate(self, item: _StickerCandidate, *, bm25_by_id: dict[int, float]) -> dict[str, Any]:
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
            "score_breakdown": result.get("score_breakdown") | {
                "bm25_raw": bm25_by_id.get(int(item.row.id)),
            },
            "skipped_reason": "",
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

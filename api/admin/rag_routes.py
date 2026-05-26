"""RAG Debug 管理接口。"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.admin.common import verify_admin
from core.database import RagDebugRun, get_db
from core.semantic.schema import ensure_semantic_schema


router = APIRouter(prefix="/rag", tags=["admin-rag"])


class RagDebugQueryRequest(BaseModel):
    source_type: str = Field(default="all")
    query: str = Field(default="")
    limit: int = Field(default=10, ge=1, le=100)
    filters: dict[str, Any] = Field(default_factory=dict)


def _safe_json_loads(raw: str | None, fallback: Any) -> Any:
    try:
        return json.loads(raw or "")
    except Exception:
        return fallback


def _run_to_dict(row: RagDebugRun, *, include_payload: bool = False) -> dict[str, Any]:
    item = {
        "id": row.id,
        "trace_id": row.trace_id,
        "source_type": row.source_type,
        "query": row.query,
        "degraded": bool(row.degraded),
        "fallback_reason": row.fallback_reason,
        "latency_ms": int(row.latency_ms or 0),
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }
    if include_payload:
        item["request"] = _safe_json_loads(row.request_json, {})
        item["response"] = _safe_json_loads(row.response_json, {})
    return item


def _build_stub_debug_response(body: RagDebugQueryRequest, latency_ms: int) -> dict[str, Any]:
    return {
        "query": body.query,
        "source_type": body.source_type,
        "stages": {
            "sql_filters": body.filters or {},
            "fts_hits": [],
            "embedding_hits": [],
            "merged_candidates": [],
            "reranker_input_pairs": [],
            "final_candidates": [],
        },
        "score_breakdown": {
            "degraded": True,
            "fallback_reason": "rag_debug_stub",
            "source_weights": {},
            "latency_ms": latency_ms,
        },
        "candidates": [],
    }


def _sticker_candidate_to_debug(item: dict[str, Any]) -> dict[str, Any]:
    score = item.get("score_breakdown") if isinstance(item.get("score_breakdown"), dict) else {}
    return {
        "candidate_id": f"sticker:{item.get('id')}:sticker",
        "id": item.get("id"),
        "source_type": "sticker",
        "title": item.get("name") or "",
        "text": item.get("description") or "",
        "reply_token": item.get("reply_token") or "",
        "send_code": item.get("send_code") or "",
        "tags": item.get("tags") or [],
        "emotions": item.get("emotions") or [],
        "reranker_score": score.get("reranker"),
        "final_score": score.get("final", item.get("score")),
        "score_breakdown": score,
        "skipped_reason": "",
    }


def _build_sticker_debug_response(
    body: RagDebugQueryRequest,
    db: Session,
    latency_ms: int,
) -> dict[str, Any]:
    from core.sticker_memory import search_stickers

    filters = body.filters or {}
    results = search_stickers(
        db,
        body.query,
        group_id=str(filters.get("group_id") or ""),
        chat_stream_id=str(filters.get("chat_stream_id") or ""),
        include_global=bool(filters.get("include_global", True)),
        limit=body.limit,
    )
    candidates = [_sticker_candidate_to_debug(item) for item in results]
    reranked = [
        item
        for item in candidates
        if item.get("score_breakdown", {}).get("reranker") is not None
    ]
    degraded = not reranked
    score_breakdown = {
        "degraded": degraded,
        "fallback_reason": "reranker_unavailable" if degraded else "",
        "source_weights": {"sticker": 1.0},
        "latency_ms": latency_ms,
        "fts_candidates": len(candidates),
        "embedding_candidates": 0,
        "merged_candidates": len(candidates),
        "reranker_candidates": len(reranked),
        "final_items": len(candidates),
    }
    return {
        "query": body.query,
        "source_type": "sticker",
        "stages": {
            "sql_filters": {
                "status": "active",
                "dedupe_status": "not_duplicate",
                "duplicate_of_id": None,
                "describe_status": "ok",
                "replyable": True,
                **filters,
            },
            "fts_hits": candidates,
            "embedding_hits": [],
            "merged_candidates": candidates,
            "reranker_input_pairs": [],
            "final_candidates": candidates,
        },
        "score_breakdown": score_breakdown,
        "candidates": candidates,
    }


def _knowledge_candidate_to_debug(item: dict[str, Any]) -> dict[str, Any]:
    score = item.get("score_breakdown") if isinstance(item.get("score_breakdown"), dict) else {}
    return {
        "candidate_id": item.get("candidate_id") or f"knowledge:{item.get('document_id')}:{item.get('chunk_id')}",
        "document_id": item.get("document_id"),
        "chunk_id": item.get("chunk_id"),
        "source_type": "knowledge",
        "title": item.get("title") or "",
        "text": item.get("text") or "",
        "citation": item.get("citation") or {},
        "trust_level": item.get("trust_level") or "",
        "reranker_score": score.get("reranker"),
        "final_score": score.get("final", item.get("score")),
        "score_breakdown": score,
        "skipped_reason": "",
    }


def _build_knowledge_debug_response(
    body: RagDebugQueryRequest,
    db: Session,
    latency_ms: int,
) -> dict[str, Any]:
    from core.knowledge_rag import KnowledgeRagService

    filters = body.filters or {}
    result = KnowledgeRagService(db).query(
        body.query,
        limit=body.limit,
        min_trust_level=str(filters.get("min_trust_level") or "low"),
        published_after=str(filters.get("published_after") or ""),
        published_before=str(filters.get("published_before") or ""),
    )
    candidates = [_knowledge_candidate_to_debug(item) for item in result.get("items") or []]
    stats = result.get("stats") or {}
    score_breakdown = {
        "degraded": bool(result.get("degraded")),
        "fallback_reason": result.get("fallback_reason") or "",
        "source_weights": {"knowledge": 1.0},
        "latency_ms": latency_ms,
        **stats,
    }
    return {
        "query": body.query,
        "source_type": "knowledge",
        "stages": {
            "sql_filters": {
                "status": "active",
                "citation_required": True,
                **filters,
            },
            "fts_hits": candidates,
            "embedding_hits": [],
            "merged_candidates": candidates,
            "reranker_input_pairs": [],
            "final_candidates": candidates,
            "skipped": {
                "no_citation": stats.get("skipped_no_citation", 0),
                "filter": stats.get("skipped_filter", 0),
            },
        },
        "score_breakdown": score_breakdown,
        "candidates": candidates,
    }


def _build_group_analysis_debug_response(
    body: RagDebugQueryRequest,
    latency_ms: int,
) -> dict[str, Any]:
    from creatures.nanobot.prompts.skills.group_analysis.local_rag import select_group_analysis_context

    filters = body.filters or {}
    messages = filters.get("messages") if isinstance(filters.get("messages"), list) else []
    result = select_group_analysis_context(
        messages,
        query=body.query,
        bundle_size=int(filters.get("bundle_size") or 8),
        lexical_top_k=int(filters.get("lexical_top_k") or 300),
        reranker_top_k=int(filters.get("reranker_top_k") or 40),
        neighbor_radius=int(filters.get("neighbor_radius") or 1),
        budget_chars=int(filters.get("budget_chars") or 0),
    )
    stats = result.get("stats_logs") or {}
    prompt_logs = result.get("prompt_logs") or {}
    candidates = [
        {
            "candidate_id": item.get("bundle_id"),
            "source_type": "group_analysis",
            "title": item.get("bundle_id"),
            "text": item.get("text"),
            "reranker_score": item.get("reranker"),
            "final_score": item.get("score"),
            "score_breakdown": item,
            "skipped_reason": "",
        }
        for item in prompt_logs.get("hit_bundles") or []
    ]
    return {
        "query": body.query,
        "source_type": "group_analysis",
        "stages": {
            "sql_filters": {key: value for key, value in filters.items() if key != "messages"},
            "stats_logs": stats,
            "prompt_logs": prompt_logs,
            "fts_hits": candidates,
            "embedding_hits": [],
            "merged_candidates": candidates,
            "reranker_input_pairs": [],
            "final_candidates": candidates,
        },
        "score_breakdown": {
            "degraded": True,
            "fallback_reason": "local_rag_debug_no_external_reranker",
            "source_weights": {"group_analysis": 1.0},
            "latency_ms": latency_ms,
            **stats,
        },
        "candidates": candidates,
    }


@router.post("/debug/query")
def run_rag_debug_query(
    body: RagDebugQueryRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    started = time.perf_counter()
    ensure_semantic_schema(db.bind)
    trace_id = uuid.uuid4().hex
    latency_ms = int((time.perf_counter() - started) * 1000)
    if body.source_type == "sticker":
        response_json = _build_sticker_debug_response(body, db, latency_ms)
        latency_ms = int((time.perf_counter() - started) * 1000)
        response_json["score_breakdown"]["latency_ms"] = latency_ms
    elif body.source_type == "knowledge":
        response_json = _build_knowledge_debug_response(body, db, latency_ms)
        latency_ms = int((time.perf_counter() - started) * 1000)
        response_json["score_breakdown"]["latency_ms"] = latency_ms
    elif body.source_type == "group_analysis":
        response_json = _build_group_analysis_debug_response(body, latency_ms)
        latency_ms = int((time.perf_counter() - started) * 1000)
        response_json["score_breakdown"]["latency_ms"] = latency_ms
    else:
        response_json = _build_stub_debug_response(body, latency_ms)
    score_breakdown = response_json.get("score_breakdown") or {}
    request_json = body.model_dump_json() if hasattr(body, "model_dump_json") else body.json()
    run = RagDebugRun(
        trace_id=trace_id,
        source_type=body.source_type,
        query=body.query,
        request_json=request_json,
        response_json=json.dumps(response_json, ensure_ascii=False),
        degraded=1 if score_breakdown.get("degraded") else 0,
        fallback_reason=str(score_breakdown.get("fallback_reason") or ""),
        latency_ms=latency_ms,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return {
        "run_id": run.id,
        "trace_id": trace_id,
        "response": response_json,
    }


@router.get("/debug/runs")
def list_rag_debug_runs(
    source_type: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    ensure_semantic_schema(db.bind)
    query = db.query(RagDebugRun)
    if source_type:
        query = query.filter(RagDebugRun.source_type == source_type)
    rows = query.order_by(RagDebugRun.id.desc()).limit(limit).all()
    return {
        "items": [_run_to_dict(row) for row in rows],
        "total": len(rows),
    }


@router.get("/debug/runs/{run_id}")
def get_rag_debug_run(
    run_id: int,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    ensure_semantic_schema(db.bind)
    row = db.query(RagDebugRun).filter(RagDebugRun.id == run_id).first()
    if row is None:
        raise HTTPException(404, "rag debug run not found")
    return _run_to_dict(row, include_payload=True)


@router.get("/debug/runs/{run_id}/export")
def export_rag_debug_run(
    run_id: int,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    ensure_semantic_schema(db.bind)
    row = db.query(RagDebugRun).filter(RagDebugRun.id == run_id).first()
    if row is None:
        raise HTTPException(404, "rag debug run not found")
    return _run_to_dict(row, include_payload=True)

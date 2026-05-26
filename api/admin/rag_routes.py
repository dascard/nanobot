"""RAG Debug 管理接口。"""

from __future__ import annotations

import json
import re
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


class RagDebugBuildIndexRequest(BaseModel):
    source_type: str = Field(default="all")
    limit_per_source: int = Field(default=500, ge=1, le=5000)
    index_version: str = Field(default="")


def _safe_json_loads(raw: str | None, fallback: Any) -> Any:
    try:
        return json.loads(raw or "")
    except Exception:
        return fallback


_SENSITIVE_KEY_PARTS = {
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "token",
    "cookie",
    "set_cookie",
    "set-cookie",
    "password",
    "passwd",
    "secret",
}
_IDENTITY_KEYS = {
    "user_id",
    "group_id",
    "session_id",
    "chat_stream_id",
    "sender_id",
    "receiver_id",
    "qq",
    "openid",
}
_CONTENT_KEYS = {"content", "text", "context", "prompt", "message", "messages", "query"}
_URL_SECRET_RE = re.compile(
    r"(?i)([?&](?:token|access_token|refresh_token|api_key|key|secret|signature|auth)=)([^&#]+)"
)


def _normalized_key(key: Any) -> str:
    return str(key or "").strip().lower().replace("-", "_")


def _is_sensitive_key(key: Any) -> bool:
    normalized = _normalized_key(key)
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _is_identity_key(key: Any) -> bool:
    return _normalized_key(key) in _IDENTITY_KEYS


def _is_content_key(key: Any) -> bool:
    return _normalized_key(key) in _CONTENT_KEYS


def _is_no_context_payload(value: dict[str, Any]) -> bool:
    visibility = str(value.get("visibility") or value.get("context_policy") or "").strip().lower()
    role = str(value.get("role") or value.get("source") or "").strip().lower()
    return bool(value.get("no_context")) or visibility in {"no_context", "internal"} or role == "internal"


def _redact_url_secrets(value: str) -> str:
    return _URL_SECRET_RE.sub(lambda match: f"{match.group(1)}[redacted]", value)


def _sanitize_debug_payload(value: Any, *, max_text_chars: int = 500, max_list_items: int = 50) -> Any:
    if isinstance(value, str):
        value = _redact_url_secrets(value)
        if len(value) <= max_text_chars:
            return value
        return value[:max_text_chars] + f"...[truncated] {len(value) - max_text_chars} chars"
    if isinstance(value, list):
        items = [
            _sanitize_debug_payload(item, max_text_chars=max_text_chars, max_list_items=max_list_items)
            for item in value[:max_list_items]
        ]
        if len(value) > max_list_items:
            items.append({"truncated_items": len(value) - max_list_items})
        return items
    if isinstance(value, dict):
        no_context = _is_no_context_payload(value)
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key):
                sanitized[key_text] = "[redacted]"
                continue
            if _is_identity_key(key):
                sanitized[key_text] = "[redacted:id]"
                continue
            if no_context and _is_content_key(key):
                sanitized[key_text] = "[redacted:no_context]"
                continue
            child_max = min(max_text_chars, 240) if _is_content_key(key) else max_text_chars
            sanitized[key_text] = _sanitize_debug_payload(
                item,
                max_text_chars=child_max,
                max_list_items=max_list_items,
            )
        return sanitized
    return value


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


def _build_memory_debug_response(
    body: RagDebugQueryRequest,
    db: Session,
    latency_ms: int,
) -> dict[str, Any]:
    from core.memory_rag import MemoryRagService
    from core.semantic.provider_factory import (
        get_embedding_provider,
        get_rag_runtime_config,
        get_reranker_provider,
    )

    filters = body.filters or {}
    requested_source = str(filters.get("source") or "").strip()
    if not requested_source:
        if body.source_type == "memory_digest":
            requested_source = "digest"
        elif body.source_type == "session_summary":
            requested_source = "session_summary"
        else:
            requested_source = "all"
    runtime = get_rag_runtime_config("memory")
    result = MemoryRagService(
        db,
        embedding_provider=get_embedding_provider(),
        reranker_provider=get_reranker_provider() if runtime.reranker_enabled else None,
        allow_degraded=runtime.allow_degraded,
    ).query(
        body.query,
        source=requested_source,
        user_id=str(filters.get("user_id") or ""),
        session_id=str(filters.get("session_id") or ""),
        limit=body.limit,
        include_debug=True,
    )
    stages = result.get("debug_trace") or {}
    stats = result.get("stats") or {}
    return {
        "query": body.query,
        "source_type": "memory",
        "source": requested_source,
        "stages": stages,
        "score_breakdown": {
            "degraded": bool(result.get("degraded")),
            "fallback_reason": result.get("fallback_reason") or "",
            "source_weights": (
                {"memory_digest": 0.5, "session_summary": 0.5}
                if requested_source == "all" else {requested_source: 1.0}
            ),
            "source_weights_mode": "display_only_no_quota",
            "latency_ms": latency_ms,
            **stats,
        },
        "candidates": stages.get("final_candidates") or [],
        "items": result.get("items") or [],
    }


def _group_memory_candidate_to_debug(row: Any, components: dict[str, Any], *, skipped_reason: str = "") -> dict[str, Any]:
    return {
        "candidate_id": f"group_memory:{row.id}:memory",
        "id": row.id,
        "source_type": "group_memory",
        "title": f"群体记忆：{row.memory_type}",
        "text": row.content or "",
        "memory_type": row.memory_type,
        "reranker_score": components.get("reranker"),
        "final_score": components.get("final"),
        "score_breakdown": components,
        "skipped_reason": skipped_reason or components.get("skip_reason") or "",
    }


def _build_group_memory_debug_response(
    body: RagDebugQueryRequest,
    db: Session,
    latency_ms: int,
) -> dict[str, Any]:
    from app.group_memory.retrieval_service import GroupMemoryRetrievalService
    from core.database import GroupMemory
    from core.semantic.provider_factory import get_rag_runtime_config, get_reranker_provider

    filters = body.filters or {}
    group_id = str(filters.get("group_id") or "")
    current_user_input = str(filters.get("current_user_input") or body.query or "")
    recent_messages = filters.get("recent_messages") if isinstance(filters.get("recent_messages"), list) else []
    runtime = get_rag_runtime_config("group_memory")
    reranker_provider = get_reranker_provider() if runtime.enabled and runtime.reranker_enabled else None
    selection = GroupMemoryRetrievalService(
        db,
        reranker_provider=reranker_provider,
    ).select(
        group_id=group_id,
        current_user_input=current_user_input,
        recent_messages=recent_messages,
        max_items=int(filters.get("max_items") or body.limit),
        max_chars=int(filters.get("max_chars") or 1200),
    )
    candidate_ids = sorted({int(key) for key in selection.score_components if str(key).isdigit()})
    rows = {
        int(row.id): row
        for row in db.query(GroupMemory).filter(GroupMemory.id.in_(candidate_ids)).all()
    } if candidate_ids else {}
    skipped = {int(item["id"]): str(item.get("reason") or "") for item in selection.skipped if item.get("id")}
    merged = [
        _group_memory_candidate_to_debug(
            rows[row_id],
            selection.score_components.get(str(row_id), {}),
            skipped_reason=skipped.get(row_id, ""),
        )
        for row_id in candidate_ids
        if row_id in rows
    ]
    final = [item for item in merged if int(item.get("id") or 0) in set(selection.selected_ids)]
    return {
        "query": body.query,
        "source_type": "group_memory",
        "stages": {
            "sql_filters": {
                "group_id": group_id,
                "status": "active",
                "inject_policy": "auto",
                **{key: value for key, value in filters.items() if key not in {"recent_messages"}},
            },
            "fts_hits": [],
            "embedding_hits": [],
            "recall_note": "group_memory 当前使用 SQL gate + reranker，不走 semantic_index FTS/embedding 召回。",
            "merged_candidates": merged,
            "reranker_input_pairs": [
                {
                    "candidate_id": item["candidate_id"],
                    "query": current_user_input,
                    "text": item["text"],
                    "source_type": "group_memory",
                }
                for item in merged
            ] if reranker_provider is not None else [],
            "final_candidates": final,
            "skipped": selection.skipped,
            "score_components": selection.score_components,
        },
        "score_breakdown": {
            "degraded": reranker_provider is None,
            "fallback_reason": "reranker_unavailable" if reranker_provider is None else "",
            "recall_mode": "sql_gate_reranker",
            "fts_embedding_trace_available": False,
            "source_weights": {"group_memory": 1.0},
            "latency_ms": latency_ms,
            "merged_candidates": len(merged),
            "reranker_candidates": len(merged) if reranker_provider is not None else 0,
            "final_items": len(final),
        },
        "candidates": final,
    }


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
    from core.sticker_rag import StickerRagService
    from core.semantic.provider_factory import get_embedding_provider, get_rag_runtime_config, get_reranker_provider

    filters = body.filters or {}
    runtime = get_rag_runtime_config("sticker")
    result = StickerRagService(
        db,
        embedding_provider=get_embedding_provider(),
        reranker_provider=get_reranker_provider() if runtime.reranker_enabled else None,
    ).query(
        body.query,
        group_id=str(filters.get("group_id") or ""),
        chat_stream_id=str(filters.get("chat_stream_id") or ""),
        include_global=bool(filters.get("include_global", True)),
        limit=body.limit,
        include_debug=True,
    )
    stages = result.get("debug_trace") if isinstance(result, dict) else {}
    candidates = stages.get("final_candidates") or []
    stats = result.get("stats") if isinstance(result, dict) else {}
    score_breakdown = {
        "degraded": bool(result.get("degraded")) if isinstance(result, dict) else True,
        "fallback_reason": str(result.get("fallback_reason") or "") if isinstance(result, dict) else "rag_debug_unavailable",
        "source_weights": {"sticker": 1.0},
        "latency_ms": latency_ms,
        **stats,
    }
    return {
        "query": body.query,
        "source_type": "sticker",
        "stages": stages,
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
    from core.semantic.provider_factory import get_embedding_provider, get_rag_runtime_config, get_reranker_provider

    filters = body.filters or {}
    runtime = get_rag_runtime_config("knowledge")
    result = KnowledgeRagService(
        db,
        embedding_provider=get_embedding_provider(),
        reranker_provider=get_reranker_provider() if runtime.reranker_enabled else None,
    ).query(
        body.query,
        limit=body.limit,
        min_trust_level=str(filters.get("min_trust_level") or "low"),
        source_type=str(filters.get("source_type") or ""),
        domain=str(filters.get("domain") or ""),
        date_start=str(filters.get("date_start") or ""),
        date_end=str(filters.get("date_end") or ""),
        published_after=str(filters.get("published_after") or ""),
        published_before=str(filters.get("published_before") or ""),
        include_debug=True,
    )
    stages = result.get("debug_trace") or {}
    candidates = stages.get("final_candidates") or []
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
        "stages": stages,
        "score_breakdown": score_breakdown,
        "candidates": candidates,
    }


def _build_group_analysis_debug_response(
    body: RagDebugQueryRequest,
    latency_ms: int,
) -> dict[str, Any]:
    from creatures.nanobot.prompts.skills.group_analysis.local_rag import select_group_analysis_context
    from core.semantic.provider_factory import get_embedding_provider, get_rag_runtime_config, get_reranker_provider

    filters = body.filters or {}
    messages = filters.get("messages") if isinstance(filters.get("messages"), list) else []
    runtime = get_rag_runtime_config("group_analysis")
    result = select_group_analysis_context(
        messages,
        query=body.query,
        bundle_size=int(filters.get("bundle_size") or 8),
        lexical_top_k=int(filters.get("lexical_top_k") or 300),
        reranker_top_k=int(filters.get("reranker_top_k") or 40),
        neighbor_radius=int(filters.get("neighbor_radius") or 1),
        budget_chars=int(filters.get("budget_chars") or 0),
        embedding_provider=get_embedding_provider() if runtime.enabled else None,
        reranker_provider=get_reranker_provider() if runtime.enabled and runtime.reranker_enabled else None,
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
    lexical_candidates = [
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
        for item in prompt_logs.get("lexical_candidates") or []
    ]
    final_candidates = [
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
        for item in prompt_logs.get("selected_bundles") or []
    ]
    return {
        "query": body.query,
        "source_type": "group_analysis",
        "stages": {
            "sql_filters": {key: value for key, value in filters.items() if key != "messages"},
            "stats_logs": stats,
            "prompt_logs": prompt_logs,
            "fts_hits": lexical_candidates,
            "embedding_hits": [],
            "merged_candidates": candidates,
            "reranker_input_pairs": prompt_logs.get("reranker_input_pairs") or [],
            "final_candidates": final_candidates,
        },
        "score_breakdown": {
            "degraded": not bool(prompt_logs.get("reranker_input_pairs")),
            "fallback_reason": "" if prompt_logs.get("reranker_input_pairs") else "local_rag_debug_no_external_reranker",
            "source_weights": {"group_analysis": 1.0},
            "latency_ms": latency_ms,
            **stats,
        },
        "candidates": final_candidates,
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
    if body.source_type in {"memory", "memory_digest", "session_summary"}:
        response_json = _build_memory_debug_response(body, db, latency_ms)
        latency_ms = int((time.perf_counter() - started) * 1000)
        response_json["score_breakdown"]["latency_ms"] = latency_ms
    elif body.source_type == "group_memory":
        response_json = _build_group_memory_debug_response(body, db, latency_ms)
        latency_ms = int((time.perf_counter() - started) * 1000)
        response_json["score_breakdown"]["latency_ms"] = latency_ms
    elif body.source_type == "sticker":
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
    request_payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    request_json = json.dumps(_sanitize_debug_payload(request_payload), ensure_ascii=False)
    run = RagDebugRun(
        trace_id=trace_id,
        source_type=body.source_type,
        query=body.query,
        request_json=request_json,
        response_json=json.dumps(_sanitize_debug_payload(response_json), ensure_ascii=False),
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


@router.get("/debug/status")
def get_rag_debug_status(
    source_type: str = Query(default="all"),
    limit_per_source: int = Query(default=500, ge=1, le=5000),
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.semantic.backfill import preview_semantic_index_backfill
    from core.semantic.provider_factory import describe_reranker_provider_config

    ensure_semantic_schema(db.bind)
    return {
        "source_type": source_type,
        "index": preview_semantic_index_backfill(
            db,
            source_type=source_type,
            limit_per_source=limit_per_source,
        ),
        "reranker": describe_reranker_provider_config(),
    }


@router.post("/debug/build-index")
def build_rag_debug_index(
    body: RagDebugBuildIndexRequest,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.semantic.backfill import (
        build_semantic_index_from_existing_data,
        preview_semantic_index_backfill,
    )

    ensure_semantic_schema(db.bind)
    result = build_semantic_index_from_existing_data(
        db,
        source_type=body.source_type,
        limit_per_source=body.limit_per_source,
        index_version=body.index_version,
    )
    return {
        "ok": True,
        "result": result,
        "index": preview_semantic_index_backfill(
            db,
            source_type=body.source_type,
            limit_per_source=body.limit_per_source,
        ),
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

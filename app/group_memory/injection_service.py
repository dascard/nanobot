"""群体记忆注入服务。"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from core.group_runtime.ids import (
    normalize_group_session_id,
    normalize_group_stream_id,
    raw_group_id,
)
from core.time_utils import db_now_naive

from app.group_memory.renderer import render_group_memory_context
from app.group_memory.retrieval_service import (
    GroupMemoryRerankerTimeout,
    GroupMemoryRetrievalService,
)


GROUP_MEMORY_RAG_CACHE: dict[str, tuple[float, GroupMemoryInjectionResult]] = {}
GROUP_MEMORY_RAG_CACHE_TTL_SECONDS = 120


@dataclass
class GroupMemoryInjectionResult:
    context: str = ""
    selected_ids: list[int] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    score_components: dict[str, dict[str, Any]] = field(default_factory=dict)
    debug: dict[str, Any] = field(default_factory=dict)


def group_memory_config_ids(group_id: str) -> list[str]:
    raw = raw_group_id(group_id)
    candidates = [
        normalize_group_stream_id(raw),
        normalize_group_session_id(raw),
    ]
    return list(dict.fromkeys(x for x in candidates if x))


class GroupMemoryInjectionService:
    def __init__(self, db: Session):
        self.db = db

    def build_context(
        self,
        *,
        group_id: str,
        current_user_input: str = "",
        recent_messages: list[dict[str, Any]] | None = None,
        max_items: int = 10,
        max_chars: int = 1200,
        record_injection: bool = False,
        rag_timeout_ms: int = 1200,
        allow_model_calls: bool = True,
    ) -> GroupMemoryInjectionResult:
        from core.database import ChatStreamConfig

        session_id = normalize_group_session_id(group_id)
        cfg_ids = group_memory_config_ids(group_id)
        cfg_rows = (
            self.db.query(ChatStreamConfig)
            .filter(ChatStreamConfig.chat_stream_id.in_(cfg_ids))
            .all()
            if cfg_ids else None
        )
        rows_by_id = {row.chat_stream_id: row for row in (cfg_rows or [])}
        cfg = next((rows_by_id[candidate] for candidate in cfg_ids if candidate in rows_by_id), None)
        mode = (cfg.group_profile_mode or "off") if cfg else "off"
        debug: dict[str, Any] = {
            "group_profile_mode": mode,
            "group_memory_injected": False,
            "group_memory_ids": [],
            "group_memory_skipped": [],
            "group_memory_context_chars": 0,
            "group_memory_context": "",
            "score_components": {},
            "group_memory_config_ids": cfg_ids,
            "cache_hit": False,
            "cache_key": "",
            "latency_ms": 0,
            "timeout_fallback": False,
            "model_calls_allowed": bool(allow_model_calls),
        }
        if mode not in {"preview", "on"}:
            return GroupMemoryInjectionResult(debug=debug)
        if not str(current_user_input or "").strip() and not (recent_messages or []):
            debug["group_memory_skipped"].append({"reason": "empty_query_context"})
            return GroupMemoryInjectionResult(debug=debug)

        cache_key = _cache_key(session_id, current_user_input, recent_messages or [])
        debug["cache_key"] = cache_key
        from core.semantic.provider_factory import (
            get_rag_runtime_config,
            get_reranker_provider,
        )

        runtime = get_rag_runtime_config("group_memory")
        if runtime.enabled and runtime.reranker_enabled and not allow_model_calls:
            debug["group_memory_skipped"].append({
                "reason": "model_calls_forbidden",
            })
            debug["model_dependent_retrieval_skipped"] = True
            return GroupMemoryInjectionResult(debug=debug)
        if not record_injection:
            cached = GROUP_MEMORY_RAG_CACHE.get(cache_key)
            if cached and cached[0] > time.monotonic():
                cached_result = copy.deepcopy(cached[1])
                cached_result.debug["cache_hit"] = True
                cached_result.debug["model_calls_allowed"] = bool(
                    allow_model_calls
                )
                return cached_result

        started = time.perf_counter()
        reranker_provider = get_reranker_provider() if runtime.enabled and runtime.reranker_enabled else None
        if runtime.enabled and runtime.reranker_enabled and reranker_provider is None and not runtime.allow_degraded:
            debug["group_memory_skipped"].append({"reason": "reranker_unavailable"})
            debug["degraded_blocked"] = True
            debug["blocked_reason"] = "reranker_unavailable"
            return GroupMemoryInjectionResult(debug=debug)
        try:
            selection = GroupMemoryRetrievalService(
                self.db,
                reranker_provider=reranker_provider,
                reranker_timeout_ms=rag_timeout_ms,
            ).select(
                group_id=session_id,
                current_user_input=current_user_input,
                recent_messages=recent_messages or [],
                max_items=max_items,
                max_chars=max_chars,
            )
        except GroupMemoryRerankerTimeout:
            debug["latency_ms"] = int((time.perf_counter() - started) * 1000)
            debug["timeout_fallback"] = True
            debug["timeout_stage"] = "reranker"
            debug["group_memory_skipped"].append({"reason": "reranker_timeout"})
            return GroupMemoryInjectionResult(debug=debug)
        latency_ms = int((time.perf_counter() - started) * 1000)
        debug["latency_ms"] = latency_ms
        if latency_ms > int(rag_timeout_ms):
            debug["timeout_fallback"] = True
            return GroupMemoryInjectionResult(
                selected_ids=selection.selected_ids,
                skipped=selection.skipped,
                score_components=selection.score_components,
                debug=debug,
            )
        context = render_group_memory_context(session_id, selection.selected)
        debug.update({
            "group_memory_ids": selection.selected_ids,
            "group_memory_skipped": selection.skipped,
            "group_memory_context": context,
            "group_memory_context_chars": len(context),
            "score_components": selection.score_components,
        })
        if mode == "on" and context:
            debug["group_memory_injected"] = True
            if record_injection:
                self.record_injected(selection.selected_ids)
            result = GroupMemoryInjectionResult(
                context=context,
                selected_ids=selection.selected_ids,
                skipped=selection.skipped,
                score_components=selection.score_components,
                debug=debug,
            )
            if not record_injection:
                GROUP_MEMORY_RAG_CACHE[cache_key] = (
                    time.monotonic() + GROUP_MEMORY_RAG_CACHE_TTL_SECONDS,
                    result,
                )
            return result
        result = GroupMemoryInjectionResult(
            context="",
            selected_ids=selection.selected_ids,
            skipped=selection.skipped,
            score_components=selection.score_components,
            debug=debug,
        )
        if not record_injection:
            GROUP_MEMORY_RAG_CACHE[cache_key] = (
                time.monotonic() + GROUP_MEMORY_RAG_CACHE_TTL_SECONDS,
                result,
            )
        return result

    def record_injected(self, selected_ids: list[int]) -> int:
        return record_group_memory_injected(self.db, selected_ids)


def record_group_memory_injected(db: Session, selected_ids: list[int]) -> int:
    ids: list[int] = []
    for item in selected_ids or []:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value > 0:
            ids.append(value)
    if not ids:
        return 0
    from core.database import GroupMemory

    now = db_now_naive()
    rows = db.query(GroupMemory).filter(GroupMemory.id.in_(ids)).all()
    for row in rows:
        row.last_injected_at = now
        row.injected_count = int(row.injected_count or 0) + 1
    db.flush()
    return len(rows)


def _cache_key(
    group_id: str,
    current_user_input: str,
    recent_messages: list[dict[str, Any]],
) -> str:
    payload = {
        "group_id": group_id,
        "current_user_input": current_user_input or "",
        "recent_messages": [
            {
                "sender": str(item.get("sender_name") or item.get("user_id") or ""),
                "content": str(item.get("content") or ""),
            }
            for item in recent_messages[-10:]
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

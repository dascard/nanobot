"""RAG 服务到 benchmark 统一结果的 adapter。"""

from __future__ import annotations

import time
import hashlib
from typing import Any

from sqlalchemy.orm import Session

from evals.rag_benchmark.schema import BenchmarkCandidate, BenchmarkCase, BenchmarkResult


PROVIDER_MODES = {"deterministic", "no_reranker_baseline", "runtime"}


class _RuntimeConfig:
    reranker_enabled = True
    allow_degraded = True
    enabled = True


class DeterministicEmbeddingProvider:
    """只依赖文本内容的轻量 embedding，供 benchmark 回归模式使用。"""

    model_name = "benchmark-deterministic-embedding"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(str(text or "").encode("utf-8")).digest()
        values = []
        for index in range(0, 32, 4):
            chunk = int.from_bytes(digest[index:index + 4], "big")
            values.append((chunk % 2000) / 1000.0 - 1.0)
        return values


class DeterministicRerankerProvider:
    """确定性 reranker；不读取 expected，只根据 query 与候选内容打分。"""

    model_name = "benchmark-deterministic-reranker"
    score_mode = "lexical_hash"

    def rerank(self, query: str, candidates: list[Any], *, top_k: int | None = None) -> list[Any]:
        from core.semantic.reranker import RerankResult, build_reranker_text
        from core.semantic.retriever import lexical_overlap_score

        scored = []
        for candidate in candidates:
            text = build_reranker_text(candidate)
            lexical = lexical_overlap_score(query, text)
            tie = int(hashlib.sha256(candidate.candidate_id.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
            score = min(1.0, max(0.0, 0.15 + lexical * 0.80 + tie * 0.0001))
            scored.append(RerankResult(
                candidate_id=candidate.candidate_id,
                raw_score=score,
                score=score,
                model=self.model_name,
                score_mode=self.score_mode,
            ))
        scored.sort(key=lambda item: (item.score or 0.0, item.candidate_id), reverse=True)
        return scored[:top_k] if top_k else scored


def _provider_mode(*, use_runtime_providers: bool | None, provider_mode: str | None) -> str:
    if provider_mode:
        mode = str(provider_mode)
    elif use_runtime_providers is None:
        mode = "deterministic"
    else:
        mode = "runtime" if use_runtime_providers else "no_reranker_baseline"
    if mode not in PROVIDER_MODES:
        raise ValueError(f"unsupported provider_mode: {mode}")
    return mode


def _runtime_providers(source_type: str, *, provider_mode: str) -> tuple[Any, Any, Any]:
    if provider_mode == "deterministic":
        return _RuntimeConfig(), DeterministicEmbeddingProvider(), DeterministicRerankerProvider()
    if provider_mode == "no_reranker_baseline":
        return None, None, None
    from core.semantic.provider_factory import get_embedding_provider, get_rag_runtime_config, get_reranker_provider

    runtime = get_rag_runtime_config(source_type)
    embedding = get_embedding_provider()
    reranker = get_reranker_provider() if runtime.reranker_enabled else None
    return runtime, embedding, reranker


def _score(item: dict[str, Any]) -> float | None:
    value = item.get("final_score")
    if value is None:
        value = item.get("score")
    if value is None and isinstance(item.get("score_breakdown"), dict):
        value = item["score_breakdown"].get("final")
    try:
        return float(value) if value is not None else None
    except Exception:
        return None


def _preview(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _candidates(items: list[dict[str, Any]], *, fallback_source_type: str) -> list[BenchmarkCandidate]:
    result: list[BenchmarkCandidate] = []
    for rank, item in enumerate(items, start=1):
        candidate_id = str(item.get("candidate_id") or "")
        if not candidate_id:
            continue
        citation = item.get("citation")
        send_code = str(item.get("send_code") or "")
        reply_token = str(item.get("reply_token") or "")
        result.append(BenchmarkCandidate(
            candidate_id=candidate_id,
            source_type=str(item.get("source_type") or fallback_source_type),
            rank=rank,
            score=_score(item),
            title=_preview(item.get("title") or "", 120),
            text_preview=_preview(item.get("text") or item.get("description") or item.get("content") or ""),
            document_id=item.get("document_id"),
            chunk_id=str(item.get("chunk_id") or "") or None,
            group_id=str(item.get("group_id") or ""),
            sendable=True if (send_code or reply_token) else None,
            citation=bool(citation) if citation is not None else None,
            score_breakdown=item.get("score_breakdown") if isinstance(item.get("score_breakdown"), dict) else {},
        ))
    return result


def _result_from_debug(
    *,
    case: BenchmarkCase,
    raw: dict[str, Any],
    fallback_source_type: str,
    latency_ms: int,
) -> BenchmarkResult:
    stages = raw.get("debug_trace") or {}
    stats = raw.get("stats") or {}
    candidates = _candidates(stages.get("final_candidates") or [], fallback_source_type=fallback_source_type)
    return BenchmarkResult(
        case_id=case.id,
        source_type=case.source_type,
        candidate_ids=[candidate.candidate_id for candidate in candidates],
        candidates=candidates,
        merged_candidates_count=int(stats.get("merged_candidates") or len(stages.get("merged_candidates") or [])),
        reranker_candidates_count=int(stats.get("reranker_candidates") or len(stages.get("reranker_input_pairs") or [])),
        degraded=bool(raw.get("degraded")),
        fallback_reason=str(raw.get("fallback_reason") or ""),
        latency_ms=latency_ms,
        reranker_latency_ms=stats.get("reranker_latency_ms"),
        debug_summary={
            "stats": stats,
            "final_items": stats.get("final_items"),
        },
    )


def _run_memory(db: Session, case: BenchmarkCase, *, provider_mode: str, readonly: bool) -> BenchmarkResult:
    from core.memory_rag import MemoryRagService

    runtime, embedding, reranker = _runtime_providers("memory", provider_mode=provider_mode)
    started = time.perf_counter()
    raw = MemoryRagService(
        db,
        embedding_provider=embedding,
        reranker_provider=reranker,
        allow_degraded=bool(getattr(runtime, "allow_degraded", True)),
        readonly=readonly,
    ).query(
        case.query,
        source=str(case.filters.get("source") or "all"),
        user_id=str(case.filters.get("user_id") or ""),
        session_id=str(case.filters.get("session_id") or ""),
        limit=int(case.expected.hit_at or 5),
        include_debug=True,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    return _result_from_debug(case=case, raw=raw, fallback_source_type="memory_digest", latency_ms=latency_ms)


def _run_sticker(db: Session, case: BenchmarkCase, *, provider_mode: str, readonly: bool) -> BenchmarkResult:
    from core.sticker_rag import StickerRagService

    _runtime, embedding, reranker = _runtime_providers("sticker", provider_mode=provider_mode)
    started = time.perf_counter()
    raw = StickerRagService(
        db,
        embedding_provider=embedding,
        reranker_provider=reranker,
        readonly=readonly,
    ).query(
        case.query,
        group_id=str(case.filters.get("group_id") or ""),
        chat_stream_id=str(case.filters.get("chat_stream_id") or ""),
        include_global=bool(case.filters.get("include_global", True)),
        limit=int(case.expected.hit_at or 5),
        include_debug=True,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    return _result_from_debug(case=case, raw=raw if isinstance(raw, dict) else {}, fallback_source_type="sticker", latency_ms=latency_ms)


def _run_knowledge(db: Session, case: BenchmarkCase, *, provider_mode: str, readonly: bool) -> BenchmarkResult:
    from core.knowledge_rag import KnowledgeRagService

    _runtime, embedding, reranker = _runtime_providers("knowledge", provider_mode=provider_mode)
    started = time.perf_counter()
    raw = KnowledgeRagService(
        db,
        embedding_provider=embedding,
        reranker_provider=reranker,
        readonly=readonly,
    ).query(
        case.query,
        limit=int(case.expected.hit_at or 5),
        min_trust_level=str(case.filters.get("min_trust_level") or "low"),
        source_type=str(case.filters.get("source_type") or ""),
        domain=str(case.filters.get("domain") or ""),
        date_start=str(case.filters.get("date_start") or ""),
        date_end=str(case.filters.get("date_end") or ""),
        published_after=str(case.filters.get("published_after") or ""),
        published_before=str(case.filters.get("published_before") or ""),
        include_debug=True,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    return _result_from_debug(case=case, raw=raw, fallback_source_type="knowledge", latency_ms=latency_ms)


def _run_group_memory(db: Session, case: BenchmarkCase, *, provider_mode: str) -> BenchmarkResult:
    from app.group_memory.retrieval_service import GroupMemoryRetrievalService
    from core.database import GroupMemory

    _runtime, _embedding, reranker = _runtime_providers("group_memory", provider_mode=provider_mode)
    started = time.perf_counter()
    selection = GroupMemoryRetrievalService(db, reranker_provider=reranker).select(
        group_id=str(case.filters.get("group_id") or ""),
        current_user_input=case.query,
        recent_messages=case.filters.get("recent_messages") if isinstance(case.filters.get("recent_messages"), list) else [],
        max_items=int(case.expected.hit_at or 5),
        max_chars=int(case.filters.get("max_chars") or 1200),
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    scored_ids = [int(key) for key in selection.score_components if str(key).isdigit()]
    rows_by_id = {
        int(row.id): row
        for row in db.query(GroupMemory).filter(GroupMemory.id.in_(scored_ids)).all()
    } if scored_ids else {}
    candidates = [
        BenchmarkCandidate(
            candidate_id=f"group_memory:{row.id}:memory",
            source_type="group_memory",
            rank=rank,
            score=(selection.score_components.get(str(row.id)) or {}).get("final"),
            title=str(row.memory_type or ""),
            text_preview=_preview(row.content),
            group_id=str(row.group_id or ""),
            score_breakdown=selection.score_components.get(str(row.id)) or {},
        )
        for rank, row in enumerate(selection.selected, start=1)
    ]
    return BenchmarkResult(
        case_id=case.id,
        source_type=case.source_type,
        candidate_ids=[candidate.candidate_id for candidate in candidates],
        candidates=candidates,
        merged_candidates_count=len(selection.score_components),
        reranker_candidates_count=len(rows_by_id) if reranker is not None else 0,
        degraded=reranker is None,
        fallback_reason="reranker_unavailable" if reranker is None else "",
        latency_ms=latency_ms,
        debug_summary={"skipped": selection.skipped, "score_components": selection.score_components},
    )


def run_case_with_adapter(
    db: Session,
    case: BenchmarkCase,
    *,
    use_runtime_providers: bool | None = None,
    provider_mode: str | None = None,
    readonly: bool = True,
) -> BenchmarkResult:
    mode = _provider_mode(use_runtime_providers=use_runtime_providers, provider_mode=provider_mode)
    if case.source_type == "memory":
        return _run_memory(db, case, provider_mode=mode, readonly=readonly)
    if case.source_type == "sticker":
        return _run_sticker(db, case, provider_mode=mode, readonly=readonly)
    if case.source_type == "knowledge":
        return _run_knowledge(db, case, provider_mode=mode, readonly=readonly)
    if case.source_type == "group_memory":
        return _run_group_memory(db, case, provider_mode=mode)
    raise ValueError(f"unsupported source_type: {case.source_type}")

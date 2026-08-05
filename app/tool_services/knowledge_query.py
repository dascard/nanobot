"""外部知识库查询的框架无关应用服务。"""

from __future__ import annotations

import json
from typing import Any

from core.tool_contracts.result import ToolServiceResult
from core.uow import UnitOfWork


def _search(
    service: Any,
    args: dict[str, Any],
    limit: int,
    *,
    allow_degraded: bool,
    access_context=None,
) -> ToolServiceResult:
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolServiceResult(error="search mode requires query")
    result = service.query(
        query,
        limit=limit,
        min_trust_level=str(args.get("min_trust_level") or "low"),
        source_type=str(args.get("source_type") or ""),
        domain=str(args.get("domain") or ""),
        date_start=str(args.get("date_start") or ""),
        date_end=str(args.get("date_end") or ""),
        published_after=str(args.get("published_after") or ""),
        published_before=str(args.get("published_before") or ""),
        access_context=access_context,
    )
    if result.get("degraded") and not allow_degraded:
        from core.semantic.provider_factory import degraded_error

        return ToolServiceResult(
            error=degraded_error(
                "knowledge",
                str(result.get("fallback_reason") or ""),
            )
        )
    items = result.get("items") or []
    if not items:
        return ToolServiceResult(
            output=f"未找到与 {query} 相关的外部知识。",
            exit_code=0,
            metadata={"structured_content": {"mode": "search", **result}},
        )
    lines = [
        "knowledge_query search: "
        f"query={query} count={len(items)} degraded={result.get('degraded')}"
    ]
    for item in items:
        citation = item.get("citation") or {}
        lines.append(
            f"- document_id={item.get('document_id')} "
            f"chunk_id={item.get('chunk_id')} "
            f"trust={item.get('trust_level')} "
            f"title={citation.get('title')}: "
            f"{str(item.get('text') or '')[:240]}"
        )
    return ToolServiceResult(
        output="\n".join(lines),
        exit_code=0,
        metadata={"structured_content": {"mode": "search", **result}},
    )


def _expand(
    service: Any,
    args: dict[str, Any],
    *,
    access_context=None,
) -> ToolServiceResult:
    document_id = args.get("document_id")
    chunk_id = str(args.get("chunk_id") or "").strip()
    if not document_id or not chunk_id:
        return ToolServiceResult(
            error="expand mode requires document_id and chunk_id"
        )
    expanded = service.expand(
        document_id=int(document_id),
        chunk_id=chunk_id,
        access_context=access_context,
    )
    return ToolServiceResult(
        output=json.dumps(expanded, ensure_ascii=False),
        exit_code=0,
        metadata={"structured_content": {"mode": "expand", **expanded}},
    )


async def execute_knowledge_query(args: dict[str, Any]) -> ToolServiceResult:
    mode = str(args.get("mode") or "search").strip()
    limit = max(1, min(int(args.get("limit") or 5), 10))
    try:
        from core.memory_governance import current_or_runtime_memory_access

        access_context = current_or_runtime_memory_access()
        with UnitOfWork() as uow:
            if uow.db is None:
                return ToolServiceResult(error="database session is unavailable")
            from core.knowledge_rag import KnowledgeRagService
            from core.semantic.provider_factory import (
                get_embedding_provider,
                get_rag_runtime_config,
                get_reranker_provider,
            )

            runtime = get_rag_runtime_config("knowledge")
            if mode == "search" and not runtime.enabled:
                return ToolServiceResult(error="knowledge RAG is disabled")
            service = KnowledgeRagService(
                uow.db,
                embedding_provider=get_embedding_provider(),
                reranker_provider=(
                    get_reranker_provider()
                    if runtime.reranker_enabled
                    else None
                ),
            )
            if mode == "search":
                return _search(
                    service,
                    args,
                    limit,
                    allow_degraded=runtime.allow_degraded,
                    access_context=access_context,
                )
            if mode == "expand":
                return _expand(
                    service,
                    args,
                    access_context=access_context,
                )
            return ToolServiceResult(error=f"Unsupported mode: {mode}")
    except Exception as exc:
        return ToolServiceResult(error=f"knowledge_query failed: {exc}")


__all__ = ["execute_knowledge_query"]

"""Memory Query 的 KT 工具适配与执行实现。"""

from __future__ import annotations

import json
import time
from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

from app.memory_digest.retrieval_service import (
    MemoryDigestRetrievalService,
    validate_digest_date,
    validate_digest_date_range,
)
from app.session_memory.retrieval_service import SessionSummaryRetrievalService
from core.uow import UnitOfWork


class MemoryQueryTool(BaseTool):
    """按关键词、时间或 digest_id 查询摘要卡片。"""

    @property
    def tool_name(self) -> str:
        return "memory_query"

    @property
    def description(self) -> str:
        return (
            "查询已生成的长期/中期聊天摘要和召回卡片。"
            "只返回结构化摘要、预览和展开摘要，不返回原始 ChatLog 全文。"
            "它只覆盖已经被摘要过的历史；当前短期窗口或未摘要消息必须用 sql_analysis 查询原始日志。"
            "历史摘要不会自动注入；当本轮问题明确依赖较早讨论、某个时期或既往决定时，由你判断是否查询。"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["search", "time", "expand", "aggregate"],
                    "description": "search=关键词检索；time=按时间列出；expand=按 digest_id 展开；aggregate=聚合预览。",
                },
                "source": {
                    "type": "string",
                    "enum": ["digest", "session_summary", "all"],
                    "description": "digest=跨天/中期摘要；session_summary=当前 session rolling summary；all=两类摘要统一 RAG 搜索。默认 digest。",
                },
                "query": {
                    "type": "string",
                    "description": "关键词。search 模式必填。",
                },
                "session_id": {
                    "type": "string",
                    "description": "会话 ID，例如 group_1097666427 或 private_0000000000。",
                },
                "user_id": {
                    "type": "string",
                    "description": "用户或群实体 ID；不确定时优先传 session_id。",
                },
                "digest_id": {
                    "type": "integer",
                    "description": "expand 模式要展开的摘要 ID。",
                },
                "summary_id": {
                    "type": "integer",
                    "description": "source=session_summary 时 expand 要展开的 rolling summary ID。",
                },
                "digest_date": {
                    "type": "string",
                    "description": "指定 YYYY-MM-DD 日期。",
                },
                "date_start": {
                    "type": "string",
                    "description": "范围开始日期，YYYY-MM-DD。",
                },
                "date_end": {
                    "type": "string",
                    "description": "范围结束日期，YYYY-MM-DD。",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数，默认 5，最大 10。",
                    "minimum": 1,
                    "maximum": 10,
                },
                "include_detail": {
                    "type": "boolean",
                    "description": "是否包含详细摘要层。默认 false；不会包含原始 ChatLog。",
                },
                "include_legacy": {
                    "type": "boolean",
                    "description": "是否包含旧格式摘要。默认 false。",
                },
            },
            "required": ["mode"],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        del kwargs
        from nanobot_kt.memory_runtime import (
            dispatch_memory_tool_call,
            has_memory_tool_runtime_binding,
            provider_result_to_tool_result,
        )

        if has_memory_tool_runtime_binding():
            result = await dispatch_memory_tool_call(self.tool_name, args)
            return provider_result_to_tool_result(result)
        return await execute_memory_query(args, adapter=self)

    def _rag_search(
        self, db, args: dict[str, Any], limit: int, *, source: str
    ) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(error="search mode requires query")
        from core.memory_rag import MemoryRagService
        from core.semantic.provider_factory import (
            degraded_error,
            get_embedding_provider,
            get_rag_runtime_config,
            get_reranker_provider,
        )

        runtime = get_rag_runtime_config("memory")
        started = time.perf_counter()
        result = MemoryRagService(
            db,
            embedding_provider=get_embedding_provider(),
            reranker_provider=get_reranker_provider(),
            allow_degraded=runtime.allow_degraded,
        ).query(
            query,
            source=source,
            user_id=str(args.get("user_id") or "").strip(),
            session_id=str(args.get("session_id") or "").strip(),
            limit=limit,
        )
        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        self._record_rag_query(
            db,
            query=query,
            source=source,
            args=args,
            limit=limit,
            result=result,
            latency_ms=latency_ms,
        )
        if result.get("degraded") and not runtime.allow_degraded:
            return ToolResult(
                error=degraded_error("memory", str(result.get("fallback_reason") or ""))
            )
        items = result.get("items") or []
        if not items:
            return ToolResult(
                output=f"未找到与 {query} 相关的摘要。",
                exit_code=0,
                metadata={
                    "structured_content": {"mode": "search", "source": source, **result}
                },
            )
        lines = [
            f"memory_query rag search: source={source} query={query} "
            f"count={len(items)} degraded={result.get('degraded')}"
        ]
        for item in items:
            best = (item.get("matched_cards") or [{}])[0]
            identifier = (
                item.get("digest_id") or item.get("summary_id") or item.get("source_id")
            )
            lines.append(
                f"- source={item.get('source')} id={identifier} score={float(item.get('parent_score') or 0):.3f}: "
                f"{str(best.get('text') or '')[:260]}"
            )
        return ToolResult(
            output="\n".join(lines),
            exit_code=0,
            metadata={
                "structured_content": {"mode": "search", "source": source, **result}
            },
        )

    @staticmethod
    def _has_rag_index(db, source: str) -> bool:
        from core.database import SemanticIndexItem
        from core.semantic.adapters import is_recallable_memory_digest_meta

        if source == "digest":
            source_types = {"memory_digest"}
        elif source == "session_summary":
            source_types = {"session_summary"}
        else:
            source_types = {"memory_digest", "session_summary"}
        rows = (
            db.query(SemanticIndexItem.source_type, SemanticIndexItem.meta_json)
            .filter(SemanticIndexItem.source_type.in_(sorted(source_types)))
            .filter(SemanticIndexItem.status == "active")
            .filter(SemanticIndexItem.visibility == "recall")
            .yield_per(200)
        )
        for source_type, meta_json in rows:
            if source_type == "session_summary":
                return True
            try:
                meta = json.loads(meta_json or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(meta, dict) and is_recallable_memory_digest_meta(meta):
                return True
        return False

    @staticmethod
    def _record_rag_query(
        db,
        *,
        query: str,
        source: str,
        args: dict[str, Any],
        limit: int,
        result: dict[str, Any],
        latency_ms: int,
    ) -> None:
        """记录真实 memory_query 消费，不持久化召回正文。"""

        try:
            from core.database import RagDebugRun
            from core.tracing_context import get_trace_context

            trace_id, run_id = get_trace_context()
            selected: list[dict[str, Any]] = []
            for item in list(result.get("items") or []):
                if not isinstance(item, dict):
                    continue
                card_ids = [
                    str(card.get("candidate_id") or "")
                    for card in list(item.get("matched_cards") or [])
                    if isinstance(card, dict) and str(card.get("candidate_id") or "")
                ]
                selected.append(
                    {
                        "source_type": str(
                            item.get("source_type") or item.get("source") or ""
                        ),
                        "source_id": str(item.get("source_id") or ""),
                        "digest_id": item.get("digest_id"),
                        "summary_id": item.get("summary_id"),
                        "candidate_ids": card_ids,
                        "parent_score": float(item.get("parent_score") or 0.0),
                    }
                )
            request_json = json.dumps(
                {
                    "mode": "runtime_memory_query",
                    "source": source,
                    "user_id": str(args.get("user_id") or ""),
                    "session_id": str(args.get("session_id") or ""),
                    "limit": int(limit),
                    "run_id": run_id,
                },
                ensure_ascii=False,
            )
            response_json = json.dumps(
                {
                    "stats": dict(result.get("stats") or {}),
                    "selected": selected,
                },
                ensure_ascii=False,
            )
            db.add(
                RagDebugRun(
                    trace_id=trace_id,
                    source_type="memory_query",
                    query=str(query or "")[:2000],
                    request_json=request_json,
                    response_json=response_json,
                    degraded=1 if result.get("degraded") else 0,
                    fallback_reason=str(result.get("fallback_reason") or "")[:1000],
                    latency_ms=max(0, int(latency_ms)),
                )
            )
            db.commit()
        except Exception:
            db.rollback()

    @staticmethod
    def _validate_date_args(args: dict[str, Any]) -> None:
        validate_digest_date(str(args.get("digest_date") or "").strip(), "digest_date")
        validate_digest_date_range(
            str(args.get("date_start") or "").strip(),
            str(args.get("date_end") or "").strip(),
        )

    def _search(
        self,
        service: MemoryDigestRetrievalService,
        args: dict[str, Any],
        limit: int,
        include_detail: bool,
        include_legacy: bool,
    ) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(error="search mode requires query")
        date_start = str(args.get("date_start") or "").strip()
        date_end = str(args.get("date_end") or "").strip()
        results = service.recall(
            keyword=query,
            user_id=str(args.get("user_id") or "").strip(),
            session_id=str(args.get("session_id") or "").strip(),
            digest_date=str(args.get("digest_date") or "").strip(),
            date_start=date_start,
            date_end=date_end,
            limit=limit,
            reveal_to_level=0 if include_detail else 2,
            include_content=include_detail,
            include_legacy=include_legacy,
        )
        if not results:
            return ToolResult(
                output=f"未找到与 {query} 相关的摘要。",
                exit_code=0,
                metadata={
                    "structured_content": {
                        "mode": "search",
                        "query": query,
                        "date_start": date_start,
                        "date_end": date_end,
                        "items": [],
                    }
                },
            )
        lines = [f"memory_query search: query={query} count={len(results)}"]
        for item in results:
            meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            preview = (
                meta.get("preview") if isinstance(meta.get("preview"), dict) else {}
            )
            cards = (
                meta.get("recall_cards")
                if isinstance(meta.get("recall_cards"), list)
                else []
            )
            card_text = ""
            if cards and isinstance(cards[0], dict):
                card_text = str(cards[0].get("text") or "").strip()
            brief = str(preview.get("brief") or card_text or "").strip()
            lines.append(
                f"- digest_id={item.get('digest_id')} date={item.get('digest_date')} "
                f"session={item.get('session_id')} confidence={item.get('confidence')}: {brief[:260]}"
            )
        return ToolResult(
            output="\n".join(lines),
            exit_code=0,
            metadata={
                "structured_content": {
                    "mode": "search",
                    "query": query,
                    "date_start": date_start,
                    "date_end": date_end,
                    "items": results,
                }
            },
        )

    def _time(
        self,
        service: MemoryDigestRetrievalService,
        args: dict[str, Any],
        limit: int,
        include_detail: bool,
        include_legacy: bool,
    ) -> ToolResult:
        rows = service.list_digests(
            user_id=str(args.get("user_id") or "").strip(),
            session_id=str(args.get("session_id") or "").strip(),
            digest_date=str(args.get("digest_date") or "").strip(),
            date_start=str(args.get("date_start") or "").strip(),
            date_end=str(args.get("date_end") or "").strip(),
            level=2,
            limit=limit,
            include_content=include_detail,
            include_legacy=include_legacy,
        )
        if not rows:
            return ToolResult(
                output="未找到符合条件的摘要。",
                exit_code=0,
                metadata={
                    "structured_content": {
                        "mode": "time",
                        "date_start": str(args.get("date_start") or "").strip(),
                        "date_end": str(args.get("date_end") or "").strip(),
                        "items": [],
                    }
                },
            )
        lines = [f"memory_query time: count={len(rows)}"]
        for row in rows:
            meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
            preview = (
                meta.get("preview") if isinstance(meta.get("preview"), dict) else {}
            )
            lines.append(
                f"- digest_id={row.get('id')} date={row.get('digest_date')} "
                f"session={row.get('session_id')} status={row.get('status')}: "
                f"{str(preview.get('brief') or row.get('content') or '')[:260]}"
            )
        return ToolResult(
            output="\n".join(lines),
            exit_code=0,
            metadata={
                "structured_content": {
                    "mode": "time",
                    "date_start": str(args.get("date_start") or "").strip(),
                    "date_end": str(args.get("date_end") or "").strip(),
                    "items": rows,
                }
            },
        )

    def _expand(
        self,
        service: MemoryDigestRetrievalService,
        args: dict[str, Any],
        include_detail: bool,
        include_legacy: bool,
    ) -> ToolResult:
        digest_source_id = args.get("digest_source_id") or args.get("source_id")
        digest_id = args.get("digest_id")

        if digest_source_id:
            # 优先按 digest_source_id 展开（聚合同 source 所有行）
            item = service.expand_by_source(
                source_id=str(digest_source_id),
                include_detail=include_detail,
                include_legacy=include_legacy,
            )
        elif digest_id is not None:
            # 回退：按 row id 展开单条
            item = service.expand_digest(
                digest_id=int(digest_id),
                include_detail=include_detail,
                include_legacy=include_legacy,
            )
        else:
            return ToolResult(
                error="expand mode requires digest_id or digest_source_id"
            )

        if not item:
            identifier = digest_source_id or digest_id
            return ToolResult(
                output=f"未找到可展开的摘要 id={identifier}。",
                exit_code=0,
                metadata={
                    "structured_content": {
                        "mode": "expand",
                        "digest_id": digest_id,
                        "digest_source_id": digest_source_id,
                        "item": None,
                    }
                },
            )
        lines = [
            f"memory_query expand: digest_id={item.get('digest_id')} source_id={item.get('digest_source_id')} date={item.get('digest_date')} session={item.get('session_id')}",
            "preview:",
            str(item.get("preview") or {}),
            "long_summary:",
            str(item.get("long_summary") or {}),
            "recall_cards:",
            str(item.get("recall_cards") or []),
        ]
        if include_detail:
            lines.append("detail_chain:")
            lines.append(str(item.get("chain") or []))
        return ToolResult(
            output="\n".join(lines),
            exit_code=0,
            metadata={
                "structured_content": {
                    "mode": "expand",
                    "digest_id": item.get("digest_id"),
                    "digest_source_id": item.get("digest_source_id"),
                    "item": item,
                }
            },
        )

    def _aggregate(
        self,
        service: MemoryDigestRetrievalService,
        args: dict[str, Any],
        limit: int,
        include_legacy: bool,
    ) -> ToolResult:
        rows = service.list_digests(
            user_id=str(args.get("user_id") or "").strip(),
            session_id=str(args.get("session_id") or "").strip(),
            digest_date=str(args.get("digest_date") or "").strip(),
            date_start=str(args.get("date_start") or "").strip(),
            date_end=str(args.get("date_end") or "").strip(),
            level=2,
            limit=limit,
            include_content=False,
            include_legacy=include_legacy,
        )
        if not rows:
            return ToolResult(
                output="没有可聚合的摘要。",
                exit_code=0,
                metadata={
                    "structured_content": {
                        "mode": "aggregate",
                        "date_start": str(args.get("date_start") or "").strip(),
                        "date_end": str(args.get("date_end") or "").strip(),
                        "items": [],
                    }
                },
            )
        lines = [f"memory_query aggregate: count={len(rows)}"]
        for row in rows:
            meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
            preview = (
                meta.get("preview") if isinstance(meta.get("preview"), dict) else {}
            )
            keywords = (
                preview.get("keywords")
                if isinstance(preview.get("keywords"), list)
                else []
            )
            lines.append(
                f"- {row.get('digest_date')} digest_id={row.get('id')}: "
                f"{str(preview.get('brief') or '')[:180]} keywords={keywords[:8]}"
            )
        return ToolResult(
            output="\n".join(lines),
            exit_code=0,
            metadata={
                "structured_content": {
                    "mode": "aggregate",
                    "date_start": str(args.get("date_start") or "").strip(),
                    "date_end": str(args.get("date_end") or "").strip(),
                    "items": rows,
                }
            },
        )

    def _session_search(
        self,
        service: SessionSummaryRetrievalService,
        args: dict[str, Any],
        limit: int,
        include_detail: bool,
        include_archived: bool,
    ) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(error="search mode requires query")
        rows = service.search(
            keyword=query,
            user_id=str(args.get("user_id") or "").strip(),
            session_id=str(args.get("session_id") or "").strip(),
            date_start=str(args.get("date_start") or "").strip(),
            date_end=str(args.get("date_end") or "").strip(),
            limit=limit,
            include_content=include_detail,
            include_archived=include_archived,
        )
        if not rows:
            return ToolResult(
                output=f"未找到与 {query} 相关的 session summary。",
                exit_code=0,
                metadata={
                    "structured_content": {
                        "mode": "search",
                        "source": "session_summary",
                        "query": query,
                        "items": [],
                    }
                },
            )
        lines = [
            f"memory_query session_summary search: query={query} count={len(rows)}"
        ]
        for row in rows:
            preview = str(row.get("summary_text") or row.get("preview") or "").strip()
            lines.append(
                f"- summary_id={row.get('summary_id')} kind={row.get('summary_kind')} "
                f"session={row.get('session_id')} covered_until={row.get('covered_until_turn_id')}: "
                f"{preview[:260]}"
            )
        return ToolResult(
            output="\n".join(lines),
            exit_code=0,
            metadata={
                "structured_content": {
                    "mode": "search",
                    "source": "session_summary",
                    "query": query,
                    "items": rows,
                }
            },
        )

    def _session_time(
        self,
        service: SessionSummaryRetrievalService,
        args: dict[str, Any],
        limit: int,
        include_detail: bool,
        include_archived: bool,
    ) -> ToolResult:
        rows = service.list_summaries(
            user_id=str(args.get("user_id") or "").strip(),
            session_id=str(args.get("session_id") or "").strip(),
            date_start=str(args.get("date_start") or "").strip(),
            date_end=str(args.get("date_end") or "").strip(),
            limit=limit,
            include_content=include_detail,
            include_archived=include_archived,
        )
        if not rows:
            return ToolResult(
                output="未找到符合条件的 session summary。",
                exit_code=0,
                metadata={
                    "structured_content": {
                        "mode": "time",
                        "source": "session_summary",
                        "items": [],
                    }
                },
            )
        lines = [f"memory_query session_summary time: count={len(rows)}"]
        for row in rows:
            preview = str(row.get("summary_text") or row.get("preview") or "").strip()
            lines.append(
                f"- summary_id={row.get('summary_id')} kind={row.get('summary_kind')} "
                f"session={row.get('session_id')} status={row.get('status')}: {preview[:240]}"
            )
        return ToolResult(
            output="\n".join(lines),
            exit_code=0,
            metadata={
                "structured_content": {
                    "mode": "time",
                    "source": "session_summary",
                    "items": rows,
                }
            },
        )

    def _session_expand(
        self,
        service: SessionSummaryRetrievalService,
        args: dict[str, Any],
        include_archived: bool,
    ) -> ToolResult:
        summary_id = args.get("summary_id", args.get("digest_id"))
        if summary_id is None:
            return ToolResult(error="expand mode requires summary_id")
        item = service.expand_summary(
            summary_id=int(summary_id),
            include_archived=include_archived,
        )
        if not item:
            return ToolResult(
                output=f"未找到可展开的 session summary summary_id={summary_id}。",
                exit_code=0,
                metadata={
                    "structured_content": {
                        "mode": "expand",
                        "source": "session_summary",
                        "summary_id": int(summary_id),
                        "item": None,
                    }
                },
            )
        lines = [
            f"memory_query session_summary expand: summary_id={item.get('summary_id')} "
            f"kind={item.get('summary_kind')} session={item.get('session_id')}",
            f"covered_turns={item.get('covered_from_turn_id')}..{item.get('covered_until_turn_id')}",
            "summary:",
            str(item.get("summary_text") or ""),
        ]
        return ToolResult(
            output="\n".join(lines),
            exit_code=0,
            metadata={
                "structured_content": {
                    "mode": "expand",
                    "source": "session_summary",
                    "summary_id": int(summary_id),
                    "item": item,
                }
            },
        )

    def _session_aggregate(
        self,
        service: SessionSummaryRetrievalService,
        args: dict[str, Any],
        limit: int,
        include_archived: bool,
    ) -> ToolResult:
        rows = service.list_summaries(
            user_id=str(args.get("user_id") or "").strip(),
            session_id=str(args.get("session_id") or "").strip(),
            date_start=str(args.get("date_start") or "").strip(),
            date_end=str(args.get("date_end") or "").strip(),
            limit=limit,
            include_content=False,
            include_archived=include_archived,
        )
        if not rows:
            return ToolResult(
                output="没有可聚合的 session summary。",
                exit_code=0,
                metadata={
                    "structured_content": {
                        "mode": "aggregate",
                        "source": "session_summary",
                        "items": [],
                    }
                },
            )
        lines = [f"memory_query session_summary aggregate: count={len(rows)}"]
        for row in rows:
            lines.append(
                f"- summary_id={row.get('summary_id')} kind={row.get('summary_kind')} "
                f"covered_until={row.get('covered_until_turn_id')}: {str(row.get('preview') or '')[:180]}"
            )
        return ToolResult(
            output="\n".join(lines),
            exit_code=0,
            metadata={
                "structured_content": {
                    "mode": "aggregate",
                    "source": "session_summary",
                    "items": rows,
                }
            },
        )


async def execute_memory_query(
    args: dict[str, Any],
    *,
    adapter: MemoryQueryTool | None = None,
) -> ToolResult:
    """执行 Memory Query；供 KT Tool 与 Memory Provider 共用。"""

    tool = adapter or MemoryQueryTool()
    mode = str(args.get("mode") or "search").strip()
    source = str(args.get("source") or "digest").strip() or "digest"
    limit = max(1, min(int(args.get("limit") or 5), 10))
    include_detail = bool(args.get("include_detail", False))
    include_legacy = bool(args.get("include_legacy", False))
    try:
        tool._validate_date_args(args)
    except ValueError as exc:
        return ToolResult(error=str(exc))

    try:
        with UnitOfWork() as uow:
            if uow.db is None:
                return ToolResult(error="database session is unavailable")
            if mode == "search" and source in {"digest", "session_summary", "all"}:
                from core.semantic.provider_factory import get_rag_runtime_config

                runtime = get_rag_runtime_config("memory")
                if runtime.enabled and (
                    source == "all" or tool._has_rag_index(uow.db, source)
                ):
                    return tool._rag_search(uow.db, args, limit, source=source)
                if source == "all":
                    return ToolResult(error="source=all requires MEMORY_RAG_ENABLED=1")
            if source == "session_summary":
                service = SessionSummaryRetrievalService(uow.db)
                if mode == "search":
                    return tool._session_search(
                        service,
                        args,
                        limit,
                        include_detail,
                        include_legacy,
                    )
                if mode == "time":
                    return tool._session_time(
                        service,
                        args,
                        limit,
                        include_detail,
                        include_legacy,
                    )
                if mode == "expand":
                    return tool._session_expand(service, args, include_legacy)
                if mode == "aggregate":
                    return tool._session_aggregate(
                        service,
                        args,
                        limit,
                        include_legacy,
                    )
                return ToolResult(error=f"Unsupported mode: {mode}")
            if source == "all":
                return ToolResult(
                    error="source=all currently supports search mode only"
                )
            if source != "digest":
                return ToolResult(error=f"Unsupported source: {source}")

            service = MemoryDigestRetrievalService(uow.db)
            if mode == "search":
                return tool._search(
                    service,
                    args,
                    limit,
                    include_detail,
                    include_legacy,
                )
            if mode == "time":
                return tool._time(
                    service,
                    args,
                    limit,
                    include_detail,
                    include_legacy,
                )
            if mode == "expand":
                return tool._expand(service, args, include_detail, include_legacy)
            if mode == "aggregate":
                return tool._aggregate(service, args, limit, include_legacy)
            return ToolResult(error=f"Unsupported mode: {mode}")
    except Exception as exc:
        return ToolResult(error=f"memory_query failed: {exc}")


__all__ = ["MemoryQueryTool", "execute_memory_query"]

"""Memory query tool — 查询结构化每日摘要，不直接暴露原始 ChatLog。"""

from __future__ import annotations

from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

from app.memory_digest.retrieval_service import MemoryDigestRetrievalService, validate_digest_date
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
            "当用户问较早之前讨论过什么、某天聊过什么、或需要从摘要继续展开时使用。"
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
        mode = str(args.get("mode") or "search").strip()
        limit = max(1, min(int(args.get("limit") or 5), 10))
        include_detail = bool(args.get("include_detail", False))
        include_legacy = bool(args.get("include_legacy", False))
        try:
            self._validate_date_args(args)
        except ValueError as exc:
            return ToolResult(error=str(exc))

        try:
            with UnitOfWork() as uow:
                if uow.db is None:
                    return ToolResult(error="database session is unavailable")
                service = MemoryDigestRetrievalService(uow.db)
                if mode == "search":
                    return self._search(service, args, limit, include_detail, include_legacy)
                if mode == "time":
                    return self._time(service, args, limit, include_detail, include_legacy)
                if mode == "expand":
                    return self._expand(service, args, include_detail, include_legacy)
                if mode == "aggregate":
                    return self._aggregate(service, args, limit, include_legacy)
                return ToolResult(error=f"Unsupported mode: {mode}")
        except Exception as exc:
            return ToolResult(error=f"memory_query failed: {exc}")

    @staticmethod
    def _validate_date_args(args: dict[str, Any]) -> None:
        validate_digest_date(str(args.get("digest_date") or "").strip(), "digest_date")
        validate_digest_date(str(args.get("date_start") or "").strip(), "date_start")
        validate_digest_date(str(args.get("date_end") or "").strip(), "date_end")

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
            preview = meta.get("preview") if isinstance(meta.get("preview"), dict) else {}
            cards = meta.get("recall_cards") if isinstance(meta.get("recall_cards"), list) else []
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
            preview = meta.get("preview") if isinstance(meta.get("preview"), dict) else {}
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
        digest_id = args.get("digest_id")
        if digest_id is None:
            return ToolResult(error="expand mode requires digest_id")
        item = service.expand_digest(
            digest_id=int(digest_id),
            include_detail=include_detail,
            include_legacy=include_legacy,
        )
        if not item:
            return ToolResult(
                output=f"未找到可展开的摘要 digest_id={digest_id}。",
                exit_code=0,
                metadata={
                    "structured_content": {
                        "mode": "expand",
                        "digest_id": int(digest_id),
                        "item": None,
                    }
                },
            )
        lines = [
            f"memory_query expand: digest_id={item.get('digest_id')} date={item.get('digest_date')} session={item.get('session_id')}",
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
                    "digest_id": int(digest_id),
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
            preview = meta.get("preview") if isinstance(meta.get("preview"), dict) else {}
            keywords = preview.get("keywords") if isinstance(preview.get("keywords"), list) else []
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

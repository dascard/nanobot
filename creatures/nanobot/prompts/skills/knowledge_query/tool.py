"""Knowledge query tool — 查询已入库外部知识库。"""

from __future__ import annotations

import json
from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

from core.uow import UnitOfWork


class KnowledgeQueryTool(BaseTool):
    """按关键词查询带 citation 的外部知识库结果。"""

    @property
    def tool_name(self) -> str:
        return "knowledge_query"

    @property
    def description(self) -> str:
        return (
            "查询已入库的外部知识库，只返回带 citation 的结果。"
            "适合查询手工文档、已保存 URL 元数据和历史日报摘要；今天/刚刚/实时资讯仍优先用 ai_daily。"
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
                    "enum": ["search", "expand"],
                    "description": "search=按关键词检索；expand=按 document_id + chunk_id 展开单个 chunk。",
                },
                "query": {
                    "type": "string",
                    "description": "检索关键词，search 模式必填。",
                },
                "document_id": {
                    "type": "integer",
                    "description": "expand 模式要展开的文档 ID。",
                },
                "chunk_id": {
                    "type": "string",
                    "description": "expand 模式要展开的 chunk_id。",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数，默认 5，最大 10。",
                    "minimum": 1,
                    "maximum": 10,
                },
                "min_trust_level": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "最低 trust_level，默认 low。",
                },
                "published_after": {
                    "type": "string",
                    "description": "仅返回此日期之后的资料，YYYY-MM-DD。",
                },
                "published_before": {
                    "type": "string",
                    "description": "仅返回此日期之前的资料，YYYY-MM-DD。",
                },
            },
            "required": ["mode"],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        mode = str(args.get("mode") or "search").strip()
        limit = max(1, min(int(args.get("limit") or 5), 10))
        try:
            with UnitOfWork() as uow:
                if uow.db is None:
                    return ToolResult(error="database session is unavailable")
                from core.knowledge_rag import KnowledgeRagService

                service = KnowledgeRagService(uow.db)
                if mode == "search":
                    return self._search(service, args, limit)
                if mode == "expand":
                    return self._expand(service, args)
                return ToolResult(error=f"Unsupported mode: {mode}")
        except Exception as exc:
            return ToolResult(error=f"knowledge_query failed: {exc}")

    def _search(self, service, args: dict[str, Any], limit: int) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(error="search mode requires query")
        result = service.query(
            query,
            limit=limit,
            min_trust_level=str(args.get("min_trust_level") or "low"),
            published_after=str(args.get("published_after") or ""),
            published_before=str(args.get("published_before") or ""),
        )
        items = result.get("items") or []
        if not items:
            return ToolResult(
                output=f"未找到与 {query} 相关的外部知识。",
                exit_code=0,
                metadata={"structured_content": {"mode": "search", **result}},
            )
        lines = [f"knowledge_query search: query={query} count={len(items)} degraded={result.get('degraded')}"]
        for item in items:
            citation = item.get("citation") or {}
            lines.append(
                f"- document_id={item.get('document_id')} chunk_id={item.get('chunk_id')} "
                f"trust={item.get('trust_level')} title={citation.get('title')}: "
                f"{str(item.get('text') or '')[:240]}"
            )
        return ToolResult(
            output="\n".join(lines),
            exit_code=0,
            metadata={"structured_content": {"mode": "search", **result}},
        )

    def _expand(self, service, args: dict[str, Any]) -> ToolResult:
        document_id = args.get("document_id")
        chunk_id = str(args.get("chunk_id") or "").strip()
        if not document_id or not chunk_id:
            return ToolResult(error="expand mode requires document_id and chunk_id")
        expanded = service.expand(document_id=int(document_id), chunk_id=chunk_id)
        return ToolResult(
            output=json.dumps(expanded, ensure_ascii=False),
            exit_code=0,
            metadata={"structured_content": {"mode": "expand", **expanded}},
        )

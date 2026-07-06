"""Web Search tool — 调用已配置的通用搜索 provider。"""

from __future__ import annotations

import logging
from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

from core.uow import UnitOfWork
from core.web_search.search_runtime import (
    WebSearchError,
    format_provider_result_for_model,
    search_enabled_providers,
)

logger = logging.getLogger("nanobot.web_search.tool")


class WebSearchTool(BaseTool):
    """通用网页搜索工具。"""

    @property
    def tool_name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "使用管理后台配置的搜索 provider 执行通用网页搜索，返回标题、URL 和摘要。"
            "系统会按后台启用顺序自动 fallback，并在第一个相关结果停止。"
            "适合查询最新网页资料、官方文档、公告、产品信息和需要外部来源的问题。"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索词。应包含关键实体、限定词或时间范围，避免只传一个模糊词。",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数，默认 5，最大 10。",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(error="Missing 'query' argument")
        try:
            raw_limit = args.get("limit")
            if raw_limit in (None, ""):
                raw_limit = args.get("max_results")
            limit = max(1, min(int(raw_limit or 5), 10))
        except (TypeError, ValueError):
            limit = 5
        provider = str(args.get("provider") or "").strip()

        try:
            with UnitOfWork() as uow:
                if uow.db is None:
                    return ToolResult(error="database session is unavailable")
                result = await search_enabled_providers(
                    uow.db,
                    query=query,
                    limit=limit,
                    provider_id=provider,
                )
        except WebSearchError as exc:
            return ToolResult(
                error=f"web_search failed: {exc.message}",
                metadata={
                    "structured_content": {
                        "error_code": exc.error_code,
                        "provider_id": exc.provider_id,
                    }
                },
            )
        except Exception as exc:
            return ToolResult(error=f"web_search failed: {exc}")

        items = result.results
        first = items[0] if items else None
        logger.info(
            "web_search result provider=%s query=%s count=%d first_title=%s first_url=%s",
            result.provider_id,
            query[:120],
            len(items),
            (first.title[:120] if first else ""),
            (first.url[:200] if first else ""),
        )
        if not items:
            return ToolResult(
                output=format_provider_result_for_model(query, result, limit=limit),
                exit_code=0,
                metadata={"structured_content": result.to_dict()},
            )

        return ToolResult(
            output=format_provider_result_for_model(query, result, limit=limit),
            exit_code=0,
            metadata={"structured_content": result.to_dict()},
        )

"""Web Search tool — 调用已配置的通用搜索 provider。"""

from __future__ import annotations

from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

from core.uow import UnitOfWork
from core.web_search.search_runtime import WebSearchError, search_enabled_providers


class WebSearchTool(BaseTool):
    """通用网页搜索工具。"""

    @property
    def tool_name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "使用管理后台配置的搜索 provider 执行通用网页搜索，返回标题、URL 和摘要。"
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
                "provider": {
                    "type": "string",
                    "description": "可选 provider id，如 searxng、brave、serper、tavily、exa、firecrawl、linkup、you、jina、ddgs。留空则按已启用 provider 自动 fallback。",
                },
            },
            "required": ["query"],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(error="Missing 'query' argument")
        try:
            limit = max(1, min(int(args.get("limit") or 5), 10))
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
        if not items:
            return ToolResult(
                output=f"web_search: query={query} provider={result.provider_id} count=0",
                exit_code=0,
                metadata={"structured_content": result.to_dict()},
            )

        lines = [
            f"web_search: query={query} provider={result.provider_id} count={len(items)}",
        ]
        for index, item in enumerate(items[:limit], start=1):
            snippet = item.snippet.replace("\n", " ").strip()
            if len(snippet) > 260:
                snippet = f"{snippet[:260]}..."
            line = f"{index}. {item.title}\n   URL: {item.url}"
            if snippet:
                line += f"\n   摘要: {snippet}"
            if item.published_at:
                line += f"\n   时间: {item.published_at}"
            lines.append(line)

        return ToolResult(
            output="\n".join(lines),
            exit_code=0,
            metadata={"structured_content": result.to_dict()},
        )

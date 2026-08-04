"""通用网页搜索工具的 KT 薄 Adapter。"""

from __future__ import annotations

from typing import Any

from nanobot_kt.optional_tool_api import BaseTool, ExecutionMode, ToolResult

from app.tool_services.web_search import execute_web_search
from core.web_search.search_runtime import (
    MODEL_QUERY_MAX_CHARS,
    search_enabled_providers,
)
from nanobot_kt.tools.result_adapter import to_kt_tool_result


class WebSearchTool(BaseTool):
    """把 KT 调用转换为框架无关的搜索应用请求。"""

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
            "additionalProperties": False,
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索词。应包含关键实体、限定词或时间范围，避免只传一个模糊词。",
                    "maxLength": MODEL_QUERY_MAX_CHARS,
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

    async def _execute(
        self,
        args: dict[str, Any],
        **kwargs: Any,
    ) -> ToolResult:
        del kwargs
        return to_kt_tool_result(
            await execute_web_search(
                args,
                search=search_enabled_providers,
            )
        )


__all__ = ["WebSearchTool"]

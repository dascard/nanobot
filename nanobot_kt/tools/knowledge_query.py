"""Knowledge Query 的 KT 工具适配与执行实现。"""

from __future__ import annotations

from typing import Any

from nanobot_kt.optional_tool_api import BaseTool, ExecutionMode, ToolResult

from app.tool_services.knowledge_query import execute_knowledge_query
from nanobot_kt.tools.result_adapter import to_kt_tool_result


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
                "source_type": {
                    "type": "string",
                    "description": "按知识来源类型过滤，如 ai_daily、manual_markdown、manual_file。",
                },
                "domain": {
                    "type": "string",
                    "description": "按资料域名过滤，如 openai.com。",
                },
                "date_start": {
                    "type": "string",
                    "description": "资料发布时间开始日期，YYYY-MM-DD；等价于 published_after。",
                },
                "date_end": {
                    "type": "string",
                    "description": "资料发布时间结束日期，YYYY-MM-DD；等价于 published_before。",
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
        del kwargs
        from nanobot_kt.memory_runtime import (
            dispatch_memory_tool_call,
            has_memory_tool_runtime_binding,
            provider_result_to_tool_result,
        )

        if has_memory_tool_runtime_binding():
            result = await dispatch_memory_tool_call(self.tool_name, args)
            return provider_result_to_tool_result(result)
        return to_kt_tool_result(await execute_knowledge_query(args))


__all__ = ["KnowledgeQueryTool", "execute_knowledge_query"]

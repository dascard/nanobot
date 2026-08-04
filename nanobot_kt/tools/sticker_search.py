"""Sticker Search 的 KT 工具适配与执行实现。"""

from __future__ import annotations

from typing import Any

from nanobot_kt.optional_tool_api import BaseTool, ExecutionMode, ToolResult

from app.tool_services.sticker_search import execute_sticker_search
from nanobot_kt.tools.result_adapter import to_kt_tool_result


class StickerSearchTool(BaseTool):
    """按语义关键词检索表情包，返回可放进 reply(content) 的 CQ 图片码。"""

    @property
    def tool_name(self) -> str:
        return "sticker_search"

    @property
    def description(self) -> str:
        return (
            "搜索当前群或全局表情包。"
            "当群聊在斗图、玩梗、发纯表情，或用户明确要表情包时使用。"
            "不要频繁发表情包；不确定是否合适时直接文字回复。"
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
                    "description": "表情包关键词、情绪或使用场景，如 震惊、拍桌、生气、疑惑",
                },
                "group_id": {
                    "type": "string",
                    "description": "当前群号，优先来自 runtime_context.group_id",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回数量，默认 3，最大 8",
                    "minimum": 1,
                    "maximum": 8,
                },
                "include_global": {
                    "type": "boolean",
                    "description": "是否同时搜索全局表情包，默认 true",
                },
            },
            "required": ["query"],
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
        return to_kt_tool_result(await execute_sticker_search(args))


__all__ = ["StickerSearchTool", "execute_sticker_search"]

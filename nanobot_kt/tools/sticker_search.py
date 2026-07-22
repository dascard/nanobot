"""Sticker Search 的 KT 工具适配与执行实现。"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

from core.database import SessionLocal
from core.sticker_memory import search_stickers


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
        return await execute_sticker_search(args, session_factory=SessionLocal)


async def execute_sticker_search(
    args: dict[str, Any],
    *,
    session_factory: Callable[[], Any] = SessionLocal,
) -> ToolResult:
    """执行 Sticker Search；供 KT Tool 与 Memory Provider 共用。"""

    query = str(args.get("query") or "").strip()
    group_id = str(args.get("group_id") or "").strip()
    limit = int(args.get("limit") or 3)
    include_global = bool(args.get("include_global", True))
    if not query:
        return ToolResult(error="Missing 'query' argument")

    db = session_factory()
    try:
        try:
            results = search_stickers(
                db,
                query,
                group_id=group_id,
                limit=max(1, min(limit, 8)),
                include_global=include_global,
            )
        except Exception as exc:
            from core.semantic.provider_factory import RagDegradedBlockedError

            if isinstance(exc, RagDegradedBlockedError):
                return ToolResult(
                    error=str(exc),
                    metadata={
                        "structured_content": {
                            "query": query,
                            "source": "sticker",
                            "degraded": False,
                            "blocked_reason": exc.fallback_reason,
                            "results": [],
                        }
                    },
                )
            raise
        payload = {
            "query": query,
            "count": len(results),
            "results": results,
            "usage_hint": "优先选择一个 result.reply_token 放进 reply(content)，reply 工具会自动展开并发送表情包；不要手抄长 URL。如果没有很贴切的候选，改用文字回复。",
        }
        return ToolResult(output=json.dumps(payload, ensure_ascii=False), exit_code=0)
    finally:
        db.close()


__all__ = ["StickerSearchTool", "execute_sticker_search"]

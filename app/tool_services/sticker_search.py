"""表情记忆查询的框架无关应用服务。"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from core.sticker_memory import search_stickers
from core.tool_contracts.result import ToolServiceResult
from core.uow import UnitOfWork


async def execute_sticker_search(
    args: dict[str, Any],
    *,
    session_factory: Callable[[], Any] | None = None,
) -> ToolServiceResult:
    query = str(args.get("query") or "").strip()
    group_id = str(args.get("group_id") or "").strip()
    limit = int(args.get("limit") or 3)
    include_global = bool(args.get("include_global", True))
    if not query:
        return ToolServiceResult(error="Missing 'query' argument")

    with UnitOfWork(session_factory=session_factory) as uow:
        if uow.db is None:
            return ToolServiceResult(error="database session is unavailable")
        try:
            results = search_stickers(
                uow.db,
                query,
                group_id=group_id,
                limit=max(1, min(limit, 8)),
                include_global=include_global,
            )
        except Exception as exc:
            from core.semantic.provider_factory import RagDegradedBlockedError

            if isinstance(exc, RagDegradedBlockedError):
                return ToolServiceResult(
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
            "usage_hint": (
                "优先选择一个 result.reply_token 放进 reply(content)，reply 工具会自动"
                "展开并发送表情包；不要手抄长 URL。如果没有很贴切的候选，改用文字回复。"
            ),
        }
        return ToolServiceResult(
            output=json.dumps(payload, ensure_ascii=False),
            exit_code=0,
        )


__all__ = ["execute_sticker_search"]

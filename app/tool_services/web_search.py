"""通用网页搜索工具的应用服务。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from core.tool_contracts.result import ToolServiceResult
from core.uow import UnitOfWork
from core.web_search.search_runtime import (
    MODEL_QUERY_MAX_CHARS,
    WebSearchError,
    format_provider_result_for_model,
)


logger = logging.getLogger("nanobot.app.tool.web_search")
SearchCallable = Callable[..., Awaitable[Any]]


async def execute_web_search(
    args: dict[str, Any],
    *,
    search: SearchCallable,
) -> ToolServiceResult:
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolServiceResult(error="Missing 'query' argument")
    if len(query) > MODEL_QUERY_MAX_CHARS:
        return ToolServiceResult(
            error=(
                "web_search query exceeds "
                f"{MODEL_QUERY_MAX_CHARS} characters"
            )
        )
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
                return ToolServiceResult(
                    error="database session is unavailable"
                )
            result = await search(
                uow.db,
                query=query,
                limit=limit,
                provider_id=provider,
            )
    except WebSearchError as exc:
        return ToolServiceResult(
            error=f"web_search failed: {exc.message}",
            metadata={
                "structured_content": {
                    "error_code": exc.error_code,
                    "provider_id": exc.provider_id,
                }
            },
        )
    except Exception as exc:
        return ToolServiceResult(error=f"web_search failed: {exc}")

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
    return ToolServiceResult(
        output=format_provider_result_for_model(
            query,
            result,
            limit=limit,
        ),
        exit_code=0,
        metadata={"structured_content": result.to_dict()},
    )


__all__ = ["SearchCallable", "execute_web_search"]

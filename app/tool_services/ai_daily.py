"""AI 日报工具的框架无关缓存与结果编排。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from typing import Any

from core.tool_contracts.ai_daily import (
    AiDailyRequest,
    AiDailyRequestError,
    parse_ai_daily_request,
)
from core.tool_contracts.result import ToolServiceResult
from core.tool_contracts.rich_output import build_rich_output


logger = logging.getLogger("nanobot.app.tool.ai_daily")
PipelineCallable = Callable[[AiDailyRequest], str]
CacheKeyCallable = Callable[..., tuple[Any, ...]]
CacheReadCallable = Callable[[tuple[Any, ...]], str | None]
CacheWriteCallable = Callable[[tuple[Any, ...], str], None]
FallbackRenderer = Callable[[str, str, list[str]], str]


def build_ai_daily_tool_result(
    html_result: str,
    *,
    query: str,
) -> ToolServiceResult:
    metadata: dict[str, object] = {}
    try:
        from core.ai_daily_ingest import (
            best_effort_ingest_ai_daily_result,
        )

        metadata["ai_daily_ingest"] = (
            best_effort_ingest_ai_daily_result(
                html_result,
                query=query,
            )
        )
    except Exception as exc:
        logger.warning(
            "[ai_daily] ingest metadata failed: error_type=%s",
            type(exc).__name__,
        )
        metadata["ai_daily_ingest"] = {
            "created": 0,
            "updated": 0,
            "warnings": ["ai_daily_ingest_metadata_failed"],
        }
    return ToolServiceResult(
        output=build_rich_output(
            html_result,
            report_kind="ai_daily",
        ),
        exit_code=0,
        metadata=metadata,
    )


async def execute_ai_daily(
    args: dict[str, Any],
    *,
    pipeline: PipelineCallable,
    make_cache_key: CacheKeyCallable,
    read_cache: CacheReadCallable,
    write_cache: CacheWriteCallable,
    render_fallback: FallbackRenderer,
) -> ToolServiceResult:
    try:
        request = parse_ai_daily_request(args)
    except AiDailyRequestError as exc:
        return ToolServiceResult(
            error=f"Invalid ai_daily arguments: {exc}"
        )

    query_len = len(request.query)
    query_sha = hashlib.sha256(
        request.query.encode("utf-8")
    ).hexdigest()[:12]
    logger.info(
        "[ai_daily] query_len=%d query_sha=%s max=%d freshness=%s "
        "bypass_cache=%s",
        query_len,
        query_sha,
        request.max_results,
        request.freshness,
        request.bypass_cache,
    )

    cache_key = make_cache_key(request, mode="quality")
    if not request.bypass_cache:
        cached = read_cache(cache_key)
        if cached is not None:
            logger.info("[ai_daily] cache HIT")
            return build_ai_daily_tool_result(
                cached,
                query=request.query,
            )

    result = await asyncio.to_thread(pipeline, request)
    if not result or not str(result).strip():
        logger.error(
            "[ai_daily] empty output query_len=%d query_sha=%s",
            query_len,
            query_sha,
        )
        result = render_fallback(
            "暂无可用资讯",
            "本轮没有生成有效输出，已触发兜底。",
            ["工具返回为空"],
        )
    elif (
        "<html" not in str(result).lower()
        and "<article" not in str(result).lower()
    ):
        logger.warning(
            "[ai_daily] non-html output query_len=%d query_sha=%s "
            "output_len=%d",
            query_len,
            query_sha,
            len(str(result)),
        )
        result = render_fallback(
            "资讯结果不完整",
            "本轮生成结果非标准HTML，已转换兜底。",
            [str(result)[:200]],
        )
    write_cache(cache_key, result)
    return build_ai_daily_tool_result(
        result,
        query=request.query,
    )


__all__ = [
    "build_ai_daily_tool_result",
    "execute_ai_daily",
]

"""News Daily Tool —— Bot 入口，只负责参数解析、路由、缓存。"""

import logging
import time as _time
from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

from .schema import fallback_digest
from .sources.curated import JuyaProvider, CURATED_SOURCES
from .pipeline.collect import collect_sources
from .pipeline.normalize import normalize_items, filter_recent
from .pipeline.dedup import dedup_items
from .pipeline.rank import rank_items
from .pipeline.digest import build_digest_deterministic
from . import cache

logger = logging.getLogger("nanobot.news_daily")


def _route_mode(query: str, mode: str = "auto") -> str:
    if mode not in ("auto", "fast", "quality", "research"):
        mode = "auto"
    if mode != "auto":
        return mode
    q = query.lower()
    research_markers = ["深入", "详细", "核验", "官方来源", "证据", "对比", "benchmark", "开源协议"]
    if any(k in q for k in research_markers):
        return "research"
    return "quality"  # 默认 quality


def _get_providers(mode: str) -> list:
    providers = [JuyaProvider()]
    # fast/quality use curated only; research adds web_search
    return providers


def run_pipeline(query: str, mode: str = "fast", limit: int = 8) -> str:
    """主 Pipeline——抓取→处理→digest→渲染。"""
    from ..render import render_html as _render
    t0 = _time.time()

    providers = _get_providers(mode)
    items = collect_sources(providers, limit_per_source=limit, timeout=8)
    logger.info("[daily] collect: %d items in %.1fs", len(items), _time.time() - t0)

    items = normalize_items(items)
    items = filter_recent(items, hours=72)
    items = dedup_items(items)
    items = rank_items(items)
    items = items[:limit]
    logger.info("[daily] pipeline: %d items after filter/rank", len(items))

    digest = build_digest_deterministic(items, query, mode)
    html = _render(digest)
    logger.info("[daily] done %d items → %d chars HTML in %.1fs",
                len(items), len(html), _time.time() - t0)
    return html


class NewsDailyTool(BaseTool):
    """AI 日报生成——RSS/官方源聚合，默认不调 LLM。"""

    @property
    def tool_name(self) -> str:
        return "news_search"

    @property
    def description(self) -> str:
        return "搜索 AI/科技领域最新资讯并生成日报。fast=快速聚合 quality=LLM摘要 research=深度搜索"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词或自然语言查询"},
                "mode": {
                    "type": "string", "enum": ["auto", "fast", "quality", "research"],
                    "default": "auto",
                },
                "max_results": {"type": "integer", "default": 8},
                "refresh": {"type": "boolean", "default": False},
            },
            "required": ["query"],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        query = args.get("query", "").strip()
        if not query:
            return ToolResult(error="Missing 'query' argument")

        mode = str(args.get("mode") or "auto")
        limit = int(args.get("max_results", 8) or 8)
        refresh = bool(args.get("refresh", False))
        resolved_mode = _route_mode(query, mode)

        ck = cache.make_key(query, resolved_mode, limit)
        if not refresh:
            cached = cache.get(ck)
            if cached:
                logger.info("[daily] cache HIT mode=%s", resolved_mode)
                return ToolResult(output=cached, exit_code=0)

        try:
            import asyncio
            result = await asyncio.to_thread(run_pipeline, query, resolved_mode, limit)
        except Exception as e:
            logger.exception("[daily] pipeline failed")
            from ..render import render_html
            result = render_html(fallback_digest(query, str(e)[:160], resolved_mode))

        cache.set(ck, result, resolved_mode)
        return ToolResult(output=result, exit_code=0)

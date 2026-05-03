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
    return "quality"  # quality is default


def _get_providers(mode: str) -> list:
    """从 SourceRegistry 获取 provider 列表。"""
    from .sources.official import get_registry
    reg = get_registry()
    pairs = reg.select(mode)
    return [p for _, p in pairs]



def _apply_quotas(items, mode: str) -> list:
    """按来源类别配额限制，防止单一类别刷屏。"""
    from .sources.official import DAILY_QUOTA
    quotas = dict(DAILY_QUOTA)
    buckets: dict[str, list] = {}
    result = []
    for item in items:
        group = "official" if item.trust > 0.85 else ("media" if item.trust > 0.7 else "curated")
        bucket = buckets.setdefault(group, [])
        limit = quotas.get(group, 5)
        if len(bucket) < limit:
            bucket.append(item)
    for group in ["official", "research", "media", "curated"]:
        result.extend(buckets.get(group, []))
    return result

def run_pipeline(query: str, mode: str = "fast", limit: int = 10) -> str:
    """主 Pipeline——fast: deterministic, quality: LLM摘要。"""
    from ..render import render_html as _render
    t0 = _time.time()

    providers = _get_providers(mode)
    items = collect_sources(providers, limit_per_source=8, timeout=8)
    logger.info("[daily] collect: %d items in %.1fs", len(items), _time.time() - t0)

    items = normalize_items(items)
    items = filter_recent(items, hours=72)
    items = dedup_items(items)
    items = rank_items(items)

    if mode == "fast":
        items = _apply_quotas(items, mode)
        items = items[:limit]
        digest = build_digest_deterministic(items, query, mode)
    else:
        from .pipeline.select_ import select_items_by_quota
        from .pipeline.evidence_light import build_light_evidence_cards
        from .pipeline.summarize_quality import summarize_quality
        from .pipeline.validate import safe_quality_digest, validate_quality_digest

        candidates = select_items_by_quota(items, max_items=limit)
        logger.info("[daily] quality candidates: %d from %d items", len(candidates), len(items))

        fallback_d = build_digest_deterministic(candidates, query, "quality")
        cards = build_light_evidence_cards(candidates)

        if cards:
            llm_d = summarize_quality(cards, dict(fallback_d))
            digest = safe_quality_digest(llm_d, dict(fallback_d), cards)
            ok, issues = validate_quality_digest(digest, cards)
            if not ok:
                logger.warning("[daily] quality validator fatal: %s", issues)
                digest = dict(fallback_d)
            elif issues:
                digest.setdefault("missing_info", []).extend(issues[:3])
        else:
            digest = dict(fallback_d)

    html = _render(digest)
    logger.info("[daily] done %s mode %d items → %d chars HTML in %.1fs",
                mode, len(items), len(html), _time.time() - t0)
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

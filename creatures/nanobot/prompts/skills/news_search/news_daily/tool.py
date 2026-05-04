"""AI Daily News Tool —— 只负责日报，不再混入 web research。"""

import logging
import time as _time
from typing import Any

from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult

from .schema import fallback_digest
from .pipeline.collect import collect_sources
from .pipeline.normalize import normalize_items, filter_recent
from .pipeline.dedup import dedup_items
from .pipeline.rank import rank_items
from .pipeline.digest import build_digest_deterministic
from . import cache

logger = logging.getLogger("nanobot.news_daily")


def _route_mode(query: str, mode: str = "auto") -> str:
    if mode not in ("auto", "fast", "quality"):
        mode = "auto"
    if mode != "auto":
        return mode
    return "quality"


def _get_providers(mode: str) -> list:
    from .sources.official import get_registry
    reg = get_registry()
    pairs = reg.select(mode)
    providers = []
    for cfg, prov in pairs:
        # Inject source_group from config
        prov._group = cfg.group
        prov._top_story_eligible = cfg.top_story_eligible
        prov._category_hint = cfg.category_hint
        providers.append(prov)
    return providers


def _apply_quotas(items, limit: int) -> list:
    from .sources.official import DAILY_QUOTA
    buckets: dict[str, list] = {}
    for item in items:
        buckets.setdefault(item.source_group or "curated", []).append(item)
    result = []
    for group in ["core_provider", "core_platform", "ai_media", "research", "curated", "community"]:
        result.extend(buckets.get(group, [])[:DAILY_QUOTA.get(group, 0)])
    if len(result) < limit:
        for item in items:
            if item not in result and item.source_group != "community":
                result.append(item)
            if len(result) >= limit:
                break
    return result[:limit]


def run_pipeline(query: str, mode: str = "quality", limit: int = 10) -> str:
    """主 Pipeline——fast: 标题索引，quality: LLM 摘要。"""
    from ..render import render_html as _render
    t0 = _time.time()

    providers = _get_providers(mode)
    items = collect_sources(providers, limit_per_source=8, timeout=10)
    logger.info("[daily] collect: %d items in %.1fs", len(items), _time.time() - t0)

    items = normalize_items(items)
    items = filter_recent(items, hours=72)
    items = dedup_items(items)
    items = rank_items(items)

    if mode == "fast":
        candidates = _apply_quotas(items, limit)
        digest = build_digest_deterministic(candidates, query, mode)
    else:
        from .pipeline.select_ import select_items_by_quota
        from .pipeline.enrich import enrich_items
        from .pipeline.evidence_light import build_light_evidence_cards
        from .pipeline.summarize_quality import summarize_quality
        from .pipeline.validate import safe_quality_digest, validate_quality_digest

        # 先 enrich 再 select——薄 RSS（如 qbitai/huggingface）靠详情补全才能入选
        items = enrich_items(items)
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
    logger.info("[daily] done %s mode %d ranked items → %d chars HTML in %.1fs", mode, len(items), len(html), _time.time() - t0)
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
                "query": {"type": "string", "description": "日报请求，例如：今日 AI 日报"},
                "mode": {"type": "string", "enum": ["auto", "fast", "quality", "research"], "default": "auto"},
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
        limit = int(args.get("max_results", 10) or 10)
        refresh = bool(args.get("refresh", False))
        resolved_mode = _route_mode(query, mode)

        ck = cache.make_key(query, resolved_mode, limit, "html")
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

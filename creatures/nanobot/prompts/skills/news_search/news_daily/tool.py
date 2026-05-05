"""AI Daily News Tool —— quality 为主，daily 为 fallback。"""

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


class FallbackNeeded(Exception):
    """quality 管线失败或质量不足时触发 daily fallback。"""
    pass


def _route_mode(query: str, mode: str = "auto") -> str:
    if mode not in ("auto", "fast", "quality", "daily"):
        mode = "auto"
    if mode != "auto":
        return mode
    return "quality"  # 默认走 quality LLM 管线


def _get_providers(mode: str) -> list:
    from .sources.official import get_registry
    reg = get_registry()
    pairs = reg.select(mode)
    providers = []
    for cfg, prov in pairs:
        prov._group = cfg.group
        prov._weight = cfg.weight
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


def _report_to_digest(report, articles):
    """EventCluster pipeline → 兼容旧 render dict 格式。"""
    from datetime import datetime
    from .pipeline.config import MAX_SAME_ENTITY_CLUSTERS_DAILY, MAX_CLUSTERS_PER_DOMAIN_FINAL

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    top = report.top_story
    highlights = report.highlights

    # 构建 article_id → source_id 映射
    art_to_src = {}
    sources = []
    for a in articles[:12]:
        sid = len(sources) + 1
        art_to_src[a.id] = sid
        sources.append({
            "source_id": sid, "title": a.title, "url": a.url, "domain": a.domain,
            "source_name": a.source,
            "published_at": a.published_at.strftime("%Y-%m-%d") if a.published_at else "",
        })

    def _cluster_src_ids(c):
        ids = []
        for a in c.articles:
            sid = art_to_src.get(a.id)
            if sid and sid not in ids:
                ids.append(sid)
        return ids[:3]

    # render guard
    seen_ids, seen_entities, seen_domains = set(), {}, {}
    safe_hl = []
    for c in highlights:
        if c.id in seen_ids:
            continue
        if any(seen_entities.get(e, 0) >= MAX_SAME_ENTITY_CLUSTERS_DAILY for e in c.entities):
            continue
        rep = c.representative
        if rep and seen_domains.get(rep.domain, 0) >= MAX_CLUSTERS_PER_DOMAIN_FINAL:
            continue
        safe_hl.append(c)
        seen_ids.add(c.id)
        for e in c.entities:
            seen_entities[e] = seen_entities.get(e, 0) + 1
        if rep:
            seen_domains[rep.domain] = seen_domains.get(rep.domain, 0) + 1

    _ENTITY_CN = {
        "openai": "OpenAI", "anthropic": "Anthropic", "google": "Google/DeepMind",
        "deepseek": "DeepSeek/深度求索", "qwen": "Qwen/通义千问", "kimi": "Kimi/月之暗面",
        "mistral": "Mistral", "meta": "Meta", "nvidia": "NVIDIA", "xai": "xAI/Grok",
    }
    _TOPIC_CN = {
        "model_release": "发布新模型", "benchmark": "评测/基准", "funding": "融资动态",
        "product": "产品更新", "policy": "政策/监管", "research": "研究成果", "incident": "安全事件",
    }

    def _build_event_summary(c):
        """从 cluster 的 entity/topic/article 生成中文事件摘要。"""
        parts = []
        entity_cn = [_ENTITY_CN.get(e, e) for e in (c.entities or [])]
        topic_cn = [_TOPIC_CN.get(t, t) for t in (c.keywords or [])]
        if entity_cn:
            parts.append("、".join(entity_cn))
        if topic_cn:
            parts.append(" · ".join(topic_cn[:2]))
        n_src = len(c.source_domains) if c.source_domains else 1
        src_note = f"{n_src} 个来源" if n_src >= 2 else ""
        base = " · ".join(parts) if parts else c.title[:60]
        # 拼接一篇文章的摘要作为补充
        snippets = [a.summary[:80] for a in c.articles[:2] if a.summary and len(a.summary) > 10]
        snippet = (" — " + snippets[0]) if snippets else ""
        return f"{base}{snippet}", src_note

    def _cluster_to_card(c):
        rep = c.representative
        summary, src_note = _build_event_summary(c)
        text = summary[:140]
        return {
            "label": (rep.source if rep else ""),
            "text": text,
            "source_ids": _cluster_src_ids(c),
            "importance": min(5, max(1, int(c.final_score * 5))),
        }

    def _cluster_to_top_story(c):
        summary, src_note = _build_event_summary(c)
        known_texts = c.known[:2] or [a.summary[:150] for a in c.articles[:2] if a.summary]
        snippets = [a.summary[:120] for a in c.articles[:3] if a.summary and len(a.summary) > 20]
        return {
            "title": summary[:100],
            "text": summary[:140],
            "what_happened": known_texts[0] if known_texts else (snippets[0] if snippets else ""),
            "why_it_matters": (c.impact or "")[:120] or (src_note or ""),
            "label": (c.representative.source if c.representative else ""),
            "source_ids": _cluster_src_ids(c),
            "importance": min(5, max(1, int(c.final_score * 5))),
        }

    def _cluster_to_detail(c):
        summary, _ = _build_event_summary(c)
        return {
            "title": summary[:100],
            "known": c.known[:3] or [a.summary[:200] for a in c.articles[:2] if a.summary],
            "unknown": c.missing[:2] or [],
            "impact": c.impact or "",
            "source_ids": _cluster_src_ids(c),
            "source_labels": [a.source for a in c.articles[:3]],
        }

    # details 也走 render guard
    safe_ids = {c.id for c in safe_hl}
    safe_details = []
    for c in ([top] if top else []) + safe_hl[:2]:
        if c and c.id not in {d.id for d in safe_details}:
            safe_details.append(c)

    return {
        "title": report.title,
        "subtitle": f"{len(safe_hl)} 个事件" if safe_hl else "暂无新事件",
        "verdict": f"共 {len(articles)} 篇文章，{len(highlights)} 个事件聚类" if highlights else "今日有效事件较少",
        "generated_at": now_str,
        "mode": "daily",
        "top_story": _cluster_to_top_story(top) if top else None,
        "highlights": [_cluster_to_card(c) for c in safe_hl[:6]],
        "details": [_cluster_to_detail(c) for c in safe_details[:3]],
        "watchlist": [],
        "missing_info": [],
        "closing": "基于事件聚类生成，同源/同实体已自动合并。",
        "sources": sources,
    }


def _html_looks_usable(html: str) -> bool:
    if not html or len(html) < 800:
        return False
    bad = ["AI 日报暂无内容", "未获取到 RSS 资讯", "今日有效事件较少", "新闻源为空"]
    return not any(m in html for m in bad)


def run_news_search_auto(query: str, limit: int = 8) -> str:
    """对外唯一入口：quality → daily fallback → fallback_digest。"""
    # 1. quality (LLM)
    try:
        html = run_pipeline(query, mode="quality", limit=limit)
        if _html_looks_usable(html):
            return html
        raise FallbackNeeded("quality html not usable")
    except Exception as e:
        logger.warning("[news] quality failed, fallback to daily: %s", e)

    # 2. daily (EventCluster)
    try:
        html = run_pipeline(query, mode="daily", limit=limit)
        if _html_looks_usable(html):
            return html
        raise FallbackNeeded("daily html not usable")
    except Exception as e:
        logger.warning("[news] daily fallback failed: %s", e)

    # 3. ultimate fallback
    from ..render import render_html
    return render_html(fallback_digest(query, "管线无法生成有效日报", "quality"))


def run_pipeline(query: str, mode: str = "quality", limit: int = 10) -> str:
    """主 Pipeline——quality: LLM 摘要，daily: EventCluster 聚类。"""
    from ..render import render_html as _render
    t0 = _time.time()

    providers = _get_providers(mode)
    items = collect_sources(providers, limit_per_source=8, timeout=10)
    logger.info("[daily] collect: %d items in %.1fs", len(items), _time.time() - t0)

    items = normalize_items(items)
    items = filter_recent(items, hours=72)
    items = dedup_items(items)
    items = rank_items(items)

    if mode == "daily":
        from datetime import datetime
        from .pipeline.normalize_v2 import normalize_articles
        from .pipeline.freshness import filter_fresh_articles
        from .pipeline.cluster import cluster_articles
        from .pipeline.diversify import score_clusters, select_diverse_clusters, build_daily_report

        now = datetime.now()
        articles = normalize_articles(items)
        articles = filter_fresh_articles(articles, now)
        clusters = cluster_articles(articles)
        clusters = score_clusters(clusters, now)
        clusters = select_diverse_clusters(clusters, now) if clusters else []
        report = build_daily_report(clusters, now)
        digest = _report_to_digest(report, articles)
        logger.info("[daily] v2: %d articles → %d clusters → %d selected in %.1fs",
                     len(articles), len(clusters), len(report.highlights), _time.time() - t0)

    elif mode == "fast":
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

        if not candidates:
            raise FallbackNeeded("quality has no candidates after enrich+quota")

        fallback_d = build_digest_deterministic(candidates, query, "quality")
        cards = build_light_evidence_cards(candidates)

        if not cards:
            raise FallbackNeeded("quality has no evidence cards")

        llm_d = summarize_quality(cards, dict(fallback_d))
        digest = safe_quality_digest(llm_d, dict(fallback_d), cards)
        ok, issues = validate_quality_digest(digest, cards)
        if not ok:
            raise FallbackNeeded(f"quality validator fatal: {issues}")
        if issues:
            digest.setdefault("missing_info", []).extend(issues[:3])

    html = _render(digest)
    logger.info("[daily] done %s mode %d ranked items → %d chars HTML in %.1fs", mode, len(items), len(html), _time.time() - t0)
    return html


class NewsDailyTool(BaseTool):
    """AI 日报生成——默认 quality LLM 摘要，失败时自动降级 daily 事件聚类。"""

    @property
    def tool_name(self) -> str:
        return "news_search"

    @property
    def description(self) -> str:
        return "搜索 AI/科技领域最新资讯并生成日报。"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "日报请求，例如：今日 AI 日报"},
                "max_results": {"type": "integer", "default": 8},
                "refresh": {"type": "boolean", "default": False},
            },
            "required": ["query"],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        query = args.get("query", "").strip()
        if not query:
            return ToolResult(error="Missing 'query' argument")

        limit = int(args.get("max_results", 8) or 8)
        refresh = bool(args.get("refresh", False))

        ck = cache.make_key(query, "quality", limit, "html")
        if not refresh:
            cached = cache.get(ck)
            if cached:
                logger.info("[daily] cache HIT")
                return ToolResult(output=cached, exit_code=0)

        try:
            import asyncio
            result = await asyncio.to_thread(run_news_search_auto, query, limit)
        except Exception as e:
            logger.exception("[daily] auto pipeline failed")
            from ..render import render_html
            result = render_html(fallback_digest(query, str(e)[:160], "quality"))

        cache.set(ck, result, "quality")
        return ToolResult(output=result, exit_code=0)

"""程序生成 NewsDigest——fast 模式不调 LLM。"""

from datetime import datetime
from ..schema import NewsItem, NewsDigest, fallback_digest


def build_digest_deterministic(items: list[NewsItem], query: str = "", mode: str = "fast") -> dict:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    if not items:
        f = fallback_digest(query, "未获取到 RSS 资讯", mode)
        return _to_dict(f)

    top = items[0]
    source_count = len(set(i.source_name for i in items))

    return {
        "title": "AI 今日速报",
        "subtitle": "RSS / 官方源自动聚合",
        "verdict": f"共 {source_count} 个来源 {len(items)} 条资讯，优先关注：{top.title[:60]}",
        "generated_at": now,
        "mode": mode,
        "top_story": {
            "title": top.title[:60],
            "what_happened": top.summary[:100] or top.title[:100],
            "why_it_matters": f"来源：{top.source_name}；可信度：{top.trust:.0%}",
            "source_ids": [1],
            "confidence": "high" if top.trust > 0.7 else "medium",
        },
        "highlights": [
            {
                "label": item.source_name or "AI资讯",
                "text": item.summary[:100] or item.title[:100],
                "source_ids": [idx + 1],
                "importance": _score_to_importance(item.score),
            }
            for idx, item in enumerate(items[:6])
        ],
        "watchlist": [],
        "missing_info": [],
        "closing": "本日报基于 RSS 自动聚合，深搜核验可使用 research 模式。",
        "sources": [
            {
                "source_id": idx + 1,
                "title": item.title,
                "url": item.url,
                "domain": item.domain,
                "source_name": item.source_name,
            }
            for idx, item in enumerate(items[:12])
        ],
    }


def _score_to_importance(score: float) -> int:
    if score >= 0.8:
        return 5
    if score >= 0.6:
        return 4
    if score >= 0.4:
        return 3
    return 2


def _to_dict(d: NewsDigest) -> dict:
    return {
        "title": d.title, "subtitle": d.subtitle, "verdict": d.verdict,
        "generated_at": d.generated_at, "mode": d.mode,
        "top_story": d.top_story, "highlights": d.highlights,
        "watchlist": d.watchlist, "missing_info": d.missing_info,
        "closing": d.closing, "sources": d.sources,
    }

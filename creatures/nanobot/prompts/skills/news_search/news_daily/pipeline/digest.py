"""程序生成 NewsDigest——fast 模式不调 LLM。"""

from dataclasses import asdict as _to_dict
from datetime import datetime
from ..schema import NewsItem, NewsDigest, fallback_digest


CATEGORY_MAP = {
    "openai_news": "模型发布", "huggingface_blog": "开源/工具",
    "mit_ai": "研究/AI", "techcrunch_ai": "行业/AI",
    "theverge_ai": "消费AI", "juya_ai_daily": "每日汇总",
    "arxiv_ai": "AI论文", "arxiv_cl": "NLP论文",
}

SOURCE_GROUP_LABEL = {"official": "官方", "research": "研究", "media": "媒体", "curated": "策展", "community": "社区"}


def _item_label(item):
    return CATEGORY_MAP.get(item.source_name, SOURCE_GROUP_LABEL.get("official" if item.trust > 0.85 else "media", "资讯"))


def build_digest_deterministic(items, query="", mode="fast"):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not items:
        return _to_dict(fallback_digest(query, "未获取到 RSS 资讯", mode))

    source_count = len(set(i.source_name for i in items))
    is_fast = mode == "fast"

    return {
        "title": "AI 快讯候选" if is_fast else "AI 今日速报",
        "subtitle": "RSS / 官方源标题索引" if is_fast else f"{source_count} 个来源候选",
        "verdict": (
            f"共 {source_count} 个来源 {len(items)} 条候选；"
            f"{'fast 模式不生成摘要，仅展示标题索引。需要日报请使用 quality 模式。' if is_fast else f'已筛选 {len(items)} 条 AI 相关资讯'}"
        ),
        "generated_at": now, "mode": mode,
        "top_story": None if is_fast else {
            "title": items[0].title[:60],
            "what_happened": items[0].title[:100],
            "why_it_matters": f"来源：{items[0].source_name}；分类：{_item_label(items[0])}",
            "source_ids": [1], "confidence": "high" if items[0].trust > 0.7 else "medium",
        },
        "highlights": [
            {"label": _item_label(item), "text": (item.summary or item.title)[:200], "source_ids": [idx+1], "importance": 3}
            for idx, item in enumerate(items[:8])
        ],
        "watchlist": [],
        "missing_info": (
            ["fast 模式未调用 AI，不判断新闻重要性，不生成内容摘要。"] if is_fast else []
        ),
        "closing": "需要正常日报请使用 quality 模式。" if is_fast else "本日报基于 RSS 自动聚合。",
        "sources": [
            {"source_id": idx+1, "title": item.title, "url": item.url, "domain": item.domain, "source_name": item.source_name, "published_at": item.published_at or ""}
            for idx, item in enumerate(items[:12])
        ],
    }

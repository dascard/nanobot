"""程序生成 NewsDigest——fast 模式不调 LLM。"""

from dataclasses import asdict as _to_dict
import re
from core.time_utils import db_now_naive
from ..schema import NewsItem, fallback_digest


CATEGORY_MAP = {
    "openai_news": "模型发布", "huggingface_blog": "开源/工具",
    "mit_ai": "研究/AI", "techcrunch_ai": "行业/AI",
    "theverge_ai": "消费AI", "juya_ai_daily": "每日汇总",
    "Juya AI Daily": "每日汇总", "deepseek_news": "模型/API",
    "qwen_blog": "模型发布", "mistral_news": "模型发布",
    "anthropic_news": "模型发布", "google_deepmind_news": "研究/模型",
    "arxiv_ai": "AI论文", "arxiv_cl": "NLP论文",
}

SOURCE_GROUP_LABEL = {"official": "官方", "research": "研究", "media": "媒体", "curated": "策展", "community": "社区"}


def _item_label(item):
    return CATEGORY_MAP.get(item.source_name, SOURCE_GROUP_LABEL.get("official" if item.trust > 0.85 else "media", "资讯"))


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _fallback_text(item: NewsItem) -> str:
    title = (item.title or "").strip()
    summary = (item.summary or item.content_excerpt or "").strip()
    if summary.startswith(title):
        summary = summary[len(title):].strip(" ：:-")
    if len(summary) > 140:
        summary = summary[:140].rstrip() + "..."
    if _has_cjk(title + summary):
        return f"【{title}】{summary or '该来源给出了新的 AI 行业动态。'}"
    return f"【{title}】来自 {item.source_name} 的 AI 动态；摘要显示其与模型、开发者工具或行业基础设施相关。"


def _top_story_score(item: NewsItem) -> int:
    text = f"{item.title} {item.summary}"
    score = 0
    today = db_now_naive().strftime("%Y-%m-%d")
    if item.published_at == today:
        score += 4
    elif item.published_at:
        score += 1
    if item.source_name in {"openai_news", "deepseek_news", "qwen_blog", "anthropic_news", "mistral_news", "Juya AI Daily", "juya_ai_daily"}:
        score += 3
    if _has_cjk(text):
        score += 2
    if re.search(r"(发布|上线|更新|开源|API|Codex|DeepSeek|Qwen|Claude|Gemini|OpenAI)", text, re.I):
        score += 2
    if re.search(r"(Mastering|Techniques|Visibility|Scheduling)", text, re.I):
        score -= 2
    return score


def _pick_top_story_index(items: list[NewsItem]) -> int:
    if not items:
        return 0
    return max(range(len(items)), key=lambda idx: (_top_story_score(items[idx]), -idx))


def build_digest_deterministic(items, query="", mode="fast"):
    now = db_now_naive().strftime("%Y-%m-%d %H:%M")
    if not items:
        return _to_dict(fallback_digest(query, "未获取到 RSS 资讯", mode))

    source_count = len(set(i.source_name for i in items))
    is_fast = mode == "fast"

    top_idx = _pick_top_story_index(items)
    top = items[top_idx]
    ordered = [top] + [item for idx, item in enumerate(items) if idx != top_idx]

    return {
        "title": "AI 快讯候选" if is_fast else "AI 今日速报",
        "subtitle": "RSS / 官方源标题索引" if is_fast else f"{source_count} 个来源候选",
        "verdict": (
            f"共 {source_count} 个来源 {len(items)} 条候选；"
            f"{'fast 模式不生成摘要，仅展示标题索引。需要日报请使用 quality 模式。' if is_fast else f'已筛选 {len(items)} 条 AI 相关资讯'}"
        ),
        "generated_at": now, "mode": mode,
        "top_story": None if is_fast else {
            "title": top.title[:80],
            "what_happened": _fallback_text(top),
            "why_it_matters": f"来源：{top.source_name}；分类：{_item_label(top)}。fallback 模式未调用总结模型，仅做保守提炼。",
            "source_ids": [top_idx + 1], "confidence": "high" if top.trust > 0.7 else "medium",
        },
        "highlights": [
            {
                "label": _item_label(item),
                "text": _fallback_text(item),
                "source_ids": [items.index(item) + 1],
                "importance": 4 if idx < 3 else 3,
            }
            for idx, item in enumerate(ordered[:8])
        ],
        "watchlist": [],
        "missing_info": (
            ["fast 模式未调用 AI，不判断新闻重要性，不生成内容摘要。"] if is_fast else []
        ),
        "closing": "需要正常日报请使用 quality 模式。" if is_fast else "本日报基于可信来源自动聚合；如模型总结不可用，已使用 fallback 提炼。",
        "sources": [
            {"source_id": idx+1, "title": item.title, "url": item.url, "domain": item.domain, "source_name": item.source_name, "published_at": item.published_at or ""}
            for idx, item in enumerate(items[:12])
        ],
    }

"""核心数据结构。"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class NewsItem:
    id: str = ""
    title: str = ""
    url: str = ""
    summary: str = ""
    content_excerpt: str = ""
    detail_text: str = ""          # 从详情页提取的正文（quality 模式用）

    source_name: str = ""
    source_type: str = ""          # rss / html_list / github / social_x ...
    source_group: str = "curated"  # core_provider / core_platform / ai_media / research / curated / community
    source_weight: float = 1.0
    source_url: str = ""
    top_story_eligible: bool = False

    domain: str = ""
    published_at: str = ""        # normalized as YYYY-MM-DD when possible
    fetched_at: str = ""
    trust: float = 0.5
    freshness: float = 0.5
    relevance: float = 0.0
    score: float = 0.0
    category: str = "AI资讯"
    tags: list[str] = field(default_factory=list)
    category_hint: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class SourceConfig:
    name: str
    type: str = "rss"
    url: str = ""
    trust: float = 0.5
    weight: float = 1.0
    enabled: bool = True
    group: str = "curated"
    modes: list[str] = field(default_factory=lambda: ["fast", "quality"])
    category_hint: list[str] = field(default_factory=list)
    top_story_eligible: bool = True
    max_items_per_run: int = 8


@dataclass
class NewsDigest:
    title: str = ""
    subtitle: str = ""
    verdict: str = ""
    generated_at: str = ""
    mode: str = "quality"
    top_story: dict | None = None
    highlights: list[dict] = field(default_factory=list)
    watchlist: list[dict] = field(default_factory=list)
    details: list[dict] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)
    closing: str = ""
    sources: list[dict] = field(default_factory=list)


def fallback_digest(query: str = "", reason: str = "", mode: str = "quality") -> NewsDigest:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return NewsDigest(
        title="AI 日报暂无内容",
        subtitle=query[:30],
        verdict=reason or "当前 RSS/官方源未获取到可用 AI 资讯。",
        generated_at=now,
        mode=mode,
        missing_info=["新闻源为空、暂时不可用，或筛选后无高相关条目"],
        closing="可稍后重试，或刷新源健康状态。",
    )

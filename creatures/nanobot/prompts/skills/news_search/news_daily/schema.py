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
    source_name: str = ""
    source_type: str = ""
    domain: str = ""
    published_at: str = ""
    fetched_at: str = ""
    trust: float = 0.5
    freshness: float = 0.5
    relevance: float = 0.0
    score: float = 0.0
    category: str = "未分类"
    tags: list[str] = field(default_factory=list)
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


@dataclass
class NewsDigest:
    title: str = ""
    subtitle: str = ""
    verdict: str = ""
    generated_at: str = ""
    mode: str = "fast"
    top_story: dict | None = None
    highlights: list[dict] = field(default_factory=list)
    watchlist: list[dict] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)
    closing: str = ""
    sources: list[dict] = field(default_factory=list)


def fallback_digest(query: str = "", reason: str = "", mode: str = "fast") -> NewsDigest:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return NewsDigest(
        title="AI 日报暂无内容",
        subtitle=query[:30],
        verdict=reason or "当前 RSS/官方源未获取到可用 AI 资讯。",
        generated_at=now,
        mode=mode,
        missing_info=["新闻源为空或暂时不可用"],
        closing="可稍后重试或切换 research 模式。",
    )

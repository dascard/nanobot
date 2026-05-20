"""核心数据结构——所有来源统一转 NewsItem，Digest 兼容 render_html。"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class NewsItem:
    title: str
    url: str
    summary: str = ""
    source: str = ""
    published_at: str = ""
    provider: str = ""
    category: str = "ai"
    score: float = 0.0
    raw: dict = field(default_factory=dict)

    def to_highlight(self, idx: int) -> dict:
        return {
            "label": self.provider or self.source or "AI资讯",
            "text": self.summary[:100] or self.title[:100],
            "source_ids": [idx],
            "importance": min(5, max(2, int(self.score or 3))),
        }


@dataclass
class NewsDigest:
    title: str
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
        verdict=reason or "当前未获取到可用 AI 资讯。",
        generated_at=now,
        mode=mode,
        missing_info=["新闻源为空或暂时不可用"],
        closing="可稍后重试或切换 research 模式。",
    )

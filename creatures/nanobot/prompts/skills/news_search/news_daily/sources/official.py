"""S 级官方源 + A 级媒体源 Provider。"""

import logging
from .rss import RSSProvider
from ..schema import NewsItem, SourceConfig

logger = logging.getLogger("nanobot.news_daily.official")

# 默认启用的 10 个源
DEFAULT_SOURCES: list[SourceConfig] = [
    # S 级：官方/一手源
    SourceConfig(name="openai_news", type="rss",
                 url="https://openai.com/news/rss.xml",
                 trust=0.98, weight=1.0, group="official",
                 category_hint=["模型发布", "API", "产品"]),
    SourceConfig(name="huggingface_blog", type="rss",
                 url="https://huggingface.co/blog/feed.xml",
                 trust=0.92, weight=1.0, group="official",
                 category_hint=["开源", "模型", "工具"]),
    SourceConfig(name="google_blog_ai", type="rss",
                 url="https://blog.google/feed/",
                 trust=0.93, weight=1.0, group="official",
                 category_hint=["Gemini", "产品", "研究"]),
    SourceConfig(name="google_deepmind", type="rss",
                 url="https://blog.google/feed/",
                 trust=0.94, weight=1.0, group="official",
                 category_hint=["DeepMind", "Gemma", "研究"]),
    SourceConfig(name="mit_ai", type="rss",
                 url="https://news.mit.edu/rss/topic/artificial-intelligence2",
                 trust=0.90, weight=1.0, group="official",
                 category_hint=["研究", "AI"]),
    SourceConfig(name="arxiv_ai", type="rss",
                 url="https://rss.arxiv.org/rss/cs.AI",
                 trust=0.88, weight=1.0, group="research",
                 category_hint=["论文", "AI"]),
    SourceConfig(name="arxiv_cl", type="rss",
                 url="https://rss.arxiv.org/rss/cs.CL",
                 trust=0.88, weight=1.0, group="research",
                 category_hint=["论文", "NLP", "LLM"]),
    # A 级：媒体源
    SourceConfig(name="techcrunch_ai", type="rss",
                 url="https://techcrunch.com/category/artificial-intelligence/feed/",
                 trust=0.78, weight=1.0, group="media",
                 category_hint=["行业", "创业", "AI"]),
    SourceConfig(name="theverge_ai", type="rss",
                 url="https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
                 trust=0.76, weight=1.0, group="media",
                 category_hint=["消费AI", "行业"]),
    # B 级：中文策展
    SourceConfig(name="juya_ai_daily", type="rss",
                 url="https://imjuya.github.io/juya-ai-daily/rss.xml",
                 trust=0.72, weight=1.0, group="curated",
                 category_hint=["日报", "中文"]),
]

SOURCE_TIER_WEIGHT = {
    "official": 1.00,
    "research": 0.88,
    "media": 0.76,
    "curated": 0.70,
    "community": 0.45,
}

DAILY_QUOTA = {
    "official": 3,
    "research": 2,
    "media": 3,
    "curated": 2,
    "community": 1,
}


class SourceRegistry:
    """管理所有 RSS 源，支持按 mode 筛选。"""

    def __init__(self):
        self.sources: list[tuple[SourceConfig, RSSProvider]] = []
        for cfg in DEFAULT_SOURCES:
            if cfg.enabled:
                prov = RSSProvider(url=cfg.url, source_name=cfg.name, trust=cfg.trust)
                self.sources.append((cfg, prov))
        logger.info("[registry] %d sources loaded", len(self.sources))

    def select(self, mode: str = "fast") -> list[tuple[SourceConfig, RSSProvider]]:
        """按 mode 选择源。"""
        if mode in ("fast", "quality"):
            return [(c, p) for c, p in self.sources if c.group != "community"]
        return list(self.sources)

    def get_quotas(self) -> dict[str, int]:
        return dict(DAILY_QUOTA)

    def get_tier_weight(self, group: str) -> float:
        return SOURCE_TIER_WEIGHT.get(group, 0.5)


# 全局单例
_registry: SourceRegistry | None = None


def get_registry() -> SourceRegistry:
    global _registry
    if _registry is None:
        _registry = SourceRegistry()
    return _registry

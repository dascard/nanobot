"""S 级官方源 + A 级媒体源 Provider。"""

import logging
from .rss import RSSProvider
from .htmllist import HtmlListProvider
from .htmllist import HtmlListProvider
from ..schema import NewsItem, SourceConfig

logger = logging.getLogger("nanobot.news_daily.official")

# 默认启用的 10 个源
DEFAULT_SOURCES: list[SourceConfig] = [
    # === core_provider: RSS ===
    SourceConfig(name="openai_news", type="rss",
                 url="https://openai.com/news/rss.xml",
                 trust=0.98, weight=1.2, group="core_provider",
                 category_hint=["模型发布", "API", "产品"]),
    SourceConfig(name="nvidia_blog", type="rss",
                 url="https://developer.nvidia.com/blog/feed/",
                 trust=0.86, weight=1.0, group="core_provider",
                 category_hint=["GPU", "推理", "开发者"]),
    # === core_platform: RSS ===
    SourceConfig(name="huggingface_blog", type="rss",
                 url="https://huggingface.co/blog/feed.xml",
                 trust=0.90, weight=1.0, group="core_platform",
                 category_hint=["开源", "模型", "工具"]),
    # === ai_media: RSS ===
    SourceConfig(name="techcrunch_ai", type="rss",
                 url="https://techcrunch.com/category/artificial-intelligence/feed/",
                 trust=0.74, weight=1.0, group="ai_media",
                 category_hint=["行业", "创业", "AI"]),
    SourceConfig(name="theverge_ai", type="rss",
                 url="https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
                 trust=0.74, weight=1.0, group="ai_media",
                 category_hint=["消费AI", "行业"]),
    SourceConfig(name="venturebeat_ai", type="rss",
                 url="https://venturebeat.com/category/ai/feed/",
                 trust=0.72, weight=1.0, group="ai_media",
                 category_hint=["企业AI", "行业"]),
    SourceConfig(name="mit_techreview_ai", type="rss",
                 url="https://www.technologyreview.com/topic/artificial-intelligence/feed/",
                 trust=0.78, weight=1.0, group="ai_media",
                 category_hint=["分析", "AI"]),
    # === research: RSS ===
    SourceConfig(name="mit_ai", enabled=False, type="rss",
                 url="https://news.mit.edu/rss/topic/artificial-intelligence2",
                 trust=0.82, weight=0.6, group="research",
                 category_hint=["研究", "AI"]),
    SourceConfig(name="arxiv_ai", enabled=False, type="rss",
                 url="https://rss.arxiv.org/rss/cs.AI",
                 trust=0.84, weight=0.6, group="research",
                 category_hint=["论文", "AI"]),
    SourceConfig(name="arxiv_cl", enabled=False, type="rss",
                 url="https://rss.arxiv.org/rss/cs.CL",
                 trust=0.84, weight=0.6, group="research",
                 category_hint=["论文", "NLP"]),
    # === curated: RSS ===
    SourceConfig(name="juya_ai_daily", type="rss",
                 url="https://imjuya.github.io/juya-ai-daily/rss.xml",
                 trust=0.72, weight=1.2, group="curated",
                 category_hint=["日报", "中文"]),
    SourceConfig(name="qbitai", type="rss",
                 url="https://www.qbitai.com/feed",
                 trust=0.70, weight=1.0, group="curated",
                 category_hint=["中文", "AI"]),
    # === core_provider: html_list (无RSS的官方博客, 暂标记enabled=True) ===
    SourceConfig(name="anthropic_news", type="html_list",
                 url="https://www.anthropic.com/news",
                 trust=0.96, weight=1.0, group="core_provider", enabled=True,
                 category_hint=["Claude", "模型", "API"]),
    SourceConfig(name="mistral_news", type="html_list",
                 url="https://mistral.ai/news",
                 trust=0.94, weight=1.0, group="core_provider", enabled=True,
                 category_hint=["Mistral", "模型"]),
    SourceConfig(name="deepseek_news", type="html_list",
                 url="https://api-docs.deepseek.com/news/",
                 trust=0.92, weight=1.0, group="core_provider", enabled=True,
                 category_hint=["DeepSeek", "模型", "API"]),
    SourceConfig(name="qwen_blog", type="html_list",
                 url="https://qwen.ai/blog",
                 trust=0.92, weight=1.0, group="core_provider", enabled=True,
                 category_hint=["Qwen", "模型"]),
    SourceConfig(name="kimi_blog", type="html_list",
                 url="https://www.kimi.com/blog/",
                 trust=0.88, weight=1.0, group="core_provider", enabled=True,
                 category_hint=["Kimi", "模型"]),
    SourceConfig(name="xai_news", type="html_list",
                 url="https://x.ai/news",
                 trust=0.90, weight=1.0, group="core_provider", enabled=True,
                 category_hint=["Grok", "xAI"]),
    SourceConfig(name="cohere_blog", type="html_list",
                 url="https://cohere.com/blog",
                 trust=0.88, weight=1.0, group="core_provider", enabled=True,
                 category_hint=["Cohere", "企业AI"]),
    SourceConfig(name="meta_ai_blog", type="html_list",
                 url="https://ai.meta.com/blog/",
                 trust=0.90, weight=1.0, group="core_provider", enabled=True,
                 category_hint=["Meta", "Llama"]),
    SourceConfig(name="google_deepmind_news", type="html_list",
                 url="https://deepmind.google/blog/",
                 trust=0.94, weight=1.0, group="core_provider", enabled=True,
                 category_hint=["Gemini", "DeepMind"]),
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
                if cfg.type == "html_list":
                    prov = HtmlListProvider(url=cfg.url, source_name=cfg.name, trust=cfg.trust)
                else:
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

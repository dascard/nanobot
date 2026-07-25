"""S 级官方源 + A 级媒体源 Provider。"""

import logging
from types import MappingProxyType
from typing import Any

from core.news.policy import DEFAULT_NEWS_RANKING_POLICY
from core.news.source_registry import (
    get_news_source_registry,
    get_runtime_news_source_registry,
)

from .rss import RSSProvider
from ..schema import SourceConfig
from .curated import JuyaProvider
from .adapters import (
    AnthropicNewsProvider,
    CohereBlogProvider,
    DeepSeekUpdatesProvider,
    KimiBlogProvider,
    MetaAIBlogProvider,
    MistralNewsProvider,
    QwenArticleApiProvider,
    XAINewsProvider,
    SourceSpecificHtmlProvider,
)

logger = logging.getLogger("nanobot.news_daily.official")

def _source_config(descriptor) -> SourceConfig:
    return SourceConfig(
        name=descriptor.source_id,
        type=(
            "api_json"
            if descriptor.adapter_kind == "qwen_api_json"
            else (
                "html_list"
                if descriptor.adapter_kind.endswith("_html")
                or descriptor.adapter_kind == "html_list"
                else "rss"
            )
        ),
        adapter_kind=descriptor.adapter_kind,
        url=descriptor.url,
        trust=descriptor.trust_weight,
        weight=descriptor.quality_weight,
        enabled=descriptor.enabled,
        group=descriptor.group,
        modes=list(descriptor.modes),
        category_hint=list(descriptor.category_hints),
        top_story_eligible=descriptor.top_story_eligible,
        max_items_per_run=descriptor.per_run_limit,
        fetch_timeout_seconds=descriptor.fetch_timeout_seconds,
        freshness_policy=descriptor.freshness_policy,
        lifecycle=descriptor.lifecycle,
        domain=descriptor.domain,
    )


DEFAULT_SOURCES: tuple[SourceConfig, ...] = tuple(
    _source_config(descriptor)
    for descriptor in get_news_source_registry().descriptors()
    if "search" not in descriptor.modes or len(descriptor.modes) > 1
)


SOURCE_ADAPTERS = MappingProxyType({
    "anthropic_html": AnthropicNewsProvider,
    "mistral_html": MistralNewsProvider,
    "deepseek_html": DeepSeekUpdatesProvider,
    "qwen_api_json": QwenArticleApiProvider,
    "kimi_html": KimiBlogProvider,
    "xai_html": XAINewsProvider,
    "cohere_html": CohereBlogProvider,
    "meta_html": MetaAIBlogProvider,
})


def create_provider_for_source(cfg: SourceConfig):
    if cfg.adapter_kind == "juya_rss":
        return JuyaProvider()
    cls = SOURCE_ADAPTERS.get(cfg.adapter_kind)
    if cls is not None:
        return cls(url=cfg.url, source_name=cfg.name, trust=cfg.trust)
    if cfg.adapter_kind == "html_list":
        return SourceSpecificHtmlProvider(url=cfg.url, source_name=cfg.name, trust=cfg.trust)
    return RSSProvider(url=cfg.url, source_name=cfg.name, trust=cfg.trust)


SOURCE_TIER_WEIGHT = DEFAULT_NEWS_RANKING_POLICY.source_group_quality
DAILY_QUOTA = DEFAULT_NEWS_RANKING_POLICY.daily_group_quotas


class SourceRegistry:
    """管理所有 RSS 源，支持按 mode 筛选。"""

    def __init__(self):
        self.sources: list[tuple[SourceConfig, Any]] = []
        for descriptor in get_runtime_news_source_registry().descriptors():
            if "search" in descriptor.modes and len(descriptor.modes) == 1:
                continue
            cfg = _source_config(descriptor)
            if not cfg.enabled:
                continue
            prov = create_provider_for_source(cfg)
            self.sources.append((cfg, prov))
        logger.info("[registry] %d sources loaded", len(self.sources))

    def select(self, mode: str = "fast") -> list[tuple[SourceConfig, RSSProvider]]:
        """按 mode 选择源。"""
        selected_ids = {
            descriptor.source_id
            for descriptor in get_runtime_news_source_registry().select(mode)
        }
        return [
            (config, provider)
            for config, provider in self.sources
            if config.name in selected_ids
        ]

    def get_quotas(self) -> dict[str, int]:
        return dict(DAILY_QUOTA)

    def get_tier_weight(self, group: str) -> float:
        return SOURCE_TIER_WEIGHT.get(group, 0.5)


# 全局单例
_registry: SourceRegistry | None = None
_registry_settings_version = -1


def get_registry() -> SourceRegistry:
    global _registry, _registry_settings_version
    from core.settings_service import settings

    if _registry is None or _registry_settings_version != settings.version:
        _registry = SourceRegistry()
        _registry_settings_version = settings.version
    return _registry

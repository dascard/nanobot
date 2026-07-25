"""新闻领域的类型化请求、描述符、信号、策略与审核接口。"""

from core.news.policy import DEFAULT_NEWS_RANKING_POLICY, NewsRankingPolicy
from core.news.signals import (
    NewsReviewReason,
    NewsSignalAssessment,
    NewsSignalExtractor,
)
from core.news.source_registry import (
    NEWS_SOURCE_REGISTRY,
    NewsSourceDescriptor,
    NewsSourceRegistry,
    get_news_source_registry,
)

__all__ = [
    "DEFAULT_NEWS_RANKING_POLICY",
    "NEWS_SOURCE_REGISTRY",
    "NewsRankingPolicy",
    "NewsReviewReason",
    "NewsSignalAssessment",
    "NewsSignalExtractor",
    "NewsSourceDescriptor",
    "NewsSourceRegistry",
    "get_news_source_registry",
]

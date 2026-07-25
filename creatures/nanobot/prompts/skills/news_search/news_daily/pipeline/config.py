"""新闻管线兼容配置投影；事实源位于 ``core.news``。"""

from core.news.policy import DEFAULT_NEWS_RANKING_POLICY
from core.news.signals import (
    KNOWN_ENTITY_ALIASES,
    NEWS_TOKEN_STOP_WORDS,
    TOPIC_SIGNAL_ALIASES,
)
from core.news.source_registry import get_news_source_registry


_POLICY = DEFAULT_NEWS_RANKING_POLICY
DAILY_FRESHNESS_HOURS = _POLICY.daily_freshness_hours
TOP_STORY_FRESHNESS_HOURS = _POLICY.top_story_hours

MAX_FINAL_CLUSTERS = _POLICY.max_final_clusters
MAX_ARTICLES_PER_DOMAIN_FINAL = _POLICY.max_articles_per_domain
MAX_CLUSTERS_PER_DOMAIN_FINAL = _POLICY.max_clusters_per_domain
MAX_SAME_ENTITY_CLUSTERS_DAILY = _POLICY.max_same_entity_clusters

CLUSTER_SIM_THRESHOLD = _POLICY.cluster_similarity_threshold

OFFICIAL_SOURCES = frozenset(
    descriptor.source_id
    for descriptor in get_news_source_registry().descriptors()
    if descriptor.group == "core_provider"
)
OFFICIAL_DOMAINS = frozenset(
    descriptor.domain
    for descriptor in get_news_source_registry().descriptors()
    if descriptor.group == "core_provider"
)
SOURCE_QUALITY = _POLICY.source_group_quality
EVENT_TYPE_WEIGHT = _POLICY.event_type_weight
MAJOR_ENTITIES = _POLICY.major_entities
STOP_WORDS = NEWS_TOKEN_STOP_WORDS
TOPIC_KEYWORDS = TOPIC_SIGNAL_ALIASES
KNOWN_ENTITIES = KNOWN_ENTITY_ALIASES

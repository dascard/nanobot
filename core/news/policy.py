"""新闻排序和配额的冻结确定性 Policy。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from core.tool_contracts.ai_daily import NEWS_LATEST_WINDOW_HOURS


def _frozen_mapping(
    value: Mapping[str, float | int],
) -> Mapping[str, float | int]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class NewsRankingPolicy:
    """集中声明评分、时间窗、配额与审核边界。"""

    policy_id: str
    trust_weight: float
    freshness_weight: float
    relevance_weight: float
    diversity_weight: float
    latest_hours: int
    daily_freshness_hours: int
    top_story_hours: int
    unknown_date_score: float
    review_boundary_min: float
    review_boundary_max: float
    failure_downrank_factor: float
    per_source_quota: int
    max_final_clusters: int
    max_articles_per_domain: int
    max_clusters_per_domain: int
    max_same_entity_clusters: int
    cluster_similarity_threshold: float
    group_priority: tuple[str, ...]
    daily_group_quotas: Mapping[str, int] = field(repr=False)
    quality_group_quotas: Mapping[str, int] = field(repr=False)
    source_group_quality: Mapping[str, float] = field(repr=False)
    event_type_weight: Mapping[str, float] = field(repr=False)
    major_entities: frozenset[str] = field(repr=False)

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise ValueError("NewsRankingPolicy.policy_id 不能为空")
        weights = (
            self.trust_weight,
            self.freshness_weight,
            self.relevance_weight,
            self.diversity_weight,
        )
        if any(not 0 <= float(value) <= 1 for value in weights):
            raise ValueError("新闻评分权重必须位于 [0, 1]")
        if sum(weights) > 1.000001:
            raise ValueError("新闻评分权重总和不能大于 1")
        if not (
            0
            <= self.review_boundary_min
            <= self.review_boundary_max
            <= 1
        ):
            raise ValueError("新闻审核边界必须位于 [0, 1] 且有序")
        if not 0 < self.failure_downrank_factor <= 1:
            raise ValueError("新闻失败降权系数必须位于 (0, 1]")
        if min(
            self.latest_hours,
            self.daily_freshness_hours,
            self.top_story_hours,
            self.per_source_quota,
            self.max_final_clusters,
            self.max_articles_per_domain,
            self.max_clusters_per_domain,
            self.max_same_entity_clusters,
        ) <= 0:
            raise ValueError("新闻窗口和配额必须为正整数")
        if not 0 <= self.cluster_similarity_threshold <= 1:
            raise ValueError("新闻聚类相似度阈值必须位于 [0, 1]")
        for field_name in (
            "daily_group_quotas",
            "quality_group_quotas",
            "source_group_quality",
            "event_type_weight",
        ):
            object.__setattr__(
                self,
                field_name,
                _frozen_mapping(getattr(self, field_name)),
            )


DEFAULT_NEWS_RANKING_POLICY = NewsRankingPolicy(
    policy_id="news_ranking.v1",
    trust_weight=0.40,
    freshness_weight=0.25,
    relevance_weight=0.25,
    diversity_weight=0.10,
    latest_hours=NEWS_LATEST_WINDOW_HOURS,
    daily_freshness_hours=48,
    top_story_hours=36,
    unknown_date_score=0.30,
    review_boundary_min=0.25,
    review_boundary_max=0.65,
    failure_downrank_factor=0.85,
    per_source_quota=2,
    max_final_clusters=8,
    max_articles_per_domain=2,
    max_clusters_per_domain=2,
    max_same_entity_clusters=1,
    cluster_similarity_threshold=0.48,
    group_priority=(
        "core_provider",
        "core_platform",
        "ai_media",
        "research",
        "curated",
        "community",
        "unknown",
    ),
    daily_group_quotas={
        "core_provider": 6,
        "core_platform": 3,
        "ai_media": 4,
        "research": 2,
        "curated": 3,
        "community": 1,
        "unknown": 1,
    },
    quality_group_quotas={
        "core_provider": 4,
        "core_platform": 2,
        "ai_media": 3,
        "research": 1,
        "curated": 2,
        "community": 0,
        "unknown": 1,
    },
    source_group_quality={
        "core_provider": 1.0,
        "core_platform": 0.9,
        "ai_media": 0.75,
        "curated": 0.55,
        "research": 0.55,
        "community": 0.35,
        "unknown": 0.3,
    },
    event_type_weight={
        "model_release": 0.9,
        "benchmark": 0.6,
        "funding": 0.5,
        "product": 0.65,
        "policy": 0.75,
        "research": 0.55,
        "incident": 0.8,
        "infrastructure": 0.7,
    },
    major_entities=frozenset(
        {"openai", "anthropic", "google", "deepseek", "qwen", "kimi", "meta"}
    ),
)

"""核心数据结构——Article / EventCluster / NewsReport。"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass
class Article:
    id: str
    title: str
    url: str
    source: str
    source_group: str
    domain: str
    published_at: datetime | None = None
    summary: str = ""
    content: str = ""

    title_norm: str = ""
    entity_keys: list[str] = field(default_factory=list)
    topic_keys: list[str] = field(default_factory=list)

    freshness_score: float = 0.0
    source_quality_score: float = 0.0
    importance_hint: float = 0.0

    is_official: bool = False
    is_low_freshness: bool = False
    is_time_unknown: bool = False


@dataclass
class EventCluster:
    id: str
    title: str
    articles: list[Article] = field(default_factory=list)

    entities: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    representative: Article | None = None

    first_seen: datetime | None = None
    latest_seen: datetime | None = None

    freshness_score: float = 0.0
    importance_score: float = 0.0
    source_diversity_score: float = 0.0
    final_score: float = 0.0

    source_domains: set[str] = field(default_factory=set)
    is_single_source: bool = True
    is_official_only: bool = False

    known: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    impact: str = ""
    follow_up: list[str] = field(default_factory=list)


@dataclass
class NewsReport:
    mode: Literal["daily", "topic"] = "daily"
    title: str = ""
    generated_at: datetime | None = None

    top_story: EventCluster | None = None
    highlights: list[EventCluster] = field(default_factory=list)
    details: list[EventCluster] = field(default_factory=list)

    dropped_stats: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

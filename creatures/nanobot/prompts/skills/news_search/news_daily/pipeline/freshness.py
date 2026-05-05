"""时效过滤——日报模式硬门槛。"""

from datetime import datetime
from .config import DAILY_FRESHNESS_HOURS, TOP_STORY_FRESHNESS_HOURS
from .models import Article, EventCluster


def compute_freshness(article: Article, now: datetime) -> float:
    if article.published_at is None:
        article.is_time_unknown = True
        return 0.0
    age_hours = (now - article.published_at).total_seconds() / 3600
    if age_hours < 0:
        return 0.2
    if age_hours <= 6:
        return 1.0
    if age_hours <= 24:
        return 0.85
    if age_hours <= 48:
        return 0.65
    if age_hours <= 72:
        return 0.35
    return 0.0


def filter_fresh_articles(articles: list[Article], now: datetime) -> list[Article]:
    kept = []
    for a in articles:
        a.freshness_score = compute_freshness(a, now)
        if a.published_at is None:
            a.is_low_freshness = True
            if a.is_official:
                a.freshness_score = 0.25
                kept.append(a)
            continue
        age_hours = (now - a.published_at).total_seconds() / 3600
        if age_hours > DAILY_FRESHNESS_HOURS:
            a.is_low_freshness = True
            continue
        kept.append(a)
    return kept


def compute_cluster_freshness(cluster: EventCluster, now: datetime) -> float:
    if cluster.latest_seen is None:
        return 0.0
    age_hours = (now - cluster.latest_seen).total_seconds() / 3600
    if age_hours <= 6:
        return 1.0
    if age_hours <= 24:
        return 0.85
    if age_hours <= 48:
        return 0.65
    return 0.35


def can_be_top_story(cluster: EventCluster, now: datetime) -> bool:
    if cluster.latest_seen is None:
        return False
    age_hours = (now - cluster.latest_seen).total_seconds() / 3600
    if age_hours > TOP_STORY_FRESHNESS_HOURS:
        return False
    if cluster.is_single_source and not (cluster.representative and cluster.representative.is_official):
        return False
    return True

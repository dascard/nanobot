"""Cluster 评分 + 多样性选择。"""

from collections import Counter
from datetime import datetime

from .config import (
    MAX_FINAL_CLUSTERS, MAX_CLUSTERS_PER_DOMAIN_FINAL,
    MAX_SAME_ENTITY_CLUSTERS_DAILY, DAILY_FRESHNESS_HOURS,
    EVENT_TYPE_WEIGHT, MAJOR_ENTITIES,
)
from .freshness import compute_cluster_freshness, can_be_top_story
from .models import EventCluster, NewsReport


def _importance(cluster: EventCluster) -> float:
    keys = set(cluster.keywords)
    base = max((EVENT_TYPE_WEIGHT.get(k, 0.3) for k in keys), default=0.3)
    if set(cluster.entities) & MAJOR_ENTITIES:
        base += 0.15
    base += min(0.15, 0.04 * len(cluster.source_domains))
    return min(1.0, base)


def _src_diversity(cluster: EventCluster) -> float:
    n = len(cluster.source_domains)
    return 1.0 if n >= 4 else 0.8 if n == 3 else 0.6 if n == 2 else 0.25


def _src_confidence(cluster: EventCluster) -> float:
    if len(cluster.source_domains) >= 2: return 1.0
    if cluster.representative and cluster.representative.is_official: return 0.75
    return 0.35


def score_clusters(clusters: list[EventCluster], now: datetime) -> list[EventCluster]:
    for c in clusters:
        c.freshness_score = compute_cluster_freshness(c, now)
        c.importance_score = _importance(c)
        c.source_diversity_score = _src_diversity(c)
        conf = _src_confidence(c)
        c.final_score = (
            0.38 * c.importance_score + 0.32 * c.freshness_score
            + 0.18 * c.source_diversity_score + 0.12 * conf
        )
        if c.is_single_source and not (c.representative and c.representative.is_official):
            c.final_score -= 0.18
        if c.latest_seen is None:
            c.final_score = min(c.final_score, 0.35)
    return clusters


def select_diverse_clusters(clusters: list[EventCluster], now: datetime) -> list[EventCluster]:
    clusters = sorted(clusters, key=lambda c: c.final_score, reverse=True)
    selected, domain_cnt, entity_cnt = [], Counter(), Counter()

    for c in clusters:
        if len(selected) >= MAX_FINAL_CLUSTERS:
            break
        if c.latest_seen is None:
            continue
        if (now - c.latest_seen).total_seconds() / 3600 > DAILY_FRESHNESS_HOURS:
            continue
        rep = c.representative
        if rep and domain_cnt[rep.domain] >= MAX_CLUSTERS_PER_DOMAIN_FINAL:
            continue
        if any(entity_cnt[e] >= MAX_SAME_ENTITY_CLUSTERS_DAILY for e in c.entities):
            continue
        selected.append(c)
        if rep: domain_cnt[rep.domain] += 1
        for e in c.entities: entity_cnt[e] += 1
    return selected


def pick_top_story(clusters: list[EventCluster], now: datetime) -> EventCluster | None:
    candidates = [c for c in clusters if can_be_top_story(c, now)]
    return max(candidates, key=lambda c: c.final_score) if candidates else None


def build_daily_report(clusters: list[EventCluster], now: datetime) -> NewsReport:
    top = pick_top_story(clusters, now)
    remaining = [c for c in clusters if top is None or c.id != top.id]
    highlights = select_diverse_clusters(remaining, now)
    return NewsReport(
        mode="daily", title="AI 日报", generated_at=now,
        top_story=top, highlights=highlights[:6],
        details=(highlights[:3] if not top else [top] + highlights[:2]),
    )

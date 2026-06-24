"""事件聚类——同事件多篇文章合并为一个 EventCluster。"""

import hashlib
from collections import Counter as _Counter
from .config import CLUSTER_SIM_THRESHOLD
from .models import Article, EventCluster
from .normalize_v2 import token_set


def _stable_hash(text: str) -> str:
    return hashlib.md5(text.strip().lower().encode()).hexdigest()[:12]


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def article_similarity(a: Article, b: Article) -> float:
    score = 0.0
    score += jaccard(set(a.entity_keys), set(b.entity_keys)) * 0.45
    score += jaccard(set(a.topic_keys), set(b.topic_keys)) * 0.25
    score += jaccard(token_set(a.title_norm), token_set(b.title_norm)) * 0.25
    if a.published_at and b.published_at:
        delta = abs((a.published_at - b.published_at).total_seconds()) / 3600
        if delta <= 24:
            score += 0.05
    return score


def _pick_representative(articles: list[Article]) -> Article:
    def score(a: Article) -> float:
        s = a.freshness_score * 0.35 + a.source_quality_score * 0.30 + a.importance_hint * 0.20
        if a.is_official:
            s += 0.10
        if a.published_at is not None:
            s += 0.05
        return s
    return max(articles, key=score)


def _most_common(seqs: list[list[str]], n: int = 5) -> list[str]:
    return [k for k, _ in _Counter(x for s in seqs for x in s).most_common(n)]


def _refresh_metadata(c: EventCluster):
    c.source_domains = {a.domain for a in c.articles}
    c.is_single_source = len(c.source_domains) == 1
    c.is_official_only = all(a.is_official for a in c.articles)
    times = [a.published_at for a in c.articles if a.published_at]
    c.first_seen = min(times) if times else None
    c.latest_seen = max(times) if times else None
    c.entities = _most_common([a.entity_keys for a in c.articles])
    c.keywords = _most_common([a.topic_keys for a in c.articles])
    c.representative = _pick_representative(c.articles)
    c.title = c.representative.title if c.representative else c.articles[0].title


def cluster_articles(articles: list[Article]) -> list[EventCluster]:
    articles = sorted(articles, key=lambda a: (a.freshness_score, a.source_quality_score), reverse=True)
    clusters: list[EventCluster] = []

    for article in articles:
        best, best_score = None, 0.0
        for c in clusters:
            sim = article_similarity(article, c.representative) if c.representative else 0.0
            if sim > best_score:
                best_score, best = sim, c
        if best and best_score >= CLUSTER_SIM_THRESHOLD:
            best.articles.append(article)
            _refresh_metadata(best)
        else:
            c = EventCluster(
                id=_stable_hash(article.title_norm + article.domain),
                title=article.title, articles=[article], representative=article,
            )
            _refresh_metadata(c)
            clusters.append(c)
    return clusters

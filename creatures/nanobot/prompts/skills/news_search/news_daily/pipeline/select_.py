"""候选选择 + 配额策略——基于 source_group 而非 trust。"""

from core.news.policy import DEFAULT_NEWS_RANKING_POLICY

from ..schema import NewsItem

GROUP_PRIORITY = DEFAULT_NEWS_RANKING_POLICY.group_priority
QUALITY_QUOTAS = DEFAULT_NEWS_RANKING_POLICY.quality_group_quotas
MAX_ITEMS_PER_SOURCE = DEFAULT_NEWS_RANKING_POLICY.per_source_quota


def _item_group(item: NewsItem) -> str:
    sg = getattr(item, "source_group", "") or ""
    if sg in GROUP_PRIORITY:
        return sg
    return "unknown"


def select_items_by_quota(items: list[NewsItem], max_items: int = 10) -> list[NewsItem]:
    """按来源类别配额选择候选条目。"""
    quotas = dict(QUALITY_QUOTAS)
    buckets: dict[str, list[NewsItem]] = {}
    for item in items:
        group = _item_group(item)
        buckets.setdefault(group, []).append(item)

    result = []
    source_counts: dict[str, int] = {}
    for group in GROUP_PRIORITY:
        limit = quotas.get(group, 2)
        added = 0
        for item in buckets.get(group, []):
            source_name = item.source_name or "unknown"
            if source_counts.get(source_name, 0) >= MAX_ITEMS_PER_SOURCE:
                continue
            result.append(item)
            source_counts[source_name] = source_counts.get(source_name, 0) + 1
            added += 1
            if added >= limit or len(result) >= max_items:
                break
        if len(result) >= max_items:
            break

    return result[:max_items]

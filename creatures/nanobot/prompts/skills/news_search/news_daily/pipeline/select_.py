"""候选选择 + 配额策略——基于 source_group 而非 trust。"""

from ..schema import NewsItem

GROUP_PRIORITY = ["core_provider", "curated", "core_platform", "ai_media", "research", "community"]
QUALITY_QUOTAS = {
    "core_provider": 4, "core_platform": 2, "ai_media": 3,
    "research": 1, "curated": 2, "community": 0,
}
MAX_ITEMS_PER_SOURCE = 2


def _item_group(item: NewsItem) -> str:
    sg = getattr(item, "source_group", "") or ""
    if sg in GROUP_PRIORITY:
        return sg
    # fallback: trust-based inference for items without source_group
    if item.trust >= 0.88:
        return "core_provider"
    if item.trust >= 0.78:
        return "core_platform"
    if item.trust >= 0.60:
        return "ai_media"
    return "curated"


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

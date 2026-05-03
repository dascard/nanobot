"""候选选择 + 配额策略。"""

from ..schema import NewsItem

QUALITY_QUOTAS = {"official": 4, "research": 2, "media": 3, "curated": 2, "community": 0}


def _item_group(item: NewsItem) -> str:
    if item.trust >= 0.88:
        return "official"
    if item.trust >= 0.78:
        return "media"
    if item.trust >= 0.60:
        return "curated"
    return "community"


def select_items_by_quota(items: list[NewsItem], max_items: int = 10) -> list[NewsItem]:
    """按来源类别配额选择候选条目。"""
    quotas = dict(QUALITY_QUOTAS)
    buckets: dict[str, list[NewsItem]] = {}
    for item in items:
        group = _item_group(item)
        buckets.setdefault(group, []).append(item)

    result = []
    for group in ["official", "media", "curated", "research", "community"]:
        limit = quotas.get(group, 2)
        result.extend(buckets.get(group, [])[:limit])

    return result[:max_items]

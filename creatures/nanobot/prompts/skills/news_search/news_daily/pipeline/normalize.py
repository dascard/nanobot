"""统一字段 + 时效过滤。"""

import logging
import hashlib
from datetime import datetime, timedelta
from ..schema import NewsItem

logger = logging.getLogger("nanobot.news_daily.normalize")


def normalize_items(items: list[NewsItem]) -> list[NewsItem]:
    """补充 id/domain/fetched_at，过滤无效条目。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    result = []
    for item in items:
        if not item.title or not item.url:
            continue
        if not item.id:
            item.id = hashlib.md5(item.url.encode()).hexdigest()[:12]
        if not item.fetched_at:
            item.fetched_at = now
        # Infer source_group from trust if not set
        if not item.source_group:
            if item.trust >= 0.88:
                item.source_group = "core_provider"
            elif item.trust >= 0.78:
                item.source_group = "core_platform"
            elif item.trust >= 0.70:
                item.source_group = "ai_media"
            else:
                item.source_group = "curated"
        result.append(item)
    return result


_DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%b %d, %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%d %b %Y",
]


def _parse_date(raw: str):
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    return None


def filter_recent(items: list[NewsItem], hours: int = 72, keep_unknown: bool = False) -> list[NewsItem]:
    """过滤过旧条目；latest 默认丢弃无日期或无法解析日期的条目。"""
    cutoff = datetime.now() - timedelta(hours=hours)
    result = []
    for item in items:
        if not item.published_at:
            if keep_unknown:
                result.append(item)
            continue
        dt = _parse_date(item.published_at)
        if dt is None:
            if keep_unknown:
                result.append(item)
        elif dt >= cutoff:
            result.append(item)
    logger.debug("[normalize] %d → %d after %dh filter", len(items), len(result), hours)
    return result

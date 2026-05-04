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


def filter_recent(items: list[NewsItem], hours: int = 72) -> list[NewsItem]:
    """过滤过旧条目。"""
    cutoff = datetime.now() - timedelta(hours=hours)
    result = []
    for item in items:
        if not item.published_at:
            result.append(item)  # 无日期保留
            continue
        try:
            dt = datetime.strptime(item.published_at, "%Y-%m-%d")
            if dt >= cutoff:
                result.append(item)
        except Exception:
            result.append(item)
    logger.debug("[normalize] %d → %d after %dh filter", len(items), len(result), hours)
    return result

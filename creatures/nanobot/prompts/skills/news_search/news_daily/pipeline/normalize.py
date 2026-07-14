"""统一字段 + 时效过滤。"""

import logging
import hashlib
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
import re
from core.time_utils import db_now_naive
from core.tool_contracts.ai_daily import AI_DAILY_TIMEZONE, AiDailyRequest
from ..schema import NewsItem

logger = logging.getLogger("nanobot.news_daily.normalize")


def normalize_items(items: list[NewsItem]) -> list[NewsItem]:
    """补充 id/domain/fetched_at，过滤无效条目。"""
    now = db_now_naive().strftime("%Y-%m-%d %H:%M")
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


_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _build_datetime(year: int, month: int, day: int) -> datetime | None:
    try:
        return datetime.fromisoformat(f"{year:04d}-{month:02d}-{day:02d}")
    except ValueError:
        return None


def _parse_date(raw: str):
    value = (raw or "").strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            return dt.astimezone(AI_DAILY_TIMEZONE).replace(tzinfo=None)
        return dt
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt is not None and dt.tzinfo is not None:
            return dt.astimezone(AI_DAILY_TIMEZONE).replace(tzinfo=None)
        return dt
    except (TypeError, ValueError):
        pass
    m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})$", value)
    if m:
        return _build_datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", value)
    if m:
        return _build_datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})$", value)
    if m:
        month = _MONTHS.get(m.group(1).lower())
        if month:
            return _build_datetime(int(m.group(3)), month, int(m.group(2)))
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})$", value)
    if m:
        month = _MONTHS.get(m.group(2).lower())
        if month:
            return _build_datetime(int(m.group(3)), month, int(m.group(1)))
    return None


def filter_recent(items: list[NewsItem], hours: int = 72, keep_unknown: bool = False) -> list[NewsItem]:
    """过滤过旧条目；latest 默认丢弃无日期或无法解析日期的条目。"""
    cutoff = db_now_naive() - timedelta(hours=hours)
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


def filter_for_ai_daily_request(
    items: list[NewsItem],
    request: AiDailyRequest,
    *,
    keep_unknown: bool = False,
) -> list[NewsItem]:
    """按请求的北京时间半开窗口过滤条目。"""
    start = request.window_start_naive
    end = request.window_end_naive
    result = []
    for item in items:
        published_at = _parse_date(item.published_at)
        if published_at is None:
            if keep_unknown:
                result.append(item)
            continue
        if start <= published_at < end:
            result.append(item)
    logger.debug(
        "[normalize] %d → %d after %s window filter",
        len(items),
        len(result),
        request.freshness,
    )
    return result

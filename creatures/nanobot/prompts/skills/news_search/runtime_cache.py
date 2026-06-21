"""运行时新闻搜索缓存 helper。"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any


NEWS_SEARCH_CACHE_TTL_SECONDS = int(os.environ.get("NEWS_SEARCH_CACHE_TTL_SECONDS", "300"))
NEWS_SEARCH_CACHE_MAX_ENTRIES = 64
_NEWS_SEARCH_CACHE: dict[tuple[Any, ...], tuple[float, str]] = {}
_NEWS_SEARCH_CACHE_LOCK = threading.Lock()


DAILY_DIGEST_KEYWORDS = {
    "日报",
    "早报",
    "每日",
    "今日ai",
    "今天ai",
    "ai daily",
    "morning briefing",
    "简报",
    "digest",
}


def _coerce_date(year: int | str, month: int | str, day: int | str) -> str | None:
    try:
        return datetime(int(year), int(month), int(day)).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _extract_date(query: str, *, now: datetime | None = None) -> str | None:
    text = query or ""
    current = now or datetime.now()

    match = re.search(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", text)
    if match:
        return _coerce_date(match.group(1), match.group(2), match.group(3))

    match = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", text)
    if match:
        return _coerce_date(match.group(1), match.group(2), match.group(3))

    match = re.search(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", text)
    if match:
        return _coerce_date(current.year, match.group(1), match.group(2))

    if re.search(r"\b(today)\b|今天|今日", text, flags=re.IGNORECASE):
        return current.strftime("%Y-%m-%d")

    return None


def _is_daily_digest_query(query: str) -> bool:
    q = (query or "").lower()
    return any(keyword in q for keyword in DAILY_DIGEST_KEYWORDS)


def _news_search_cache_key(
    query: str,
    max_results: int,
    mode: str = "fast",
    user_id: str = "",
    session_id: str = "",
    *,
    now: datetime | None = None,
    date_extractor: Callable[[str], str | None] | None = None,
    daily_digest_detector: Callable[[str], bool] | None = None,
) -> tuple[Any, ...]:
    del user_id, session_id
    current = now or datetime.now()
    q = re.sub(r"\s+", " ", (query or "").lower()).strip()
    is_daily_digest = daily_digest_detector or _is_daily_digest_query
    if date_extractor is None:
        target_date = _extract_date(query, now=current)
    else:
        target_date = date_extractor(query)
    today = current.strftime("%Y-%m-%d")
    version = "v2_20260503"
    if is_daily_digest(q):
        return (version, "daily_ai", target_date or today, int(max_results), mode)
    return (version, "query", q, int(max_results), mode)


def _get_cached_news_result(
    key: tuple[Any, ...],
    *,
    ttl_seconds: int | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> str | None:
    ttl = NEWS_SEARCH_CACHE_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    now = monotonic()
    with _NEWS_SEARCH_CACHE_LOCK:
        cached = _NEWS_SEARCH_CACHE.get(key)
        if not cached:
            return None
        created_at, output = cached
        if now - created_at > ttl:
            _NEWS_SEARCH_CACHE.pop(key, None)
            return None
        return output


def _store_cached_news_result(
    key: tuple[Any, ...],
    output: str,
    *,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    with _NEWS_SEARCH_CACHE_LOCK:
        if len(_NEWS_SEARCH_CACHE) > NEWS_SEARCH_CACHE_MAX_ENTRIES:
            oldest_key = min(_NEWS_SEARCH_CACHE, key=lambda k: _NEWS_SEARCH_CACHE[k][0])
            _NEWS_SEARCH_CACHE.pop(oldest_key, None)
        _NEWS_SEARCH_CACHE[key] = (monotonic(), output)

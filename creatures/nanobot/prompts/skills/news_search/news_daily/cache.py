"""TTL 缓存。"""

import time
import threading
import hashlib
from datetime import datetime

logger = __import__("logging").getLogger("nanobot.news_daily.cache")

_cache: dict[str, tuple[float, str, str]] = {}
_lock = threading.Lock()

TTL = {
    "fast": 1800,      # 30min
    "quality": 3600,  # 60min
    "error": 120,
}

SOURCE_SET_VERSION = "ai_sources_v2_patched"


def _daily_query_key(query: str) -> str:
    q = (query or "").strip().lower()
    if any(k in q for k in ["日报", "早报", "快讯", "今日", "今天", "daily", "brief"]):
        return "daily_ai"
    return hashlib.md5(q.encode()).hexdigest()[:10]


def make_key(query: str, mode: str, limit: int, output_format: str = "html") -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    q = _daily_query_key(query)
    return f"news:{date}:{mode}:{SOURCE_SET_VERSION}:{output_format}:{limit}:{q}"


def get(key: str) -> str | None:
    with _lock:
        entry = _cache.get(key)
        if not entry:
            return None
        ts, value, mode = entry
        if time.monotonic() - ts < TTL.get(mode, 1800):
            return value
        del _cache[key]
    return None


def set(key: str, value: str, mode: str = "quality") -> None:
    with _lock:
        _cache[key] = (time.monotonic(), value, mode)
        if len(_cache) > 100:
            now = time.monotonic()
            stale = [k for k, v in _cache.items() if now - v[0] > 7200]
            for k in stale:
                del _cache[k]

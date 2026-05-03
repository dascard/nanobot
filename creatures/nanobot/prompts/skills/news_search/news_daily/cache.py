"""TTL 缓存。"""

import time
import threading
import hashlib
from datetime import datetime

logger = __import__("logging").getLogger("nanobot.news_daily.cache")

_cache: dict[str, tuple[float, str]] = {}
_lock = threading.Lock()

TTL = {
    "fast": 1800,     # 30min
    "quality": 1800,
    "research": 600,  # 10min
    "error": 120,     # 2min
}


def make_key(query: str, mode: str, limit: int) -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    q = hashlib.md5(query.strip().lower().encode()).hexdigest()[:8]
    return f"news:{date}:{mode}:{limit}:{q}"


def get(key: str) -> str | None:
    with _lock:
        entry = _cache.get(key)
        if entry and time.monotonic() - entry[0] < TTL.get("fast", 1800):
            return entry[1]
        if entry:
            del _cache[key]
    return None


def set(key: str, value: str, mode: str = "fast") -> None:
    with _lock:
        _cache[key] = (time.monotonic(), value)
        # 惰性清理
        if len(_cache) > 100:
            now = time.monotonic()
            stale = [k for k, v in _cache.items() if now - v[0] > 3600]
            for k in stale:
                del _cache[k]

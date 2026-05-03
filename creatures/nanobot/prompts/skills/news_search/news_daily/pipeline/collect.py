"""并发抓取多个 source。"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from ..schema import NewsItem
from ..sources.base import SourceProvider

logger = logging.getLogger("nanobot.news_daily.collect")


def collect_sources(
    providers: list[SourceProvider],
    limit_per_source: int = 8,
    timeout: int = 8,
) -> list[NewsItem]:
    """并发抓取多个 provider，合并结果。"""
    if not providers:
        return []

    items: list[NewsItem] = []
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=min(4, len(providers))) as ex:
        futures = {ex.submit(p.fetch, limit_per_source): p for p in providers}
        for future in as_completed(futures, timeout=timeout + 2):
            provider = futures[future]
            try:
                result = future.result(timeout=timeout)
                items.extend(result)
                logger.debug("[collect] %s: %d items", provider.name, len(result))
            except Exception as e:
                logger.warning("[collect] %s failed: %s", provider.name, e)

    logger.info("[collect] %d providers → %d items in %.1fs",
                len(providers), len(items), time.time() - t0)
    return items

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

    ex = ThreadPoolExecutor(max_workers=min(8, len(providers)))
    try:
        futures = {ex.submit(p.fetch, limit_per_source): p for p in providers}
        try:
            for future in as_completed(futures, timeout=timeout + 2):
                provider = futures[future]
                try:
                    result = future.result(timeout=2)
                    items.extend(result)
                    logger.debug("[collect] %s: %d items", provider.name, len(result))
                except Exception as e:
                    logger.warning("[collect] %s failed: %s", provider.name, e)
        except TimeoutError:
            n_done = sum(1 for f in futures if f.done())
            logger.warning("[collect] timeout: %d/%d done, using partial results", n_done, len(futures))
        except Exception as e:
            logger.warning("[collect] futures error: %s (%d items)", e, len(items))
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

    logger.info("[collect] %d providers → %d items in %.1fs",
                len(providers), len(items), time.time() - t0)
    return items

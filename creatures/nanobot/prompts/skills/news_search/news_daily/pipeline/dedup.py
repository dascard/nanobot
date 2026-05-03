"""去重——URL + 标题 + 来源优先级。"""

import logging
import re
from ..schema import NewsItem

logger = logging.getLogger("nanobot.news_daily.dedup")


def _norm_title(title: str) -> str:
    t = title.lower().strip()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[｜|_-].*$", "", t)
    t = re.sub(r"[\[\]【】]", "", t)
    return t


def dedup_items(items: list[NewsItem]) -> list[NewsItem]:
    """URL + 归一化标题去重。相同事件保留 trust 高的来源。"""
    seen_urls = set()
    seen_titles = {}
    result = []

    for item in items:
        # URL 去重
        if item.url in seen_urls:
            continue
        seen_urls.add(item.url)

        # 标题去重
        key = _norm_title(item.title)[:60]
        if key in seen_titles:
            existing = seen_titles[key]
            # 保留 trust 更高的
            if item.trust > existing.trust:
                seen_titles[key] = item
                result = [item if x is existing else x for x in result]
            continue
        seen_titles[key] = item
        result.append(item)

    logger.debug("[dedup] %d → %d", len(items), len(result))
    return result

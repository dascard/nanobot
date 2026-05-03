"""策展源——Juya AI Daily + 可选扩展。"""

import logging
from .rss import RSSProvider
from ..schema import NewsItem

logger = logging.getLogger("nanobot.news_daily.curated")

JUYA_RSS_URL = "https://imjuya.github.io/juya-ai-daily/rss.xml"


class JuyaProvider(RSSProvider):
    """Juya AI Daily RSS，拆条为多条 NewsItem。"""

    name = "juya_ai_daily"

    def __init__(self):
        super().__init__(url=JUYA_RSS_URL, source_name="Juya AI Daily", trust=0.80)

    def fetch(self, limit: int = 10) -> list[NewsItem]:
        items = super().fetch(limit=limit * 3 if limit else 30)
        # Juya 每篇日报包含多条新闻——拆条已在父类完成
        # 这里只做去重和截断
        seen = set()
        result = []
        for item in items:
            key = item.title[:60]
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        logger.info("[juya] %d items after dedup", len(result))
        return result[:limit]


CURATED_SOURCES = {
    "juya": JuyaProvider,
}

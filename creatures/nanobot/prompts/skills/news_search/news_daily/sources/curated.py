"""策展源——Juya AI Daily + 可选扩展。"""

import logging
import re
from core.time_utils import db_now_naive
from .rss import RSSProvider
from ..schema import NewsItem

logger = logging.getLogger("nanobot.news_daily.curated")

JUYA_RSS_URL = "https://imjuya.github.io/juya-ai-daily/rss.xml"

# 拆条: 按 #N 编号切分
_SPLIT_PATTERN = re.compile(r"(?:^|\s)#(\d{1,2})\s+")
# 分类词
_CAT_HEADERS = {"概览", "要闻", "开发生态", "行业动态", "研究前沿", "产品发布", "融资动态",
                "政策监管", "开源动态", "模型发布", "AI应用", "工具推荐", "视频版",
                "资源推荐", "社区热议", "新模型", "AI资讯"}


class JuyaProvider(RSSProvider):
    """Juya AI Daily RSS，拆条为多条 NewsItem——只取详情区。"""

    name = "juya_ai_daily"

    def __init__(self):
        super().__init__(url=JUYA_RSS_URL, source_name="Juya AI Daily", trust=0.80)

    def fetch(self, limit: int = 10) -> list[NewsItem]:
        """从 Juya RSS 抓取并按 #N 拆分为独立事件——只取详情区。"""
        from datetime import timedelta
        cutoff = (db_now_naive() - timedelta(days=3)).strftime("%Y-%m-%d")

        raw_items = super().fetch(limit=min(limit * 3, 30))
        result = []
        seen = set()

        for parent in raw_items:
            if parent.published_at < cutoff:
                continue

            text = parent.content_excerpt or parent.summary or ""
            if not text:
                continue

            text = re.sub(r'AI\s*早报\s*\d{4}-\d{2}-\d{2}\s*', '', text)
            text = re.sub(r'视频版：[\w\s｜|]+', '', text)

            # 索引区 #N 后紧跟短标题，详情区 #N 后紧跟长描述（>30 字符）
            # 找到详情区起点：第二个 #1 或第一个后跟长描述的 #N
            detail_start = 0
            first_hash = text.find("#1")
            if first_hash >= 0:
                second = text.find("#1", first_hash + 2)
                if second >= 0:
                    detail_start = second
            if detail_start > 0:
                text = text[detail_start:]

            parts = _SPLIT_PATTERN.split(text)
            if len(parts) < 3:
                continue

            events = []
            i = 1
            while i + 1 < len(parts):
                num = parts[i]
                body = parts[i + 1].strip()
                i += 2
                # 跳过短文本（索引区残余/纯链接）
                if len(body) < 30:
                    continue
                events.append((num, body))

            for num, body in events:
                # 标题 = 第一句（到句号/冒号/链接）
                title_end = len(body)
                for sep in ["。", "：", "相关链接", "http"]:
                    idx = body.find(sep)
                    if 12 < idx < title_end:
                        title_end = idx
                title = body[:title_end].strip().rstrip(" ↗")
                summary = body.strip()

                if len(title) < 8:
                    continue

                key = title[:80]
                if key in seen:
                    continue
                seen.add(key)

                result.append(NewsItem(
                    id=f"juya_{parent.published_at}_{num}",
                    title=title[:120],
                    url=parent.url,
                    summary=summary[:400],
                    content_excerpt=summary[:1200],
                    source_name=self.source_name,
                    source_type="curated",
                    domain=parent.domain,
                    trust=self.trust,
                    published_at=parent.published_at,
                    category="AI资讯",
                    freshness=0.9,
                    source_group="curated",
                ))

        logger.info("[juya] %d raw → %d split (last 3 days)", len(raw_items), len(result))
        return result[:limit * 2]


CURATED_SOURCES = {
    "juya": JuyaProvider,
}

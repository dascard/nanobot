"""通用 RSS/Atom Provider。"""

import logging
import re
import os as _os
from datetime import datetime
from urllib.request import build_opener, ProxyHandler, Request
from core.time_utils import db_now_naive
from ..schema import NewsItem

logger = logging.getLogger("nanobot.news_daily.rss")

_proxy_url = _os.environ.get("http_proxy") or _os.environ.get("HTTP_PROXY") or ""
_opener = build_opener(ProxyHandler({"http": _proxy_url, "https": _proxy_url})) if _proxy_url else build_opener()


def _fetch_url(url: str, timeout: int = 8) -> str:
    req = Request(url, headers={"User-Agent": "Nanobot/2.0"})
    with _opener.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


class RSSProvider:
    """通用 RSS/Atom feed provider。"""

    name = "rss"

    def __init__(self, url: str, source_name: str = "RSS", trust: float = 0.5):
        self.url = url
        self.source_name = source_name
        self.trust = trust

    def fetch(self, limit: int = 10) -> list[NewsItem]:
        try:
            raw = _fetch_url(self.url, timeout=8)
            return self._parse(raw, limit)
        except Exception as e:
            logger.warning("[rss] %s fetch failed: %s", self.source_name, e)
            return []

    def _parse(self, xml: str, limit: int) -> list[NewsItem]:
        """使用 feedparser 解析 RSS/Atom——原生支持两种格式。"""
        import feedparser
        items = []
        try:
            feed = feedparser.parse(xml)
            for entry in feed.entries[:limit]:
                title = (entry.get("title") or "").strip()
                link = (entry.get("link") or entry.get("links", [{}])[0].get("href") or "").strip()
                desc = (entry.get("description") or entry.get("summary") or "").strip()
                pub = (entry.get("published") or entry.get("pubDate") or entry.get("updated") or "")

                if not title or not link:
                    continue

                pub_date = ""
                if pub:
                    try:
                        from datetime import date

                        parsed = getattr(entry, "published_parsed", None)
                        pub_date = date(*parsed[:3]).isoformat() if parsed else pub[:10]
                    except Exception:
                        pub_date = str(pub)[:10]

                domain = self._domain(link)

                # HTML content → 纯文本
                content_text = ""
                content_list = entry.get("content", [])
                if content_list:
                    raw_html = content_list[0].get("value", "")
                    if raw_html:
                        content_text = re.sub(r'<[^>]+>', ' ', raw_html)
                        content_text = re.sub(r'\s+', ' ', content_text).strip()

                items.append(NewsItem(
                    id=link, title=title, url=link,
                    summary=(content_text or desc)[:600],
                    content_excerpt=content_text[:3000],
                    source_name=self.source_name, source_type="rss",
                    source_group=getattr(self, "_group", "curated"),
                    source_weight=getattr(self, "_weight", 1.0),
                    top_story_eligible=getattr(self, "_top_story_eligible", True),
                    category_hint=getattr(self, "_category_hint", []),
                    domain=domain, published_at=pub_date, trust=self.trust,
                    freshness=1.0 if self._is_recent(pub_date) else 0.5,
                ))
        except Exception as e:
            logger.warning("[rss] parse error for %s: %s", self.source_name, e)
        return items[:limit]



    @staticmethod
    def _domain(url: str) -> str:
        try:
            from urllib.parse import urlparse
            return (urlparse(url).netloc or "").lower().lstrip("www.")
        except Exception:
            return ""

    @staticmethod
    def _is_recent(pub_date: str) -> bool:
        if not pub_date:
            return False
        try:
            dt = datetime.fromisoformat(pub_date)
            return (db_now_naive() - dt).days <= 1
        except Exception:
            return False

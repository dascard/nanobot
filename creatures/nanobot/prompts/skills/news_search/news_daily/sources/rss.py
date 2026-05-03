"""通用 RSS/Atom Provider。"""

import logging
import re
import os as _os
from email.utils import parsedate_to_datetime
from urllib.request import urlopen, build_opener, ProxyHandler, Request
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
        import xml.etree.ElementTree as ET
        items = []
        try:
            root = ET.fromstring(xml)
            ns = self._ns(root)
            entries = root.findall(".//item") or root.findall(f".//{{{ns.get('', '')}}}entry")
            if not entries:
                channel = root.find("channel")
                if channel is not None:
                    entries = channel.findall("item")
            for entry in entries[:limit]:
                title = self._text(entry, "title", ns).strip()
                link = self._text(entry, "link", ns).strip()
                desc = self._text(entry, "description", ns).strip()
                pub = self._text(entry, "pubDate", ns).strip() or self._text(entry, "published", ns).strip()

                if not title or not link:
                    continue

                pub_date = ""
                if pub:
                    try:
                        pub_date = parsedate_to_datetime(pub).strftime("%Y-%m-%d")
                    except Exception:
                        pub_date = pub[:10]

                domain = self._domain(link)

                items.append(NewsItem(
                    id=link, title=title, url=link, summary=desc[:200],
                    source_name=self.source_name, source_type="rss",
                    domain=domain, published_at=pub_date, trust=self.trust,
                    freshness=1.0 if self._is_recent(pub_date) else 0.5,
                ))
        except ET.ParseError as e:
            logger.warning("[rss] xml parse error for %s: %s", self.source_name, e)
        return items[:limit]

    @staticmethod
    def _ns(root) -> dict:
        m = re.match(r"\{(.*)\}", root.tag)
        return {"": m.group(1)} if m else {}

    @staticmethod
    def _text(el, tag, ns) -> str:
        for t in (tag, f"{{{ns.get('', '')}}}{tag}"):
            e = el.find(t)
            if e is not None and e.text:
                return e.text
        return ""

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
            from datetime import datetime, timedelta
            dt = datetime.strptime(pub_date, "%Y-%m-%d")
            return (datetime.now() - dt).days <= 1
        except Exception:
            return False

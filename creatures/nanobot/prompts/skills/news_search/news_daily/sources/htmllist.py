"""HtmlListProvider——从无 RSS 的官方博客/新闻页提取标题列表。"""

import logging
import re
from urllib.request import urlopen, build_opener, ProxyHandler, Request
import os as _os
from ..schema import NewsItem

logger = logging.getLogger("nanobot.news_daily.htmllist")

_proxy_url = _os.environ.get("http_proxy") or _os.environ.get("HTTP_PROXY") or ""
_opener = build_opener(ProxyHandler({"http": _proxy_url, "https": _proxy_url})) if _proxy_url else build_opener()


def _fetch(url: str, timeout: int = 8) -> str:
    req = Request(url, headers={"User-Agent": "Nanobot/2.0"})
    with _opener.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _domain(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return (urlparse(url).netloc or "").lower().lstrip("www.")
    except Exception:
        return ""


class HtmlListProvider:
    """从 HTML 页面提取标题和链接列表——用于没有 RSS 的官方博客。"""

    name = "html_list"

    def __init__(self, url: str, source_name: str = "Blog", trust: float = 0.8,
                 link_selector: str = "", title_selector: str = ""):
        self.url = url
        self.source_name = source_name
        self.trust = trust
        self.link_selector = link_selector
        self.title_selector = title_selector

    def fetch(self, limit: int = 5) -> list[NewsItem]:
        try:
            html = _fetch(self.url, timeout=8)
            return self._extract(html, limit)
        except Exception as e:
            logger.warning("[htmllist] %s fetch failed: %s", self.source_name, e)
            return []

    def _extract(self, html: str, limit: int) -> list[NewsItem]:
        """从 HTML 中提取文章标题和链接。"""
        items = []
        dom = self._domain(self.url)

        # 找所有 <a> 标签，提取看起来像文章链接的
        links = re.findall(
            r'<a[^>]*href="([^"]*/(?:blog|news|engineering|articles?)/[^"]*)"[^>]*>(.*?)</a>',
            html, re.IGNORECASE | re.DOTALL,
        )
        if not links:
            # 退一步：找所有含标题样式的链接
            links = re.findall(
                r'<a[^>]*href="([^"]+/(?:blog|news|engineering)[^"]*)"[^>]*>([\s\S]{10,200}?)</a>',
                html, re.IGNORECASE,
            )

        seen = set()
        for href, raw_title in links:
            raw_title = re.sub(r'<[^>]+>', '', raw_title).strip()
            raw_title = re.sub(r'\s+', ' ', raw_title)
            if len(raw_title) < 15 or len(raw_title) > 200:
                continue
            url = href if href.startswith("http") else f"https://{dom}{href}"
            key = raw_title[:60]
            if key in seen:
                continue
            seen.add(key)
            items.append(NewsItem(
                id=url, title=raw_title[:120], url=url,
                source_name=self.source_name, source_type="html_list",
                domain=_domain(url), trust=self.trust,
            ))
            if len(items) >= limit:
                break

        logger.info("[htmllist] %s extracted %d items from HTML", self.source_name, len(items))
        return items

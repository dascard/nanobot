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
        """从 HTML 提取标题/链接/日期/摘要。"""
        import re as _re
        items = []
        dom = _domain(self.url)

        # 日期: <time> 标签或常见格式
        def _find_date(text):
            for ptn in [
                r'<time[^>]*datetime=["\'](\d{4}-\d{2}-\d{2})',
                r'(\d{4}[/-]\d{2}[/-]\d{2})',
                r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}',
            ]:
                m = _re.search(ptn, text, _re.IGNORECASE)
                if m:
                    return m.group(0)[:10]
            return ""

        # 摘要: meta description 或标签周围文本
        def _find_summary(text):
            m = _re.search(r'<meta[^>]*name="description"[^>]*content="([^"]{20,250})"', text, _re.IGNORECASE)
            if m:
                return m.group(1)
            clean = _re.sub(r'<[^>]+>', ' ', text)
            clean = _re.sub(r'\s+', ' ', clean).strip()
            return clean[:200] if len(clean) > 40 else ""

        seen = set()
        for m in _re.finditer(
            r'<a[^>]*href="([^"]*/(?:blog|news|engineering|articles?|product|research)/[^"]*)"[^>]*>(.*?)</a>',
            html, _re.IGNORECASE | _re.DOTALL,
        ):
            href, raw_title = m.group(1), m.group(2)
            raw_title = _re.sub(r'<[^>]+>', '', raw_title).strip()
            raw_title = _re.sub(r'\s+', ' ', raw_title)
            if len(raw_title) < 12 or len(raw_title) > 250:
                continue
            url = href if href.startswith("http") else f"https://{dom}{href}"
            if raw_title[:60] in seen:
                continue
            seen.add(raw_title[:60])

            # 取链接周围 1000 字符上下文用于日期和摘要
            ctx_start = max(0, m.start() - 500)
            ctx_end = min(len(html), m.end() + 500)
            ctx = html[ctx_start:ctx_end]

            items.append(NewsItem(
                id=url, title=raw_title[:120], url=url,
                summary=_find_summary(ctx)[:200],
                source_name=self.source_name, source_type="html_list",
                domain=_domain(url), trust=self.trust,
                published_at=_find_date(ctx),
                freshness=0.5,
            ))
            if len(items) >= limit:
                break

        logger.info("[htmllist] %s: %d items", self.source_name, len(items))
        return items

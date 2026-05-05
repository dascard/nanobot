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
            m = _re.search(r'<time[^>]*datetime=["\'](\d{4}-\d{2}-\d{2})', text, _re.IGNORECASE)
            if m:
                return m.group(1)
            m = _re.search(r'(\d{4}[/-]\d{2}[/-]\d{2})', text, _re.IGNORECASE)
            if m:
                return m.group(1).replace("/", "-")
            m = _re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}', text, _re.IGNORECASE)
            if m:
                return m.group(0)
            return ""

        # 摘要: meta description 或标签周围文本
        def _find_summary(text):
            m = _re.search(r'<meta[^>]*name="description"[^>]*content="([^"]{20,250})"', text, _re.IGNORECASE)
            if m:
                return m.group(1)
            clean = _re.sub(r'<[^>]+>', ' ', text)
            clean = _re.sub(r'\s+', ' ', clean).strip()
            return clean[:200] if len(clean) > 40 else ""

        # 页面级 meta description（fallback 用）
        page_meta = ""
        m_meta = _re.search(r'<meta[^>]*name="description"[^>]*content="([^"]{20,300})"', html, _re.IGNORECASE)
        if m_meta:
            page_meta = m_meta.group(1)
        if not page_meta:
            m_og = _re.search(r'<meta[^>]*property="og:description"[^>]*content="([^"]{20,300})"', html, _re.IGNORECASE)
            if m_og:
                page_meta = m_og.group(1)

        seen = set()
        for m in _re.finditer(
            r'<a[^>]*href="([^"]*/(?:blog|news|engineering|articles?|product|research)/[^"]*)"[^>]*>(.*?)</a>',
            html, _re.IGNORECASE | _re.DOTALL,
        ):
            href, raw_html = m.group(1), m.group(2)

            # 标签替换为空格，防止 "TitleProductDate" 粘连
            raw_title = _re.sub(r'<[^>]+>', ' ', raw_html).strip()
            raw_title = _re.sub(r'\s+', ' ', raw_title)

            # 在第一个日期/元数据关键词前截断标题
            title = raw_title
            for sep in [r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}',
                        r'\b\d{4}[-/]\d{2}[-/]\d{2}\b',
                        r'\bProduct\b', r'\bResearch\b', r'\bAnnouncements?\b',
                        r'\bBlog\b', r'\bNews\b']:
                m_sep = _re.search(sep, title)
                if m_sep and m_sep.start() > 12:
                    title = title[:m_sep.start()].strip()
                    break

            if len(title) < 12 or len(title) > 120:
                continue
            url = href if href.startswith("http") else f"https://{dom}{href}"
            if title[:60] in seen:
                continue
            seen.add(title[:60])

            # 取链接周围上下文用于日期
            ctx_start = max(0, m.start() - 500)
            ctx_end = min(len(html), m.end() + 500)
            ctx = html[ctx_start:ctx_end]

            # 摘要：优先取页面 meta description，清理 URL/CDN 垃圾
            raw_summary = page_meta or _re.sub(r'<[^>]+>', ' ', ctx)
            raw_summary = _re.sub(r'\s+', ' ', raw_summary).strip()
            raw_summary = _re.sub(r'https?://\S+|_nc_cat=\d+|cdn\S+\.(?:png|jpg|webp)', '', raw_summary)
            raw_summary = _re.sub(r'\s+', ' ', raw_summary).strip()
            summary = raw_summary[:220] if len(raw_summary) > 30 else ""

            items.append(NewsItem(
                id=url, title=title[:120], url=url,
                summary=summary,
                source_name=self.source_name, source_type="html_list",
                source_group=getattr(self, "_group", "curated"),
                source_weight=getattr(self, "_weight", 1.0),
                top_story_eligible=getattr(self, "_top_story_eligible", True),
                category_hint=getattr(self, "_category_hint", []),
                domain=_domain(url), trust=self.trust,
                published_at=_find_date(ctx),
                freshness=0.5,
            ))
            if len(items) >= limit:
                break

        logger.info("[htmllist] %s: %d items", self.source_name, len(items))
        return items

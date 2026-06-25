"""站点专用新闻源适配器。"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

from ..schema import NewsItem
from .htmllist import _fetch

logger = logging.getLogger("nanobot.news_daily.adapters")

_BAD_ANCHOR_TITLES = {
    "blog",
    "featured",
    "learn more",
    "news",
    "products",
    "product",
    "research",
    "announcements",
    "open source",
    "ai research",
    "ml applications",
}

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _clean_text(value: str) -> str:
    text = html_lib.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _date_prefix(value: str) -> str:
    if not value:
        return ""
    text = _clean_text(value)
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"([A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})", text)
    if m:
        month_day_year = re.match(r"^([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})$", m.group(1))
        if month_day_year:
            month = _MONTHS.get(month_day_year.group(1).lower())
            if month:
                try:
                    return datetime.fromisoformat(
                        f"{int(month_day_year.group(3)):04d}-{month:02d}-{int(month_day_year.group(2)):02d}"
                    ).strftime("%Y-%m-%d")
                except ValueError:
                    pass
        day_month_year = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})$", m.group(1))
        if day_month_year:
            month = _MONTHS.get(day_month_year.group(2).lower())
            if month:
                try:
                    return datetime.fromisoformat(
                        f"{int(day_month_year.group(3)):04d}-{month:02d}-{int(day_month_year.group(1)):02d}"
                    ).strftime("%Y-%m-%d")
                except ValueError:
                    pass
    return text[:24].strip()


def _domain(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().lstrip("www.")
    except Exception:
        return ""


def _title_from_slug(url: str) -> str:
    path = urlparse(url).path.strip("/")
    slug = path.split("/")[-1] if path else ""
    slug = re.sub(r"[-_]+", " ", slug).strip()
    return slug.title() if slug else ""


def _preferred_anchor_title(raw_html: str, url: str) -> str:
    for pattern in (
        r"<h[1-6][^>]*>(.*?)</h[1-6]>",
        r"<span[^>]*class=[\"'][^\"']*(?:title|headline)[^\"']*[\"'][^>]*>(.*?)</span>",
        r"<div[^>]*class=[\"'][^\"']*(?:title|headline)[^\"']*[\"'][^>]*>(.*?)</div>",
    ):
        match = re.search(pattern, raw_html or "", re.I | re.S)
        if match:
            title = _clean_text(match.group(1))
            if title:
                return title
    label = re.search(r"aria-label=[\"'](?:Read\s+)?([^\"']+)[\"']", raw_html or "", re.I)
    if label:
        return _clean_text(label.group(1))
    text = _clean_text(raw_html)
    return text or _title_from_slug(url)


def _is_bad_title(title: str) -> bool:
    clean = _clean_text(title).strip().lower()
    if clean in _BAD_ANCHOR_TITLES:
        return True
    return len(clean) < 8


def _item(
    *,
    title: str,
    url: str,
    summary: str,
    source_name: str,
    trust: float,
    published_at: str = "",
    source_group: str = "core_provider",
) -> NewsItem:
    title = _clean_text(title)[:140]
    summary = _clean_text(summary)[:600]
    if not title:
        title = _title_from_slug(url)
    return NewsItem(
        id=url,
        title=title,
        url=url,
        summary=summary,
        content_excerpt=summary[:3000],
        source_name=source_name,
        source_type="site_adapter",
        source_group=source_group,
        source_weight=1.0,
        top_story_eligible=True,
        domain=_domain(url),
        published_at=_date_prefix(published_at),
        trust=trust,
        freshness=1.0 if _date_prefix(published_at) else 0.5,
    )


class SourceSpecificHtmlProvider:
    """按站点规则从 HTML 页面提取新闻列表。"""

    path_markers: tuple[str, ...] = ()
    blocked_markers: tuple[str, ...] = ()
    base_url: str = ""

    def __init__(self, url: str, source_name: str, trust: float):
        self.url = url
        self.source_name = source_name
        self.trust = trust

    @property
    def name(self) -> str:
        return self.source_name

    def fetch(self, limit: int = 5) -> list[NewsItem]:
        try:
            html = _fetch(self.url, timeout=8)
            return self._extract(html, limit)
        except Exception as e:
            logger.warning("[%s] fetch failed: %s", self.source_name, e)
            return []

    def _extract(self, html: str, limit: int) -> list[NewsItem]:
        return _extract_anchor_items(
            html,
            limit,
            base_url=self.base_url or self.url,
            source_name=self.source_name,
            trust=self.trust,
            path_markers=self.path_markers,
            blocked_markers=self.blocked_markers,
        )


def _extract_anchor_items(
    html: str,
    limit: int,
    *,
    base_url: str,
    source_name: str,
    trust: float,
    path_markers: tuple[str, ...],
    blocked_markers: tuple[str, ...] = (),
) -> list[NewsItem]:
    items: list[NewsItem] = []
    seen: set[str] = set()
    for match in re.finditer(r"<a[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html or "", re.I | re.S):
        href, raw_title = match.group(1), match.group(2)
        url = urljoin(base_url, html_lib.unescape(href))
        lower_url = url.lower()
        if lower_url.rstrip("/") == base_url.lower().rstrip("/"):
            continue
        if path_markers and not any(marker in lower_url for marker in path_markers):
            continue
        if blocked_markers and any(marker in lower_url for marker in blocked_markers):
            continue
        title = _preferred_anchor_title(raw_title, url)
        if _is_bad_title(title):
            title = _title_from_slug(url)
        if _is_bad_title(title) or url in seen:
            continue
        seen.add(url)
        ctx = html[max(0, match.start() - 500): min(len(html), match.end() + 500)]
        date_match = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}|[A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})", ctx)
        summary = _clean_text(ctx)
        items.append(
            _item(
                title=title,
                url=url,
                summary=summary,
                source_name=source_name,
                trust=trust,
                published_at=date_match.group(1) if date_match else "",
            )
        )
        if len(items) >= limit:
            break
    return items


class AnthropicNewsProvider(SourceSpecificHtmlProvider):
    path_markers = ("anthropic.com/news/", "/news/")
    blocked_markers = ("support",)
    base_url = "https://www.anthropic.com/news"


class KimiBlogProvider(SourceSpecificHtmlProvider):
    path_markers = ("kimi.com/blog/", "/blog/")
    base_url = "https://www.kimi.com/blog/"

    def _extract(self, html: str, limit: int) -> list[NewsItem]:
        items: list[NewsItem] = []
        seen: set[str] = set()
        for match in re.finditer(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", html or "", re.I | re.S):
            attrs = match.group("attrs")
            if "menu-card" not in attrs:
                continue
            href_match = re.search(r"href=[\"']([^\"']+)[\"']", attrs, re.I)
            if not href_match:
                continue
            body = match.group("body")
            href = href_match.group(1)
            url = urljoin(self.base_url, html_lib.unescape(href))
            if "/blog/" not in urlparse(url).path:
                continue
            if url in seen:
                continue
            title_match = re.search(r"<h[1-6][^>]*class=[\"'][^\"']*card-title[^\"']*[\"'][^>]*>(.*?)</h[1-6]>", body, re.I | re.S)
            date_match = re.search(r"<p[^>]*class=[\"'][^\"']*card-date[^\"']*[\"'][^>]*>(.*?)</p>", body, re.I | re.S)
            title = _clean_text(title_match.group(1)) if title_match else _preferred_anchor_title(body, url)
            if _is_bad_title(title):
                continue
            desc_match = re.search(r"<p[^>]*class=[\"'][^\"']*card-desc[^\"']*[\"'][^>]*>(.*?)</p>", body, re.I | re.S)
            summary = _clean_text(desc_match.group(1)) if desc_match else _clean_text(body)
            published_at = _clean_text(date_match.group(1)) if date_match else ""
            seen.add(url)
            items.append(
                _item(
                    title=title,
                    url=url,
                    summary=summary,
                    source_name=self.source_name,
                    trust=self.trust,
                    published_at=published_at,
                )
            )
            if len(items) >= limit:
                return items
        return items or super()._extract(html, limit)


class XAINewsProvider(SourceSpecificHtmlProvider):
    path_markers = ("x.ai/news/", "/news/")
    base_url = "https://x.ai/news"


class CohereBlogProvider(SourceSpecificHtmlProvider):
    path_markers = ("cohere.com/blog/", "/blog/")
    base_url = "https://cohere.com/blog"


class MetaAIBlogProvider(SourceSpecificHtmlProvider):
    path_markers = ("ai.meta.com/blog/", "/blog/")
    blocked_markers = ("?filter", "?category")
    base_url = "https://ai.meta.com/blog/"


class MistralNewsProvider(SourceSpecificHtmlProvider):
    path_markers = ("mistral.ai/news/", "/news/")
    base_url = "https://mistral.ai/news"

    def _extract(self, html: str, limit: int) -> list[NewsItem]:
        text = (html or "").replace('\\"', '"')
        pattern = re.compile(
            r'"slug"\s*:\s*"(?P<slug>[^"]+)".{0,500}?'
            r'"date"\s*:\s*"(?P<date>[^"]+)".{0,500}?'
            r'"title"\s*:\s*"(?P<title>[^"]+)".{0,500}?'
            r'"description"\s*:\s*"(?P<description>[^"]*)"',
            re.S,
        )
        items: list[NewsItem] = []
        seen: set[str] = set()
        for match in pattern.finditer(text):
            slug = match.group("slug")
            if slug in seen:
                continue
            seen.add(slug)
            items.append(
                _item(
                    title=match.group("title"),
                    url=f"https://mistral.ai/news/{slug}",
                    summary=match.group("description"),
                    source_name=self.source_name,
                    trust=self.trust,
                    published_at=match.group("date"),
                )
            )
            if len(items) >= limit:
                return items
        return items or super()._extract(html, limit)


class QwenArticleApiProvider(SourceSpecificHtmlProvider):
    base_url = "https://qwen.ai/blog"

    def fetch(self, limit: int = 5) -> list[NewsItem]:
        try:
            raw = _fetch(self.url, timeout=8)
            return self._parse(raw, limit)
        except Exception as e:
            logger.warning("[%s] fetch failed: %s", self.source_name, e)
            return []

    def _parse(self, raw: str, limit: int) -> list[NewsItem]:
        try:
            data = json.loads(raw or "[]")
        except Exception as e:
            logger.warning("[%s] json parse failed: %s", self.source_name, e)
            return []
        if not isinstance(data, list):
            return []
        items: list[NewsItem] = []
        for entry in data[:limit]:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or "").strip()
            article_id = str(entry.get("id") or "").strip()
            if not title or not article_id:
                continue
            items.append(
                _item(
                    title=title,
                    url=f"https://qwen.ai/blog?id={article_id}",
                    summary=str(entry.get("description") or entry.get("introduction") or ""),
                    source_name=self.source_name,
                    trust=self.trust,
                    published_at=str(entry.get("date") or ""),
                )
            )
        return items


class DeepSeekUpdatesProvider(SourceSpecificHtmlProvider):
    base_url = "https://api-docs.deepseek.com/updates"

    def _extract(self, html: str, limit: int) -> list[NewsItem]:
        items: list[NewsItem] = []
        for section in re.finditer(
            r"<h2[^>]*>\s*Date:\s*(?P<date>\d{4}-\d{2}-\d{2}).*?(?=<h2\b|$)",
            html or "",
            re.I | re.S,
        ):
            date = section.group("date")
            body = section.group(0)
            for h3 in re.finditer(
                r'<h3[^>]*id="(?P<id>[^"]+)"[^>]*>(?P<title>.*?)(?:<a\b.*?</a>)?\s*</h3>\s*(?P<summary><p>.*?</p>)?',
                body,
                re.I | re.S,
            ):
                anchor = h3.group("id")
                title = _clean_text(h3.group("title"))
                if not title:
                    continue
                items.append(
                    _item(
                        title=title,
                        url=f"https://api-docs.deepseek.com/updates#{anchor}",
                        summary=_clean_text(h3.group("summary") or ""),
                        source_name=self.source_name,
                        trust=self.trust,
                        published_at=date,
                    )
                )
                if len(items) >= limit:
                    return items
        return items

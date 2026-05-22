"""候选详情页补全——从原始页面提取 meta description 和正文前几段。"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from urllib.parse import urlparse
from urllib.request import urlopen, build_opener, ProxyHandler, Request
import os as _os

from ..schema import NewsItem

logger = logging.getLogger("nanobot.news_daily.enrich")

_proxy_url = _os.environ.get("http_proxy") or _os.environ.get("HTTP_PROXY") or ""
_opener = build_opener(ProxyHandler({"http": _proxy_url, "https": _proxy_url})) if _proxy_url else build_opener()

DETAIL_FETCH_GROUPS = {"core_provider", "core_platform", "ai_media", "curated"}
DETAIL_FETCH_MAX_PER_RUN = 12
DETAIL_TEXT_MAX_CHARS = 1200
DETAIL_SKIP_DOMAINS = {"openai.com", "qbitai.com"}


def _clean_text(text: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def extract_detail_text(html: str, max_chars: int = 1200) -> str:
    """用 BeautifulSoup 从 HTML 提取详情：meta description → article/main 前几段。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    # 1. meta description
    for sel in ['meta[property="og:description"]', 'meta[name="description"]',
                'meta[name="twitter:description"]']:
        tag = soup.select_one(sel)
        if tag and tag.get("content", "").strip():
            text = tag["content"].strip()
            if len(text) > 20:
                return text[:max_chars]

    # 2. article/main 前几段
    root = soup.select_one("article") or soup.select_one("main") or soup.body
    if root:
        ps = root.find_all(["p", "li"], limit=8)
        cleaned = []
        for p in ps:
            text = p.get_text(" ", strip=True)
            if len(text) > 20:
                cleaned.append(text)
        if cleaned:
            return "\n".join(cleaned)[:max_chars]
    return ""


def _fetch_detail(url: str, timeout: int = 8) -> str:
    try:
        if "openai.com/index/" in url and not url.endswith("/"):
            url = url.rstrip("/") + "/"
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Nanobot/2.0)", "Accept": "text/html,application/xhtml+xml"})
        with _opener.open(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            return extract_detail_text(raw)
    except Exception as e:
        logger.warning("[enrich] failed %s: %s", url, str(e)[:160])
        return ""


def _should_skip_detail_fetch(item: NewsItem) -> bool:
    domain = (item.domain or "").lower().lstrip("www.")
    url = (item.url or "").lower()
    try:
        url_domain = (urlparse(url).netloc or "").lower().lstrip("www.")
    except Exception:
        url_domain = ""
    if (
        domain in DETAIL_SKIP_DOMAINS
        or url_domain in DETAIL_SKIP_DOMAINS
        or "openai.com/index/" in url
    ) and (item.summary or item.content_excerpt):
        return True
    if len(item.summary or "") >= 180 or len(item.content_excerpt or "") >= 300:
        return True
    return False


def enrich_items(items: list[NewsItem], max_fetch: int = 12) -> list[NewsItem]:
    """对高优先级候选并发抓详情页。"""
    # 策展/聚合源无独立文章页，跳过详情抓取
    eligible = [
        item for item in items
        if getattr(item, 'source_group', '') in DETAIL_FETCH_GROUPS
        and "juya" not in (item.source_name or "").lower()
        and not item.url.endswith('.xml')
        and not _should_skip_detail_fetch(item)
    ][:max_fetch]

    if not eligible:
        return items

    logger.info("[enrich] fetching details for %d items", len(eligible))
    pool = ThreadPoolExecutor(max_workers=4)
    try:
        futures = {pool.submit(_fetch_detail, item.url): item for item in eligible}
        try:
            for f in as_completed(futures, timeout=20):
                item = futures[f]
                try:
                    detail = f.result()
                    if detail:
                        item.detail_text = detail
                        if not item.summary:
                            item.summary = _clean_text(detail)[:240]
                except Exception:
                    pass
        except TimeoutError:
            logger.debug("[enrich] partial——timeout, using completed results")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    enriched = sum(1 for item in items if item.detail_text)
    logger.info("[enrich] %d/%d items enriched", enriched, len(eligible))
    return items

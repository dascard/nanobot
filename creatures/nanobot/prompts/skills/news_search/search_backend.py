"""新闻搜索后端实现。"""

from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from duckduckgo_search import DDGS
import trafilatura

from . import runtime_cache
from .legacy_report import _combined_score, _domain


logger = logging.getLogger("nanobot.ai_daily")

_proxy_url = os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY") or ""
_proxy_opener = (
    build_opener(ProxyHandler({"http": _proxy_url, "https": _proxy_url}))
    if _proxy_url
    else build_opener()
)


def _urlopen(url: str, timeout: int = 10):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return _proxy_opener.open(req, timeout=timeout) if _proxy_url else urlopen(url, timeout=timeout)


def _ddgs_kwargs() -> dict[str, str]:
    return {"proxy": _proxy_url} if _proxy_url else {}


NEWS_SEARCH_DDG_ENABLED = os.environ.get("NEWS_SEARCH_DDG_ENABLED", "0") == "1"
JUYA_RSS_URL = "https://imjuya.github.io/juya-ai-daily/rss.xml"
RSS_KEYWORDS = {
    "juya",
    "ai daily",
    "morning briefing",
    "日报",
    "早报",
    "每日",
    "快讯",
    "newsletter",
    "简报",
    "digest",
}
DAILY_DIGEST_KEYWORDS = runtime_cache.DAILY_DIGEST_KEYWORDS
RSS_SOURCES = [
    {
        "name": "juya_ai_daily",
        "url": "https://imjuya.github.io/juya-ai-daily/rss.xml",
        "weight": 3,
    },
    {
        "name": "reddit_localllama",
        "url": "https://www.reddit.com/r/LocalLLaMA/.rss",
        "weight": 1,
    },
]


def _extract_date(query: str) -> str | None:
    return runtime_cache._extract_date(query, now=datetime.now())


def _is_daily_digest_query(query: str) -> bool:
    return runtime_cache._is_daily_digest_query(query)


def _is_rss_first_query(query: str) -> bool:
    q = (query or "").lower()
    return any(k in q for k in RSS_KEYWORDS)


def _should_use_juya_direct(query: str) -> bool:
    """Juya RSS 用于日报类和模型改写后的日报变体。"""
    if _is_daily_digest_query(query):
        return True
    q = (query or "").lower()
    has_date = bool(re.search(r"(2026|2025|\d{4}-\d{2}-\d{2}|\d{1,2}月\d{1,2}日)", q))
    has_ai = bool(re.search(r"(ai|人工智能|大模型|llm|模型|新闻|资讯)", q))
    has_today = any(k in q for k in ("今日", "今天", "today"))
    return (has_today or has_date) and has_ai


def _is_news_query(query: str) -> bool:
    q = (query or "").lower()
    markers = ["news", "daily", "brief", "briefing", "最新", "快讯", "早报", "资讯", "日报", "发布"]
    return any(m in q for m in markers)


def _infer_timelimit(query: str) -> str | None:
    q = (query or "").lower()
    if any(k in q for k in ["today", "今日", "今天", "latest", "最新", "刚刚", "24h", "24小时"]):
        return "d"
    if any(k in q for k in ["this week", "本周", "一周", "7天", "7 days"]):
        return "w"
    if any(k in q for k in ["this month", "本月", "30天", "30 days"]):
        return "m"
    return None


def _is_urgent_news_query(query: str) -> bool:
    q = (query or "").lower()
    markers = ["today", "今日", "今天", "latest", "最新", "刚刚", "breaking", "24h", "24小时"]
    return any(k in q for k in markers)


def _tokenize_query(query: str) -> list[str]:
    q = (query or "").lower().strip()
    if not q:
        return []
    tokens = re.findall(r"[a-z0-9\-\+\.]{2,}|[\u4e00-\u9fff]{2,}", q)
    blacklist = {"news", "daily", "brief", "briefing", "最新", "快讯", "早报", "资讯", "日报", "发布"}
    return [t for t in tokens if t not in blacklist]


def _extract_item_date(item: ET.Element) -> str:
    pub = (item.findtext("pubDate") or item.findtext("published") or "").strip()
    if not pub:
        return ""
    try:
        dt = parsedate_to_datetime(pub)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
    except Exception:
        return ""


def _is_recent_enough(raw_date: str, hours: int = 72) -> bool:
    if not raw_date:
        return True
    dt = None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(raw_date, fmt)
            break
        except Exception:
            continue
    if dt is None:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt) <= timedelta(hours=hours)


def _normalize_search_result(item: dict[str, Any], *, strategy: str) -> dict[str, Any]:
    normalized = dict(item)
    href = normalized.get("href") or normalized.get("url") or ""
    normalized["href"] = href
    normalized.setdefault("search_strategy", strategy)
    return normalized


def _filter_stale_news_results(results: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    if not _is_news_query(query):
        return results

    hours = 36 if _is_urgent_news_query(query) else 72
    filtered: list[dict[str, Any]] = []
    for item in results:
        raw_date = (item.get("date") or "").strip()
        if raw_date and not _is_recent_enough(raw_date, hours=hours):
            continue
        filtered.append(item)
    return filtered


def _match_query(item: dict[str, Any], query: str) -> bool:
    tokens = _tokenize_query(query)
    if not tokens:
        return True
    text = f"{item.get('title', '')} {item.get('body', '')}".lower()
    return any(t in text for t in tokens)


def _fetch_rss_source(
    source: dict[str, Any],
    max_results: int,
    *,
    urlopen_fn: Callable[..., Any] = _urlopen,
) -> list[dict[str, Any]]:
    try:
        with urlopen_fn(source["url"], timeout=6) as resp:
            xml_data = resp.read()

        root = ET.fromstring(xml_data)
        items = root.findall("./channel/item")
        parsed: list[dict[str, Any]] = []

        for item in items:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip()
            content_encoded = ""

            for child in list(item):
                if child.tag.endswith("content") or child.tag.endswith("encoded"):
                    content_encoded = (child.text or "").strip()
                    if content_encoded:
                        break

            if not link:
                continue

            parsed.append(
                {
                    "title": title or source["name"],
                    "href": link,
                    "body": (description or content_encoded)[:800],
                    "date": _extract_item_date(item),
                    "source_weight": source.get("weight", 0),
                    "search_strategy": f"rss:{source['name']}",
                }
            )

        return parsed[:max_results]
    except Exception as e:
        logger.warning(f"RSS source fetch failed: {source.get('name')} {e}")
        return []


def _fetch_multi_rss(
    query: str | None = None,
    max_results: int = 5,
    *,
    urlopen_fn: Callable[..., Any] = _urlopen,
) -> list[dict[str, Any]]:
    query_text = query or ""
    target_date = _extract_date(query_text)
    all_items: list[dict[str, Any]] = []
    for source in RSS_SOURCES:
        all_items.extend(_fetch_rss_source(source, max_results=max_results * 2, urlopen_fn=urlopen_fn))

    filtered: list[dict[str, Any]] = []
    for item in all_items:
        title = (item.get("title") or "").strip()
        raw_date = (item.get("date") or "").strip()
        if target_date and target_date not in title and not raw_date.startswith(target_date):
            continue
        if not _is_recent_enough(raw_date, hours=72):
            continue
        if not _match_query(item, query_text):
            continue
        filtered.append(item)

    filtered = _dedup_results(filtered)
    filtered = _rerank_with_domain_diversity(filtered, max_results=max_results)
    return filtered


def _fetch_juya_rss(
    max_results: int,
    target_date: str | None = None,
    *,
    urlopen_fn: Callable[..., Any] = _urlopen,
) -> list[dict[str, Any]]:
    try:
        with urlopen_fn(JUYA_RSS_URL, timeout=6) as resp:
            xml_data = resp.read()

        root = ET.fromstring(xml_data)
        items = root.findall("./channel/item")
        parsed: list[dict[str, Any]] = []

        for item in items:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip()
            content_encoded = ""

            for child in list(item):
                if child.tag.endswith("content") or child.tag.endswith("encoded"):
                    content_encoded = (child.text or "").strip()
                    if content_encoded:
                        break

            if not link:
                continue

            item_date = _extract_item_date(item)
            if target_date and target_date not in title and not item_date.startswith(target_date):
                continue

            parsed.append(
                {
                    "title": title or "Juya AI Daily",
                    "href": link,
                    "body": (description or content_encoded)[:800],
                    "date": item_date,
                    "source_weight": 3,
                    "search_strategy": "juya_rss",
                }
            )

        return parsed[:max_results]
    except Exception as e:
        logger.warning(f"Juya RSS fetch failed: {e}")
        return []


def _build_query_variants(query: str) -> list[str]:
    q = (query or "").strip()
    if not q:
        return []
    variants = [q]
    if "news" not in q.lower() and "资讯" not in q and "日报" not in q:
        variants.append(f"{q} AI news")
    variants.append(f"{q} model pricing OR free tier OR api")
    variants.append(f"{q} 开源 OR 免费 OR 低价 模型")
    variants.append(f"{q} site:reuters.com OR site:techcrunch.com OR site:theverge.com")
    if _is_news_query(q):
        today = datetime.now().strftime("%Y-%m-%d")
        variants.append(f"{today} {q}")
        variants.append(f"{q} today breaking")
        variants.append(f"{q} site:openai.com OR site:anthropic.com OR site:huggingface.co")
    return variants


def _dedup_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    cleaned = []
    for r in results:
        url = (r.get("href") or "").strip()
        title = (r.get("title") or "").strip().lower()
        key = (url, title)
        if not url or key in seen:
            continue
        seen.add(key)
        cleaned.append(r)
    return cleaned


def _rerank_with_domain_diversity(results: list[dict[str, Any]], max_results: int) -> list[dict[str, Any]]:
    if not results:
        return []

    ranked = sorted(results, key=_combined_score, reverse=True)
    picked: list[dict[str, Any]] = []
    used_domains: set[str] = set()

    for item in ranked:
        d = _domain(item.get("href", ""))
        if d and d in used_domains:
            continue
        picked.append(item)
        if d:
            used_domains.add(d)
        if len(picked) >= max_results:
            return picked

    for item in ranked:
        if item in picked:
            continue
        picked.append(item)
        if len(picked) >= max_results:
            break

    return picked


def search(
    query: str,
    max_results: int = 5,
    deep: bool = False,
    *,
    ddg_enabled: bool | None = None,
    ddgs_factory: Any = DDGS,
    ddgs_kwargs_fn: Callable[[], dict[str, Any]] = _ddgs_kwargs,
    multi_rss_fetcher: Callable[..., list[dict[str, Any]]] = _fetch_multi_rss,
    juya_fetcher: Callable[..., list[dict[str, Any]]] = _fetch_juya_rss,
) -> tuple[list[dict[str, Any]], str]:
    errors: list[str] = []

    rss_limit = max_results * (2 if deep else 1)
    try:
        rss_results = list(multi_rss_fetcher(query=query, max_results=rss_limit))
    except Exception as e:
        errors.append(f"rss:{e}")
        logger.warning(f"RSS aggregate search failed: {e}")
        rss_results = []

    target_date = _extract_date(query)
    if _is_rss_first_query(query):
        try:
            rss_results.extend(juya_fetcher(max_results=max_results, target_date=target_date))
        except Exception as e:
            errors.append(f"juya:{e}")
            logger.warning(f"Juya RSS search failed: {e}")

    results: list[dict[str, Any]] = []
    timelimit = _infer_timelimit(query)
    if timelimit is None and _is_news_query(query):
        timelimit = "d"
    per_variant = max_results * (4 if deep else 2)
    variants = _build_query_variants(query)
    if deep:
        variants.extend(
            [
                f"{query} official blog release notes",
                f"{query} site:openai.com OR site:anthropic.com OR site:ai.googleblog.com",
                f"{query} benchmark price token cost",
            ]
        )

    effective_ddg_enabled = NEWS_SEARCH_DDG_ENABLED if ddg_enabled is None else ddg_enabled
    if not effective_ddg_enabled:
        logger.info("[search] ddg disabled by NEWS_SEARCH_DDG_ENABLED=0")
        return _filter_stale_news_results(rss_results, query), ""

    try:
        with ddgs_factory(**ddgs_kwargs_fn()) as ddgs:
            if _is_news_query(query):
                try:
                    for r in ddgs.news(
                        keywords=query,
                        region="wt-wt",
                        safesearch="moderate",
                        timelimit=timelimit,
                        max_results=per_variant,
                    ):
                        results.append(_normalize_search_result(dict(r), strategy="web_ddg_news"))
                except Exception as e:
                    errors.append(f"ddg_news:{e}")
                    logger.warning(f"DDG news search failed: {e}")

            for variant in variants:
                try:
                    for r in ddgs.text(
                        variant,
                        region="wt-wt",
                        safesearch="moderate",
                        timelimit=timelimit,
                        max_results=per_variant,
                    ):
                        results.append(
                            _normalize_search_result(
                                dict(r),
                                strategy="web_ddg_deep" if deep else "web_ddg_multi_variant",
                            )
                        )
                except Exception as e:
                    errors.append(f"ddg_text:{variant}: {e}")
                    logger.warning(f"DDG text search failed for variant={variant!r}: {e}")
    except Exception as e:
        errors.append(f"ddg:{e}")
        logger.warning(f"DDG search session failed: {e}")

    merged = _filter_stale_news_results(rss_results + results, query)
    merged = _dedup_results(merged)
    merged = _rerank_with_domain_diversity(merged, max_results=max_results)
    if not merged and errors:
        last_error = " | ".join(errors[-4:])
        logger.error(f"Search failed: {last_error}")
        return merged, last_error
    return merged, ""


def extract_web_content(url: str, *, trafilatura_module: Any = trafilatura) -> str:
    try:
        downloaded = trafilatura_module.fetch_url(url, timeout=5)
        if downloaded:
            result = trafilatura_module.extract(
                downloaded,
                include_comments=False,
                include_tables=False,
                favor_precision=True,
                with_metadata=False,
            )
            return result or "Failed to extract content"
        return "Failed to download url"
    except Exception as e:
        return f"Error extracting {url}: {e}"

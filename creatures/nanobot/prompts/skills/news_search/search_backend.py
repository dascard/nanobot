"""新闻搜索后端实现。"""

from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from core.news.source_registry import (
    get_news_source_registry,
    get_runtime_news_source_registry,
)
from core.tool_contracts.ai_daily import (
    AI_DAILY_TIMEZONE,
    NewsRequest,
    parse_news_search_request,
)
from duckduckgo_search import DDGS
import trafilatura

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
_SOURCE_REGISTRY = get_news_source_registry()
_JUYA_DESCRIPTOR = _SOURCE_REGISTRY.require("juya_ai_daily")
JUYA_RSS_URL = _JUYA_DESCRIPTOR.url
RSS_SOURCES = [
    {
        "name": descriptor.source_id,
        "url": descriptor.url,
        "weight": descriptor.quality_weight,
        "timeout": descriptor.fetch_timeout_seconds,
        "per_run_limit": descriptor.per_run_limit,
    }
    for descriptor in _SOURCE_REGISTRY.select("search")
]


def _runtime_rss_sources() -> list[dict[str, Any]]:
    return [
        {
            "name": descriptor.source_id,
            "url": descriptor.url,
            "weight": descriptor.quality_weight,
            "timeout": descriptor.fetch_timeout_seconds,
            "per_run_limit": descriptor.per_run_limit,
        }
        for descriptor in get_runtime_news_source_registry().select("search")
    ]


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


def _parse_result_datetime(raw_date: str) -> datetime | None:
    if not raw_date:
        return None
    try:
        dt = parsedate_to_datetime(raw_date)
    except Exception:
        try:
            dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(AI_DAILY_TIMEZONE)


def _is_within_request_window(
    raw_date: str,
    request: NewsRequest,
) -> bool:
    """未知日期保守保留；已知日期严格使用请求中一次计算的半开窗口。"""

    parsed = _parse_result_datetime(raw_date)
    if parsed is None:
        return True
    return request.window_start <= parsed < request.window_end


def _normalize_search_result(item: dict[str, Any], *, strategy: str) -> dict[str, Any]:
    normalized = dict(item)
    href = normalized.get("href") or normalized.get("url") or ""
    normalized["href"] = href
    normalized.setdefault("search_strategy", strategy)
    return normalized


def _filter_stale_news_results(
    results: list[dict[str, Any]],
    request: NewsRequest,
) -> list[dict[str, Any]]:
    return [
        item
        for item in results
        if _is_within_request_window(
            str(item.get("date") or ""),
            request,
        )
    ]


def _fetch_rss_source(
    source: dict[str, Any],
    max_results: int,
    *,
    urlopen_fn: Callable[..., Any] = _urlopen,
) -> list[dict[str, Any]]:
    try:
        with urlopen_fn(
            source["url"],
            timeout=int(source.get("timeout") or 6),
        ) as resp:
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

        source_limit = int(source.get("per_run_limit") or max_results)
        return parsed[: min(max_results, source_limit)]
    except Exception as e:
        logger.warning(f"RSS source fetch failed: {source.get('name')} {e}")
        return []


def _fetch_multi_rss_request(
    request: NewsRequest,
    max_results: int = 5,
    *,
    urlopen_fn: Callable[..., Any] = _urlopen,
) -> list[dict[str, Any]]:
    all_items: list[dict[str, Any]] = []
    for source in _runtime_rss_sources():
        all_items.extend(_fetch_rss_source(source, max_results=max_results * 2, urlopen_fn=urlopen_fn))

    filtered: list[dict[str, Any]] = []
    for item in all_items:
        title = (item.get("title") or "").strip()
        raw_date = (item.get("date") or "").strip()
        if (
            request.target_date
            and request.target_date not in title
            and not raw_date.startswith(request.target_date)
        ):
            continue
        if not _is_within_request_window(raw_date, request):
            continue
        filtered.append(item)

    filtered = _dedup_results(filtered)
    filtered = _rerank_with_domain_diversity(filtered, max_results=max_results)
    return filtered


def _fetch_multi_rss(
    query: str | None = None,
    max_results: int = 5,
    *,
    urlopen_fn: Callable[..., Any] = _urlopen,
) -> list[dict[str, Any]]:
    """旧测试补丁点 Adapter；主流程只调用类型化 request 入口。"""

    request = parse_news_search_request(
        query or "AI",
        max_results=max_results,
    )
    return _fetch_multi_rss_request(
        request,
        max_results=max_results,
        urlopen_fn=urlopen_fn,
    )


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


def _build_query_variants(request: NewsRequest, *, deep: bool) -> list[str]:
    """搜索策略只由类型化请求和显式 deep 能力决定。"""

    variants = [request.query]
    if deep:
        variants.extend(
            (
                f"{request.query} official release notes",
                f"{request.query} independent coverage",
            )
        )
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


def _timelimit_for_request(request: NewsRequest) -> str | None:
    if request.freshness in {"today", "latest"}:
        return "d"
    if request.freshness == "week":
        return "w"
    return None


def search_news(
    request: NewsRequest,
    deep: bool = False,
    *,
    ddg_enabled: bool | None = None,
    ddgs_factory: Any = DDGS,
    ddgs_kwargs_fn: Callable[[], dict[str, Any]] = _ddgs_kwargs,
    multi_rss_fetcher: Callable[
        [NewsRequest, int],
        list[dict[str, Any]],
    ] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    if not isinstance(request, NewsRequest):
        raise TypeError("search_news 必须接收 NewsRequest")
    errors: list[str] = []
    max_results = request.max_results

    rss_limit = max_results * (2 if deep else 1)
    try:
        if multi_rss_fetcher is None:
            rss_results = _fetch_multi_rss_request(
                request,
                max_results=rss_limit,
            )
        else:
            rss_results = list(multi_rss_fetcher(request, rss_limit))
    except Exception as e:
        errors.append(f"rss:{e}")
        logger.warning(f"RSS aggregate search failed: {e}")
        rss_results = []

    results: list[dict[str, Any]] = []
    timelimit = _timelimit_for_request(request)
    per_variant = max_results * (4 if deep else 2)
    variants = _build_query_variants(request, deep=deep)

    effective_ddg_enabled = NEWS_SEARCH_DDG_ENABLED if ddg_enabled is None else ddg_enabled
    if not effective_ddg_enabled:
        logger.info("[search] ddg disabled by NEWS_SEARCH_DDG_ENABLED=0")
        return _filter_stale_news_results(rss_results, request), ""

    try:
        with ddgs_factory(**ddgs_kwargs_fn()) as ddgs:
            try:
                for r in ddgs.news(
                    keywords=request.query,
                    region="wt-wt",
                    safesearch="moderate",
                    timelimit=timelimit,
                    max_results=per_variant,
                ):
                    results.append(
                        _normalize_search_result(
                            dict(r),
                            strategy="web_ddg_news",
                        )
                    )
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

    merged = _filter_stale_news_results(rss_results + results, request)
    merged = _dedup_results(merged)
    merged = _rerank_with_domain_diversity(merged, max_results=max_results)
    if not merged and errors:
        last_error = " | ".join(errors[-4:])
        logger.error(f"Search failed: {last_error}")
        return merged, last_error
    return merged, ""


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
    """旧字符串入口 Adapter；立即构造统一 ``NewsRequest``。"""

    del juya_fetcher
    request = parse_news_search_request(
        query,
        max_results=max_results,
    )

    def _legacy_rss_adapter(
        typed_request: NewsRequest,
        limit: int,
    ) -> list[dict[str, Any]]:
        return list(
            multi_rss_fetcher(
                query=typed_request.query,
                max_results=limit,
            )
        )

    return search_news(
        request,
        deep=deep,
        ddg_enabled=ddg_enabled,
        ddgs_factory=ddgs_factory,
        ddgs_kwargs_fn=ddgs_kwargs_fn,
        multi_rss_fetcher=_legacy_rss_adapter,
    )


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

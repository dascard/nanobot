"""统一 Web Search provider 运行时。"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote_plus

import aiohttp

from core.web_search.provider_catalog import get_provider_catalog, list_provider_catalog
from core.web_search.provider_settings import ProviderResolvedConfig, resolve_provider_config
from core.web_search.usage_stats import record_provider_usage


USER_AGENT = "Nanobot-WebSearch/1.0"
TIMEOUT_SECONDS = 12


@dataclass(frozen=True)
class WebSearchResult:
    provider: str
    title: str
    url: str
    snippet: str = ""
    published_at: str = ""
    score: float | None = None
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WebSearchProviderResult:
    provider_id: str
    results: list[WebSearchResult]
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "elapsed_ms": self.elapsed_ms,
            "results": [item.to_dict() for item in self.results],
        }


class WebSearchError(RuntimeError):
    """Provider 搜索失败，携带可展示错误码。"""

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        provider_id: str = "",
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.provider_id = provider_id
        self.status = status


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _mask_secret(text: str, api_key: str = "") -> str:
    masked = str(text or "")
    if api_key:
        masked = masked.replace(api_key, "***")
    masked = re.sub(
        r"(?i)(authorization|x-api-key|x-subscription-token)(\s*[:=]\s*)(bearer\s+)?[^\s,;]+",
        r"\1\2***",
        masked,
    )
    masked = re.sub(r"(?i)(api_key|apikey|token|key)=([^&\s]+)", r"\1=***", masked)
    return masked


def _status_error_code(status: int) -> str:
    if status in {401, 403}:
        return "provider_auth_failed"
    if status == 429:
        return "provider_rate_limited"
    return "provider_bad_response"


def _base_url(config: ProviderResolvedConfig, default: str) -> str:
    return (config.base_url or default).strip().rstrip("/")


def _append_path(base: str, path: str) -> str:
    normalized_base = base.rstrip("/")
    normalized_path = "/" + path.strip("/")
    if normalized_base.endswith(normalized_path):
        return normalized_base
    parent, _, leaf = normalized_path.rpartition("/")
    if parent and normalized_base.endswith(parent):
        return f"{normalized_base}/{leaf}"
    return f"{normalized_base}{normalized_path}"


async def _json_response(response: aiohttp.ClientResponse) -> Any:
    try:
        return await response.json(content_type=None)
    except Exception:
        text = await response.text()
        raise WebSearchError(
            "provider_bad_response",
            f"Provider returned non-json response: {text[:200]}",
        )


async def _error_body_snippet(response: aiohttp.ClientResponse) -> str:
    try:
        text = await response.text()
    except Exception:
        return ""
    return text.strip()[:200]


async def _ensure_ok(response: aiohttp.ClientResponse, config: ProviderResolvedConfig) -> None:
    if 200 <= response.status < 300:
        return
    snippet = await _error_body_snippet(response)
    raise WebSearchError(
        _status_error_code(response.status),
        _mask_secret(f"Provider 返回 {response.status}: {snippet}", config.api_key),
        provider_id=config.provider_id,
        status=response.status,
    )


def _string_value(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, list):
        return "\n".join(str(item) for item in raw if item is not None)
    if isinstance(raw, dict):
        for key in ("text", "markdown", "content", "description", "value"):
            value = raw.get(key)
            if value:
                return _string_value(value)
        return ""
    return str(raw)


def _first(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        text = _string_value(value).strip()
        if text:
            return text
    return ""


def _first_number(item: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _normalized_result(
    provider: str,
    item: Any,
    *,
    title_keys: tuple[str, ...] = ("title", "name"),
    url_keys: tuple[str, ...] = ("url", "href", "link"),
    snippet_keys: tuple[str, ...] = ("snippet", "description", "content", "body", "text", "markdown"),
    published_keys: tuple[str, ...] = ("published_at", "publishedDate", "date", "page_age"),
    score_keys: tuple[str, ...] = ("score",),
) -> WebSearchResult | None:
    if not isinstance(item, dict):
        return None
    url = _first(item, url_keys)
    if not url:
        return None
    title = _first(item, title_keys) or url
    snippet = _first(item, snippet_keys)
    return WebSearchResult(
        provider=provider,
        title=title[:500],
        url=url,
        snippet=snippet[:2000],
        published_at=_first(item, published_keys),
        score=_first_number(item, score_keys),
        raw=item,
    )


def _take_normalized(provider: str, items: list[Any], limit: int) -> list[WebSearchResult]:
    results: list[WebSearchResult] = []
    seen: set[str] = set()
    for item in items:
        normalized = _normalized_result(provider, item)
        if normalized is None or normalized.url in seen:
            continue
        seen.add(normalized.url)
        results.append(normalized)
        if len(results) >= limit:
            break
    return results


def _list_at(data: Any, path: tuple[str, ...]) -> list[Any]:
    current = data
    for key in path:
        if not isinstance(current, dict):
            return []
        current = current.get(key)
    return current if isinstance(current, list) else []


def _collect_lists(data: Any, paths: tuple[tuple[str, ...], ...]) -> list[Any]:
    items: list[Any] = []
    for path in paths:
        items.extend(_list_at(data, path))
    return items


def _limit(value: int) -> int:
    return max(1, min(int(value or 5), 10))


def format_provider_result_for_model(
    query: str,
    result: WebSearchProviderResult,
    limit: int = 5,
) -> str:
    """把搜索结果格式化成工具输出给模型的文本。"""

    normalized_limit = _limit(limit)
    items = result.results[:normalized_limit]
    lines = [
        f"web_search: query={str(query or '').strip()} provider={result.provider_id} count={len(items)}",
    ]
    for index, item in enumerate(items, start=1):
        snippet = item.snippet.replace("\n", " ").strip()
        if len(snippet) > 260:
            snippet = f"{snippet[:260]}..."
        line = f"{index}. {item.title}\n   URL: {item.url}"
        if snippet:
            line += f"\n   摘要: {snippet}"
        if item.published_at:
            line += f"\n   时间: {item.published_at}"
        lines.append(line)
    return "\n".join(lines)


async def _test_json_get(
    config: ProviderResolvedConfig,
    url: str,
    *,
    params: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> Any:
    request_headers = {"User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS),
        headers=request_headers,
    ) as session:
        async with session.get(url, params=params, max_redirects=3) as response:
            await _ensure_ok(response, config)
            return await _json_response(response)


async def _test_json_post(
    config: ProviderResolvedConfig,
    url: str,
    *,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> Any:
    request_headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS),
        headers=request_headers,
    ) as session:
        async with session.post(url, json=body, max_redirects=3) as response:
            await _ensure_ok(response, config)
            return await _json_response(response)


async def _search_searxng(config: ProviderResolvedConfig, query: str, limit: int) -> list[WebSearchResult]:
    if not config.base_url:
        raise WebSearchError("invalid_base_url", "请先配置 SearXNG Base URL", provider_id=config.provider_id)
    url = _append_path(config.base_url, "/search")
    data = await _test_json_get(config, url, params={"q": query, "format": "json"})
    items = _list_at(data, ("results",))
    if not isinstance(items, list):
        raise WebSearchError("provider_bad_response", "Provider 响应缺少 results", provider_id=config.provider_id)
    return _take_normalized(config.provider_id, items, limit)


async def _search_serper(config: ProviderResolvedConfig, query: str, limit: int) -> list[WebSearchResult]:
    url = _append_path(_base_url(config, "https://google.serper.dev"), "/search")
    data = await _test_json_post(
        config,
        url,
        body={"q": query, "num": limit},
        headers={"X-API-KEY": config.api_key},
    )
    items = _list_at(data, ("organic",))
    if not isinstance(items, list):
        raise WebSearchError("provider_bad_response", "Provider 响应缺少 organic", provider_id=config.provider_id)
    return _take_normalized(config.provider_id, items, limit)


async def _search_brave(config: ProviderResolvedConfig, query: str, limit: int) -> list[WebSearchResult]:
    url = _append_path(_base_url(config, "https://api.search.brave.com/res/v1"), "/web/search")
    data = await _test_json_get(
        config,
        url,
        params={"q": query, "count": limit},
        headers={"X-Subscription-Token": config.api_key},
    )
    items = _list_at(data, ("web", "results"))
    if not isinstance(items, list):
        raise WebSearchError("provider_bad_response", "Provider 响应缺少 web.results", provider_id=config.provider_id)
    return _take_normalized(config.provider_id, items, limit)


async def _search_tavily(config: ProviderResolvedConfig, query: str, limit: int) -> list[WebSearchResult]:
    url = _append_path(_base_url(config, "https://api.tavily.com"), "/search")
    data = await _test_json_post(
        config,
        url,
        body={
            "query": query,
            "max_results": limit,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
        },
        headers={"Authorization": f"Bearer {config.api_key}"},
    )
    items = _list_at(data, ("results",))
    if not isinstance(items, list):
        raise WebSearchError("provider_bad_response", "Provider 响应缺少 results", provider_id=config.provider_id)
    return _take_normalized(config.provider_id, items, limit)


def _ddgs_search(query: str, limit: int) -> list[Any]:
    try:
        from ddgs import DDGS
    except Exception:
        try:
            from duckduckgo_search import DDGS
        except Exception as exc:
            raise ImportError("缺少 ddgs 或 duckduckgo_search 依赖") from exc

    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=limit))


async def _search_ddgs(config: ProviderResolvedConfig, query: str, limit: int) -> list[WebSearchResult]:
    try:
        items = await asyncio.to_thread(_ddgs_search, query, limit)
    except ImportError as exc:
        raise WebSearchError("dependency_missing", str(exc), provider_id=config.provider_id) from exc
    except Exception as exc:
        raise WebSearchError("provider_bad_response", str(exc), provider_id=config.provider_id) from exc
    return _take_normalized(config.provider_id, items, limit)


async def _search_exa(config: ProviderResolvedConfig, query: str, limit: int) -> list[WebSearchResult]:
    url = _append_path(_base_url(config, "https://api.exa.ai"), "/search")
    data = await _test_json_post(
        config,
        url,
        body={"query": query, "type": "auto", "numResults": limit, "contents": {"highlights": True}},
        headers={"x-api-key": config.api_key},
    )
    items = _list_at(data, ("results",))
    if not isinstance(items, list):
        raise WebSearchError("provider_bad_response", "Provider 响应缺少 results", provider_id=config.provider_id)
    return _take_normalized(
        config.provider_id,
        items,
        limit,
    )


async def _search_firecrawl(config: ProviderResolvedConfig, query: str, limit: int) -> list[WebSearchResult]:
    url = _append_path(_base_url(config, "https://api.firecrawl.dev"), "/v2/search")
    data = await _test_json_post(
        config,
        url,
        body={
            "query": query,
            "limit": limit,
            "sources": ["web"],
            "scrapeOptions": {},
        },
        headers={"Authorization": f"Bearer {config.api_key}"},
    )
    items = _collect_lists(data, (("data", "web"), ("data", "news"), ("data",), ("results",)))
    if not isinstance(items, list):
        raise WebSearchError("provider_bad_response", "Provider 响应缺少 data.web", provider_id=config.provider_id)
    return _take_normalized(config.provider_id, items, limit)


async def _search_linkup(config: ProviderResolvedConfig, query: str, limit: int) -> list[WebSearchResult]:
    url = _append_path(_base_url(config, "https://api.linkup.so"), "/v1/search")
    data = await _test_json_post(
        config,
        url,
        body={"q": query, "depth": "standard", "outputType": "searchResults", "maxResults": limit},
        headers={"Authorization": f"Bearer {config.api_key}"},
    )
    items = _collect_lists(data, (("results",), ("sources",)))
    if not isinstance(items, list):
        raise WebSearchError("provider_bad_response", "Provider 响应缺少 results", provider_id=config.provider_id)
    return _take_normalized(config.provider_id, items, limit)


async def _search_you(config: ProviderResolvedConfig, query: str, limit: int) -> list[WebSearchResult]:
    url = _append_path(_base_url(config, "https://ydc-index.io"), "/v1/search")
    data = await _test_json_get(
        config,
        url,
        params={"query": query, "count": limit},
        headers={"X-API-Key": config.api_key},
    )
    items = _collect_lists(
        data,
        (
            ("results", "web"),
            ("results", "news"),
            ("results",),
            ("hits",),
            ("data",),
        ),
    )
    if not isinstance(items, list):
        raise WebSearchError("provider_bad_response", "Provider 响应缺少 results.web", provider_id=config.provider_id)
    return _take_normalized(config.provider_id, items, limit)


async def _search_jina(config: ProviderResolvedConfig, query: str, limit: int) -> list[WebSearchResult]:
    base = _base_url(config, "https://s.jina.ai")
    url = f"{base}/?q={quote_plus(query)}"
    headers = {"Accept": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    data = await _test_json_get(config, url, params={"num": limit}, headers=headers)
    if isinstance(data, list):
        items = data
    else:
        items = _collect_lists(data, (("data",), ("results",), ("items",)))
    if not isinstance(items, list):
        raise WebSearchError("provider_bad_response", "Provider 响应缺少 data", provider_id=config.provider_id)
    return _take_normalized(config.provider_id, items, limit)


_SEARCHERS = {
    "searxng": _search_searxng,
    "serper": _search_serper,
    "brave": _search_brave,
    "tavily": _search_tavily,
    "ddgs": _search_ddgs,
    "exa": _search_exa,
    "firecrawl": _search_firecrawl,
    "linkup": _search_linkup,
    "you": _search_you,
    "jina": _search_jina,
}


async def search_provider(
    config: ProviderResolvedConfig,
    query: str,
    limit: int = 5,
) -> WebSearchProviderResult:
    item = get_provider_catalog(config.provider_id)
    if item is None:
        raise WebSearchError("unknown_provider", "Unknown web search provider", provider_id=config.provider_id)
    if item.requires_api_key and not config.api_key:
        raise WebSearchError("missing_api_key", "请先配置 API Key", provider_id=config.provider_id)

    searcher = _SEARCHERS.get(config.provider_id)
    if searcher is None:
        raise WebSearchError("not_implemented", "Provider 未接入搜索运行时", provider_id=config.provider_id)

    normalized_limit = _limit(limit)
    start = time.perf_counter()
    try:
        results = await searcher(config, str(query or "").strip(), normalized_limit)
    except asyncio.TimeoutError as exc:
        raise WebSearchError("provider_timeout", "Provider 请求超时", provider_id=config.provider_id) from exc
    except WebSearchError:
        raise
    except Exception as exc:
        raise WebSearchError(
            "provider_bad_response",
            _mask_secret(str(exc), config.api_key),
            provider_id=config.provider_id,
        ) from exc
    return WebSearchProviderResult(
        provider_id=config.provider_id,
        results=results[:normalized_limit],
        elapsed_ms=_elapsed_ms(start),
    )


async def search_enabled_providers(
    db,
    query: str,
    limit: int = 5,
    provider_id: str = "",
) -> WebSearchProviderResult:
    async def _search_and_record(config: ProviderResolvedConfig) -> WebSearchProviderResult:
        start = time.perf_counter()
        try:
            provider_result = await search_provider(config, query, limit=limit)
            record_provider_usage(
                db,
                config.provider_id,
                ok=True,
                duration_ms=_elapsed_ms(start),
            )
            return provider_result
        except WebSearchError as exc:
            record_provider_usage(
                db,
                config.provider_id,
                ok=False,
                error_code=exc.error_code,
                duration_ms=_elapsed_ms(start),
            )
            raise

    requested = str(provider_id or "").strip()
    if requested:
        config = resolve_provider_config(db, requested)
        if not config.enabled:
            raise WebSearchError("provider_disabled", f"Provider {requested} 未启用", provider_id=requested)
        return await _search_and_record(config)

    configs = [resolve_provider_config(db, item.id) for item in list_provider_catalog()]
    enabled_configs = [config for config in configs if config.enabled]
    if not enabled_configs:
        raise WebSearchError("no_enabled_provider", "没有启用的搜索 provider，请先到管理后台“搜索 API”启用至少一个 provider。")

    last_error: WebSearchError | None = None
    for config in enabled_configs:
        try:
            result = await _search_and_record(config)
            if result.results:
                return result
            last_error = WebSearchError("empty_results", f"Provider {config.provider_id} 返回空结果", provider_id=config.provider_id)
        except WebSearchError as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    raise WebSearchError("provider_bad_response", "所有搜索 provider 均未返回结果")

"""Web Search provider 连接测试。"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

from core.web_search.provider_catalog import get_provider_catalog
from core.web_search.provider_settings import ProviderResolvedConfig


USER_AGENT = "Nanobot-WebSearchConfig/1.0"
TIMEOUT_SECONDS = 8


@dataclass
class ProviderTestResult:
    ok: bool
    provider_id: str
    duration_ms: int
    message: str
    sample_count: int = 0
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "ok": self.ok,
            "provider_id": self.provider_id,
            "duration_ms": self.duration_ms,
            "message": self.message,
            "sample_count": self.sample_count,
        }
        if self.error_code:
            data["error_code"] = self.error_code
        return data


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


def _failure(provider_id: str, start: float, message: str, error_code: str, api_key: str = "") -> ProviderTestResult:
    return ProviderTestResult(
        ok=False,
        provider_id=provider_id,
        duration_ms=_elapsed_ms(start),
        message=_mask_secret(message, api_key),
        error_code=error_code,
    )


def _success(provider_id: str, start: float, sample_count: int) -> ProviderTestResult:
    return ProviderTestResult(
        ok=True,
        provider_id=provider_id,
        duration_ms=_elapsed_ms(start),
        message="连接成功",
        sample_count=sample_count,
    )


def _status_error_code(status: int) -> str:
    if status in {401, 403}:
        return "provider_auth_failed"
    if status == 429:
        return "provider_rate_limited"
    return "provider_bad_response"


async def _json_response(response: aiohttp.ClientResponse) -> Any:
    try:
        return await response.json(content_type=None)
    except Exception:
        text = await response.text()
        raise ValueError(f"Provider returned non-json response: {text[:200]}")


async def _error_body_snippet(response: aiohttp.ClientResponse) -> str:
    """非 2xx 时安全读取 body 片段供错误信息使用,body 非 JSON 也不抛异常。"""
    try:
        text = await response.text()
    except Exception:
        return ""
    return text.strip()[:200]


async def _test_searxng(config: ProviderResolvedConfig, query: str, start: float) -> ProviderTestResult:
    if not config.base_url:
        return _failure(config.provider_id, start, "请先配置 SearXNG Base URL", "invalid_base_url")
    url = f"{config.base_url.rstrip('/')}/search"
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS),
        headers={"User-Agent": USER_AGENT},
    ) as session:
        async with session.get(url, params={"q": query, "format": "json"}, max_redirects=3) as response:
            if response.status < 200 or response.status >= 300:
                snippet = await _error_body_snippet(response)
                return _failure(config.provider_id, start, f"Provider 返回 {response.status}: {snippet}", _status_error_code(response.status))
            data = await _json_response(response)
            results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(results, list):
                return _failure(config.provider_id, start, "Provider 响应缺少 results", "provider_bad_response")
            return _success(config.provider_id, start, len(results))


async def _test_serper(config: ProviderResolvedConfig, query: str, start: float) -> ProviderTestResult:
    url = f"{(config.base_url or 'https://google.serper.dev').rstrip('/')}/search"
    headers = {"X-API-KEY": config.api_key, "User-Agent": USER_AGENT}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS), headers=headers) as session:
        async with session.post(url, json={"q": query, "num": 3}, max_redirects=3) as response:
            if response.status < 200 or response.status >= 300:
                snippet = await _error_body_snippet(response)
                return _failure(config.provider_id, start, f"Provider 返回 {response.status}: {snippet}", _status_error_code(response.status), config.api_key)
            data = await _json_response(response)
            organic = data.get("organic") if isinstance(data, dict) else None
            if not isinstance(organic, list):
                return _failure(config.provider_id, start, "Provider 响应缺少 organic", "provider_bad_response", config.api_key)
            return _success(config.provider_id, start, len(organic))


async def _test_brave(config: ProviderResolvedConfig, query: str, start: float) -> ProviderTestResult:
    url = f"{(config.base_url or 'https://api.search.brave.com/res/v1').rstrip('/')}/web/search"
    headers = {"X-Subscription-Token": config.api_key, "User-Agent": USER_AGENT}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS), headers=headers) as session:
        async with session.get(url, params={"q": query, "count": 3}, max_redirects=3) as response:
            if response.status < 200 or response.status >= 300:
                snippet = await _error_body_snippet(response)
                return _failure(config.provider_id, start, f"Provider 返回 {response.status}: {snippet}", _status_error_code(response.status), config.api_key)
            data = await _json_response(response)
            results = ((data.get("web") or {}).get("results")) if isinstance(data, dict) else None
            if not isinstance(results, list):
                return _failure(config.provider_id, start, "Provider 响应缺少 web.results", "provider_bad_response", config.api_key)
            return _success(config.provider_id, start, len(results))


async def _test_tavily(config: ProviderResolvedConfig, query: str, start: float) -> ProviderTestResult:
    url = f"{(config.base_url or 'https://api.tavily.com').rstrip('/')}/search"
    body = {"api_key": config.api_key, "query": query, "max_results": 3}
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS),
        headers={"User-Agent": USER_AGENT},
    ) as session:
        async with session.post(url, json=body, max_redirects=3) as response:
            if response.status < 200 or response.status >= 300:
                snippet = await _error_body_snippet(response)
                return _failure(config.provider_id, start, f"Provider 返回 {response.status}: {snippet}", _status_error_code(response.status), config.api_key)
            data = await _json_response(response)
            results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(results, list):
                return _failure(config.provider_id, start, "Provider 响应缺少 results", "provider_bad_response", config.api_key)
            return _success(config.provider_id, start, len(results))


def _ddgs_search(query: str) -> list:
    try:
        from ddgs import DDGS
    except Exception:
        try:
            from duckduckgo_search import DDGS
        except Exception as exc:
            raise ImportError("缺少 ddgs 或 duckduckgo_search 依赖") from exc

    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=3))


async def _test_ddgs(config: ProviderResolvedConfig, query: str, start: float) -> ProviderTestResult:
    try:
        results = await asyncio.to_thread(_ddgs_search, query)
    except ImportError as exc:
        return _failure(config.provider_id, start, str(exc), "dependency_missing")
    except Exception as exc:
        return _failure(config.provider_id, start, str(exc), "provider_bad_response")
    return _success(config.provider_id, start, len(results))


async def test_provider(provider_id: str, config: ProviderResolvedConfig, query: str) -> ProviderTestResult:
    start = time.perf_counter()
    item = get_provider_catalog(provider_id)
    if item is None:
        return _failure(provider_id, start, "Unknown web search provider", "unknown_provider")
    if not item.testable:
        return _failure(provider_id, start, "暂不支持连接测试", "not_implemented")
    if item.requires_api_key and not config.api_key:
        return _failure(provider_id, start, "请先配置 API Key", "missing_api_key")

    try:
        if provider_id == "searxng":
            return await _test_searxng(config, query, start)
        if provider_id == "serper":
            return await _test_serper(config, query, start)
        if provider_id == "brave":
            return await _test_brave(config, query, start)
        if provider_id == "tavily":
            return await _test_tavily(config, query, start)
        if provider_id == "ddgs":
            return await _test_ddgs(config, query, start)
        return _failure(provider_id, start, "暂不支持连接测试", "not_implemented")
    except asyncio.TimeoutError:
        return _failure(provider_id, start, "Provider 请求超时", "provider_timeout", config.api_key)
    except Exception as exc:
        return _failure(provider_id, start, str(exc), "provider_bad_response", config.api_key)

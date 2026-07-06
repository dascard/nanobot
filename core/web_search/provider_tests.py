"""Web Search provider 连接测试。"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from core.web_search.provider_catalog import get_provider_catalog
from core.web_search.provider_settings import ProviderResolvedConfig
from core.web_search.search_runtime import WebSearchError, search_provider
from core.web_search.usage_stats import record_provider_usage


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


async def test_provider(provider_id: str, config: ProviderResolvedConfig, query: str, db=None) -> ProviderTestResult:
    start = time.perf_counter()
    item = get_provider_catalog(provider_id)
    if item is None:
        return _failure(provider_id, start, "Unknown web search provider", "unknown_provider")
    if item.requires_api_key and not config.api_key:
        return _failure(provider_id, start, "请先配置 API Key", "missing_api_key")

    try:
        result = await search_provider(config, query, limit=3)
        record_provider_usage(
            db,
            provider_id,
            ok=True,
            duration_ms=_elapsed_ms(start),
        )
        return _success(provider_id, start, len(result.results))
    except WebSearchError as exc:
        record_provider_usage(
            db,
            provider_id,
            ok=False,
            error_code=exc.error_code,
            duration_ms=_elapsed_ms(start),
        )
        return _failure(provider_id, start, exc.message, exc.error_code, config.api_key)
    except Exception as exc:
        record_provider_usage(
            db,
            provider_id,
            ok=False,
            error_code="provider_bad_response",
            duration_ms=_elapsed_ms(start),
        )
        return _failure(provider_id, start, str(exc), "provider_bad_response", config.api_key)

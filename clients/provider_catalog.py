"""Provider 模型目录发现客户端。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from foundation.llm.safe_diagnostics import safe_response_summary


MAX_CATALOG_RESPONSE_BYTES = 2 * 1024 * 1024


class ProviderDiscoveryUnsupportedError(RuntimeError):
    pass


def _safe_provider_error(value: object, *, api_key: str) -> str:
    """生成有界错误摘要，并移除上游可能回显的当前凭据。"""

    summary = safe_response_summary(value, max_chars=300)
    if api_key:
        summary = summary.replace(api_key, "[REDACTED]")
    return summary


def _catalog_url(provider: Mapping[str, Any]) -> str:
    driver_type = str(provider.get("driver_type") or "openai").strip().lower()
    if driver_type != "openai":
        raise ProviderDiscoveryUnsupportedError(
            f"{driver_type} 驱动尚未接入 Nanobot 模型目录发现"
        )
    base_url = str(provider.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise ValueError("Provider 未配置 Base URL")
    return f"{base_url}/models"


def discover_provider_models(
    provider: Mapping[str, Any],
    *,
    timeout_seconds: float = 10,
    opener_factory: Callable[..., Any] | None = None,
) -> list[str]:
    """调用 Provider 的模型目录端点并返回去重后的模型 ID。"""

    url = _catalog_url(provider)
    headers = {"Accept": "application/json"}
    api_key = str(provider.get("api_key") or "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    factory = opener_factory or urllib.request.build_opener
    opener = factory(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            raw = response.read(MAX_CATALOG_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(4096)
        except Exception:
            body = b""
        detail = _safe_provider_error(
            body.decode("utf-8", errors="replace"),
            api_key=api_key,
        )
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"Provider 返回 HTTP {exc.code}{suffix}") from exc
    except Exception as exc:
        raise RuntimeError(
            _safe_provider_error(exc, api_key=api_key) or type(exc).__name__
        ) from exc

    if len(raw) > MAX_CATALOG_RESPONSE_BYTES:
        raise RuntimeError("Provider 模型目录响应超过 2 MiB 上限")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Provider 模型目录不是有效 JSON") from exc
    items = payload.get("data", []) if isinstance(payload, dict) else []
    if not isinstance(items, list):
        raise RuntimeError("Provider 模型目录缺少 data 数组")
    return sorted({
        str(item.get("id") or "").strip()
        for item in items
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    })


__all__ = [
    "ProviderDiscoveryUnsupportedError",
    "discover_provider_models",
]

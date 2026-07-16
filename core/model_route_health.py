"""模型路由共用的异步健康探测。"""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import aiohttp


ModelRouteHealthStatus = Literal[
    "ready",
    "not_configured",
    "provider_disabled",
    "timeout",
    "connection_refused",
    "dns_error",
    "auth_failed",
    "client_error",
    "server_error",
    "invalid_models_response",
    "model_not_ready",
    "network_error",
]


@dataclass(frozen=True)
class ModelRouteHealth:
    status: ModelRouteHealthStatus
    reachable: bool
    usable: bool
    status_code: int | None
    latency_ms: int

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reachable": self.reachable,
            "usable": self.usable,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
            "auth_error": self.status == "auth_failed",
        }


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.monotonic() - started_at) * 1000))


def _models_url(base_url: str) -> str:
    parts = urlsplit(base_url.strip())
    path = f"{parts.path.rstrip('/')}/models"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _provider_enabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _network_failure_status(exc: Exception) -> ModelRouteHealthStatus:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, aiohttp.ServerTimeoutError)):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "connection_refused"
    if isinstance(exc, socket.gaierror):
        return "dns_error"
    if isinstance(exc, aiohttp.ClientConnectorError):
        os_error = exc.os_error
        if isinstance(os_error, ConnectionRefusedError):
            return "connection_refused"
        if isinstance(os_error, socket.gaierror):
            return "dns_error"
    return "network_error"


def _model_ids(payload: object) -> list[str] | None:
    if not isinstance(payload, Mapping):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    result: list[str] = []
    for item in data:
        if isinstance(item, Mapping):
            model_id = item.get("id")
        elif isinstance(item, str):
            model_id = item
        else:
            continue
        normalized = str(model_id or "").strip()
        if normalized:
            result.append(normalized)
    return result


async def probe_model_route(
    route: Mapping[str, Any],
    session: aiohttp.ClientSession,
) -> ModelRouteHealth:
    """探测 ``resolve_model_route()`` 生成的最终路由快照。"""

    started_at = time.monotonic()
    base_url = str(route.get("base_url") or "").strip()
    if not base_url:
        return ModelRouteHealth("not_configured", False, False, None, 0)
    if not _provider_enabled(route.get("provider_enabled", True)):
        return ModelRouteHealth("provider_disabled", False, False, None, 0)

    api_key = str(route.get("api_key") or "").strip()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        route_timeout = float(route.get("timeout") or 10)
    except (TypeError, ValueError):
        route_timeout = 10
    timeout = aiohttp.ClientTimeout(total=max(0.1, min(route_timeout, 10.0)))

    try:
        async with session.get(
            _models_url(base_url),
            headers=headers,
            timeout=timeout,
        ) as response:
            status_code = int(response.status)
            latency_ms = _elapsed_ms(started_at)
            if status_code in {401, 403}:
                return ModelRouteHealth(
                    "auth_failed", True, False, status_code, latency_ms
                )
            if 400 <= status_code < 500:
                return ModelRouteHealth(
                    "client_error", True, False, status_code, latency_ms
                )
            if status_code >= 500:
                return ModelRouteHealth(
                    "server_error", True, False, status_code, latency_ms
                )
            if not 200 <= status_code < 300:
                return ModelRouteHealth(
                    "client_error", True, False, status_code, latency_ms
                )
            try:
                payload = await response.json(content_type=None)
            except Exception:
                return ModelRouteHealth(
                    "invalid_models_response", True, False, status_code, latency_ms
                )
            model_ids = _model_ids(payload)
            if model_ids is None:
                return ModelRouteHealth(
                    "invalid_models_response", True, False, status_code, latency_ms
                )
            target_model = str(route.get("model") or "").strip()
            model_is_explicit = target_model not in {"", "未指定", "*"}
            if not model_ids or (model_is_explicit and target_model not in model_ids):
                return ModelRouteHealth(
                    "model_not_ready", True, False, status_code, latency_ms
                )
            return ModelRouteHealth("ready", True, True, status_code, latency_ms)
    except Exception as exc:
        return ModelRouteHealth(
            _network_failure_status(exc),
            False,
            False,
            None,
            _elapsed_ms(started_at),
        )

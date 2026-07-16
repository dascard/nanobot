"""启动网络连通性探测。"""

from __future__ import annotations

import asyncio
import time
from logging import Logger
from typing import Any

import aiohttp

from core.build_info import resolve_build_info


async def _probe_public_endpoint(
    session: aiohttp.ClientSession,
    url: str,
) -> tuple[str, int | None, int]:
    started_at = time.monotonic()
    try:
        async with session.get(
            url,
            headers={"User-Agent": "Nanobot/1.0"},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as response:
            status_code = int(response.status)
            status = "ready" if 200 <= status_code < 400 else "http_error"
            return status, status_code, round((time.monotonic() - started_at) * 1000)
    except Exception:
        return "network_error", None, round((time.monotonic() - started_at) * 1000)


async def run_startup_network_check(
    logger: Logger,
    *,
    session: aiohttp.ClientSession | Any | None = None,
) -> None:
    """异步探测实际模型路由和公开资讯端点，日志不包含凭据。"""

    build = resolve_build_info(logger=logger)
    logger.info(
        "[startup] server version=%s date=%s",
        build.commit,
        build.commit_date,
    )

    from config import NANOBOT_ADMIN_TOKEN, NANOBOT_API_TOKEN

    logger.info(
        "[startup] push_api_auth configured=%s",
        str(bool(NANOBOT_API_TOKEN)).lower(),
    )
    logger.info(
        "[startup] admin_web_token configured=%s",
        str(bool(NANOBOT_ADMIN_TOKEN)).lower(),
    )

    owns_session = session is None
    if session is None:
        session = aiohttp.ClientSession()

    try:
        from clients import classifier_client
        from core import model_route_health

        targets = (
            ("reply", "reply"),
            ("timing_gate", "timing_gate"),
            ("sticker_describe", "sticker_describe"),
        )
        route_snapshots: list[tuple[str, dict[str, Any] | None]] = []
        for logical_name, route_key in targets:
            try:
                route = classifier_client.resolve_model_route(route_key)
            except Exception:
                route = None
            route_snapshots.append((logical_name, route))

        async def probe_route(logical_name: str, route: dict[str, Any] | None):
            if route is None:
                return logical_name, model_route_health.ModelRouteHealth(
                    "network_error", False, False, None, 0
                )
            try:
                health = await model_route_health.probe_model_route(route, session)
            except Exception:
                health = model_route_health.ModelRouteHealth(
                    "network_error", False, False, None, 0
                )
            return logical_name, health

        route_results = await asyncio.gather(
            *(probe_route(logical_name, route) for logical_name, route in route_snapshots)
        )
        public_results = await asyncio.gather(
            _probe_public_endpoint(session, "https://duckduckgo.com"),
            _probe_public_endpoint(session, "https://www.reddit.com/r/LocalLLaMA/.rss"),
        )

        failure_count = 0
        for logical_name, health in route_results:
            if not health.usable:
                failure_count += 1
            logger.info(
                "[NetworkCheck] route=%s status=%s reachable=%s usable=%s "
                "status_code=%s latency_ms=%s",
                logical_name,
                health.status,
                str(health.reachable).lower(),
                str(health.usable).lower(),
                health.status_code,
                health.latency_ms,
            )
        for logical_name, (status, status_code, latency_ms) in zip(
            ("ddg", "rss"),
            public_results,
            strict=True,
        ):
            if status != "ready":
                failure_count += 1
            logger.info(
                "[NetworkCheck] route=%s status=%s status_code=%s latency_ms=%s",
                logical_name,
                status,
                status_code,
                latency_ms,
            )
        if failure_count:
            logger.warning(
                "[NetworkCheck] unavailable_backends=%s",
                failure_count,
            )
    finally:
        if owns_session:
            await session.close()

"""启动网络连通性探测。"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from logging import Logger
from pathlib import Path


def run_startup_network_check(logger: Logger) -> None:
    """启动时探测关键后端连通性，结果记入日志。"""
    try:
        project_root = Path(__file__).resolve().parents[1]
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=project_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        dt = subprocess.check_output(
            ["git", "log", "-1", "--format=%ci", "--date=short"],
            cwd=project_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()[:10]
        logger.info("[startup] server version=%s date=%s", sha, dt)
    except Exception:
        logger.info("[startup] server version=unknown")

    from config import NANOBOT_ADMIN_TOKEN, NANOBOT_API_TOKEN

    logger.info("[startup] ========================================")
    logger.info("[startup] Push API auth: %s", "enabled" if NANOBOT_API_TOKEN else "disabled")
    logger.info("[startup] Admin WebUI Token: %s", NANOBOT_ADMIN_TOKEN)
    logger.info("[startup] 访问 http://host:8000 并输入此 Token 登录")
    logger.info("[startup] ========================================")

    targets: dict[str, str] = {}
    proxy_url = os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY") or ""
    opener = (
        urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        )
        if proxy_url
        else urllib.request.build_opener()
    )

    def _fetch(url: str, timeout: int):
        req = urllib.request.Request(url, headers={"User-Agent": "Nanobot/1.0"})
        return opener.open(req, timeout=timeout)

    base = os.environ.get("NEW_API_BASE_URL", "http://10.60.42.158:9000/v1")
    key = os.environ.get("NEW_API_KEY", "")
    t0 = time.time()
    try:
        req = urllib.request.Request(
            f"{base}/models",
            headers={"Authorization": f"Bearer {key}"} if key else {},
        )
        with urllib.request.build_opener().open(req, timeout=5) as response:
            n = len(json.loads(response.read()).get("data", []))
        targets["llm_api"] = f"OK ({n} models, {time.time() - t0:.1f}s)"
    except Exception as exc:
        targets["llm_api"] = f"FAIL: {type(exc).__name__}: {exc}"

    qwen = os.environ.get("CLASSIFIER_API_URL", "http://10.60.42.158:9999/v1")
    t0 = time.time()
    try:
        with urllib.request.build_opener().open(f"{qwen}/models", timeout=3):
            targets["qwen"] = f"OK ({time.time() - t0:.1f}s)"
    except Exception as exc:
        targets["qwen"] = f"FAIL: {type(exc).__name__}: {exc}"

    t0 = time.time()
    try:
        with _fetch("https://duckduckgo.com", 5) as response:
            targets["ddg"] = f"OK ({response.status}, {time.time() - t0:.1f}s)"
    except Exception as exc:
        targets["ddg"] = f"FAIL ({time.time() - t0:.1f}s): {type(exc).__name__}: {exc}"

    t0 = time.time()
    try:
        with _fetch("https://www.reddit.com/r/LocalLLaMA/.rss", 5) as response:
            targets["rss"] = f"OK ({response.status}, {time.time() - t0:.1f}s)"
    except Exception as exc:
        targets["rss"] = f"FAIL ({time.time() - t0:.1f}s): {type(exc).__name__}: {exc}"

    ok = sum(1 for value in targets.values() if value.startswith("OK"))
    fail = len(targets) - ok
    logger.info("[NetworkCheck] %d/%d reachable:", ok, len(targets))
    for name, status in targets.items():
        logger.info("  %s: %s", name, status)
    if fail:
        logger.warning("[NetworkCheck] %d backends unreachable - news_search may be slow", fail)

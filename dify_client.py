"""
Dify Workflow HTTP 客户端。
封装所有对 Dify API 的调用，含指数退避重试。
"""
import json
import time
import logging
import requests

from config import (
    DIFY_BASE_URL,
    DIFY_MAX_RETRIES,
    DIFY_RETRY_BASE_DELAY,
    DIFY_REQUEST_TIMEOUT,
)

logger = logging.getLogger("nanobot.dify")


def call_dify_workflow(api_key: str, inputs: dict) -> dict:
    """
    通用 Dify Workflow 调用器（含指数退避重试）。
    返回 outputs 字典，失败时抛出 RuntimeError。
    """
    url = f"{DIFY_BASE_URL}/workflows/run"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": inputs,
        "response_mode": "blocking",
        "user": "nanobot-server",
    }
    safe_key = f"***{api_key[-4:]}" if len(api_key) >= 4 else "***"

    last_exc: Exception | None = None
    for attempt in range(1, DIFY_MAX_RETRIES + 1):
        try:
            logger.info(
                f"  → [attempt {attempt}/{DIFY_MAX_RETRIES}] POST {url}  "
                f"key={safe_key}  inputs_keys={list(inputs.keys())}"
            )
            resp = requests.post(
                url, headers=headers, json=payload, timeout=DIFY_REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            body = resp.json()

            outputs = body.get("data", {}).get("outputs", {})
            if not outputs:
                raise RuntimeError(
                    f"Dify returned empty outputs. "
                    f"Response: {json.dumps(body, ensure_ascii=False)[:500]}"
                )
            return outputs

        except (requests.RequestException, RuntimeError) as exc:
            last_exc = exc
            if attempt < DIFY_MAX_RETRIES:
                delay = DIFY_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    f"  ⚠ Attempt {attempt} failed: {exc}. "
                    f"Retrying in {delay:.1f}s..."
                )
                time.sleep(delay)
            else:
                logger.error(f"  ✗ All {DIFY_MAX_RETRIES} attempts failed.")

    raise RuntimeError(
        f"Dify workflow call failed after {DIFY_MAX_RETRIES} retries: {last_exc}"
    )

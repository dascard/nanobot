"""OpenAI SDK 请求出口追踪。

用于不可直接修改第三方 provider 代码的场景：在主仓侧幂等包装
`chat.completions.create`，记录传给 SDK 的真实请求参数。
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _request_payload_from_sdk_kwargs(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    payload = dict(kwargs)
    extra_body = payload.pop("extra_body", None)
    if isinstance(extra_body, dict):
        payload.update(extra_body)
    if args:
        payload["_args"] = list(args)
    return payload


def install_openai_chat_completion_tracer(
    llm: Any,
    *,
    provider: str = "",
    base_url: str = "",
) -> bool:
    """幂等包装 OpenAI SDK `chat.completions.create`，记录真实出站参数。"""
    try:
        client = getattr(llm, "_client", None)
        completions = getattr(getattr(client, "chat", None), "completions", None)
        if completions is None or not hasattr(completions, "create"):
            return False

        current_create = completions.create
        original_create = getattr(current_create, "__dict__", {}).get(
            "_nanobot_original_create",
            current_create,
        )
        target_provider = str(provider or getattr(llm, "provider_name", "") or "unknown")
        target_base_url = str(
            base_url
            or getattr(llm, "base_url", "")
            or getattr(llm, "_base_url_input", "")
            or ""
        ).rstrip("/")

        async def _traced_create(*args: Any, _orig=original_create, **kwargs: Any) -> Any:
            try:
                from core.llm_trace_context import get_llm_trace_vars
                from core.tracing import LLMRequestTracer

                trace_id, run_id, source = get_llm_trace_vars()
                request_payload = _request_payload_from_sdk_kwargs(args, kwargs)
                headers = {"Content-Type": "application/json"}
                api_key = str(getattr(llm, "_api_key", "") or "")
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                extra_headers = getattr(llm, "_extra_headers", {}) or {}
                if isinstance(extra_headers, dict):
                    headers.update(extra_headers)

                LLMRequestTracer.record_request(
                    trace_id=trace_id,
                    run_id=run_id,
                    source=source or "unknown",
                    provider=target_provider,
                    model=str(request_payload.get("model", "") or ""),
                    url=f"{target_base_url}/chat/completions" if target_base_url else "",
                    method="POST",
                    headers=headers,
                    request=request_payload,
                    status="created",
                )
            except Exception as exc:
                logger.debug("OpenAI SDK request tracing skipped: %s", exc)

            result = _orig(*args, **kwargs)
            if inspect.isawaitable(result):
                return await result
            return result

        setattr(_traced_create, "_nanobot_original_create", original_create)
        completions.create = _traced_create  # type: ignore[method-assign]
        return True
    except Exception as exc:
        logger.warning("install OpenAI SDK request tracer failed: %s", exc)
        return False

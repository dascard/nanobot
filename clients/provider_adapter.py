"""OpenAI-compatible 模型供应商 Adapter。"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.request
from collections.abc import Mapping
from typing import Any, Callable

from core.model_provider.contracts import (
    ModelProviderRequest,
    ModelProviderResponse,
    ProviderAvailability,
    ProviderCapability,
    ProviderDescriptor,
)
from core.model_provider.registry import ModelProviderRegistry
from core.model_provider.response_normalization import strip_think_blocks
from core.runtime.event_bus import emit_runtime_event
from core.runtime.events import RuntimeEventContext
from foundation.llm.model_options import apply_enable_thinking_to_payload
from foundation.llm.safe_diagnostics import safe_response_summary


logger = logging.getLogger("nanobot.provider.openai_compatible")

DEFAULT_RESPONSE_MAX_BYTES = 1024 * 1024


def _response_audit(raw_body: bytes, *, truncated: bool) -> dict[str, object]:
    return {
        "response_body_omitted": True,
        "response_body_chars": len(raw_body.decode("utf-8", errors="replace")),
        "response_body_sha256": hashlib.sha256(raw_body).hexdigest(),
        "response_body_truncated": bool(truncated),
    }


def _payload_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8", errors="replace")
    return hashlib.sha256(encoded).hexdigest()


def _model_event_context() -> RuntimeEventContext:
    try:
        from core.llm_trace_context import get_llm_trace_vars

        trace_id, run_id, _source = get_llm_trace_vars()
    except Exception:
        trace_id, run_id = "", ""
    return RuntimeEventContext(trace_id=trace_id, run_id=run_id)


def _read_bounded_response(response: Any, limit: int) -> tuple[bytes, bool]:
    try:
        raw_body = response.read(limit + 1)
    except TypeError:
        raw_body = response.read()
    if isinstance(raw_body, str):
        raw_bytes = raw_body.encode("utf-8")
    elif isinstance(raw_body, (bytes, bytearray, memoryview)):
        raw_bytes = bytes(raw_body)
    else:
        raise ValueError("model response body must be bytes or string")
    truncated = len(raw_bytes) > limit
    return raw_bytes[:limit], truncated


class OpenAICompatibleProviderAdapter:
    """同步 OpenAI Chat Completions Adapter。

    endpoint 和 API key 只保存在 Adapter 私有字段中，Registry introspection
    仅暴露是否已配置，避免控制面意外输出凭据或内部地址。
    """

    def __init__(
        self,
        *,
        descriptor: ProviderDescriptor,
        base_url: str,
        api_key: str = "",
        enabled: bool = True,
        response_max_bytes: int = DEFAULT_RESPONSE_MAX_BYTES,
        opener_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._descriptor = descriptor
        self._base_url = str(base_url or "").rstrip("/")
        self._api_key = str(api_key or "")
        self._enabled = bool(enabled)
        self._response_max_bytes = int(response_max_bytes)
        self._opener_factory = opener_factory or urllib.request.build_opener
        if self._response_max_bytes <= 0:
            raise ValueError("response_max_bytes 必须大于 0")

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    def availability(self) -> ProviderAvailability:
        if not self._enabled:
            return ProviderAvailability(
                available=False,
                configured=bool(self._base_url),
                reason_code="provider_disabled",
            )
        if not self._base_url:
            return ProviderAvailability(
                available=False,
                configured=False,
                reason_code="not_configured",
            )
        return ProviderAvailability(
            available=True,
            configured=True,
            reason_code="configured",
        )

    def introspect(self) -> Mapping[str, object]:
        result = self._descriptor.metadata()
        result.update(self.availability().metadata())
        result["endpoint_configured"] = bool(self._base_url)
        result["authentication_configured"] = bool(self._api_key)
        return result

    def complete(self, request: ModelProviderRequest) -> ModelProviderResponse:
        availability = self.availability()
        if not availability.available:
            raise RuntimeError(
                f"provider {self._descriptor.id} unavailable: {availability.reason_code}"
            )

        payload: dict[str, Any] = {
            "messages": [dict(message) for message in request.messages],
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.model:
            payload["model"] = request.model
        apply_enable_thinking_to_payload(
            payload,
            request.model,
            request.enable_thinking,
        )
        url = f"{self._base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        started = time.time()
        event_context = _model_event_context()
        request_sha256 = _payload_digest(payload)
        event_attributes = {
            "provider": self._descriptor.id,
            "model": request.model,
            "source": request.trace_source,
            "request_sha256": request_sha256,
        }
        candidate_index = request.metadata.get("candidate_index")
        if candidate_index is not None:
            event_attributes["candidate_index"] = candidate_index
        emit_runtime_event(
            "model.request",
            "started",
            context=event_context,
            attributes=event_attributes,
        )
        log_id = self._record_trace_request(
            request=request,
            url=url,
            headers=headers,
            payload=payload,
        )
        response_status = 0
        response_audit: dict[str, object] = {}
        try:
            body, response_status, response_audit = self._perform_request(
                url=url,
                headers=headers,
                payload=payload,
                timeout_seconds=request.timeout_seconds,
            )
            result = self._parse_response(body)
            self._finish_trace(
                log_id=log_id,
                response=body,
                response_status=response_status,
                status="success",
                started=started,
            )
            emit_runtime_event(
                "model.request",
                "succeeded",
                context=event_context,
                attributes={
                    **event_attributes,
                    "response_sha256": _payload_digest(body),
                    "latency_ms": (time.time() - started) * 1000,
                },
            )
            return result
        except Exception as exc:
            failure_audit = getattr(exc, "response_audit", response_audit)
            self._finish_trace(
                log_id=log_id,
                response=failure_audit,
                response_status=getattr(exc, "code", 0) or response_status,
                status="error",
                started=started,
                error=safe_response_summary(exc, max_chars=1000),
            )
            emit_runtime_event(
                "model.request",
                "failed",
                context=event_context,
                attributes={
                    **event_attributes,
                    "latency_ms": (time.time() - started) * 1000,
                    "error_type": type(exc).__name__,
                },
            )
            raise

    def _perform_request(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> tuple[dict[str, Any], int, dict[str, object]]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        opener = self._opener_factory(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", None) or (
                response.getcode() if hasattr(response, "getcode") else 200
            )
            raw_body, truncated = _read_bounded_response(
                response,
                self._response_max_bytes,
            )
            audit = _response_audit(raw_body, truncated=truncated)
            if truncated:
                error = ValueError("model response exceeds size limit")
                setattr(error, "response_audit", audit)
                raise error
            try:
                body = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                error = ValueError(f"model response invalid JSON: {exc}")
                setattr(error, "response_audit", audit)
                raise error from exc
        if not isinstance(body, dict):
            raise ValueError("model response root must be an object")
        return body, int(status), audit

    @staticmethod
    def _parse_response(body: Mapping[str, Any]) -> ModelProviderResponse:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("model response missing choices[0]")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("model response missing choices[0].message")
        content = message.get("content")
        if not isinstance(content, str):
            raise ValueError(
                "model response choices[0].message.content must be a string"
            )
        reasoning = message.get("reasoning_content")
        if reasoning is None:
            reasoning = message.get("reasoning")
        if reasoning is not None and not isinstance(reasoning, str):
            raise ValueError(
                "model response choices[0].message.reasoning_content must be a string or null"
            )
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise ValueError(
                "model response choices[0].finish_reason must be a string or null"
            )
        usage = body.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise ValueError("model response usage must be an object or null")
        return ModelProviderResponse(
            content=strip_think_blocks(content),
            reasoning_content=reasoning or "",
            finish_reason=finish_reason,
            usage=usage or {},
            raw_response=dict(body),
        )

    def _record_trace_request(
        self,
        *,
        request: ModelProviderRequest,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> int:
        try:
            from core.llm_trace_context import get_llm_trace_vars
            from core.tracing import LLMRequestTracer

            trace_id, run_id, source = get_llm_trace_vars()
            return LLMRequestTracer.record_request(
                trace_id=trace_id,
                run_id=run_id,
                source=source or request.trace_source,
                provider=self._descriptor.id,
                model=request.model,
                url=url,
                method="POST",
                headers=dict(headers),
                request=dict(payload),
                status="created",
            )
        except Exception:
            return 0

    @staticmethod
    def _finish_trace(
        *,
        log_id: int,
        response: Mapping[str, Any],
        response_status: int,
        status: str,
        started: float,
        error: str = "",
    ) -> None:
        try:
            from core.tracing import LLMRequestTracer

            LLMRequestTracer.finish_request(
                log_id=log_id,
                response=dict(response),
                response_status=response_status,
                status=status,
                error=error,
                latency_ms=int((time.time() - started) * 1000),
            )
        except Exception:
            return


def descriptor_from_route(route: Mapping[str, Any]) -> ProviderDescriptor:
    """把兼容 route dict 映射为类型化 Provider 描述符。"""

    provider_id = str(route.get("provider_id") or "openai_compatible").strip()
    return ProviderDescriptor(
        id=provider_id,
        display_name=provider_id,
        capabilities=frozenset(
            {
                ProviderCapability.CHAT_COMPLETION,
                ProviderCapability.REASONING_CONTENT,
            }
        ),
        implementation="openai_compatible",
        built_in=provider_id in {"newapi", "local_llama", "local_vision"},
    )


def adapter_from_route(
    route: Mapping[str, Any],
    *,
    opener_factory: Callable[..., Any] | None = None,
) -> OpenAICompatibleProviderAdapter:
    """兼容 composition factory：现有 route 仍是配置事实源。"""

    return OpenAICompatibleProviderAdapter(
        descriptor=descriptor_from_route(route),
        base_url=str(route.get("base_url") or ""),
        api_key=str(route.get("api_key") or ""),
        enabled=route.get("provider_enabled") is not False,
        opener_factory=opener_factory,
    )


def registry_from_provider_configs(
    provider_configs: list[Mapping[str, Any]],
) -> ModelProviderRegistry:
    """把现有配置目录转换为已冻结 Registry，并执行重复/别名校验。"""

    registry = ModelProviderRegistry()
    for config in provider_configs:
        provider_id = str(config.get("id") or "").strip()
        aliases = tuple(
            str(alias).strip()
            for alias in config.get("legacy_aliases", ())
            if str(alias).strip()
        )
        descriptor = ProviderDescriptor(
            id=provider_id,
            display_name=str(config.get("name") or provider_id),
            capabilities=frozenset(
                {
                    ProviderCapability.CHAT_COMPLETION,
                    ProviderCapability.REASONING_CONTENT,
                }
            ),
            aliases=aliases,
            implementation="openai_compatible",
            built_in=provider_id in {"newapi", "local_llama", "local_vision"},
        )
        registry.register(
            OpenAICompatibleProviderAdapter(
                descriptor=descriptor,
                base_url=str(config.get("base_url") or ""),
                api_key=str(config.get("api_key") or ""),
                enabled=config.get("enabled") is not False,
            )
        )
    return registry.freeze()

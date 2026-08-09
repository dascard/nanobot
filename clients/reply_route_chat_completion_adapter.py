"""Reply Route 到 Native Runtime ``ChatCompletionPort`` 的生产适配器。"""

from __future__ import annotations

import codecs
import hashlib
import json
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any

import aiohttp

from core.model_provider.chat_runtime import (
    ChatCompletionRequest,
    ChatCompletionRuntimeUnavailableError,
)
from core.model_provider.route_plan import ReplyRoutePlan
from core.runtime.event_bus import emit_runtime_event
from foundation.llm.model_options import apply_enable_thinking_to_payload
from foundation.llm.stream_trace import LLMStreamTraceAccumulator
from foundation.llm.cost_usage import normalize_llm_cost_usage


_RESERVED_EXTRA_BODY_FIELDS = frozenset({
    "messages",
    "model",
    "stream",
    "stream_options",
    "temperature",
    "max_tokens",
    "tools",
    "tool_choice",
    "metadata",
    "request_id",
    "run_id",
    "trace_id",
})
_ALLOWED_DRIVER_OPTIONS = frozenset({"echo_reasoning"})
_DETERMINISTIC_HTTP_STATUS = {
    400: "invalid request",
    401: "unauthorized",
    403: "forbidden",
    404: "invalid route",
    413: "request too large",
    422: "invalid request",
}


class ReplyRouteUnavailableError(ChatCompletionRuntimeUnavailableError):
    """当前 Reply Route 不能由 Native Chat Completions 传输执行。"""


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _payload_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _response_error(status: int) -> dict[str, object]:
    label = _DETERMINISTIC_HTTP_STATUS.get(status, f"HTTP {status}")
    return {
        "error": {
            "code": f"http_{status}",
            "message": f"{label} (HTTP {status})",
        }
    }


def _runtime_cost_usage(
    route: ReplyRoutePlan,
    response: Mapping[str, Any],
) -> dict[str, Any]:
    """仅为 Runtime Usage 补充成本，不改写 Provider 原始 Trace。"""

    result = dict(response)
    raw_usage = result.get("usage")
    if not isinstance(raw_usage, Mapping):
        return result
    usage = dict(raw_usage)

    def tokens(*keys: str) -> int:
        for key in keys:
            value = usage.get(key)
            if type(value) is int and value >= 0:
                return value
        return 0

    cost = normalize_llm_cost_usage(
        result,
        successful=True,
        input_tokens=tokens("prompt_tokens", "input_tokens"),
        output_tokens=tokens("completion_tokens", "output_tokens"),
        cost_input_1m=route.cost_input_1m,
        cost_output_1m=route.cost_output_1m,
    )
    if cost.source == "not_available":
        return result
    usage["cost_microunits"] = cost.cost_microusd
    result["usage"] = usage
    return result


def _validate_header(name: object, value: object) -> tuple[str, str]:
    normalized_name = str(name or "").strip()
    normalized_value = str(value or "").strip()
    if not normalized_name or not normalized_value:
        raise ReplyRouteUnavailableError("Native Reply Route 包含空请求头")
    if any(char in normalized_name or char in normalized_value for char in ("\r", "\n")):
        raise ReplyRouteUnavailableError("Native Reply Route 请求头包含非法换行")
    if normalized_name.lower() in {"host", "content-length"}:
        raise ReplyRouteUnavailableError(
            f"Native Reply Route 不允许覆盖请求头 {normalized_name}"
        )
    return normalized_name, normalized_value


def _validate_route(route: ReplyRoutePlan) -> None:
    driver_type = str(route.driver_type or "openai").strip().lower()
    if driver_type != "openai":
        raise ReplyRouteUnavailableError(
            f"Native Runtime 暂不支持 Reply Route Driver：{driver_type}；"
            "请将该会话灰度到 KT Runtime"
        )
    if route.request_protocol != "openai_chat_completions":
        raise ReplyRouteUnavailableError(
            "Native Reply Route 只接受 openai_chat_completions Descriptor"
        )
    if route.request_path != "/chat/completions":
        raise ReplyRouteUnavailableError(
            "Native Reply Route request_path 与 Adapter 合同不一致"
        )
    if not str(route.base_url or "").strip():
        raise ReplyRouteUnavailableError("Native Reply Route 缺少 Base URL")
    if route.profile_id and not str(route.model or "").strip():
        raise ReplyRouteUnavailableError("Native Reply Route Preset 缺少模型 ID")
    if route.provider_native_tools:
        names = ",".join(sorted(str(name) for name in route.provider_native_tools))
        raise ReplyRouteUnavailableError(
            "Native Runtime 尚未接入 Provider 原生工具，不能安全执行：" + names
        )

    reserved = _RESERVED_EXTRA_BODY_FIELDS.intersection(route.extra_body)
    if reserved:
        fields = ",".join(sorted(reserved))
        raise ReplyRouteUnavailableError(
            "Native Reply Route extra_body 不能覆盖受控字段：" + fields
        )
    unsupported_options = set(route.driver_options).difference(
        _ALLOWED_DRIVER_OPTIONS
    )
    if unsupported_options:
        fields = ",".join(sorted(str(name) for name in unsupported_options))
        raise ReplyRouteUnavailableError(
            "Native Reply Route 包含未支持的 driver_options：" + fields
        )
    for name, value in route.extra_headers.items():
        _validate_header(name, value)


def _has_image(messages: tuple[Mapping[str, Any], ...]) -> bool:
    for message in messages:
        content = message.get("content")
        if not isinstance(content, (list, tuple)):
            continue
        for part in content:
            if isinstance(part, Mapping) and part.get("type") == "image_url":
                return True
    return False


class ReplyRouteChatCompletionAdapter:
    """执行当前 Bridge 已冻结的 OpenAI-compatible Reply Route。

    Adapter 不做候选模型切换；候选选择、熔断以及副作用后的继续语义仍由
    Nanobot Bridge/Runtime 负责。每次调用先复制当前 Route，避免请求处理中
    观察到后续候选的连接配置。
    """

    def __init__(
        self,
        *,
        session_provider: Callable[[], aiohttp.ClientSession | None] | None = None,
        response_max_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        if response_max_bytes <= 0:
            raise ValueError("response_max_bytes 必须大于 0")
        self._session_provider = session_provider
        self._response_max_bytes = int(response_max_bytes)
        self._route: ReplyRoutePlan | None = None

    @property
    def adapter_id(self) -> str:
        return "reply_route_openai_chat_completion"

    def bind_route(self, route: ReplyRoutePlan) -> None:
        if not isinstance(route, ReplyRoutePlan):
            raise TypeError("route 必须是 ReplyRoutePlan")
        _validate_route(route)
        self._route = deepcopy(route)

    def _require_route(self) -> ReplyRoutePlan:
        route = self._route
        if route is None:
            raise ReplyRouteUnavailableError("Native Reply Route 尚未绑定")
        return deepcopy(route)

    @asynccontextmanager
    async def _request_session(self) -> AsyncIterator[aiohttp.ClientSession]:
        session = self._session_provider() if self._session_provider is not None else None
        if session is not None and not getattr(session, "closed", False):
            yield session
            return
        async with aiohttp.ClientSession() as temporary:
            yield temporary

    @staticmethod
    def _headers(route: ReplyRoutePlan) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
        }
        for name, value in route.extra_headers.items():
            header_name, header_value = _validate_header(name, value)
            headers[header_name] = header_value
        if route.api_key:
            headers["Authorization"] = f"Bearer {route.api_key}"
        return headers

    @staticmethod
    def _payload(
        route: ReplyRoutePlan,
        request: ChatCompletionRequest,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        if route.profile_id and request.manual_model != route.model:
            raise ReplyRouteUnavailableError(
                "Native 请求模型与已冻结 Reply Route Preset 不一致"
            )
        model = str(request.manual_model or route.model or "").strip()
        if not model:
            raise ReplyRouteUnavailableError("Native Reply Route 没有可调用模型")

        capabilities = dict(route.capabilities or {})
        if stream and capabilities.get("supports_stream") is not True:
            raise ReplyRouteUnavailableError("当前 Reply Route 不支持流式输出")
        if request.tools and capabilities.get("supports_tools") is not True:
            raise ReplyRouteUnavailableError("当前 Reply Route 不支持工具调用")
        if _has_image(request.messages) and capabilities.get("supports_image") is not True:
            raise ReplyRouteUnavailableError("当前 Reply Route 不支持图片输入")

        payload = _json_value(route.extra_body)
        payload.update({
            "model": model,
            "messages": [_json_value(message) for message in request.messages],
            "temperature": request.temperature,
            "stream": stream,
        })
        if stream:
            payload["stream_options"] = {"include_usage": True}
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.tools:
            payload["tools"] = [_json_value(tool) for tool in request.tools]
            payload["tool_choice"] = "auto"
        if route.reasoning_effort:
            payload.setdefault("reasoning_effort", route.reasoning_effort)
        if route.service_tier:
            payload.setdefault("service_tier", route.service_tier)
        apply_enable_thinking_to_payload(
            payload,
            model,
            request.enable_thinking,
        )
        return payload

    @staticmethod
    def _event_attributes(
        route: ReplyRoutePlan,
        request: ChatCompletionRequest,
        payload: Mapping[str, Any],
    ) -> dict[str, object]:
        return {
            "route_key": "reply",
            "provider": route.provider_id or route.registry_provider,
            "model": str(payload.get("model") or ""),
            "source": request.trace_source or "native_agent",
            "request_sha256": _payload_sha256(payload),
        }

    @staticmethod
    def _start_trace(
        route: ReplyRoutePlan,
        request: ChatCompletionRequest,
        payload: Mapping[str, Any],
    ) -> int:
        try:
            from core.tracing import LLMRequestTracer

            return LLMRequestTracer.record_request(
                trace_id=request.trace_id,
                run_id=request.run_id,
                source=request.trace_source or "native_agent",
                provider=route.provider_id or route.registry_provider,
                model=str(payload.get("model") or ""),
                url=f"{route.base_url.rstrip('/')}/chat/completions",
                method="POST",
                headers=ReplyRouteChatCompletionAdapter._headers(route),
                request=dict(payload),
                status="created",
            )
        except Exception:
            return 0

    @staticmethod
    def _finish_trace(
        log_id: int,
        *,
        response: Mapping[str, Any],
        response_status: int,
        status: str,
        latency_ms: int,
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
                latency_ms=latency_ms,
                record_ledger_usage=False,
            )
        except Exception:
            return

    async def complete_chat(
        self,
        request: ChatCompletionRequest,
    ) -> Mapping[str, Any]:
        route = self._require_route()
        payload = self._payload(route, request, stream=False)
        attributes = self._event_attributes(route, request, payload)
        started = time.monotonic()
        log_id = self._start_trace(route, request, payload)
        response_status = 0
        emit_runtime_event("model.request", "started", attributes=attributes)
        try:
            async with self._request_session() as session:
                async with session.post(
                    f"{route.base_url.rstrip('/')}/chat/completions",
                    headers=self._headers(route),
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=float(route.timeout)),
                ) as response:
                    status = int(response.status)
                    response_status = status
                    raw_body = await response.read()
            if status != 200:
                error_response = _response_error(status)
                self._finish_trace(
                    log_id,
                    response=error_response,
                    response_status=status,
                    status="failed",
                    error=f"HTTP {status}",
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
                emit_runtime_event(
                    "model.request",
                    "failed",
                    attributes={
                        **attributes,
                        "latency_ms": (time.monotonic() - started) * 1000,
                        "failure_code": f"http_{status}",
                        "error_type": "http_status",
                    },
                )
                return error_response
            if len(raw_body) > self._response_max_bytes:
                raise ValueError("模型响应超过大小上限")
            decoded = json.loads(raw_body.decode("utf-8"))
            if not isinstance(decoded, Mapping):
                raise ValueError("模型响应根节点必须是对象")
            result = dict(decoded)
            self._finish_trace(
                log_id,
                response=result,
                response_status=status,
                status="success",
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            result = _runtime_cost_usage(route, result)
            emit_runtime_event(
                "model.request",
                "succeeded",
                attributes={
                    **attributes,
                    "latency_ms": (time.monotonic() - started) * 1000,
                    "response_sha256": hashlib.sha256(raw_body).hexdigest(),
                    "response_bytes": len(raw_body),
                    "response_truncated": False,
                },
            )
            return result
        except BaseException as exc:
            self._finish_trace(
                log_id,
                response={},
                response_status=response_status,
                status="error",
                error=type(exc).__name__,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            emit_runtime_event(
                "model.request",
                "failed",
                attributes={
                    **attributes,
                    "latency_ms": (time.monotonic() - started) * 1000,
                    "failure_code": "transport_error",
                    "error_type": type(exc).__name__,
                },
            )
            raise

    async def stream_chat(
        self,
        request: ChatCompletionRequest,
    ) -> AsyncIterator[Mapping[str, Any]]:
        route = self._require_route()
        payload = self._payload(route, request, stream=True)
        attributes = self._event_attributes(route, request, payload)
        started = time.monotonic()
        log_id = self._start_trace(route, request, payload)
        response_status = 0
        stream_trace = LLMStreamTraceAccumulator()
        emit_runtime_event("model.request", "started", attributes=attributes)
        response_bytes = 0
        response_digest = hashlib.sha256()
        try:
            async with self._request_session() as session:
                async with session.post(
                    f"{route.base_url.rstrip('/')}/chat/completions",
                    headers=self._headers(route),
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=float(route.timeout)),
                ) as response:
                    status = int(response.status)
                    response_status = status
                    if status != 200:
                        await response.read()
                        error_response = _response_error(status)
                        self._finish_trace(
                            log_id,
                            response=error_response,
                            response_status=status,
                            status="stream_error",
                            error=f"HTTP {status}",
                            latency_ms=int(
                                (time.monotonic() - started) * 1000
                            ),
                        )
                        emit_runtime_event(
                            "model.request",
                            "failed",
                            attributes={
                                **attributes,
                                "latency_ms": (time.monotonic() - started) * 1000,
                                "failure_code": f"http_{status}",
                                "error_type": "http_status",
                            },
                        )
                        yield error_response
                        return
                    async for raw_event, decoded in self._iter_sse(response):
                        response_bytes += len(raw_event)
                        if response_bytes > self._response_max_bytes:
                            raise ValueError("模型流响应超过大小上限")
                        response_digest.update(raw_event)
                        stream_trace.record_chunk(dict(decoded))
                        yield _runtime_cost_usage(route, decoded)
            stream_response = stream_trace.build_response()
            self._finish_trace(
                log_id,
                response=stream_response,
                response_status=response_status,
                status="stream_success",
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            emit_runtime_event(
                "model.request",
                "succeeded",
                attributes={
                    **attributes,
                    "latency_ms": (time.monotonic() - started) * 1000,
                    "response_sha256": response_digest.hexdigest(),
                    "response_bytes": response_bytes,
                    "response_truncated": False,
                },
            )
        except BaseException as exc:
            self._finish_trace(
                log_id,
                response={},
                response_status=response_status,
                status="stream_error",
                error=type(exc).__name__,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            emit_runtime_event(
                "model.request",
                "failed",
                attributes={
                    **attributes,
                    "latency_ms": (time.monotonic() - started) * 1000,
                    "failure_code": "transport_error",
                    "error_type": type(exc).__name__,
                },
            )
            raise

    @staticmethod
    async def _iter_sse(
        response: aiohttp.ClientResponse,
    ) -> AsyncIterator[tuple[bytes, Mapping[str, Any]]]:
        decoder = codecs.getincrementaldecoder("utf-8")()
        buffer = ""
        data_lines: list[str] = []

        def flush_event() -> tuple[bytes, Mapping[str, Any]] | None:
            if not data_lines:
                return None
            data = "\n".join(data_lines).strip()
            data_lines.clear()
            if not data or data == "[DONE]":
                return None
            decoded = json.loads(data)
            if not isinstance(decoded, Mapping):
                raise ValueError("模型 SSE data 必须是 JSON 对象")
            return data.encode("utf-8"), dict(decoded)

        async for chunk in response.content.iter_any():
            buffer += decoder.decode(chunk)
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.rstrip("\r")
                if not line:
                    event = flush_event()
                    if event is not None:
                        yield event
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())

        buffer += decoder.decode(b"", final=True)
        if buffer:
            line = buffer.rstrip("\r")
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        event = flush_event()
        if event is not None:
            yield event


__all__ = [
    "ReplyRouteChatCompletionAdapter",
    "ReplyRouteUnavailableError",
]

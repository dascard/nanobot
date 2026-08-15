"""模型 Provider 的只读分层诊断 Adapter。"""

from __future__ import annotations

import json
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from clients.provider_catalog import discover_provider_models
from core.model_provider.diagnostics import (
    ProviderDiagnosticCheck,
    ProviderDiagnosticLayer,
    ProviderDiagnosticReport,
    ProviderDiagnosticStatus,
    ProviderErrorCategory,
    classify_provider_error,
    provider_error_retryable,
)
from foundation.llm.safe_diagnostics import safe_response_summary


_MAX_PROBE_RESPONSE_BYTES = 1024 * 1024
_TINY_TEST_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/"
    "x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


@dataclass(frozen=True, slots=True)
class ProviderDoctorOptions:
    model: str = ""
    live_completion: bool = True
    probe_stream: bool = False
    probe_tools: bool = False
    probe_image: bool = False
    timeout_seconds: float = 10.0
    model_capabilities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not 0.1 <= float(self.timeout_seconds) <= 60:
            raise ValueError("Provider Doctor timeout 必须在 0.1 到 60 秒之间")
        object.__setattr__(self, "model", str(self.model or "").strip()[:160])
        object.__setattr__(
            self,
            "model_capabilities",
            frozenset(
                str(item or "").strip()
                for item in self.model_capabilities
                if str(item or "").strip()
            ),
        )


class ProviderProbeFailure(RuntimeError):
    def __init__(
        self,
        category: ProviderErrorCategory,
        message: str,
        *,
        http_status: int = 0,
    ) -> None:
        self.category = ProviderErrorCategory(category)
        self.http_status = int(http_status or 0)
        super().__init__(str(message or self.category.value)[:300])


def _safe_failure(error: object, *, api_key: str = "") -> str:
    summary = safe_response_summary(error, max_chars=300)
    if api_key:
        summary = summary.replace(api_key, "[REDACTED]")
    return summary


def _passed(
    layer: ProviderDiagnosticLayer,
    *,
    latency_ms: int = 0,
    summary: str = "",
    metadata: dict[str, Any] | None = None,
) -> ProviderDiagnosticCheck:
    return ProviderDiagnosticCheck(
        layer=layer,
        status=ProviderDiagnosticStatus.PASSED,
        latency_ms=max(0, int(latency_ms)),
        summary=summary,
        metadata=metadata or {},
    )


def _failed(
    layer: ProviderDiagnosticLayer,
    error: object,
    *,
    category: ProviderErrorCategory | None = None,
    http_status: int = 0,
    latency_ms: int = 0,
    api_key: str = "",
) -> ProviderDiagnosticCheck:
    resolved = category or classify_provider_error(error, http_status=http_status)
    return ProviderDiagnosticCheck(
        layer=layer,
        status=ProviderDiagnosticStatus.FAILED,
        category=resolved,
        latency_ms=max(0, int(latency_ms)),
        summary=_safe_failure(error, api_key=api_key),
        retryable=provider_error_retryable(resolved),
    )


def _skipped(
    layer: ProviderDiagnosticLayer,
    summary: str,
    *,
    unsupported: bool = False,
) -> ProviderDiagnosticCheck:
    return ProviderDiagnosticCheck(
        layer=layer,
        status=(
            ProviderDiagnosticStatus.UNSUPPORTED
            if unsupported
            else ProviderDiagnosticStatus.SKIPPED
        ),
        category=(
            ProviderErrorCategory.CAPABILITY
            if unsupported
            else ProviderErrorCategory.NONE
        ),
        summary=summary,
    )


def _network_target(base_url: str) -> tuple[str, int, bool]:
    parsed = urlsplit(str(base_url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Provider Base URL 不是有效的 http/https 地址")
    secure = parsed.scheme == "https"
    return parsed.hostname, parsed.port or (443 if secure else 80), secure


def _probe_dns(host: str, port: int) -> int:
    started = time.monotonic()
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise socket.gaierror("DNS 未返回可连接地址")
    return int((time.monotonic() - started) * 1000)


def _probe_tcp(host: str, port: int, timeout: float) -> int:
    started = time.monotonic()
    connection = socket.create_connection((host, port), timeout=timeout)
    try:
        return int((time.monotonic() - started) * 1000)
    finally:
        connection.close()


def _probe_tls(host: str, port: int, timeout: float) -> int:
    started = time.monotonic()
    connection = socket.create_connection((host, port), timeout=timeout)
    try:
        context = ssl.create_default_context()
        wrapped = context.wrap_socket(connection, server_hostname=host)
        try:
            return int((time.monotonic() - started) * 1000)
        finally:
            wrapped.close()
    except Exception:
        connection.close()
        raise


def _read_response(response: Any) -> bytes:
    try:
        raw = response.read(_MAX_PROBE_RESPONSE_BYTES + 1)
    except TypeError:
        raw = response.read()
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    raw = bytes(raw)
    if len(raw) > _MAX_PROBE_RESPONSE_BYTES:
        raise ProviderProbeFailure(
            ProviderErrorCategory.RESPONSE_PROTOCOL,
            "Provider 诊断响应超过 1 MiB 上限",
        )
    return raw


def _validate_non_stream_response(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderProbeFailure(
            ProviderErrorCategory.RESPONSE_PROTOCOL,
            "Provider 最小请求返回了无效 JSON",
        ) from exc
    if not isinstance(payload, dict):
        raise ProviderProbeFailure(
            ProviderErrorCategory.RESPONSE_PROTOCOL,
            "Provider 最小请求响应根节点不是对象",
        )
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderProbeFailure(
            ProviderErrorCategory.RESPONSE_PROTOCOL,
            "Provider 最小请求响应缺少 choices",
        )
    return payload


def _validate_stream_response(raw: bytes, elapsed_ms: int) -> dict[str, Any]:
    first_chunk_ms = 0
    usage: dict[str, Any] = {}
    chunks = 0
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(b":"):
            continue
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if data == b"[DONE]":
            continue
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderProbeFailure(
                ProviderErrorCategory.RESPONSE_PROTOCOL,
                "Provider 流式探测返回了无效 SSE JSON",
            ) from exc
        if not isinstance(payload, dict):
            continue
        chunks += 1
        if not first_chunk_ms:
            first_chunk_ms = max(1, elapsed_ms)
        if isinstance(payload.get("usage"), dict):
            usage = dict(payload["usage"])
    if chunks == 0:
        raise ProviderProbeFailure(
            ProviderErrorCategory.RESPONSE_PROTOCOL,
            "Provider 流式探测没有返回数据 chunk",
        )
    return {
        "usage": usage,
        "stream_metrics": {"first_chunk_ms": first_chunk_ms},
        "probe_chunk_count": chunks,
    }


def _probe_payload(kind: ProviderDiagnosticLayer, model: str) -> dict[str, Any]:
    content: Any = "只回复 OK"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
        "max_tokens": 2,
        "stream": kind is ProviderDiagnosticLayer.STREAM,
    }
    if kind is ProviderDiagnosticLayer.STREAM:
        payload["stream_options"] = {"include_usage": True}
    elif kind is ProviderDiagnosticLayer.TOOL:
        payload["tools"] = [{
            "type": "function",
            "function": {
                "name": "nanobot_provider_probe",
                "description": "只用于验证工具 schema 是否被接受",
                "parameters": {"type": "object", "properties": {}},
            },
        }]
        payload["tool_choice"] = "auto"
    elif kind is ProviderDiagnosticLayer.IMAGE:
        payload["messages"] = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "确认收到测试图片，只回复 OK"},
                {"type": "image_url", "image_url": {"url": _TINY_TEST_PNG}},
            ],
        }]
    return payload


def _trace_start(provider: Any, model: str, payload: dict[str, Any]) -> int:
    try:
        from core.tracing import LLMRequestTracer

        descriptor = provider.descriptor
        return LLMRequestTracer.record_request(
            source="provider_doctor",
            provider=provider.id,
            model=model,
            url=f"{provider.base_url.rstrip('/')}{descriptor.request_path}",
            method="POST",
            headers={
                "Authorization": (
                    f"Bearer {provider.api_key}" if provider.api_key else ""
                )
            },
            request=payload,
        )
    except Exception:
        return 0


def _trace_finish(
    log_id: int,
    *,
    response: dict[str, Any],
    response_status: int,
    status: str,
    latency_ms: int,
    error: str = "",
) -> None:
    try:
        from core.tracing import LLMRequestTracer

        LLMRequestTracer.finish_request(
            log_id=log_id,
            response=response,
            response_status=response_status,
            status=status,
            latency_ms=latency_ms,
            error=error,
        )
    except Exception:
        return


def _probe_chat_request(
    provider: Any,
    *,
    model: str,
    kind: ProviderDiagnosticLayer,
    timeout: float,
) -> tuple[int, dict[str, Any]]:
    descriptor = provider.descriptor
    url = f"{provider.base_url.rstrip('/')}{descriptor.request_path}"
    payload = _probe_payload(kind, model)
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.monotonic()
    log_id = _trace_start(provider, model, payload)
    status_code = 0
    try:
        with opener.open(request, timeout=timeout) as response:
            status_code = int(
                getattr(response, "status", 0)
                or (response.getcode() if hasattr(response, "getcode") else 200)
            )
            raw = _read_response(response)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        parsed = (
            _validate_stream_response(raw, elapsed_ms)
            if kind is ProviderDiagnosticLayer.STREAM
            else _validate_non_stream_response(raw)
        )
        _trace_finish(
            log_id,
            response=parsed,
            response_status=status_code,
            status=(
                "stream_success"
                if kind is ProviderDiagnosticLayer.STREAM
                else "success"
            ),
            latency_ms=elapsed_ms,
        )
        return elapsed_ms, parsed
    except urllib.error.HTTPError as exc:
        status_code = int(exc.code or 0)
        category = classify_provider_error(exc, http_status=status_code)
        failure = ProviderProbeFailure(
            category,
            f"Provider 返回 HTTP {status_code}",
            http_status=status_code,
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        _trace_finish(
            log_id,
            response={"response_body_omitted": True},
            response_status=status_code,
            status="error",
            latency_ms=elapsed_ms,
            error=str(failure),
        )
        raise failure from exc
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        failure = (
            exc
            if isinstance(exc, ProviderProbeFailure)
            else ProviderProbeFailure(classify_provider_error(exc), str(exc))
        )
        _trace_finish(
            log_id,
            response={"response_body_omitted": True},
            response_status=status_code,
            status="error",
            latency_ms=elapsed_ms,
            error=str(failure),
        )
        raise failure from exc


def run_provider_doctor(
    provider: Any,
    options: ProviderDoctorOptions | None = None,
) -> ProviderDiagnosticReport:
    """执行只读 Doctor；只有显式 live 选项会发送最小模型请求。"""

    options = options or ProviderDoctorOptions()
    descriptor = provider.descriptor
    checks: list[ProviderDiagnosticCheck] = []
    configuration_errors: list[str] = []
    if not provider.enabled:
        configuration_errors.append("Provider 已禁用")
    # OpenAI-compatible Doctor 走本模块自己的受控 HTTP Adapter，不依赖
    # KT/OpenAI SDK。Native Runtime 同样可以在未安装 ``openai`` 包时执行
    # Chat Completions，因此不能把 KT Driver 的可用性误判为 Provider
    # 连通性失败。其他协议尚无独立 Doctor Adapter，仍按 Driver 依赖
    # fail closed。
    if (
        not provider.runtime_available
        and descriptor.request_protocol.value != "openai_chat_completions"
    ):
        configuration_errors.append(
            provider.runtime_unavailable_reason or "Provider Driver 不可用"
        )
    if provider.driver_type != "codex" and not provider.base_url:
        configuration_errors.append("Provider 未配置 Base URL")
    if provider.driver_type in {"anthropic", "codex"} and not (
        provider.credential_configured
    ):
        configuration_errors.append("Provider 凭据未配置")
    if configuration_errors:
        checks.append(_failed(
            ProviderDiagnosticLayer.CONFIGURATION,
            "；".join(configuration_errors),
            category=ProviderErrorCategory.CONFIGURATION,
        ))
        for layer in (
            ProviderDiagnosticLayer.DNS,
            ProviderDiagnosticLayer.TRANSPORT,
            ProviderDiagnosticLayer.TLS,
            ProviderDiagnosticLayer.AUTHENTICATION,
            ProviderDiagnosticLayer.CATALOG,
            ProviderDiagnosticLayer.MODEL,
            ProviderDiagnosticLayer.COMPLETION,
            ProviderDiagnosticLayer.STREAM,
            ProviderDiagnosticLayer.TOOL,
            ProviderDiagnosticLayer.IMAGE,
        ):
            checks.append(_skipped(layer, "配置检查未通过"))
        return ProviderDiagnosticReport(
            provider_id=provider.id,
            request_protocol=descriptor.request_protocol.value,
            descriptor=descriptor.metadata(),
            checks=tuple(checks),
            model=options.model,
        )

    checks.append(_passed(
        ProviderDiagnosticLayer.CONFIGURATION,
        summary="Provider 配置可用于当前 Doctor Adapter",
        metadata={
            "credential_configured": bool(provider.credential_configured),
            "anonymous_auth_probe": not bool(provider.credential_configured),
            "agent_runtime_available": bool(provider.runtime_available),
            "agent_runtime_unavailable_reason": (
                str(provider.runtime_unavailable_reason or "")
                if not provider.runtime_available
                else ""
            ),
        },
    ))
    if provider.driver_type == "codex":
        for layer in (
            ProviderDiagnosticLayer.DNS,
            ProviderDiagnosticLayer.TRANSPORT,
            ProviderDiagnosticLayer.TLS,
            ProviderDiagnosticLayer.AUTHENTICATION,
            ProviderDiagnosticLayer.CATALOG,
            ProviderDiagnosticLayer.MODEL,
            ProviderDiagnosticLayer.COMPLETION,
            ProviderDiagnosticLayer.STREAM,
            ProviderDiagnosticLayer.TOOL,
            ProviderDiagnosticLayer.IMAGE,
        ):
            checks.append(_skipped(
                layer,
                "Codex OAuth 连接由账号诊断与 Preset 探测负责",
                unsupported=True,
            ))
        return ProviderDiagnosticReport(
            provider_id=provider.id,
            request_protocol=descriptor.request_protocol.value,
            descriptor=descriptor.metadata(),
            checks=tuple(checks),
            model=options.model,
        )

    api_key = str(provider.api_key or "")
    host, port, secure = _network_target(provider.base_url)
    try:
        latency = _probe_dns(host, port)
        checks.append(_passed(
            ProviderDiagnosticLayer.DNS,
            latency_ms=latency,
            summary="DNS 解析成功",
        ))
    except Exception as exc:
        checks.append(_failed(ProviderDiagnosticLayer.DNS, exc, api_key=api_key))
        for layer in (
            ProviderDiagnosticLayer.TRANSPORT,
            ProviderDiagnosticLayer.TLS,
            ProviderDiagnosticLayer.AUTHENTICATION,
            ProviderDiagnosticLayer.CATALOG,
            ProviderDiagnosticLayer.MODEL,
            ProviderDiagnosticLayer.COMPLETION,
            ProviderDiagnosticLayer.STREAM,
            ProviderDiagnosticLayer.TOOL,
            ProviderDiagnosticLayer.IMAGE,
        ):
            checks.append(_skipped(layer, "DNS 检查未通过"))
        return ProviderDiagnosticReport(
            provider_id=provider.id,
            request_protocol=descriptor.request_protocol.value,
            descriptor=descriptor.metadata(),
            checks=tuple(checks),
            model=options.model,
        )

    try:
        latency = _probe_tcp(host, port, options.timeout_seconds)
        checks.append(_passed(
            ProviderDiagnosticLayer.TRANSPORT,
            latency_ms=latency,
            summary="TCP 连接成功",
        ))
    except Exception as exc:
        checks.append(_failed(
            ProviderDiagnosticLayer.TRANSPORT,
            exc,
            category=classify_provider_error(exc),
            api_key=api_key,
        ))
        for layer in (
            ProviderDiagnosticLayer.TLS,
            ProviderDiagnosticLayer.AUTHENTICATION,
            ProviderDiagnosticLayer.CATALOG,
            ProviderDiagnosticLayer.MODEL,
            ProviderDiagnosticLayer.COMPLETION,
            ProviderDiagnosticLayer.STREAM,
            ProviderDiagnosticLayer.TOOL,
            ProviderDiagnosticLayer.IMAGE,
        ):
            checks.append(_skipped(layer, "传输连接未通过"))
        return ProviderDiagnosticReport(
            provider_id=provider.id,
            request_protocol=descriptor.request_protocol.value,
            descriptor=descriptor.metadata(),
            checks=tuple(checks),
            model=options.model,
        )

    if secure:
        try:
            latency = _probe_tls(host, port, options.timeout_seconds)
            checks.append(_passed(
                ProviderDiagnosticLayer.TLS,
                latency_ms=latency,
                summary="TLS 握手与证书校验成功",
            ))
        except Exception as exc:
            checks.append(_failed(
                ProviderDiagnosticLayer.TLS,
                exc,
                category=ProviderErrorCategory.TLS,
                api_key=api_key,
            ))
    else:
        checks.append(_skipped(
            ProviderDiagnosticLayer.TLS,
            "HTTP Endpoint 不执行 TLS 检查",
        ))

    if descriptor.request_protocol.value != "openai_chat_completions":
        for layer in (
            ProviderDiagnosticLayer.AUTHENTICATION,
            ProviderDiagnosticLayer.CATALOG,
            ProviderDiagnosticLayer.MODEL,
            ProviderDiagnosticLayer.COMPLETION,
            ProviderDiagnosticLayer.STREAM,
            ProviderDiagnosticLayer.TOOL,
            ProviderDiagnosticLayer.IMAGE,
        ):
            checks.append(_skipped(
                layer,
                "当前 Doctor Adapter 尚未实现该请求协议",
                unsupported=True,
            ))
        return ProviderDiagnosticReport(
            provider_id=provider.id,
            request_protocol=descriptor.request_protocol.value,
            descriptor=descriptor.metadata(),
            checks=tuple(checks),
            model=options.model,
        )

    models: list[str] = []
    catalog_ok = False
    started = time.monotonic()
    try:
        models = discover_provider_models(
            provider.internal_view(),
            timeout_seconds=options.timeout_seconds,
        )
        latency = int((time.monotonic() - started) * 1000)
        checks.append(_passed(
            ProviderDiagnosticLayer.AUTHENTICATION,
            latency_ms=latency,
            summary="认证与目录请求成功",
        ))
        checks.append(_passed(
            ProviderDiagnosticLayer.CATALOG,
            latency_ms=latency,
            summary="模型目录响应有效",
            metadata={"model_count": len(models)},
        ))
        catalog_ok = True
    except Exception as exc:
        latency = int((time.monotonic() - started) * 1000)
        category = classify_provider_error(exc)
        if category is ProviderErrorCategory.AUTHENTICATION:
            checks.append(_failed(
                ProviderDiagnosticLayer.AUTHENTICATION,
                exc,
                category=category,
                latency_ms=latency,
                api_key=api_key,
            ))
            checks.append(_skipped(
                ProviderDiagnosticLayer.CATALOG,
                "认证检查未通过",
            ))
        else:
            checks.append(_skipped(
                ProviderDiagnosticLayer.AUTHENTICATION,
                "目录请求未能确认认证状态",
            ))
            checks.append(_failed(
                ProviderDiagnosticLayer.CATALOG,
                exc,
                category=category,
                latency_ms=latency,
                api_key=api_key,
            ))

    if options.model:
        if catalog_ok and options.model not in models:
            checks.append(_failed(
                ProviderDiagnosticLayer.MODEL,
                "指定模型不在当前 Provider 目录中",
                category=ProviderErrorCategory.NOT_FOUND,
            ))
        elif catalog_ok:
            checks.append(_passed(
                ProviderDiagnosticLayer.MODEL,
                summary="指定模型存在于当前目录",
            ))
        else:
            checks.append(_skipped(
                ProviderDiagnosticLayer.MODEL,
                "目录不可用，无法核验模型身份；仍可执行显式 live probe",
            ))
    else:
        checks.append(_skipped(
            ProviderDiagnosticLayer.MODEL,
            "没有选择受管模型",
        ))

    completion_passed = False
    if options.live_completion and options.model:
        started = time.monotonic()
        try:
            latency, _response = _probe_chat_request(
                provider,
                model=options.model,
                kind=ProviderDiagnosticLayer.COMPLETION,
                timeout=options.timeout_seconds,
            )
            checks.append(_passed(
                ProviderDiagnosticLayer.COMPLETION,
                latency_ms=latency,
                summary="最小 Chat Completion 请求成功",
            ))
            completion_passed = True
        except ProviderProbeFailure as exc:
            checks.append(_failed(
                ProviderDiagnosticLayer.COMPLETION,
                exc,
                category=exc.category,
                http_status=exc.http_status,
                latency_ms=int((time.monotonic() - started) * 1000),
                api_key=api_key,
            ))
    else:
        checks.append(_skipped(
            ProviderDiagnosticLayer.COMPLETION,
            "未启用 live probe 或没有受管模型",
        ))

    optional_probes = (
        (
            ProviderDiagnosticLayer.STREAM,
            options.probe_stream,
            "supports_stream",
        ),
        (
            ProviderDiagnosticLayer.TOOL,
            options.probe_tools,
            "supports_tools",
        ),
        (
            ProviderDiagnosticLayer.IMAGE,
            options.probe_image,
            "supports_image",
        ),
    )
    for layer, enabled, capability in optional_probes:
        if not enabled:
            checks.append(_skipped(layer, "未请求该能力探测"))
            continue
        if capability not in options.model_capabilities:
            checks.append(_skipped(
                layer,
                "受管模型 Descriptor 未声明该能力",
                unsupported=True,
            ))
            continue
        if not completion_passed:
            checks.append(_skipped(layer, "最小 Completion 探测未通过"))
            continue
        started = time.monotonic()
        try:
            latency, _response = _probe_chat_request(
                provider,
                model=options.model,
                kind=layer,
                timeout=options.timeout_seconds,
            )
            checks.append(_passed(
                layer,
                latency_ms=latency,
                summary=f"{layer.value} 能力探测成功",
            ))
        except ProviderProbeFailure as exc:
            checks.append(_failed(
                layer,
                exc,
                category=exc.category,
                http_status=exc.http_status,
                latency_ms=int((time.monotonic() - started) * 1000),
                api_key=api_key,
            ))

    return ProviderDiagnosticReport(
        provider_id=provider.id,
        request_protocol=descriptor.request_protocol.value,
        descriptor=descriptor.metadata(),
        checks=tuple(checks),
        model=options.model,
    )


__all__ = [
    "ProviderDoctorOptions",
    "ProviderProbeFailure",
    "run_provider_doctor",
]

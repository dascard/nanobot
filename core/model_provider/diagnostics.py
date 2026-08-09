"""Provider Doctor 的框架无关诊断合同与稳定错误分类。"""

from __future__ import annotations

import asyncio
import socket
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class ProviderDiagnosticLayer(StrEnum):
    CONFIGURATION = "configuration"
    DNS = "dns"
    TRANSPORT = "transport"
    TLS = "tls"
    AUTHENTICATION = "authentication"
    CATALOG = "catalog"
    MODEL = "model"
    COMPLETION = "completion"
    STREAM = "stream"
    TOOL = "tool"
    IMAGE = "image"


class ProviderDiagnosticStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNSUPPORTED = "unsupported"


class ProviderErrorCategory(StrEnum):
    NONE = "none"
    CONFIGURATION = "configuration"
    DNS = "dns"
    CONNECT = "connect"
    TLS = "tls"
    AUTHENTICATION = "authentication"
    NOT_FOUND = "not_found"
    RATE_LIMIT = "rate_limit"
    INVALID_REQUEST = "invalid_request"
    CAPABILITY = "capability"
    TIMEOUT = "timeout"
    UPSTREAM = "upstream"
    RESPONSE_PROTOCOL = "response_protocol"
    CIRCUIT_OPEN = "circuit_open"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


_RETRYABLE_CATEGORIES = frozenset({
    ProviderErrorCategory.DNS,
    ProviderErrorCategory.CONNECT,
    ProviderErrorCategory.TIMEOUT,
    ProviderErrorCategory.RATE_LIMIT,
    ProviderErrorCategory.UPSTREAM,
})


def provider_error_retryable(category: ProviderErrorCategory | str) -> bool:
    return ProviderErrorCategory(category) in _RETRYABLE_CATEGORIES


def classify_provider_error(
    error: object = None,
    *,
    http_status: int = 0,
) -> ProviderErrorCategory:
    """把 SDK／HTTP 差异归一为稳定类别，不返回上游正文。"""

    status = int(http_status or 0)
    if status in {401, 403}:
        return ProviderErrorCategory.AUTHENTICATION
    if status == 404:
        return ProviderErrorCategory.NOT_FOUND
    if status in {408, 504}:
        return ProviderErrorCategory.TIMEOUT
    if status == 429:
        return ProviderErrorCategory.RATE_LIMIT
    if status in {400, 405, 409, 413, 415, 422}:
        return ProviderErrorCategory.INVALID_REQUEST
    if status >= 500:
        return ProviderErrorCategory.UPSTREAM

    if isinstance(error, asyncio.CancelledError):
        return ProviderErrorCategory.CANCELLED
    if isinstance(error, socket.gaierror):
        return ProviderErrorCategory.DNS
    if isinstance(error, ssl.SSLError):
        return ProviderErrorCategory.TLS
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return ProviderErrorCategory.TIMEOUT
    if isinstance(error, (ConnectionError, BrokenPipeError)):
        return ProviderErrorCategory.CONNECT

    text = str(error or "").strip().lower()
    if not text:
        return ProviderErrorCategory.NONE
    if any(marker in text for marker in (
        "circuit open",
        "circuit-disabled",
        "circuit disabled",
    )):
        return ProviderErrorCategory.CIRCUIT_OPEN
    if any(marker in text for marker in (
        "name resolution",
        "nodename nor servname",
        "temporary failure in name",
        "dns",
    )):
        return ProviderErrorCategory.DNS
    if any(marker in text for marker in (
        "certificate verify",
        "sslerror",
        "tls",
        "certificate has expired",
    )):
        return ProviderErrorCategory.TLS
    if any(marker in text for marker in (
        "timed out",
        "timeout",
        "deadline exceeded",
    )):
        return ProviderErrorCategory.TIMEOUT
    if any(marker in text for marker in (
        "http 401",
        "http 403",
        "unauthorized",
        "forbidden",
        "invalid api key",
        "authentication",
    )):
        return ProviderErrorCategory.AUTHENTICATION
    if any(marker in text for marker in (
        "http 429",
        "rate limit",
        "too many requests",
        "quota exceeded",
    )):
        return ProviderErrorCategory.RATE_LIMIT
    if any(marker in text for marker in (
        "not supported",
        "unsupported",
        "lacks required capabilities",
        "capability",
    )):
        return ProviderErrorCategory.CAPABILITY
    if any(marker in text for marker in (
        "invalid json",
        "missing choices",
        "response protocol",
        "response root",
    )):
        return ProviderErrorCategory.RESPONSE_PROTOCOL
    if any(f"http {status_code}" in text for status_code in range(500, 600)):
        return ProviderErrorCategory.UPSTREAM
    if any(marker in text for marker in (
        "connection refused",
        "connection reset",
        "network is unreachable",
        "connect call failed",
    )):
        return ProviderErrorCategory.CONNECT
    return ProviderErrorCategory.UNKNOWN


@dataclass(frozen=True, slots=True)
class ProviderDiagnosticCheck:
    layer: ProviderDiagnosticLayer
    status: ProviderDiagnosticStatus
    category: ProviderErrorCategory = ProviderErrorCategory.NONE
    latency_ms: int = 0
    summary: str = ""
    retryable: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        layer = ProviderDiagnosticLayer(self.layer)
        status = ProviderDiagnosticStatus(self.status)
        category = ProviderErrorCategory(self.category)
        if status is ProviderDiagnosticStatus.PASSED:
            category = ProviderErrorCategory.NONE
        if self.latency_ms < 0:
            raise ValueError("Provider diagnostic latency_ms 不能为负数")
        summary = str(self.summary or "").strip()[:300]
        object.__setattr__(self, "layer", layer)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def to_dict(self) -> dict[str, object]:
        return {
            "layer": self.layer.value,
            "status": self.status.value,
            "category": self.category.value,
            "latency_ms": self.latency_ms,
            "summary": self.summary,
            "retryable": self.retryable,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ProviderDiagnosticReport:
    provider_id: str
    request_protocol: str
    descriptor: Mapping[str, Any]
    checks: tuple[ProviderDiagnosticCheck, ...]
    model: str = ""

    def __post_init__(self) -> None:
        provider_id = str(self.provider_id or "").strip()
        protocol = str(self.request_protocol or "").strip()
        if not provider_id or not protocol:
            raise ValueError("Provider diagnostic 缺少 provider_id 或 protocol")
        checks = tuple(self.checks)
        seen: set[ProviderDiagnosticLayer] = set()
        for check in checks:
            if not isinstance(check, ProviderDiagnosticCheck):
                raise TypeError("Provider diagnostic checks 类型无效")
            if check.layer in seen:
                raise ValueError(
                    f"Provider diagnostic layer 重复: {check.layer.value}"
                )
            seen.add(check.layer)
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "request_protocol", protocol)
        object.__setattr__(self, "descriptor", MappingProxyType(dict(self.descriptor)))
        object.__setattr__(self, "checks", checks)
        object.__setattr__(self, "model", str(self.model or "").strip()[:160])

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(
            check.status is not ProviderDiagnosticStatus.FAILED
            for check in self.checks
        )

    @property
    def blocking_layer(self) -> str:
        return next((
            check.layer.value
            for check in self.checks
            if check.status is ProviderDiagnosticStatus.FAILED
        ), "")

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "provider_id": self.provider_id,
            "request_protocol": self.request_protocol,
            "model": self.model,
            "blocking_layer": self.blocking_layer,
            "descriptor": dict(self.descriptor),
            "checks": [check.to_dict() for check in self.checks],
        }


__all__ = [
    "ProviderDiagnosticCheck",
    "ProviderDiagnosticLayer",
    "ProviderDiagnosticReport",
    "ProviderDiagnosticStatus",
    "ProviderErrorCategory",
    "classify_provider_error",
    "provider_error_retryable",
]

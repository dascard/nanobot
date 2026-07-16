"""QQ push 的结构化 HTTP 传输与旧三态兼容适配。"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import math
import os
import re
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Literal

import aiohttp

logger = logging.getLogger("nanobot.outbound_transport")


class QQPushConfigurationError(ValueError):
    """QQ push 配置无效，异常不得包含配置原值。"""


def resolve_qq_push_token(
    environ: Mapping[str, str] | None = None,
) -> str:
    source = os.environ if environ is None else environ
    raw_token = str(source.get("NANOBOT_PUSH_TOKEN") or "")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in raw_token):
        raise QQPushConfigurationError(
            "NANOBOT_PUSH_TOKEN 包含非法控制字符"
        )
    token = raw_token.strip(" ")
    if not token:
        raise QQPushConfigurationError("NANOBOT_PUSH_TOKEN 未配置")
    return token


def _available_aiohttp_exception_types(
    *names: str,
) -> tuple[type[BaseException], ...]:
    result: list[type[BaseException]] = []
    for name in names:
        candidate = getattr(aiohttp, name, None)
        if isinstance(candidate, type) and issubclass(candidate, BaseException):
            result.append(candidate)
    return tuple(result)


_INVALID_URL_ERRORS = _available_aiohttp_exception_types(
    "InvalidURL",
    "InvalidUrlClientError",
    "NonHttpUrlClientError",
)
_TLS_FINGERPRINT_ERRORS = _available_aiohttp_exception_types(
    "ServerFingerprintMismatch",
)
_TLS_CERTIFICATE_ERRORS = _available_aiohttp_exception_types(
    "ClientConnectorCertificateError",
)
_TLS_ERRORS = _available_aiohttp_exception_types(
    "ClientSSLError",
    "ClientConnectorSSLError",
)
_CONNECTION_TIMEOUT_ERRORS = _available_aiohttp_exception_types(
    "ConnectionTimeoutError",
)
_SOCKET_TIMEOUT_ERRORS = _available_aiohttp_exception_types(
    "SocketTimeoutError",
)

DEFAULT_RESPONSE_BODY_LIMIT_BYTES = 16 * 1024
MAX_RETRY_AFTER_SECONDS = 300
_STREAM_CHUNK_BYTES = 8 * 1024

DeliveryCategory = Literal[
    "success",
    "transient",
    "endpoint",
    "destination",
    "payload",
    "payload_contract",
    "ambiguous",
]
TransportPhase = Literal["connect", "write", "read", "response_received"]

_DESTINATION_ERROR_TYPES = frozenset(
    {
        "destination_missing",
        "destination_rejected",
        "destination_deleted",
        "target_missing",
        "target_rejected",
        "target_deleted",
    }
)
_PAYLOAD_CONTRACT_ERROR_TYPES = frozenset(
    {
        "schema_contract_mismatch",
        "unsupported_envelope",
        "unsupported_envelope_version",
        "unsupported_schema_version",
    }
)
_STRUCTURED_ERROR_TYPES = (
    _DESTINATION_ERROR_TYPES | _PAYLOAD_CONTRACT_ERROR_TYPES
)
_RESPONSE_BODY_OMITTED_SUMMARY = "响应正文已省略"
_SAFE_EXCEPTION_SUMMARIES = {
    "invalid_url": "无效的出站 URL",
    "tls_certificate_error": "TLS 证书校验失败",
    "tls_error": "TLS 连接失败",
    "tls_fingerprint_mismatch": "TLS 指纹不匹配",
    "connect_timeout": "连接超时",
    "connection_refused": "连接被拒绝",
    "dns_error": "域名解析失败",
    "connection_reset_before_send": "发送前连接被重置",
    "connection_error": "连接失败",
    "read_timeout": "读取响应超时",
    "write_timeout": "发送请求超时",
    "connection_reset": "连接被重置",
    "transport_error": "出站传输失败",
}
_NAMED_HTTP_FAILURES: dict[int, tuple[DeliveryCategory, str]] = {
    400: ("payload", "bad_request"),
    401: ("endpoint", "unauthorized"),
    403: ("endpoint", "forbidden"),
    404: ("endpoint", "route_missing"),
    405: ("endpoint", "method_not_allowed"),
    408: ("transient", "request_timeout"),
    410: ("endpoint", "route_gone"),
    413: ("payload", "payload_too_large"),
    415: ("endpoint", "unsupported_media_type"),
    422: ("payload", "unprocessable_payload"),
    425: ("transient", "too_early"),
    429: ("transient", "rate_limited"),
    500: ("transient", "internal_server_error"),
    501: ("endpoint", "not_implemented"),
    502: ("transient", "bad_gateway"),
    503: ("transient", "service_unavailable"),
    504: ("transient", "gateway_timeout"),
    505: ("endpoint", "http_version_not_supported"),
}


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """一次 HTTP 投递的可审计结构化结果。"""

    category: DeliveryCategory
    error_type: str
    status_code: int | None
    retry_after_seconds: int | None
    duration_ms: int
    safe_summary: str
    transport_phase: TransportPhase


def delivery_outcome_to_legacy(outcome: DeliveryOutcome) -> bool | None:
    """保持旧 QQ push 的精确三态语义，不按新类别重新解释。"""

    if outcome.status_code == 200:
        return True
    if outcome.status_code is not None and 400 <= outcome.status_code < 500:
        return False
    return None


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.monotonic() - started_at) * 1000))


def _normalized_now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _retry_after_seconds(
    value: object,
    *,
    now: datetime | None,
) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if re.fullmatch(r"[0-9]+", raw):
        normalized = raw.lstrip("0") or "0"
        cap_text = str(MAX_RETRY_AFTER_SECONDS)
        if len(normalized) > len(cap_text) or (
            len(normalized) == len(cap_text) and normalized > cap_text
        ):
            return MAX_RETRY_AFTER_SECONDS
        return int(normalized)
    try:
        retry_at = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at is None:
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    else:
        retry_at = retry_at.astimezone(timezone.utc)
    delta = max(0.0, (retry_at - _normalized_now(now)).total_seconds())
    return min(math.ceil(delta), MAX_RETRY_AFTER_SECONDS)


async def _read_limited_body(response: Any, *, limit_bytes: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit_bytes
    iterator = response.content.iter_chunked(min(_STREAM_CHUNK_BYTES, limit_bytes))
    async for chunk in iterator:
        if isinstance(chunk, str):
            raw = chunk.encode("utf-8")
        elif isinstance(chunk, (bytes, bytearray, memoryview)):
            raw = bytes(chunk)
        else:
            raise TypeError("响应正文 chunk 必须是 bytes")
        if not raw:
            continue
        chunks.append(raw[:remaining])
        remaining -= min(len(raw), remaining)
        if remaining <= 0:
            break
    return b"".join(chunks)


def _structured_error_type(body: bytes) -> str | None:
    if not body:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (
        UnicodeDecodeError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        RecursionError,
    ):
        return None
    if not isinstance(payload, Mapping):
        return None

    candidates: list[Mapping[str, Any]] = [payload]
    for container_key in ("error", "detail"):
        nested = payload.get(container_key)
        if isinstance(nested, Mapping):
            candidates.append(nested)
    for candidate in candidates:
        for key in ("error_type", "code"):
            raw = candidate.get(key)
            if not isinstance(raw, str):
                continue
            normalized = raw.strip().lower()
            if normalized in _STRUCTURED_ERROR_TYPES:
                return normalized
    return None


def _safe_qq_response_summary(body: bytes) -> str:
    """响应正文仅用于白名单错误码解析，不进入结果或日志。"""

    return _RESPONSE_BODY_OMITTED_SUMMARY if body else ""


def _classify_http(
    status_code: int,
    *,
    structured_error_type: str | None,
) -> tuple[DeliveryCategory, str]:
    if 200 <= status_code < 300:
        return "success", ""
    if 300 <= status_code < 400:
        return "endpoint", "unexpected_redirect"

    if status_code in {401, 403, 405, 501, 505}:
        return _NAMED_HTTP_FAILURES[status_code]
    if status_code in {408, 425, 429}:
        return _NAMED_HTTP_FAILURES[status_code]
    if 500 <= status_code < 600:
        return _NAMED_HTTP_FAILURES.get(
            status_code,
            ("transient", "http_server_error"),
        )

    if status_code in {404, 410}:
        if structured_error_type in _DESTINATION_ERROR_TYPES:
            return "destination", structured_error_type
        return _NAMED_HTTP_FAILURES[status_code]
    if status_code == 415:
        if structured_error_type in _PAYLOAD_CONTRACT_ERROR_TYPES:
            return "payload_contract", structured_error_type
        return _NAMED_HTTP_FAILURES[status_code]
    if status_code in {400, 422}:
        if structured_error_type in _PAYLOAD_CONTRACT_ERROR_TYPES:
            return "payload_contract", structured_error_type
        return _NAMED_HTTP_FAILURES[status_code]
    if status_code == 413:
        return _NAMED_HTTP_FAILURES[status_code]
    if 400 <= status_code < 500:
        if structured_error_type in _DESTINATION_ERROR_TYPES:
            return "destination", structured_error_type
        if structured_error_type in _PAYLOAD_CONTRACT_ERROR_TYPES:
            return "payload_contract", structured_error_type
        return "payload", "http_client_error"
    return "ambiguous", "unexpected_http_status"


def _connector_error_type(exc: aiohttp.ClientConnectorError) -> str:
    try:
        os_error = exc.os_error
    except AttributeError:
        return "connection_error"
    error_number = getattr(os_error, "errno", None)
    if isinstance(os_error, ConnectionRefusedError) or error_number == errno.ECONNREFUSED:
        return "connection_refused"
    if isinstance(os_error, socket.gaierror):
        return "dns_error"
    if isinstance(os_error, ConnectionResetError) or error_number == errno.ECONNRESET:
        return "connection_reset_before_send"
    return "connection_error"


def _ambiguous_transport_phase(phase_hint: TransportPhase) -> TransportPhase:
    return "read" if phase_hint in {"read", "response_received"} else "write"


def _classify_exception(
    exc: Exception,
    *,
    phase_hint: TransportPhase,
) -> tuple[DeliveryCategory, TransportPhase, str]:
    if _INVALID_URL_ERRORS and isinstance(exc, _INVALID_URL_ERRORS):
        return "endpoint", "connect", "invalid_url"
    if _TLS_FINGERPRINT_ERRORS and isinstance(exc, _TLS_FINGERPRINT_ERRORS):
        return "endpoint", "connect", "tls_fingerprint_mismatch"
    if _TLS_CERTIFICATE_ERRORS and isinstance(exc, _TLS_CERTIFICATE_ERRORS):
        return "endpoint", "connect", "tls_certificate_error"
    if _TLS_ERRORS and isinstance(exc, _TLS_ERRORS):
        return "endpoint", "connect", "tls_error"
    if _CONNECTION_TIMEOUT_ERRORS and isinstance(exc, _CONNECTION_TIMEOUT_ERRORS):
        return "transient", "connect", "connect_timeout"
    if isinstance(exc, aiohttp.ClientConnectorError):
        return "transient", "connect", _connector_error_type(exc)
    if isinstance(exc, ConnectionRefusedError):
        return "transient", "connect", "connection_refused"
    if isinstance(exc, socket.gaierror):
        return "transient", "connect", "dns_error"
    if _SOCKET_TIMEOUT_ERRORS and isinstance(exc, _SOCKET_TIMEOUT_ERRORS):
        return "ambiguous", "read", "read_timeout"
    if isinstance(exc, (aiohttp.ServerDisconnectedError, aiohttp.ClientPayloadError)):
        return "ambiguous", "read", "connection_reset"
    if isinstance(exc, aiohttp.ClientOSError):
        return "ambiguous", _ambiguous_transport_phase(phase_hint), "connection_reset"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        phase = _ambiguous_transport_phase(phase_hint)
        error_type = "read_timeout" if phase == "read" else "write_timeout"
        return "ambiguous", phase, error_type
    if isinstance(exc, ConnectionResetError):
        phase = _ambiguous_transport_phase(phase_hint)
        return "ambiguous", phase, "connection_reset"
    phase = _ambiguous_transport_phase(phase_hint)
    return "ambiguous", phase, "transport_error"


def _safe_exception_summary(error_type: str) -> str:
    return _SAFE_EXCEPTION_SUMMARIES.get(error_type, "出站传输失败")


def _log_outcome(outcome: DeliveryOutcome) -> None:
    if outcome.category == "success":
        logger.info(
            "QQ push transport result category=%s status=%s phase=%s duration_ms=%s",
            outcome.category,
            outcome.status_code,
            outcome.transport_phase,
            outcome.duration_ms,
        )
        return
    logger.warning(
        "QQ push transport result category=%s error_type=%s status=%s phase=%s "
        "duration_ms=%s retry_after_seconds=%s summary=%r",
        outcome.category,
        outcome.error_type,
        outcome.status_code,
        outcome.transport_phase,
        outcome.duration_ms,
        outcome.retry_after_seconds,
        outcome.safe_summary,
    )


async def deliver_qq_push_with_session(
    session: aiohttp.ClientSession,
    *,
    push_url: str,
    push_token: str,
    target_type: str,
    target_id: str,
    message: str,
    timeout_seconds: float,
    response_body_limit_bytes: int = DEFAULT_RESPONSE_BODY_LIMIT_BYTES,
    now: datetime | None = None,
) -> DeliveryOutcome:
    """发送一次 QQ push，并返回不含请求敏感数据的结构化结果。"""

    token = resolve_qq_push_token({"NANOBOT_PUSH_TOKEN": push_token})
    if (
        not isinstance(response_body_limit_bytes, int)
        or isinstance(response_body_limit_bytes, bool)
        or response_body_limit_bytes <= 0
    ):
        raise ValueError("response_body_limit_bytes 必须是正整数")
    try:
        normalized_timeout = float(timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds 必须是正数") from exc
    if not math.isfinite(normalized_timeout) or normalized_timeout <= 0:
        raise ValueError("timeout_seconds 必须是正数")

    started_at = time.monotonic()
    phase: TransportPhase = "write"
    try:
        async with session.post(
            push_url,
            headers={"Authorization": f"Bearer {token}"},
            json={
                "target_type": target_type,
                "target_id": target_id,
                "message": message,
            },
            timeout=aiohttp.ClientTimeout(total=normalized_timeout),
            allow_redirects=False,
        ) as response:
            phase = "response_received"
            raw_status_code = int(response.status)
            status_code = (
                raw_status_code if 100 <= raw_status_code <= 599 else None
            )
            if status_code is not None and 200 <= status_code < 300:
                outcome = DeliveryOutcome(
                    category="success",
                    error_type="",
                    status_code=status_code,
                    retry_after_seconds=None,
                    duration_ms=_elapsed_ms(started_at),
                    safe_summary="",
                    transport_phase="response_received",
                )
                _log_outcome(outcome)
                return outcome

            phase = "read"
            body = await _read_limited_body(
                response,
                limit_bytes=response_body_limit_bytes,
            )
            phase = "response_received"
            if status_code is None:
                category: DeliveryCategory = "ambiguous"
                error_type = "invalid_http_status"
            else:
                structured_error = _structured_error_type(body)
                category, error_type = _classify_http(
                    status_code,
                    structured_error_type=structured_error,
                )
            retry_after = None
            if status_code == 429:
                retry_after = _retry_after_seconds(
                    response.headers.get("Retry-After"),
                    now=now,
                )
            summary = _safe_qq_response_summary(body)
            outcome = DeliveryOutcome(
                category=category,
                error_type=error_type,
                status_code=status_code,
                retry_after_seconds=retry_after,
                duration_ms=_elapsed_ms(started_at),
                safe_summary=summary,
                transport_phase="response_received",
            )
            _log_outcome(outcome)
            return outcome
    except Exception as exc:
        category, transport_phase, error_type = _classify_exception(
            exc,
            phase_hint=phase,
        )
        outcome = DeliveryOutcome(
            category=category,
            error_type=error_type,
            status_code=None,
            retry_after_seconds=None,
            duration_ms=_elapsed_ms(started_at),
            safe_summary=_safe_exception_summary(error_type),
            transport_phase=transport_phase,
        )
        _log_outcome(outcome)
        return outcome

"""client_meta 边界解析与校验。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_PLATFORM_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_TRACE_STRING_KEYS = ("request_id", "correlation_id", "source")
_TRACE_VALUE_MAX_CHARS = 128


class ClientMetaValidationError(ValueError):
    """client_meta 字段不符合边界层约束。"""


def _normalize_platform(value: Any) -> str:
    if value is None or value == "":
        return "qq"
    if not isinstance(value, str):
        raise ClientMetaValidationError("platform must be a string")
    platform = value.strip().lower() or "qq"
    if not _PLATFORM_RE.fullmatch(platform):
        raise ClientMetaValidationError("platform must match ^[a-z][a-z0-9_-]{0,31}$")
    return platform


def _normalize_chat_type(value: Any, *, expected_chat_type: str) -> str:
    expected = str(expected_chat_type or "").strip().lower()
    if expected not in {"private", "group"}:
        raise ClientMetaValidationError("expected_chat_type must be private or group")
    if value is None or value == "":
        return expected
    if not isinstance(value, str):
        raise ClientMetaValidationError("chat_type must be a string")
    actual = value.strip().lower()
    if actual != expected:
        raise ClientMetaValidationError(f"chat_type must be {expected}")
    return expected


def _normalize_trace(value: Any) -> dict[str, str]:
    if value in (None, ""):
        return {}
    if not isinstance(value, Mapping):
        raise ClientMetaValidationError("trace must be an object")
    trace: dict[str, str] = {}
    for key in _TRACE_STRING_KEYS:
        if key not in value or value[key] in (None, ""):
            continue
        if not isinstance(value[key], str):
            raise ClientMetaValidationError(f"trace.{key} must be a string")
        normalized = value[key].strip()
        if normalized:
            trace[key] = normalized[:_TRACE_VALUE_MAX_CHARS]
    return trace


def normalize_client_meta(
    client_meta: Mapping[str, Any] | None,
    *,
    expected_chat_type: str,
) -> dict[str, Any]:
    raw = dict(client_meta) if isinstance(client_meta, Mapping) else {}
    normalized = dict(raw)
    normalized["platform"] = _normalize_platform(raw.get("platform"))
    normalized["chat_type"] = _normalize_chat_type(
        raw.get("chat_type"),
        expected_chat_type=expected_chat_type,
    )
    trace = _normalize_trace(raw.get("trace"))
    if trace:
        normalized["trace"] = trace
    else:
        normalized.pop("trace", None)
    return normalized


def client_meta_request_id(client_meta: Mapping[str, Any] | None) -> str:
    if not isinstance(client_meta, Mapping):
        return ""
    trace = client_meta.get("trace")
    if not isinstance(trace, Mapping):
        return ""
    return str(trace.get("request_id") or "")

"""外部响应与异常日志共用的有界脱敏摘要。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


DEFAULT_SAFE_SUMMARY_MAX_CHARS = 512
_TRUNCATED_SUFFIX = "...[TRUNCATED]"
_SENSITIVE_KEY_PARTS = frozenset({
    "accesskey",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "setcookie",
    "signature",
    "token",
})
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_AUTH_SCHEME_RE = re.compile(r"\b(Bearer|Basic)\s+[^\s,;]+", re.IGNORECASE)
_SENSITIVE_HEADER_RE = re.compile(
    r"(?im)^(\s*(?:authorization|proxy-authorization|cookie|set-cookie|"
    r"x-api-key)\s*:\s*).*$"
)
_SENSITIVE_PAIR_RE = re.compile(
    r"(?i)([\"']?\b[a-z0-9_.-]*(?:access[_-]?key|api[_-]?key|authorization|cookie|"
    r"credential|password|secret|set[_-]?cookie|signature|token)"
    r"[a-z0-9_.-]*\b[\"']?\s*[=:]\s*)"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;&}\]]+)"
)


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _is_sensitive_key(value: object) -> bool:
    normalized = _normalized_key(value)
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _redact_structure(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]" if _is_sensitive_key(key) else _redact_structure(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_redact_structure(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_url(match: re.Match[str]) -> str:
    return safe_url_for_logging(match.group(0), max_chars=len(match.group(0)) + 32)


def safe_url_for_logging(value: object, *, max_chars: int = 2048) -> str:
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars <= 0:
        raise ValueError("max_chars 必须是正整数")
    raw_url = str(value or "").replace("\x00", "").strip()
    if not raw_url:
        return ""
    if "\r" in raw_url or "\n" in raw_url:
        return "[URL_REDACTED]"[:max_chars]
    try:
        parts = urlsplit(raw_url)
        if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
            return "[URL_REDACTED]"[:max_chars]
        netloc = parts.netloc
        if "@" in netloc:
            netloc = f"[REDACTED]@{netloc.rsplit('@', 1)[1]}"
        query = urlencode(
            [
                (key, "[REDACTED]" if _is_sensitive_key(key) else item)
                for key, item in parse_qsl(parts.query, keep_blank_values=True)
            ],
            doseq=True,
        )
        safe_url = urlunsplit((parts.scheme, netloc, parts.path, query, ""))
    except (TypeError, ValueError):
        safe_url = "[URL_REDACTED]"
    return safe_url if len(safe_url) <= max_chars else safe_url[:max_chars]


def _redact_text(value: str) -> str:
    text = value.replace("\x00", "")
    text = _SENSITIVE_HEADER_RE.sub(r"\1[REDACTED]", text)
    text = _AUTH_SCHEME_RE.sub(r"\1 [REDACTED]", text)
    text = _URL_RE.sub(_redact_url, text)
    return _SENSITIVE_PAIR_RE.sub(r'\1"[REDACTED]"', text)


def _as_redacted_text(value: object) -> str:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError, RecursionError):
            return _redact_text(value)
        if isinstance(parsed, (dict, list)):
            try:
                return json.dumps(
                    _redact_structure(parsed),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                )
            except (RecursionError, ValueError):
                return _redact_text(value)
        return _redact_text(value)
    if isinstance(value, (Mapping, list, tuple)):
        try:
            return json.dumps(
                _redact_structure(value),
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
        except (RecursionError, ValueError):
            return _redact_text(str(value))
    return _redact_text(str(value))


def safe_response_summary(
    value: object,
    *,
    max_chars: int = DEFAULT_SAFE_SUMMARY_MAX_CHARS,
) -> str:
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars <= 0:
        raise ValueError("max_chars 必须是正整数")
    text = _as_redacted_text(value)
    if len(text) <= max_chars:
        return text
    if max_chars <= len(_TRUNCATED_SUFFIX):
        return _TRUNCATED_SUFFIX[:max_chars]
    return text[: max_chars - len(_TRUNCATED_SUFFIX)] + _TRUNCATED_SUFFIX

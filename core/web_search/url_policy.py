"""网页搜索与研究草稿共用的严格 HTTP(S) URL 规范化策略。"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_CONTROL_OR_SPACE = re.compile(r"[\x00-\x20\x7f]")
_TRACKING_QUERY_KEYS = frozenset({
    "fbclid",
    "gclid",
    "dclid",
    "msclkid",
    "yclid",
    "mc_cid",
    "mc_eid",
})


def canonicalize_http_url(raw: object) -> str:
    """只接受无 userinfo、无控制字符的绝对 HTTP(S) URL。"""

    value = str(raw or "").strip().rstrip(".,;:!?，。；：！？、")
    if (
        not value
        or value.startswith("//")
        or value.lower().startswith("www.")
        or _CONTROL_OR_SPACE.search(value)
        or "\\" in value
    ):
        return ""
    try:
        parts = urlsplit(value)
    except (TypeError, ValueError):
        return ""
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not parts.netloc:
        return ""
    if parts.username is not None or parts.password is not None:
        return ""
    try:
        hostname = str(parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return ""
    if not hostname or "%" in hostname or _CONTROL_OR_SPACE.search(hostname):
        return ""

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None

    if address is not None:
        if not address.is_global:
            return ""
        host_text = f"[{address.compressed}]" if address.version == 6 else address.compressed
    else:
        if hostname.isdigit():
            return ""
        try:
            host_text = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            return ""
        labels = host_text.split(".")
        if (
            len(labels) < 2
            or any(not label or len(label) > 63 for label in labels)
            or host_text == "localhost"
            or host_text.endswith((".localhost", ".local", ".internal", ".home", ".lan"))
        ):
            return ""
    if port is not None and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    ):
        host_text = f"{host_text}:{port}"

    try:
        query_items = [
            (key, item_value)
            for key, item_value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in _TRACKING_QUERY_KEYS
        ]
    except ValueError:
        return ""
    path = parts.path.rstrip("/")
    query = urlencode(sorted(query_items), doseq=True)
    return urlunsplit((scheme, host_text, path, query, ""))

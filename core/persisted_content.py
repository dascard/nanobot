"""消息历史的二进制、宿主路径和短期凭据清洗。"""

from __future__ import annotations

import re


_INLINE_DATA_RE = re.compile(
    r"(?i)(?:data:[^\s,;]{1,128}(?:;[^\s,]{1,128})*;base64,|base64://)"
    r"[A-Za-z0-9+/=_-]{8,}"
)
_FILE_URI_RE = re.compile(r"(?i)file://[^\s\]\[<>()\"']+")
_WINDOWS_HOST_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/](?:[^\s\]\[<>()\"']+))"
)
_POSIX_HOST_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9:])/(?:home|mnt|srv|tmp|var|app|root|opt|run)/"
    r"[^\s\]\[<>()\"']*"
)
_SHORT_ASSET_TOKEN_RE = re.compile(
    r"\[asset_download:[A-Za-z0-9_.-]{32,8192}\]"
)


def sanitize_persisted_content(value: object) -> str:
    """保留稳定 Artifact 引用，移除不可重建或含凭据的内容。"""

    text = str(value or "")
    text = _INLINE_DATA_RE.sub("[内联二进制已移除]", text)
    text = _FILE_URI_RE.sub("[宿主文件路径已移除]", text)
    text = _WINDOWS_HOST_PATH_RE.sub("[宿主文件路径已移除]", text)
    text = _POSIX_HOST_PATH_RE.sub("[宿主文件路径已移除]", text)
    return _SHORT_ASSET_TOKEN_RE.sub("[资产短期凭据已移除]", text)


__all__ = ["sanitize_persisted_content"]

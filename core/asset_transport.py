"""把短期资产凭据仅在最终传输出口展开为下载 URL。"""

from __future__ import annotations

import os
import re
from urllib.parse import urlencode, urlsplit

from core.asset_tokens import AssetTokenError, AssetTokenSigner, signer_from_settings


ASSET_DOWNLOAD_REF_PATTERN = re.compile(
    r"\[asset_download:([A-Za-z0-9_.-]{32,8192})\]"
)
_ASSET_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_UNAVAILABLE_TEXT = "（资产下载链接暂不可用）"


def build_asset_reply_token(transport_token: str) -> str:
    token = str(transport_token or "")
    if len(token) > 8192 or not _ASSET_TOKEN_RE.fullmatch(token):
        raise AssetTokenError("资产 Token 无效")
    return f"[asset_download:{token}]"


def _public_base_url(value: str | None = None) -> str:
    base_url = str(
        os.environ.get("NANOBOT_PUBLIC_BASE_URL", "")
        if value is None
        else value
    ).strip().rstrip("/")
    try:
        parsed = urlsplit(base_url)
    except ValueError:
        return ""
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return ""
    return base_url


def public_asset_download_url(
    transport_token: str,
    *,
    signer: AssetTokenSigner | None = None,
    base_url: str | None = None,
) -> str:
    public_base = _public_base_url(base_url)
    if not public_base:
        return ""
    try:
        verifier = signer or signer_from_settings()
        claims = verifier.verify(transport_token)
    except AssetTokenError:
        return ""
    query = urlencode({
        "token": transport_token,
        "recipient_type": claims.recipient_type,
        "recipient_id": claims.recipient_id,
    })
    return (
        f"{public_base}/api/v1/assets/{claims.asset_sha256}/download?{query}"
    )


def expand_asset_download_refs_in_content(
    content: str,
    *,
    signer: AssetTokenSigner | None = None,
    base_url: str | None = None,
) -> str:
    text = str(content or "")

    def replace(match: re.Match[str]) -> str:
        url = public_asset_download_url(
            match.group(1),
            signer=signer,
            base_url=base_url,
        )
        return url or _UNAVAILABLE_TEXT

    return ASSET_DOWNLOAD_REF_PATTERN.sub(replace, text)

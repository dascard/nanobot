"""把短期资产凭据仅在最终传输出口展开为下载 URL。"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlencode, urlsplit

from core.asset_tokens import AssetTokenError, AssetTokenSigner, signer_from_settings


ASSET_DOWNLOAD_REF_PATTERN = re.compile(
    r"\[asset_download:([A-Za-z0-9_.-]{32,8192})\]"
)
ARTIFACT_REF_PATTERN = re.compile(r"\[artifact:(art_[A-Za-z0-9_]{4,60})\]")
_ASSET_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_UNAVAILABLE_TEXT = "（资产下载链接暂不可用）"


def build_asset_reply_token(transport_token: str) -> str:
    token = str(transport_token or "")
    if len(token) > 8192 or not _ASSET_TOKEN_RE.fullmatch(token):
        raise AssetTokenError("资产 Token 无效")
    return f"[asset_download:{token}]"


def build_artifact_reply_token(artifact_id: str) -> str:
    normalized = str(artifact_id or "").strip()
    if not ARTIFACT_REF_PATTERN.fullmatch(f"[artifact:{normalized}]"):
        raise AssetTokenError("Artifact 标识无效")
    return f"[artifact:{normalized}]"


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
    if claims.artifact_id:
        return (
            f"{public_base}/api/v1/assets/artifacts/"
            f"{claims.artifact_id}/download?{query}"
        )
    return f"{public_base}/api/v1/assets/{claims.asset_sha256}/download?{query}"


def _artifact_transport(
    artifact_id: str,
    *,
    db: Any,
    signer: AssetTokenSigner | None,
    base_url: str | None,
) -> tuple[str, str]:
    from core.artifact_port import SqlAlchemyArtifactPort

    port = SqlAlchemyArtifactPort.for_metadata(db)
    artifact, owner = port.resolve_trusted_sync(artifact_id)
    resolved_signer = signer or signer_from_settings(db)
    token = resolved_signer.issue(
        artifact.sha256,
        artifact_id=artifact.artifact_id,
        recipient_type="session",
        recipient_id=owner.canonical_id,
    )
    return (
        public_asset_download_url(
            token,
            signer=resolved_signer,
            base_url=base_url,
        ),
        artifact.media_type,
    )


def artifact_preview_url(
    artifact_id: str,
    *,
    db: Any,
    signer: AssetTokenSigner | None = None,
) -> str:
    """为已授权管理界面生成相对、短期且 owner-bound 的预览地址。"""

    from core.artifact_port import SqlAlchemyArtifactPort

    artifact, owner = SqlAlchemyArtifactPort.for_metadata(
        db
    ).resolve_trusted_sync(artifact_id)
    resolved_signer = signer or signer_from_settings(db)
    token = resolved_signer.issue(
        artifact.sha256,
        artifact_id=artifact.artifact_id,
        recipient_type="session",
        recipient_id=owner.canonical_id,
    )
    query = urlencode({
        "token": token,
        "recipient_type": "session",
        "recipient_id": owner.canonical_id,
    })
    return (
        f"/api/v1/assets/artifacts/{artifact.artifact_id}/preview?{query}"
    )


def expand_artifact_refs_in_content(
    content: str,
    *,
    db: Any | None = None,
    signer: AssetTokenSigner | None = None,
    base_url: str | None = None,
    render_images: bool = False,
) -> str:
    """仅在最终传输出口把稳定 Artifact 引用换成短期凭据。"""

    text = str(content or "")
    if ARTIFACT_REF_PATTERN.search(text) is None:
        return text
    own_db = db is None
    if own_db:
        from core.database import SessionLocal

        db = SessionLocal()
    try:
        def replace(match: re.Match[str]) -> str:
            try:
                url, media_type = _artifact_transport(
                    match.group(1),
                    db=db,
                    signer=signer,
                    base_url=base_url,
                )
            except Exception:
                return _UNAVAILABLE_TEXT
            if not url:
                return _UNAVAILABLE_TEXT
            if render_images and media_type.startswith("image/"):
                escaped = (
                    url.replace("&", "&amp;")
                    .replace("[", "&#91;")
                    .replace("]", "&#93;")
                )
                return f"[CQ:image,file={escaped}]"
            return url

        return ARTIFACT_REF_PATTERN.sub(replace, text)
    finally:
        if own_db:
            db.close()


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

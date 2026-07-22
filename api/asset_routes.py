"""受信网关资产上传与短期签名下载。"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import AsyncIterable, AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from api.common_auth import verify_token
from core.asset_tokens import AssetTokenError, signer_from_settings
from core.asset_transport import build_asset_reply_token
from core.database import Asset, Workspace, WorkspaceAsset, get_db
from core.sandbox.asset_service import AssetService
from core.sandbox.asset_store import safe_media_type
from core.sandbox.client import AsyncSandboxdAssetClient
from core.sandbox.contracts import (
    PublishedAsset,
    SandboxErrorCode,
    SandboxServiceError,
    success_result,
)
from core.sandbox.paths import validate_relative_path, validate_sha256
from core.sandbox.tool_service import (
    authorize_sandbox_access,
    resolve_sandbox_setting,
    workspace_policy_from_settings,
)
from core.sandbox.workspace_service import WorkspaceService


router = APIRouter(tags=["assets"])


async def _bounded_upload_stream(
    content: AsyncIterable[bytes],
    *,
    max_bytes: int,
) -> AsyncIterator[bytes]:
    """在 Nanobot 转发层也限制 chunked 上传，尽早停止超限流量。"""

    received = 0
    async for chunk in content:
        received += len(chunk)
        if received > int(max_bytes):
            raise SandboxServiceError(
                SandboxErrorCode.ASSET_TOO_LARGE,
                "资产超过允许的单文件大小上限",
            )
        yield chunk


def _safe_logical_name(value: str) -> str:
    normalized = "/".join(validate_relative_path(str(value or "")))
    if len(normalized.encode("utf-8")) > 512:
        raise SandboxServiceError(
            SandboxErrorCode.INVALID_PATH,
            "资产逻辑文件名无效",
        )
    return normalized


def _runtime_context(
    *,
    platform: str,
    chat_type: str,
    user_id: str,
    group_id: str,
    session_id: str,
) -> dict[str, object]:
    return {
        "platform": platform,
        "chat_type": chat_type,
        "user_id": user_id,
        "group_id": group_id,
        "session_id": session_id,
    }


def _asset_client(db: Session) -> AsyncSandboxdAssetClient:
    return AsyncSandboxdAssetClient(
        socket_path=str(resolve_sandbox_setting(db, "sandbox.sandboxd_socket")),
        token_file=str(resolve_sandbox_setting(db, "sandbox.sandboxd_token_file")),
        timeout_seconds=float(resolve_sandbox_setting(
            db,
            "sandbox.asset_transfer_timeout_seconds",
        )),
    )


def _upload_error(error: SandboxServiceError) -> HTTPException:
    if error.code in {
        SandboxErrorCode.AUTHORIZATION_FAILED,
        SandboxErrorCode.SANDBOX_NOT_ENABLED,
        SandboxErrorCode.ASSET_NOT_AUTHORIZED,
    }:
        status_code = 403
    elif error.code is SandboxErrorCode.ASSET_TOO_LARGE:
        status_code = 413
    elif error.code in {
        SandboxErrorCode.DISK_PRESSURE,
        SandboxErrorCode.WORKSPACE_QUOTA_EXCEEDED,
    }:
        status_code = 507
    elif error.code is SandboxErrorCode.RUNTIME_UNAVAILABLE:
        status_code = 503
    else:
        status_code = 400
    return HTTPException(status_code=status_code, detail=error.to_result())


@router.post("/assets/upload")
async def upload_asset(
    request: Request,
    user_id: Annotated[str, Query(min_length=1, max_length=255)],
    logical_name: Annotated[str, Query(min_length=1, max_length=512)],
    session_id: Annotated[str, Query(min_length=1, max_length=255)],
    platform: Annotated[str, Query(min_length=1, max_length=32)] = "qq",
    chat_type: Annotated[Literal["private", "group"], Query()] = "private",
    group_id: Annotated[str, Query(max_length=255)] = "",
    media_type: Annotated[str, Query(max_length=255)] = "",
    db: Session = Depends(get_db),
    _auth=Depends(verify_token),
):
    try:
        context = _runtime_context(
            platform=platform,
            chat_type=chat_type,
            user_id=user_id,
            group_id=group_id,
            session_id=session_id,
        )
        access, _runtime = authorize_sandbox_access(
            db,
            "asset_import",
            context,
        )
        normalized_name = _safe_logical_name(logical_name)
        signer = signer_from_settings(db)
        workspace_service = WorkspaceService(
            db,
            policy=workspace_policy_from_settings(db),
        )
        workspace = db.get(Workspace, access.workspace_id)
        if workspace is None or workspace.status != "active":
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "当前会话没有可用的 Workspace",
            )
        max_asset_bytes = int(resolve_sandbox_setting(db, "sandbox.asset_max_bytes"))
        raw_length = str(request.headers.get("content-length") or "").strip()
        try:
            content_length = int(raw_length) if raw_length else None
        except ValueError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "资产上传长度无效",
            ) from exc
        if content_length is not None and (
            content_length < 0 or content_length > max_asset_bytes
        ):
            raise SandboxServiceError(
                SandboxErrorCode.ASSET_TOO_LARGE,
                "资产超过允许的单文件大小上限",
            )
        resolved_media_type = safe_media_type(
            media_type or request.headers.get("content-type") or "application/octet-stream",
        )
        client = _asset_client(db)
        try:
            response = await client.upload_asset(
                workspace_id=workspace.id,
                media_type=resolved_media_type,
                content=_bounded_upload_stream(
                    request.stream(),
                    max_bytes=max_asset_bytes,
                ),
                content_length=content_length,
                request_id=f"assetup_{secrets.token_hex(16)}",
            )
        finally:
            await client.close()
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        try:
            published = PublishedAsset(
                sha256=str(data.get("sha256") or ""),
                size_bytes=int(data.get("size_bytes")),
                media_type=str(data.get("media_type") or resolved_media_type),
                storage_key=str(data.get("storage_key") or ""),
            )
        except (TypeError, ValueError) as exc:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 控制面返回了无效资产元数据",
                retryable=True,
                stop=False,
            ) from exc
        asset_service = AssetService(
            db,
            workspace_service=workspace_service,
            max_asset_bytes=max_asset_bytes,
        )
        asset, link = asset_service.register_published_for_workspace(
            workspace.id,
            published,
            logical_name=normalized_name,
        )
        transport_token = signer.issue(
            asset.sha256,
            recipient_type="session",
            recipient_id=str(access.identity.chat_stream_id),
        )
        claims = signer.verify(transport_token)
        reply_token = build_asset_reply_token(transport_token)
        db.commit()
        return success_result(
            "资产上传并授权完成",
            data={
                "source_ref": f"asset://sha256/{asset.sha256}",
                "logical_name": link.logical_name,
                "size_bytes": int(asset.size_bytes),
                "media_type": asset.media_type,
                "transport_token": transport_token,
                "reply_token": reply_token,
                "recipient_type": claims.recipient_type,
                "recipient_id": claims.recipient_id,
                "expires_at": claims.expires_at,
            },
            artifacts=[{
                "type": "asset",
                "ref": f"asset://sha256/{asset.sha256}",
                "logical_name": link.logical_name,
                "size_bytes": int(asset.size_bytes),
                "transport_token": transport_token,
                "reply_token": reply_token,
            }],
        )
    except AssetTokenError as exc:
        db.rollback()
        raise HTTPException(503, "资产下载凭据未安全配置") from exc
    except SandboxServiceError as exc:
        db.rollback()
        raise _upload_error(exc) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(503, "资产上传暂时不可用") from exc


@router.get("/assets/{sha256}/download")
async def download_asset(
    sha256: str,
    token: Annotated[str, Query(min_length=1, max_length=8192)],
    recipient_type: Annotated[Literal["session"], Query()],
    recipient_id: Annotated[str, Query(min_length=1, max_length=512)],
    range_header: Annotated[str, Header(alias="Range", max_length=128)] = "",
    db: Session = Depends(get_db),
):
    try:
        digest = validate_sha256(sha256)
        signer = signer_from_settings(db)
        claims = signer.verify(
            token,
            recipient_type=recipient_type,
            recipient_id=recipient_id,
        )
        if not hmac.compare_digest(claims.asset_sha256, digest):
            raise AssetTokenError("资产 Token 与资源不匹配")
        asset = db.get(Asset, digest)
        if asset is None:
            raise AssetTokenError("资产不存在")
        from core.sandbox.access_policy import SandboxAccessPolicy

        decision = SandboxAccessPolicy(db).evaluate(
            "asset_import",
            platform=recipient_id.split(":", 1)[0],
            chat_type="private",
            session_id=recipient_id,
        )
        if not decision.allowed:
            raise AssetTokenError("资产授权已失效")
        authorized = (
            db.query(WorkspaceAsset.id)
            .filter(
                WorkspaceAsset.workspace_id == decision.workspace_id,
                WorkspaceAsset.asset_sha256 == digest,
            )
            .first()
        )
        if authorized is None:
            raise AssetTokenError("资产授权已失效")
    except (AssetTokenError, SandboxServiceError):
        raise HTTPException(404, "资产不存在或下载凭据无效") from None

    client = _asset_client(db)
    try:
        upstream = await client.open_asset(digest, range_header=range_header)
    except SandboxServiceError as exc:
        await client.close()
        if exc.code in {SandboxErrorCode.ASSET_NOT_FOUND, SandboxErrorCode.ASSET_NOT_AUTHORIZED}:
            raise HTTPException(404, "资产不存在或下载凭据无效") from exc
        raise HTTPException(503, "资产下载暂时不可用") from exc

    async def body_iterator():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.close()

    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "Content-Disposition": f'attachment; filename="{digest}"',
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    }
    for header_name in ("content-length", "content-range"):
        if upstream.headers.get(header_name):
            headers[header_name.title()] = upstream.headers[header_name]
    return StreamingResponse(
        body_iterator(),
        status_code=upstream.status_code,
        headers=headers,
        media_type=asset.media_type,
    )

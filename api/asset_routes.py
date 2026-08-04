"""受信网关资产上传与短期签名下载。"""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterable, AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from api.common_auth import verify_token
from core.agent_runtime import (
    RuntimeArtifactResolveRequest,
    RuntimeOwnerType,
    RuntimePrincipal,
)
from core.artifact_port import (
    ArtifactStreamPublishRequest,
    SqlAlchemyArtifactPort,
)
from core.asset_tokens import AssetTokenError, signer_from_settings
from core.asset_transport import build_artifact_reply_token
from core.database import Workspace, get_db
from core.sandbox.asset_store import safe_media_type
from core.sandbox.client import AsyncSandboxdAssetClient
from core.sandbox.contracts import (
    SandboxErrorCode,
    SandboxServiceError,
    success_result,
)
from core.sandbox.paths import validate_relative_path, validate_sha256
from core.sandbox.tool_service import (
    authorize_sandbox_access,
    resolve_sandbox_setting,
)


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


def _artifact_port(
    db: Session,
    *,
    metadata_only: bool = False,
) -> SqlAlchemyArtifactPort:
    if metadata_only:
        return SqlAlchemyArtifactPort.for_metadata(db)
    return SqlAlchemyArtifactPort.from_settings(db)


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


def _owner_from_transport_recipient(recipient_id: str) -> RuntimePrincipal:
    parts = str(recipient_id or "").split(":", 2)
    if len(parts) != 3:
        raise AssetTokenError("Artifact Token owner 无效")
    platform, owner_type, owner_id = parts
    try:
        return RuntimePrincipal(
            platform=platform,
            owner_type=RuntimeOwnerType(owner_type),
            owner_id=owner_id,
        )
    except (TypeError, ValueError) as exc:
        raise AssetTokenError("Artifact Token owner 无效") from exc


async def _stream_asset_response(
    *,
    db: Session,
    sha256: str,
    media_type: str,
    range_header: str,
    filename: str,
    disposition: str,
) -> StreamingResponse:
    client = _asset_client(db)
    try:
        upstream = await client.open_asset(sha256, range_header=range_header)
    except SandboxServiceError as exc:
        await client.close()
        if exc.code in {
            SandboxErrorCode.ASSET_NOT_FOUND,
            SandboxErrorCode.ASSET_NOT_AUTHORIZED,
        }:
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
        "Content-Disposition": f'{disposition}; filename="{filename}"',
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
        media_type=media_type,
    )


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
        owner = RuntimePrincipal(
            platform=str(workspace.platform),
            owner_type=RuntimeOwnerType(str(workspace.owner_type)),
            owner_id=str(workspace.owner_id),
        )
        port = _artifact_port(db)
        try:
            artifact = await port.publish_stream(ArtifactStreamPublishRequest(
                owner=owner,
                workspace_id=workspace.id,
                virtual_path=normalized_name,
                media_type=resolved_media_type,
                content=_bounded_upload_stream(
                    request.stream(),
                    max_bytes=max_asset_bytes,
                ),
                content_length=content_length,
                source_kind="upload",
                overwrite=True,
            ))
        finally:
            port.close()
        reply_token = build_artifact_reply_token(artifact.artifact_id)
        db.commit()
        return success_result(
            "资产上传并授权完成",
            data={
                "source_ref": artifact.uri,
                "content_ref": f"asset://sha256/{artifact.sha256}",
                "artifact_id": artifact.artifact_id,
                "logical_name": normalized_name,
                "version": artifact.version,
                "size_bytes": artifact.size_bytes,
                "media_type": artifact.media_type,
                "reply_token": reply_token,
            },
            artifacts=[{
                "type": "artifact",
                "ref": artifact.uri,
                "artifact_id": artifact.artifact_id,
                "logical_name": normalized_name,
                "version": artifact.version,
                "size_bytes": artifact.size_bytes,
                "reply_token": reply_token,
            }],
        )
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
        if claims.artifact_id:
            raise AssetTokenError("Artifact Token 必须使用稳定下载端点")
        from core.sandbox.access_policy import SandboxAccessPolicy

        decision = SandboxAccessPolicy(db).evaluate(
            "asset_import",
            platform=recipient_id.split(":", 1)[0],
            chat_type="private",
            session_id=recipient_id,
        )
        if not decision.allowed:
            raise AssetTokenError("资产授权已失效")
        workspace = db.get(Workspace, decision.workspace_id)
        if workspace is None or workspace.status != "active":
            raise AssetTokenError("资产授权已失效")
        artifact = _artifact_port(
            db,
            metadata_only=True,
        ).resolve_sha_for_workspace_sync(
            workspace_id=str(workspace.id),
            sha256=digest,
        )
    except (AssetTokenError, PermissionError, SandboxServiceError):
        raise HTTPException(404, "资产不存在或下载凭据无效") from None

    return await _stream_asset_response(
        db=db,
        sha256=digest,
        media_type=artifact.media_type,
        range_header=range_header,
        filename=digest,
        disposition="attachment",
    )


async def _authorized_artifact_response(
    *,
    artifact_id: str,
    token: str,
    recipient_type: str,
    recipient_id: str,
    range_header: str,
    disposition: str,
    db: Session,
) -> StreamingResponse:
    try:
        signer = signer_from_settings(db)
        claims = signer.verify(
            token,
            recipient_type=recipient_type,
            recipient_id=recipient_id,
        )
        if not claims.artifact_id or not hmac.compare_digest(
            claims.artifact_id,
            str(artifact_id or ""),
        ):
            raise AssetTokenError("Artifact Token 与资源不匹配")
        owner = _owner_from_transport_recipient(claims.recipient_id)
        port = _artifact_port(db, metadata_only=True)
        artifact = port.resolve_sync(RuntimeArtifactResolveRequest(
            artifact_id=artifact_id,
            owner=owner,
        ))
        if not hmac.compare_digest(claims.asset_sha256, artifact.sha256):
            raise AssetTokenError("Artifact Token 与内容不匹配")
    except (AssetTokenError, PermissionError, SandboxServiceError):
        raise HTTPException(404, "资产不存在或下载凭据无效") from None
    return await _stream_asset_response(
        db=db,
        sha256=artifact.sha256,
        media_type=artifact.media_type,
        range_header=range_header,
        filename=artifact.artifact_id,
        disposition=disposition,
    )


@router.get("/assets/artifacts/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    token: Annotated[str, Query(min_length=1, max_length=8192)],
    recipient_type: Annotated[Literal["session"], Query()],
    recipient_id: Annotated[str, Query(min_length=1, max_length=512)],
    range_header: Annotated[str, Header(alias="Range", max_length=128)] = "",
    db: Session = Depends(get_db),
):
    return await _authorized_artifact_response(
        artifact_id=artifact_id,
        token=token,
        recipient_type=recipient_type,
        recipient_id=recipient_id,
        range_header=range_header,
        disposition="attachment",
        db=db,
    )


@router.get("/assets/artifacts/{artifact_id}/preview")
async def preview_artifact(
    artifact_id: str,
    token: Annotated[str, Query(min_length=1, max_length=8192)],
    recipient_type: Annotated[Literal["session"], Query()],
    recipient_id: Annotated[str, Query(min_length=1, max_length=512)],
    range_header: Annotated[str, Header(alias="Range", max_length=128)] = "",
    db: Session = Depends(get_db),
):
    return await _authorized_artifact_response(
        artifact_id=artifact_id,
        token=token,
        recipient_type=recipient_type,
        recipient_id=recipient_id,
        range_header=range_header,
        disposition="inline",
        db=db,
    )

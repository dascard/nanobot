"""基于现有 Workspace/Asset Store 的生产 ArtifactPort。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import AsyncIterable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from core.agent_runtime.contracts import RuntimeArtifactRef, RuntimePrincipal
from core.agent_runtime.service_ports import (
    ArtifactPort,
    RuntimeArtifactContent,
    RuntimeArtifactPublishRequest,
    RuntimeArtifactReadRequest,
    RuntimeArtifactResolveRequest,
)
from core.sandbox.asset_service import AssetService
from core.sandbox.backend import SandboxBackend
from core.sandbox.client import AsyncSandboxdAssetClient, HttpSandboxdBackend
from core.sandbox.contracts import (
    PublishedAsset,
    SandboxErrorCode,
    SandboxServiceError,
)
from core.sandbox.identity import Principal
from core.sandbox.paths import validate_sha256
from core.sandbox.tool_service import (
    resolve_sandbox_setting,
    workspace_policy_from_settings,
)
from core.sandbox.workspace_service import WorkspaceService
from core.database import Workspace


def _foundation_principal(value: RuntimePrincipal) -> Principal:
    if not isinstance(value, RuntimePrincipal):
        raise TypeError("owner 必须是 RuntimePrincipal")
    owner_type = getattr(value.owner_type, "value", value.owner_type)
    return Principal(
        platform=value.platform,
        owner_type=str(owner_type),
        owner_id=value.owner_id,
    )


def _runtime_principal(value: Principal) -> RuntimePrincipal:
    return RuntimePrincipal(
        platform=str(value.platform),
        owner_type=str(value.owner_type),
        owner_id=str(value.owner_id),
    )


def _acl_sha256(principal: Principal) -> str:
    payload = json.dumps(
        {
            "platform": str(principal.platform),
            "owner_type": str(principal.owner_type),
            "owner_id": str(principal.owner_id),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _response_data(response: Mapping[str, Any]) -> dict[str, Any]:
    data = response.get("data")
    if not isinstance(data, Mapping):
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "Sandbox 控制面返回了无效资产元数据",
            retryable=True,
            stop=False,
        )
    return dict(data)


def _published_asset(data: Mapping[str, Any]) -> PublishedAsset:
    try:
        return PublishedAsset(
            sha256=str(data.get("sha256") or ""),
            size_bytes=int(data.get("size_bytes")),
            media_type=str(
                data.get("media_type") or "application/octet-stream"
            ),
            storage_key=str(data.get("storage_key") or ""),
        )
    except (TypeError, ValueError) as exc:
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "Sandbox 控制面返回了无效资产元数据",
            retryable=True,
            stop=False,
        ) from exc


@dataclass(frozen=True, slots=True)
class ArtifactStreamPublishRequest:
    owner: RuntimePrincipal
    workspace_id: str
    virtual_path: str
    media_type: str
    content: AsyncIterable[bytes]
    content_length: int | None
    source_run_id: str = ""
    source_kind: str = "upload"
    expected_sha256: str = ""
    overwrite: bool = True


class SqlAlchemyArtifactPort(ArtifactPort):
    """只复用 WorkspaceAsset 与 sandboxd CAS，不创建第二套资产存储。"""

    def __init__(
        self,
        db: Session,
        *,
        backend: SandboxBackend | None,
        asset_client_factory: Callable[[], AsyncSandboxdAssetClient] | None,
        max_asset_bytes: int,
    ) -> None:
        self.db = db
        self.backend = backend
        self.asset_client_factory = asset_client_factory
        self.max_asset_bytes = int(max_asset_bytes)
        self.workspace_service = WorkspaceService(
            db,
            policy=workspace_policy_from_settings(db),
        )
        self.asset_service = AssetService(
            db,
            workspace_service=self.workspace_service,
            max_asset_bytes=self.max_asset_bytes,
        )

    @classmethod
    def from_settings(cls, db: Session) -> "SqlAlchemyArtifactPort":
        socket_path = str(resolve_sandbox_setting(db, "sandbox.sandboxd_socket"))
        token_file = str(resolve_sandbox_setting(db, "sandbox.sandboxd_token_file"))
        backend_timeout = float(resolve_sandbox_setting(
            db,
            "sandbox.backend_timeout_seconds",
        ))
        transfer_timeout = float(resolve_sandbox_setting(
            db,
            "sandbox.asset_transfer_timeout_seconds",
        ))

        def client_factory() -> AsyncSandboxdAssetClient:
            return AsyncSandboxdAssetClient(
                socket_path=socket_path,
                token_file=token_file,
                timeout_seconds=transfer_timeout,
            )

        return cls(
            db,
            backend=HttpSandboxdBackend(
                socket_path=socket_path,
                token_file=token_file,
                timeout_seconds=backend_timeout,
            ),
            asset_client_factory=client_factory,
            max_asset_bytes=int(resolve_sandbox_setting(
                db,
                "sandbox.asset_max_bytes",
            )),
        )

    @classmethod
    def for_metadata(cls, db: Session) -> "SqlAlchemyArtifactPort":
        return cls(
            db,
            backend=None,
            asset_client_factory=None,
            max_asset_bytes=int(resolve_sandbox_setting(
                db,
                "sandbox.asset_max_bytes",
            )),
        )

    def close(self) -> None:
        close = getattr(self.backend, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _ref(link: Any, asset: Any) -> RuntimeArtifactRef:
        return RuntimeArtifactRef(
            artifact_id=str(link.artifact_id),
            uri=f"artifact://{link.artifact_id}",
            sha256=str(asset.sha256),
            media_type=str(asset.media_type),
            size_bytes=int(asset.size_bytes),
            version=int(link.version),
            source_run_id=str(link.source_run_id or ""),
        )

    @staticmethod
    def _assert_acl(link: Any, principal: Principal) -> None:
        expected = _acl_sha256(principal)
        if not (
            str(link.acl_platform) == str(principal.platform)
            and str(link.acl_owner_type) == str(principal.owner_type)
            and str(link.acl_owner_id) == str(principal.owner_id)
            and secrets.compare_digest(str(link.acl_sha256), expected)
        ):
            raise PermissionError("Artifact 不存在或 owner 未授权")

    def resolve_sync(
        self,
        request: RuntimeArtifactResolveRequest,
    ) -> RuntimeArtifactRef:
        if not isinstance(request, RuntimeArtifactResolveRequest):
            raise TypeError("request 必须是 RuntimeArtifactResolveRequest")
        principal = _foundation_principal(request.owner)
        resolved = self.asset_service.link_repository.get_owned_artifact(
            request.artifact_id,
            principal,
        )
        if resolved is None:
            raise PermissionError("Artifact 不存在或 owner 未授权")
        link, asset, _workspace = resolved
        self._assert_acl(link, principal)
        return self._ref(link, asset)

    def resolve_trusted_sync(
        self,
        artifact_id: str,
    ) -> tuple[RuntimeArtifactRef, RuntimePrincipal]:
        """最终传输层从持久化 ACL 派生 owner，调用方不能传入 owner。"""

        resolved = self.asset_service.link_repository.get_by_artifact_id(
            str(artifact_id or "")
        )
        if resolved is None:
            raise PermissionError("Artifact 不存在或 owner 未授权")
        link, _asset, workspace = resolved
        principal = Principal(
            platform=str(workspace.platform),
            owner_type=str(workspace.owner_type),
            owner_id=str(workspace.owner_id),
        )
        owner = _runtime_principal(principal)
        return (
            self.resolve_sync(RuntimeArtifactResolveRequest(
                artifact_id=str(artifact_id or ""),
                owner=owner,
            )),
            owner,
        )

    def resolve_sha_sync(
        self,
        *,
        owner: RuntimePrincipal,
        sha256: str,
    ) -> RuntimeArtifactRef:
        principal = _foundation_principal(owner)
        link, asset = self.asset_service.require_authorized(
            principal,
            validate_sha256(sha256),
        )
        self._assert_acl(link, principal)
        return self._ref(link, asset)

    def _workspace_principal(self, workspace_id: str) -> tuple[Workspace, Principal]:
        workspace = self.db.get(Workspace, str(workspace_id or ""))
        if workspace is None or workspace.status != "active":
            raise PermissionError("Artifact 不存在或 owner 未授权")
        return workspace, Principal(
            platform=str(workspace.platform),
            owner_type=str(workspace.owner_type),
            owner_id=str(workspace.owner_id),
        )

    def resolve_for_workspace_sync(
        self,
        *,
        workspace_id: str,
        artifact_id: str,
    ) -> RuntimeArtifactRef:
        """用服务端已固定的 Workspace 校验 Artifact 版本归属。"""

        workspace, principal = self._workspace_principal(workspace_id)
        resolved = self.asset_service.link_repository.get_owned_artifact(
            artifact_id,
            principal,
        )
        if resolved is None or str(resolved[2].id) != str(workspace.id):
            raise PermissionError("Artifact 不存在或 owner 未授权")
        link, asset, _resolved_workspace = resolved
        self._assert_acl(link, principal)
        return self._ref(link, asset)

    def resolve_sha_for_workspace_sync(
        self,
        *,
        workspace_id: str,
        sha256: str,
    ) -> RuntimeArtifactRef:
        """只用于迁移期内容引用；结果规范化为稳定 Artifact 版本。"""

        _workspace, principal = self._workspace_principal(workspace_id)
        link, asset = self.asset_service.require_authorized_for_workspace(
            workspace_id,
            validate_sha256(sha256),
        )
        self._assert_acl(link, principal)
        return self._ref(link, asset)

    def _register(
        self,
        *,
        workspace_id: str,
        virtual_path: str,
        published: PublishedAsset,
        expected_sha256: str = "",
        source_run_id: str = "",
        source_kind: str = "runtime",
    ) -> RuntimeArtifactRef:
        if expected_sha256 and not secrets.compare_digest(
            validate_sha256(expected_sha256),
            validate_sha256(published.sha256),
        ):
            raise SandboxServiceError(
                SandboxErrorCode.EDIT_CONFLICT,
                "Artifact 来源摘要与 expected_sha256 不一致",
            )
        asset, link = self.asset_service.register_published_for_workspace(
            workspace_id,
            published,
            logical_name=virtual_path,
            source_run_id=source_run_id,
            source_kind=source_kind,
        )
        return self._ref(link, asset)

    def publish_sync(
        self,
        request: RuntimeArtifactPublishRequest,
    ) -> RuntimeArtifactRef:
        if not isinstance(request, RuntimeArtifactPublishRequest):
            raise TypeError("request 必须是 RuntimeArtifactPublishRequest")
        principal = _foundation_principal(request.identity.owner)
        workspace = self.workspace_service.require_owned(
            principal,
            request.workspace_id,
        )
        if self.backend is None:
            raise RuntimeError("ArtifactPort 未配置发布后端")
        response = self.backend.publish_asset({
            "workspace_id": workspace.id,
            "path": request.virtual_path,
            "media_type": request.media_type,
        })
        actor_type = getattr(
            request.identity.actor.actor_type,
            "value",
            request.identity.actor.actor_type,
        )
        source_kind = "tool" if str(actor_type) == "tool" else "model"
        return self._register(
            workspace_id=workspace.id,
            virtual_path=request.virtual_path,
            published=_published_asset(_response_data(response)),
            expected_sha256=request.expected_sha256,
            source_run_id=request.identity.run_id,
            source_kind=source_kind,
        )

    async def publish(
        self,
        request: RuntimeArtifactPublishRequest,
    ) -> RuntimeArtifactRef:
        if not isinstance(request, RuntimeArtifactPublishRequest):
            raise TypeError("request 必须是 RuntimeArtifactPublishRequest")
        principal = _foundation_principal(request.identity.owner)
        workspace = self.workspace_service.require_owned(
            principal,
            request.workspace_id,
        )
        if self.backend is None:
            raise RuntimeError("ArtifactPort 未配置发布后端")
        response = await asyncio.to_thread(
            self.backend.publish_asset,
            {
                "workspace_id": workspace.id,
                "path": request.virtual_path,
                "media_type": request.media_type,
            },
        )
        actor_type = getattr(
            request.identity.actor.actor_type,
            "value",
            request.identity.actor.actor_type,
        )
        source_kind = "tool" if str(actor_type) == "tool" else "model"
        return self._register(
            workspace_id=workspace.id,
            virtual_path=request.virtual_path,
            published=_published_asset(_response_data(response)),
            expected_sha256=request.expected_sha256,
            source_run_id=request.identity.run_id,
            source_kind=source_kind,
        )

    async def resolve(
        self,
        request: RuntimeArtifactResolveRequest,
    ) -> RuntimeArtifactRef:
        return self.resolve_sync(request)

    async def read(
        self,
        request: RuntimeArtifactReadRequest,
    ) -> RuntimeArtifactContent:
        if not isinstance(request, RuntimeArtifactReadRequest):
            raise TypeError("request 必须是 RuntimeArtifactReadRequest")
        resolved = self.resolve_sync(RuntimeArtifactResolveRequest(
            artifact_id=request.artifact.artifact_id,
            owner=request.owner,
        ))
        if request.artifact.sha256 and not secrets.compare_digest(
            request.artifact.sha256,
            resolved.sha256,
        ):
            raise PermissionError("Artifact 不存在或 owner 未授权")
        if request.offset >= resolved.size_bytes:
            return RuntimeArtifactContent(
                artifact=resolved,
                data=b"",
                offset=request.offset,
                eof=True,
            )
        end = min(
            resolved.size_bytes - 1,
            request.offset + request.limit - 1,
        )
        if self.asset_client_factory is None:
            raise RuntimeError("ArtifactPort 未配置读取后端")
        client = self.asset_client_factory()
        try:
            upstream = await client.open_asset(
                resolved.sha256,
                range_header=f"bytes={request.offset}-{end}",
            )
            try:
                payload = bytearray()
                async for chunk in upstream.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > request.limit:
                        raise SandboxServiceError(
                            SandboxErrorCode.RUNTIME_UNAVAILABLE,
                            "Sandbox 资产读取超过声明上限",
                        )
            finally:
                await upstream.aclose()
        finally:
            await client.close()
        return RuntimeArtifactContent(
            artifact=resolved,
            data=bytes(payload),
            offset=request.offset,
            eof=request.offset + len(payload) >= resolved.size_bytes,
        )

    async def publish_stream(
        self,
        request: ArtifactStreamPublishRequest,
    ) -> RuntimeArtifactRef:
        if not isinstance(request, ArtifactStreamPublishRequest):
            raise TypeError("request 必须是 ArtifactStreamPublishRequest")
        principal = _foundation_principal(request.owner)
        workspace = self.workspace_service.require_owned(
            principal,
            str(request.workspace_id or ""),
        )
        if request.content_length is not None and (
            request.content_length < 0
            or request.content_length > self.max_asset_bytes
        ):
            raise SandboxServiceError(
                SandboxErrorCode.ASSET_TOO_LARGE,
                "资产超过允许的单文件大小上限",
            )
        if self.backend is None or self.asset_client_factory is None:
            raise RuntimeError("ArtifactPort 未配置发布后端")
        await asyncio.to_thread(
            self.backend.ensure_workspace,
            workspace.id,
            request_id=f"artifactws_{secrets.token_hex(12)}",
        )
        client = self.asset_client_factory()
        try:
            uploaded = _response_data(await client.upload_asset(
                workspace_id=workspace.id,
                media_type=request.media_type,
                content=request.content,
                content_length=request.content_length,
                request_id=f"artifactup_{secrets.token_hex(12)}",
            ))
            staged = _published_asset(uploaded)
            if request.expected_sha256 and not secrets.compare_digest(
                validate_sha256(request.expected_sha256),
                validate_sha256(staged.sha256),
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.EDIT_CONFLICT,
                    "Artifact 来源摘要与 expected_sha256 不一致",
                )
            materialized = _response_data(await client.materialize_asset(
                {
                    "workspace_id": workspace.id,
                    "sha256": staged.sha256,
                    "storage_key": staged.storage_key,
                    "path": request.virtual_path,
                    "quota_bytes": int(workspace.quota_bytes),
                    "overwrite": bool(request.overwrite),
                },
                request_id=f"artifactmat_{secrets.token_hex(12)}",
            ))
            if (
                materialized.get("used_bytes") is None
                or materialized.get("usage_delta_bytes") is None
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Sandbox 控制面缺少 Workspace 空间核算事实",
                    retryable=True,
                    stop=False,
                )
            self.workspace_service.record_usage_delta(
                workspace.id,
                delta_bytes=int(materialized["usage_delta_bytes"]),
                observed_used_bytes=int(materialized["used_bytes"]),
            )
            published = _published_asset(_response_data(
                await client.publish_asset(
                    {
                        "workspace_id": workspace.id,
                        "path": request.virtual_path,
                        "media_type": request.media_type,
                    },
                    request_id=f"artifactpub_{secrets.token_hex(12)}",
                )
            ))
        finally:
            await client.close()
        if not secrets.compare_digest(staged.sha256, published.sha256):
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Workspace 发布内容与暂存内容不一致",
            )
        return self._register(
            workspace_id=workspace.id,
            virtual_path=request.virtual_path,
            published=published,
            expected_sha256=request.expected_sha256,
            source_run_id=request.source_run_id,
            source_kind=request.source_kind,
        )


__all__ = [
    "ArtifactStreamPublishRequest",
    "SqlAlchemyArtifactPort",
]

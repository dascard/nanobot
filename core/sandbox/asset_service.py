"""不可变 Asset 发布、物理去重与 Workspace 授权。"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.database import Asset, Workspace, WorkspaceAsset
from core.sandbox.asset_store import (
    LocalAssetStore,
    asset_runtime_error,
    safe_media_type,
)
from core.sandbox.contracts import (
    PublishedAsset,
    SandboxErrorCode,
    SandboxServiceError,
)
from core.sandbox.identity import Principal
from core.sandbox.paths import SandboxStorageLayout, validate_relative_path, validate_sha256
from core.sandbox.repositories import AssetRepository, WorkspaceAssetRepository
from core.sandbox.workspace_service import WorkspaceService


def _asset_runtime_error() -> SandboxServiceError:
    return asset_runtime_error()


def _safe_media_type(value: str) -> str:
    return safe_media_type(value)


def _safe_logical_name(value: str) -> str:
    components = validate_relative_path(str(value or ""))
    normalized = "/".join(components)
    if len(normalized.encode("utf-8")) > 512:
        raise SandboxServiceError(
            SandboxErrorCode.INVALID_PATH,
            "资产逻辑文件名无效",
        )
    return normalized


class AssetService:
    def __init__(
        self,
        db: Session,
        *,
        workspace_service: WorkspaceService | None = None,
        local_store: LocalAssetStore | None = None,
        max_asset_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        self.db = db
        self.workspace_service = workspace_service or WorkspaceService(db)
        self.asset_repository = AssetRepository(db)
        self.link_repository = WorkspaceAssetRepository(db)
        self.local_store = local_store
        self.max_asset_bytes = int(max_asset_bytes)

    def publish_local_file(
        self,
        principal: Principal,
        relative_path: str,
        *,
        logical_name: str | None = None,
        media_type: str = "application/octet-stream",
    ) -> tuple[Asset, WorkspaceAsset]:
        """测试/本地 sandboxd 适配入口；生产 Nanobot 使用后端返回值注册。"""

        if self.local_store is None:
            raise _asset_runtime_error()
        workspace = self.workspace_service.current(principal)
        published = self.local_store.publish(
            workspace.id,
            relative_path,
            media_type=media_type,
        )
        return self.register_published(
            principal,
            published,
            logical_name=logical_name or relative_path,
        )

    def register_published(
        self,
        principal: Principal,
        published: PublishedAsset,
        *,
        logical_name: str,
    ) -> tuple[Asset, WorkspaceAsset]:
        workspace = self.workspace_service.current(principal)
        return self.register_published_for_workspace(
            workspace.id,
            published,
            logical_name=logical_name,
        )

    def register_published_for_workspace(
        self,
        workspace_id: str,
        published: PublishedAsset,
        *,
        logical_name: str,
    ) -> tuple[Asset, WorkspaceAsset]:
        """为已经由 SandboxAccessPolicy 授权的 Workspace 注册资产。"""

        workspace = self._require_workspace(workspace_id)
        sha256 = validate_sha256(published.sha256)
        size_bytes = int(published.size_bytes)
        if size_bytes < 0 or size_bytes > self.max_asset_bytes:
            raise SandboxServiceError(
                SandboxErrorCode.ASSET_TOO_LARGE,
                "资产超过允许的单文件大小上限",
            )
        expected_key = SandboxStorageLayout.asset_storage_key(sha256)
        if published.storage_key != expected_key:
            raise _asset_runtime_error()
        normalized_name = _safe_logical_name(logical_name)
        media_type = _safe_media_type(published.media_type)

        asset = self.asset_repository.get(sha256)
        if asset is None:
            candidate = Asset(
                sha256=sha256,
                size_bytes=size_bytes,
                media_type=media_type,
                storage_key=expected_key,
            )
            try:
                with self.db.begin_nested():
                    self.asset_repository.add(candidate)
                    self.db.flush()
                asset = candidate
            except IntegrityError:
                asset = self.asset_repository.get(sha256)
                if asset is None:
                    raise _asset_runtime_error() from None
        if (
            int(asset.size_bytes) != size_bytes
            or asset.storage_key != expected_key
        ):
            raise _asset_runtime_error()

        existing_link = self.link_repository.get_by_logical_name(
            workspace.id,
            normalized_name,
        )
        if existing_link is not None:
            if existing_link.asset_sha256 != sha256:
                raise SandboxServiceError(
                    SandboxErrorCode.ASSET_NAME_CONFLICT,
                    "当前 Workspace 已存在同名的其他资产",
                    hint="请使用新的逻辑文件名",
                )
            return asset, existing_link

        candidate_link = WorkspaceAsset(
            workspace_id=workspace.id,
            asset_sha256=sha256,
            logical_name=normalized_name,
        )
        try:
            with self.db.begin_nested():
                self.link_repository.add(candidate_link)
                self.db.flush()
            link = candidate_link
        except IntegrityError:
            link = self.link_repository.get_by_logical_name(
                workspace.id,
                normalized_name,
            )
            if link is None:
                raise _asset_runtime_error() from None
            if link.asset_sha256 != sha256:
                raise SandboxServiceError(
                    SandboxErrorCode.ASSET_NAME_CONFLICT,
                    "当前 Workspace 已存在同名的其他资产",
                    hint="请使用新的逻辑文件名",
                ) from None
        return asset, link

    def require_authorized(
        self,
        principal: Principal,
        sha256: str,
    ) -> tuple[WorkspaceAsset, Asset]:
        workspace = self.workspace_service.current(principal)
        return self.require_authorized_for_workspace(workspace.id, sha256)

    def require_authorized_for_workspace(
        self,
        workspace_id: str,
        sha256: str,
    ) -> tuple[WorkspaceAsset, Asset]:
        workspace = self._require_workspace(workspace_id)
        normalized = validate_sha256(sha256)
        authorized = self.link_repository.get_authorized(workspace.id, normalized)
        if authorized is None:
            raise SandboxServiceError(
                SandboxErrorCode.ASSET_NOT_AUTHORIZED,
                "资产不存在或当前 Workspace 无权访问",
            )
        return authorized

    def import_authorized_ref(
        self,
        principal: Principal,
        source_ref: str,
        *,
        logical_name: str,
    ) -> tuple[Asset, WorkspaceAsset]:
        prefix = "asset://sha256/"
        if not str(source_ref).startswith(prefix):
            raise SandboxServiceError(
                SandboxErrorCode.ASSET_NOT_AUTHORIZED,
                "资产不存在或当前 Workspace 无权访问",
            )
        workspace = self.workspace_service.current(principal)
        return self.import_authorized_ref_for_workspace(
            workspace.id,
            source_ref,
            logical_name=logical_name,
        )

    def import_authorized_ref_for_workspace(
        self,
        workspace_id: str,
        source_ref: str,
        *,
        logical_name: str,
    ) -> tuple[Asset, WorkspaceAsset]:
        prefix = "asset://sha256/"
        if not str(source_ref).startswith(prefix):
            raise SandboxServiceError(
                SandboxErrorCode.ASSET_NOT_AUTHORIZED,
                "资产不存在或当前 Workspace 无权访问",
            )
        sha256 = validate_sha256(str(source_ref)[len(prefix):])
        _source_link, asset = self.require_authorized_for_workspace(
            workspace_id,
            sha256,
        )
        return self.register_published_for_workspace(
            workspace_id,
            PublishedAsset(
                sha256=asset.sha256,
                size_bytes=int(asset.size_bytes),
                media_type=asset.media_type,
                storage_key=asset.storage_key,
            ),
            logical_name=logical_name,
        )

    def _require_workspace(self, workspace_id: str) -> Workspace:
        workspace = self.db.get(Workspace, str(workspace_id or ""))
        if workspace is None or workspace.status != "active":
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "当前会话没有可用的 Workspace",
            )
        return workspace

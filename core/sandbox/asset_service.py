"""不可变 Asset 发布、物理去重与 Workspace 授权。"""

from __future__ import annotations

import hashlib
import json

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


_SOURCE_KINDS = frozenset({
    "legacy",
    "upload",
    "import",
    "tool",
    "model",
    "runtime",
})


def _source_kind(value: str) -> str:
    normalized = str(value or "runtime").strip().lower()
    if normalized not in _SOURCE_KINDS:
        raise ValueError(f"Artifact source_kind 无效：{normalized or '<empty>'}")
    return normalized


def _acl_snapshot(workspace: Workspace) -> tuple[str, str]:
    payload = json.dumps(
        {
            "platform": str(workspace.platform),
            "owner_type": str(workspace.owner_type),
            "owner_id": str(workspace.owner_id),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _artifact_id(
    *,
    workspace_id: str,
    logical_name: str,
    version: int,
    sha256: str,
) -> str:
    digest = hashlib.sha256(
        json.dumps(
            [workspace_id, logical_name, int(version), sha256],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"art_{digest[:48]}"


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
        source_run_id: str = "",
        source_kind: str = "runtime",
    ) -> tuple[Asset, WorkspaceAsset]:
        workspace = self.workspace_service.current(principal)
        return self.register_published_for_workspace(
            workspace.id,
            published,
            logical_name=logical_name,
            source_run_id=source_run_id,
            source_kind=source_kind,
        )

    def register_published_for_workspace(
        self,
        workspace_id: str,
        published: PublishedAsset,
        *,
        logical_name: str,
        source_run_id: str = "",
        source_kind: str = "runtime",
    ) -> tuple[Asset, WorkspaceAsset]:
        """登记已从 owner Workspace 发布的不可变 Artifact 版本。"""

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
        normalized_source_kind = _source_kind(source_kind)
        from core.runtime.event_bus import current_runtime_event_context

        correlation = current_runtime_event_context()
        normalized_source_run_id = str(
            source_run_id or correlation.run_id or ""
        ).strip()
        if len(normalized_source_run_id) > 64:
            raise ValueError("Artifact source_run_id 超过 64 字符")

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

        existing_link = self.link_repository.get_by_content_and_name(
            workspace.id,
            sha256,
            normalized_name,
        )
        if existing_link is not None:
            self._record_published_artifact(workspace, asset, existing_link)
            return asset, existing_link

        _acl_json, acl_sha256 = _acl_snapshot(workspace)
        link: WorkspaceAsset | None = None
        for _attempt in range(4):
            version = self.link_repository.next_version(
                workspace.id,
                normalized_name,
            )
            candidate_link = WorkspaceAsset(
                workspace_id=workspace.id,
                artifact_id=_artifact_id(
                    workspace_id=str(workspace.id),
                    logical_name=normalized_name,
                    version=version,
                    sha256=sha256,
                ),
                asset_sha256=sha256,
                logical_name=normalized_name,
                version=version,
                source_run_id=normalized_source_run_id,
                source_kind=normalized_source_kind,
                acl_platform=str(workspace.platform),
                acl_owner_type=str(workspace.owner_type),
                acl_owner_id=str(workspace.owner_id),
                acl_sha256=acl_sha256,
            )
            try:
                with self.db.begin_nested():
                    self.link_repository.add(candidate_link)
                    self.db.flush()
                link = candidate_link
                break
            except IntegrityError:
                link = self.link_repository.get_by_content_and_name(
                    workspace.id,
                    sha256,
                    normalized_name,
                )
                if link is not None:
                    break
        if link is None:
            raise _asset_runtime_error()
        self._record_published_artifact(workspace, asset, link)
        return asset, link

    def _record_published_artifact(
        self,
        workspace: Workspace,
        asset: Asset,
        link: WorkspaceAsset,
    ) -> None:
        """与资产登记共用事务写入不可变版本事实，不保存路径或 URI。"""

        from core.runtime.event_bus import current_runtime_event_context

        correlation = current_runtime_event_context()
        if not correlation.run_id:
            return
        from core.run_ledger.adapters import artifact_published_event
        from core.run_ledger.contracts import RunLedgerAuthorityError
        from core.run_ledger.persistence import SqlAlchemyRunEventLedger

        event = artifact_published_event(
            correlation=correlation,
            artifact_id=str(link.artifact_id),
            version=int(link.version),
            source_run_id=str(link.source_run_id or ""),
            workspace_id=str(workspace.id),
            sha256=str(asset.sha256),
            size_bytes=int(asset.size_bytes),
            media_type=str(asset.media_type),
        )
        try:
            SqlAlchemyRunEventLedger(self.db).append(event)
        except Exception as exc:
            if isinstance(exc, RunLedgerAuthorityError):
                raise
            raise RunLedgerAuthorityError(
                "Artifact 权威入账失败",
                run_id=event.run_id,
                event_type=event.event_type,
                code="artifact_write_failed",
            ) from exc

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
        raw_ref = str(source_ref or "")
        if not (
            raw_ref.startswith("asset://sha256/")
            or raw_ref.startswith("artifact://")
            or (raw_ref.startswith("[artifact:") and raw_ref.endswith("]"))
        ):
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
        raw_ref = str(source_ref or "")
        asset_prefix = "asset://sha256/"
        artifact_prefix = "artifact://"
        marker_prefix = "[artifact:"
        if raw_ref.startswith(asset_prefix):
            sha256 = validate_sha256(raw_ref[len(asset_prefix):])
            source_link, asset = self.require_authorized_for_workspace(
                workspace_id,
                sha256,
            )
        else:
            if raw_ref.startswith(artifact_prefix):
                artifact_id = raw_ref[len(artifact_prefix):]
            elif raw_ref.startswith(marker_prefix) and raw_ref.endswith("]"):
                artifact_id = raw_ref[len(marker_prefix):-1]
            else:
                raise SandboxServiceError(
                    SandboxErrorCode.ASSET_NOT_AUTHORIZED,
                    "资产不存在或当前 Workspace 无权访问",
                )
            resolved = self.link_repository.get_by_artifact_id(artifact_id)
            if resolved is None or resolved[0].workspace_id != workspace_id:
                raise SandboxServiceError(
                    SandboxErrorCode.ASSET_NOT_AUTHORIZED,
                    "资产不存在或当前 Workspace 无权访问",
                )
            source_link, asset, _workspace = resolved
        return self.register_published_for_workspace(
            workspace_id,
            PublishedAsset(
                sha256=asset.sha256,
                size_bytes=int(asset.size_bytes),
                media_type=asset.media_type,
                storage_key=asset.storage_key,
            ),
            logical_name=str(logical_name or source_link.logical_name),
            source_kind="import",
        )

    def _require_workspace(self, workspace_id: str) -> Workspace:
        workspace = self.db.get(Workspace, str(workspace_id or ""))
        if workspace is None or workspace.status != "active":
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "当前会话没有可用的 Workspace",
            )
        return workspace

"""Sandbox 业务表的最小 SQLAlchemy 仓库。"""

from __future__ import annotations

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from core.database import Asset, SandboxRun, Workspace, WorkspaceAsset
from core.sandbox.identity import Principal


class WorkspaceRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, workspace: Workspace) -> Workspace:
        self.db.add(workspace)
        return workspace

    def get_owned(
        self,
        principal: Principal,
        *,
        name: str = "default",
    ) -> Workspace | None:
        return (
            self.db.query(Workspace)
            .filter(
                Workspace.platform == principal.platform,
                Workspace.owner_type == principal.owner_type,
                Workspace.owner_id == principal.owner_id,
                Workspace.name == name,
            )
            .first()
        )

    def get_by_id_owned(
        self,
        workspace_id: str,
        principal: Principal,
    ) -> Workspace | None:
        return (
            self.db.query(Workspace)
            .filter(
                Workspace.id == workspace_id,
                Workspace.platform == principal.platform,
                Workspace.owner_type == principal.owner_type,
                Workspace.owner_id == principal.owner_id,
            )
            .first()
        )

    def total_used_bytes(self) -> int:
        value = self.db.query(func.coalesce(func.sum(Workspace.used_bytes), 0)).scalar()
        return int(value or 0)

    def add_usage_delta(
        self,
        workspace_id: str,
        *,
        delta_bytes: int,
        accessed_at,
    ) -> int:
        """在单条 SQL 中累加 sandboxd 返回的真实占用增量。"""

        delta = int(delta_bytes)
        result = self.db.execute(
            update(Workspace)
            .where(
                Workspace.id == workspace_id,
                Workspace.used_bytes + delta >= 0,
                Workspace.used_bytes + delta <= Workspace.quota_bytes,
            )
            .values(
                used_bytes=Workspace.used_bytes + delta,
                last_accessed_at=accessed_at,
                updated_at=accessed_at,
            )
            .execution_options(synchronize_session=False),
        )
        return int(result.rowcount or 0)


class AssetRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, sha256: str) -> Asset | None:
        return self.db.get(Asset, sha256)

    def add(self, asset: Asset) -> Asset:
        self.db.add(asset)
        return asset


class WorkspaceAssetRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_authorized(
        self,
        workspace_id: str,
        sha256: str,
    ) -> tuple[WorkspaceAsset, Asset] | None:
        return (
            self.db.query(WorkspaceAsset, Asset)
            .join(Asset, Asset.sha256 == WorkspaceAsset.asset_sha256)
            .filter(
                WorkspaceAsset.workspace_id == workspace_id,
                WorkspaceAsset.asset_sha256 == sha256,
            )
            .first()
        )

    def get_by_logical_name(
        self,
        workspace_id: str,
        logical_name: str,
    ) -> WorkspaceAsset | None:
        return (
            self.db.query(WorkspaceAsset)
            .filter(
                WorkspaceAsset.workspace_id == workspace_id,
                WorkspaceAsset.logical_name == logical_name,
            )
            .first()
        )

    def add(self, link: WorkspaceAsset) -> WorkspaceAsset:
        self.db.add(link)
        return link

    def list_authorized(self, workspace_id: str) -> list[tuple[WorkspaceAsset, Asset]]:
        return (
            self.db.query(WorkspaceAsset, Asset)
            .join(Asset, Asset.sha256 == WorkspaceAsset.asset_sha256)
            .filter(WorkspaceAsset.workspace_id == workspace_id)
            .order_by(WorkspaceAsset.id.asc())
            .all()
        )


class SandboxRunRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_request_id(self, request_id: str) -> SandboxRun | None:
        return (
            self.db.query(SandboxRun)
            .filter(SandboxRun.request_id == request_id)
            .first()
        )

    def add(self, run: SandboxRun) -> SandboxRun:
        self.db.add(run)
        return run

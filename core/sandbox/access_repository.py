"""Sandbox session grant、Workspace 与硬配额绑定查询仓库。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.database import (
    SandboxAccessGrant,
    Workspace,
    WorkspaceMaintenanceState,
    WorkspaceQuotaBinding,
    WorkspaceRuntimeQuotaBinding,
)


class SandboxAccessRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_grant(self, chat_stream_id: str) -> SandboxAccessGrant | None:
        return (
            self.db.query(SandboxAccessGrant)
            .filter(SandboxAccessGrant.chat_stream_id == chat_stream_id)
            .one_or_none()
        )

    def get_workspace(self, workspace_id: str) -> Workspace | None:
        return self.db.get(Workspace, str(workspace_id or ""))

    def get_quota_binding(
        self,
        workspace_id: str,
    ) -> WorkspaceQuotaBinding | None:
        return self.db.get(WorkspaceQuotaBinding, str(workspace_id or ""))

    def get_runtime_quota_binding(
        self,
        workspace_id: str,
    ) -> WorkspaceRuntimeQuotaBinding | None:
        return self.db.get(
            WorkspaceRuntimeQuotaBinding,
            str(workspace_id or ""),
        )

    def get_maintenance_state(
        self,
        workspace_id: str,
    ) -> WorkspaceMaintenanceState | None:
        return self.db.get(
            WorkspaceMaintenanceState,
            str(workspace_id or ""),
        )


__all__ = ["SandboxAccessRepository"]

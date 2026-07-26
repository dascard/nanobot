"""Sandbox 业务表的最小 SQLAlchemy 仓库。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.database import (
    Asset,
    SANDBOX_EXECUTION_PROFILES,
    SANDBOX_LEASE_NONTERMINAL_STATUSES,
    SANDBOX_LEASE_STATUSES,
    SANDBOX_LEASE_TERMINAL_STATUSES,
    SandboxLease,
    SandboxRun,
    Workspace,
    WorkspaceAsset,
)
from core.sandbox.identity import Principal


_LEASE_TRANSITION_FIELDS = frozenset({
    "controller_epoch",
    "idle_expires_at",
    "image_digest",
    "last_active_at",
    "last_error_code",
    "last_error_summary",
    "max_expires_at",
    "reconciled_at",
    "stopped_at",
})


def _require_lease_status(status: object) -> str:
    normalized = str(status or "").strip()
    if normalized not in SANDBOX_LEASE_STATUSES:
        raise ValueError(f"Sandbox Lease status 无效：{normalized or '<empty>'}")
    return normalized


def _require_lease_profile(profile_id: object) -> str:
    normalized = str(profile_id or "").strip()
    if normalized not in SANDBOX_EXECUTION_PROFILES:
        raise ValueError(
            f"Sandbox Lease profile_id 无效：{normalized or '<empty>'}"
        )
    return normalized


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
        observed_used_bytes: int,
        accessed_at,
    ) -> int:
        """在单条 SQL 中累加增量，并校验 sandboxd 的绝对观测未超配额。"""

        delta = int(delta_bytes)
        observed = int(observed_used_bytes)
        result = self.db.execute(
            update(Workspace)
            .where(
                Workspace.id == workspace_id,
                Workspace.used_bytes + delta >= 0,
                Workspace.used_bytes + delta <= Workspace.quota_bytes,
                Workspace.quota_bytes >= observed,
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


class SandboxLeaseRepository:
    """Lease 当前态唯一性与幂等状态迁移的数据库边界。"""

    statuses = SANDBOX_LEASE_STATUSES
    nonterminal_statuses = SANDBOX_LEASE_NONTERMINAL_STATUSES
    terminal_statuses = SANDBOX_LEASE_TERMINAL_STATUSES

    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, lease_id: str) -> SandboxLease | None:
        return self.db.get(SandboxLease, str(lease_id or ""))

    def get_current_by_lease_key(
        self,
        lease_key: str,
    ) -> SandboxLease | None:
        return (
            self.db.query(SandboxLease)
            .filter(
                SandboxLease.lease_key == str(lease_key or ""),
                SandboxLease.status.in_(self.nonterminal_statuses),
            )
            .one_or_none()
        )

    def add_or_get_current(self, lease: SandboxLease) -> SandboxLease:
        """插入当前 Lease；唯一键竞争时返回数据库中的胜出行。"""

        status = _require_lease_status(lease.status)
        _require_lease_profile(lease.profile_id)
        if status not in self.nonterminal_statuses:
            raise ValueError("add_or_get_current 只接受非终态 Lease")
        lease_key = str(lease.lease_key or "").strip()
        if not lease_key:
            raise ValueError("Sandbox Lease lease_key 不能为空")

        current = self.get_current_by_lease_key(lease_key)
        if current is not None:
            return current
        try:
            with self.db.begin_nested():
                self.db.add(lease)
                self.db.flush()
        except IntegrityError:
            self.db.expire_all()
            current = self.get_current_by_lease_key(lease_key)
            if current is None:
                raise
            return current
        return lease

    def add_history(self, lease: SandboxLease) -> SandboxLease:
        status = _require_lease_status(lease.status)
        _require_lease_profile(lease.profile_id)
        if status not in self.terminal_statuses:
            raise ValueError("add_history 只接受终态 Lease")
        self.db.add(lease)
        return lease

    def transition(
        self,
        lease_id: str,
        *,
        expected_statuses: Iterable[str],
        target_status: str,
        changes: Mapping[str, object] | None = None,
    ) -> SandboxLease | None:
        """按预期状态原子迁移；重复写入目标状态时返回同一行。"""

        expected = frozenset(
            _require_lease_status(status)
            for status in expected_statuses
        )
        if not expected:
            raise ValueError("expected_statuses 不能为空")
        target = _require_lease_status(target_status)
        values = dict(changes or {})
        unexpected_fields = sorted(set(values) - _LEASE_TRANSITION_FIELDS)
        if unexpected_fields:
            raise ValueError(
                f"Sandbox Lease transition 字段无效：{unexpected_fields}"
            )

        row = self.get(lease_id)
        if row is None:
            return None
        current_status = _require_lease_status(row.status)
        if current_status == target:
            if values:
                self.db.execute(
                    update(SandboxLease)
                    .where(
                        SandboxLease.lease_id == str(lease_id or ""),
                        SandboxLease.status == target,
                    )
                    .values(**values)
                    .execution_options(synchronize_session=False)
                )
                self.db.expire_all()
                return self.get(lease_id)
            return row
        if (
            current_status in self.terminal_statuses
            or current_status not in expected
        ):
            return None

        values["status"] = target
        changed = self.db.execute(
            update(SandboxLease)
            .where(
                SandboxLease.lease_id == str(lease_id or ""),
                SandboxLease.status == current_status,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        self.db.expire_all()
        if int(changed.rowcount or 0) == 1:
            return self.get(lease_id)
        raced = self.get(lease_id)
        if raced is not None and str(raced.status) == target:
            return raced
        return None


__all__ = [
    "AssetRepository",
    "SandboxLeaseRepository",
    "SandboxRunRepository",
    "WorkspaceAssetRepository",
    "WorkspaceRepository",
]

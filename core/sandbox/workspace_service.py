"""Workspace owner-only ACL、幂等创建与逻辑配额。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.database import Workspace
from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.identity import Principal
from core.sandbox.repositories import WorkspaceRepository
from core.time_utils import db_now_naive


@dataclass(frozen=True)
class WorkspacePolicy:
    default_quota_bytes: int = 2 * 1024 * 1024 * 1024
    total_quota_bytes: int = 10 * 1024 * 1024 * 1024
    disk_max_percent: int = 80
    disk_min_free_bytes: int = 50 * 1024 * 1024 * 1024


class WorkspaceService:
    def __init__(
        self,
        db: Session,
        *,
        policy: WorkspacePolicy | None = None,
    ) -> None:
        self.db = db
        self.policy = policy or WorkspacePolicy()
        self.repository = WorkspaceRepository(db)

    def ensure_default(self, principal: Principal) -> Workspace:
        existing = self.repository.get_owned(principal)
        if existing is not None:
            return self._require_active(existing)

        workspace = Workspace(
            id=str(uuid4()),
            platform=principal.platform,
            owner_type=principal.owner_type,
            owner_id=principal.owner_id,
            name="default",
            status="active",
            quota_bytes=self.policy.default_quota_bytes,
            used_bytes=0,
        )
        try:
            with self.db.begin_nested():
                self.repository.add(workspace)
                self.db.flush()
        except IntegrityError:
            existing = self.repository.get_owned(principal)
            if existing is None:
                raise
            return self._require_active(existing)
        return workspace

    def current(self, principal: Principal) -> Workspace:
        workspace = self.repository.get_owned(principal)
        if workspace is None:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "当前身份没有可用的 Workspace",
            )
        return self._require_active(workspace)

    def require_owned(self, principal: Principal, workspace_id: str) -> Workspace:
        workspace = self.repository.get_by_id_owned(workspace_id, principal)
        if workspace is None:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "当前身份没有可用的 Workspace",
            )
        return self._require_active(workspace)

    def record_usage_delta(
        self,
        workspace_id: str,
        *,
        delta_bytes: int,
        observed_used_bytes: int,
    ) -> None:
        """原子记录 sandboxd 核算的真实文件大小增量。

        配额执行的唯一事实源是持有工作区目录的 sandboxd；数据库字段仅是
        管理端投影，但仍以单条 SQL 更新，避免并发请求覆盖彼此的快照。
        """

        observed = int(observed_used_bytes)
        delta = int(delta_bytes)
        # sandboxd 返回的是同一次串行 Workspace 变更的 before/after 差值。
        # 数据库提交可能乱序，因此不能用 observed 覆盖投影或要求它等于本次
        # SQL 更新结果；但 observed - delta 必须仍是合法的写入前占用。
        if observed < 0 or observed - delta < 0:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Sandbox 控制面返回了无效空间核算结果",
                retryable=True,
                stop=False,
            )
        changed = self.repository.add_usage_delta(
            workspace_id,
            delta_bytes=delta,
            observed_used_bytes=observed,
            accessed_at=db_now_naive(),
        )
        if changed != 1:
            raise SandboxServiceError(
                SandboxErrorCode.RUNTIME_UNAVAILABLE,
                "Workspace 空间核算投影冲突",
                retryable=True,
                stop=False,
            )
        self.db.expire_all()

    @staticmethod
    def _require_active(workspace: Workspace) -> Workspace:
        if workspace.status != "active":
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "当前身份没有可用的 Workspace",
            )
        workspace.last_accessed_at = db_now_naive()
        return workspace

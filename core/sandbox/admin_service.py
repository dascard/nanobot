"""Sandbox 管理请求的数据库事务边界与幂等入队。"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.database import (
    SandboxAccessGrant,
    SandboxAdminOperation,
    SandboxProjectSequence,
    Workspace,
    WorkspaceQuotaBinding,
)
from core.sandbox.access_contracts import SandboxCapability
from core.sandbox.access_policy import canonical_sandbox_identity
from core.sandbox.tool_service import resolve_sandbox_setting
from core.time_utils import db_now_naive


PROJECT_ID_MIN = 10000
PROJECT_ID_MAX = 2_147_483_647
QUOTA_MIN_BYTES = 1024 * 1024
QUOTA_MAX_BYTES = 1024 * 1024 * 1024 * 1024


class SandboxAdminRequestError(ValueError):
    """可安全返回给管理端的请求冲突或约束错误。"""

    def __init__(self, code: str, summary: str, *, status_code: int = 400) -> None:
        self.code = str(code)
        self.summary = str(summary)
        self.status_code = int(status_code)
        super().__init__(self.summary)


@dataclass(frozen=True, slots=True)
class EnqueuedSandboxOperation:
    operation: SandboxAdminOperation
    created: bool


def _operation_id() -> str:
    return f"sbxop_{secrets.token_hex(16)}"


def _normalize_request_id(value: object) -> str:
    request_id = str(value or "").strip()
    if (
        not 8 <= len(request_id) <= 64
        or any(ord(char) < 33 or ord(char) > 126 for char in request_id)
    ):
        raise SandboxAdminRequestError(
            "invalid_request_id",
            "request_id 必须是 8-64 个可打印 ASCII 字符",
        )
    return request_id


def _normalize_quota(value: object) -> int:
    if isinstance(value, bool):
        raise SandboxAdminRequestError("invalid_quota", "Workspace 配额无效")
    try:
        quota = int(value)
    except (TypeError, ValueError) as exc:
        raise SandboxAdminRequestError("invalid_quota", "Workspace 配额无效") from exc
    if not QUOTA_MIN_BYTES <= quota <= QUOTA_MAX_BYTES:
        raise SandboxAdminRequestError(
            "invalid_quota",
            "Workspace 配额必须位于 1 MiB 到 1 TiB",
        )
    return quota


class SandboxAdminService:
    """只持久化期望状态；宿主副作用由异步 operation runner 执行。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _ensure_project_sequence(self) -> None:
        if self.db.get(SandboxProjectSequence, "workspace") is not None:
            return
        try:
            with self.db.begin_nested():
                self.db.add(SandboxProjectSequence(
                    name="workspace",
                    next_value=PROJECT_ID_MIN,
                    updated_at=db_now_naive(),
                ))
                self.db.flush()
        except IntegrityError:
            # 并发初始化时唯一键决定唯一种子。
            pass

    def _lock_quota_budget(self) -> None:
        """串行化总配额核算，避免并发 check-then-act 超卖预算。"""

        self._ensure_project_sequence()
        changed = self.db.execute(
            update(SandboxProjectSequence)
            .where(SandboxProjectSequence.name == "workspace")
            .values(next_value=SandboxProjectSequence.next_value)
        )
        if int(changed.rowcount or 0) != 1:
            raise SandboxAdminRequestError(
                "project_id_sequence_unavailable",
                "Workspace project ID 序列不可用",
                status_code=503,
            )

    def _projected_total_quota(
        self,
        *,
        workspace_id: str | None,
        desired_quota_bytes: int,
    ) -> int:
        query = self.db.query(func.coalesce(func.sum(
            WorkspaceQuotaBinding.desired_quota_bytes,
        ), 0))
        if workspace_id:
            query = query.filter(WorkspaceQuotaBinding.workspace_id != workspace_id)
        return int(query.scalar() or 0) + int(desired_quota_bytes)

    def _assert_total_budget(
        self,
        *,
        workspace_id: str | None,
        desired_quota_bytes: int,
    ) -> None:
        self._lock_quota_budget()
        total_limit = int(resolve_sandbox_setting(
            self.db,
            "sandbox.total_quota_bytes",
        ))
        if self._projected_total_quota(
            workspace_id=workspace_id,
            desired_quota_bytes=desired_quota_bytes,
        ) > total_limit:
            raise SandboxAdminRequestError(
                "sandbox_total_quota_exceeded",
                "Sandbox Workspace 总配额预算不足",
                status_code=409,
            )

    def allocate_project_id(self) -> int:
        """用单条 UPDATE RETURNING 原子领取 project ID。"""

        self._ensure_project_sequence()

        result = self.db.execute(
            update(SandboxProjectSequence)
            .where(
                SandboxProjectSequence.name == "workspace",
                SandboxProjectSequence.next_value <= PROJECT_ID_MAX,
            )
            .values(
                next_value=SandboxProjectSequence.next_value + 1,
                updated_at=db_now_naive(),
            )
            .returning(SandboxProjectSequence.next_value)
        )
        next_value = result.scalar_one_or_none()
        if next_value is None:
            raise SandboxAdminRequestError(
                "project_id_exhausted",
                "Workspace project ID 已耗尽或序列未初始化",
                status_code=503,
            )
        project_id = int(next_value) - 1
        if not PROJECT_ID_MIN <= project_id <= PROJECT_ID_MAX:
            raise SandboxAdminRequestError(
                "project_id_exhausted",
                "Workspace project ID 已耗尽",
                status_code=503,
            )
        return project_id

    def _existing_request(
        self,
        request_id: str,
        *,
        operation_type: str,
        chat_stream_id: str,
        workspace_id: str,
        desired_capability: str,
        desired_quota_bytes: int,
    ) -> SandboxAdminOperation | None:
        existing = (
            self.db.query(SandboxAdminOperation)
            .filter(SandboxAdminOperation.request_id == request_id)
            .one_or_none()
        )
        if existing is None:
            return None
        actual = (
            str(existing.operation_type or ""),
            str(existing.chat_stream_id or ""),
            str(existing.workspace_id or ""),
            str(existing.desired_capability or ""),
            int(existing.desired_quota_bytes or 0),
        )
        expected = (
            operation_type,
            chat_stream_id,
            workspace_id,
            desired_capability,
            int(desired_quota_bytes),
        )
        if actual != expected:
            raise SandboxAdminRequestError(
                "idempotency_conflict",
                "同一 request_id 已用于不同 Sandbox 管理请求",
                status_code=409,
            )
        return existing

    def enqueue_access_change(
        self,
        *,
        request_id: object,
        platform: object,
        chat_type: object,
        session_id: object,
        capability: object,
        quota_bytes: object | None,
        expected_version: int | None,
        reason: object,
        actor: object,
    ) -> EnqueuedSandboxOperation:
        normalized_request_id = _normalize_request_id(request_id)
        try:
            identity = canonical_sandbox_identity(
                platform=platform,
                chat_type=chat_type,
                session_id=session_id,
            )
        except Exception as exc:
            raise SandboxAdminRequestError(
                "invalid_session_identity",
                "Sandbox 会话身份无法规范化",
            ) from exc
        if identity.chat_type != "private":
            raise SandboxAdminRequestError(
                "group_sandbox_not_supported",
                "首期只允许管理私聊 Sandbox 会话",
            )
        try:
            desired = SandboxCapability.parse(capability)
        except ValueError as exc:
            raise SandboxAdminRequestError(
                "invalid_capability",
                "Sandbox 能力等级无效",
            ) from exc
        desired_name = desired.value_name
        actor_name = str(actor or "admin")[:128]
        reason_text = str(reason or "")[:255]
        grant = (
            self.db.query(SandboxAccessGrant)
            .filter(SandboxAccessGrant.chat_stream_id == identity.chat_stream_id)
            .one_or_none()
        )
        previous = SandboxCapability.OFF
        if grant is not None:
            previous = SandboxCapability.parse(grant.capability_level)

        if desired is SandboxCapability.OFF:
            normalized_quota = 0
        elif quota_bytes is None:
            normalized_quota = int(resolve_sandbox_setting(
                self.db,
                "sandbox.workspace_quota_bytes",
            ))
        else:
            normalized_quota = _normalize_quota(quota_bytes)

        existing = self._existing_request(
            normalized_request_id,
            operation_type="set_access",
            chat_stream_id=identity.chat_stream_id,
            workspace_id=str(grant.workspace_id or "") if grant else "",
            desired_capability=desired_name,
            desired_quota_bytes=normalized_quota,
        )
        if existing is not None:
            return EnqueuedSandboxOperation(existing, False)
        if (
            grant is not None
            and expected_version is not None
            and int(expected_version) != int(grant.version)
        ):
            raise SandboxAdminRequestError(
                "grant_version_conflict",
                "Sandbox 授权已被其他管理员修改，请刷新后重试",
                status_code=409,
            )

        now = db_now_naive()
        if grant is None:
            grant = SandboxAccessGrant(
                id=str(uuid4()),
                chat_stream_id=identity.chat_stream_id,
                platform=identity.platform,
                chat_type=identity.chat_type,
                external_session_id=identity.external_session_id,
                capability_level="off",
                status="disabled",
                version=1,
                reason=reason_text,
                created_by=actor_name,
                updated_by=actor_name,
                created_at=now,
                updated_at=now,
            )
            self.db.add(grant)
            self.db.flush()

        binding: WorkspaceQuotaBinding | None = None
        if desired is not SandboxCapability.OFF:
            workspace = self.db.get(Workspace, str(grant.workspace_id or ""))
            if workspace is None:
                workspace = Workspace(
                    id=str(uuid4()),
                    platform=identity.platform,
                    owner_type="user",
                    owner_id=str(grant.id),
                    name="default",
                    status="active",
                    quota_bytes=normalized_quota,
                    used_bytes=0,
                    created_at=now,
                    updated_at=now,
                )
                self.db.add(workspace)
                self.db.flush()
                grant.workspace_id = workspace.id
            if normalized_quota < int(workspace.used_bytes or 0):
                raise SandboxAdminRequestError(
                    "quota_below_usage",
                    "新配额不能低于 Workspace 当前占用",
                    status_code=409,
                )
            self._assert_total_budget(
                workspace_id=workspace.id,
                desired_quota_bytes=normalized_quota,
            )
            binding = self.db.get(WorkspaceQuotaBinding, workspace.id)
            if binding is None:
                binding = WorkspaceQuotaBinding(
                    workspace_id=workspace.id,
                    project_id=self.allocate_project_id(),
                    desired_quota_bytes=normalized_quota,
                    applied_quota_bytes=0,
                    status="pending",
                    generation=1,
                    created_at=now,
                    updated_at=now,
                )
                self.db.add(binding)
            elif int(binding.desired_quota_bytes) != normalized_quota:
                binding.desired_quota_bytes = normalized_quota
                binding.status = "pending"
                binding.generation = int(binding.generation or 0) + 1
                binding.last_error_code = ""
                binding.last_error_summary = ""
                binding.updated_at = now

        # version 表示最新管理期望的 fencing 代际。每个新请求先推进版本，
        # 使尚未执行的旧 operation 无法覆盖更新后的能力目标。
        grant.version = int(grant.version) + 1
        # 降级和关闭必须立即收窄；升级只在宿主硬配额确认后生效。
        if desired <= previous:
            grant.capability_level = desired_name
            grant.status = "disabled" if desired is SandboxCapability.OFF else "active"
        elif previous is SandboxCapability.OFF:
            grant.status = "provisioning"
        grant.reason = reason_text
        grant.updated_by = actor_name
        grant.updated_at = now

        operation = SandboxAdminOperation(
            operation_id=_operation_id(),
            request_id=normalized_request_id,
            operation_type="set_access",
            chat_stream_id=identity.chat_stream_id,
            workspace_id=str(grant.workspace_id or "") or None,
            desired_capability=desired_name,
            previous_capability=previous.value_name,
            desired_quota_bytes=normalized_quota,
            expected_grant_version=int(grant.version),
            expected_quota_generation=(
                int(binding.generation) if binding is not None else None
            ),
            status="pending",
            step="queued",
            reason=reason_text,
            created_by=actor_name,
            created_at=now,
            updated_at=now,
        )
        self.db.add(operation)
        self.db.flush()
        return EnqueuedSandboxOperation(operation, True)

    def enqueue_quota_change(
        self,
        *,
        request_id: object,
        workspace_id: object,
        quota_bytes: object,
        reason: object,
        actor: object,
    ) -> EnqueuedSandboxOperation:
        normalized_request_id = _normalize_request_id(request_id)
        normalized_workspace_id = str(workspace_id or "").strip()
        desired_quota = _normalize_quota(quota_bytes)
        workspace = self.db.get(Workspace, normalized_workspace_id)
        binding = self.db.get(WorkspaceQuotaBinding, normalized_workspace_id)
        if workspace is None or binding is None:
            raise SandboxAdminRequestError(
                "workspace_not_found",
                "Workspace 或 project quota 绑定不存在",
                status_code=404,
            )
        if desired_quota < int(workspace.used_bytes or 0):
            raise SandboxAdminRequestError(
                "quota_below_usage",
                "新配额不能低于 Workspace 当前占用",
                status_code=409,
            )
        self._assert_total_budget(
            workspace_id=normalized_workspace_id,
            desired_quota_bytes=desired_quota,
        )
        existing = self._existing_request(
            normalized_request_id,
            operation_type="set_quota",
            chat_stream_id="",
            workspace_id=normalized_workspace_id,
            desired_capability="",
            desired_quota_bytes=desired_quota,
        )
        if existing is not None:
            return EnqueuedSandboxOperation(existing, False)

        now = db_now_naive()
        if int(binding.desired_quota_bytes) != desired_quota:
            binding.desired_quota_bytes = desired_quota
            binding.generation = int(binding.generation or 0) + 1
        binding.status = "pending"
        binding.last_error_code = ""
        binding.last_error_summary = ""
        binding.updated_at = now
        operation = SandboxAdminOperation(
            operation_id=_operation_id(),
            request_id=normalized_request_id,
            operation_type="set_quota",
            workspace_id=normalized_workspace_id,
            desired_quota_bytes=desired_quota,
            expected_quota_generation=int(binding.generation),
            status="pending",
            step="queued",
            reason=str(reason or "")[:255],
            created_by=str(actor or "admin")[:128],
            created_at=now,
            updated_at=now,
        )
        self.db.add(operation)
        self.db.flush()
        return EnqueuedSandboxOperation(operation, True)


__all__ = [
    "EnqueuedSandboxOperation",
    "SandboxAdminRequestError",
    "SandboxAdminService",
]

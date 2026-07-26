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
    WorkspaceMaintenanceState,
    WorkspaceQuotaBinding,
    WorkspaceRuntimeQuotaBinding,
)
from core.sandbox.access_contracts import SandboxCapability
from core.sandbox.access_policy import canonical_sandbox_identity
from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.execution_profiles import load_execution_profile_registry
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

        while True:
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
                    "Sandbox project ID 已耗尽或序列未初始化",
                    status_code=503,
                )
            project_id = int(next_value) - 1
            if not PROJECT_ID_MIN <= project_id <= PROJECT_ID_MAX:
                raise SandboxAdminRequestError(
                    "project_id_exhausted",
                    "Sandbox project ID 已耗尽",
                    status_code=503,
                )
            workspace_used = self.db.query(WorkspaceQuotaBinding).filter(
                WorkspaceQuotaBinding.project_id == project_id,
            ).first()
            runtime_used = self.db.query(WorkspaceRuntimeQuotaBinding).filter(
                WorkspaceRuntimeQuotaBinding.project_id == project_id,
            ).first()
            if workspace_used is None and runtime_used is None:
                return project_id

    @staticmethod
    def _runtime_quota_bytes(profile_id: object) -> int:
        try:
            profile = load_execution_profile_registry().descriptor(
                str(profile_id or "restricted"),
            )
        except Exception as exc:
            raise SandboxAdminRequestError(
                "execution_profile_unavailable",
                "Sandbox 执行 Profile 当前不可用于配额配置",
                status_code=503,
            ) from exc
        return int(profile.runtime_quota_bytes)

    def _ensure_runtime_binding(
        self,
        *,
        workspace_id: str,
        profile_id: object,
        workspace_binding: WorkspaceQuotaBinding,
        now,
    ) -> WorkspaceRuntimeQuotaBinding:
        desired_runtime_quota = self._runtime_quota_bytes(profile_id)
        runtime_binding = self.db.get(
            WorkspaceRuntimeQuotaBinding,
            workspace_id,
        )
        if runtime_binding is None:
            runtime_binding = WorkspaceRuntimeQuotaBinding(
                workspace_id=workspace_id,
                project_id=self.allocate_project_id(),
                desired_quota_bytes=desired_runtime_quota,
                applied_quota_bytes=0,
                status="pending",
                generation=int(workspace_binding.generation),
                created_at=now,
                updated_at=now,
            )
            self.db.add(runtime_binding)
            return runtime_binding

        generation = max(
            int(workspace_binding.generation or 1),
            int(runtime_binding.generation or 1),
        )
        if int(runtime_binding.desired_quota_bytes) != desired_runtime_quota:
            generation += 1
            runtime_binding.desired_quota_bytes = desired_runtime_quota
            runtime_binding.applied_quota_bytes = 0
        if int(workspace_binding.generation) != generation:
            workspace_binding.generation = generation
            workspace_binding.status = "pending"
        runtime_binding.generation = generation
        if (
            runtime_binding.status != "applied"
            or int(runtime_binding.applied_quota_bytes or 0)
            != desired_runtime_quota
        ):
            runtime_binding.status = "pending"
        runtime_binding.last_error_code = ""
        runtime_binding.last_error_summary = ""
        runtime_binding.updated_at = now
        return runtime_binding

    def _mark_workspace_quiescing(
        self,
        *,
        workspace_id: str,
        generation: int,
        now,
    ) -> WorkspaceMaintenanceState:
        """在管理请求提交时先关闭新 Lease/进程门禁。"""

        state = self.db.get(WorkspaceMaintenanceState, workspace_id)
        if state is None:
            state = WorkspaceMaintenanceState(
                workspace_id=workspace_id,
                status="quiescing",
                generation=max(1, int(generation)),
                applied_quota_generation=0,
                created_at=now,
                updated_at=now,
            )
            self.db.add(state)
            return state
        state.status = "quiescing"
        state.generation = max(
            int(state.generation or 1),
            int(generation),
        )
        state.locked_by = ""
        state.fencing_token = ""
        state.lease_expires_at = None
        state.last_error_code = ""
        state.last_error_summary = ""
        state.updated_at = now
        return state

    def _existing_request(
        self,
        request_id: str,
        *,
        operation_type: str,
        chat_stream_id: str,
        workspace_id: str,
        desired_capability: str,
        desired_quota_bytes: int,
        desired_profile: str = "",
        current_profile: str = "",
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
        if desired_profile and current_profile != desired_profile:
            raise SandboxAdminRequestError(
                "idempotency_conflict",
                "同一 request_id 已用于不同 Sandbox 执行 Profile",
                status_code=409,
            )
        return existing

    @staticmethod
    def _execution_profile(value: object):
        profile_id = str(value or "restricted").strip()
        try:
            registry = load_execution_profile_registry()
            return registry.descriptor(profile_id)
        except SandboxServiceError as exc:
            if exc.code is SandboxErrorCode.AUTHORIZATION_FAILED:
                raise SandboxAdminRequestError(
                    "invalid_execution_profile",
                    "Sandbox 执行 Profile 无效或当前不可授权",
                ) from exc
            raise SandboxAdminRequestError(
                "execution_profile_unavailable",
                "Sandbox 执行 Profile 注册表当前不可用",
                status_code=503,
            ) from exc

    def _is_noop_access_change(
        self,
        *,
        grant: SandboxAccessGrant,
        desired: SandboxCapability,
        previous: SandboxCapability,
        desired_profile: str,
        normalized_quota: int,
    ) -> bool:
        """目标状态与当前完全一致且宿主已全部应用时才算 noop。

        任一环节(绑定/运行时配额/维护门禁)未就绪都返回 False:重发同样
        参数是合法的修复动作,必须走正常入队路径。
        """
        if desired is not previous:
            return False
        if desired_profile != str(grant.execution_profile or ""):
            return False
        if desired is SandboxCapability.OFF:
            return str(grant.status) == "disabled"
        if str(grant.status) != "active":
            return False
        workspace_id = str(grant.workspace_id or "")
        if not workspace_id:
            return False
        workspace = self.db.get(Workspace, workspace_id)
        binding = self.db.get(WorkspaceQuotaBinding, workspace_id)
        if (
            workspace is None
            or str(workspace.status) != "active"
            or binding is None
            or str(binding.status) != "applied"
            or int(binding.desired_quota_bytes or 0) != normalized_quota
            or int(binding.applied_quota_bytes or 0) != normalized_quota
            or int(workspace.quota_bytes or 0) != normalized_quota
        ):
            return False
        runtime_binding = self.db.get(
            WorkspaceRuntimeQuotaBinding,
            workspace_id,
        )
        if (
            runtime_binding is None
            or str(runtime_binding.status) != "applied"
            or int(runtime_binding.applied_quota_bytes or 0)
            != int(runtime_binding.desired_quota_bytes or 0)
            or int(runtime_binding.generation or 0) != int(binding.generation or 0)
        ):
            return False
        maintenance = self.db.get(WorkspaceMaintenanceState, workspace_id)
        return (
            maintenance is not None
            and str(maintenance.status) == "ready"
            and int(maintenance.generation or 0) == int(binding.generation or 0)
            and int(maintenance.applied_quota_generation or 0)
            == int(binding.generation or 0)
        )

    def enqueue_access_change(
        self,
        *,
        request_id: object,
        platform: object,
        chat_type: object,
        session_id: object,
        capability: object,
        execution_profile: object = "restricted",
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
        profile = self._execution_profile(execution_profile)
        desired_profile = profile.profile_id
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
            desired_profile=desired_profile,
            current_profile=(
                str(grant.execution_profile)
                if grant is not None
                else ""
            ),
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
        # 原样保存守卫:目标能力/Profile/配额与当前完全一致且宿主状态已
        # 全部应用时,不得进入正常入队路径——那会 quiesce Workspace 并回收
        # 运行中的 Lease/进程。记一条已完成的 noop 操作保留审计与幂等。
        if grant is not None and self._is_noop_access_change(
            grant=grant,
            desired=desired,
            previous=previous,
            desired_profile=desired_profile,
            normalized_quota=normalized_quota,
        ):
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
                status="succeeded",
                step="noop_no_change",
                reason=reason_text,
                created_by=actor_name,
                created_at=now,
                updated_at=now,
                finished_at=now,
            )
            self.db.add(operation)
            self.db.flush()
            return EnqueuedSandboxOperation(operation, False)

        if grant is None:
            grant = SandboxAccessGrant(
                id=str(uuid4()),
                chat_stream_id=identity.chat_stream_id,
                platform=identity.platform,
                chat_type=identity.chat_type,
                external_session_id=identity.external_session_id,
                capability_level="off",
                execution_profile=desired_profile,
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
        else:
            grant.execution_profile = desired_profile

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
            runtime_binding = self._ensure_runtime_binding(
                workspace_id=workspace.id,
                profile_id=desired_profile,
                workspace_binding=binding,
                now=now,
            )
            self._mark_workspace_quiescing(
                workspace_id=workspace.id,
                generation=max(
                    int(binding.generation),
                    int(runtime_binding.generation),
                ),
                now=now,
            )

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
        # 幂等重放必须先于业务预检:已成功入队的请求重放时,当前用量/总预算
        # 可能已变化,预检若先执行会把合法重放误判成 409,破坏 request_id
        # 幂等契约(与 enqueue_access_change 的先重放后校验顺序对齐)。
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

        now = db_now_naive()
        if int(binding.desired_quota_bytes) != desired_quota:
            binding.desired_quota_bytes = desired_quota
            binding.generation = int(binding.generation or 0) + 1
        grant = (
            self.db.query(SandboxAccessGrant)
            .filter(SandboxAccessGrant.workspace_id == normalized_workspace_id)
            .order_by(SandboxAccessGrant.updated_at.desc())
            .first()
        )
        runtime_binding = self._ensure_runtime_binding(
            workspace_id=normalized_workspace_id,
            profile_id=(
                grant.execution_profile
                if grant is not None
                else "restricted"
            ),
            workspace_binding=binding,
            now=now,
        )
        runtime_binding.generation = int(binding.generation)
        runtime_binding.status = "pending"
        binding.status = "pending"
        binding.last_error_code = ""
        binding.last_error_summary = ""
        binding.updated_at = now
        self._mark_workspace_quiescing(
            workspace_id=normalized_workspace_id,
            generation=int(binding.generation),
            now=now,
        )
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

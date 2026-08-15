"""Sandbox 唯一授权 Policy：硬上限、业务开关、session grant 与硬配额。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from sqlalchemy.orm import Session

from core.chat_stream_identity import (
    ChatStreamIdentityError,
    resolve_chat_stream_identity,
)
from core.config_registry import SETTING_DEFS
from core.database import SystemSetting
from core.sandbox.access_contracts import (
    LEASE_PROCESS_TOOL_NAMES,
    SandboxAccessDecision,
    SandboxCapability,
    TOOL_REQUIRED_CAPABILITY,
)
from core.sandbox.access_repository import SandboxAccessRepository
from core.sandbox.contracts import SandboxServiceError
from core.sandbox.execution_profiles import load_execution_profile_registry
from core.sandbox.workspace_acl import workspace_matches_sandbox_grant
from core.runtime.extensions import (
    PolicyDescriptor,
    PolicyTypedFailure,
    RuntimeFailurePolicy,
    execute_policy,
)
from core.settings_service import coerce_setting_value, settings


SANDBOX_ACCESS_POLICY_DESCRIPTOR = PolicyDescriptor(
    policy_id="sandbox.access",
    owner_module="sandbox.control_plane",
    domain="sandbox",
    input_contract="sandbox.access.input.v1",
    output_contract="sandbox.access.decision.v2",
    failure_policy=RuntimeFailurePolicy.FAIL_CLOSED,
    security_sensitive=True,
)


def _database_bool(db: Session, key: str, default: bool = False) -> bool:
    """业务开关允许 DB 覆写；安全硬上限不经过本函数。"""

    try:
        row = db.query(SystemSetting).filter(SystemSetting.key == key).one_or_none()
        if row is not None and row.value is not None:
            definition = SETTING_DEFS.get(key)
            if definition is not None:
                return bool(coerce_setting_value(row.value, definition))
            return str(row.value).strip().lower() in {"1", "true", "yes", "on"}
    except Exception:
        return False
    return settings.get_bool(key, default)


def canonical_sandbox_identity(
    *,
    platform: object,
    chat_type: object,
    session_id: object,
):
    normalized_chat_type = str(chat_type or "").strip().lower()
    if normalized_chat_type == "private_superuser":
        normalized_chat_type = "private"
    identity = resolve_chat_stream_identity(
        platform=str(platform or ""),
        chat_type=normalized_chat_type,
        session_id=str(session_id or ""),
    )
    if (
        len(identity.external_session_id) > 255
        or len(identity.chat_stream_id) > 512
    ):
        raise ChatStreamIdentityError(
            "sandbox_session_id_too_long",
            "Sandbox session_id 超出存储契约上限",
        )
    return identity


class SandboxAccessPolicy:
    """同一实例接口同时服务 wire schema 过滤和工具执行前校验。"""

    def __init__(
        self,
        db: Session | None,
        *,
        infrastructure_allowed: bool | None = None,
    ) -> None:
        self.db = db
        self.repository = SandboxAccessRepository(db) if db is not None else None
        self._infrastructure_allowed = infrastructure_allowed

    def _hard_ceiling(self) -> bool:
        if self._infrastructure_allowed is not None:
            return bool(self._infrastructure_allowed)
        # 该设置的描述符只允许 environment/default，数据库记录永不生效。
        return settings.get_bool(
            "sandbox.infrastructure_enable_allowed",
            False,
        )

    def evaluate_context(
        self,
        tool_name: str,
        context: Mapping[str, Any] | None,
    ) -> SandboxAccessDecision:
        runtime = context if isinstance(context, Mapping) else {}
        return self.evaluate(
            tool_name,
            platform=runtime.get("platform"),
            chat_type=runtime.get("chat_type"),
            session_id=runtime.get("session_id"),
        )

    def evaluate(
        self,
        tool_name: str,
        *,
        platform: object,
        chat_type: object,
        session_id: object,
    ) -> SandboxAccessDecision:
        required = TOOL_REQUIRED_CAPABILITY.get(
            str(tool_name or ""),
            SandboxCapability.OFF,
        )
        decision = execute_policy(
            SANDBOX_ACCESS_POLICY_DESCRIPTOR,
            lambda: self._evaluate(
                tool_name,
                platform=platform,
                chat_type=chat_type,
                session_id=session_id,
            ),
            fallback=lambda failure: self._failure_decision(
                failure,
                required=required,
            ),
        ).value
        self._record_runtime_decision(tool_name, decision)
        return decision

    def _record_runtime_decision(
        self,
        tool_name: str,
        decision: SandboxAccessDecision,
    ) -> None:
        """在返回真实工具调用前提交权限事实；ToolPlan 预检不重复入账。"""

        if self.db is None:
            return
        from core.runtime.event_bus import current_runtime_event_context

        correlation = current_runtime_event_context()
        if not correlation.run_id or not correlation.tool_call_id:
            return
        from core.run_ledger.adapters import (
            sandbox_permission_decision_event,
        )
        from core.run_ledger.contracts import RunLedgerAuthorityError
        from core.run_ledger.persistence import SqlAlchemyRunEventLedger

        event = sandbox_permission_decision_event(
            correlation=correlation,
            tool_name=tool_name,
            allowed=decision.allowed,
            reason_code=decision.code or "allowed",
            capability=decision.required_capability.value_name,
        )
        try:
            ledger = SqlAlchemyRunEventLedger(self.db)
            existing = ledger.get(event.event_id)
            if existing is not None:
                # Lease 执行会在入口和创建进程前重复校验同一授权事实。
                # 复用首次时间戳，使同一 event_id 保持严格幂等；其余字段
                # 仍由 Ledger 摘要比对，任何事实漂移都会继续拒绝。
                event = replace(
                    event,
                    occurred_at=existing.event.occurred_at,
                )
            ledger.append(event)
            # 权限决定是工具副作用前的权威屏障；拒绝路径随后即使回滚，
            # 也不能抹去已经向调用方生效的决定。
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            if isinstance(exc, RunLedgerAuthorityError):
                raise
            raise RunLedgerAuthorityError(
                "Sandbox 权限决定权威入账失败",
                run_id=event.run_id,
                event_type=event.event_type,
                code="permission_decision_write_failed",
            ) from exc

    @staticmethod
    def _failure_decision(
        failure: PolicyTypedFailure,
        *,
        required: SandboxCapability,
    ) -> SandboxAccessDecision:
        del failure
        return SandboxAccessDecision(
            False,
            "authorization_failed",
            "Sandbox 授权策略暂时不可用",
            required,
        )

    def _evaluate(
        self,
        tool_name: str,
        *,
        platform: object,
        chat_type: object,
        session_id: object,
    ) -> SandboxAccessDecision:
        required = TOOL_REQUIRED_CAPABILITY.get(str(tool_name or ""))
        if required is None:
            return SandboxAccessDecision(
                False,
                "authorization_failed",
                "Sandbox 工具身份无效",
                SandboxCapability.OFF,
            )
        if self.db is None or self.repository is None:
            return SandboxAccessDecision(
                False,
                "authorization_failed",
                "Sandbox 授权状态不可用",
                required,
            )
        if not self._hard_ceiling():
            return SandboxAccessDecision(
                False,
                "sandbox_not_enabled",
                "Sandbox 基础设施硬上限未允许",
                required,
            )
        if not _database_bool(self.db, "sandbox.enabled"):
            return SandboxAccessDecision(
                False,
                "sandbox_not_enabled",
                "Sandbox 总开关未启用",
                required,
            )
        if required is SandboxCapability.EXEC and not _database_bool(
            self.db,
            "sandbox.exec_enabled",
        ):
            return SandboxAccessDecision(
                False,
                "sandbox_not_enabled",
                "Sandbox 执行能力未启用",
                required,
            )
        try:
            identity = canonical_sandbox_identity(
                platform=platform,
                chat_type=chat_type,
                session_id=session_id,
            )
        except ChatStreamIdentityError:
            return SandboxAccessDecision(
                False,
                "authorization_failed",
                "无法确认 canonical Sandbox 会话身份",
                required,
            )
        if (
            identity.chat_type == "group"
            and not _database_bool(self.db, "sandbox.group_enabled")
        ):
            return SandboxAccessDecision(
                False,
                "sandbox_not_enabled",
                "群聊 Sandbox 业务开关未启用",
                required,
                identity=identity,
            )
        grant = self.repository.get_grant(identity.chat_stream_id)
        if grant is None:
            return SandboxAccessDecision(
                False,
                "authorization_failed",
                "当前会话没有显式 Sandbox 授权",
                required,
                identity=identity,
            )
        try:
            granted = SandboxCapability.parse(grant.capability_level)
        except ValueError:
            granted = SandboxCapability.OFF
        if grant.status != "active" or granted < required:
            return SandboxAccessDecision(
                False,
                "authorization_failed",
                "当前会话的 Sandbox 能力等级不足",
                required,
                granted_capability=granted,
                identity=identity,
                grant_id=str(grant.id),
                workspace_id=str(grant.workspace_id or ""),
            )
        profile_id = str(grant.execution_profile or "").strip()
        try:
            profile = load_execution_profile_registry().descriptor(profile_id)
        except SandboxServiceError:
            return SandboxAccessDecision(
                False,
                "authorization_failed",
                "当前会话的 Sandbox 执行 Profile 不可授权",
                required,
                granted_capability=granted,
                identity=identity,
                grant_id=str(grant.id),
                workspace_id=str(grant.workspace_id or ""),
                execution_profile=profile_id or "restricted",
            )
        if (
            required is SandboxCapability.EXEC
            and profile.execution_mode == "lease"
            and not settings.get_bool(
                "sandbox.session_execution_allowed",
                False,
            )
        ):
            return SandboxAccessDecision(
                False,
                "sandbox_not_enabled",
                "会话型 Sandbox 执行硬上限未允许",
                required,
                granted_capability=granted,
                identity=identity,
                grant_id=str(grant.id),
                workspace_id=str(grant.workspace_id or ""),
                execution_profile=profile_id,
            )
        if (
            required is SandboxCapability.EXEC
            and profile.network_policy_id != "none"
            and not settings.get_bool(
                "sandbox.developer_network_allowed",
                False,
            )
        ):
            return SandboxAccessDecision(
                False,
                "sandbox_not_enabled",
                "开发型 Sandbox 网络出口硬上限未允许",
                required,
                granted_capability=granted,
                identity=identity,
                grant_id=str(grant.id),
                workspace_id=str(grant.workspace_id or ""),
                execution_profile=profile_id,
            )
        if (
            tool_name in LEASE_PROCESS_TOOL_NAMES
            and profile.execution_mode != "lease"
        ):
            return SandboxAccessDecision(
                False,
                "authorization_failed",
                "当前 Sandbox Profile 不支持 Lease 进程控制",
                required,
                granted_capability=granted,
                identity=identity,
                grant_id=str(grant.id),
                workspace_id=str(grant.workspace_id or ""),
                execution_profile=profile_id,
            )
        if (
            tool_name == "sandbox_write_stdin"
            and not profile.allow_stdin
        ):
            return SandboxAccessDecision(
                False,
                "authorization_failed",
                "当前 Sandbox Profile 不支持 stdin",
                required,
                granted_capability=granted,
                identity=identity,
                grant_id=str(grant.id),
                workspace_id=str(grant.workspace_id or ""),
                execution_profile=profile_id,
            )
        workspace_id = str(grant.workspace_id or "")
        workspace = self.repository.get_workspace(workspace_id)
        binding = self.repository.get_quota_binding(workspace_id)
        runtime_binding = (
            self.repository.get_runtime_quota_binding(workspace_id)
            if required is SandboxCapability.EXEC
            else None
        )
        maintenance = self.repository.get_maintenance_state(workspace_id)
        if (
            workspace is None
            or not workspace_matches_sandbox_grant(workspace, grant)
            or workspace.status != "active"
            or binding is None
            or binding.status != "applied"
            or int(binding.applied_quota_bytes or 0) <= 0
            or int(binding.applied_quota_bytes or 0)
            != int(binding.desired_quota_bytes or 0)
            or int(workspace.quota_bytes or 0)
            != int(binding.applied_quota_bytes or 0)
            or (
                required is SandboxCapability.EXEC
                and (
                    runtime_binding is None
                    or runtime_binding.status != "applied"
                    or int(runtime_binding.applied_quota_bytes or 0)
                    != int(runtime_binding.desired_quota_bytes or 0)
                    or int(runtime_binding.applied_quota_bytes or 0)
                    != int(profile.runtime_quota_bytes)
                    or int(runtime_binding.generation or 0)
                    != int(binding.generation or 0)
                )
            )
            or (
                maintenance is not None
                and (
                    maintenance.status != "ready"
                    or int(maintenance.generation or 0)
                    != int(binding.generation or 0)
                    or int(maintenance.applied_quota_generation or 0)
                    != int(binding.generation or 0)
                )
            )
        ):
            return SandboxAccessDecision(
                False,
                "authorization_failed",
                "当前会话的 Workspace 或硬配额尚未就绪",
                required,
                granted_capability=granted,
                identity=identity,
                grant_id=str(grant.id),
                workspace_id=workspace_id,
            )
        return SandboxAccessDecision(
            True,
            "",
            "已授权",
            required,
            granted_capability=granted,
            identity=identity,
            grant_id=str(grant.id),
            workspace_id=workspace_id,
            quota_bytes=int(binding.applied_quota_bytes),
            execution_profile=profile_id,
            grant_version=int(grant.version),
            quota_generation=int(binding.generation),
        )


__all__ = [
    "SANDBOX_ACCESS_POLICY_DESCRIPTOR",
    "SandboxAccessPolicy",
    "canonical_sandbox_identity",
]

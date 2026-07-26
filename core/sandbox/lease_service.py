"""Server 侧 canonical session → 当前 Sandbox Lease 编排。"""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from core.chat_stream_identity import (
    ChatStreamIdentityError,
    parse_canonical_chat_stream_id,
)
from core.database import (
    SANDBOX_LEASE_NONTERMINAL_STATUSES,
    SandboxLease,
    SandboxRun,
)
from core.sandbox.access_contracts import SandboxAccessDecision
from core.sandbox.access_policy import SandboxAccessPolicy
from core.sandbox.backend import SandboxBackend
from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from core.sandbox.environment_service import SandboxEnvironmentService
from core.sandbox.execution_profiles import (
    ExecutionProfileRegistry,
    load_execution_profile_registry,
)
from core.sandbox.repositories import SandboxLeaseRepository
from core.time_utils import db_now_naive


_CONTROLLER_EPOCH_RE = re.compile(r"sbxctl_[0-9a-f]{32}")
_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _request_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


def _data(response: Mapping[str, Any]) -> dict[str, Any]:
    value = response.get("data")
    if not isinstance(value, Mapping):
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "Sandbox Lease 控制面返回了无效响应",
            retryable=True,
            stop=False,
        )
    return dict(value)


def _timestamp(value: object) -> datetime | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp)
    except (OSError, OverflowError, ValueError):
        return None


def _controller_epoch(value: object) -> str:
    normalized = str(value or "")
    if not _CONTROLLER_EPOCH_RE.fullmatch(normalized):
        raise SandboxServiceError(
            SandboxErrorCode.RUNTIME_UNAVAILABLE,
            "sandboxd controller epoch 无效",
            retryable=True,
            stop=False,
        )
    return normalized


class SandboxLeaseService:
    """Lease 生命周期对模型透明；调用方只能传已完成的授权结论。"""

    def __init__(
        self,
        db: Session,
        backend: SandboxBackend,
        *,
        profile_registry: ExecutionProfileRegistry | None = None,
        environment_service: SandboxEnvironmentService | None = None,
    ) -> None:
        self.db = db
        self.backend = backend
        self.profile_registry = (
            profile_registry or load_execution_profile_registry()
        )
        self.environment_service = (
            environment_service or SandboxEnvironmentService()
        )
        self.repository = SandboxLeaseRepository(db)

    def _fresh_access(
        self,
        access: SandboxAccessDecision,
    ) -> SandboxAccessDecision:
        """对携带 fencing 代际的授权结论重新读取全部当前事实。"""

        if access.grant_version <= 0 or access.quota_generation <= 0:
            return access
        identity = access.identity
        if identity is None:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 授权身份已失效",
            )
        self.db.expire_all()
        current = SandboxAccessPolicy(self.db).evaluate(
            "sandbox_exec",
            platform=identity.platform,
            chat_type=identity.chat_type,
            session_id=identity.external_session_id,
        )
        if (
            not current.allowed
            or current.grant_id != access.grant_id
            or current.workspace_id != access.workspace_id
            or current.execution_profile != access.execution_profile
            or current.grant_version != access.grant_version
            or current.quota_generation != access.quota_generation
        ):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox 授权、Profile 或配额代际已变化",
            )
        return current

    @staticmethod
    def lease_key(
        *,
        chat_stream_id: str,
        workspace_id: str,
        profile_id: str,
    ) -> str:
        payload = (
            "sandbox-lease-key.v1\0"
            f"{chat_stream_id}\0{workspace_id}\0{profile_id}"
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _require_access(access: SandboxAccessDecision) -> tuple[str, str]:
        identity = access.identity
        if (
            not access.allowed
            or identity is None
            or not access.grant_id
            or not access.workspace_id
            or identity.chat_type != "private"
        ):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "当前会话无权创建 Sandbox Lease",
            )
        try:
            parsed = parse_canonical_chat_stream_id(
                identity.chat_stream_id
            )
        except ChatStreamIdentityError as exc:
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox canonical session 无效",
            ) from exc
        if (
            parsed.platform != identity.platform
            or parsed.chat_type != identity.chat_type
            or parsed.external_session_id
            != identity.external_session_id
        ):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "Sandbox canonical session 与授权身份不一致",
            )
        return identity.chat_stream_id, access.execution_profile

    def _finish_restarted_lease(self, lease: SandboxLease) -> None:
        now = db_now_naive()
        failed = self.repository.transition(
            lease.lease_id,
            expected_statuses=SANDBOX_LEASE_NONTERMINAL_STATUSES,
            target_status="failed",
            changes={
                "stopped_at": now,
                "reconciled_at": now,
                "last_error_code": "controller_restarted",
                "last_error_summary": (
                    "sandboxd controller 重启，旧 Lease 已回收"
                ),
            },
        )
        if failed is None:
            self.db.rollback()
            raise SandboxServiceError(
                SandboxErrorCode.SANDBOX_BUSY,
                "Sandbox Lease 状态已被并发维护操作改变",
                retryable=True,
                stop=False,
            )
        runs = (
            self.db.query(SandboxRun)
            .filter(
                SandboxRun.lease_id == lease.lease_id,
                SandboxRun.status.in_({"pending", "running"}),
            )
            .all()
        )
        for run in runs:
            if str(run.status) in _TERMINAL_RUN_STATUSES:
                continue
            run.status = "failed"
            run.process_state = "lost"
            run.termination_reason = "controller_restarted"
            run.finished_at = now
            run.last_seen_at = now
            run.updated_at = now
        self.db.commit()

    def ensure_for_access(
        self,
        access: SandboxAccessDecision,
        *,
        authorized_assets: Sequence[Mapping[str, str]] = (),
    ) -> SandboxLease:
        access = self._fresh_access(access)
        chat_stream_id, profile_id = self._require_access(access)
        descriptor = self.profile_registry.descriptor(profile_id)
        if descriptor.execution_mode != "lease":
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "当前 Sandbox Profile 不使用 Lease",
            )
        controller_ready = _data(self.backend.ready())
        ready_epoch = _controller_epoch(
            controller_ready.get("controller_epoch")
        )
        profile_state = self.profile_registry.require_controller_profile(
            controller_ready,
            profile_id,
        )

        lease_key = self.lease_key(
            chat_stream_id=chat_stream_id,
            workspace_id=access.workspace_id,
            profile_id=profile_id,
        )
        candidate = SandboxLease(
            lease_id=f"sbxlease_{secrets.token_hex(16)}",
            lease_key=lease_key,
            grant_id=access.grant_id,
            chat_stream_id=chat_stream_id,
            workspace_id=access.workspace_id,
            profile_id=profile_id,
            catalog_generation=self.profile_registry.catalog_generation,
            policy_sha256=self.profile_registry.policy_sha256,
            status="provisioning",
            image_digest="",
            controller_epoch="",
        )
        lease = self.repository.add_or_get_current(candidate)
        if (
            str(lease.grant_id) != access.grant_id
            or str(lease.chat_stream_id) != chat_stream_id
            or str(lease.workspace_id) != access.workspace_id
            or str(lease.profile_id) != profile_id
            or str(lease.catalog_generation)
            != self.profile_registry.catalog_generation
            or str(lease.policy_sha256)
            != self.profile_registry.policy_sha256
        ):
            raise SandboxServiceError(
                SandboxErrorCode.AUTHORIZATION_FAILED,
                "当前 Sandbox Lease 与最新授权或策略不一致",
            )
        if str(lease.status) == "stopping":
            raise SandboxServiceError(
                SandboxErrorCode.SANDBOX_BUSY,
                "当前 Sandbox Lease 正在回收",
                retryable=True,
                stop=False,
            )
        if (
            str(lease.controller_epoch or "")
            and str(lease.controller_epoch) != ready_epoch
        ):
            self._finish_restarted_lease(lease)
            lease = self.repository.add_or_get_current(SandboxLease(
                lease_id=f"sbxlease_{secrets.token_hex(16)}",
                lease_key=lease_key,
                grant_id=access.grant_id,
                chat_stream_id=chat_stream_id,
                workspace_id=access.workspace_id,
                profile_id=profile_id,
                catalog_generation=(
                    self.profile_registry.catalog_generation
                ),
                policy_sha256=self.profile_registry.policy_sha256,
                status="provisioning",
                image_digest="",
                controller_epoch="",
            ))
            if (
                str(lease.grant_id) != access.grant_id
                or str(lease.chat_stream_id) != chat_stream_id
                or str(lease.workspace_id) != access.workspace_id
                or str(lease.profile_id) != profile_id
                or str(lease.catalog_generation)
                != self.profile_registry.catalog_generation
                or str(lease.policy_sha256)
                != self.profile_registry.policy_sha256
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.AUTHORIZATION_FAILED,
                    "当前 Sandbox Lease 与最新授权或策略不一致",
                )
        self.db.commit()

        ensure_request_id = _request_id("sbxleaseensure")
        try:
            access = self._fresh_access(access)
            fact = _data(self.backend.ensure_lease({
                "request_id": ensure_request_id,
                "lease_id": lease.lease_id,
                "workspace_id": lease.workspace_id,
                "profile_id": lease.profile_id,
                "catalog_generation": lease.catalog_generation,
                "policy_sha256": lease.policy_sha256,
                "quota_generation": max(
                    1,
                    int(access.quota_generation or 1),
                ),
            }))
            if (
                str(fact.get("lease_id") or "") != lease.lease_id
                or str(fact.get("workspace_id") or "")
                != lease.workspace_id
                or str(fact.get("profile_id") or "")
                != lease.profile_id
                or str(fact.get("catalog_generation") or "")
                != lease.catalog_generation
                or str(fact.get("policy_sha256") or "")
                != lease.policy_sha256
                or str(fact.get("image_digest") or "").lower()
                != profile_state.image_id
                or _controller_epoch(
                    fact.get("controller_epoch")
                ) != ready_epoch
                or fact.get("present") is not True
                or fact.get("running") is not True
            ):
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Sandbox Lease 控制面事实与 Server 请求不一致",
                )
            self.environment_service.require_ready(
                fact.get("environment"),
                profile_id=lease.profile_id,
                catalog_generation=lease.catalog_generation,
                policy_sha256=lease.policy_sha256,
                image_digest=profile_state.image_id,
            )

            asset_request_id = _request_id("sbxleaseassets")
            asset_response = _data(self.backend.sync_lease_assets(
                lease.lease_id,
                {
                    "request_id": asset_request_id,
                    "workspace_id": lease.workspace_id,
                    "assets": [dict(item) for item in authorized_assets],
                },
            ))
            if str(asset_response.get("lease_id") or "") != lease.lease_id:
                raise SandboxServiceError(
                    SandboxErrorCode.RUNTIME_UNAVAILABLE,
                    "Sandbox Lease 资产同步响应无效",
                )
            self._fresh_access(access)
        except Exception:
            try:
                self.backend.stop_lease(
                    lease.lease_id,
                    request_id=_request_id("sbxleasestop"),
                )
            except Exception:
                pass
            failed = self.repository.transition(
                lease.lease_id,
                expected_statuses={
                    "provisioning",
                    "active",
                    "idle",
                },
                target_status="failed",
                changes={
                    "stopped_at": db_now_naive(),
                    "last_error_code": "runtime_unavailable",
                    "last_error_summary": (
                        "Sandbox Lease 创建或资产同步失败"
                    ),
                },
            )
            self.db.commit()
            if failed is None:
                self.db.rollback()
            raise

        active = self.repository.transition(
            lease.lease_id,
            expected_statuses={"provisioning", "active", "idle"},
            target_status="active",
            changes={
                "controller_epoch": ready_epoch,
                "image_digest": str(
                    fact.get("image_digest") or ""
                )[:255],
                "last_active_at": (
                    _timestamp(fact.get("last_active_at_unix"))
                    or db_now_naive()
                ),
                "idle_expires_at": _timestamp(
                    fact.get("idle_expires_at_unix")
                ),
                "max_expires_at": _timestamp(
                    fact.get("max_expires_at_unix")
                ),
                "reconciled_at": db_now_naive(),
                "last_error_code": "",
                "last_error_summary": "",
            },
        )
        if active is None:
            self.db.rollback()
            raise SandboxServiceError(
                SandboxErrorCode.SANDBOX_BUSY,
                "Sandbox Lease 状态已被并发维护操作改变",
                retryable=True,
                stop=False,
            )
        self.db.commit()
        return active


__all__ = ["SandboxLeaseService"]
